"""Mapped infrastructure near a place: data centres, dams, military areas and licence-plate readers.

Added 2026-11-08 from github.com/bilawalsidhu/gods-eye-view (MIT). Everything here is what
OpenStreetMap volunteers have mapped (ODbL 1.0, © OpenStreetMap contributors) — a map, not a
statement about who uses a place now, whether a device works, or that the list is complete.

| Kind | Source | How |
|---|---|---|
| Data centres (4,352) | OSM `telecom=data_center` / `building=data_center`, gods-eye-view extract | bundled (data/osm_infrastructure.json) |
| Dams (703, a selection: mostly hydro-power dams) | OSM dams via Open Infrastructure Map, gods-eye-view extract | bundled |
| Named military areas (36,448) | OSM via Overture Maps base/land_use 2026-09-23.1 | bundled |
| Licence-plate readers (US, Canada) | OSM `surveillance:type=ALPR`, DeFlock's hourly extract (tiles.dontgetflocked.com) | live vector tiles |

The bundled file keeps only an OSM id, a representative point, the name and operator (military: class
and area); contact fields were removed upstream and anything that looks like an address, phone number or
link is dropped again here. Plate readers: only the mapped device, its brand, operator and facing are
read — never any plate, image or record, which the map does not hold. Public Overpass servers refuse
automated queries (overpass-api.de robots.txt disallows /api/), so nothing here uses them.
"""
from __future__ import annotations

import gzip
import json
import math
import struct
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.intelligence.explore import bearing_deg, compass, distance_km
from app.intelligence.transit import _first, _varint, fields

DATA = Path(__file__).with_name("data") / "osm_infrastructure.json"
KINDS = {"datacenter": "data centre", "dam": "dam", "military": "military area"}
MILITARY_CLASSES = {"military_land": "military land", "airfield": "military airfield", "naval_base": "naval base",
                    "range": "firing range", "barracks": "barracks", "base": "base", "training_area": "training area"}
OSM_TYPES = {"n": "node", "w": "way", "r": "relation"}
CREDIT = "© OpenStreetMap contributors (ODbL 1.0)"

ALPR_TILES = "https://tiles.dontgetflocked.com/cameras-{country}-hourly/{z}/{x}/{y}.mvt"
#: The tilesets' own bounds (TileJSON), south, north, west, east.
ALPR_BOUNDS = {"us": (17.68, 61.62, -159.70, -64.60), "ca": (41.6, 83.2, -141.1, -52.5)}
#: Detail tiles carry attributes from zoom 9; at most this many tiles are read per question.
MAX_TILES = 9


@lru_cache(maxsize=1)
def _bundle() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def osm_url(reference: str) -> str:
    kind = OSM_TYPES.get(reference[:1], "")
    return f"https://www.openstreetmap.org/{kind}/{reference[1:]}" if kind and reference[1:].isdigit() else ""


# ---------------------------------------------------------------------- vector tiles
def tile_of(latitude: float, longitude: float, zoom: int) -> tuple[float, float]:
    n = 2 ** zoom
    y = (1 - math.asinh(math.tan(math.radians(max(-85.05, min(85.05, latitude))))) / math.pi) / 2 * n
    return (longitude + 180) / 360 * n, y


