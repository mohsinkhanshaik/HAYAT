"""Ground height anywhere on Earth, and how much a trip between two places climbs.

Added 2026-11-08 from the terrain-heights proxy of github.com/bilawalsidhu/gods-eye-view (MIT).
Before this HAYAT knew heights only inside the United States (USGS EPQS, live.py).

| Feature | Source | Licence |
|---|---|---|
| Height above sea level | Re:Earth Terrain heights API (terrain.reearth.land/heights.json) | Mapterhorn DEM CC BY 4.0; EGM2008 geoid public domain (NGA) |

Re:Earth Terrain is keyless, needs no signup and asks only that one client does not dominate
its traffic ("we may add rate limits without notice"). HAYAT therefore asks once per question
with at most 64 points, and keeps every answer on disk for 30 days: terrain does not move. The
DEM is Mapterhorn's blend of Copernicus GLO-30, NASADEM and others (roughly 30 m cells), so a
summit reads lower than its surveyed height and there are no sea-floor depths.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any
from urllib.parse import urlencode

from app.intelligence.explore import distance_km

HEIGHTS = "https://terrain.reearth.land/heights.json"
CREDIT = "Re:Earth Terrain · Mapterhorn (CC BY 4.0)"
MAX_POINTS = 64
#: Terrain does not move; one answer is kept a month.
TTL = 30 * 86400
#: Lowest and highest dry land on Earth with room for model noise; anything outside is not a height.
LOWEST, HIGHEST = -500.0, 9000.0


def great_circle(lat1: float, lon1: float, lat2: float, lon2: float, count: int) -> list[tuple[float, float]]:
    """count evenly spaced points from the first place to the second, both ends included."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (math.cos(p1) * math.cos(l1), math.cos(p1) * math.sin(l1), math.sin(p1))
    b = (math.cos(p2) * math.cos(l2), math.cos(p2) * math.sin(l2), math.sin(p2))
    omega = math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)))))
    points = []
    for index in range(count):
        t = index / (count - 1)
        if omega < 1e-9:
            x, y, z = a
        else:
            s1, s2 = math.sin((1 - t) * omega) / math.sin(omega), math.sin(t * omega) / math.sin(omega)
            x, y, z = (s1 * a[i] + s2 * b[i] for i in range(3))
        points.append((math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))))
    return points


def climb(heights: list[float]) -> tuple[float, float]:
    """Total ascent and descent along sampled heights (metres)."""
    up = sum(max(0.0, b - a) for a, b in zip(heights, heights[1:]))
    down = sum(max(0.0, a - b) for a, b in zip(heights, heights[1:]))
    return up, down


def feet(metres: float) -> int:
    return round(metres / 0.3048)


