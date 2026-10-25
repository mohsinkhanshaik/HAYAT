"""More of the world, on demand: launches, satellites, aircraft details, weather maps, imagery, fires, routes, bikes.

Added 2026-10-11 after comparing HAYAT with github.com/bilawalsidhu/gods-eye-view
(MIT), whose tool list covers these. Every source below was checked through
HAYAT's own fetcher, robots.txt included; Radio Browser was not used because
its robots.txt disallows the API.

| Feature | Source | Notes |
|---|---|---|
| Rocket launches | Launch Library 2 (The Space Devs) | free tier 15 requests/hour: cached 30 min |
| Satellites overhead, next pass | CelesTrak GP elements + SGP4 | each group fetched at most every 2 hours |
| Aircraft details | adsbdb | type, registration, route; owner names are not shown |
| Weather maps | NOAA nowCOAST WMS | global cloud imagery; radar and lightning cover the US |
| Satellite image of a place | NASA Worldview snapshots (GIBS) | VIIRS true colour, daily |
| Fire hotspots | NASA FIRMS, VIIRS NOAA-20, last 24 h | one 6 MB file, re-read at most hourly |
| Directions | OSRM on routing.openstreetmap.de (FOSSGIS) | occasional use, as its policy asks |
| Bike share | GBFS (MobilityData's public system list) | systems that need no key |
| Vehicles in a camera still | RF-DETR COCO detector (Apache 2.0) | counts only: no plates, faces or identities |
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)
JSON_TYPES = {"application/json", "text/json", "application/*+json", "text/plain"}
IMAGE_TYPES = {"image/png", "image/jpeg"}
LL2 = "https://ll.thespacedevs.com/2.3.0/launches/"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=json"
SATELLITE_GROUPS = {"visual": "brightest satellites", "stations": "space stations", "starlink": "Starlink",
                    "gps-ops": "GPS", "weather": "weather satellites", "science": "science satellites",
                    "amateur": "amateur radio satellites"}
ADSBDB = "https://api.adsbdb.com/v0/"
NOWCOAST = "https://nowcoast.noaa.gov/geoserver/observations/{service}/ows"
WEATHER_MAPS = {
    "clouds": ("satellite", "global_longwave_imagery_mosaic", "Infrared clouds (day and night), global"),
    "visible": ("satellite", "global_visible_imagery_mosaic", "Visible satellite (daylight), global"),
    "water_vapor": ("satellite", "global_water_vapor_imagery_mosaic", "Water vapour, global"),
    "radar": ("weather_radar", "base_reflectivity_mosaic", "Weather radar (US, Alaska, Hawaii, Caribbean, Guam)"),
    "lightning": ("lightning_detection", "ldn_lightning_strike_density", "Lightning strike density (Americas)"),
}
WORLDVIEW = "https://wvs.earthdata.nasa.gov/api/v1/snapshot"
GIBS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
FIRMS = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_Global_24h.csv"
OSRM = {"car": "https://routing.openstreetmap.de/routed-car/route/v1/driving/",
        "bike": "https://routing.openstreetmap.de/routed-bike/route/v1/driving/",
        "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/driving/"}
GBFS_SYSTEMS = "https://raw.githubusercontent.com/MobilityData/gbfs/master/systems.csv"
VEHICLE_CLASSES = {"car": "cars", "truck": "trucks", "bus": "buses", "motorcycle": "motorcycles", "bicycle": "bicycles"}
EARTH_RADIUS = 6371.0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS * math.asin(min(1.0, math.sqrt(a)))


def area(latitude: float, longitude: float, radius_km: float) -> tuple[float, float, float, float]:
    """(south, west, north, east) around a point."""
    dlat = radius_km / 111.0
    dlon = radius_km / max(1.0, 111.0 * math.cos(math.radians(latitude)))
    return (max(-89.9, latitude - dlat), max(-180.0, longitude - dlon), min(89.9, latitude + dlat), min(180.0, longitude + dlon))


# ---------------------------------------------------------------------- orbital helpers
WGS84_A, WGS84_F = 6378.137, 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


def _julian(moment: datetime) -> tuple[float, float]:
    from sgp4.api import jday
    return jday(moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second + moment.microsecond / 1e6)


def observer_ecef(latitude: float, longitude: float, height_km: float = 0.0) -> tuple[float, float, float]:
    lat, lon = math.radians(latitude), math.radians(longitude)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
    return ((n + height_km) * math.cos(lat) * math.cos(lon), (n + height_km) * math.cos(lat) * math.sin(lon),
            (n * (1 - WGS84_E2) + height_km) * math.sin(lat))


def to_ecef(position: tuple[float, float, float], julian: float) -> tuple[float, float, float]:
    """TEME/ECI to Earth-fixed by Greenwich sidereal time (polar motion ignored: well under a kilometre)."""
    from app.intelligence.live import _gmst
    theta = _gmst(julian)
    x, y, z = position
    return (x * math.cos(theta) + y * math.sin(theta), -x * math.sin(theta) + y * math.cos(theta), z)


def look_angles(target: tuple[float, float, float], observer: tuple[float, float, float], latitude: float,
                longitude: float) -> tuple[float, float, float]:
    """(elevation°, azimuth°, range km) of an Earth-fixed point seen from an observer."""
    lat, lon = math.radians(latitude), math.radians(longitude)
    dx, dy, dz = (target[0] - observer[0], target[1] - observer[1], target[2] - observer[2])
    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = -math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz
    up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz
    return (math.degrees(math.atan2(up, math.hypot(east, north))), math.degrees(math.atan2(east, north)) % 360,
            math.sqrt(dx * dx + dy * dy + dz * dz))


def sun_eci(julian: float) -> tuple[float, float, float]:
    """The Sun's direction and distance (km), low-precision almanac formula (about 0.01°)."""
    n = julian - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * n) % 360
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360)
    ecliptic = math.radians(mean_longitude + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 4e-7 * n)
    distance = (1.00014 - 0.01671 * math.cos(anomaly) - 0.00014 * math.cos(2 * anomaly)) * 149597870.7
    return (distance * math.cos(ecliptic), distance * math.cos(obliquity) * math.sin(ecliptic),
            distance * math.sin(obliquity) * math.sin(ecliptic))


def sunlit(position: tuple[float, float, float], sun: tuple[float, float, float]) -> bool:
    """False while the satellite is inside Earth's (cylindrical) shadow."""
    length = math.sqrt(sum(value * value for value in sun))
    direction = [value / length for value in sun]
    along = sum(p * s for p, s in zip(position, direction))
    if along > 0:
        return True
    perpendicular = math.sqrt(max(0.0, sum(p * p for p in position) - along * along))
    return perpendicular > EARTH_RADIUS


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


# ---------------------------------------------------------------------- fires
def read_firms(path: Path) -> list[tuple[float, float, float, str, str, str]]:
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            try:
                rows.append((float(item["latitude"]), float(item["longitude"]), float(item.get("frp") or 0),
                             item.get("confidence", ""), f"{item.get('acq_date', '')} {item.get('acq_time', '').zfill(4)[:2]}:"
                             f"{item.get('acq_time', '').zfill(4)[2:]}", item.get("daynight", "")))
            except (KeyError, ValueError):
                continue
    return rows


FIRE_CELL = 0.05  # degrees, about 5.5 km: detections in touching cells belong to one fire


def cluster_hotspots(rows: list[tuple[float, float, float, str, str, str]]) -> list[dict[str, Any]]:
    """Join hotspots in touching grid cells into fires (union-find over occupied cells)."""
    cells: dict[tuple[int, int], list[tuple[float, float, float, str, str, str]]] = {}
    for row in rows:
        cells.setdefault((math.floor(row[0] / FIRE_CELL), math.floor(row[1] / FIRE_CELL)), []).append(row)
    parent = {cell: cell for cell in cells}

    def root(cell):
        while parent[cell] != cell:
            parent[cell] = parent[parent[cell]]
            cell = parent[cell]
        return cell
    for cell in cells:
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                other = (cell[0] + dlat, cell[1] + dlon)
                if other in cells:
                    parent[root(other)] = root(cell)
    groups: dict[tuple[int, int], list[tuple[float, float, float, str, str, str]]] = {}
    for cell, members in cells.items():
        groups.setdefault(root(cell), []).extend(members)
    fires = []
    for members in groups.values():
        power = sum(row[2] for row in members)
        weight = power or len(members)
        lat = sum(row[0] * (row[2] or 1) for row in members) / (weight if power else len(members))
        lon = sum(row[1] * (row[2] or 1) for row in members) / (weight if power else len(members))
        extent = max(distance_km(lat, lon, row[0], row[1]) for row in members)
        confident = sum(1 for row in members if row[3] in ("h", "high", "n", "nominal"))
        fires.append({"latitude": round(lat, 4), "longitude": round(lon, 4), "detections": len(members),
                      "power_mw": round(power, 1), "max_power_mw": round(max(row[2] for row in members), 1),
                      "extent_km": round(extent, 1), "latest": max(row[4] for row in members) + " UTC",
                      "confident_share": round(confident / len(members), 2)})
    return fires


