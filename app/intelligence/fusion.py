"""Everything around a place, combined into one picture -- and how it has changed.

Given a place (a name, coordinates, or "here") and a radius, this gathers in
parallel from every source that covers it:

  nearest cameras (ranked) · events (earthquakes, wildfires, disasters, road
  closures and incidents) · weather forecast · nearest weather stations · air
  quality · river gauges · aircraft · ships · public transit · elevation · ISS

and writes one interpretation from the evidence, saying plainly which kinds of
data do not cover the place. Each look is stored as an observation, so the next
one can say what changed against ~1 hour ago, ~yesterday, and what is usual at
this hour. Nothing here guesses: every sentence names its source.
"""

from __future__ import annotations

import logging
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.intelligence.live import LiveData, bearing, distance_km, safe
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
ALL_PARTS = ("cameras", "events", "weather", "stations", "air", "gauges", "aircraft", "vessels", "transit",
             "elevation", "iss")


def reverse_place(get_json: Callable[[str], Any], latitude: float, longitude: float) -> dict[str, Any] | None:
    """Country code and region for coordinates: from GeoNames on this computer when it is set up, otherwise from
    BigDataCloud's keyless endpoint, with a position near the owner rounded to about 11 km first."""
    from app.location_privacy import SHIELD, PlaceIndex
    from app.tools.devices import REVERSE_GEOCODER

    places = PlaceIndex()
    if places.available():
        found = places.nearest(latitude, longitude)
        if found:
            return {"country_code": found["country_code"], "region": found["region"], "city": found["city"]}
    if SHIELD._near(latitude, longitude):
        latitude, longitude = round(latitude, 1), round(longitude, 1)
    data = get_json(REVERSE_GEOCODER.format(lat=latitude, lon=longitude))
    if not isinstance(data, dict) or not data.get("countryCode"):
        return None
    return {"country_code": data.get("countryCode"), "region": data.get("principalSubdivision"),
            "city": data.get("locality") or data.get("city")}


def place_key(latitude: float, longitude: float) -> str:
    """About 1 km cells: looks at the same spot share a history."""
    return f"{latitude:.2f},{longitude:.2f}"


def camera_score(row: dict[str, Any], reliability: dict[str, float], radius_km: float) -> tuple[float, list[str]]:
    """Rank by distance, media, network reliability, freshness and authority; return the reasons too."""
    from app.intelligence.camera_chat import playback_mode
    from app.intelligence.discovery import is_official

    details = row.get("details") or {}
    distance = float(row.get("distance_km") or 0.0)
    reasons = [f"{distance:.1f} km away"]
    score = 0.45 * max(0.0, 1 - distance / max(radius_km, 1))
    mode = playback_mode(details)
    media = {"stream": 1.0, "clip": 0.7, "still": 0.6}.get(mode, 0.2 if details.get("page_url") else 0.0)
    score += 0.2 * media
    reasons.append({"stream": "live video", "clip": "recorded clip", "still": "refreshing still"}.get(mode, "publisher viewer"))
    rate = reliability.get(row.get("feed", ""))
    if rate is not None:
        score += 0.15 * rate
        reasons.append(f"network {rate:.0%} reliable")
    else:
        score += 0.15 * 0.7
    modified = str(details.get("catalog_modified_at") or "")
    if modified:
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(modified)).total_seconds()
            score += 0.1 * (1.0 if age < 3600 else 0.5 if age < 86400 else 0.2)
        except (ValueError, TypeError):
            pass
    else:
        score += 0.05
    from app.intelligence.feeds import BY_KEY

    feed = BY_KEY.get(row.get("feed", ""))
    if feed is not None and (is_official(feed.url) or is_official(feed.documentation)):
        score += 0.1
        reasons.append("official publisher")
    return round(score, 3), reasons


