"""Unusual patterns worth a look -- labelled as anomalies, never as conclusions.

Each anomaly says what was measured, what would be normal, and ordinary explanations.
Nothing here infers a military operation or any hidden purpose from public tracking
data; a change in what transponders broadcast is reported as exactly that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.intelligence.history import holding_patterns
from app.intelligence.live import distance_km

LABEL = "ANOMALY -- an unusual pattern to look at, not a conclusion"


def scan(manager: Any, latitude: float | None = None, longitude: float | None = None, radius_km: float = 150.0,
         hours: float = 6.0) -> dict[str, Any]:
    found: list[dict[str, Any]] = []
    history = getattr(manager, "history", None)
    aviation = getattr(manager, "aviation", None)
    regional = latitude is not None and longitude is not None
    # 1. Emergency squawks worldwide (or in the region): 7500, 7600, 7700.
    if aviation is not None:
        try:
            for craft in aviation.emergencies():
                if regional and distance_km(latitude, longitude, craft["latitude"], craft["longitude"]) > radius_km:
                    continue
                found.append({"type": "emergency squawk", "label": LABEL, "subject": craft["flight"],
                              "latitude": craft["latitude"], "longitude": craft["longitude"],
                              "measured": f"squawk {craft['squawk']} ({craft['squawk_meaning']})",
                              "normal": "almost no aircraft squawk an emergency code",
                              "alternatives": "Medical emergencies, technical faults, radio failure and mistaken "
                                              "codes are far more common than hijacking; 7700 is often precautionary."})
        except Exception as exc:
            found.append({"type": "check unavailable", "label": "emergency squawks", "measured": type(exc).__name__})
    if history is not None and regional:
        # 2. Holding or circling patterns among aircraft recorded in the region.
        moment = datetime.now(UTC)
        recent = history.near(latitude, longitude, radius_km, moment - timedelta(minutes=15), window_minutes=15)
        for craft in recent["found"][:60]:
            track = history.track(craft["ident"], hours=1)
            pattern = holding_patterns(track)
            if pattern:
                found.append({"type": pattern["anomaly"], "label": LABEL, "subject": craft["label"] or craft["ident"],
                              "latitude": craft["latitude"], "longitude": craft["longitude"],
                              "measured": f"turned {pattern['turned_degrees']} degrees within {pattern['within_km']} km "
                                          f"in {pattern['minutes']} minutes", "normal": "aircraft usually fly fairly straight legs",
                              "alternatives": pattern["alternatives"]})
        # 3. Traffic appearing or disappearing: distinct aircraft per hour against the earlier hours.
        degrees = radius_km / 111.0
        counts = history.counts(latitude - degrees, longitude - degrees * 1.5, latitude + degrees, longitude + degrees * 1.5,
                                hours=int(max(3, hours)))
        if len(counts) >= 4:
            earlier = [entry["distinct"] for entry in counts[:-1]]
            usual = sorted(earlier)[len(earlier) // 2]
            now = counts[-1]["distinct"]
            if usual >= 5 and (now <= usual * 0.3 or now >= usual * 3):
                found.append({"type": "aircraft traffic dropped" if now < usual else "aircraft traffic surged",
                              "label": LABEL, "subject": "aircraft in the region", "latitude": latitude, "longitude": longitude,
                              "measured": f"{now} distinct aircraft this hour against a usual {usual}",
                              "normal": f"about {usual} an hour over the last {len(earlier)} recorded hours",
                              "alternatives": "Receiver coverage gaps, weather, time-of-day patterns, airspace "
                                              "restrictions or simply fewer flights; transponders switched off are "
                                              "not evidence of anything by themselves."})
        vessel_counts = history.counts(latitude - degrees, longitude - degrees * 1.5, latitude + degrees,
                                       longitude + degrees * 1.5, hours=int(max(3, hours)), kind="vessel")
        if len(vessel_counts) >= 4:
            earlier = [entry["distinct"] for entry in vessel_counts[:-1]]
            usual = sorted(earlier)[len(earlier) // 2]
            now = vessel_counts[-1]["distinct"]
            if usual >= 5 and now >= usual * 2.5:
                found.append({"type": "unusual concentration of ships", "label": LABEL, "subject": "vessels in the region",
                              "latitude": latitude, "longitude": longitude,
                              "measured": f"{now} ships against a usual {usual}", "normal": f"about {usual}",
                              "alternatives": "Weather shelter, port congestion, a regatta or fishing season."})
    # 4. Several independent reports from the same area.
    objects = getattr(manager, "objects", None)
    if objects is not None:
        since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        for item in objects.all():
            if (item.get("last_reported") or "") < since or item["independent_sources"] < 3:
                continue
            if regional and distance_km(latitude, longitude, item["latitude"], item["longitude"]) > radius_km:
                continue
            found.append({"type": "multiple reporting outlets", "label": LABEL, "subject": item["title"],
                          "latitude": item["latitude"], "longitude": item["longitude"],
                          "measured": f"{item['independent_sources']} distinct reporting outlets within {hours:g} h ({item['status']})",
                          "normal": "most events are reported by one or two sources", "object_id": item["id"],
                          "alternatives": "Outlets often repeat one original report; check whether they are truly independent."})
    # 5. Rapid weather change at places HAYAT has been watching (from stored observations).
    return {"intelligence": True, "anomalies": True, "count": len(found), "found": found,
            "region": {"latitude": latitude, "longitude": longitude, "radius_km": radius_km} if regional else "world",
            "basis": "Anomalies come only from data HAYAT observed. They are prompts to look closer: none of them "
                     "establishes what happened or why, and none implies a hidden operation."}
