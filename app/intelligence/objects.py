"""One intelligence object per real-world event, however many reports describe it.

Reports of the same event (two agencies' readings of one earthquake, five outlets on one
strike) are merged by place, time and wording. Each object carries its sources with a
score that is shown, not hidden, and a verification status:

  CONFIRMED         an authoritative event source; for conflict and attacks,
                    another reputable outlet must also report it
  LIKELY            two distinct sources agree, without authoritative confirmation
  UNVERIFIED        a single source, or sources that are not independent
  FALSE/CONTRADICTED  sources dispute the claim and outweigh those asserting it

Correlation with what was nearby (cameras, aircraft, ships, weather, fires, alerts, news,
history) is evidence to look at, never proof of cause; every correlation says so and
lists other explanations.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.intelligence.live import bearing, distance_km
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Providers that are themselves the authority for what they report.
SECURITY = ("attack", "conflict")
OBJECT_CATEGORIES = ("attack", "conflict", "emergency", "earthquakes", "wildfires", "severe_weather", "floods",
                     "volcanoes", "aviation", "space_weather", "tsunami", "health", "space", "air_quality")
ALTERNATIVES = {
    "attack": "An explosion can be industrial, a gas leak, an accident, munitions disposal or a misattributed older event; "
              "early reports often mislocate or misdate. Responsibility claims are not evidence of responsibility.",
    "conflict": "Reports of fighting can describe exercises, older footage, or one side's claims; front lines are "
                "rarely where early reports put them.",
    "emergency": "Early figures change; an evacuation can be precautionary.",
    "earthquakes": "Different agencies' magnitudes and depths differ in the first hours; that is normal, not a contradiction.",
    "wildfires": "A heat detection can be a flare, an industrial site or a controlled burn.",
    "aviation": "Airport restrictions are often routine notices (non-scheduled traffic, oversized aircraft), not incidents.",
    "tsunami": "Most tsunami bulletins are information statements that no threat exists; only a warning or advisory "
               "asks anyone to act, and a first bulletin is often revised within the hour.",
    "health": "An outbreak report is placed at the centre of the country it names, not where cases are; early case "
              "counts and causes are often revised.",
    "volcanoes": "A raised alert level means unrest (earthquakes, gas, deformation), not necessarily an eruption.",
}
_STOP = frozenset("the a an of in on at to for and or as by with from after before over amid says said new news live "
                  "is are was were be has have it its this that their they he she video watch latest update".split())


def _tokens(title: str) -> set[str]:
    return {word for word in re.findall(r"[a-z][a-z'-]{2,}", title.lower()) if word not in _STOP}


def _when(row: dict[str, Any]) -> datetime | None:
    for key in ("observed_at", "retrieved_at"):
        value = row.get(key)
        if value:
            try:
                moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
            except ValueError:
                continue
    return None


def reported_at(item: dict[str, Any]) -> datetime:
    return _when({"observed_at": item.get("last_reported")}) or datetime.min.replace(tzinfo=UTC)


def sort_objects(items: list[dict[str, Any]], order: str = "priority") -> list[dict[str, Any]]:
    """Stable, shared ordering for single-category and combined searches."""
    result = sorted(items, key=lambda item: str(item.get("id", "")))
    result.sort(key=reported_at, reverse=True)
    if order == "nearest":
        result.sort(key=lambda item: item.get("distance_km", math.inf))
    elif order == "confidence":
        result.sort(key=lambda item: float(item.get("confidence", 0)), reverse=True)
    elif order == "title":
        result.sort(key=lambda item: str(item.get("title", "")).casefold())
    elif order == "priority":
        status = {"CONFIRMED": 0, "LIKELY": 1, "UNVERIFIED": 2, "FALSE/CONTRADICTED": 3}
        result.sort(key=lambda item: (-item.get("severity", 0), status.get(item.get("status"), 4)))
    elif order != "newest":
        raise ValueError("sort must be priority, nearest, newest, confidence or title.")
    return result


def is_official(row: dict[str, Any]) -> bool:
    from app.intelligence.classify import HAZARD_ASSESSED

    return row.get("feed") in ("earthquakes", "earthquakes_emsc", "storms_nhc", "airports_faa", "disasters",
                               "natural_events", "wildfires_us", "space_weather", "cyber", *HAZARD_ASSESSED)


def source_score(row: dict[str, Any], reliability: dict[str, float], now: datetime) -> dict[str, Any]:
    """The parts of one source's weight, shown so the owner can see why it counts as much as it does."""
    details = row.get("details") or {}
    official = is_official(row)
    authority = 0.95 if official else max(0, min(1, float(details.get("authority", 0.5))))
    accuracy = reliability.get(row.get("feed", ""), 0.7)
    moment = _when(row)
    age_hours = (now - moment).total_seconds() / 3600 if moment else 48
    freshness = 1.0 if age_hours < 6 else 0.7 if age_hours < 24 else 0.4 if age_hours < 72 else 0.2
    precision = str(details.get("location_precision") or "")
    proximity = 1.0 if not precision else 0.8 if precision.startswith("city") else 0.4
    evidence = 1.0 if not details.get("security_category") else 0.6  # provider coordinates vs a place named in text
    consistency = 1.0 if moment and age_hours > -1 else 0.5          # a timestamp from the future is suspect
    bias = str(details.get("bias_note") or "")
    bias_risk = 0.6 if "propaganda" in bias or "state media" in bias else 0.2 if "state-funded" in bias else 0.0
    score = (0.35 * authority + 0.15 * accuracy + 0.15 * freshness + 0.1 * proximity + 0.1 * evidence
             + 0.1 * consistency + 0.05 * (1 - bias_risk))
    return {"score": round(score, 3), "authority": authority, "fetch_reliability": round(accuracy, 2),
            "freshness": freshness, "geographic_precision": proximity, "evidence_quality": evidence,
            "timestamp_consistency": consistency, "bias_risk": bias_risk, "bias_note": bias or None, "official": official}


