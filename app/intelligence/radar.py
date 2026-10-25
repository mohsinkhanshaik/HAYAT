"""Around me: every live hazard near a place, by distance and bearing, as a radar.

One pass over the events HAYAT already holds (no new requests): earthquakes,
storms, fires, floods, volcanoes, tsunami bulletins, outbreaks, air quality,
airspace hazards, fireballs, road closures close by. Each comes back with its
distance, compass bearing and the publisher-based severity, sorted most serious
first, then nearest. The place is the owner's location by default (Windows
location, else the approximate IP location, and the answer says which), or any
named place or coordinates.

``heads_up`` picks what deserves interrupting the owner: a medium or high
severity hazard inside the inner radius that was not shown before. The GUI
calls it on its own refresh; nothing here talks to the network.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.intelligence.live import bearing, distance_km

#: Categories that are places to look or opinions, not hazards near you.
NOT_HAZARDS = {"cameras", "news"}
#: Road events matter only close by.
TRAFFIC_KM = 50.0
#: Disease reports are placed at a country's centre, so their distance is only meaningful within the country.
COUNTRY_LEVEL_NOTE = "placed at the centre of the country, not where it is happening"


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    return round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)


def bbox_around(latitude: float, longitude: float, radius_km: float) -> list[float]:
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * math.cos(math.radians(latitude)))
    return [max(-180.0, longitude - dlon), max(-90.0, latitude - dlat), min(180.0, longitude + dlon), min(90.0, latitude + dlat)]


def radar(manager: Any, place: str = "", latitude: float | None = None, longitude: float | None = None,
          radius_km: float = 500.0, since_hours: float = 72.0, limit: int = 60) -> dict[str, Any]:
    """Hazards within ``radius_km`` of the place, most serious first, then nearest."""
    radius_km = max(10.0, min(float(radius_km or 500), 3000.0))
    since_hours = max(1.0, min(float(since_hours or 72), 720.0))
    if latitude is not None and longitude is not None:
        home = {"name": place or f"{latitude:.3f}, {longitude:.3f}", "latitude": float(latitude), "longitude": float(longitude)}
    else:
        home = manager.fusion.resolve(place or "here")
    lat, lon = home["latitude"], home["longitude"]
    _sources, rows = manager._matching("", "", bbox_around(lat, lon, radius_km), True, "", "", since_hours)
    found = []
    now = datetime.now(UTC)
    for row in rows:
        if row.get("category") in NOT_HAZARDS or row.get("latitude") is None:
            continue
        distance = distance_km(lat, lon, row["latitude"], row["longitude"])
        if distance > radius_km or (row.get("category") == "traffic" and distance > TRAFFIC_KM):
            continue
        observed = row.get("observed_at")
        try:
            future = bool(observed) and datetime.fromisoformat(str(observed).replace("Z", "+00:00")) > now + timedelta(hours=1)
        except ValueError:
            future = False
        details = row.get("details") or {}
        found.append({
            "id": row["id"], "title": row.get("headline") or row.get("title"), "category": row.get("category"),
            "category_title": row.get("category_title"), "severity": row.get("severity"),
            "severity_rank": row.get("severity_rank", 0), "basis": row.get("severity_basis"),
            "distance_km": round(distance, 1), "bearing": bearing(lat, lon, row["latitude"], row["longitude"]),
            "bearing_deg": bearing_degrees(lat, lon, row["latitude"], row["longitude"]),
            "observed_at": observed, "scheduled": future, "provider": row.get("provider"), "url": row.get("url"),
            "latitude": row["latitude"], "longitude": row["longitude"],
            "note": COUNTRY_LEVEL_NOTE if "country" in str(details.get("location_precision") or "") else "",
        })
    found.sort(key=lambda item: (-item["severity_rank"], item["distance_km"]))
    serious = [item for item in found if item["severity"] in ("high", "medium") and not item["scheduled"]]
    nearest = min(found, key=lambda item: item["distance_km"]) if found else None
    if not found:
        summary = f"No hazards within {radius_km:.0f} km of {home['name']} in the last {since_hours:.0f} h."
    else:
        summary = (f"{len(found)} hazard(s) within {radius_km:.0f} km of {home['name']} in the last {since_hours:.0f} h; "
                   f"{len(serious)} medium or high.")
        if serious:
            top = serious[0]
            summary += f" Most serious: {top['title']} ({top['severity']}), {top['distance_km']:.0f} km {top['bearing']}."
        if nearest:
            summary += f" Nearest: {nearest['title']}, {nearest['distance_km']:.0f} km {nearest['bearing']}."
    by_category: dict[str, int] = {}
    for item in found:
        by_category[item["category_title"] or item["category"]] = by_category.get(item["category_title"] or item["category"], 0) + 1
    return {"radar_view": True, "home": {key: home.get(key) for key in ("name", "latitude", "longitude")},
            "radius_km": radius_km, "since_hours": since_hours, "hazards": found[:limit], "total": len(found),
            "serious": len(serious), "by_category": by_category, "summary": summary,
            "note": "From the public feeds HAYAT already holds; refresh them for the latest. Distances are to each "
                    "publisher's point, which for regions and countries is their centre."}


class HeadsUp:
    """Remembers what was already shown, so each serious nearby hazard interrupts the owner once."""

    def __init__(self, inner_km: float = 300.0) -> None:
        self.inner_km = inner_km
        self.seen: set[str] = set()

    def new(self, view: dict[str, Any]) -> list[dict[str, Any]]:
        fresh = [item for item in view.get("hazards") or []
                 if item["severity"] in ("high", "medium") and not item["scheduled"]
                 and item["distance_km"] <= self.inner_km and item["id"] not in self.seen and not item["note"]]
        self.seen.update(item["id"] for item in view.get("hazards") or [])
        return fresh


def report(history: list[dict[str, Any]]) -> str | None:
    for event in reversed(history or []):
        if event.get("tool") != "intelligence_radar":
            continue
        result = event.get("result") or {}
        value = result.get("output") or {}
        if not result.get("success") or not value.get("radar_view"):
            continue
        lines = [value["summary"]]
        lines += [f"  {item['severity'].upper():6} {item['distance_km']:>6.0f} km {item['bearing']:<2}  {item['title']}"
                  + (f" ({item['note']})" if item["note"] else "") for item in value["hazards"][:12]]
        if value["total"] > 12:
            lines.append(f"  … and {value['total'] - 12} more.")
        return "\n".join(lines)
    return None


def register_radar_tools(registry: Any, manager: Any) -> None:
    registry.register(
        "intelligence_radar",
        "Around me: every hazard HAYAT holds near a place (default: this computer's location) -- earthquakes, storms, "
        "fires, floods, volcanoes, tsunami bulletins, outbreaks, air quality, airspace hazards, fireballs, nearby road "
        "closures -- with distance, compass bearing and severity, most serious first.",
        {"type": "object", "properties": {
            "place": {"type": "string", "maxLength": 200},
            "latitude": {"type": "number", "minimum": -90, "maximum": 90},
            "longitude": {"type": "number", "minimum": -180, "maximum": 180},
            "radius_km": {"type": "number", "minimum": 10, "maximum": 3000},
            "since_hours": {"type": "number", "minimum": 1, "maximum": 720},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
         "additionalProperties": False},
        "low", "computer_observe", lambda **arguments: radar(manager, **arguments))
