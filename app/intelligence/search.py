"""One search across everything HAYAT knows about the world.

A query is read for what it names -- a callsign, a registration, an ICAO hex address, a
ship's MMSI, an airport code, a place with a radius, a time range, an event type, a
country, a source -- and each part is answered from the right store. Free words search
every event record and merged intelligence object.
"""

from __future__ import annotations

import re
from typing import Any

from app.intelligence.gazetteer import COUNTRIES, locate
from app.intelligence.feeds import coordinates as valid_coordinates
from app.intelligence.objects import sort_objects

EVENT_WORDS = {"conflict": "conflict", "conflicts": "conflict", "war": "conflict", "wars": "conflict", "fighting": "conflict",
               "attack": "attack", "attacks": "attack", "explosion": "attack", "explosions": "attack", "airstrike": "attack",
               "airstrikes": "attack", "missile": "attack", "drone": "attack", "strikes": "attack",
               "fire": "wildfires", "fires": "wildfires", "wildfire": "wildfires", "wildfires": "wildfires",
               "earthquake": "earthquakes", "earthquakes": "earthquakes", "quake": "earthquakes", "quakes": "earthquakes",
               "flood": "floods", "floods": "floods", "storm": "severe_weather", "storms": "severe_weather",
               "hurricane": "severe_weather", "hurricanes": "severe_weather", "cyclone": "severe_weather",
               "emergency": "emergency", "emergencies": "emergency", "volcano": "volcanoes", "volcanoes": "volcanoes",
               "airport": "aviation", "airports": "aviation", "traffic": "traffic", "road": "traffic"}
_HOURS = re.compile(r"(?i)\b(?:last|past|previous)\s+(\d{1,3}|an?|one|two|three|six|twelve|twenty-four)\s*(hours?|hrs?|days?|minutes?)\b")
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "six": 6, "twelve": 12, "twenty-four": 24}
_RADIUS = re.compile(r"(?i)\bwithin\s+(\d{1,4}(?:\.\d+)?)\s*(miles?|mi|km|kilometres?|kilometers?|nm)\b")
_NEAR = re.compile(r"(?i)\b(?:near|around|in|at|over|of)\s+(.+?)(?=\s+(?:last|past|previous|within|today|now|currently|and)\b|$)")
_MMSI = re.compile(r"\b(\d{9})\b")
_HEX = re.compile(r"(?i)\b(?:hex\s+)?([0-9a-f]{6})\b")
_REGISTRATION = re.compile(r"\b([A-Z]{1,2}-[A-Z0-9]{3,5}|N\d{1,5}[A-Z]{0,2})\b")
_CALLSIGN = re.compile(r"\b([A-Z]{3}\d{1,4}[A-Z]?)\b")
_AIRPORT = re.compile(r"\b([A-Z]{4}|[A-Z]{3})\b(?=\s*(?:airport|international)?)")


def parse(query: str) -> dict[str, Any]:
    text = " ".join(str(query or "").split())
    found: dict[str, Any] = {"text": text}
    if hours := _HOURS.search(text):
        amount = _NUMBER_WORDS.get(hours[1].lower()) or int(hours[1])
        unit = hours[2].lower()
        found["since_hours"] = amount * 24 if unit.startswith("day") else amount / 60 if unit.startswith("min") else amount
    elif re.search(r"(?i)\b(?:today|right now|now|currently|active)\b", text):
        found["since_hours"] = 24
    if radius := _RADIUS.search(text):
        value, unit = float(radius[1]), radius[2].lower()
        found["radius_km"] = round(value * (1.609 if unit.startswith("mi") else 1.852 if unit == "nm" else 1), 1)
    categories = {category for word, category in EVENT_WORDS.items() if re.search(rf"(?i)\b{word}\b", text)}
    if categories:
        found["categories"] = sorted(categories)
    if mmsi := _MMSI.search(text):
        found["mmsi"] = mmsi[1]
    if registration := _REGISTRATION.search(text):
        found["registration"] = registration[1]
    if callsign := _CALLSIGN.search(text):
        found["callsign"] = callsign[1]
    if hexcode := re.search(r"(?i)\bhex\s+([0-9a-f]{6})\b", text):
        found["hex"] = hexcode[1].lower()
    if near := _NEAR.search(text):
        found["place"] = near[1].strip(" ,.!?")
    elif (re.search(r"(?i)\bairport\b", text) or re.fullmatch(r"[A-Z]{3,4}", text)) and (airport := _AIRPORT.search(text)):
        if airport[1] not in ("ADS", "AIS", "UTC", "MMSI", "ICAO"):
            found["place"] = airport[1]
    countries = [name for name in sorted(COUNTRIES, key=len, reverse=True)
                 if len(name) > 3 and re.search(rf"(?i)\b{re.escape(name)}\b", text)]
    if countries:
        found["country"] = countries[0]
    return found