class Terrain:
    def __init__(self, explore: Any) -> None:
        self.explore = explore

    def heights(self, points: list[tuple[float, float]]) -> list[dict[str, float]]:
        """Heights above mean sea level for (latitude, longitude) points, in one request."""
        if not points or len(points) > MAX_POINTS:
            raise ValueError(f"Ask for 1 to {MAX_POINTS} points at a time.")
        query = ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in points)
        key = "terrain-" + hashlib.sha1(query.encode(), usedforsecurity=False).hexdigest()[:20]
        data = self.explore._json(key, HEIGHTS + "?" + urlencode({"points": query}), TTL, 1024 * 1024, disk=True)
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list) or len(results) != len(points):
            raise ValueError("Re:Earth Terrain answered without a height for every point.")
        found = []
        for row in results:
            elevation, geoid = (row or {}).get("elevation"), (row or {}).get("geoid")
            if not isinstance(elevation, (int, float)) or not math.isfinite(elevation) or not LOWEST <= elevation <= HIGHEST:
                raise ValueError("Re:Earth Terrain sent a height that is not a height on Earth.")
            found.append({"elevation_m": round(float(elevation), 1),
                          "geoid_m": round(float(geoid), 1) if isinstance(geoid, (int, float)) and math.isfinite(geoid) else None})
        return found

    def elevation(self, place: str = "here", to: str = "", samples: int = 32) -> dict[str, Any]:
        start = self.explore._place(place)
        base = {"intelligence": True, "source": f"{CREDIT}; EGM2008 geoid (NGA, public domain)",
                "basis": "Ground height above mean sea level from a global terrain model of roughly 30 m cells, at the "
                         "point the place name resolves to. Summits read lower than their surveyed height, buildings are "
                         "not included, and the sea reads 0 m (no sea-floor depths). Not for navigation or safety-critical use."}
        if not (to or "").strip():
            height = self.heights([(start["latitude"], start["longitude"])])[0]
            return {**base, "explore": "elevation", "place": start["name"], "latitude": start["latitude"],
                    "longitude": start["longitude"], **height, "elevation_ft": feet(height["elevation_m"]),
                    "sea_or_sea_level": height["elevation_m"] == 0}
        end = self.explore._place(to)
        length = distance_km(start["latitude"], start["longitude"], end["latitude"], end["longitude"])
        if length > 2000:
            raise ValueError(f"{start['name']} to {end['name']} is {length:,.0f} km; a height profile is offered up to 2,000 km.")
        count = max(8, min(int(samples), MAX_POINTS)) if length >= .2 else 2
        path = great_circle(start["latitude"], start["longitude"], end["latitude"], end["longitude"], count)
        heights = [item["elevation_m"] for item in self.heights(path)]
        up, down = climb(heights)
        step = length / (count - 1)
        top, bottom = max(range(count), key=heights.__getitem__), min(range(count), key=heights.__getitem__)
        return {**base, "explore": "elevation_profile", "place": start["name"], "to": end["name"],
                "distance_km": round(length, 1), "step_km": round(step, 2),
                "start_m": heights[0], "end_m": heights[-1], "net_m": round(heights[-1] - heights[0], 1),
                "climb_m": round(up), "descent_m": round(down),
                "highest": {"elevation_m": heights[top], "at_km": round(top * step, 1)},
                "lowest": {"elevation_m": heights[bottom], "at_km": round(bottom * step, 1)},
                "profile": [{"at_km": round(index * step, 2), "elevation_m": value} for index, value in enumerate(heights)],
                "note": f"Sampled every {step:.2f} km in a straight line (not along roads); rises and dips between samples "
                        "are not counted."}


TERRAIN_TOOLS = ("place_elevation",)


def register_terrain_tools(registry: Any, terrain: Terrain) -> None:
    registry.register("place_elevation", "Height above sea level of any place on Earth (Re:Earth Terrain / Mapterhorn), or, "
                      "with 'to', a straight-line height profile between two places: climb, descent, highest and lowest point.",
                      {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                                        "to": {"type": "string", "maxLength": 200},
                                                        "samples": {"type": "integer", "minimum": 8, "maximum": MAX_POINTS}},
                       "required": [], "additionalProperties": False},
                      "low", "public_web_read", terrain.elevation)


def report_lines(value: dict[str, Any]) -> list[str]:
    if value["explore"] == "elevation":
        metres = value["elevation_m"]
        if value.get("sea_or_sea_level"):
            line = f"{value['place']} reads 0 m: at sea level or over water (this model has no sea-floor depths)."
        elif metres < 0:
            line = f"The ground at {value['place']} is {-metres:,.0f} m ({-value['elevation_ft']:,} ft) below sea level."
        else:
            line = f"The ground at {value['place']} is {metres:,.0f} m ({value['elevation_ft']:,} ft) above sea level."
        return [line, f"Point {value['latitude']:.4f}, {value['longitude']:.4f}. {CREDIT}."]
    if value["explore"] == "elevation_profile":
        return [f"{value['place']} ({value['start_m']:,.0f} m) to {value['to']} ({value['end_m']:,.0f} m), "
                f"{value['distance_km']:,} km in a straight line: climbs {value['climb_m']:,} m and descends {value['descent_m']:,} m "
                f"in total (net {value['net_m']:+,.0f} m).",
                f"Highest {value['highest']['elevation_m']:,.0f} m at {value['highest']['at_km']:g} km; lowest "
                f"{value['lowest']['elevation_m']:,.0f} m at {value['lowest']['at_km']:g} km.",
                value["note"] + f" {CREDIT}."]
    return []