class Fusion:
    def __init__(self, manager: Any, live: LiveData, *, my_location: Callable[[], dict[str, Any] | None] | None = None,
                 reverse: Callable[[float, float], dict[str, Any] | None] | None = None) -> None:
        self.manager, self.live = manager, live
        self.my_location, self.reverse = my_location, reverse
        self.last_place: dict[str, Any] | None = None

    # ------------------------------------------------------------- place
    def resolve(self, place: str = "", latitude: Any = None, longitude: Any = None) -> dict[str, Any]:
        if latitude is not None and longitude is not None:
            lat, lon = float(latitude), float(longitude)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Coordinates must be latitude -90..90 and longitude -180..180.")
            found = {"name": place or f"{lat:.4f}, {lon:.4f}", "latitude": lat, "longitude": lon}
        elif not place or place.strip().lower() in {"here", "me", "my location", "where i am", "this place"}:
            if self.last_place and place.strip().lower() in {"here", "this place"}:
                found = dict(self.last_place)
            else:
                location = self.my_location() if self.my_location else None
                if not location or location.get("latitude") is None:
                    raise ValueError("I could not read this computer's location; name a place or give coordinates.")
                found = {"name": (location.get("place") or {}).get("label") or "your location",
                         "latitude": float(location["latitude"]), "longitude": float(location["longitude"])}
        else:
            match = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*", place)
            if match:
                return self.resolve(place, float(match[1]), float(match[2]))
            geocoded = self.live.geocode(place)
            if not geocoded:
                raise ValueError(f"I couldn't find a place called {place!r}.")
            found = {"name": ", ".join(part for part in (geocoded["name"], geocoded.get("region"), geocoded.get("country")) if part),
                     "latitude": geocoded["latitude"], "longitude": geocoded["longitude"],
                     "country_code": geocoded.get("country_code"), "region": geocoded.get("region")}
        if "country_code" not in found and self.reverse is not None:
            details = safe(lambda: self.reverse(found["latitude"], found["longitude"]), "reverse geocoding")[0] or {}
            found.update({"country_code": details.get("country_code"), "region": details.get("region")})
            # A clicked spot is named by where it is, not by its coordinates, when that is known.
            named = ", ".join(part for part in (details.get("city"), details.get("region")) if part)
            if named and not place:
                found["name"] = f"near {named}"
        region = str(found.get("region") or "").lower()
        found["state_code"] = US_STATES.get(region, "") if (found.get("country_code") or "").upper() == "US" else ""
        return found

    # ------------------------------------------------------------- gather
    def around(self, place: str = "", latitude: Any = None, longitude: Any = None, radius_km: float = 16.0,
               include: list[str] | None = None, record: bool = True) -> dict[str, Any]:
        radius = max(0.5, min(float(radius_km or 16.0), 500.0))
        where = self.resolve(place, latitude, longitude)
        lat, lon = where["latitude"], where["longitude"]
        parts = [part for part in (include or ALL_PARTS) if part in ALL_PARTS]
        us = (where.get("country_code") or "").upper() == "US"
        jobs: dict[str, Callable[[], Any]] = {
            "cameras": lambda: self._cameras(lat, lon, radius),
            "events": lambda: self._events(lat, lon, radius),
            "weather": lambda: self.live.weather(lat, lon),
            "stations": lambda: self.live.stations(lat, lon, where.get("state_code", ""), max(radius, 40)) if us else [],
            "air": lambda: self.live.air(lat, lon, min(radius, 30)),
            "gauges": lambda: self.live.gauges(lat, lon, min(radius, 40)) if us else [],
            "aircraft": lambda: self.live.aircraft(lat, lon, min(radius, 100)),
            "vessels": lambda: self.live.vessels(lat, lon, radius),
            "transit": lambda: self.live.transit(lat, lon, min(radius, 25)),
            "elevation": lambda: self.live.elevation(lat, lon) if us else None,
            "iss": lambda: self._iss(lat, lon),
        }
        results: dict[str, Any] = {}
        gaps: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {part: pool.submit(safe, jobs[part], part) for part in parts}
            for part, future in futures.items():
                value, problem = future.result()
                results[part] = value
                if problem:
                    gaps[part] = problem
        self.last_place = {key: where[key] for key in ("name", "latitude", "longitude", "country_code", "region")
                           if key in where}
        metrics = self._metrics(results)
        moment = datetime.now(UTC)
        observation_key = f'{place_key(lat, lon)}:{radius:g}km'
        changes = self.compare(observation_key, metrics, moment)
        if record:
            try:
                self.manager.store.save_observation(observation_key, moment.isoformat(timespec="seconds"), metrics)
            except Exception:
                log_swallowed(_log, "saving a place observation")
        coverage_notes = self._coverage_notes(results, parts, where, gaps)
        return {"fusion": True, "place": where, "radius_km": radius, "observed_at": moment.isoformat(timespec="seconds"),
                **{part: results.get(part) for part in parts}, "metrics": metrics, "changes": changes,
                "not_covered": coverage_notes, "unavailable": gaps,
                "interpretation": self.interpret(where, radius, results, changes, coverage_notes),
                "basis": ("Combined from public providers, each named with its data; nothing is inferred beyond them. "
                          "Model forecasts are not measurements; citizen sensors are uncalibrated.")}

    def _cameras(self, lat: float, lon: float, radius: float) -> list[dict[str, Any]]:
        found = self.manager.cameras(near=[lat, lon], radius_km=min(radius, 200), available_only=True, limit=500)
        reliability = {}
        try:
            for feed, entry in self.manager.store.health_summary().items():
                if entry["attempts"]:
                    reliability[feed] = entry["successes"] / entry["attempts"]
        except Exception:
            log_swallowed(_log, "reading feed reliability for camera ranking")
        ranked = []
        for row in found["cameras"]:
            score, reasons = camera_score(row, reliability, radius)
            details = row.get("details") or {}
            ranked.append({"id": row["id"], "title": row["title"], "distance_km": row.get("distance_km"),
                           "direction": bearing(lat, lon, row["latitude"], row["longitude"]),
                           "provider": details.get("provider"), "score": score, "why": ", ".join(reasons),
                           "image": bool(details.get("image_url")), "stream": "live video" in reasons})
        return sorted(ranked, key=lambda item: -item["score"])[:10]

    def _events(self, lat: float, lon: float, radius: float) -> list[dict[str, Any]]:
        _sources, rows = self.manager._matching("", "", None, True)
        found = []
        for row in rows:
            if row.get("category") == "cameras" or row.get("latitude") is None:
                continue
            distance = distance_km(lat, lon, row["latitude"], row["longitude"])
            if distance <= radius:
                found.append({"id": row["id"], "title": row.get("headline") or row["title"], "category": row.get("category"),
                              "kind": row.get("kind"), "severity": row.get("severity"),
                              "severity_rank": row.get("severity_rank", 0), "distance_km": round(distance, 1),
                              "direction": bearing(lat, lon, row["latitude"], row["longitude"]),
                              "observed_at": row.get("observed_at"), "provider": row.get("provider")})
        return sorted(found, key=lambda item: (-item["severity_rank"], item["distance_km"]))[:25]

    def _iss(self, lat: float, lon: float) -> dict[str, Any]:
        position = self.live.iss()
        position["distance_km"] = round(distance_km(lat, lon, position["latitude"], position["longitude"]))
        position["overhead"] = position["distance_km"] < 2000
        return position

    # ------------------------------------------------------------- time
    @staticmethod
    def _metrics(results: dict[str, Any]) -> dict[str, Any]:
        weather = results.get("weather") or {}
        events = results.get("events") or []
        stations = results.get("stations") or []
        air = results.get("air") or {}
        metrics = {
            "temperature_c": weather.get("temperature_c"), "wind_ms": weather.get("wind_speed_ms"),
            "precipitation_next_hour_mm": weather.get("precipitation_next_hour_mm"),
            "station_temperature_f": stations[0].get("temperature_f") if stations else None,
            "pm2_5": air.get("pm2_5_median"),
            "aircraft": len(results["aircraft"]) if isinstance(results.get("aircraft"), list) else None,
            "vessels": len(results["vessels"]) if isinstance(results.get("vessels"), list) else None,
            "transit_vehicles": len(results["transit"]) if isinstance(results.get("transit"), list) else None,
            "events": len(events) if isinstance(results.get("events"), list) else None,
            "road_closures": (sum(1 for item in events if item.get("kind") in ("closure", "lane closure"))
                              if isinstance(results.get('events'), list) else None),
            "cameras": len(results["cameras"]) if isinstance(results.get("cameras"), list) else None,
            "gauge_height_ft": next((site.get("gauge_height_ft") for site in results.get("gauges") or []
                                     if site.get("gauge_height_ft") is not None), None),
        }
        return {key: value for key, value in metrics.items() if value is not None}

    def compare(self, place: str, now_metrics: dict[str, Any], moment: datetime | None = None) -> list[dict[str, Any]]:
        """Now against ~1 hour ago, ~yesterday and usual at this hour, from stored observations."""
        moment = moment or datetime.now(UTC)
        try:
            history = self.manager.store.observations(place)
        except Exception:
            return []
        dated = []
        for item in history:
            try:
                dated.append((datetime.fromisoformat(item["at"]), item))
            except (KeyError, ValueError):
                continue

        def nearest(target: timedelta, tolerance: timedelta) -> dict[str, Any] | None:
            wanted = moment - target
            candidates = [(abs(stamp - wanted), item) for stamp, item in dated if abs(stamp - wanted) <= tolerance]
            return min(candidates, key=lambda pair: pair[0])[1] if candidates else None

        hour = nearest(timedelta(hours=1), timedelta(minutes=50))
        recent = nearest(timedelta(minutes=10), timedelta(minutes=8)) if hour is None else None
        yesterday = nearest(timedelta(days=1), timedelta(hours=3))
        same_hour = [item for stamp, item in dated if stamp.hour == moment.hour and moment - stamp > timedelta(hours=20)]
        found = []
        labels = {"temperature_c": ("temperature", "°C"), "precipitation_next_hour_mm": ("rain expected next hour", " mm"),
                  "wind_ms": ("wind", " m/s"), "pm2_5": ("PM2.5", " µg/m³"), "aircraft": ("aircraft nearby", ""),
                  "vessels": ("ships nearby", ""), "transit_vehicles": ("transit vehicles nearby", ""),
                  "events": ("events nearby", ""), "road_closures": ("road closures", ""),
                  "gauge_height_ft": ("river gauge height", " ft"), "station_temperature_f": ("station temperature", " °F")}
        for metric, now_value in now_metrics.items():
            if metric not in labels or not isinstance(now_value, (int, float)):
                continue
            entry: dict[str, Any] = {"metric": metric, "label": labels[metric][0], "unit": labels[metric][1], "now": now_value}
            for name, then in (("an_hour_ago", hour), ("ten_minutes_ago", recent), ("yesterday", yesterday)):
                value = (then or {}).get(metric)
                if isinstance(value, (int, float)):
                    entry[name] = value
            usual = [item.get(metric) for item in same_hour if isinstance(item.get(metric), (int, float))]
            if len(usual) >= 3:
                entry["usual"] = round(statistics.median(usual), 2)
                spread = statistics.pstdev(usual) or 0.0
                entry["unusual"] = bool(spread and abs(now_value - entry["usual"]) > 2 * spread)
            if len(entry) > 4:
                found.append(entry)
        return found

    # ------------------------------------------------------------- words
    @staticmethod
    def _coverage_notes(results: dict[str, Any], parts: list[str], where: dict[str, Any], gaps: dict[str, str]) -> list[str]:
        notes = []
        us = (where.get("country_code") or "").upper() == "US"
        if "vessels" in parts and results.get("vessels") == [] and "vessels" not in gaps:
            notes.append("Ships: the only keyless AIS feed covers the Baltic; none here.")
        if "transit" in parts and results.get("transit") == [] and "transit" not in gaps:
            notes.append("Transit: no keyless live vehicle feed covers this place yet (connected: Boston, Finland).")
        if not us and "stations" in parts:
            notes.append("Weather stations and river gauges: US networks only so far.")
        if "cameras" in parts and results.get("cameras") == []:
            notes.append("Cameras: no connected public camera within this radius.")
        if gaps:
            notes.append("Unavailable right now (provider error or rate limit): " + ", ".join(sorted(gaps)) + ".")
        return notes

    @staticmethod
    def interpret(where: dict[str, Any], radius: float, results: dict[str, Any], changes: list[dict[str, Any]],
                  notes: list[str]) -> str:
        lines = [f"Around {where['name']} (within {radius:g} km):"]
        events = results.get("events") or []
        serious = [item for item in events if item.get("severity_rank", 0) >= 2]
        if events:
            kinds: dict[str, int] = {}
            for item in events:
                kinds[item.get("kind") or item.get("category") or "event"] = kinds.get(item.get("kind") or item.get("category") or "event", 0) + 1
            lines.append(f"{len(events)} reported event(s): " + ", ".join(f"{count} {kind}" for kind, count in
                                                                         sorted(kinds.items(), key=lambda pair: -pair[1])[:5]) + ".")
            for item in serious[:3]:
                lines.append(f"  {item['severity']}: {item['title']} — {item['distance_km']} km {item['direction']} ({item.get('provider') or 'publisher'}).")
        elif "events" in results:  # only when events were read; a weather-only look says nothing about them
            lines.append("No reported earthquakes, fires, disasters or road events within the radius.")
        cameras = results.get("cameras") or []
        if cameras:
            best = cameras[0]
            lines.append(f"{len(cameras)} public camera(s) nearby; best view: {best['title']} ({best['why']}).")
        weather = results.get("weather")
        if weather:
            rain = weather.get("precipitation_next_hour_mm")
            lines.append(f"Weather: {weather.get('temperature_c')}°C, wind {weather.get('wind_speed_ms')} m/s"
                         + (f", {rain} mm rain expected in the next hour" if rain else ", no rain expected in the next hour")
                         + " (MET Norway forecast).")
            for day in weather.get("outlook") or []:
                rain = (f"{day['rain_mm']} mm rain over about {day['rain_hours']} h" if day["rain_mm"]
                        else "no rain forecast")
                wind = f", wind up to {day['max_wind_ms']} m/s" if day.get("max_wind_ms") is not None else ""
                lines.append(f"{day['day'].capitalize()}: {day['low_c']}–{day['high_c']}°C, {rain}{wind}.")
        stations = results.get("stations") or []
        if stations:
            station = stations[0]
            lines.append(f"Nearest station {station['name']} ({station['distance_km']} km) reported {station.get('temperature_f')}°F "
                         f"at {str(station.get('observed_at') or '')[11:16]} UTC.")
        air = results.get("air") or {}
        if air.get("sensors"):
            lines.append(f"Air: PM2.5 median {air.get('pm2_5_median')} µg/m³ from {air['sensors']} citizen sensor(s).")
        aircraft = results.get("aircraft")
        if isinstance(aircraft, list):
            airborne = [plane for plane in aircraft if not plane.get("on_ground")]
            emergency = [plane for plane in aircraft if plane.get("emergency")]
            lines.append(f"{len(airborne)} aircraft airborne nearby" + (f", {len(aircraft) - len(airborne)} on the ground" if len(aircraft) > len(airborne) else "")
                         + (f"; EMERGENCY squawk from {', '.join(plane['flight'] for plane in emergency)}" if emergency else "") + ".")
        if results.get("vessels"):
            lines.append(f"{len(results['vessels'])} ship(s) within the radius (Digitraffic AIS).")
        if results.get("transit"):
            lines.append(f"{len(results['transit'])} transit vehicle(s) moving nearby.")
        gauges = results.get("gauges") or []
        if gauges:
            gauge = gauges[0]
            lines.append(f"River: {gauge['site']} at {gauge.get('gauge_height_ft')} ft (USGS).")
        iss = results.get("iss")
        if iss and iss.get("overhead"):
            lines.append(f"The ISS is about {iss['distance_km']} km away horizontally, at {iss['altitude_km']:.0f} km altitude.")
        for change in changes[:5]:
            compared = [f"{change[name]}{change['unit']} {label}" for name, label in
                        (("ten_minutes_ago", "10 minutes ago"), ("an_hour_ago", "an hour ago"), ("yesterday", "yesterday"),
                         ("usual", "usual at this hour")) if name in change]
            lines.append(f"Change: {change['label']} now {change['now']}{change['unit']} vs " + ", ".join(compared)
                         + (" — unusual" if change.get("unusual") else "") + ".")
        lines.extend(notes)
        lines.extend(f'{part}: {reason}' for part, reason in (results.get('_unavailable') or {}).items())
        return "\n".join(lines)