def search(manager: Any, query: str, limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    wanted = parse(query)
    if not wanted["text"]:
        raise ValueError("Say what to search for: a place, aircraft, ship, airport, event type, country or keyword.")
    sections: dict[str, Any] = {}
    aviation, history, objects = (getattr(manager, name, None) for name in ("aviation", "history", "objects"))
    # Aircraft by identity.
    for key in ("hex", "registration", "callsign"):
        if wanted.get(key) and aviation is not None:
            try:
                aircraft = aviation.lookup(wanted[key])
            except Exception as exc:
                aircraft, sections["aircraft_error"] = [], f"{type(exc).__name__}: {exc}"
            sections["aircraft"] = aircraft[:10]
            if wanted.get("callsign"):
                try:
                    sections["route"] = aviation.route(wanted["callsign"])
                except Exception:
                    sections["route"] = None
            if history is not None:
                ident = aircraft[0]["hex"] if aircraft else wanted[key]
                sections["track"] = history.track(ident, hours=wanted.get("since_hours") or 6)[-200:]
            break
    if wanted.get("mmsi") and history is not None:
        sections["vessel_track"] = history.track(wanted["mmsi"], hours=wanted.get("since_hours") or 24, kind="vessel")[-200:]
    # A place: airports first (exact codes), then the gazetteer and the geocoder.
    point = None
    if wanted.get("place"):
        from app.intelligence import airports

        airport = airports.find(manager.store.path.parent, wanted["place"]) if len(wanted["place"]) in (3, 4) else None
        if airport:
            point = (airport["latitude"], airport["longitude"], f"{airport['name']} ({airport['iata'] or airport['icao']})")
            sections["airport"] = airport
        else:
            coordinates = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)", wanted["place"])
            if coordinates:
                point = (float(coordinates[1]), float(coordinates[2]), wanted["place"])
                if valid_coordinates([point[1], point[0]])[0] is None:
                    raise ValueError("Coordinates must be a finite latitude from -90 to 90 and longitude from -180 to 180.")
            elif located := locate(wanted["place"]):
                point = (located["latitude"], located["longitude"], located["place"])
            elif getattr(manager, "live", None) is not None:
                try:
                    geocoded = manager.live.geocode(wanted["place"])
                except Exception:
                    geocoded = None
                if geocoded:
                    point = (geocoded["latitude"], geocoded["longitude"], geocoded["name"])
    elif wanted.get("country"):
        latitude, longitude = COUNTRIES[wanted["country"]]
        point = (latitude, longitude, wanted["country"].title())
        wanted.setdefault("radius_km", 800)
    radius = wanted.get("radius_km") or 100
    if wanted.get("place") and point is None:
        return {"intelligence": True, "search": True, "query": wanted, "sections": sections,
                "basis": f"Could not resolve {wanted['place']!r}; no unrelated worldwide records were substituted."}
    if point:
        sections["place"] = {"name": point[2], "latitude": point[0], "longitude": point[1], "radius_km": radius}
    # Merged events.
    if objects is not None:
        categories = wanted.get("categories") or [""]
        merged: list[dict[str, Any]] = []
        for category in categories:
            result = objects.query(category=category, since_hours=wanted.get("since_hours"),
                                   latitude=point[0] if point else None, longitude=point[1] if point else None,
                                   radius_km=radius, limit=limit)
            merged += result["objects"]
        if not point and not wanted.get("categories"):
            words = [word for word in re.findall(r"[a-z]{4,}", wanted["text"].lower())]
            merged = [item for item in merged if any(word in item["title"].lower() for word in words)] if words else merged
        merged = list({item["id"]: item for item in merged}.values())
        sections["events"] = sort_objects(merged, "nearest" if point else "priority")[:limit]
    # Aircraft observed around the place (live now, and from history when a time range was asked).
    if point and (re.search(r"(?i)\b(?:aircraft|planes?|flights?|air activity|helicopters?|military)\b", wanted["text"])):
        live = getattr(manager, "live", None)
        if live is not None:
            try:
                sections["aircraft_now"] = live.aircraft(point[0], point[1], min(radius, 460))[:100]
            except Exception as exc:
                sections["aircraft_now_error"] = f"{type(exc).__name__}: {exc}"
    if point and re.search(r"(?i)\bcameras?\b", wanted["text"]):
        cameras = manager.cameras(near=[point[0], point[1]], radius_km=min(radius, 200), available_only=True, limit=10)
        sections["cameras"] = [{"id": row["id"], "title": row["title"], "distance_km": row.get("distance_km")} for row in cameras["cameras"]]
    # Free text across every raw record, as a fallback and for sources and keywords.
    if not point and not wanted.get("categories") and not sections.get("events") and not sections.get("aircraft"):
        words = re.sub(r"(?i)\b(?:show|find|search|me|all|the|for|what|which|are|is|events?|reports?)\b", " ", wanted["text"]).strip()
        if words:
            records = manager.dashboard(query=words[:200], limit=limit)
            sections["records"] = [{"id": row["id"], "title": row.get("headline") or row["title"], "category": row["category"],
                                    "feed": row["feed"], "observed_at": row.get("observed_at")} for row in records["events"]]
    return {"intelligence": True, "search": True, "query": wanted, "sections": sections,
            "basis": "Each part of the query was answered from its own source: aircraft from ADS-B, tracks from "
                     "HAYAT's own position history, events from merged intelligence objects, places from the "
                     "gazetteer, airport table or geocoder."}
