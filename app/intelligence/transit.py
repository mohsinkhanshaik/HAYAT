"""Live buses, trams and trains in more cities, from the operators' own GTFS-realtime feeds.

Added 2026-11-06 from the transit registry of github.com/bilawalsidhu/gods-eye-view (MIT),
which quotes each operator's licence. Every feed below was fetched through HAYAT's own
fetcher (robots.txt honoured) on that day. Two of that registry's feeds are left out:
Barrie Transit and York Region Transit ask for a licence to be accepted before use, and
accepting a licence is the owner's decision, not HAYAT's.

GTFS-realtime is protobuf. The decoder below reads only the VehiclePosition fields HAYAT
shows, straight from the wire format, so no generated bindings are needed. Licence plates,
which the format allows, are never read.
"""
from __future__ import annotations

import json
import math
import re
import struct
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

STATUS = {0: "arriving", 1: "stopped", 2: "moving"}
OCCUPANCY = {0: "empty", 1: "many seats", 2: "few seats", 3: "standing room only", 4: "crushed standing room only",
             5: "full", 6: "not accepting passengers", 7: "no data", 8: "not boardable"}
#: A report older than this is shown as stale; one older than DROP_SECONDS is not where the vehicle is.
STALE_SECONDS = 600
DROP_SECONDS = 7200


@dataclass(frozen=True)
class TransitFeed:
    key: str
    name: str
    operator: str
    region: str
    latitude: float
    longitude: float
    radius_km: float
    url: str
    license: str
    attribution: str
    headers: tuple[tuple[str, str], ...] = ()
    #: Seconds a snapshot is reused. OVapi asks to be read at most once a minute without conditional requests.
    ttl: int = 20