def compare_fire(fire: dict[str, Any], previous: list[dict[str, Any]]) -> str:
    match = min(previous, key=lambda other: distance_km(fire["latitude"], fire["longitude"], other["latitude"], other["longitude"]),
                default=None)
    if match is None or distance_km(fire["latitude"], fire["longitude"], match["latitude"], match["longitude"]) > max(
            10.0, fire["extent_km"] + match["extent_km"]):
        return "new"
    if fire["detections"] >= 1.5 * match["detections"] or fire["power_mw"] >= 1.5 * max(match["power_mw"], 1):
        return "growing"
    if fire["detections"] <= match["detections"] / 1.5 and fire["power_mw"] <= match["power_mw"] / 1.5:
        return "shrinking"
    return "steady"


def sun_elevation(latitude: float, longitude: float, moment: datetime | None = None) -> float:
    """The Sun's height above the horizon in degrees, now or at `moment`."""
    jd, fraction = _julian(moment or datetime.now(UTC))
    return look_angles(to_ecef(sun_eci(jd + fraction), jd + fraction), observer_ecef(latitude, longitude), latitude, longitude)[0]


def placeholder(image: Any) -> bool:
    """A card ("Temporarily Unavailable"), a blank or a test pattern rather than a scene: most pixels one shade.
    Measured on 2026-10-11: the Caltrans card 86 % within one narrow band of grey; 13 real road scenes 11-49 %."""
    import cv2
    import numpy as np
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    histogram = np.bincount(grey.ravel(), minlength=256)
    mode = int(histogram.argmax())
    return float(histogram[max(0, mode - 8):mode + 9].sum()) / grey.size >= .7


