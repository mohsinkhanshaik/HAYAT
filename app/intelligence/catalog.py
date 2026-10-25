"""One source system: what every source is, what it is for, and how far to trust it.

Feeds, discovered catalogs, on-demand providers, livestreams and (later) the
owner's own devices all describe themselves the same way here, so HAYAT can
search, rank and explain them together instead of as separate integrations.

Every profile carries: provider, types, country/region, coverage, media types,
API/stream URL, refresh rate, authentication, licence, reliability, health,
last checked, last successful update, restrictions and confidence.
"""

from __future__ import annotations

from typing import Any
import logging
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: The source types, and why each one helps -- HAYAT says this when asked what it can see and why.
TYPES: dict[str, dict[str, str]] = {
    "LIVE_VIDEO": {"title": "Live video",
                   "why": "Shows what is happening right now -- traffic moving, weather arriving, a crowd, a launch -- "
                          "so a report can be checked with your own eyes instead of trusted."},
    "SNAPSHOT": {"title": "Refreshing camera stills",
                 "why": "A fresh picture every minute or two is enough to confirm snow, flooding, smoke or a blocked "
                        "road, at a fraction of video's bandwidth."},
    "SATELLITE": {"title": "Satellite imagery and orbits",
                  "why": "Sees whole regions at once: smoke plumes, storm systems, floods and burn scars where no "
                         "camera stands; orbital elements locate the ISS and other spacecraft."},
    "SENSOR": {"title": "Sensors",
               "why": "Numbers instead of pictures -- air quality, river height, weather stations -- that show change "
                      "precisely and can be compared with yesterday and with normal."},
    "LOCATION": {"title": "Positions of moving things",
                 "why": "Where aircraft, ships and vehicles are, so an event can be related to what was near it."},
    "TRAFFIC": {"title": "Road conditions",
                "why": "Incidents, closures and construction explain a jam before you reach it and tell you which "
                       "camera to look at."},
    "AIRCRAFT": {"title": "Aircraft (ADS-B)",
                 "why": "Which flights are overhead or delayed near a place -- useful for airports, storms, "
                        "emergencies and simply knowing what that noise is."},
    "VESSEL": {"title": "Ships (AIS)",
               "why": "Ship positions and speeds near ports and coasts: congestion, a vessel in trouble, what a harbour "
                      "camera is showing."},
    "TRANSIT": {"title": "Public transit",
                "why": "Where buses and trains actually are, so delays and disruptions show up as they happen."},
    "WEATHER": {"title": "Weather",
                "why": "Forecasts and observations explain what cameras show and warn about what comes next."},
    "EVENT": {"title": "Events",
              "why": "Earthquakes, wildfires, floods, storms and disasters from the agencies that record them -- the "
                     "starting point for gathering everything nearby."},
    "NEWS": {"title": "News",
             "why": "What people are reporting about a place, to compare with what the data and cameras show."},
    "MAP": {"title": "Maps, places and terrain",
            "why": "Turns a name into coordinates, gives elevation and context, and puts everything on one map."},
    "HISTORICAL": {"title": "History",
                   "why": "What is usual for a place and time, so HAYAT can tell an anomaly from an ordinary Tuesday."},
}


def feed_types(key: str, kind_counts: dict[str, int] | None = None) -> list[str]:
    """Source types of a feed, from its key and the kinds of records it holds."""
    if key == "livestreams":
        return ["LIVE_VIDEO", "SNAPSHOT"]
    if key.startswith('news_') or key == 'gdelt_security':
        return ['NEWS', 'EVENT']
    if key.startswith(("cameras_", "discovered_")):
        return ["SNAPSHOT", "LIVE_VIDEO"]
    if key.startswith("roads_"):
        return ["TRAFFIC", "EVENT"]
    if key in ("earthquakes", "disasters", "natural_events", "wildfires_us"):
        return ["EVENT"]
    if key in ("space_weather", "aviation_sigmet"):
        return ["EVENT", "WEATHER"]
    if key == "cyber":
        return ["EVENT"]
    return ["EVENT"]