# ------------------------------------------------------------------ routing
def _radius(n: int) -> str:
    return (rf"(?:within\s+(?P<radius{n}>\d{{1,3}}(?:\.\d+)?)\s*(?P<unit{n}>miles?|mi|km|kilometres?|kilometers?)"
            r"\s+(?:of\s+)?)?")
_AROUND = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could)\s+(?:you|u)\s+)?(?:"
    r"(?:show|tell\s+me|give\s+me|what(?:'s|\s+is)|whats)\s+(?:me\s+)?(?:everything|all)(?:\s+(?:that'?s|that\s+is))?"
    r"(?:\s+(?:happening|going\s+on))?\s+" + _radius(1) + r"(?:(?:around|near|in|at)\s+|(?<=of\s))"
    r"|what(?:'s|\s+is)\s+(?:happening|going\s+on)\s+" + _radius(2) + r"(?:around|near|in|at)\s+"
    r"|(?:show|find|list)\s+(?:me\s+)?(?P<parts>(?:(?:aircraft|planes|flights|ships|vessels|boats|transit|trains|buses|"
    r"traffic|weather|cameras|events|air\s+quality|sensors)(?:\s*,\s*|\s+and\s+|\s+))+)" + _radius(3) + r"(?:around|near|over|in|at)\s+"
    r")(?P<place>.+?)\s*[?.!]*$")