def _zigzag(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


def _packed(data: Any) -> list[int]:
    values, index, data = [], 0, bytes(data or b"")
    while index < len(data):
        value, index = _varint(data, index)
        values.append(value)
    return values


def _value(data: bytes) -> Any:
    message = fields(data)
    for number, wire, value in message:
        if number == 1 and wire == 2:
            return bytes(value).decode("utf-8", "replace")
        if number in (4, 5) and wire == 0:
            return value if number == 5 or value < 2 ** 63 else value - 2 ** 64
        if number == 6 and wire == 0:
            return _zigzag(value)
        if number == 7 and wire == 0:
            return bool(value)
        if number == 2 and wire == 5:
            return struct.unpack("<f", bytes(value))[0]
        if number == 3 and wire == 1:
            return struct.unpack("<d", bytes(value))[0]
    return None


def decode_points(content: bytes, zoom: int, tile_x: int, tile_y: int, layer_name: str = "cameras") -> list[dict[str, Any]]:
    """Point features of one layer of a Mapbox vector tile, with their attributes and positions in degrees."""
    found = []
    for number, wire, layer_bytes in fields(bytes(content)):
        if number != 3 or wire != 2:
            continue
        layer = fields(layer_bytes)
        if bytes(_first(layer, 1, 2) or b"").decode("utf-8", "replace") != layer_name:
            continue
        extent = _first(layer, 5, 0) or 4096
        keys = [bytes(value).decode("utf-8", "replace") for field, kind, value in layer if field == 3 and kind == 2]
        values = [_value(value) for field, kind, value in layer if field == 4 and kind == 2]
        for field, kind, feature_bytes in layer:
            if field != 2 or kind != 2:
                continue
            feature = fields(feature_bytes)
            if _first(feature, 3, 0) != 1:  # POINT
                continue
            tags = _packed(_first(feature, 2, 2))
            properties = {keys[tags[i]]: values[tags[i + 1]] for i in range(0, len(tags) - 1, 2)
                          if tags[i] < len(keys) and tags[i + 1] < len(values)}
            geometry, index, x, y = _packed(_first(feature, 4, 2)), 0, 0, 0
            while index < len(geometry):
                command, count = geometry[index] & 7, geometry[index] >> 3
                index += 1
                if command != 1:  # only MoveTo is meaningful for points
                    break
                for _ in range(count):
                    if index + 1 >= len(geometry):
                        break
                    x, y = x + _zigzag(geometry[index]), y + _zigzag(geometry[index + 1])
                    index += 2
                    n = 2 ** zoom
                    longitude = (tile_x + x / extent) / n * 360 - 180
                    latitude = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (tile_y + y / extent) / n))))
                    found.append({**properties, "latitude": latitude, "longitude": longitude})
    return found


