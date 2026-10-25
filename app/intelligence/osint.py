"""The global intelligence layer's tools: aircraft, history, merged events, correlation, anomalies, search, identification.

HAYAT observes, correlates and explains. It does not interfere with aircraft, ships,
communications or any system; it does not bypass authentication, restricted APIs or
private cameras; and it never identifies or tracks private individuals.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class HistorySampler:
    """Every few minutes, record worldwide military aircraft and emergency squawks into the position history.

    Small requests (a few hundred KB an hour), rate-limited upstream; paused while the owner
    has privacy mode on. This is what makes "what was flying near here at 14:20" answerable
    for places nobody happened to be looking at.
    """

    def __init__(self, aviation: Any, interval_seconds: float = 300.0, allowed: Any = None) -> None:
        self.aviation, self.interval = aviation, max(60.0, float(interval_seconds))
        self.allowed = allowed or (lambda: True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last: dict[str, Any] = {}

    def sample(self) -> dict[str, Any]:
        result: dict[str, Any] = {"at": datetime.now(UTC).isoformat(timespec="seconds")}
        for name, call in (("military", self.aviation.military), ("emergencies", self.aviation.emergencies)):
            try:
                result[name] = len(call())
            except Exception as exc:
                result[name] = f"unavailable ({type(exc).__name__})"
        self.last = result
        return result

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if self.allowed():
                    self.sample()
            except Exception:
                log_swallowed(_log, "sampling aircraft history")

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="hayat-aircraft-history", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _moment(text: str) -> datetime:
    """'14:20' (today, UTC, or yesterday if that is still in the future), an ISO time, or now."""
    value = str(text or "").strip()
    if not value or value.lower() == "now":
        return datetime.now(UTC)
    if match := re.fullmatch(r"(\d{1,2}):(\d{2})(?:\s*(utc|z))?", value, re.IGNORECASE):
        now = datetime.now(UTC)
        moment = now.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
        return moment - timedelta(days=1) if moment > now + timedelta(minutes=5) else moment
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def register_osint_tools(registry: Any, manager: Any) -> None:
    from app.intelligence import anomalies, objects, search

    place = {"place": {"type": "string", "maxLength": 200},
             "latitude": {"type": "number", "minimum": -90, "maximum": 90},
             "longitude": {"type": "number", "minimum": -180, "maximum": 180},
             "radius_km": {"type": "number", "minimum": 0.5, "maximum": 1000}}

    def where(place_name="", latitude=None, longitude=None):
        if (latitude is None) != (longitude is None):
            raise ValueError("Supply both latitude and longitude.")
        if latitude is not None and longitude is not None:
            return float(latitude), float(longitude), f"{latitude:.3f}, {longitude:.3f}"
        found = manager.fusion.resolve(place_name or "here")
        return found["latitude"], found["longitude"], found["name"]

    def aircraft(mode="near", query="", place="", latitude=None, longitude=None, radius_km=160.0, kind=""):
        aviation = manager.aviation
        if mode == "military":
            found = aviation.military()
        elif mode == "emergencies":
            found = aviation.emergencies()
        elif mode == "lookup":
            found = aviation.lookup(query)
            route_error = None
            try:
                route = aviation.route(found[0]["callsign"] or query) if found else None
            except Exception as exc:
                route, route_error = None, f"Route unavailable ({type(exc).__name__})."
            track = manager.history.track(found[0]["hex"], hours=6) if found else []
            return {"intelligence": True, "aircraft_view": True, "mode": mode, "query": query, "aircraft": found,
                    "route": route, "route_error": route_error, "track": track[-300:], "summary": aviation.summary(found),
                    "basis": "Public ADS-B (adsb.lol). The track is what HAYAT itself recorded; earlier legs it did not see are absent."}
        else:
            lat, lon, name = where(place, latitude, longitude)
            found = manager.live.aircraft(lat, lon, min(float(radius_km or 160), 460))
            if kind:
                found = [item for item in found if item["kind"] == kind]
            return {"intelligence": True, "aircraft_view": True, "mode": "near", "place": name, "radius_km": radius_km,
                    "aircraft": found[:300], "count": len(found), "summary": aviation.summary(found),
                    "basis": "Aircraft broadcasting ADS-B within range of volunteer receivers. Aircraft that do not "
                             "broadcast, including many military ones, are absent; absence proves nothing."}
        if kind:
            found = [item for item in found if item["kind"] == kind]
        return {"intelligence": True, "aircraft_view": True, "mode": mode, "aircraft": found[:500], "count": len(found),
                "summary": aviation.summary(found),
                "basis": "Worldwide public ADS-B. Only aircraft that choose to broadcast appear; HAYAT infers nothing "
                         "about operations from who is or is not visible."}

    def history_tool(place="", latitude=None, longitude=None, radius_km=50.0, at="", window_minutes=10, kind="aircraft"):
        lat, lon, name = where(place, latitude, longitude)
        value = manager.history.near(lat, lon, float(radius_km or 50), _moment(at), int(window_minutes or 10), kind)
        return {"intelligence": True, "history_view": True, "place": name, **value}

    def changes_tool(place="", latitude=None, longitude=None, radius_km=150.0, hours=6.0):
        lat, lon, name = where(place, latitude, longitude)
        since = (datetime.now(UTC) - timedelta(hours=float(hours or 6))).isoformat()
        events = [item for item in manager.objects.query(latitude=lat, longitude=lon, radius_km=float(radius_km or 150),
                                                         limit=200)["objects"] if (item.get("last_reported") or "") >= since]
        degrees = float(radius_km or 150) / 111.0
        traffic = manager.history.counts(lat - degrees, lon - degrees * 1.5, lat + degrees, lon + degrees * 1.5, int(float(hours or 6)))
        found = anomalies.scan(manager, lat, lon, float(radius_km or 150), float(hours or 6))
        return {"intelligence": True, "region_changes": True, "place": name, "hours": hours, "radius_km": radius_km,
                "new_or_updated_events": events[:50], "aircraft_per_hour": traffic, "anomalies": found["found"],
                "basis": "Events merged from public reports; aircraft counts only where HAYAT was recording; "
                         "anomalies are prompts, not conclusions."}

    registry.register(
        "intelligence_aircraft",
        "Publicly broadcast aircraft (ADS-B): around a place (commercial, cargo, private, helicopter, military, "
        "drone), worldwide military, worldwide emergency squawks, or one aircraft by callsign, registration or hex "
        "with its route and recorded track. Only aircraft that broadcast appear.",
        {"type": "object", "properties": {"mode": {"enum": ["near", "military", "emergencies", "lookup"]},
                                          "query": {"type": "string", "maxLength": 20},
                                          "kind": {"enum": ["", "military", "helicopter", "cargo", "commercial", "private", "drone", "unknown"]},
                                          **place}, "additionalProperties": False},
        "low", "public_api_get", aircraft)
    registry.register(
        "intelligence_positions",
        "What aircraft (or ships) HAYAT observed near a place at a time -- '14:20', an ISO time or now -- from its own "
        "seven-day position history. Says plainly when nothing was being recorded then.",
        {"type": "object", "properties": {**place, "at": {"type": "string", "maxLength": 40},
                                          "window_minutes": {"type": "integer", "minimum": 1, "maximum": 180},
                                          "kind": {"enum": ["aircraft", "vessel"]}}, "additionalProperties": False},
        "low", "filesystem_read", history_tool)
    registry.register(
        "intelligence_region_changes",
        "What changed in a region over the last hours: new and updated events, aircraft per hour, and anomalies.",
        {"type": "object", "properties": {**place, "hours": {"type": "number", "minimum": 1, "maximum": 168}},
         "additionalProperties": False}, "low", "public_api_get", changes_tool)
    registry.register(
        "intelligence_objects",
        "Merged world events (conflicts, attacks, emergencies, earthquakes, tsunami bulletins, volcano alerts, fires, "
        "storms, floods, disease outbreaks, airspace hazards, airport disruptions), one "
        "heuristic group per apparent event with sources, scores and status: CONFIRMED, LIKELY, UNVERIFIED or FALSE/CONTRADICTED. "
        "Inspect the sources; distinct outlets may share a report and the scores are not calibrated probabilities.",
        {"type": "object", "properties": {"category": {"type": "string", "maxLength": 30}, "status": {"type": "string", "maxLength": 20},
                                          "since_hours": {"type": "number", "minimum": 0.1, "maximum": 8784},
                                          "sort": {"enum": ["priority", "nearest", "newest", "confidence", "title"]},
                                          "limit": {"type": "integer", "minimum": 1, "maximum": 200}, **place},
         "additionalProperties": False},
        "low", "filesystem_read",
        lambda category="", status="", since_hours=None, limit=50, place="", latitude=None, longitude=None, radius_km=300, sort="":
            manager.objects.query(category, status, since_hours, place,
                                  *(where(place, latitude, longitude)[:2] if (place or latitude is not None) else (None, None)),
                                  radius_km, limit, sort))
    registry.register(
        "intelligence_correlate",
        "Correlate one event with everything near it in place and time: public cameras, aircraft before and after, "
        "ships, weather, fires, official alerts, news and earlier incidents -- with confidence, alternatives, and the "
        "rule that correlation is not causation.",
        {"type": "object", "properties": {"object_id": {"type": "string", "maxLength": 220},
                                          "radius_km": {"type": "number", "minimum": 1, "maximum": 1000}},
         "required": ["object_id"], "additionalProperties": False},
        "low", "public_api_get", lambda object_id, radius_km=50.0: objects.correlate(manager, object_id, radius_km))
    registry.register(
        "intelligence_anomalies",
        "Unusual patterns, labelled as anomalies and never as conclusions: emergency squawks, holding patterns, "
        "traffic disappearing or surging, ship concentrations, many independent reports from one area.",
        {"type": "object", "properties": {**place, "hours": {"type": "number", "minimum": 1, "maximum": 168}},
         "additionalProperties": False}, "low", "public_api_get",
        lambda place="", latitude=None, longitude=None, radius_km=150.0, hours=6.0:
            anomalies.scan(manager, *(where(place, latitude, longitude)[:2] if (place or latitude is not None) else (None, None)),
                           float(radius_km or 150), float(hours or 6)))
    registry.register(
        "intelligence_search",
        "Search everything at once by country, city, coordinates, radius, time range, aircraft callsign, "
        "registration or hex, ship MMSI, airport code, event type, conflict, source or keyword.",
        {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300},
                                          "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
         "required": ["query"], "additionalProperties": False},
        "low", "public_api_get", lambda query, limit=30: search.search(manager, query, limit))


# ------------------------------------------------------------------ routing
_PLACE = r"(?P<place>.+?)"
_ROUTES: tuple[tuple[re.Pattern[str], Any], ...] = (
    (re.compile(r"(?i)^what\s+(?:aircraft|planes|flights)\s+(?:were|was)\s+(?:near|around|over)\s+" + _PLACE
                + r"\s+at\s+(?P<at>\d{1,2}:\d{2}(?:\s*utc)?)\s*[?.!]*$"),
     lambda m: ("intelligence_positions", {"place": m["place"], "at": m["at"]})),
    (re.compile(r"(?i)^(?:show|list|find)\s+(?:me\s+)?(?:all\s+)?(?:publicly\s+trackable\s+|public\s+|live\s+)?"
                r"(?P<kind>military\s+)?(?:aircraft|planes|flights|helicopters)\s+within\s+(?P<radius>\d{1,3})\s*"
                r"(?P<unit>miles?|mi|km)(?:\s+(?:of|around|near)\s+" + _PLACE + r")?\s*[?.!]*$"),
     lambda m: ("intelligence_aircraft", {"mode": "near", "place": m["place"] or "here",
                                          "radius_km": round(float(m["radius"]) * (1.609 if m["unit"].lower().startswith("mi") else 1), 1),
                                          **({"kind": "military"} if m["kind"] else {})})),
    (re.compile(r"(?i)^(?:show|list|find|where\s+are)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:public(?:ly\s+trackable)?\s+)?"
                r"military\s+(?:aircraft|planes|flights|aviation)(?:\s+(?:worldwide|in\s+the\s+world|now|right\s+now))?\s*[?.!]*$"),
     lambda m: ("intelligence_aircraft", {"mode": "military"})),
    (re.compile(r"(?i)^(?:show|list|are\s+there\s+any)\s+(?:aircraft|planes|flights)\s+(?:squawking\s+)?(?:emergenc(?:y|ies)|7700|7600|7500)\s*[?.!]*$"
                r"|^(?:show|list)\s+(?:emergency\s+squawks|aircraft\s+emergencies)\s*[?.!]*$"),
     lambda m: ("intelligence_aircraft", {"mode": "emergencies"})),
    (re.compile(r"(?i)^(?:track|find|where\s+is|show)\s+(?:flight|aircraft|plane|callsign|registration)\s+(?P<query>[A-Z0-9-]{2,10})\s*[?.!]*$"),
     lambda m: ("intelligence_aircraft", {"mode": "lookup", "query": m["query"]})),
    (re.compile(r"(?i)^what\s+conflicts\s+are\s+(?:active|happening|going\s+on)(?:\s+(?:worldwide|in\s+the\s+world|right\s+now|now))*\s*[?.!]*$"),
     lambda m: ("intelligence_objects", {"category": "conflict", "since_hours": 72})),
    (re.compile(r"(?i)^(?:show|list|what\s+were)\s+(?:the\s+)?(?:major|big|important)\s+events\s+(?:that\s+happened\s+)?"
                r"(?:during|in|over)\s+the\s+(?:last|past)\s+(?P<hours>\d{1,3}|24)\s+hours\s*[?.!]*$"),
     lambda m: ("intelligence_objects", {"since_hours": float(m["hours"]), "limit": 40})),
    (re.compile(r"(?i)^what\s+(?:has\s+)?changed\s+in\s+(?:this\s+region|the\s+region|" + _PLACE + r")\s+"
                r"(?:during|in|over)\s+the\s+(?:last|past)\s+(?P<hours>\d{1,3}|six|twelve|24)\s+hours\s*[?.!]*$"),
     lambda m: ("intelligence_region_changes", {"place": m["place"] or "here",
                                                "hours": {"six": 6, "twelve": 12}.get(m["hours"].lower(), None) or float(m["hours"])})),
    (re.compile(r"(?i)^(?:were\s+there|are\s+there|show|find)\s+(?:any\s+)?(?:unusual|anomalous)\s+(?:aircraft\s+|air\s+|flight\s+)?"
                r"(?:patterns|activity|anomalies)(?:\s+(?:near|around|over|in)\s+" + _PLACE + r")?\s*[?.!]*$"),
     lambda m: ("intelligence_anomalies", {"place": m["place"]} if m["place"] else {})),
    (re.compile(r"(?i)^(?:what|which)\s+(?:official\s+)?sources\s+confirm\s+(?:this|the)\s+(?:attack|event|incident|report)\s*[?.!]*$"),
     lambda m: ("intelligence_correlate", {"object_id": "@last"})),
    (re.compile(r"(?i)^compare\s+what\s+happened\s+before\s+and\s+after\s+(?:this|the)\s+(?:incident|event|attack)\s*[?.!]*$"),
     lambda m: ("intelligence_correlate", {"object_id": "@last"})),
    (re.compile(r"(?i)^(?:show\s+)?(?:conflict\s+events\s+and\s+air\s+activity|air\s+activity\s+and\s+conflict\s+events)\s+"
                r"(?:around|near|over|in)\s+" + _PLACE + r"\s*[?.!]*$"),
     lambda m: ("intelligence_search", {"query": f"conflict and aircraft near {m['place']} last 24 hours"})),
    (re.compile(r"(?i)^(?:search|find)\s+(?:global\s+)?intelligence\s+(?:for|about)\s+(?P<query>.+?)\s*[?.!]*$"),
     lambda m: ("intelligence_search", {"query": m["query"]})),
)


def osint_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    for pattern, build in _ROUTES:
        if match := pattern.match(value):
            tool, arguments = build(match)
            return {"tool": tool, "arguments": {key: item for key, item in arguments.items() if item not in (None, "")}}
    return None


def last_object_id(session: Any) -> str | None:
    """A pronoun can name a single object in the last verified task, never guess among a list."""
    for event in reversed(getattr(getattr(session, "last_task", None), "history", []) or []):
        if not event.get("result", {}).get("success") or not event.get("verification", {}).get("passed"):
            continue
        value = event["result"].get("output") or {}
        if not isinstance(value, dict):
            continue
        if value.get("correlation") and value.get("object"):
            return value["object"]["id"]
        if value.get("intelligence_objects") and len(value.get("objects") or []) == 1:
            return value["objects"][0]["id"]
    return None


def osint_report(value: dict[str, Any]) -> str | None:
    """Deliver the observed records without needing a model to invent a narrative."""
    flags = ("aircraft_view", "history_view", "region_changes", "intelligence_objects", "correlation", "anomalies", "search")
    if not any(value.get(flag) for flag in flags):
        return None
    lines = ["Hayat intelligence"]

    def events(items):
        for item in items[:12]:
            lines.append(f"• {item['status']} · {item['title']} ({item['id']})")
            lines.append(f"  {item['status_reason']} Location: {item['location_precision']}.")
            for source in item.get("sources", [])[:4]:
                lines.append(f"  {source['outlet']}: {source['url'] or 'No source URL'}" +
                             (" (disputes the report)" if source.get("disputes") else "") +
                             (" (stale source)" if source.get("stale") else ""))

    def aircraft(items):
        for item in items[:12]:
            lines.append(f"• {item.get('flight') or item.get('label') or item.get('ident') or item.get('hex')} · "
                         f"{item.get('kind') or 'unclassified'} · {item['latitude']:.3f}, {item['longitude']:.3f}" +
                         (f" · observed {item['at']}" if item.get("at") else ""))

    if value.get("aircraft_view"):
        lines.append(f"{value.get('count', len(value['aircraft']))} broadcasting aircraft returned.")
        aircraft(value["aircraft"])
        lines.append(f"{len(value.get('track') or [])} retained track positions.")
        if value.get("route"):
            lines.append("Usual route: " + " → ".join(item.get("icao") or item.get("name") or "?"
                                                        for item in value["route"]["airports"]))
            lines.append(value["route"]["basis"])
        if value.get("route_error"):
            lines.append(value["route_error"])
    elif value.get("history_view"):
        lines.append(f"{len(value['found'])} craft observed near {value['place']} at {value['moment']} "
                     f"(±{value['window_minutes']} minutes).")
        aircraft(value["found"])
    elif value.get("intelligence_objects"):
        lines.append(f"{value['total']} merged event reports match; showing {min(12, len(value['objects']))}.")
        events(value["objects"])
    elif value.get("correlation"):
        events([value["object"]])
        for kind in ("before", "after"):
            items = value["aircraft"].get(kind) or []
            lines.append(f"Aircraft {kind} the report: {len(items)} recorded matches.")
            aircraft(items)
        lines.extend(f"Camera: {row['title']} · {row['distance_km']} km ({row['id']})" for row in value.get("cameras", []))
        lines.extend(f"Nearby {row['category']}: {row['title']} · {row['distance_km']} km" for row in value.get("nearby", [])[:8])
        lines.append(value["caution"])
        lines.append(value["alternatives"])
    elif value.get("region_changes"):
        lines.append(f"Changes around {value['place']} in the last {value['hours']:g} hours.")
        events(value["new_or_updated_events"])
        lines.extend(f"{row['hour']} UTC: {row['distinct']} distinct aircraft observed" for row in value["aircraft_per_hour"])
    elif value.get("search"):
        sections = value["sections"]
        if sections.get("place"):
            lines.append(f"Around {sections['place']['name']} within {sections['place']['radius_km']:g} km.")
        events(sections.get("events") or [])
        aircraft(sections.get("aircraft") or sections.get("aircraft_now") or [])
        for row in (sections.get("records") or [])[:12]:
            lines.append(f"• {row['category']} · {row['title']} ({row['id']})")
        lines.extend(f"Camera: {row['title']} ({row['id']})" for row in sections.get("cameras") or [])
        lines.extend(str(error) for key, error in sections.items() if key.endswith("_error"))
        if not any(sections.get(key) for key in ("events", "aircraft", "aircraft_now", "records", "cameras", "vessel_track")):
            lines.append("No matching observations are retained; this does not establish absence.")
        if sections.get("vessel_track"):
            lines.append(f"{len(sections['vessel_track'])} recorded vessel positions.")
    anomalies = value.get("found", []) if value.get("anomalies") is True else value.get("anomalies", [])
    if isinstance(anomalies, list):
        if value.get("anomalies") is True:
            lines.append(f"{len(anomalies)} patterns or unavailable checks.")
        for item in anomalies[:10]:
            lines.extend([f"• {item['label']} · {item['type']}: {item['measured']}", item.get("alternatives") or ""])
    if value.get("basis"):
        lines.append(value["basis"])
    return "\n".join(line for line in lines if line)