FEEDS = (
    TransitFeed("capmetro", "CapMetro", "Capital Metropolitan Transportation Authority", "Austin, TX", 30.2672, -97.7431, 60,
                "https://data.texas.gov/download/eiei-9rpf/application%2Foctet-stream", "CapMetro Developer Tools licence",
                "Capital Metropolitan Transportation Authority (data.texas.gov)"),
    TransitFeed("metrotransit", "Metro Transit", "Metro Transit (Metropolitan Council)", "Minneapolis–St Paul, MN", 44.9778, -93.265, 70,
                "https://svc.metrotransit.org/mtgtfs/vehiclepositions.pb", "public Metro Transit vehicle data",
                "Metro Transit, Metropolitan Council"),
    TransitFeed("hsl", "HSL", "Helsinki Region Transport", "Helsinki, Finland", 60.1699, 24.9384, 70,
                "https://realtime.hsl.fi/realtime/vehicle-positions/v2/hsl", "CC BY 4.0", "HSL (Helsinki Region Transport)"),
    TransitFeed("ovapi", "OVapi", "Stichting OpenGeo (NDOV data)", "Netherlands", 52.2, 5.3, 220,
                "https://gtfs.ovapi.nl/nl/vehiclePositions.pb", "free to use, best effort (OVapi README)",
                "OVapi / Stichting OpenGeo", ttl=60),
    TransitFeed("entur", "Entur", "Entur AS (Norwegian national transit data)", "Norway", 64.0, 11.5, 720,
                "https://api.entur.io/realtime/v1/gtfs-rt/vehicle-positions", "NLOD (Norwegian open government data)",
                "Entur, data under NLOD", headers=(("ET-Client-Name", "hayat-assistant"),), ttl=30),
    TransitFeed("translink", "TransLink", "TransLink (Queensland Government)", "South East Queensland, Australia", -27.4698, 153.0251, 150,
                "https://gtfsrt.api.translink.com.au/api/realtime/seq/VehiclePositions", "CC BY 4.0",
                "TransLink, Queensland Government"),
    TransitFeed("ttc", "TTC", "Toronto Transit Commission", "Toronto, ON", 43.6532, -79.3832, 45,
                "https://bustime.ttc.ca/gtfsrt/vehicles", "Open Government Licence – Toronto",
                "Toronto Transit Commission, City of Toronto Open Data"),
    TransitFeed("miway", "MiWay", "MiWay (City of Mississauga)", "Mississauga, ON", 43.589, -79.6441, 30,
                "https://www.miapp.ca/GTFS_RT/Vehicle/VehiclePositions.pb", "City of Mississauga Open Data Terms of Use",
                "MiWay, City of Mississauga Open Data"),
    TransitFeed("hamilton", "Hamilton HSR", "Hamilton Street Railway (City of Hamilton)", "Hamilton, ON", 43.2557, -79.8711, 30,
                "https://opendata.hamilton.ca/GTFS-RT/GTFS_VehiclePositions.pb", "City of Hamilton Open Data Licence",
                "Contains public sector Data made available under the City of Hamilton's Open Data Licence"),
    TransitFeed("durham", "Durham Region Transit", "Durham Region Transit", "Durham Region, ON", 43.9496, -78.9365, 45,
                "https://drtonline.durhamregiontransit.com/gtfsrealtime/VehiclePositions", "Region of Durham Open Data Licence",
                "Contains public sector information made available under The Regional Municipality of Durham's Open Data Licence"),
    TransitFeed("guelph", "Guelph Transit", "Guelph Transit (City of Guelph)", "Guelph, ON", 43.5325, -80.2482, 15,
                "https://glphprdtmgtfs.glphtrpcloud.com/tmgtfsrealtimewebservice/vehicle/vehiclepositions.pb",
                "Open Government Licence – City of Guelph",
                "Contains information licensed under the Open Government Licence – City of Guelph"),
    TransitFeed("kingston", "Kingston Transit", "Kingston Transit (City of Kingston)", "Kingston, ON", 44.24, -76.57, 20,
                "https://api.cityofkingston.ca/gtfs-realtime/vehicleupdates.pb", "City of Kingston Open Data Licence",
                "Contains information licensed under the Open Data Licence – City of Kingston"),
    TransitFeed("burlington", "Burlington Transit", "Burlington Transit (City of Burlington)", "Burlington, ON", 43.3342, -79.8089, 12,
                "https://opendata.burlington.ca/gtfs-rt/GTFS_VehiclePositions.pb", "City of Burlington Open Data Terms of Use",
                "Transit data © City of Burlington (Burlington Transit)"),
    TransitFeed("rio_bus", "Rio buses", "SMTR, Prefeitura do Rio de Janeiro", "Rio de Janeiro, Brazil", -22.92, -43.42, 45,
                "https://dados.mobilidade.rio/gtfs/realtime/onibus/vehicle-positions", "CC BY 4.0",
                "SMTR / Prefeitura da Cidade do Rio de Janeiro (buses)"),
    TransitFeed("rio_brt", "Mobi-Rio BRT", "SMTR, Prefeitura do Rio de Janeiro", "Rio de Janeiro, Brazil", -22.92, -43.42, 45,
                "https://dados.mobilidade.rio/gtfs/realtime/brt/vehicle-positions", "CC BY 4.0",
                "SMTR / Prefeitura da Cidade do Rio de Janeiro (BRT, Mobi-Rio)"),
)
BY_KEY = {feed.key: feed for feed in FEEDS}
#: Hamilton, Guelph, Burlington and Rio publish internal route keys; these are the numbers riders know, from each
#: operator's GTFS routes.txt as tabled in gods-eye-view (see data/THIRD_PARTY.md). A key missing here is shown as published.
ROUTE_NAMES_FILE = Path(__file__).with_name("data") / "transit_routes.json"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@lru_cache(maxsize=1)
def _route_tables() -> dict[str, Any]:
    return json.loads(ROUTE_NAMES_FILE.read_text(encoding="utf-8"))


def route_name(feed: str, route: str) -> str:
    """The route as a rider would say it, or '' when the operator publishes only an opaque key."""
    tables = _route_tables()
    table = tables.get(feed)
    if isinstance(table, str):
        table = tables.get(table)
    if isinstance(table, dict) and route in table:
        return table[route]
    if feed == "entur":
        route = route.rsplit(":Line:", 1)[-1]  # "VYG:Line:R22" → R22
    elif feed == "translink":
        route = re.sub(r"-\d+$", "", route)  # "385-4": the suffix is a timetable version
    elif feed == "ovapi" and re.fullmatch(r"\d{5,}", route):
        return ""  # OVapi sends its own six-digit route keys ("152342"), never the line number on the vehicle
    return "" if UUID.fullmatch(route) else route
