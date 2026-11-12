"""Tropical cyclone forecasts: where each storm is heading, its cone, and what it means for a place.

Added 2026-11-08 from the Cyclones layer of github.com/bilawalsidhu/gods-eye-view (MIT). HAYAT already
listed active storms (feeds.py storms_nhc); this adds the official forecast itself:

| Part | Source | Layer |
|---|---|---|
| Storms now | NHC/CPHC CurrentStorms.json | position, intensity, motion, advisory links |
| Forecast points and track | NOAA tropical weather summary MapServer | 5 and 6 |
| Forecast cone | same | 7 |
| Coastal watches and warnings | same | 8 |
| Chance of tropical-storm-force winds (5 days) | same | 30 |
| Areas being watched for formation (7 days) | same | 3 |

All NOAA/NWS public data under the NWS disclaimer (weather.gov/disclaimer); no endorsement implied.
Coverage is the Atlantic and the eastern and central North Pacific only. The cone shows where the
storm's *centre* is likely to go, not how big the storm is or where its hazards end.
"""
from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from app.intelligence.airspace import contains, edge_km
from app.intelligence.explore import bearing_deg, compass, distance_km

STATUS = "https://www.nhc.noaa.gov/CurrentStorms.json"
GIS = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather_summary/MapServer"
LAYERS = {"points": 5, "track": 6, "cone": 7, "warnings": 8, "wind34": 30, "outlook": 3}
CLASSES = {"TD": "tropical depression", "TS": "tropical storm", "HU": "hurricane", "MH": "major hurricane",
           "STD": "subtropical depression", "STS": "subtropical storm", "PTC": "potential tropical cyclone",
           "PC": "post-tropical cyclone", "TY": "typhoon", "DB": "disturbance"}
WARNINGS = {"HWR": "hurricane warning", "HWA": "hurricane watch", "TWR": "tropical storm warning", "TWA": "tropical storm watch"}
COVERAGE = "Atlantic and eastern/central North Pacific (NHC and CPHC); not the western Pacific, Indian Ocean or southern hemisphere."
#: A place this far from every storm, cone and watch area is not near any of them.
NEAR_KM = 1500.0


def category(knots: float | None) -> str:
    """Saffir-Simpson category for a hurricane's sustained wind."""
    if knots is None or knots < 64:
        return ""
    return "category " + str(1 if knots < 83 else 2 if knots < 96 else 3 if knots < 113 else 4 if knots < 137 else 5)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and abs(number) < 9000 else None  # NHC writes 9999 for "not given"


