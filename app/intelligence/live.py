"""Live, on-demand public data around a place: aircraft, ships, transit, weather, air, water, terrain, space.

These are not catalogs to store; they are asked when a question needs them
("what is around Detroit?") and cached for a short while. Every provider here
was checked live on 2026-10-06 through HAYAT's own fetcher -- robots.txt
honoured, public addresses only, no key:

=====================  ==============================  ============  ==========================
What                   Provider                        Coverage      Licence / terms
=====================  ==============================  ============  ==========================
aircraft (ADS-B)       adsb.lol                        world         ODbL, free API
ships (AIS)            Fintraffic Digitraffic Marine   Baltic        CC BY 4.0
transit vehicles       MBTA v3 API; Digitraffic trains Boston; FI    MBTA terms; CC BY 4.0
weather forecast       MET Norway Locationforecast     world         CC BY 4.0, identify yourself
weather stations       Iowa Environmental Mesonet      US (ASOS)     public, crawl-delay honoured
air quality sensors    Sensor.Community                world         ODbL
river gauges           USGS Water Services             US            public domain
elevation              USGS EPQS                       US            public domain
place names            Open-Meteo geocoding            world         CC BY 4.0
ISS position           CelesTrak elements + SGP4       world         public
=====================  ==============================  ============  ==========================

Refused by their own robots.txt, so not used: Open-Meteo forecast/air/elevation
(api.open-meteo.com disallows all), RainViewer, api.weather.gov, Overpass,
wheretheiss.at. airplanes.live answers 403.
"""

from __future__ import annotations


import logging
import math
import statistics
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def daily_outlook(series: list[dict[str, Any]], longitude: float, days: int = 3) -> list[dict[str, Any]]:
    """Low/high, rain and wind per local day from a MET Norway timeseries.

    "Will it rain tomorrow" needs the day, not the next hour. MET times are UTC;
    the local day is approximated from the longitude (15 degrees an hour), so
    a day's edges can be off by an hour where a time zone is irregular.
    """
    offset = timedelta(hours=round(float(longitude) / 15))
    by_day: dict[str, dict[str, Any]] = {}
    for entry in series:
        try:
            moment = datetime.fromisoformat(str(entry.get("time")).replace("Z", "+00:00")) + offset
        except ValueError:
            continue
        data = entry.get("data") or {}
        details = (data.get("instant") or {}).get("details") or {}
        day = by_day.setdefault(moment.date().isoformat(), {"temperatures": [], "winds": [], "rain_mm": 0.0, "rain_hours": 0})
        if len(by_day) > days:
            by_day.pop(moment.date().isoformat())
            break
        if isinstance(details.get("air_temperature"), (int, float)):
            day["temperatures"].append(details["air_temperature"])
        if isinstance(details.get("wind_speed"), (int, float)):
            day["winds"].append(details["wind_speed"])
        # Hourly steps carry next_1_hours; later six-hour steps only next_6_hours.
        period = data.get("next_1_hours") or data.get("next_6_hours") or {}
        amount = (period.get("details") or {}).get("precipitation_amount")
        if isinstance(amount, (int, float)) and amount > 0:
            day["rain_mm"] += amount
            day["rain_hours"] += 1 if data.get("next_1_hours") else 6
    first = min(by_day) if by_day else ""
    outlook = []
    for date, day in sorted(by_day.items()):
        if not day["temperatures"]:
            continue
        label = {0: "today", 1: "tomorrow"}.get((datetime.fromisoformat(date) - datetime.fromisoformat(first)).days, date)
        outlook.append({"day": label, "date": date, "low_c": min(day["temperatures"]), "high_c": max(day["temperatures"]),
                        "rain_mm": round(day["rain_mm"], 1), "rain_hours": day["rain_hours"],
                        "max_wind_ms": max(day["winds"]) if day["winds"] else None})
    return outlook