class Infrastructure:
    def __init__(self, explore: Any) -> None:
        self.explore = explore

    # ----------------------------------------------------------------- bundled map data
    def mapped(self, place: str = "here", kind: str = "all", radius_km: float = 50, limit: int = 15) -> dict[str, Any]:
        where = self.explore._place(place)
        latitude, longitude = where["latitude"], where["longitude"]
        radius = max(1.0, min(float(radius_km), 500.0))
        kinds = list(KINDS) if kind in ("", "all", None) else [kind]
        if any(item not in KINDS for item in kinds):
            raise ValueError("kind must be datacenter, dam, military or all.")
        bundle = _bundle()
        classes = bundle.get("military_classes") or []
        dlat = radius / 110.57
        dlon = radius / max(1e-6, 111.32 * math.cos(math.radians(latitude)))
        found: dict[str, list[dict[str, Any]]] = {}
        for item in kinds:
            rows = []
            for row in bundle.get(item) or []:
                if item == "military":
                    reference, name, lon, lat, klass, area = row
                    operator = ""
                else:
                    reference, name, operator, lon, lat = row
                    klass = area = None
                if abs(lat - latitude) > dlat or abs(((lon - longitude + 180) % 360) - 180) > dlon:
                    continue
                distance = distance_km(latitude, longitude, lat, lon)
                if distance > radius:
                    continue
                entry = {"name": name or f"unnamed {KINDS[item]}", "distance_km": round(distance, 1),
                         "direction": compass(bearing_deg(latitude, longitude, lat, lon)) if distance > .05 else "here",
                         "latitude": lat, "longitude": lon, "osm": osm_url(reference)}
                if operator:
                    entry["operator"] = operator
                if item == "military":
                    label = classes[klass] if isinstance(klass, int) and 0 <= klass < len(classes) else ""
                    entry["class"] = MILITARY_CLASSES.get(label, label.replace("_", " "))
                    entry["area_km2"] = round(area / 1e6, 2) if area else None
                rows.append(entry)
            rows.sort(key=lambda entry: entry["distance_km"])
            found[item] = rows
        cap = max(1, min(int(limit), 50))
        return {"intelligence": True, "explore": "infrastructure", "place": where["name"], "radius_km": radius,
                "kinds": kinds, "counts": {item: len(rows) for item, rows in found.items()},
                "items": {item: rows[:cap] for item, rows in found.items()},
                "operators": {item: _top([entry.get("operator") for entry in rows]) for item, rows in found.items() if item != "military"},
                "source": f"OpenStreetMap extracts bundled from gods-eye-view, {CREDIT}; dams via Open Infrastructure Map, "
                          "military names via Overture Maps (2026-09-23.1)",
                "basis": "What volunteers have mapped in OpenStreetMap, as of the snapshot: not who uses a site now, whether "
                         "it is active, or a complete list (the dams are a 703-site selection, mostly hydro-power dams, so many "
                         "large dams are missing). Distances are to the mapped site's centre."}

    # ----------------------------------------------------------------- plate readers
    def plate_readers(self, place: str = "here", radius_km: float = 3, limit: int = 15) -> dict[str, Any]:
        where = self.explore._place(place)
        latitude, longitude = where["latitude"], where["longitude"]
        radius = max(.2, min(float(radius_km), 10.0))
        countries = [country for country, (south, north, west, east) in ALPR_BOUNDS.items()
                     if south <= latitude <= north and west <= longitude <= east]
        if not countries:
            raise ValueError(f"The open plate-reader map (DeFlock, from OpenStreetMap) covers the United States and Canada; "
                             f"{where['name']} is outside it.")
        zoom = 13 if radius <= 2.5 else 12
        dlat = radius / 110.57
        dlon = radius / max(1e-6, 111.32 * math.cos(math.radians(latitude)))
        x0, y0 = tile_of(latitude + dlat, longitude - dlon, zoom)
        x1, y1 = tile_of(latitude - dlat, longitude + dlon, zoom)
        tiles = [(x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]
        if len(tiles) > MAX_TILES:
            raise ValueError("That area needs too many map tiles; ask for a smaller radius.")
        readers: dict[str, dict[str, Any]] = {}
        read, failed = 0, []
        for country in countries:
            for x, y in tiles:
                url = ALPR_TILES.format(country=country, z=zoom, x=x, y=y)
                try:
                    content = self._tile(url)
                except Exception as exc:  # a missing tile is an empty one; anything else is reported
                    if "404" in str(exc):
                        read += 1
                        continue
                    failed.append(f"{country} {zoom}/{x}/{y}: {str(exc)[:80]}")
                    continue
                read += 1
                for point in decode_points(content, zoom, x, y):
                    distance = distance_km(latitude, longitude, point["latitude"], point["longitude"])
                    if distance > radius:
                        continue
                    kind, number = str(point.get("osmType") or ""), point.get("osmId")
                    key = f"{kind}/{number}" if number is not None else f"{point['latitude']:.6f},{point['longitude']:.6f}"
                    facing = point.get("directionCardinal") or (compass(float(point["direction"]))
                                                                 if isinstance(point.get("direction"), (int, float)) else None)
                    readers[key] = {"distance_km": round(distance, 2),
                                    "direction": compass(bearing_deg(latitude, longitude, point["latitude"], point["longitude"])),
                                    "latitude": round(point["latitude"], 5), "longitude": round(point["longitude"], 5),
                                    "brand": _short(point.get("brand")), "operator": _short(point.get("operator")),
                                    "facing": str(facing).upper() if facing else None,
                                    "mapped": str(point.get("osmTimestamp") or "")[:10] or None,
                                    "osm": f"https://www.openstreetmap.org/{kind}/{number}" if kind in ("node", "way") and number else ""}
        if failed and not read:
            raise ValueError("The plate-reader map did not answer: " + "; ".join(failed[:2]))
        rows = sorted(readers.values(), key=lambda item: item["distance_km"])
        return {"intelligence": True, "explore": "plate_readers", "place": where["name"], "radius_km": radius,
                "count": len(rows), "readers": rows[:max(1, min(int(limit), 50))],
                "operators": _top([item["operator"] for item in rows]), "brands": _top([item["brand"] for item in rows]),
                "tiles_read": read, "unavailable": failed,
                "source": f"DeFlock's hourly OpenStreetMap extract (surveillance:type=ALPR), {CREDIT}",
                "basis": "Licence-plate readers volunteers have mapped in OpenStreetMap: where devices are, not plate "
                         "records, not proof a device is working, and not a complete list. 'Mapped' is the last edit date, "
                         "not a check in the field."}

    def _tile(self, url: str) -> bytes:
        """One vector tile, kept an hour (the extract is rebuilt hourly)."""
        key = "alpr " + url
        with self.explore._lock:
            cached = self.explore._cache.get(key)
        if cached and time.time() - cached[0] < 3600:
            return cached[1]
        content = bytes(self.explore.fetcher.fetch(url, 4 * 1024 * 1024,
                                                   {"application/x-protobuf", "application/vnd.mapbox-vector-tile",
                                                    "application/octet-stream"}).content)
        if content[:2] == b"\x1f\x8b":
            content = gzip.decompress(content)
        with self.explore._lock:
            self.explore._cache[key] = (time.time(), content)
            while len(self.explore._cache) > 200:
                self.explore._cache.pop(next(iter(self.explore._cache)))
        return content


def _short(value: Any, limit: int = 60) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:limit] if text and text not in ("?",) else None