#: Covered elsewhere in HAYAT (live.py): MBTA's own API names its routes, and Finland's trains come from Digitraffic.
OTHER_COVERAGE = ("Boston (MBTA)", "Finland (VR trains)")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------- protobuf wire format
def _varint(data: bytes, index: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if index >= len(data) or shift > 63:
            raise ValueError("Truncated or oversized varint in GTFS-realtime data.")
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7


def fields(data: bytes) -> list[tuple[int, int, Any]]:
    """(field number, wire type, value) for one protobuf message; nested messages stay bytes."""
    found, index, end = [], 0, len(data)
    while index < end:
        key, index = _varint(data, index)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, index = _varint(data, index)
        elif wire == 1:
            value, index = data[index:index + 8], index + 8
        elif wire == 2:
            length, index = _varint(data, index)
            value, index = data[index:index + length], index + length
        elif wire == 5:
            value, index = data[index:index + 4], index + 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire} in GTFS-realtime data.")
        if index > end:
            raise ValueError("Truncated GTFS-realtime message.")
        found.append((number, wire, value))
    return found


def _first(message: list[tuple[int, int, Any]], number: int, wire: int) -> Any:
    return next((value for field, kind, value in message if field == number and kind == wire), None)


def _text(value: Any) -> str:
    return bytes(value).decode("utf-8", "replace").strip() if value is not None else ""


def _float(value: Any) -> float | None:
    if value is None or len(value) != 4:
        return None
    number = struct.unpack("<f", bytes(value))[0]
    return number if math.isfinite(number) else None


def decode_vehicle_positions(content: bytes) -> dict[str, Any]:
    """A GTFS-realtime FeedMessage → its timestamp and the vehicles with a usable position."""
    message = fields(bytes(content))
    header = fields(_first(message, 1, 2) or b"")
    vehicles = []
    for number, wire, entity_bytes in message:
        if number != 2 or wire != 2:
            continue
        entity = fields(entity_bytes)
        if _first(entity, 2, 0):  # is_deleted
            continue
        vehicle_bytes = _first(entity, 4, 2)
        if vehicle_bytes is None:
            continue
        vehicle = fields(vehicle_bytes)
        position = fields(_first(vehicle, 2, 2) or b"")
        latitude, longitude = _float(_first(position, 1, 5)), _float(_first(position, 2, 5))
        if latitude is None or longitude is None or not (-90 <= latitude <= 90 and -180 <= longitude <= 180) \
                or (abs(latitude) < 1e-6 and abs(longitude) < 1e-6):
            continue  # no position, or a GPS dropout reported as 0,0
        trip = fields(_first(vehicle, 1, 2) or b"")
        descriptor = fields(_first(vehicle, 8, 2) or b"")  # field 3 there is the licence plate: never read
        speed = _float(_first(position, 5, 5))
        bearing = _float(_first(position, 3, 5))
        status, occupancy = _first(vehicle, 4, 0), _first(vehicle, 9, 0)
        vehicles.append({
            "id": _text(_first(descriptor, 1, 2)) or _text(_first(entity, 1, 2)),
            "label": _text(_first(descriptor, 2, 2)),
            "route": _text(_first(trip, 5, 2)),
            "latitude": round(latitude, 6), "longitude": round(longitude, 6),
            "bearing": round(bearing) % 360 if bearing is not None and 0 <= bearing <= 360 else None,
            "speed_kmh": round(speed * 3.6, 1) if speed is not None and 0 <= speed < 120 else None,
            "status": STATUS.get(status) if status is not None else None,
            "occupancy": OCCUPANCY.get(occupancy) if occupancy is not None else None,
            "timestamp": _first(vehicle, 5, 0),
        })
    return {"timestamp": _first(header, 3, 0), "vehicles": vehicles}