#: One row per provider: the metadata every source carries (see catalog.py).
PROVIDERS: dict[str, dict[str, Any]] = {
    "adsb": {"provider": "adsb.lol", "types": ["AIRCRAFT", "LOCATION"], "coverage": "World (where volunteers receive ADS-B)",
             "url": "https://api.adsb.lol/", "license": "ODbL; free API", "refresh_seconds": 15, "per_minute": 6,
             "restrictions": "Aircraft that do not broadcast ADS-B, or are filtered, are absent."},
    "adsb_global": {"provider": "adsb.lol (worldwide military, emergency squawks, lookups)", "types": ["AIRCRAFT", "LOCATION"],
                    "coverage": "World (where volunteers receive ADS-B)", "url": "https://api.adsb.lol/v2/mil",
                    "license": "ODbL; free API", "refresh_seconds": 30, "per_minute": 8,
                    "restrictions": "Only aircraft broadcasting ADS-B; many military aircraft do not, and are absent."},
    "adsb_route": {"provider": "adsb.lol route database", "types": ["AIRCRAFT"], "coverage": "World",
                   "url": "https://api.adsb.lol/api/0/route/", "license": "ODbL; free API", "refresh_seconds": 21600,
                   "per_minute": 10, "restrictions": "Usual route for a callsign, not a filed flight plan."},
    "ais": {"provider": "Fintraffic Digitraffic Marine", "types": ["VESSEL", "LOCATION"], "coverage": "Baltic Sea and Finnish waters",
            "url": "https://meri.digitraffic.fi/api/ais/v1/locations", "license": "CC BY 4.0", "refresh_seconds": 60,
            "per_minute": 2, "restrictions": "Only ships received by Finnish AIS stations; no global keyless AIS exists."},
    "mbta": {"provider": "MBTA (Massachusetts Bay Transportation Authority)", "types": ["TRANSIT", "LOCATION"],
             "coverage": "Boston area", "url": "https://api-v3.mbta.com/vehicles", "license": "MBTA developer terms",
             "refresh_seconds": 30, "per_minute": 4, "restrictions": "Keyless use is rate-limited by MBTA."},
    "fi_trains": {"provider": "Fintraffic Digitraffic Rail", "types": ["TRANSIT", "LOCATION"], "coverage": "Finland",
                  "url": "https://rata.digitraffic.fi/api/v1/train-locations/latest/", "license": "CC BY 4.0",
                  "refresh_seconds": 30, "per_minute": 4, "restrictions": ""},
    "met": {"provider": "MET Norway Locationforecast", "types": ["WEATHER"], "coverage": "World",
            "url": "https://api.met.no/weatherapi/locationforecast/2.0/", "license": "CC BY 4.0 (MET Norway)",
            "refresh_seconds": 1800, "per_minute": 10, "restrictions": "Model forecast, not a local measurement."},
    "asos": {"provider": "Iowa Environmental Mesonet (ASOS stations)", "types": ["WEATHER", "SENSOR"],
             "coverage": "United States", "url": "https://mesonet.agron.iastate.edu/api/1/currents.json",
             "license": "public; IEM crawl-delay honoured", "refresh_seconds": 600, "per_minute": 2,
             "restrictions": "Airport weather stations only."},
    "air": {"provider": "Sensor.Community", "types": ["SENSOR"], "coverage": "World (citizen sensors, densest in Europe)",
            "url": "https://data.sensor.community/", "license": "ODbL", "refresh_seconds": 300, "per_minute": 4,
            "restrictions": "Low-cost citizen sensors; uncalibrated readings."},
    "gauges": {"provider": "USGS Water Services", "types": ["SENSOR", "EVENT"], "coverage": "United States",
               "url": "https://waterservices.usgs.gov/nwis/iv/", "license": "public domain", "refresh_seconds": 900,
               "per_minute": 4, "restrictions": "River and stream gauges only; flood stage is not included."},
    "elevation": {"provider": "USGS Elevation Point Query Service", "types": ["MAP"], "coverage": "United States",
                  "url": "https://epqs.nationalmap.gov/v1/json", "license": "public domain", "refresh_seconds": 86400,
                  "per_minute": 10, "restrictions": ""},
    "geocode": {"provider": "Open-Meteo geocoding (GeoNames)", "types": ["MAP", "LOCATION"], "coverage": "World",
                "url": "https://geocoding-api.open-meteo.com/v1/search", "license": "CC BY 4.0",
                "refresh_seconds": 86400, "per_minute": 20, "restrictions": "Place names, not street addresses."},
    "iss": {"provider": "CelesTrak orbital elements + SGP4", "types": ["SATELLITE", "LOCATION"], "coverage": "World",
            "url": "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=json", "license": "public",
            "refresh_seconds": 21600, "per_minute": 2,
            "restrictions": "Position computed from published elements; accurate to a few kilometres."},
}