def country_of(rows: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        country = str((row.get("details") or {}).get("country") or "")
        if country:
            counts[country] = counts.get(country, 0) + 1
    return max(counts, key=counts.get) if counts else ""


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    points = [(row["latitude"], row["longitude"]) for row in rows
              if row.get("latitude") is not None and row.get('longitude') is not None]
    if not points:
        return None
    lats, lons = [point[0] for point in points], [point[1] for point in points]
    return {"south": round(min(lats), 3), "west": round(min(lons), 3), "north": round(max(lats), 3),
            "east": round(max(lons), 3), "located_records": len(points)}


def feed_profile(feed: Any, state: dict[str, Any], rows: list[dict[str, Any]], health: dict[str, Any]) -> dict[str, Any]:
    """A built-in or discovered feed, described in the common shape."""
    from app.intelligence.discovery import is_official

    details = [row.get("details") or {} for row in rows]
    media = sorted({label for item in details for label, present in (
        ("HLS stream", str(item.get("video_url") or "").lower().split("?")[0].endswith(".m3u8")),
        ("video clip", str(item.get("video_url") or "").lower().split("?")[0].endswith(".mp4")),
        ("still image", bool(item.get("image_url"))),
        ("publisher player", bool(item.get("page_url")))) if present})
    attempts = health.get("attempts") or 0
    reliability = round(health.get("successes", 0) / attempts, 2) if attempts else None
    official = is_official(feed.url) or is_official(feed.documentation) or (
        feed.key == 'livestreams' and bool(details) and all(item.get('official_publisher') for item in details))
    types = feed_types(feed.key)
    if rows and (feed.key == 'livestreams' or feed.key.startswith(('cameras_', 'discovered_'))):
        types = [kind for kind, present in (
            ('SNAPSHOT', any(item.get('image_url') for item in details)),
            ('LIVE_VIDEO', any(item.get('live_page') or str(item.get('video_url') or '').lower().split('?')[0].endswith('.m3u8')
                               for item in details)),
        ) if present]
    return {
        "key": feed.key, "title": feed.title, "provider": feed.title.split(" · ")[0],
        "types": types, "country": country_of(rows), "coverage": coverage(rows),
        "records": len(rows), "media_types": media, "url": feed.url if not feed.key_env else feed.url.split("?")[0],
        "documentation": feed.documentation, "refresh_seconds": feed.interval,
        "authentication": f"developer key ({feed.key_env})" if feed.key_env else "none",
        "license": "publisher terms (see documentation)", "official": official,
        "reliability": reliability, "health": state.get("status", "not_fetched"),
        "last_checked": state.get("last_attempt_at"), "last_success": state.get("last_success_at"),
        "restrictions": "media only from the hosts the publisher's own catalog uses",
        "confidence": _confidence(official, reliability, state.get("status")),
    }


def provider_profile(key: str, meta: dict[str, Any], health: dict[str, Any] | None) -> dict[str, Any]:
    health = health or {}
    return {"key": f"live:{key}", "title": meta["provider"], "provider": meta["provider"], "types": meta["types"],
            "country": "", "coverage": meta["coverage"], "records": None, "media_types": ["on-demand data"],
            "url": meta["url"], "documentation": meta["url"], "refresh_seconds": meta["refresh_seconds"],
            "authentication": "none", "license": meta["license"], "official": bool(meta.get('official', False)),
            "reliability": None, "health": "ok" if health.get("ok") else ("failed" if health else "on_demand"),
            "last_checked": health.get("checked_at"), "last_success": health.get("last_success"),
            "restrictions": meta.get("restrictions") or f"rate-limited to {meta['per_minute']} requests a minute",
            "confidence": "high" if health.get("ok") else "medium"}


def _confidence(official: bool, reliability: float | None, status: str | None) -> str:
    if status != "ok":
        return "low"
    if reliability is not None and reliability < 0.7:
        return "medium"
    return "high" if official else "medium"


def explain(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per type: how many sources and records HAYAT has, and why that type helps."""
    found: dict[str, dict[str, Any]] = {}
    for profile in profiles:
        for kind in profile["types"]:
            entry = found.setdefault(kind, {"type": kind, "title": TYPES[kind]["title"], "why": TYPES[kind]["why"],
                                            "sources": 0, "records": 0, "examples": []})
            entry["sources"] += 1
            entry["records"] += profile.get("records") or 0
            if len(entry["examples"]) < 4:
                entry["examples"].append(profile["provider"])
    order = list(TYPES)
    return sorted(found.values(), key=lambda item: order.index(item["type"]))


def missing_types(profiles: list[dict[str, Any]]) -> list[str]:
    """Types HAYAT knows about but has no keyless source for yet (shown honestly)."""
    available = {kind for profile in profiles for kind in profile['types']}
    return [kind for kind in TYPES if kind not in available]


def source_catalog(manager: Any, live: Any = None, kind: str = "") -> dict[str, Any]:
    """Every source HAYAT can use, in the common shape, with why each type helps."""
    from app.intelligence.feeds import ALL_FEEDS
    from app.intelligence.live import PROVIDERS

    kind = (kind or "").strip().upper().replace(" ", "_")
    if kind and kind not in TYPES:
        raise ValueError(f"Choose a source type: {', '.join(TYPES)}.")
    feeds = {feed.key: feed for feed in (*ALL_FEEDS, *manager.feeds)}
    _sources, rows = manager._matching("", "", None, False)
    by_feed: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_feed.setdefault(row.get("feed", ""), []).append(row)
    try:
        health = manager.store.health_summary()
    except Exception:
        health = {}
    profiles = [feed_profile(feeds[source["key"]], source, by_feed.get(source["key"], []), health.get(source["key"], {}))
                for source in manager._sources() if source["key"] in feeds]
    live_health = getattr(live, "health", {}) if live is not None else {}
    profiles += [provider_profile(key, meta, live_health.get(key)) for key, meta in PROVIDERS.items()]
    recorder = getattr(live, 'recorder', None)
    if recorder is not None:
        try:
            history = recorder.stats()
            count = sum(item.get('positions', 0) for item in history.values())
            latest = max((item.get('to') or '' for item in history.values()), default='') or None
            profiles.append({'key': 'local:position_history', 'title': 'HAYAT observed position history',
                             'provider': 'HAYAT retained public observations', 'types': ['HISTORICAL'],
                             'country': '', 'coverage': 'Only locations and times HAYAT previously observed',
                             'records': count, 'media_types': ['position history'], 'url': '', 'documentation': '',
                             'refresh_seconds': 30, 'authentication': 'local session', 'license': 'upstream source terms',
                             'official': False, 'reliability': None, 'health': 'local' if count else 'empty',
                             'last_checked': latest, 'last_success': latest,
                             'restrictions': 'Limited retention; gaps do not prove that a craft was absent.',
                             'confidence': 'medium' if count else 'low'})
        except Exception:
            # A history read failure cannot invent historical coverage.
            log_swallowed(_log, 'reading observed position history')
    summary = explain(profiles)
    missing = missing_types(profiles)
    if kind:
        profiles = [profile for profile in profiles if kind in profile["types"]]
        summary = [entry for entry in summary if entry["type"] == kind]
    return {"intelligence": True, "source_catalog": True, "types": summary,
            "missing_types": [{"type": name, "title": TYPES[name]["title"], "why": TYPES[name]["why"]}
                              for name in missing],
            "count": len(profiles), "sources": profiles,
            "basis": "Built-in feeds, catalogs HAYAT discovered and approved, and on-demand providers, described alike. "
                     "Reliability is the share of successful refreshes HAYAT recorded; confidence combines it with "
                     "whether the publisher is official and the source currently healthy."}