# ---------------------------------------------------------------------- reading the feeds
class WorldTransit:
    """The operators' feeds around a point, each read at most every `ttl` seconds and at most four times a minute."""

    PER_MINUTE = 4

    def __init__(self, fetch: Callable[[str, dict[str, str] | None], bytes], clock: Callable[[], float] = time.time) -> None:
        self.fetch, self.clock = fetch, clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._calls: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self.health: dict[str, dict[str, Any]] = {}

    @staticmethod
    def feeds_near(latitude: float, longitude: float, radius_km: float = 0) -> list[TransitFeed]:
        return [feed for feed in FEEDS if distance_km(latitude, longitude, feed.latitude, feed.longitude) <= feed.radius_km + radius_km]

    def snapshot(self, feed: TransitFeed) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            cached = self._cache.get(feed.key)
            if cached and now - cached[0] < feed.ttl:
                return cached[1]
            calls = [stamp for stamp in self._calls.get(feed.key, []) if now - stamp < 60]
            if len(calls) >= self.PER_MINUTE:
                if cached:
                    return cached[1]
                raise RuntimeError(f"{feed.name}: read {self.PER_MINUTE} times in the last minute; try again shortly.")
            self._calls[feed.key] = [*calls, now]
        try:
            value = decode_vehicle_positions(self.fetch(feed.url, dict(feed.headers) or None))
        except Exception as exc:
            with self._lock:
                self.health[feed.key] = {"ok": False, "checked_at": now, "error": f"{type(exc).__name__}: {exc}"[:200]}
            raise
        with self._lock:
            self._cache[feed.key] = (now, value)
            self.health[feed.key] = {"ok": True, "checked_at": now, "vehicles": len(value["vehicles"])}
        return value

    def near(self, latitude: float, longitude: float, radius_km: float = 5, route: str = "") -> dict[str, Any]:
        """Vehicles within radius_km, nearest first, with the operators read and any that failed."""
        now = self.clock()
        wanted = route.strip().lower()
        vehicles, read, errors = [], [], {}
        for feed in self.feeds_near(latitude, longitude, radius_km):
            try:
                snapshot = self.snapshot(feed)
            except Exception as exc:  # one operator down must not hide the others
                errors[feed.name] = str(exc)[:160] or type(exc).__name__
                continue
            read.append(feed)
            reference = snapshot.get("timestamp") or now
            for item in snapshot["vehicles"]:
                distance = distance_km(latitude, longitude, item["latitude"], item["longitude"])
                if distance > radius_km:
                    continue
                item = {**item, "route": route_name(feed.key, item["route"])}
                if wanted and item["route"].lower() != wanted and item["label"].lower() != wanted:
                    continue
                age = max(0, round(reference - item["timestamp"])) if item.get("timestamp") else None
                if age is not None and age > DROP_SECONDS:
                    continue
                vehicles.append({**item, "operator": feed.name, "distance_km": round(distance, 2), "age_seconds": age,
                                 "stale": bool(age is not None and age > STALE_SECONDS)})
        vehicles.sort(key=lambda item: item["distance_km"])
        return {"vehicles": vehicles, "feeds": [feed.name for feed in read], "errors": errors,
                "credits": [f"{feed.attribution} ({feed.license})" for feed in read]}


def coverage() -> list[str]:
    return [f"{feed.region} ({feed.name})" for feed in FEEDS] + list(OTHER_COVERAGE)


#: The same areas in a few words, for notes that sit among other findings.
COVERAGE_SHORT = ("Boston, Austin, Minneapolis–St Paul, Toronto and nearby Ontario cities, Helsinki and Finland's trains, "
                  "the Netherlands, Norway, South East Queensland and Rio de Janeiro")