_CHANGED = re.compile(r"(?i)^what\s+(?:has\s+)?changed\s+(?:here|around\s+(?P<place>.+?)|in\s+(?P<place2>.+?))"
                      r"(?:\s+(?:during|in|over)\s+the\s+last\s+(?:hour|day|\d+\s+(?:minutes|hours)))?\s*[?.!]*$")
_BEST = re.compile(r"(?i)^(?:which|what)\s+(?:public\s+)?camera\s+(?:has|gives|shows)\s+the\s+best\s+view\s+of\s+(?P<place>.+?)\s*[?.!]*$")
_SOURCES = re.compile(r"(?i)^(?:what|which)\s+(?:data\s+)?sources\s+(?:do\s+you\s+have|can\s+you\s+(?:see|use))"
                      r"(?:\s+and\s+why(?:\s+are\s+they)?\s+(?:helpful|useful))?\s*[?.!]*$"
                      r"|^(?:why\s+are\s+(?:these|your|the)\s+(?:data\s+)?sources\s+(?:helpful|useful))\s*[?.!]*$"
                      r"|^(?:show|list)\s+(?:me\s+)?(?:all\s+)?(?:your\s+|the\s+)?(?:data\s+)?(?:sources|source\s+types)\s*[?.!]*$")
_PART_WORDS = {"aircraft": "aircraft", "planes": "aircraft", "flights": "aircraft", "ships": "vessels",
               "vessels": "vessels", "boats": "vessels", "transit": "transit", "trains": "transit", "buses": "transit",
               "traffic": "events", "weather": "weather", "cameras": "cameras", "events": "events",
               "air quality": "air", "sensors": "air"}