# Community receivers and republished geographic data remain community sources.
for _provider in ('ais', 'mbta', 'fi_trains', 'met', 'gauges', 'elevation'):
    PROVIDERS[_provider]['official'] = True


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    y = math.sin(math.radians(lon2 - lon1)) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(math.radians(lon2 - lon1)))
    degrees = (math.degrees(math.atan2(y, x)) + 360) % 360
    return ("N", "NE", "E", "SE", "S", "SW", "W", "NW")[int((degrees + 22.5) // 45) % 8]


class RateLimited(RuntimeError):
    pass


class LiveData:
    """On-demand providers with a per-provider rate limit and short cache. `get_json` is injectable for tests."""

    def __init__(self, get_json: Callable[[str], Any], clock: Callable[[], float] = time.time,
                 recorder: Any = None) -> None:
        self.get_json, self.clock, self.recorder = get_json, clock, recorder
        self._calls: dict[str, list[float]] = {}
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.health: dict[str, dict[str, Any]] = {}

    def _fetch(self, provider: str, url: str) -> Any:
        meta = PROVIDERS[provider]
        now = self.clock()
        with self._lock:
            cached = self._cache.get(url)
            if cached and now - cached[0] < meta["refresh_seconds"]:
                return cached[1]
            calls = [stamp for stamp in self._calls.get(provider, []) if now - stamp < 60]
            if len(calls) >= meta["per_minute"]:
                raise RateLimited(f"{meta['provider']}: rate limit of {meta['per_minute']} requests a minute reached")
            calls.append(now)
            self._calls[provider] = calls
        try:
            value = self.get_json(url)
        except Exception as exc:
            with self._lock:
                self.health[provider] = {**self.health.get(provider, {}), "ok": False,
                                         "checked_at": _iso(now), "error": f"{type(exc).__name__}: {exc}"[:200]}
            raise
        with self._lock:
            self.health[provider] = {"ok": True, "checked_at": _iso(now), "last_success": _iso(now)}
            self._cache[url] = (now, value)
            if len(self._cache) > 300:
                self._cache.pop(next(iter(self._cache)))
        return value

    def record(self, kind: str, items: list[dict[str, Any]], source: str) -> None:
        """Hand every observed position to the history store, so later questions about the past can be answered."""
        if self.recorder is not None and items:
            try:
                self.recorder.record(kind, items, source)
            except Exception:
                log_swallowed(_log, f"recording {kind} positions")

    # ------------------------------------------------------------- moving things
    def aircraft(self, lat: float, lon: float, radius_km: float = 40) -> list[dict[str, Any]]:
        from app.intelligence.aviation import normalize

        nm = max(1, min(250, round(radius_km / 1.852)))
        data = self._fetch("adsb", f"https://api.adsb.lol/v2/lat/{lat:.4f}/lon/{lon:.4f}/dist/{nm}")
        found = [item for item in (normalize(plane, (lat, lon)) for plane in (data or {}).get("ac") or []) if item]
        self.record("aircraft", found, "adsb.lol")
        return sorted(found, key=lambda item: item["distance_km"])

    def vessels(self, lat: float, lon: float, radius_km: float = 40) -> list[dict[str, Any]]:
        if not (53 <= lat <= 67 and 9 <= lon <= 32):
            return []  # Digitraffic covers the Baltic; elsewhere there is no keyless AIS
        data = self._fetch("ais", PROVIDERS["ais"]["url"])
        found = []
        for feature in (data or {}).get("features") or []:
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            distance = distance_km(lat, lon, coordinates[1], coordinates[0])
            if distance <= radius_km:
                properties = feature.get("properties") or {}
                mmsi = properties.get("mmsi") or feature.get("mmsi")
                found.append({"id": mmsi, "mmsi": mmsi, "latitude": coordinates[1],
                              "longitude": coordinates[0], "speed_kn": properties.get("sog"),
                              "course": properties.get("cog"), "distance_km": round(distance, 1)})
        self.record("vessel", found, "Digitraffic AIS")
        return sorted(found, key=lambda item: item["distance_km"])[:200]

    def transit(self, lat: float, lon: float, radius_km: float = 10) -> list[dict[str, Any]]:
        found = []
        if 41.8 <= lat <= 43.0 and -71.9 <= lon <= -70.5:
            data = self._fetch("mbta", "https://api-v3.mbta.com/vehicles?fields%5Bvehicle%5D=latitude,longitude,label,"
                                       "current_status,bearing,speed&include=route")
            routes = {item["id"]: (item.get("attributes") or {}).get("short_name") or (item.get("attributes") or {}).get("long_name")
                      for item in (data or {}).get("included") or [] if item.get("type") == "route"}
            for vehicle in (data or {}).get("data") or []:
                attributes = vehicle.get("attributes") or {}
                if attributes.get("latitude") is None:
                    continue
                route = ((vehicle.get("relationships") or {}).get("route") or {}).get("data") or {}
                found.append({"id": vehicle.get("id"), "label": attributes.get("label"),
                              "route": routes.get(route.get("id"), route.get("id")), "operator": "MBTA",
                              "latitude": attributes["latitude"], "longitude": attributes["longitude"],
                              "status": attributes.get("current_status")})
        if 59.5 <= lat <= 70.5 and 19.5 <= lon <= 31.6:
            for train in self._fetch("fi_trains", PROVIDERS["fi_trains"]["url"]) or []:
                coordinates = (train.get("location") or {}).get("coordinates") or []
                if len(coordinates) >= 2:
                    found.append({"id": f"FI-{train.get('trainNumber')}", "label": f"Train {train.get('trainNumber')}",
                                  "route": "", "operator": "VR (Finland)", "latitude": coordinates[1],
                                  "longitude": coordinates[0], "speed_kmh": train.get("speed")})
        for item in found:
            item["distance_km"] = round(distance_km(lat, lon, item["latitude"], item["longitude"]), 1)
        return sorted([item for item in found if item["distance_km"] <= radius_km], key=lambda item: item["distance_km"])

    # ---------------------------------------------------------------- conditions
    def weather(self, lat: float, lon: float) -> dict[str, Any]:
        data = self._fetch("met", f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat:.3f}&lon={lon:.3f}")
        series = ((data or {}).get("properties") or {}).get("timeseries") or []
        if not series:
            raise ValueError("MET Norway returned no forecast for this place.")
        now = series[0]
        details = (((now.get("data") or {}).get("instant") or {}).get("details")) or {}
        next_hour = ((now.get("data") or {}).get("next_1_hours") or {})
        return {"time": now.get("time"), "temperature_c": details.get("air_temperature"),
                "wind_speed_ms": details.get("wind_speed"), "wind_from_deg": details.get("wind_from_direction"),
                "humidity_pct": details.get("relative_humidity"), "pressure_hpa": details.get("air_pressure_at_sea_level"),
                "cloud_pct": details.get("cloud_area_fraction"),
                "precipitation_next_hour_mm": (next_hour.get("details") or {}).get("precipitation_amount"),
                "summary": (next_hour.get("summary") or {}).get("symbol_code"),
                "outlook": daily_outlook(series, lon),
                "source": "MET Norway Locationforecast (model forecast, CC BY 4.0)"}

    def stations(self, lat: float, lon: float, state_code: str, radius_km: float = 60) -> list[dict[str, Any]]:
        if not state_code or len(state_code) != 2:
            return []
        data = self._fetch("asos", f"https://mesonet.agron.iastate.edu/api/1/currents.json?network={state_code.upper()}_ASOS")
        found = []
        for row in (data or {}).get("data") or []:
            if row.get("lat") is None or row.get("lon") is None:
                continue
            distance = distance_km(lat, lon, row["lat"], row["lon"])
            if distance <= radius_km:
                found.append({"station": row.get("station"), "name": row.get("name"), "latitude": row["lat"],
                              "longitude": row["lon"], "distance_km": round(distance, 1), "observed_at": row.get("utc_valid"),
                              "temperature_f": row.get("tmpf"), "wind_kt": row.get("sknt"), "visibility_mi": row.get("vsby"),
                              "precip_today_in": row.get("pday"), "weather": row.get("wxcodes")})
        return sorted(found, key=lambda item: item["distance_km"])[:5]

    def air(self, lat: float, lon: float, radius_km: float = 15) -> dict[str, Any]:
        rows = self._fetch("air", f"https://data.sensor.community/airrohr/v1/filter/area={lat:.3f},{lon:.3f},{max(1, min(radius_km, 50)):.0f}")
        pm25, pm10, sensors = [], [], set()
        for row in rows or []:
            for value in row.get("sensordatavalues") or []:
                try:
                    number = float(value.get("value"))
                except (TypeError, ValueError):
                    continue
                if value.get("value_type") == "P2" and 0 <= number < 1000:
                    pm25.append(number)
                    sensors.add((row.get("sensor") or {}).get("id"))
                elif value.get("value_type") == "P1" and 0 <= number < 2000:
                    pm10.append(number)
        median = lambda values: round(statistics.median(values), 1) if values else None
        return {"pm2_5_median": median(pm25), "pm10_median": median(pm10), "sensors": len(sensors),
                "source": "Sensor.Community citizen sensors (uncalibrated, ODbL)"}

    def gauges(self, lat: float, lon: float, radius_km: float = 25) -> list[dict[str, Any]]:
        degrees = max(0.05, min(radius_km / 111.0, 1.0))
        box = f"{lon - degrees:.3f},{lat - degrees:.3f},{lon + degrees:.3f},{lat + degrees:.3f}"
        data = self._fetch("gauges", f"https://waterservices.usgs.gov/nwis/iv/?format=json&bBox={box}"
                                     "&parameterCd=00065,00060&siteStatus=active")
        sites: dict[str, dict[str, Any]] = {}
        for series in ((data or {}).get("value") or {}).get("timeSeries") or []:
            info = series.get("sourceInfo") or {}
            location = ((info.get("geoLocation") or {}).get("geogLocation")) or {}
            values = ((series.get("values") or [{}])[0].get("value")) or []
            if not values or location.get("latitude") is None:
                continue
            code = ((series.get("variable") or {}).get("variableCode") or [{}])[0].get("value")
            site = sites.setdefault(info.get("siteName"), {"site": info.get("siteName"), "latitude": location["latitude"],
                                                          "longitude": location["longitude"]})
            site["gauge_height_ft" if code == "00065" else "discharge_cfs"] = _number(values[-1].get("value"))
            site["observed_at"] = values[-1].get("dateTime")
        found = list(sites.values())
        for site in found:
            site["distance_km"] = round(distance_km(lat, lon, site["latitude"], site["longitude"]), 1)
        return sorted([site for site in found if site['distance_km'] <= radius_km], key=lambda item: item["distance_km"])[:5]

    def elevation(self, lat: float, lon: float) -> float | None:
        data = self._fetch("elevation", f"https://epqs.nationalmap.gov/v1/json?x={lon:.5f}&y={lat:.5f}&units=Meters&wkid=4326")
        value = _number((data or {}).get("value"))
        return value if value is not None and value > -1000 else None

    def geocode(self, name: str) -> dict[str, Any] | None:
        from urllib.parse import quote

        data = self._fetch("geocode", f"https://geocoding-api.open-meteo.com/v1/search?name={quote(name.strip()[:80])}&count=1")
        results = (data or {}).get("results") or []
        if not results:
            return None
        place = results[0]
        return {"name": place.get("name"), "latitude": place.get("latitude"), "longitude": place.get("longitude"),
                "country": place.get("country"), "country_code": place.get("country_code"),
                "region": place.get("admin1"), "population": place.get("population")}

    def iss(self, track_minutes: int = 0) -> dict[str, Any]:
        """Where the International Space Station is now, computed with SGP4 from CelesTrak's elements.

        With ``track_minutes`` it also returns the ground track from now on, one point a minute,
        so the atlas can draw the orbit ahead of the station.
        """
        from datetime import timedelta

        from sgp4 import omm
        from sgp4.api import Satrec

        records = self._fetch("iss", PROVIDERS["iss"]["url"])
        satellite = Satrec()
        omm.initialize(satellite, records[0])
        moment = datetime.now(UTC)
        latitude, longitude, altitude = _ground_point(satellite, moment)
        value = {"latitude": latitude, "longitude": longitude, "altitude_km": altitude,
                 "computed_at": moment.isoformat(timespec="seconds"), "elements_epoch": records[0].get("EPOCH"),
                 "source": "CelesTrak elements, SGP4 (a few km accurate)"}
        if track_minutes:
            minutes = max(1, min(int(track_minutes), 180))
            value["track"] = [list(_ground_point(satellite, moment + timedelta(minutes=step))[:2])
                              for step in range(0, minutes + 1)]
        return value


def _ground_point(satellite: Any, moment: datetime) -> tuple[float, float, float]:
    from sgp4.api import jday

    jd, fraction = jday(moment.year, moment.month, moment.day, moment.hour, moment.minute,
                        moment.second + moment.microsecond / 1e6)
    error, position, _velocity = satellite.sgp4(jd, fraction)
    if error:
        raise ValueError(f"SGP4 could not propagate the ISS elements (error {error}).")
    x, y, z = position  # TEME kilometres; rotate by Greenwich sidereal time for the ground point
    longitude = math.degrees(math.atan2(y, x) - _gmst(jd + fraction))
    longitude = (longitude + 540) % 360 - 180
    radius = math.sqrt(x * x + y * y + z * z)
    return round(math.degrees(math.asin(z / radius)), 3), round(longitude, 3), round(radius - 6371, 0)


def _gmst(julian_date: float) -> float:
    t = (julian_date - 2451545.0) / 36525.0
    seconds = 67310.54841 + (876600 * 3600 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t ** 3
    return math.radians((seconds % 86400) / 240.0)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, UTC).isoformat(timespec="seconds")


#: Official public livestreams. Each channel's /live address always points at its current broadcast.
LIVESTREAMS = (
    {"id": "makkah", "title": "Makkah live (Masjid al-Haram) · Saudi Quran TV", "page": "https://www.youtube.com/@SaudiQuranTv/live",
     "latitude": 21.4225, "longitude": 39.8262, "country": "Saudi Arabia", "city": "Makkah",
     "provider": "Saudi Quran TV (official)", "keywords": "makkah mecca kaaba haram quran"},
    {"id": "madinah", "title": "Madinah live (Al-Masjid an-Nabawi) · Saudi Sunnah TV", "page": "https://www.youtube.com/@SaudiSunnahTv/live",
     "latitude": 24.4672, "longitude": 39.6112, "country": "Saudi Arabia", "city": "Madinah",
     "provider": "Saudi Sunnah TV (official)", "keywords": "madinah medina nabawi prophet mosque sunnah"},
    # The channel's live page: the fixed video this used (jKHvbJe9c_Y) ended, so it never played.
    {"id": "nasa_iss", "title": "NASA live · International Space Station", "page": "https://www.youtube.com/@NASA/live",
     "latitude": None, "longitude": None, "country": "United States", "city": "",
     "provider": "NASA (official)", "keywords": "nasa iss space station earth live space"},
    {"id": "siddhivinayak", "title": "Mumbai · Siddhivinayak live darshan",
     "page": "https://www.siddhivinayak.org/live-darshan/", "latitude": 19.0169, "longitude": 72.8305,
     "country": "India", "state": "Maharashtra", "city": "Mumbai",
     "channel": "https://www.youtube.com/@ShreeSiddhivinayakTemple/live",
     "provider": "Shree Siddhivinayak Ganapati Mandir Trust", "keywords": "india indian mumbai bombay siddhivinayak ganapati darshan"},
    {"id": "somnath", "title": "Somnath · Shree Somnath Trust live darshan",
     "page": "https://somnath.org/somnath-live-darshan", "latitude": 20.8880, "longitude": 70.4012,
     "country": "India", "state": "Gujarat", "city": "Somnath",
     "provider": "Shree Somnath Trust", "keywords": "india indian somnath gujarat darshan"},
    {"id": "shirdi", "title": "Shirdi · Saibaba Samadhi Mandir live darshan",
     "page": "https://sai.org.in/node/504?height=540&iframe=true&width=960", "latitude": 19.7663, "longitude": 74.4777,
     "country": "India", "state": "Maharashtra", "city": "Shirdi",
     "provider": "Shree Saibaba Sansthan Trust", "keywords": "india indian shirdi sai baba saibaba darshan",
     "stream_host": "cam.pc.cdn.bitgravity.com"},
    {"id": "arunachala", "title": "Tiruvannamalai · Arunachala mountain webcam",
     "page": "https://webcam.arunachala.org/", "image_url": "https://webcam.arunachala.org/live/video.jpg",
     "latitude": 12.2240, "longitude": 79.0670, "country": "India", "state": "Tamil Nadu", "city": "Tiruvannamalai",
     "provider": "Sri Ramanasramam", "keywords": "india indian arunachala ramanasramam tiruvannamalai tamil nadu"},
    # 2026-10-10: official 24/7 webcams and live news channels, each live when checked.
    {"id": "monterey_aquarium", "title": "Monterey Bay Aquarium live cams", "page": "https://www.youtube.com/@MontereyBayAquarium/live",
     "latitude": 36.6182, "longitude": -121.9019, "country": "United States", "state": "California", "city": "Monterey",
     "provider": "Monterey Bay Aquarium (official)", "keywords": "aquarium monterey bay ocean sea animals jellyfish birds aviary webcam"},
    {"id": "explore_nature", "title": "explore.org live nature cams (Brooks Falls bears, Alaska)", "page": "https://www.youtube.com/@ExploreLiveNatureCams/live",
     "latitude": 58.5544, "longitude": -155.7911, "country": "United States", "state": "Alaska", "city": "Katmai",
     "provider": "explore.org (official)", "keywords": "explore nature bears bear cam brooks falls katmai wildlife animals webcam"},
    {"id": "earthcam", "title": "EarthCam live", "page": "https://www.youtube.com/@earthcam/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "EarthCam (official)", "keywords": "earthcam city webcam times square skyline storm watch"},
    {"id": "sen_earth", "title": "Sen · Earth from space, live 4K", "page": "https://www.youtube.com/@Sen/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "Sen (official)", "keywords": "earth from space orbit planet 4k sen"},
    {"id": "jackson_hole", "title": "See Jackson Hole live webcams", "page": "https://www.youtube.com/@SeeJH/live",
     "latitude": 43.4799, "longitude": -110.7624, "country": "United States", "state": "Wyoming", "city": "Jackson",
     "provider": "SeeJH (official)", "keywords": "jackson hole wyoming town square mountains webcam"},
    {"id": "kilauea", "title": "USGS · Kilauea volcano live (Hawaii)", "page": "https://www.youtube.com/@usgs/live",
     "latitude": 19.4069, "longitude": -155.2834, "country": "United States", "state": "Hawaii", "city": "Hawaii Volcanoes National Park",
     "provider": "U.S. Geological Survey (official)", "keywords": "kilauea volcano lava hawaii usgs eruption webcam"},
    {"id": "news_aljazeera", "title": "Al Jazeera English · live news", "page": "https://www.youtube.com/@aljazeeraenglish/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "Al Jazeera English (official)", "keywords": "news al jazeera aljazeera world live tv"},
    {"id": "news_sky", "title": "Sky News · live", "page": "https://www.youtube.com/@SkyNews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "Sky News (official)", "keywords": "news sky news uk britain live tv"},
    {"id": "news_dw", "title": "DW News · live", "page": "https://www.youtube.com/@dwnews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "Deutsche Welle (official)", "keywords": "news dw deutsche welle germany europe live tv"},
    {"id": "news_abc", "title": "ABC News Live (US)", "page": "https://www.youtube.com/@ABCNews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "ABC News (official)", "keywords": "news abc america usa live tv"},
    {"id": "news_nbc", "title": "NBC News NOW · live", "page": "https://www.youtube.com/@NBCNews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "NBC News (official)", "keywords": "news nbc america usa live tv"},
    {"id": "news_cbs", "title": "CBS News 24/7 · live", "page": "https://www.youtube.com/@CBSNews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "CBS News (official)", "keywords": "news cbs america usa live tv"},
    {"id": "news_wion", "title": "WION · live news (India)", "page": "https://www.youtube.com/@WION/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "WION (official)", "keywords": "news wion india world live tv"},
    {"id": "news_ndtv", "title": "NDTV · live news (India)", "page": "https://www.youtube.com/@NDTV/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "NDTV (official)", "keywords": "news ndtv india hindi live tv"},
    {"id": "news_indiatoday", "title": "India Today · live news", "page": "https://www.youtube.com/@IndiaToday/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "India Today (official)", "keywords": "news india today live tv"},
    {"id": "news_trt", "title": "TRT World · live news", "page": "https://www.youtube.com/@trtworld/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "TRT World (official)", "keywords": "news trt world turkey live tv"},
    {"id": "news_cgtn", "title": "CGTN · live news", "page": "https://www.youtube.com/@cgtn/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "CGTN (official)", "keywords": "news cgtn china live tv"},
    {"id": "news_euronews", "title": "euronews English · live", "page": "https://www.youtube.com/@euronews/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "euronews (official)", "keywords": "news euronews europe live tv"},
    {"id": "news_nhk", "title": "NHK WORLD-JAPAN · live news", "page": "https://www.youtube.com/@NHKWORLDJAPAN/live",
     "latitude": None, "longitude": None, "country": "", "state": "", "city": "",
     "provider": "NHK (official)", "keywords": "news nhk japan live tv"},
)


def broadcast_source(page: str) -> dict[str, Any]:
    """Only publisher pages registered in this application can resolve media."""
    source = next((item for item in LIVESTREAMS if item['page'] == page and not item.get('image_url')), None)
    if source is None:
        raise ValueError("Choose a registered official livestream.")
    return source


def publisher_streams(content: bytes, page: str) -> list[str]:
    """Read the publisher's player address as data; never execute page scripts."""
    import json
    import re
    from bs4 import BeautifulSoup
    from urllib.parse import urlsplit

    source = broadcast_source(page)
    targets = []
    if source.get('stream_host'):
        # Drupal's public player settings encode slashes and ampersands in JSON.
        for candidate in re.findall(r'https?:[^\s"\'<>]+\.m3u8[^\s"\'<>]*', content.decode('utf-8', 'replace')):
            try:
                url = json.loads('"' + candidate + '"')
                parts = urlsplit(url)
                if (parts.scheme == 'https' and parts.hostname == source['stream_host']
                        and not parts.username and not parts.password and parts.port in (None, 443)
                        and parts.path.startswith('/cam/live/') and '/Saibaba/temple/feed/' in parts.path):
                    targets.append(url)
            except (ValueError, TypeError):
                continue
    else:
        for iframe in BeautifulSoup(content, 'html.parser').select('iframe[src]'):
            context = ' '.join(str(tag.get(key, '')) for tag in (iframe, iframe.parent)
                               if tag is not None for key in ('title', 'id', 'class', 'aria-label'))
            if re.search(r'(?i)\badvert(?:isement|ising)?\b|\badsense\b|\bad[-_ ]?(?:slot|container|banner|player)\b|\bpre[-_ ]?roll\b', context):
                continue
            parts = urlsplit(str(iframe['src']))
            match = re.fullmatch(r'/embed/([A-Za-z0-9_-]{11})', parts.path)
            if parts.scheme == 'https' and parts.hostname in ('www.youtube.com', 'www.youtube-nocookie.com') and match:
                targets.append('https://www.youtube.com/watch?v=' + match[1])
    if not targets:
        raise ValueError('The official publisher page offered no supported live player.')
    return list(dict.fromkeys(targets))[:4]


def publisher_stream(content: bytes, page: str) -> str:
    return publisher_streams(content, page)[0]


def resolve_livestream(page: str, *, include_audio: bool = False) -> str | dict[str, str]:
    """The current HLS address behind an official channel's /live page, via yt-dlp (no download)."""
    import yt_dlp
    from urllib.parse import urlsplit
    from app.research.safety import validate_public_url

    source = broadcast_source(page)
    targets = [page]
    if urlsplit(page).hostname != 'www.youtube.com':
        from app.research.fetcher import PublicFetcher
        fetcher = PublicFetcher('HAYAT-PublicIntelligence/2.0', timeout=10)
        try:
            resource = fetcher.fetch(page, 2 * 1024 * 1024)
            targets = publisher_streams(resource.content, page)
            if source.get('stream_host'):
                # A signed playlist must still answer as HLS, not a login page or expired token.
                for target in targets:
                    try:
                        playlist = fetcher.fetch(target, 2 * 1024 * 1024)
                        if playlist.content.lstrip().startswith(b'#EXTM3U'):
                            url = fetcher.validate_url(target)
                            return {'video_url': url, 'audio_url': ''} if include_audio else url
                    except Exception:
                        log_swallowed(_log, 'checking registered broadcast playlist')
                        continue
                raise ValueError('The publisher live playlist is unavailable.')
        finally:
            fetcher.close()

    # No format selector: forcing one ("best[...]") made yt-dlp refuse live broadcasts
    # outright, so the HLS rendition is chosen here -- the sharpest up to 720p, which
    # plays smoothly in the chat player.
    options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
               "socket_timeout": 10, "retries": 1, "extractor_retries": 1}
    info = None
    with yt_dlp.YoutubeDL(options) as client:
        for address in list(dict.fromkeys(([source['channel']] if source.get('channel') else []) + targets))[:4]:
            try:
                candidate = client.extract_info(address, download=False)
                if isinstance(candidate, dict) and candidate.get('is_live') and any(
                        str(fmt.get('protocol', '')).startswith('m3u8') and fmt.get('url')
                        and fmt.get('vcodec') != 'none' and not fmt.get('has_drm')
                        for fmt in candidate.get('formats') or []):
                    info = candidate
                    break
            except yt_dlp.utils.DownloadError:
                continue
    if not isinstance(info, dict) or not info.get("is_live"):
        raise ValueError("The channel is not broadcasting live right now.")
    hls = [item for item in info.get("formats") or [] if str(item.get("protocol", "")).startswith("m3u8")
           and item.get("url") and item.get("vcodec") != "none" and not item.get('has_drm')]
    # Sound and picture together when offered. On 2026-10-06 YouTube served Makkah's broadcast only as
    # separate video and audio renditions; a video-only stream still plays (silently) and can be looked at.
    streams = [item for item in hls if item.get("acodec") != "none"] or hls
    if not streams:
        raise ValueError("The live broadcast offered no HLS stream.")
    fitting = [item for item in streams if (item.get("height") or 0) <= 720] or streams
    selected = max(fitting, key=lambda item: item.get("height") or 0)
    url = selected['url']
    parts = urlsplit(url)
    if parts.scheme != 'https' or not (parts.hostname or '').endswith('.googlevideo.com'):
        raise ValueError("The channel returned an unexpected stream host.")
    url = validate_public_url(url)
    if not include_audio:
        return url
    audio_url = ''
    if selected.get('acodec') == 'none':
        audio = [item for item in info.get('formats') or [] if item.get('vcodec') == 'none'
                 and item.get('acodec') != 'none' and not item.get('has_drm')
                 and str(item.get('protocol', '')).startswith('m3u8') and item.get('url')]
        if audio:
            audio_url = max(audio, key=lambda item: item.get('abr') or item.get('tbr') or 0)['url']
            parts = urlsplit(audio_url)
            if parts.scheme != 'https' or not (parts.hostname or '').endswith('.googlevideo.com'):
                audio_url = ''
            else:
                audio_url = validate_public_url(audio_url)
    return {'video_url': url, 'audio_url': audio_url}


def livestream_records(_payload: Any = None) -> list[dict[str, Any]]:
    return [dict(item) for item in LIVESTREAMS]


def static_records(key: str) -> list[dict[str, Any]] | None:
    """Catalogs that ship with HAYAT instead of being fetched: the livestreams and Tallinn's camera list."""
    if key == "livestreams":
        return livestream_records()
    if key == "cameras_tallinn":
        import json
        from pathlib import Path
        path = Path(__file__).parent / "data" / "tallinn_cameras.json"
        return json.loads(path.read_text(encoding="utf-8"))["cameras"]
    return None


def safe(call: Callable[[], Any], label: str) -> tuple[Any, str]:
    """(value, '') or (None, a plain reason): one provider failing never sinks a combined answer."""
    try:
        return call(), ""
    except RateLimited as exc:
        return None, str(exc)
    except Exception as exc:
        log_swallowed(_log, f"live provider {label}")
        return None, f"{label} unavailable ({type(exc).__name__})"


__all__ = ["LiveData", "PROVIDERS", "LIVESTREAMS", "distance_km", "bearing", "resolve_livestream", "safe"]