def compass(azimuth: float) -> str:
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((azimuth + 22.5) // 45) % 8]


# ---------------------------------------------------------------------- the service
class Explore:
    def __init__(self, fetcher: Any, folder: Path | str, resolve: Callable[[str], dict[str, Any]],
                 frame: Callable[[str], dict[str, Any]] | None = None, land: Path | None = None) -> None:
        self.fetcher, self.resolve, self.frame = fetcher, resolve, frame
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.land = land
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self._vehicle_model: Any = None

    # ----------------------------------------------------------------- fetching
    def _json(self, key: str, url: str, ttl: float, max_bytes: int = 16 * 1024 * 1024, disk: bool = False) -> Any:
        """A JSON document, cached in memory (and on disk for strict providers) for ttl seconds."""
        with self._lock:
            cached = self._cache.get(key)
            if cached and time.time() - cached[0] < ttl:
                return cached[1]
        path = self.folder / "cache" / (re.sub(r"[^\w.-]", "_", key) + ".json")
        if disk and path.exists() and time.time() - path.stat().st_mtime < ttl:
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = json.loads(bytes(self.fetcher.fetch(url, max_bytes, JSON_TYPES).content))
            if disk:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
        with self._lock:
            self._cache[key] = (time.time(), value)
            while len(self._cache) > 200:
                self._cache.pop(next(iter(self._cache)))
        return value

    def _place(self, place: str) -> dict[str, Any]:
        found = self.resolve(place or "here")
        return {"name": found.get("name") or place, "latitude": float(found["latitude"]), "longitude": float(found["longitude"]),
                "country_code": found.get("country_code", "")}

    # ----------------------------------------------------------------- launches
    def launches(self, mode: str = "upcoming", query: str = "", limit: int = 10) -> dict[str, Any]:
        if mode not in ("upcoming", "recent"):
            raise ValueError("Choose upcoming or recent launches.")
        path = "upcoming" if mode == "upcoming" else "previous"
        data = self._json(f"ll2-{path}", f"{LL2}{path}/?limit=40&mode=normal", 1800, disk=True)
        needle = query.strip().lower()
        rows = []
        for item in data.get("results") or []:
            provider = (item.get("launch_service_provider") or {}).get("name", "")
            pad = item.get("pad") or {}
            location = (pad.get("location") or {}).get("name", "")
            mission = item.get("mission") or {}
            text = " ".join([item.get("name", ""), provider, location, mission.get("name") or ""]).lower()
            if needle and needle not in text:
                continue
            if mode == "upcoming" and (item.get("status") or {}).get("abbrev") in ("Success", "Failure", "Partial Failure"):
                continue  # the provider keeps a launch in "upcoming" for a day after it flies
            rows.append({"name": item.get("name", ""), "provider": provider, "time": item.get("net"),
                         "status": (item.get("status") or {}).get("name", ""), "pad": pad.get("name", ""), "location": location,
                         "pad_latitude": _number(pad.get("latitude")), "pad_longitude": _number(pad.get("longitude")),
                         "mission": mission.get("name") or "", "orbit": ((mission.get("orbit") or {}).get("name") or ""),
                         "description": (mission.get("description") or "")[:400], "webcast_live": bool(item.get("webcast_live")),
                         "probability": item.get("probability"), "url": item.get("url", "")})
        return {"intelligence": True, "explore": "launches", "mode": mode, "query": query, "launches": rows[:max(1, min(int(limit), 40))],
                "source": "Launch Library 2 (The Space Devs)", "basis": "Times are the provider's 'no earlier than' (NET) estimates; launches slip."}

    # ----------------------------------------------------------------- satellites
    def _elements(self, group: str) -> list[dict[str, Any]]:
        if group not in SATELLITE_GROUPS:
            raise ValueError("Satellite groups: " + ", ".join(SATELLITE_GROUPS))
        # CelesTrak asks clients not to fetch one group more than once every two hours.
        return self._json(f"celestrak-{group}", CELESTRAK.format(group=group), 7200, 32 * 1024 * 1024, disk=True)

    @staticmethod
    def _satellite(record: dict[str, Any]) -> Any:
        from sgp4 import omm
        from sgp4.api import Satrec
        satellite = Satrec()
        omm.initialize(satellite, record)
        return satellite

    def satellites_overhead(self, place: str = "here", group: str = "visual", min_elevation: float = 10.0,
                            limit: int = 20) -> dict[str, Any]:
        where = self._place(place)
        observer = observer_ecef(where["latitude"], where["longitude"])
        moment = datetime.now(UTC)
        jd, fraction = _julian(moment)
        sun = sun_eci(jd + fraction)
        sun_elevation = look_angles(to_ecef(sun, jd + fraction), observer, where["latitude"], where["longitude"])[0]
        found = []
        for record in self._elements(group):
            try:
                satellite = self._satellite(record)
            except (ValueError, KeyError, TypeError):
                continue
            error, position, _velocity = satellite.sgp4(jd, fraction)
            if error:
                continue
            elevation, azimuth, distance = look_angles(to_ecef(position, jd + fraction), observer, where["latitude"], where["longitude"])
            if elevation >= min_elevation:
                lit = sunlit(position, sun)
                found.append({"name": record.get("OBJECT_NAME", "?"), "norad_id": record.get("NORAD_CAT_ID"),
                              "elevation": round(elevation, 1), "azimuth": round(azimuth) % 360, "direction": compass(azimuth),
                              "range_km": round(distance), "sunlit": lit, "naked_eye_chance": lit and sun_elevation < -6})
        found.sort(key=lambda item: -item["elevation"])
        return {"intelligence": True, "explore": "satellites", "place": where["name"], "group": group, "min_elevation": min_elevation,
                "group_title": SATELLITE_GROUPS[group], "count": len(found), "satellites": found[:max(1, min(int(limit), 100))],
                "sun_elevation": round(sun_elevation, 1), "computed_at": moment.isoformat(timespec="seconds"),
                "source": "CelesTrak elements, SGP4",
                "basis": "Positions are computed from published orbital elements (a few km accurate). A satellite is only visible "
                         "to the eye when it is sunlit and the sky is dark."}

    def satellite_pass(self, place: str = "here", name: str = "ISS", days: int = 3, min_elevation: float = 10.0) -> dict[str, Any]:
        where = self._place(place)
        wanted = name.strip().upper()
        aliases = {"ISS": "ISS (ZARYA)", "SPACE STATION": "ISS (ZARYA)", "TIANGONG": "CSS (TIANHE)", "CSS": "CSS (TIANHE)",
                   "HUBBLE": "HST"}
        target_name = aliases.get(wanted, wanted)
        record = None
        for group in ("stations", "visual", "science"):
            record = next((item for item in self._elements(group) if str(item.get("OBJECT_NAME", "")).upper() == target_name), None) \
                or next((item for item in self._elements(group) if target_name in str(item.get("OBJECT_NAME", "")).upper()), None)
            if record:
                break
        if record is None:
            raise ValueError(f"No satellite named {name} among CelesTrak's stations, brightest and science satellites.")
        satellite = self._satellite(record)
        observer = observer_ecef(where["latitude"], where["longitude"])
        start = datetime.now(UTC)
        passes, current = [], None
        unresolved_short_passes = 0
        step = timedelta(seconds=30)
        for index in range(int(max(1, min(days, 7)) * 86400 / 30)):
            moment = start + step * index
            jd, fraction = _julian(moment)
            error, position, _velocity = satellite.sgp4(jd, fraction)
            if error:
                break
            elevation, azimuth, distance = look_angles(to_ecef(position, jd + fraction), observer, where["latitude"], where["longitude"])
            if elevation >= min_elevation:
                sun = sun_eci(jd + fraction)
                dark = look_angles(to_ecef(sun, jd + fraction), observer, where["latitude"], where["longitude"])[0] < -6
                visible = dark and sunlit(position, sun)
                if current is None:
                    current = {"rise": moment, "rise_direction": compass(azimuth), "peak_elevation": elevation, "peak": moment,
                               "visible_seconds": 0}
                if elevation > current["peak_elevation"]:
                    current.update(peak_elevation=elevation, peak=moment, peak_direction=compass(azimuth))
                current["visible_seconds"] += 30 if visible else 0
                current["set"], current["set_direction"] = moment, compass(azimuth)
            elif current is not None:
                if current['set'] > current['rise']:
                    passes.append(current)
                else:
                    # One above-threshold point does not resolve a rise/set duration.
                    # Keep the observation count without inventing a zero-minute pass.
                    unresolved_short_passes += 1
                current = None
                if len(passes) >= 8:
                    break
        rows = [{"rise": item["rise"].isoformat(timespec="seconds"), "set": item["set"].isoformat(timespec="seconds"),
                 "peak": item["peak"].isoformat(timespec="seconds"), "peak_elevation": round(item["peak_elevation"]),
                 "from": item["rise_direction"], "to": item["set_direction"],
                 "minutes": round((item["set"] - item["rise"]).total_seconds() / 60, 1),
                 "visible_minutes": round(min(item['visible_seconds'], (item['set'] - item['rise']).total_seconds()) / 60, 1)} for item in passes]
        return {"intelligence": True, "explore": "passes", "place": where["name"], "satellite": record.get("OBJECT_NAME"),
                "passes": rows, "unresolved_short_passes": unresolved_short_passes,
                "next_visible": next((row for row in rows if row["visible_minutes"] >= 1), None),
                "source": "CelesTrak elements, SGP4", "basis": "Visible means the sky is dark (Sun 6° below the horizon) "
                "and the satellite is sunlit; clouds are not considered. Times are UTC. "
                "Positions are sampled every 30 seconds. Single-sample threshold crossings are counted separately "
                "as unresolved brief crossings because their rise/set duration is not resolved."}

    # ----------------------------------------------------------------- aircraft
    def aircraft_info(self, query: str) -> dict[str, Any]:
        """Type and registration (by ICAO hex or registration) or route (by callsign). Registered owners are not shown."""
        value = re.sub(r"[^A-Za-z0-9-]", "", str(query or "")).upper()
        if not 2 <= len(value) <= 10:
            raise ValueError("Give a callsign (UAE1), an ICAO hex address (896450) or a registration (A6-EUA).")
        result: dict[str, Any] = {"intelligence": True, "explore": "aircraft", "query": value, "source": "adsbdb"}
        looks_hex = bool(re.fullmatch(r"[0-9A-F]{6}", value))
        looks_registration = "-" in value
        if looks_hex or looks_registration:
            try:
                data = self._json(f"adsbdb-aircraft-{value}", f"{ADSBDB}aircraft/{value}", 86400)
                aircraft = (data.get("response") or {}).get("aircraft") or {}
                result["aircraft"] = {key: aircraft.get(key) for key in ("type", "icao_type", "manufacturer", "mode_s", "registration",
                                                                           "registered_owner_country_name", "registered_owner_operator_flag_code")}
            except Exception:  # unknown to adsbdb
                log_swallowed(_log, "looking up aircraft details")
        if not looks_hex:
            try:
                data = self._json(f"adsbdb-callsign-{value}", f"{ADSBDB}callsign/{value}", 3600)
                route = (data.get("response") or {}).get("flightroute") or {}
                airport = lambda item: {key: (item or {}).get(key) for key in ("name", "iata_code", "icao_code", "municipality", "country_name")}
                result["route"] = {"callsign": route.get("callsign"), "airline": (route.get("airline") or {}).get("name"),
                                   "origin": airport(route.get("origin")), "destination": airport(route.get("destination"))}
            except Exception:  # not a scheduled callsign
                log_swallowed(_log, "looking up a flight route")
        if "aircraft" not in result and "route" not in result:
            raise ValueError(f"adsbdb has no aircraft or flight route for {value}.")
        result["basis"] = ("adsbdb's crowd-maintained records; a route is the callsign's usual schedule, not proof of today's flight. "
                           "Registered owners are not shown.")
        return result

    # ----------------------------------------------------------------- images
    def _save(self, content: bytes, prefix: str) -> Path:
        target = self.folder / "images" / f"{prefix}-{hashlib.sha256(content).hexdigest()[:16]}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        images = sorted(target.parent.glob("*.png"), key=lambda item: item.stat().st_mtime)
        for old in images[:-200]:  # bounded: the newest 200 pictures
            old.unlink(missing_ok=True)
        return target

    def _base_map(self, box: tuple[float, float, float, float], width: int, height: int) -> Any:
        """NASA Blue Marble relief, dimmed, under a transparent overlay; it never changes, so it is kept on disk."""
        from urllib.parse import urlencode
        from PIL import Image
        south, west, north, east = box
        query = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": "BlueMarble_ShadedRelief_Bathymetry",
                 "STYLES": "", "CRS": "EPSG:4326", "BBOX": f"{south:.3f},{west:.3f},{north:.3f},{east:.3f}",
                 "WIDTH": width, "HEIGHT": height, "FORMAT": "image/jpeg"}
        url = GIBS_WMS + "?" + urlencode(query)
        path = self.folder / "cache" / ("base-" + hashlib.sha256(url.encode()).hexdigest()[:20] + ".jpg")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bytes(self.fetcher.fetch(url, 8 * 1024 * 1024, IMAGE_TYPES).content))
            for old in sorted(path.parent.glob("base-*.jpg"), key=lambda item: item.stat().st_mtime)[:-100]:
                old.unlink(missing_ok=True)
        base = Image.open(path).convert("RGBA")
        return Image.blend(base, Image.new("RGBA", base.size, (11, 22, 32, 255)), .45)

    def _decorate(self, content: bytes, box: tuple[float, float, float, float], where: dict[str, Any], title: str,
                  outline: bool = True, base: Any = None) -> bytes:
        """Coastlines (or a relief base map), the place marker and a caption, so the picture reads as a map."""
        from PIL import Image, ImageDraw
        image = Image.open(io.BytesIO(content)).convert("RGBA")
        if base is not None:
            base.alpha_composite(image.resize(base.size))
            image = base
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        south, west, north, east = box
        width, height = image.size
        point = lambda lon, lat: ((lon - west) / (east - west) * width, (north - lat) / (north - south) * height)
        if outline and self.land and self.land.exists():
            for polygon in json.loads(self.land.read_text(encoding="utf-8"))["polygons"]:
                if all(lon < west or lon > east for lon, _lat in polygon[::5]) or all(lat < south or lat > north for _lon, lat in polygon[::5]):
                    continue
                draw.line([point(lon, lat) for lon, lat in polygon], fill=(242, 197, 69), width=1)
        x, y = point(where["longitude"], where["latitude"])
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=(255, 70, 70), width=3)
        draw.rectangle((0, height - 22, width, height), fill=(10, 16, 24))
        draw.text((8, height - 18), f"{title} · {where['name'][:60]}", fill=(235, 235, 235))
        output = io.BytesIO()
        image.save(output, "PNG")
        return output.getvalue()

    def weather_map(self, place: str = "here", kind: str = "clouds", radius_km: float = 900) -> dict[str, Any]:
        if kind not in WEATHER_MAPS:
            raise ValueError("Map kinds: " + ", ".join(WEATHER_MAPS))
        service, layer, title = WEATHER_MAPS[kind]
        where = self._place(place)
        radius = max(100.0, min(float(radius_km), 3000.0))
        box = area(where["latitude"], where["longitude"], radius)
        south, west, north, east = box
        aspect = (east - west) * math.cos(math.radians(where["latitude"])) / max(.01, north - south)
        width = 900
        height = max(300, min(900, int(width / max(.3, aspect))))
        from urllib.parse import urlencode
        query = {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": layer, "styles": "", "crs": "EPSG:4326",
                 "bbox": f"{south},{west},{north},{east}", "width": width, "height": height, "format": "image/png",
                 "transparent": "false", "bgcolor": "0x0B1620"}
        sparse = kind in ("radar", "lightning")  # mostly empty: drawn over a relief map instead of black
        if sparse:
            query["transparent"] = "true"
        content = bytes(self.fetcher.fetch(NOWCOAST.format(service=service) + "?" + urlencode(query), 16 * 1024 * 1024, IMAGE_TYPES).content)
        base = None
        if sparse:
            try:
                base = self._base_map(box, width, height)
            except Exception:  # the overlay alone is still a correct map
                log_swallowed(_log, "fetching the relief base map")
        moment = datetime.now(UTC)
        path = self._save(self._decorate(content, box, where, f"{title} · {moment:%Y-%m-%d %H:%M} UTC",
                                         outline=base is None, base=base), f"weather-{kind}")
        return {"intelligence": True, "explore": "weather_map", "kind": kind, "title": title, "place": where["name"],
                "path": str(path), "bbox": box, "fetched_at": moment.isoformat(timespec="seconds"),
                "source": "NOAA nowCOAST (" + layer + ")", "basis": "The latest mosaic NOAA has published; it can lag by minutes. "
                "Radar and lightning only cover the regions named; elsewhere the map is empty, not clear."}

    def satellite_image(self, place: str = "here", date: str = "", radius_km: float = 120) -> dict[str, Any]:
        where = self._place(place)
        day = date or (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise ValueError("Give the date as YYYY-MM-DD.")
        radius = max(20.0, min(float(radius_km), 1500.0))
        box = area(where["latitude"], where["longitude"], radius)
        south, west, north, east = box
        from urllib.parse import urlencode
        query = {"REQUEST": "GetSnapshot", "TIME": day, "BBOX": f"{south},{west},{north},{east}", "CRS": "EPSG:4326",
                 "LAYERS": "VIIRS_SNPP_CorrectedReflectance_TrueColor,Coastlines_15m", "WRAP": "day,x", "FORMAT": "image/jpeg",
                 "WIDTH": 900, "HEIGHT": max(300, min(900, int(900 * (north - south) / max(.01, (east - west) * math.cos(math.radians(where['latitude']))))))}
        content = bytes(self.fetcher.fetch(WORLDVIEW + "?" + urlencode(query), 16 * 1024 * 1024, IMAGE_TYPES).content)
        # Worldview draws its own 15 m coastlines; the coarse world outline would sit beside them.
        path = self._save(self._decorate(content, box, where, f"Satellite (VIIRS true colour) {day}", outline=False), "satellite")
        return {"intelligence": True, "explore": "satellite_image", "place": where["name"], "date": day, "path": str(path),
                "bbox": box, "source": "NASA Worldview / GIBS, VIIRS (Suomi NPP) true colour",
                "basis": "One daily pass at about 375 m per pixel; clouds may cover the place. Not a live view."}

    # ----------------------------------------------------------------- fires
    def _hotspots(self) -> list[tuple[float, float, float, str, str, str]]:
        with self._lock:
            cached = self._cache.get("firms-rows")
            if cached and time.time() - cached[0] < 3600:
                return cached[1]
        path = self.folder / "cache" / "firms_viirs_24h.csv"
        if not path.exists() or time.time() - path.stat().st_mtime > 3600:
            content = bytes(self.fetcher.fetch(FIRMS, 32 * 1024 * 1024, {"text/csv", "text/plain", "application/octet-stream"}).content)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                # The earlier download is kept: fires are compared with it (new, growing, shrinking).
                path.replace(path.with_name("firms_viirs_24h_previous.csv"))
            path.write_bytes(content)
        rows = read_firms(path)
        with self._lock:
            self._cache["firms-rows"] = (time.time(), rows)
        return rows

    def _previous_hotspots(self) -> tuple[list[tuple[float, float, float, str, str, str]], str | None]:
        """The download before the current one, and when it was made; empty until a second download."""
        path = self.folder / "cache" / "firms_viirs_24h_previous.csv"
        if not path.exists():
            return [], None
        stamp = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get("firms-previous")
            if cached and cached[0] == stamp:
                return cached[1], cached[2]
        rows = read_firms(path)
        moment = datetime.fromtimestamp(stamp, UTC).isoformat(timespec="minutes")
        with self._lock:
            self._cache["firms-previous"] = (stamp, rows, moment)
        return rows, moment

    def fire_clusters(self, place: str = "", radius_km: float = 300, limit: int = 10) -> dict[str, Any]:
        """Hotspots joined into fires, strongest first, each compared with the previous download."""
        where = self._place(place) if place else None
        radius = max(5.0, min(float(radius_km), 3000.0))

        def near(rows):
            if where is None:
                return rows
            return [row for row in rows if distance_km(where["latitude"], where["longitude"], row[0], row[1]) <= radius]
        current = cluster_hotspots(near(self._hotspots()))
        previous_rows, previous_at = self._previous_hotspots()
        previous = cluster_hotspots(near(previous_rows)) if previous_rows else []
        for fire in current:
            fire["change"] = compare_fire(fire, previous) if previous_rows else "unknown"
            if where is not None:
                fire["distance_km"] = round(distance_km(where["latitude"], where["longitude"], fire["latitude"], fire["longitude"]), 1)
                fire["direction"] = compass(bearing_deg(where["latitude"], where["longitude"], fire["latitude"], fire["longitude"]))
        current.sort(key=lambda fire: -fire["power_mw"])
        gone = [fire for fire in previous if not any(distance_km(fire["latitude"], fire["longitude"], other["latitude"], other["longitude"])
                                                     <= max(10.0, fire["extent_km"] + other["extent_km"]) for other in current)]
        return {"intelligence": True, "explore": "fire_clusters", "place": where["name"] if where else "world",
                "radius_km": radius if where else None, "count": len(current), "fires": current[:max(1, min(int(limit), 100))],
                "new": sum(fire["change"] == "new" for fire in current), "growing": sum(fire["change"] == "growing" for fire in current),
                "no_longer_detected": len(gone), "compared_with": previous_at,
                "source": "NASA FIRMS · VIIRS NOAA-20, last 24 hours",
                "basis": "Hotspots in touching 5 km grid squares are joined into one fire. Change compares this 24-hour window "
                         "with the previous download (at least an hour older): 'growing' means at least half again as many detections "
                         "or as much power. Satellites pass a few times a day, so a fire can look quiet between passes."}

    def fire_hotspots(self, place: str = "", radius_km: float = 300, limit: int = 25) -> dict[str, Any]:
        rows = self._hotspots()
        where = self._place(place) if place else None
        radius = max(5.0, min(float(radius_km), 3000.0))
        selected = []
        for lat, lon, frp, confidence, moment, daynight in rows:
            if where is not None:
                distance = distance_km(where["latitude"], where["longitude"], lat, lon)
                if distance > radius:
                    continue
            else:
                distance = None
            selected.append({"latitude": lat, "longitude": lon, "power_mw": frp, "confidence": {"h": "high", "n": "nominal", "l": "low"}.get(confidence, confidence),
                             "detected": moment + " UTC", "daynight": "day" if daynight == "D" else "night",
                             "distance_km": round(distance, 1) if distance is not None else None})
        selected.sort(key=lambda item: -item["power_mw"])
        return {"intelligence": True, "explore": "fires", "place": where["name"] if where else "world", "radius_km": radius if where else None,
                "count": len(selected), "world_count": len(rows), "hotspots": selected[:max(1, min(int(limit), 500))],
                "source": "NASA FIRMS · VIIRS NOAA-20, last 24 hours",
                "basis": "A hotspot is a satellite heat detection (375 m pixel): wildfires, but also flares, industry and burning fields. "
                         "Power is fire radiative power in megawatts."}

    def hotspots_in(self, south: float, west: float, north: float, east: float, limit: int = 3000) -> list[dict[str, Any]]:
        """For the atlas layer: the strongest hotspots inside a map view."""
        rows = [row for row in self._hotspots() if south <= row[0] <= north and (west <= row[1] <= east if west <= east else row[1] >= west or row[1] <= east)]
        rows.sort(key=lambda row: -row[2])
        return [{"latitude": lat, "longitude": lon, "power_mw": frp, "confidence": confidence, "detected": moment}
                for lat, lon, frp, confidence, moment, _daynight in rows[:limit]]

    # ----------------------------------------------------------------- routes
    def route(self, origin: str, destination: str, mode: str = "car") -> dict[str, Any]:
        if mode not in OSRM:
            raise ValueError("Travel by car, bike or foot.")
        start, end = self._place(origin), self._place(destination)
        url = (f"{OSRM[mode]}{start['longitude']:.6f},{start['latitude']:.6f};{end['longitude']:.6f},{end['latitude']:.6f}"
               "?overview=simplified&geometries=geojson&steps=true")
        data = self._json(f"osrm-{mode}-{url}", url, 600)
        if data.get("code") != "Ok" or not data.get("routes"):
            raise ValueError(f"No {mode} route found: {data.get('message') or data.get('code')}.")
        best = data["routes"][0]
        steps = []
        for leg in best.get("legs") or []:
            for step in leg.get("steps") or []:
                maneuver = step.get("maneuver") or {}
                kind, modifier = maneuver.get("type", ""), maneuver.get("modifier", "")
                road = step.get("name") or step.get("ref") or ""
                if kind in ("depart", "arrive") or step.get("distance", 0) >= 50:
                    steps.append({"instruction": " ".join(part for part in (kind.replace("_", " "), modifier) if part).capitalize()
                                  + (f" onto {road}" if road and kind not in ("arrive",) else ""),
                                  "distance_km": round(step.get("distance", 0) / 1000, 2)})
        return {"intelligence": True, "explore": "route", "mode": mode, "from": start["name"], "to": end["name"],
                "distance_km": round(best["distance"] / 1000, 1), "duration_minutes": round(best["duration"] / 60),
                "steps": steps[:40], "path": (best.get("geometry") or {}).get("coordinates", [])[:500],
                "source": "OSRM on OpenStreetMap (routing.openstreetmap.de, FOSSGIS)",
                "basis": "Typical travel time with no live traffic; check road closures before you go. © OpenStreetMap contributors."}

    # ----------------------------------------------------------------- bike share
    def _systems(self) -> list[dict[str, str]]:
        with self._lock:
            cached = self._cache.get("gbfs-systems")
            if cached and time.time() - cached[0] < 86400:
                return cached[1]
        content = bytes(self.fetcher.fetch(GBFS_SYSTEMS, 4 * 1024 * 1024, {"text/plain", "text/csv"}).content).decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(content)))
        with self._lock:
            self._cache["gbfs-systems"] = (time.time(), rows)
        return rows

    @staticmethod
    def _feeds(document: dict[str, Any]) -> dict[str, str]:
        data = document.get("data") or {}
        feeds = data.get("feeds") if isinstance(data.get("feeds"), list) else next(
            (value.get("feeds") for value in data.values() if isinstance(value, dict) and isinstance(value.get("feeds"), list)), [])
        return {item.get("name"): item.get("url") for item in feeds or [] if isinstance(item, dict)}

    def bikeshare(self, place: str = "here", radius_km: float = 2.0, limit: int = 10) -> dict[str, Any]:
        where = self._place(place)
        city = where["name"].split(",")[0].strip().lower()
        country = (where.get("country_code") or "").upper()
        candidates = [row for row in self._systems() if city and city in (row.get("Location", "") + " " + row.get("Name", "")).lower()
                      and not (row.get("Authentication Info URL") or row.get("Authentication Type")) and (not country or row.get("Country Code", "").upper() == country)]
        if not candidates:
            raise ValueError(f"No open bike-share system is listed for {where['name']} in MobilityData's GBFS catalog.")
        radius = max(.2, min(float(radius_km), 10.0))
        stations, systems, errors = [], [], []
        for system in candidates[:6]:
            try:
                feeds = self._feeds(self._json("gbfs-" + system["Auto-Discovery URL"], system["Auto-Discovery URL"], 3600, 2 * 1024 * 1024))
                information = self._json("gbfs-info-" + feeds["station_information"], feeds["station_information"], 3600, 16 * 1024 * 1024)
                status = self._json("gbfs-status-" + feeds["station_status"], feeds["station_status"], 60, 16 * 1024 * 1024)
            except Exception as exc:  # one system unreachable or keyed: try the next
                errors.append(f"{system.get('Name')}: {type(exc).__name__}")
                continue
            live = {item.get("station_id"): item for item in ((status.get("data") or {}).get("stations") or [])}
            systems.append(system.get("Name"))
            for item in (information.get("data") or {}).get("stations") or []:
                try:
                    distance = distance_km(where["latitude"], where["longitude"], float(item["lat"]), float(item["lon"]))
                except (KeyError, TypeError, ValueError):
                    continue
                if distance > radius:
                    continue
                state = live.get(item.get("station_id")) or {}
                name = item.get("name")
                if isinstance(name, list):  # GBFS 3 localises names
                    name = (name[0] or {}).get("text") if name else ""
                stations.append({"name": name or item.get("station_id"), "system": system.get("Name"), "distance_km": round(distance, 2),
                                 "bikes": state.get("num_bikes_available") if state.get("num_bikes_available") is not None else state.get("num_vehicles_available"),
                                 "docks": state.get("num_docks_available"), "renting": bool(state.get("is_renting", True)),
                                 "latitude": float(item["lat"]), "longitude": float(item["lon"])})
        if not systems:
            raise ValueError("The bike-share systems for this place did not answer: " + "; ".join(errors))
        # Docked schemes (Citi Bike, Vélib', Divvy) first; free-floating scooter operators publish
        # thousands of virtual "stations" that would bury them.
        docked = {item["system"] for item in stations if item["docks"] is not None}
        if docked:
            stations = [item for item in stations if item["system"] in docked]
            systems = [name for name in systems if name in docked]
        stations.sort(key=lambda item: item["distance_km"])
        return {"intelligence": True, "explore": "bikeshare", "place": where["name"], "systems": systems, "radius_km": radius,
                "stations": stations[:max(1, min(int(limit), 50))], "count": len(stations),
                "source": "GBFS feeds listed by MobilityData", "basis": "Live counts as each operator publishes them, usually within a minute."}

    # ----------------------------------------------------------------- vehicles in a camera still
    def _vehicles(self) -> Any:
        with self._lock:
            if self._vehicle_model is None:
                import open_image_models.detection.core.hub as hub
                from open_image_models import create_detector
                from app.anpr.detector import onnx_providers
                hub.MODEL_CACHE_DIR = self.folder.parent.parent / "models" / "vision"  # inside HAYAT's folder
                self._vehicle_model = VehicleModel(create_detector, onnx_providers("auto"))
            return self._vehicle_model

    def _traffic_history(self, event_id: str, total: int, counts: dict[str, int], fetched_at: Any) -> dict[str, Any]:
        """Keep each count (no image, nothing identifying) and compare it with this camera's usual at this hour."""
        import sqlite3
        import statistics
        moment = datetime.now(UTC)
        with self._lock:
            db = sqlite3.connect(self.folder / "traffic.db")
            try:
                with db:
                    db.execute("CREATE TABLE IF NOT EXISTS counts (camera TEXT, at TEXT, hour INTEGER, weekday INTEGER, total INTEGER, kinds TEXT)")
                    db.execute("CREATE INDEX IF NOT EXISTS counts_camera ON counts (camera, at)")
                    earlier = db.execute("SELECT at, hour, weekday, total FROM counts WHERE camera = ? ORDER BY at DESC LIMIT 500",
                                         (event_id,)).fetchall()
                    db.execute("INSERT INTO counts VALUES (?, ?, ?, ?, ?, ?)", (event_id, moment.isoformat(timespec="seconds"),
                                                                             moment.hour, moment.weekday(), total, json.dumps(counts)))
                    db.execute("DELETE FROM counts WHERE at < ?", ((moment - timedelta(days=60)).isoformat(),))
            finally:
                db.close()
        result: dict[str, Any] = {"samples": len(earlier) + 1}
        if earlier:
            result["previous"] = {"at": earlier[0][0], "vehicles": earlier[0][3]}
        # Hours wrap: 23:00 is next to 00:00. Counts within five minutes of this one are the same moment, not a baseline.
        same_hour = [row[3] for row in earlier if min(abs(row[1] - moment.hour), 24 - abs(row[1] - moment.hour)) <= 1
                     and (moment - datetime.fromisoformat(row[0])).total_seconds() > 300]
        if len(same_hour) >= 3:
            usual = statistics.median(same_hour)
            spread = max(2.0, statistics.pstdev(same_hour))
            result.update(usual=usual, baseline_samples=len(same_hour),
                          compared="busier than usual" if total > usual + spread else "quieter than usual" if total < usual - spread
                          else "about usual")
        else:
            result["compared"] = None
            result["needed"] = 3 - len(same_hour)
        return result

    def traffic_count(self, event_id: str) -> dict[str, Any]:
        """How many vehicles a public camera's current still shows. Counts only: nothing is read or identified."""
        if self.frame is None:
            raise ValueError("Camera frames are not available in this session.")
        frame = self.frame(event_id)
        import cv2
        image = cv2.imread(str(frame["path"]))
        if image is None:
            raise ValueError("The camera image could not be read.")
        if placeholder(image):
            # Caltrans, 2026-10-11: a white "Temporarily Unavailable" card was counted as "0 vehicles, empty".
            raise ValueError("The camera is showing a placeholder or blank picture, not the road; nothing was counted.")
        dark = None
        if frame.get("latitude") is not None and frame.get("longitude") is not None:
            dark = sun_elevation(float(frame["latitude"]), float(frame["longitude"])) < -6
        counts: dict[str, int] = {}
        for result in self._vehicles().predict(image) or []:
            label = VEHICLE_CLASSES.get(str(result.label).lower())
            if label:
                counts[label] = counts.get(label, 0) + 1
        total = sum(counts.values())
        history = self._traffic_history(event_id, total, counts, frame.get("fetched_at"))
        return {"intelligence": True, "explore": "traffic", "event_id": event_id, "title": frame.get("title", event_id),
                "vehicles": total, "counts": counts, "fetched_at": frame.get("fetched_at"), "image": str(frame["path"]),
                "level": "empty" if total == 0 else "light" if total < 8 else "moderate" if total < 20 else "busy",
                "history": history, "dark": dark,
                "source": "RF-DETR (COCO) on the publisher's current still",
                "basis": "A count of vehicles visible in one still; far or hidden vehicles are missed. Nothing is identified: "
                         "no plates, faces or people are read."}