def _advisory(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.lstrip("0") or "0" if re.fullmatch(r"\d{1,3}[A-Z]?", text) else ""


def _contains(geometry: Any, latitude: float, longitude: float) -> bool:
    # Tracks past the date line carry longitudes beyond ±180 on one side or the other.
    return any(contains(geometry, latitude, longitude + shift) for shift in (0.0, -360.0, 360.0))


def _edge(geometry: Any, latitude: float, longitude: float) -> float | None:
    found = [value for shift in (0.0, -360.0, 360.0) if (value := edge_km(geometry, latitude, longitude + shift)) is not None]
    return min(found) if found else None


def _valid(text: Any, issued: datetime | None) -> str | None:
    """NHC 'DD/HHMM' (UTC) as an ISO time, in the month of the advisory."""
    match = re.fullmatch(r"(\d{2})/(\d{2})(\d{2})", str(text or ""))
    if not match or issued is None:
        return None
    day, hour, minute = map(int, match.groups())
    year, month = issued.year, issued.month
    if day < issued.day - 7:  # the forecast ran into next month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    try:
        return datetime(year, month, day, hour, minute, tzinfo=UTC).isoformat(timespec="minutes")
    except ValueError:
        return None


def _vertices(geometry: Any) -> list[tuple[float, float]]:
    """Every (lat, lon) of a point, line or polygon geometry."""
    found: list[tuple[float, float]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            found.append((float(value[1]), float(value[0])))
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk((geometry or {}).get("coordinates"))
    return found


class TropicalCyclones:
    def __init__(self, explore: Any) -> None:
        self.explore = explore

    def _layer(self, name: str) -> list[dict[str, Any]]:
        url = f"{GIS}/{LAYERS[name]}/query?" + urlencode({"where": "1=1", "outFields": "*", "outSR": "4326", "geometryPrecision": "3",
                                                          "resultRecordCount": "500", "f": "geojson"})
        data = self.explore._json(f"nhc-gis-{name}", url, 600, 8 * 1024 * 1024)
        if not isinstance(data, dict) or not isinstance(data.get("features"), list):
            raise ValueError(f"NOAA's tropical map service sent no {name} layer.")
        return [feature for feature in data["features"] if isinstance(feature, dict)]

    def forecast(self, place: str = "", name: str = "") -> dict[str, Any]:
        status = self.explore._json("nhc-current-storms", STATUS, 300, 512 * 1024)
        rows = status.get("activeStorms") if isinstance(status, dict) else None
        if not isinstance(rows, list):
            raise ValueError("The National Hurricane Center's storm list did not load.")
        where = self.explore._place(place) if (place or "").strip() else None
        layers: dict[str, list[dict[str, Any]]] = {}
        unavailable: dict[str, str] = {}
        for layer in LAYERS:
            if layer != "outlook" and not rows:
                continue  # no storm: only the formation outlook has anything to say
            try:
                layers[layer] = self._layer(layer)
            except Exception as exc:  # one map layer down must not hide the storms
                unavailable[layer] = str(exc)[:160] or type(exc).__name__
        wanted = (name or "").strip().lower()
        storms = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            storm_name = str(row.get("name") or "").strip()
            if wanted and wanted not in storm_name.lower() and wanted != str(row.get("id") or "").lower():
                continue
            storms.append(self._storm(row, layers, where))
        if wanted and not storms:
            names = ", ".join(str(row.get("name")) for row in rows if isinstance(row, dict)) or "none"
            raise ValueError(f"No active NHC/CPHC storm is named {name}. Active now: {names}.")
        storms.sort(key=lambda item: item.get("distance_km") if item.get("distance_km") is not None else 0)
        value: dict[str, Any] = {"intelligence": True, "explore": "cyclones", "count": len(storms), "storms": storms,
                                 "place": where["name"] if where else None, "coverage": COVERAGE, "unavailable": unavailable,
                                 "source": "NOAA National Hurricane Center / Central Pacific Hurricane Center (U.S. public data)",
                                 "basis": "Official advisories as last issued (every 6 hours, more often near land). The cone "
                                          "is where the storm's centre is likely to go (about two times in three), not the "
                                          "storm's size or where its wind, rain and surge end. Follow local officials, not this "
                                          "summary."}
        if "outlook" in layers:
            value["outlook"] = self._outlook(layers["outlook"], where)
        if where and "wind34" in layers:
            value["wind_chance"] = self._wind_chance(layers["wind34"], where)
        return value

    # ----------------------------------------------------------------- one storm
    def _storm(self, row: dict[str, Any], layers: dict[str, list[dict[str, Any]]], where: dict[str, Any] | None) -> dict[str, Any]:
        latitude, longitude = _number(row.get("latitudeNumeric")), _number(row.get("longitudeNumeric"))
        wind = _number(row.get("intensity"))
        speed_mph = _number(row.get("movementSpeed"))
        heading = _number(row.get("movementDir"))
        advisory = row.get("publicAdvisory") if isinstance(row.get("publicAdvisory"), dict) else {}
        number = _advisory(advisory.get("advNum"))
        try:
            issued = datetime.fromisoformat(str(advisory.get("issuance") or row.get("lastUpdate")).replace("Z", "+00:00"))
        except ValueError:
            issued = None
        kind = CLASSES.get(str(row.get("classification") or "").upper(), str(row.get("classification") or "storm"))
        bin_number = str(row.get("binNumber") or "").upper()
        storm: dict[str, Any] = {
            "name": str(row.get("name") or "Unnamed"), "id": row.get("id"), "kind": kind,
            "wind_kt": wind, "wind_mph": round(wind * 1.15078) if wind is not None else None,
            "wind_kmh": round(wind * 1.852) if wind is not None else None, "category": category(wind),
            "pressure_mb": _number(row.get("pressure")), "latitude": latitude, "longitude": longitude,
            "moving": compass(heading) if heading is not None and speed_mph else ("stationary" if speed_mph == 0 else None),
            "moving_deg": heading, "speed_mph": speed_mph, "speed_kt": round(speed_mph * 0.868976) if speed_mph is not None else None,
            "advisory": number, "issued": issued.isoformat(timespec="minutes") if issued else None,
            "advisory_url": advisory.get("url"),
            "graphics_url": (row.get("forecastGraphics") or {}).get("url") if isinstance(row.get("forecastGraphics"), dict) else None,
        }

        def mine(feature: dict[str, Any]) -> bool:
            properties = feature.get("properties") or {}
            return str(properties.get("binnumber") or "").upper() == bin_number

        points = sorted((feature for feature in layers.get("points", []) if mine(feature) and feature.get("geometry")),
                        key=lambda feature: _number((feature.get("properties") or {}).get("tau")) or 0)
        map_advisories = {_advisory((feature.get("properties") or {}).get("advisnum"))
                          for name in ("points", "track", "cone") for feature in layers.get(name, []) if mine(feature)}
        map_advisories.discard("")
        storm["map_advisory"] = ", ".join(sorted(map_advisories)) or None
        storm["map_matches"] = bool(map_advisories) and map_advisories == {number}
        forecast = []
        for feature in points:
            properties = feature["properties"]
            coordinates = (feature["geometry"] or {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            knots = _number(properties.get("maxwind"))
            forecast.append({"hours": int(_number(properties.get("tau")) or 0), "valid": _valid(properties.get("validtime"), issued),
                             "latitude": round(float(coordinates[1]), 2), "longitude": round(float(coordinates[0]), 2),
                             "wind_kt": knots, "gust_kt": _number(properties.get("gust")), "category": category(knots),
                             "kind": str(properties.get("tcdvlp") or "").lower() or None})
        storm["forecast"] = forecast
        if forecast:
            peak = max(forecast, key=lambda item: item["wind_kt"] or 0)
            storm["peak"] = {"wind_kt": peak["wind_kt"], "hours": peak["hours"], "category": peak["category"], "kind": peak["kind"]}
        cone = next((feature.get("geometry") for feature in layers.get("cone", []) if mine(feature) and feature.get("geometry")), None)
        storm["has_cone"] = cone is not None
        warnings = [feature for feature in layers.get("warnings", []) if mine(feature) and feature.get("geometry")]
        storm["warnings"] = sorted({WARNINGS.get(str((feature.get("properties") or {}).get("tcww") or "").upper(),
                                                 str((feature.get("properties") or {}).get("tcww") or "watch or warning"))
                                    for feature in warnings})
        if where and latitude is not None and longitude is not None:
            lat, lon = where["latitude"], where["longitude"]
            storm["distance_km"] = round(distance_km(lat, lon, latitude, longitude))
            storm["direction"] = compass(bearing_deg(lat, lon, latitude, longitude))
            if forecast:
                closest = min(forecast, key=lambda item: distance_km(lat, lon, item["latitude"], item["longitude"]))
                storm["closest"] = {**closest, "distance_km": round(distance_km(lat, lon, closest["latitude"], closest["longitude"]))}
            if cone is not None:
                storm["in_cone"] = _contains(cone, lat, lon)
                edge = _edge(cone, lat, lon)
                storm["cone_edge_km"] = round(edge) if edge is not None and edge < NEAR_KM and not storm["in_cone"] else None
            if warnings:
                nearest = min((distance_km(lat, lon, a, b) for feature in warnings for a, b in _vertices(feature["geometry"])),
                              default=None)
                storm["nearest_warning_km"] = round(nearest) if nearest is not None else None
        return storm

    # ----------------------------------------------------------------- the place
    @staticmethod
    def _wind_chance(features: list[dict[str, Any]], where: dict[str, Any]) -> dict[str, Any]:
        """The 5-day chance of tropical-storm-force winds at the place: the band NOAA's map puts it in."""
        best, best_low = None, -1.0
        for feature in features:
            band = str((feature.get("properties") or {}).get("percentage") or "").strip()
            if not band or not feature.get("geometry") or not _contains(feature["geometry"], where["latitude"], where["longitude"]):
                continue
            low = _number(re.sub(r"[^\d.].*$", "", band.lstrip("<>"))) or 0.0
            if band.startswith("<"):
                low = 0.0
            if low > best_low:
                best, best_low = band, low
        return {"band": best or "below 5%", "mapped": best is not None,
                "meaning": "Chance of sustained winds of at least 39 mph (34 kt) at the place within the next 5 days, "
                           "from every active storm together."}

    @staticmethod
    def _outlook(features: list[dict[str, Any]], where: dict[str, Any] | None) -> list[dict[str, Any]]:
        areas = []
        for feature in features:
            properties = feature.get("properties") or {}
            item = {"basin": properties.get("basin"), "chance_2_days": properties.get("prob2day"),
                    "chance_7_days": properties.get("prob7day"), "risk_7_days": properties.get("risk7day")}
            geometry = feature.get("geometry")
            if where and geometry:
                item["covers_place"] = _contains(geometry, where["latitude"], where["longitude"])
                points = _vertices(geometry)
                if points:
                    item["distance_km"] = 0 if item["covers_place"] else round(min(
                        distance_km(where["latitude"], where["longitude"], a, b) for a, b in points))
            areas.append(item)
        areas.sort(key=lambda item: -(_number(str(item.get("chance_7_days") or "0").rstrip("%")) or 0))
        return areas


CYCLONE_TOOLS = ("tropical_cyclones",)


def register_cyclone_tools(registry: Any, cyclones: TropicalCyclones) -> None:
    registry.register("tropical_cyclones", "Official hurricane and tropical storm forecasts (NHC/CPHC; Atlantic and eastern/central "
                      "Pacific): each storm's strength, motion, 5-day forecast track and cone, watches and warnings, areas being "
                      "watched for formation, and for a place: whether it is in a cone, how close the storm comes, and the chance "
                      "of tropical-storm-force winds there.",
                      {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                                        "name": {"type": "string", "maxLength": 40}},
                       "required": [], "additionalProperties": False},
                      "low", "public_web_read", cyclones.forecast)


def _wind(knots: float | None) -> str:
    return f"{knots:.0f} kt ({round(knots * 1.15078)} mph)" if knots is not None else "wind not given"


def report_lines(value: dict[str, Any]) -> list[str]:
    place = value.get("place")
    lines: list[str] = []
    if not value["storms"]:
        lines.append("No active tropical cyclones in the NHC/CPHC areas right now.")
    else:
        lines.append(f"{value['count']} active tropical cyclone{'s' if value['count'] != 1 else ''} (NHC/CPHC):")
    for storm in value["storms"][:6]:
        head = (f"• {storm['kind'].title()} {storm['name']}" + (f" ({storm['category']})" if storm.get("category") else "")
                + f": {_wind(storm.get('wind_kt'))}")
        if storm.get("pressure_mb"):
            head += f", {storm['pressure_mb']:.0f} mb"
        if storm.get("latitude") is not None:
            head += f", at {abs(storm['latitude']):.1f}°{'N' if storm['latitude'] >= 0 else 'S'} {abs(storm['longitude']):.1f}°{'W' if storm['longitude'] < 0 else 'E'}"
        if storm.get("moving") and storm.get("speed_mph"):
            head += f", moving {storm['moving']} at {storm['speed_mph']:.0f} mph"
        lines.append(head + f" (advisory {storm.get('advisory') or '?'})")
        if storm.get("peak") and storm.get("forecast"):
            last = storm["forecast"][-1]
            lines.append(f"  Forecast: peaks at {_wind(storm['peak']['wind_kt'])}"
                         + (f" ({storm['peak']['category']})" if storm["peak"].get("category") else "")
                         + f" in {storm['peak']['hours']} h; at {last['hours']} h near {abs(last['latitude']):.1f}°"
                         f"{'N' if last['latitude'] >= 0 else 'S'} {abs(last['longitude']):.1f}°{'W' if last['longitude'] < 0 else 'E'}.")
        if storm.get("warnings"):
            lines.append("  In effect: " + ", ".join(storm["warnings"]) + ".")
        if place and storm.get("distance_km") is not None:
            parts = [f"{storm['distance_km']:,} km {storm['direction']} of {place} now"]
            if storm.get("closest"):
                parts.append(f"closest forecast point {storm['closest']['distance_km']:,} km away in {storm['closest']['hours']} h")
            if storm.get("in_cone"):
                parts.append(f"{place} is INSIDE the forecast cone")
            elif storm.get("has_cone"):
                parts.append(f"{place} is outside the cone" + (f" ({storm['cone_edge_km']:,} km from its edge)"
                                                                 if storm.get("cone_edge_km") is not None else ""))
            if storm.get("nearest_warning_km") is not None:
                parts.append(f"nearest coastal watch/warning {storm['nearest_warning_km']:,} km away")
            lines.append("  " + "; ".join(parts) + ".")
        if storm.get("map_advisory") and not storm.get("map_matches"):
            lines.append(f"  The forecast map is from advisory {storm['map_advisory']}, the text advisory is {storm.get('advisory')}: "
                         "one has not caught up with the other yet.")
        if storm.get("advisory_url"):
            lines.append(f"  Advisory: {storm['advisory_url']}")
    if place and value.get("wind_chance"):
        lines.append(f"Chance of tropical-storm-force winds (39 mph+) at {place} in the next 5 days: {value['wind_chance']['band']}.")
    watched = [area for area in value.get("outlook") or [] if str(area.get("chance_7_days") or "0%") not in ("0%", "")]
    if watched:
        lines.append("Areas NHC is watching for new storms: " + "; ".join(
            f"{area.get('basin') or '?'} {area['chance_7_days']} in 7 days ({area.get('chance_2_days')} in 2)"
            + (" — covers " + place if area.get("covers_place") else f", {area['distance_km']:,} km away" if place and area.get("distance_km") is not None else "")
            for area in watched[:4]) + ".")
    if value.get("unavailable"):
        lines.append("Not loaded: " + ", ".join(sorted(value["unavailable"])) + ".")
    lines.append("Coverage: " + value["coverage"])
    return lines
