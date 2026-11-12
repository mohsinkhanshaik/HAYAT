"""Is this place inside restricted airspace? FAA prohibited and restricted areas and temporary flight restrictions.

The prohibited/restricted layer follows the Prohibited & Restricted Airspace layer of
github.com/bilawalsidhu/gods-eye-view (MIT): the FAA's Special Use Airspace table, types P and
R only, the always-defined slice. Temporary flight restrictions (VIP visits, stadiums, fires,
security zones) come from tfr.faa.gov and are HAYAT's addition. Both are U.S. public domain and
cover United States airspace only.

The FAA's ArcGIS organisation enforces a shared per-minute quota, and the table changes on the
56-day aeronautical cycle, so it is kept on disk for a day and the last copy is used (and said
to be used) when the FAA refuses. This is information, not a flight briefing: drone pilots in
the US still check B4UFLY or LAANC, and every pilot checks NOTAMs.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

SUA = ("https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/Special_Use_Airspace/FeatureServer/0/query?"
       + urlencode({"where": "TYPE_CODE IN ('P','R')",
                    "outFields": "NAME,TYPE_CODE,UPPER_VAL,UPPER_UOM,UPPER_CODE,LOWER_VAL,LOWER_UOM,LOWER_CODE,TIMESOFUSE,CONT_AGENT,CITY,STATE",
                    "maxAllowableOffset": "0.001", "outSR": "4326", "f": "geojson"}))
TFR_SHAPES = ("https://tfr.faa.gov/geoserver/TFR/ows?service=WFS&version=1.1.0&request=GetFeature&typeName=TFR:V_TFR_LOC"
              "&maxFeatures=500&outputFormat=application/json&srsname=EPSG:4326")
TFR_LIST = "https://tfr.faa.gov/tfrapi/exportTfrList"
SUA_TTL, SUA_STALE = 86400, 30 * 86400
TFR_TTL, TFR_STALE = 900, 6 * 3600
KINDS = {"P": "Prohibited area", "R": "Restricted area"}
#: US airspace, roughly: the 50 states, Puerto Rico and the Pacific territories. A point outside gets a plain "not covered".
US_BOXES = ((24.0, 50.0, -125.5, -66.0), (51.0, 72.0, -180.0, -129.0), (18.5, 22.5, -161.0, -154.5),
            (17.5, 18.6, -67.5, -64.5), (13.0, 21.0, 144.0, 146.5), (-14.6, -14.1, -171.0, -169.4))
NOTE = ("Information, not a flight briefing: US drone pilots still check B4UFLY or LAANC, and pilots check NOTAMs. "
        "Controlled airspace around airports is not part of this check.")


def in_us(latitude: float, longitude: float) -> bool:
    return any(south <= latitude <= north and west <= longitude <= east for south, north, west, east in US_BOXES)


def rings(geometry: dict[str, Any] | None) -> list[list[list[list[float]]]]:
    """Polygon or MultiPolygon → a list of polygons, each a list of rings ([lon, lat] points)."""
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiPolygon":
        return list(geometry.get("coordinates") or [])
    return []


def _inside_ring(latitude: float, longitude: float, ring: list[list[float]]) -> bool:
    inside = False
    for index in range(len(ring)):
        x1, y1 = ring[index - 1][0], ring[index - 1][1]
        x2, y2 = ring[index][0], ring[index][1]
        if (y1 > latitude) != (y2 > latitude) and longitude < (x2 - x1) * (latitude - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def contains(geometry: dict[str, Any] | None, latitude: float, longitude: float) -> bool:
    for polygon in rings(geometry):
        if polygon and _inside_ring(latitude, longitude, polygon[0]) \
                and not any(_inside_ring(latitude, longitude, hole) for hole in polygon[1:]):
            return True
    return False


def edge_km(geometry: dict[str, Any] | None, latitude: float, longitude: float) -> float | None:
    """Distance from the point to the nearest boundary, in a local flat projection (good to well under 1 % at these sizes)."""
    scale_x, scale_y = 111.32 * math.cos(math.radians(latitude)), 110.57
    best = None
    for polygon in rings(geometry):
        for ring in polygon:
            for index in range(1, len(ring)):
                ax, ay = (ring[index - 1][0] - longitude) * scale_x, (ring[index - 1][1] - latitude) * scale_y
                bx, by = (ring[index][0] - longitude) * scale_x, (ring[index][1] - latitude) * scale_y
                dx, dy = bx - ax, by - ay
                t = max(0.0, min(1.0, -(ax * dx + ay * dy) / ((dx * dx + dy * dy) or 1e-12)))
                distance = math.hypot(ax + t * dx, ay + t * dy)
                best = distance if best is None or distance < best else best
    return best


def altitude(value: Any, unit: Any, code: Any) -> str:
    if value in (None, ""):
        return "?"
    text = str(value).strip()
    if text.upper() in ("0", "SFC", "GND") and str(code or "").upper() in ("SFC", "", "GND"):
        return "surface"
    if text.upper() == "UNLTD" or text == "-9998":  # the FAA tables code "unlimited" as -9998
        return "unlimited"
    unit = str(unit or "").upper()
    return f"FL{text}" if unit == "FL" else f"{text} ft" + (" above ground" if str(code or "").upper() == "AGL" else " MSL" if code else "")


class Airspace:
    def __init__(self, get_json: Callable[[str, int], Any], folder: Path | str, resolve: Callable[[str], dict[str, Any]],
                 clock: Callable[[], float] = time.time) -> None:
        self.get_json, self.resolve, self.clock = get_json, resolve, clock
        self.folder = Path(folder)
        self._memory: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _cached(self, name: str, load: Callable[[], Any], ttl: float, stale: float) -> tuple[Any, float | None]:
        """(value, age in seconds if a stale copy had to be used, else None)."""
        now = self.clock()
        path = self.folder / f"{name}.json"
        with self._lock:
            held = self._memory.get(name)
        if held is None and path.exists():
            try:
                held = (path.stat().st_mtime, json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                held = None
        if held and now - held[0] < ttl:
            return held[1], None
        try:
            value = load()
        except Exception:
            if held and now - held[0] < stale:
                return held[1], now - held[0]
            raise
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        with self._lock:
            self._memory[name] = (now, value)
        return value, None

    def _sua(self) -> list[dict[str, Any]]:
        features, offset = [], 0
        for _page in range(5):
            page = self.get_json(SUA + (f"&resultOffset={offset}" if offset else ""), 16 * 1024 * 1024)
            if not isinstance(page, dict) or "features" not in page:
                raise ValueError(str((page or {}).get("error", {}).get("message") or "FAA airspace answered without features")[:160])
            features += page["features"]
            if not (page.get("exceededTransferLimit") or (page.get("properties") or {}).get("exceededTransferLimit")):
                break
            offset = len(features)
        zones = []
        for feature in features:
            properties = feature.get("properties") or {}
            if not rings(feature.get("geometry")):
                continue
            # Altitudes are kept as published and worded when answering, so a wording fix needs no new download.
            zones.append({"name": str(properties.get("NAME") or "").strip(), "kind": KINDS.get(properties.get("TYPE_CODE"), "Special use airspace"),
                          "lower": [properties.get("LOWER_VAL"), properties.get("LOWER_UOM"), properties.get("LOWER_CODE")],
                          "upper": [properties.get("UPPER_VAL"), properties.get("UPPER_UOM"), properties.get("UPPER_CODE")],
                          "times": str(properties.get("TIMESOFUSE") or "").strip(), "agency": str(properties.get("CONT_AGENT") or "").strip(),
                          "where": ", ".join(str(part).strip() for part in (properties.get("CITY"), properties.get("STATE")) if part),
                          "geometry": feature["geometry"]})
        return zones

    def _tfrs(self) -> list[dict[str, Any]]:
        shapes = self.get_json(TFR_SHAPES, 16 * 1024 * 1024)
        listed = {str(item.get("notam_id")): item for item in self.get_json(TFR_LIST, 4 * 1024 * 1024) or [] if isinstance(item, dict)}
        zones = []
        for feature in (shapes or {}).get("features") or []:
            properties = feature.get("properties") or {}
            notam = re.match(r"(\d+/\d+)", str(properties.get("NOTAM_KEY") or ""))
            if not notam or not rings(feature.get("geometry")):
                continue
            item = listed.get(notam[1])
            if item is None:
                continue  # a shape whose NOTAM is no longer listed has expired
            zones.append({"name": str(properties.get("TITLE") or item.get("description") or "").strip(),
                          "kind": f"Temporary flight restriction ({str(item.get('type') or properties.get('LEGAL') or 'TFR').title()})",
                          "notam": notam[1], "description": str(item.get("description") or "").strip(),
                          "where": str(item.get("state") or properties.get("STATE") or "").strip(),
                          "page": f"https://tfr.faa.gov/tfr3/?page=detail_{notam[1].replace('/', '_')}",
                          "geometry": feature["geometry"]})
        return zones

    def check(self, place: str = "here", radius_km: float = 30) -> dict[str, Any]:
        found = self.resolve(place or "here")
        latitude, longitude = float(found["latitude"]), float(found["longitude"])
        name = found.get("name") or place
        if not in_us(latitude, longitude):
            raise ValueError(f"{name} is outside United States airspace; HAYAT's airspace data is the FAA's and covers the US only.")
        radius = max(1.0, min(float(radius_km), 200.0))
        zones, stale, unavailable = [], {}, {}
        for label, loader, ttl, limit in (("restricted areas", self._sua, SUA_TTL, SUA_STALE), ("temporary restrictions", self._tfrs, TFR_TTL, TFR_STALE)):
            try:
                value, age = self._cached("airspace-" + label.split()[0], loader, ttl, limit)
            except Exception as exc:  # report the gap; the other half still answers
                unavailable[label] = str(exc)[:160] or type(exc).__name__
                continue
            if age is not None:
                stale[label] = round(age / 3600, 1)
            zones += value
        if len(unavailable) == 2:
            raise ValueError("The FAA airspace services did not answer: " + "; ".join(unavailable.values()))
        inside: dict[tuple[str, str], dict[str, Any]] = {}
        nearby: dict[tuple[str, str], dict[str, Any]] = {}
        for zone in zones:
            card = {key: value for key, value in zone.items() if key not in ("geometry", "lower", "upper")}
            if zone.get("lower") or zone.get("upper"):
                card["floor"], card["ceiling"] = altitude(*zone.get("lower") or [None] * 3), altitude(*zone.get("upper") or [None] * 3)
            # One NOTAM or area can be drawn as several shapes: it is listed once.
            key = (card["kind"], card.get("notam") or card["name"])
            if contains(zone["geometry"], latitude, longitude):
                inside.setdefault(key, card)
                continue
            distance = edge_km(zone["geometry"], latitude, longitude)
            if distance is not None and distance <= radius and (key not in nearby or distance < nearby[key]["edge_km"]):
                nearby[key] = {**card, "edge_km": round(distance, 1)}
        inside = list(inside.values())
        nearby = sorted((card for key, card in nearby.items() if key not in {(item["kind"], item.get("notam") or item["name"])
                                                                             for item in inside}), key=lambda item: item["edge_km"])
        return {"intelligence": True, "explore": "airspace", "place": name, "latitude": latitude, "longitude": longitude,
                "radius_km": radius, "inside": inside, "nearby": nearby[:15], "restricted": bool(inside),
                "stale_hours": stale, "unavailable": unavailable,
                "source": "FAA Special Use Airspace (prohibited and restricted areas) and tfr.faa.gov (U.S. public domain)",
                "basis": NOTE}


def register_airspace_tools(registry: Any, airspace: Airspace) -> None:
    registry.register(
        "airspace_check",
        "Is a US place inside, or near, prohibited or restricted airspace or a temporary flight restriction (VIP, stadium, "
        "fire, security)? FAA data: altitudes, times of use, controlling agency, NOTAM link. US only; not a flight briefing.",
        {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                          "radius_km": {"type": "number", "minimum": 1, "maximum": 200}},
         "required": [], "additionalProperties": False},
        "low", "public_web_read", airspace.check)


def _zone_line(zone: dict[str, Any]) -> str:
    parts = [f"{zone['kind']}: {zone['name'] or zone.get('notam') or '?'}"]
    if zone.get("floor") or zone.get("ceiling"):
        parts.append(f"{zone.get('floor', '?')} to {zone.get('ceiling', '?')}")
    if zone.get("times"):
        parts.append(f"in use {zone['times'][:80]}")
    if zone.get("agency"):
        parts.append(f"controlled by {zone['agency'][:60]}")
    if zone.get("edge_km") is not None:
        parts.append(f"edge {zone['edge_km']} km away")
    if zone.get("page"):
        parts.append(zone["page"])
    return " · ".join(parts)


def report_lines(value: dict[str, Any]) -> list[str]:
    if value["inside"]:
        lines = [f"{value['place']} is INSIDE {len(value['inside'])} restricted zone{'s' if len(value['inside']) > 1 else ''}:"]
        lines += ["▲ " + _zone_line(zone) for zone in value["inside"]]
    else:
        # Claim only what was checked: when one FAA service failed, its half is not part of "not inside".
        words = {"restricted areas": "FAA prohibited or restricted area", "temporary restrictions": "temporary flight restriction"}
        checked = [label for label in words if label not in (value.get("unavailable") or {})]
        lines = [f"{value['place']} is not inside any " + " or ".join(words[label] for label in checked) + "."]
    if value["nearby"]:
        lines.append(f"Within {value['radius_km']:g} km:")
        lines += ["• " + _zone_line(zone) for zone in value["nearby"][:10]]
    for label, hours in (value.get("stale_hours") or {}).items():
        lines.append(f"The FAA did not answer for {label}; using HAYAT's copy from {hours:g} h ago.")
    for label, problem in (value.get("unavailable") or {}).items():
        lines.append(f"Could not check {label}: {problem[:100].rstrip('.')}. Treat that part as unknown, not clear.")
    return lines