class VehicleModel:
    """RF-DETR on the GPU when there is room, and on the CPU when the GPU fails (same counts, measured)."""

    def __init__(self, create: Callable[..., Any], providers: list[Any]) -> None:
        self.create = create
        self.rebuild(providers)

    def rebuild(self, providers: list[Any]) -> None:
        from app.anpr.detector import session_device
        self.model = self.create("rf-detr-small-512-coco", conf_thresh=.35, providers=providers)
        self.device = session_device(self.model)

    def predict(self, image: Any) -> Any:
        from app.anpr.detector import with_cpu_fallback
        return with_cpu_fallback(self, lambda: self.model.predict(image))


# ---------------------------------------------------------------------- tools
EXPLORE_TOOLS = ("space_launches", "satellites_overhead", "satellite_pass", "aircraft_info", "weather_map", "satellite_image",
                 "fire_hotspots", "fire_clusters", "route_directions", "bike_share", "traffic_count")


def register_explore_tools(registry: Any, explore: Explore) -> None:
    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    place = {"type": "string", "maxLength": 200}
    registry.register("space_launches", "Upcoming or recent rocket launches worldwide (Launch Library 2): provider, rocket and "
                      "mission, pad, time, status, webcast.", obj({"mode": {"enum": ["upcoming", "recent"]},
                      "query": {"type": "string", "maxLength": 80}, "limit": {"type": "integer", "minimum": 1, "maximum": 40}}),
                      "low", "public_web_read", explore.launches)
    registry.register("satellites_overhead", "Satellites above a place right now (CelesTrak + SGP4): brightest, space stations, "
                      "Starlink, GPS, weather, science or amateur; elevation, direction and whether it could be seen.",
                      obj({"place": place, "group": {"enum": list(SATELLITE_GROUPS)},
                           "min_elevation": {"type": "number", "minimum": 0, "maximum": 80},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100}}), "low", "public_web_read", explore.satellites_overhead)
    registry.register("satellite_pass", "The next passes of the ISS (or another named satellite) over a place, and the next one "
                      "visible to the naked eye.", obj({"place": place, "name": {"type": "string", "maxLength": 60},
                      "days": {"type": "integer", "minimum": 1, "maximum": 7}}), "low", "public_web_read", explore.satellite_pass)
    registry.register("aircraft_info", "Aircraft type and registration by ICAO hex or registration, or a flight's usual route by "
                      "callsign (adsbdb). Registered owners are not shown.", obj({"query": {"type": "string", "minLength": 2, "maxLength": 12}},
                      ["query"]), "low", "public_web_read", explore.aircraft_info)
    registry.register("weather_map", "A current weather map image of an area from NOAA: infrared clouds, visible or water-vapour "
                      "satellite (global), radar (US) or lightning (Americas).", obj({"place": place, "kind": {"enum": list(WEATHER_MAPS)},
                      "radius_km": {"type": "number", "minimum": 100, "maximum": 3000}}), "low", "public_web_read", explore.weather_map)
    registry.register("satellite_image", "A true-colour satellite image of a place for a day (NASA Worldview, VIIRS, default "
                      "yesterday).", obj({"place": place, "date": {"type": "string", "maxLength": 10},
                      "radius_km": {"type": "number", "minimum": 20, "maximum": 1500}}), "low", "public_web_read", explore.satellite_image)
    registry.register("fire_hotspots", "Satellite fire/heat detections from the last 24 hours (NASA FIRMS VIIRS), near a place or "
                      "worldwide, strongest first.", obj({"place": place, "radius_km": {"type": "number", "minimum": 5, "maximum": 3000},
                      "limit": {"type": "integer", "minimum": 1, "maximum": 500}}), "low", "public_web_read", explore.fire_hotspots)
    registry.register("fire_clusters", "Fires near a place or worldwide: satellite hotspots joined into fires, with size, power and "
                      "whether each is new, growing, steady or shrinking since the previous download (NASA FIRMS).",
                      obj({"place": place, "radius_km": {"type": "number", "minimum": 5, "maximum": 3000},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 100}}), "low", "public_web_read", explore.fire_clusters)
    registry.register("route_directions", "Driving, cycling or walking directions between two places over OpenStreetMap (OSRM): "
                      "distance, time and turns.", obj({"origin": {"type": "string", "minLength": 1, "maxLength": 200},
                      "destination": {"type": "string", "minLength": 1, "maxLength": 200}, "mode": {"enum": list(OSRM)}},
                      ["origin", "destination"]), "low", "public_web_read", explore.route)
    registry.register("bike_share", "Live bike-share stations near a place (GBFS): bikes and free docks.",
                      obj({"place": place, "radius_km": {"type": "number", "minimum": .2, "maximum": 10},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 50}}), "low", "public_web_read", explore.bikeshare)
    registry.register("traffic_count", "Count the vehicles in a public camera's current still (cars, trucks, buses, motorcycles, "
                      "bicycles). Counts only: no plates, faces or identities.", obj({"event_id": {"type": "string", "maxLength": 220}},
                      ["event_id"]), "low", "public_web_read", explore.traffic_count)