# ---------------------------------------------------------------------- the tool
def transit_vehicles(live: Any, resolve: Callable[[str], dict[str, Any]], place: str = "here", radius_km: float = 3,
                     route: str = "", limit: int = 25) -> dict[str, Any]:
    found = resolve(place or "here")
    latitude, longitude = float(found["latitude"]), float(found["longitude"])
    radius = max(.2, min(float(radius_km), 25.0))
    world = getattr(live, "world_transit", None)
    if world is None:
        raise ValueError("Live transit is not available in this session.")
    result = world.near(latitude, longitude, radius, route)
    vehicles = result["vehicles"]
    other = [item for item in live.transit(latitude, longitude, radius, world=False)] if hasattr(live, "transit") else []
    if route:
        other = [item for item in other if str(item.get("route") or "").lower() == route.strip().lower()]
    vehicles = sorted(vehicles + other, key=lambda item: item["distance_km"])
    operators = result["feeds"] + sorted({item["operator"] for item in other} - set(result["feeds"]))
    if not operators and not result["errors"]:
        raise ValueError(f"No open live-transit feed covers {found.get('name') or place}. Covered: " + "; ".join(coverage()) + ".")
    routes: dict[str, int] = {}
    for item in vehicles:
        if item.get("route"):
            routes[item["route"]] = routes.get(item["route"], 0) + 1
    return {"intelligence": True, "explore": "transit", "place": found.get("name") or place,
            "latitude": latitude, "longitude": longitude, "radius_km": radius, "route": route,
            "count": len(vehicles), "vehicles": vehicles[:max(1, min(int(limit), 100))],
            "busiest_routes": sorted(routes.items(), key=lambda pair: -pair[1])[:8],
            "operators": operators, "unavailable": result["errors"], "credits": result["credits"],
            "source": "operators' GTFS-realtime vehicle positions" + (" and MBTA / Digitraffic" if other else ""),
            "basis": "Positions as each operator's vehicles last reported them (usually within a minute); "
                     "'stale' means the report is over 10 minutes old. Vehicles without GPS are absent."}


def register_transit_tools(registry: Any, live: Any, resolve: Callable[[str], dict[str, Any]]) -> None:
    registry.register(
        "transit_vehicles",
        "Live buses, trams and trains near a place from the operators' own feeds: Boston, Austin, Minneapolis, Toronto "
        "and six other Ontario cities, Helsinki and Finland's trains, the Netherlands, Norway, South East Queensland and Rio "
        "de Janeiro. Optional route filter.",
        {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                          "radius_km": {"type": "number", "minimum": .2, "maximum": 25},
                                          "route": {"type": "string", "maxLength": 40},
                                          "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
         "required": [], "additionalProperties": False},
        "low", "public_web_read",
        lambda place="here", radius_km=3, route="", limit=25: transit_vehicles(live, resolve, place, radius_km, route, limit))


def report_lines(value: dict[str, Any]) -> list[str]:
    lines = [f"{value['count']} live vehicle{'s' if value['count'] != 1 else ''} within {value['radius_km']:g} km of {value['place']}"
             + (f" on route {value['route']}" if value.get("route") else "") + f" ({', '.join(value['operators']) or 'no operator answered'}):"]
    for item in value["vehicles"][:12]:
        parts = [f"{item['operator']}" + (f" route {item['route']}" if item.get("route") else ""),
                 f"vehicle {item.get('label') or item.get('id') or '?'}", f"{item['distance_km']} km away"]
        if item.get("speed_kmh") is not None:
            parts.append(f"{item['speed_kmh']:g} km/h")
        if item.get("status"):
            parts.append(item["status"])
        if item.get("occupancy") and item["occupancy"] != "no data":
            parts.append(item["occupancy"])
        if item.get("stale"):
            parts.append(f"report {round(item['age_seconds'] / 60)} min old")
        lines.append("• " + " · ".join(parts))
    if value.get("busiest_routes"):
        lines.append("Most vehicles here: " + ", ".join(f"route {route} ({count})" for route, count in value["busiest_routes"][:6]) + ".")
    if value.get("unavailable"):
        lines.append("Not answering right now: " + "; ".join(f"{name} ({problem[:60]})" for name, problem in value["unavailable"].items()) + ".")
    if value.get("credits"):
        lines.append("Data: " + "; ".join(value["credits"]) + ".")
    return lines