def fusion_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if match := _SOURCES.match(value):
        return {"tool": "intelligence_sources", "arguments": {}}
    if match := _BEST.match(value):
        return {"tool": "intelligence_best_camera", "arguments": {"place": match["place"]}}
    if match := _CHANGED.match(value):
        return {"tool": "intelligence_changes", "arguments": {"place": match["place"] or match["place2"] or "here"}}
    match = _AROUND.match(value)
    if not match or not match["place"] or re.match(r"(?i)(?:the\s+)?world\b", match["place"]):
        return None  # the world as a whole is the assessment's job, not one place
    arguments: dict[str, Any] = {"place": re.sub(r"(?i)^(?:the\s+)?(?:city\s+of\s+)?", "", match["place"]).strip()}
    number = next((n for n in (1, 2, 3) if match[f"radius{n}"]), None)
    if number:
        radius = float(match[f"radius{number}"])
        unit = match[f"unit{number}"].lower()
        arguments["radius_km"] = round(radius * 1.609, 1) if unit.startswith("mi") else radius
    if match["parts"]:
        wanted = []
        for word, part in _PART_WORDS.items():
            if re.search(rf"\b{word}\b", match["parts"].lower()) and part not in wanted:
                wanted.append(part)
        if wanted == ["cameras"]:
            return None  # cameras alone are the camera search's job (it handles coordinates and radius)
        if "events" in wanted and "cameras" not in wanted:
            wanted.append("cameras")
        arguments["include"] = wanted
    return {"tool": "intelligence_around", "arguments": arguments}