# ---------------------------------------------------------------------- chat
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_END = r"\s*[?.!]*$"
_PLACE = r"(?P<place>(?!(?:the\s+)?(?:last|next)\s)[^\s,?.!].{0,60}?)"
_NOW = r"(?:\s+(?:now|right\s+now|today|tonight|currently))?"


def _c(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


ROUTES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], tuple[str, dict[str, Any]]]], ...] = (
    (_c(_LEAD + r"(?:(?:when|what)\s+(?:is|are)\s+(?:the\s+)?)?(?:show\s+(?:me\s+)?)?(?:the\s+)?(?P<which>next|upcoming|latest|recent|last)\s+"
        r"(?:(?P<query>spacex|nasa|isro|rocket\s+lab|blue\s+origin|esa|china|cnsa|starship|falcon(?:\s+9)?|artemis)\s+)?"
        r"(?:rocket\s+)?launch(?:es)?" + _NOW + _END),
     lambda m: ("space_launches", {"mode": "recent" if m["which"].lower() in ("latest", "recent", "last") else "upcoming",
                                   **({"query": m["query"].lower()} if m["query"] else {})})),
    (_c(_LEAD + r"(?:any\s+)?(?:rocket\s+)?launch(?:es)?\s+(?:this\s+week|today|tomorrow|coming\s+up|soon)" + _END),
     lambda m: ("space_launches", {"mode": "upcoming"})),
    (_c(_LEAD + r"(?:what|which)\s+(?P<group>starlink\s+|gps\s+|weather\s+)?satellites\s+(?:are\s+)?(?:above|over|overhead)"
        r"(?:\s+(?:me|us|here)|\s+(?:(?:in|at|over)\s+)?(?!(?:now|right\s+now|today|tonight|currently)\s*[?.!]*$)" + _PLACE + r")?"
        + _NOW + _END),
     lambda m: ("satellites_overhead", {"place": m["place"] or "here",
                                        "group": {"starlink": "starlink", "gps": "gps-ops", "weather": "weather"}.get((m["group"] or "").strip().lower(), "visual")})),
    (_c(_LEAD + r"(?:show\s+(?:me\s+)?)?satellites\s+(?:overhead|above\s+(?:me|us))" + _NOW + _END),
     lambda m: ("satellites_overhead", {"place": "here"})),
    (_c(_LEAD + r"(?:when\s+(?:can|will)\s+(?:i|we)\s+see\s+|when\s+(?:is|does|will)\s+|next\s+)(?:the\s+)?(?P<name>iss|international\s+space\s+station|"
        r"space\s+station|tiangong|hubble)(?:\s+(?:pass|passes|go\s+over|fly\s+over|be\s+visible|visible|overhead))*"
        r"(?:\s+(?:over|from|in|at)\s+" + _PLACE + r")?" + _END),
     lambda m: ("satellite_pass", {"place": m["place"] or "here", "name": "ISS" if "station" in m["name"].lower() or m["name"].lower() == "iss"
                                   else m["name"].upper()})),
    (_c(_LEAD + r"(?:what\s+(?:plane|aircraft|flight)\s+is|who\s+operates|which\s+airline\s+is|flight\s+info(?:rmation)?\s+(?:for|on)|"
        r"aircraft\s+info(?:rmation)?\s+(?:for|on)|details\s+(?:for|of|on)\s+(?:flight|aircraft))\s+(?P<query>[A-Z0-9-]{2,10})" + _END),
     lambda m: ("aircraft_info", {"query": m["query"]})),
    (_c(_LEAD + r"(?:show\s+(?:me\s+)?)?(?:the\s+)?(?:current\s+|live\s+|latest\s+)?(?P<kind>weather\s+radar|radar|lightning|cloud|clouds|satellite\s+clouds|"
        r"water\s+vapou?r|infrared)(?:\s+(?:map|image|picture|imagery))?\s+(?:over|for|of|near|around|in)\s+" + _PLACE + _NOW + _END),
     lambda m: ("weather_map", {"place": m["place"], "kind": {"weather radar": "radar", "radar": "radar", "lightning": "lightning",
                                                              "water vapor": "water_vapor", "water vapour": "water_vapor"}.get(m["kind"].lower(), "clouds")})),
    (_c(_LEAD + r"(?:show\s+(?:me\s+)?)?(?:a\s+|the\s+)?(?:latest\s+|recent\s+)?satellite\s+(?:image|picture|photo|view|imagery)\s+(?:of|over|for)\s+"
        + _PLACE + r"(?:\s+(?:on|from)\s+(?P<date>\d{4}-\d{2}-\d{2}))?" + _END),
     lambda m: ("satellite_image", {"place": m["place"], **({"date": m["date"]} if m["date"] else {})})),
    (_c(_LEAD + r"(?:show\s+(?:me\s+)?|any\s+|are\s+there\s+(?:any\s+)?)?(?:active\s+)?(?:fire|heat)\s+(?:hotspots?|detections?)"
        r"(?:\s+(?:near|in|around|over)\s+" + _PLACE + r")?" + _NOW + _END),
     lambda m: ("fire_hotspots", {"place": m["place"]} if m["place"] else {})),
    (_c(_LEAD + r"(?:(?:which|what|are\s+(?:there\s+)?any)\s+)?(?:fires?|wildfires?)\s+(?:are\s+)?(?:growing|spreading|new)"
        r"(?:\s+(?:near|in|around)\s+" + _PLACE + r")?" + _NOW + _END),
     lambda m: ("fire_clusters", {"place": m["place"]} if m["place"] else {})),
    (_c(_LEAD + r"(?:show\s+(?:me\s+)?)?(?:the\s+)?(?:fire\s+clusters|active\s+fires|new\s+fires|growing\s+fires)"
        r"(?:\s+(?:near|in|around)\s+" + _PLACE + r")?" + _NOW + _END),
     lambda m: ("fire_clusters", {"place": m["place"]} if m["place"] else {})),
    (_c(_LEAD + r"(?:give\s+me\s+|show\s+(?:me\s+)?|get\s+)?(?:the\s+)?(?:(?P<mode1>driving|walking|cycling|bike|bicycle|car)\s+)?(?:directions|route|way)"
        r"\s+from\s+(?P<origin>.{1,80}?)\s+to\s+(?P<destination>.{1,80}?)(?:\s+(?:by|on)\s+(?P<mode2>car|bike|bicycle|foot|walking|cycling))?" + _END),
     lambda m: ("route_directions", {"origin": m["origin"], "destination": m["destination"],
                                     "mode": {"walking": "foot", "foot": "foot", "cycling": "bike", "bike": "bike", "bicycle": "bike"}.get(
                                         (m["mode2"] or m["mode1"] or "car").lower(), "car")})),
    (_c(_LEAD + r"how\s+(?:far|long)\s+is\s+it\s+from\s+(?P<origin>.{1,80}?)\s+to\s+(?P<destination>.{1,80}?)(?:\s+by\s+(?P<mode>car|bike|foot|walking))?" + _END),
     lambda m: ("route_directions", {"origin": m["origin"], "destination": m["destination"],
                                     "mode": {"walking": "foot", "foot": "foot", "bike": "bike"}.get((m["mode"] or "car").lower(), "car")})),
    (_c(_LEAD + r"(?:any\s+|find\s+(?:me\s+)?|where\s+(?:are|can\s+i\s+get)\s+|show\s+(?:me\s+)?)?(?:a\s+)?(?:share\s+bikes?|bike[\s-]?share|"
        r"rental\s+bikes?|city\s+bikes?|bikes?\s+to\s+rent)(?:\s+(?:stations?|docks?))?\s+(?:near|in|around|at)\s+" + _PLACE + _END),
     lambda m: ("bike_share", {"place": m["place"]})),
)