def _provider(feed: str) -> str:
    from app.intelligence.feeds import BY_KEY

    known = BY_KEY.get(feed)
    return known.title.split(" · ")[0] if known else feed


def _same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    family = {"attack": "security", "conflict": "security"}
    if family.get(a["category"], a["category"]) != family.get(b["category"], b["category"]):
        return False
    if a.get("latitude") is None or b.get("latitude") is None:
        return False
    distance = distance_km(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    ta, tb = _when(a), _when(b)
    gap = abs((ta - tb).total_seconds()) / 3600 if ta and tb else 99
    if a["category"] == "earthquakes":
        if a["feed"] == b["feed"] and a["id"] != b["id"]:
            return False
        ma, mb = (a.get("details") or {}).get("magnitude"), (b.get("details") or {}).get("magnitude")
        return distance <= 100 and gap <= 0.025 and (ma is None or mb is None or abs(ma - mb) <= 0.7)
    if a["category"] in SECURITY + ("emergency",):
        overlap = len(_tokens(a["title"]) & _tokens(b["title"])) / max(1, min(len(_tokens(a["title"])), len(_tokens(b["title"]))))
        country_level = "country" in str((a.get("details") or {}).get("location_precision", "")) \
            or "country" in str((b.get("details") or {}).get("location_precision", ""))
        return distance <= 75 and gap <= 36 and overlap >= (0.45 if country_level else 0.3)
    return distance <= 50 and gap <= 48 and a["feed"] != b["feed"]


def build(rows: list[dict[str, Any]], reliability: dict[str, float] | None = None,
          now: datetime | None = None) -> list[dict[str, Any]]:
    """Merge rows into intelligence objects with sources, scores, status and confidence."""
    now = now or datetime.now(UTC)
    reliability = reliability or {}
    candidates = [row for row in rows if row.get("category") in OBJECT_CATEGORIES and row.get("latitude") is not None]
    candidates.sort(key=lambda row: _when(row) or now)
    groups: list[list[dict[str, Any]]] = []
    for row in candidates:
        for group in groups:
            if any(_same_event(row, member) for member in group[-6:]):
                group.append(row)
                break
        else:
            groups.append([row])
    return [_object(group, reliability, now) for group in groups]


def _object(group: list[dict[str, Any]], reliability: dict[str, float], now: datetime) -> dict[str, Any]:
    sources = []
    for row in group:
        details = row.get("details") or {}
        sources.append({"id": row["id"], "feed": row["feed"], "outlet": details.get("outlet") or details.get("provider") or _provider(row["feed"]),
                        "title": row.get("title"), "url": row.get("url"), "at": (_when(row) or now).isoformat(timespec="seconds"),
                        "disputes": bool(details.get("disputes")), "stale": bool(row.get("stale")),
                        **source_score(row, reliability, now)})
    asserting = [source for source in sources if not source["disputes"]]
    disputing = [source for source in sources if source["disputes"]]
    independent = {str(source["outlet"]).strip().lower() for source in asserting}
    category = group[0]["category"]
    official = any(source["official"] for source in asserting)
    reputable = {str(source["outlet"]).strip().lower() for source in asserting if source["authority"] >= 0.7}
    def independent_weight(items):
        weights = {}
        for source in items:
            key = str(source["outlet"]).strip().lower()
            weights[key] = max(weights.get(key, 0), source["score"])
        return sum(weights.values())

    if disputing and independent_weight(disputing) > independent_weight(asserting):
        status, reason = "FALSE/CONTRADICTED", f"{len(disputing)} source(s) dispute this and outweigh those asserting it."
    elif category in SECURITY:
        if official and len(reputable) >= 2 and not disputing:
            status, reason = "CONFIRMED", "An authoritative event source and another reputable outlet report it."
        elif len(independent) >= 2:
            status, reason = "LIKELY", f"{len(independent)} distinct outlets report it; independent reporting is not established."
        elif official:
            status, reason = "LIKELY", "Reported by an official body alone; independent confirmation is still needed."
        else:
            status, reason = "UNVERIFIED", "A single outlet reports this; conflict reports need independent confirmation."
    elif official:
        status, reason = "CONFIRMED", "Reported by the agency responsible for this kind of event."
    elif len(independent) >= 2:
        status, reason = "LIKELY", f"{len(independent)} distinct sources; independent reporting is not established."
    else:
        status, reason = "UNVERIFIED", "A single source."
    if disputing and status != "FALSE/CONTRADICTED":
        reason += f" Disputed by {', '.join(item['outlet'] for item in disputing)}."
    weights = [min(0.95, item["score"]) for item in {source["outlet"]: source for source in asserting}.values()]
    confidence = 1 - math.prod(1 - weight * 0.6 for weight in weights) if weights else 0.1
    confidence = min(confidence, {"UNVERIFIED": 0.5, "LIKELY": 0.8, "FALSE/CONTRADICTED": 0.2}.get(status, 0.97))
    best = max(group, key=lambda row: (is_official(row), (row.get("details") or {}).get("authority") or 0,
                                        "city" in str((row.get("details") or {}).get("location_precision", ""))))
    located = min(group, key=lambda row: 0 if not (row.get("details") or {}).get("location_precision") else
                  1 if "city" in str(row["details"]["location_precision"]) else 2)
    times = [_when(row) for row in group if _when(row)]
    identifier = "object:" + hashlib.sha256(min(row["id"] for row in group).encode()).hexdigest()[:16]
    return {"id": identifier, "title": best.get("headline") or best["title"], "category": category,
            "kind": best.get("kind"), "severity": max((row.get("severity_rank", 0) for row in group), default=0),
            "latitude": located["latitude"], "longitude": located["longitude"],
            "place": (located.get("details") or {}).get("place") or (located.get("details") or {}).get("region"),
            "location_precision": (located.get("details") or {}).get("location_precision") or "provider coordinates",
            "first_reported": min(times).isoformat(timespec="seconds") if times else None,
            "last_reported": max(times).isoformat(timespec="seconds") if times else None,
            "status": status, "status_reason": reason, "confidence": round(confidence, 2),
            "confidence_basis": "Heuristic evidence weight, not a calibrated probability. Distinct outlets may share an original report.",
            "independent_sources": len(independent), "sources": sorted(sources, key=lambda item: item["at"]),
            "alternatives": ALTERNATIVES.get(category, "Other explanations are possible; check the sources."),
            "member_ids": [row["id"] for row in group]}


class Objects:
    """Objects built from the manager's current rows, cached per store revision, and kept as history."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self._cache: tuple[int, list[dict[str, Any]]] | None = None
        self._lock = threading.RLock()

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._all()

    def _all(self) -> list[dict[str, Any]]:
        revision = getattr(self.manager.store, "revision", 0)
        if self._cache and self._cache[0] == revision:
            return self._cache[1]
        _sources, rows = self.manager._matching("", "", None, True)
        reliability = {}
        try:
            for feed, entry in self.manager.store.health_summary().items():
                if entry["attempts"]:
                    reliability[feed] = entry["successes"] / entry["attempts"]
        except Exception:
            log_swallowed(_log, "reading feed reliability for source scores")
            reliability = {}
        objects = build(rows, reliability)
        try:
            previous = self.manager.store.objects_since("", limit=20000)
            by_member = {member: item for item in previous for member in item.get('member_ids', [])}
            used = set()
            for item in objects:
                matches = {old['id']: old for member in item['member_ids'] if (old := by_member.get(member))}
                matches = {key: old for key, old in matches.items() if key not in used}
                if matches:
                    old = min(matches.values(), key=lambda value: value.get('first_reported') or '')
                    item['id'] = old['id']
                    first_times = [stamp for stamp in (item.get('first_reported'), old.get('first_reported')) if stamp]
                    item['first_reported'] = min(first_times) if first_times else None
                    item['supersedes'] = [key for key in matches if key != old['id']]
                    item['member_ids'] = list(dict.fromkeys([*old['member_ids'], *item['member_ids']]))[:200]
                    used.update(matches)
            self.manager.store.save_objects(objects)
        except Exception:
            log_swallowed(_log, "keeping intelligence objects as history")
        self._cache = (revision, objects)
        return objects

    def query(self, category: str = "", status: str = "", since_hours: float | None = None, place: str = "",
              latitude: float | None = None, longitude: float | None = None, radius_km: float = 300,
              limit: int = 50, sort: str = "") -> dict[str, Any]:
        from app.intelligence.feeds import coordinates
        if (latitude is None) != (longitude is None):
            raise ValueError("Supply both latitude and longitude.")
        if place and latitude is None:
            resolved = self.manager.fusion.resolve(place)
            latitude, longitude = resolved["latitude"], resolved["longitude"]
        if latitude is not None and coordinates([longitude, latitude])[0] is None:
            raise ValueError("Latitude and longitude must be a finite map position.")
        if latitude is not None:
            latitude, longitude = coordinates([longitude, latitude])
        if not math.isfinite(float(radius_km)) or float(radius_km) <= 0:
            raise ValueError("radius_km must be finite and positive.")
        radius_km = float(radius_km)
        if since_hours is not None and (not math.isfinite(float(since_hours)) or float(since_hours) <= 0):
            raise ValueError("since_hours must be finite and positive.")
        order = sort or ("nearest" if latitude is not None else "priority")
        if order == "nearest" and latitude is None:
            raise ValueError("Nearest sorting requires a place or coordinates.")
        current = self.all()
        if since_hours:
            cutoff = (datetime.now(UTC) - timedelta(hours=float(since_hours))).isoformat()
            retained = self.manager.store.objects_since(cutoff)
            found = list({item['id']: item for item in [*retained, *current]}.values())
        else:
            found = current
        if category:
            wanted = {"conflicts": "conflict", "attacks": "attack", "fires": "wildfires", "storms": "severe_weather",
                      "emergencies": "emergency", "quakes": "earthquakes"}.get(category, category)
            found = [item for item in found if item["category"] == wanted or (wanted == "security" and item["category"] in SECURITY)]
        if status:
            found = [item for item in found if item["status"].startswith(status.upper())]
        if since_hours:
            cutoff_time = datetime.now(UTC) - timedelta(hours=float(since_hours))
            found = [item for item in found if reported_at(item) >= cutoff_time]
        if latitude is not None and longitude is not None:
            found = [{**item, "distance_km": round(distance_km(latitude, longitude, item["latitude"], item["longitude"]), 1)}
                     for item in found if coordinates([item.get("longitude"), item.get("latitude")])[0] is not None]
            found = [item for item in found if item["distance_km"] <= radius_km]
        found = sort_objects(found, order)
        counts: dict[str, int] = {}
        for item in found:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"intelligence": True, "intelligence_objects": True, "total": len(found), "by_status": counts,
                "sort": order,
                "objects": found[:max(1, min(int(limit), 200))],
                "basis": "Reports grouped by place, time and wording, using distinct outlets as a corroboration proxy "
                         "and authoritative event sources for confirmation. Grouping, denial wording and weights "
                         "are heuristic; inspect the linked sources. Repetition alone is not independent verification. "
                         "Recent reports do not establish that an event is still active."}

    def get(self, object_id: str) -> dict[str, Any]:
        current = self.all()
        for item in [*current, *self.manager.store.objects_since("", limit=20000)]:
            if item["id"] == object_id or object_id in item["member_ids"]:
                return item
        raise ValueError("No intelligence object or member record with that id.")


def correlate(manager: Any, object_id: str, radius_km: float = 50.0) -> dict[str, Any]:
    """Everything observed near an event in place and time -- evidence to examine, not proof of cause."""
    objects = getattr(manager, "objects", None) or Objects(manager)
    item = objects.get(object_id)
    lat, lon = item["latitude"], item["longitude"]
    moment = datetime.fromisoformat(item["first_reported"]) if item.get("first_reported") else datetime.now(UTC)
    precision = item["location_precision"]
    if "country" in precision:
        radius_km = max(radius_km, 300.0)
    found: dict[str, Any] = {"intelligence": True, "correlation": True, "object": item, "radius_km": radius_km,
                             "moment": moment.isoformat(timespec="seconds")}
    try:
        cameras = manager.cameras(near=[lat, lon], radius_km=min(radius_km, 100), available_only=True, limit=8)["cameras"]
        found["cameras"] = [{"id": row["id"], "title": row["title"], "distance_km": row.get("distance_km"),
                             "provider": row["details"].get("provider")} for row in cameras]
    except Exception:
        found["cameras"] = []
    history = getattr(manager, "history", None)
    if history is not None:
        earlier = history.near(lat, lon, radius_km, moment - timedelta(minutes=30), window_minutes=30)
        later = history.near(lat, lon, radius_km, moment + timedelta(minutes=30), window_minutes=30)
        before = [craft for craft in earlier["found"] if craft["at"] <= moment.isoformat()]
        after = [craft for craft in later["found"] if craft["at"] > moment.isoformat()]
        found["aircraft"] = {"before": before[:25], "after": after[:25],
                             "coverage": {"before": earlier["coverage"], "after": later["coverage"]}, "basis": earlier["basis"]}
        vessels = history.near(lat, lon, radius_km, moment, window_minutes=120, kind="vessel")
        found["vessels"] = vessels["found"][:25]
    else:
        found["aircraft"] = {"before": [], "after": [], "basis": "No position history is being kept in this session."}
    _sources, rows = manager._matching("", "", None, True)
    window = timedelta(hours=48)
    nearby = []
    for row in rows:
        if row["id"] in item["member_ids"] or row.get("category") == "cameras":
            continue
        distance = distance_km(lat, lon, row["latitude"], row["longitude"])
        when = _when(row)
        if distance <= radius_km and (when is None or abs(when - moment) <= window):
            nearby.append({"id": row["id"], "title": row.get("headline") or row["title"], "category": row["category"],
                           "official": is_official(row), "distance_km": round(distance, 1),
                           "direction": bearing(lat, lon, row["latitude"], row["longitude"]),
                           "at": when.isoformat(timespec="seconds") if when else None, "feed": row["feed"]})
    nearby.sort(key=lambda entry: entry["distance_km"])
    found["nearby"] = nearby[:25]
    found["official_alerts"] = [entry for entry in nearby if entry["official"]][:15]
    found["fires"] = [entry for entry in nearby if entry["category"] == "wildfires"][:10]
    found["news"] = [entry for entry in nearby if entry["category"] in ("news", "attack", "conflict", "emergency")
                     and not entry["official"]][:15]
    try:
        found["history"] = [entry for entry in manager.store.objects_near(lat, lon, max(radius_km, 100))
                            if entry["id"] != item["id"] and (entry.get("first_reported") or "") < (moment - timedelta(hours=24)).isoformat()][:10]
    except Exception:
        found["history"] = []
    live = getattr(manager, "live", None)
    if live is not None and datetime.now(UTC) - moment < timedelta(hours=3):
        try:
            found["weather_now"] = live.weather(lat, lon)
        except Exception as exc:
            found["weather_now"] = {"unavailable": f"{type(exc).__name__}"}
    found["satellite"] = {"layer": "NASA GIBS true colour (atlas Satellite layer)", "fire_detections":
                          "NASA FIRMS active-fire detections need a free MAP_KEY (not configured)."}
    found["caution"] = ("Correlation is not causation. These are things observed near the same place and time; "
                        "none of them shows what caused the event or who was responsible.")
    found["alternatives"] = item["alternatives"]
    return found