def _top(values: list[Any], count: int = 5) -> list[tuple[str, int]]:
    tally: dict[str, int] = {}
    for value in values:
        if value:
            tally[value] = tally.get(value, 0) + 1
    return sorted(tally.items(), key=lambda pair: (-pair[1], pair[0]))[:count]


INFRASTRUCTURE_TOOLS = ("mapped_infrastructure", "plate_readers")


def register_infrastructure_tools(registry: Any, infrastructure: Infrastructure) -> None:
    place = {"type": "string", "maxLength": 200}
    registry.register("mapped_infrastructure", "Data centres, dams and named military areas mapped in OpenStreetMap near a place: "
                      "name, operator, distance and direction (bundled ODbL extract; a map, not current activity).",
                      {"type": "object", "properties": {"place": place, "kind": {"enum": ["all", *KINDS]},
                                                        "radius_km": {"type": "number", "minimum": 1, "maximum": 500},
                                                        "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                       "required": [], "additionalProperties": False},
                      "low", "public_web_read", infrastructure.mapped)
    registry.register("plate_readers", "Licence-plate reader (ALPR) cameras volunteers have mapped in OpenStreetMap near a place in "
                      "the US or Canada (DeFlock's hourly extract): how many, operators, brands, nearest and which way they face. "
                      "Locations only, never plate data.",
                      {"type": "object", "properties": {"place": place, "radius_km": {"type": "number", "minimum": .2, "maximum": 10},
                                                        "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
                       "required": [], "additionalProperties": False},
                      "low", "public_web_read", infrastructure.plate_readers)


def report_lines(value: dict[str, Any]) -> list[str]:
    if value["explore"] == "infrastructure":
        counts = value["counts"]
        lines = [f"Mapped within {value['radius_km']:g} km of {value['place']}: "
                 + ", ".join(f"{counts[item]} {KINDS[item]}{'s' if counts[item] != 1 else ''}" for item in value["kinds"]) + "."]
        for item in value["kinds"]:
            for entry in value["items"][item][:8]:
                parts = [entry["name"]]
                if entry.get("operator") and entry["operator"] != entry["name"]:
                    parts.append(entry["operator"])
                if entry.get("class"):
                    parts.append(entry["class"] + (f", {entry['area_km2']:g} km²" if entry.get("area_km2") else ""))
                lines.append(f"• {KINDS[item]}: " + " · ".join(parts) + f" — {entry['distance_km']:g} km {entry['direction']}")
            if value["operators"].get(item):
                lines.append(f"  Most {KINDS[item]}s by operator: " + ", ".join(f"{name} {count}" for name, count in value["operators"][item]))
        lines.append("A map, not a statement about current activity. " + CREDIT + ".")
        return lines
    if value["explore"] == "plate_readers":
        lines = [f"{value['count']} mapped licence-plate reader{'s' if value['count'] != 1 else ''} within {value['radius_km']:g} km of "
                 f"{value['place']}" + (":" if value["count"] else ".")]
        if value["operators"]:
            lines.append("Operators: " + ", ".join(f"{name} {count}" for name, count in value["operators"]) + ".")
        if value["brands"]:
            lines.append("Brands: " + ", ".join(f"{name} {count}" for name, count in value["brands"]) + ".")
        for item in value["readers"][:10]:
            lines.append(f"• {item['distance_km']:g} km {item['direction']}" + (f", facing {item['facing']}" if item.get("facing") else "")
                         + (f" · {item['brand']}" if item.get("brand") else "") + (f" · {item['operator']}" if item.get("operator") else "")
                         + (f" · mapped {item['mapped']}" if item.get("mapped") else ""))
        if value.get("unavailable"):
            lines.append(f"{len(value['unavailable'])} map tile(s) did not load, so the count may be short.")
        lines.append("Locations volunteers mapped in OpenStreetMap (DeFlock); not plate records or proof a device works.")
        return lines
    return []