def explore_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if not value or len(value) > 220:
        return None
    for pattern, build in ROUTES:
        if match := pattern.match(value):
            tool, arguments = build(match)
            place = arguments.get("place")
            if isinstance(place, str) and place.lower() in ("me", "us", "my location", "where i am"):
                arguments["place"] = "here"
            return {"tool": tool, "arguments": arguments}
    return None


# ---------------------------------------------------------------------- replies
def _when(value: str | None) -> str:
    if not value:
        return "time not set"
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    delta = moment - datetime.now(UTC)
    hours = delta.total_seconds() / 3600
    relative = (f"in {hours:.0f} h" if 1 <= hours < 36 else f"in {hours / 24:.0f} days" if hours >= 36 else
                f"in {delta.total_seconds() / 60:.0f} min" if hours > 0 else
                f"{-hours:.0f} h ago" if -36 < hours else f"{-hours / 24:.0f} days ago")
    return f"{moment:%d %b %H:%M} UTC ({relative})"


def explore_report(value: dict[str, Any]) -> str | None:
    kind = value.get("explore")
    if not kind:
        return None
    lines: list[str] = []
    if kind == "launches":
        lines.append(("Upcoming" if value["mode"] == "upcoming" else "Recent") + " launches"
                     + (f" matching {value['query']}" if value["query"] else "") + ":")
        for item in value["launches"][:10]:
            lines.append(f"• {item['name']} — {item['provider']} · {_when(item['time'])} · {item['status']}"
                         + (f" · from {item['location']}" if item["location"] else "") + (" · webcast live" if item["webcast_live"] else ""))
        if not value["launches"]:
            lines.append("None found.")
    elif kind == "satellites":
        lines.append(f"{value['count']} {value['group_title']} above {value['place']} now (above {value.get('min_elevation', 10):g}°); Sun at {value['sun_elevation']}°:")
        for item in value["satellites"][:12]:
            lines.append(f"• {item['name']} · {item['elevation']}° up in the {item['direction']} · {item['range_km']:,} km"
                         + (" · could be visible" if item["naked_eye_chance"] else " · sunlit" if item["sunlit"] else " · in Earth's shadow"))
    elif kind == "passes":
        lines.append(f"{value['satellite']} over {value['place']}:")
        best = value.get("next_visible")
        if best:
            lines.append(f"Next visible pass: {_when(best['rise'])}, up to {best['peak_elevation']}° high, from the {best['from']} "
                         f"to the {best['to']}, visible for about {best['visible_minutes']} min.")
        else:
            lines.append('No resolved pass in the period meets the reported visibility criteria.')
        if value.get('unresolved_short_passes'):
            lines.append(f"{value['unresolved_short_passes']} brief threshold crossing(s) were unresolved at 30-second sampling; "
                         'no duration or visibility claim is made for them.')
        for item in value["passes"][:5]:
            lines.append(f"• {_when(item['rise'])} · {item['minutes']} min · max {item['peak_elevation']}° · {item['from']} → {item['to']}"
                         + (f" · visible {item['visible_minutes']} min" if item["visible_minutes"] else ""))
    elif kind == "aircraft":
        aircraft, route = value.get("aircraft"), value.get("route")
        if aircraft:
            lines.append(f"{value['query']}: {aircraft.get('manufacturer') or ''} {aircraft.get('type') or ''} ({aircraft.get('icao_type') or '?'})"
                         f" · registration {aircraft.get('registration') or '?'} · Mode S {aircraft.get('mode_s') or '?'}"
                         + (f" · registered in {aircraft['registered_owner_country_name']}" if aircraft.get("registered_owner_country_name") else ""))
        if route:
            origin, destination = route["origin"], route["destination"]
            lines.append(f"Flight {route.get('callsign')}" + (f" ({route['airline']})" if route.get("airline") else "")
                         + f": {origin.get('municipality') or origin.get('name')} ({origin.get('iata_code') or origin.get('icao_code')}) → "
                         f"{destination.get('municipality') or destination.get('name')} ({destination.get('iata_code') or destination.get('icao_code')})")
    elif kind in ("weather_map", "satellite_image"):
        label = value.get("title") or f"Satellite image, {value.get('date')}"
        lines.append(f"{label} · {value['place']}")
        lines.append(value["path"])
    elif kind == "fires":
        where = f"within {value['radius_km']:g} km of {value['place']}" if value.get("radius_km") else "worldwide"
        lines.append(f"{value['count']:,} satellite fire/heat detections {where} in the last 24 hours (of {value['world_count']:,} worldwide):")
        for item in value["hotspots"][:10]:
            lines.append(f"• {item['power_mw']:.0f} MW · {item['latitude']:.3f}, {item['longitude']:.3f}"
                         + (f" · {item['distance_km']} km away" if item.get("distance_km") is not None else "")
                         + f" · {item['confidence']} confidence · {item['detected']} ({item['daynight']})")
    elif kind == "fire_clusters":
        where = f"within {value['radius_km']:g} km of {value['place']}" if value.get("radius_km") else "worldwide"
        lines.append(f"{value['count']:,} fires {where} in the last 24 hours"
                     + (f"; {value['new']} new and {value['growing']} growing since {value['compared_with'][11:16]} UTC, "
                        f"{value['no_longer_detected']} no longer detected" if value.get("compared_with") else
                        "; change is shown from the next hourly download") + ":")
        for fire in value["fires"][:10]:
            lines.append(f"• {fire['power_mw']:.0f} MW · {fire['detections']} detections over {fire['extent_km']:g} km · "
                         f"{fire['latitude']:.3f}, {fire['longitude']:.3f}"
                         + (f" · {fire['distance_km']} km {fire['direction']}" if fire.get("distance_km") is not None else "")
                         + (f" · {fire['change']}" if fire.get("change") not in (None, "unknown") else "") + f" · latest {fire['latest']}")
    elif kind == "route":
        hours, minutes = divmod(value["duration_minutes"], 60)
        lines.append(f"{ {'car': 'Driving', 'bike': 'Cycling', 'foot': 'Walking'}[value['mode']] } from {value['from']} to {value['to']}: "
                     f"{value['distance_km']} km, about " + (f"{hours} h {minutes} min" if hours else f"{minutes} min") + ".")
        for index, step in enumerate(value["steps"][:15], 1):
            lines.append(f"{index}. {step['instruction']} ({step['distance_km']} km)")
    elif kind == "bikeshare":
        lines.append(f"Bike share near {value['place']} ({', '.join(value['systems'])}): {value['count']} stations within {value['radius_km']:g} km.")
        for item in value["stations"][:10]:
            lines.append(f"• {item['name']} · {item['distance_km']} km · {item['bikes'] if item['bikes'] is not None else '?'} bikes, "
                         f"{item['docks'] if item['docks'] is not None else '?'} free docks" + ("" if item["renting"] else " · not renting"))
    elif kind == "traffic":
        detail = ", ".join(f"{count} {name}" for name, count in value["counts"].items()) or "none"
        lines.append(f"{value['title']}: {value['vehicles']} vehicles visible ({detail}) — {value['level']}.")
        if value.get("dark"):
            lines.append("It is dark there: vehicles seen only as headlights are missed, so the real number is likely higher.")
        history = value.get("history") or {}
        if history.get("compared"):
            lines.append(f"That is {history['compared']} for this hour (usually about {history['usual']:g}, from "
                         f"{history['baseline_samples']} earlier counts).")
        elif history.get("previous"):
            lines.append(f"Last count {history['previous']['vehicles']} at {history['previous']['at'][11:16]} UTC; "
                         f"{history['needed']} more counts near this hour give a usual level to compare with.")
        lines.append(value["image"])
    lines.append(f"Source: {value['source']}. {value['basis']}")
    return "\n".join(lines)