def register_fusion_tools(registry: Any, fusion: Fusion, sources: Callable[[str], dict[str, Any]]) -> None:
    place = {"place": {"type": "string", "maxLength": 200}, "latitude": {"type": "number"}, "longitude": {"type": "number"}}
    registry.register(
        "intelligence_around",
        "Everything around a place, combined: ranked public cameras, events (earthquakes, wildfires, disasters, road "
        "closures and incidents), weather, stations, air quality, river gauges, aircraft, ships, transit, elevation "
        "and the ISS, with what changed since the last look and what no source covers.",
        {"type": "object", "properties": {**place, "radius_km": {"type": "number", "minimum": 0.5, "maximum": 500},
                                          "include": {"type": "array", "items": {"type": "string", "enum": list(ALL_PARTS)}}},
         "additionalProperties": False}, "low", "public_web_read", fusion.around)
    registry.register(
        "intelligence_changes",
        "What changed at a place: now against about an hour ago, yesterday and usual at this hour, from HAYAT's own "
        "stored observations of that place.",
        {"type": "object", "properties": place, "additionalProperties": False}, "low", "public_web_read",
        lambda place="here", latitude=None, longitude=None: {**fusion.around(place, latitude, longitude), "changes_only": True})
    registry.register(
        "intelligence_best_camera",
        "Which public camera has the best view of a place: ranked by distance, media (live video first), network "
        "reliability, freshness and official publisher, with the reasons.",
        {"type": "object", "properties": place, "additionalProperties": False}, "low", "public_web_read",
        lambda place="", latitude=None, longitude=None: {**fusion.around(place, latitude, longitude, 25, ["cameras"]),
                                                        "best_camera_only": True})
    registry.register(
        "intelligence_sources",
        "Every data source HAYAT can use, described the same way (provider, types, coverage, media, refresh, "
        "authentication, licence, reliability, health, last success, restrictions, confidence), and why each type helps.",
        {"type": "object", "properties": {"type": {"type": "string", "maxLength": 20}}, "additionalProperties": False},
        "low", "filesystem_read", sources)
