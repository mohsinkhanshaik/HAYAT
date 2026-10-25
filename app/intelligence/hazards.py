"""More of the world's official hazard warnings: tsunamis, volcanoes, national quake networks, outbreaks, airspace, floods.

Each source is the issuing authority's own public feed, checked live (robots.txt
included) on 2026-10-14:

* NOAA tsunami warning centres (NTWC Palmer, PTWC Honolulu): Atom bulletins.
* USGS volcano alert levels (HANS): the elevated volcanoes, placed by a second
  call per volcano that is cached for good -- volcanoes do not move.
* Japan Meteorological Agency earthquake list, with English area names and the
  JMA seismic intensity (shindo) actually measured.
* GeoNet New Zealand: felt earthquakes and volcanic alert levels.
* WHO Disease Outbreak News, placed at the country the report names.
* US aviation SIGMETs (aviationweather.gov): thunderstorms, severe turbulence,
  icing and volcanic ash, at the centre of the area they cover.
* England's Environment Agency flood warnings, placed by their flood area (also
  cached for good; areas are fixed).

Refused or unusable that day, and so not here: Germany's DWD warnings (robots.txt),
Smithsonian volcano reports and CISA advisories (403 to automated clients), IODA
internet outages (server errors), MeteoAlarm (megabytes per country with no
coordinates), JTWC (positions only inside per-storm text; GDACS covers its storms).

Severity follows each publisher's own scale and says which rule set it.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable

from app.intelligence.feeds import event, finite, text, timestamp
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

HAZARD_FEEDS = ("tsunami_ntwc", "tsunami_ptwc", "volcanoes_usgs", "earthquakes_jma", "earthquakes_geonet",
                "volcanoes_geonet", "outbreaks_who", "aviation_sigmet", "floods_uk",
                # Second wave, 2026-10-15.
                "earthquakes_bmkg", "fireballs_jpl", "asteroids_jpl", "geomagnetic_kp", "solar_flares",
                "air_quality_sg", "warnings_hko")
#: Category each feed's records belong to on the map and in the filters.
CATEGORY = {"tsunami_ntwc": "tsunami", "tsunami_ptwc": "tsunami", "volcanoes_usgs": "volcanoes",
            "earthquakes_jma": "earthquakes", "earthquakes_geonet": "earthquakes", "volcanoes_geonet": "volcanoes",
            "outbreaks_who": "health", "aviation_sigmet": "aviation", "floods_uk": "floods",
            "earthquakes_bmkg": "earthquakes", "fireballs_jpl": "space", "asteroids_jpl": "space",
            "geomagnetic_kp": "space_weather", "solar_flares": "space_weather", "air_quality_sg": "air_quality",
            "warnings_hko": "severe_weather"}
PROVIDER = {"tsunami_ntwc": "NOAA National Tsunami Warning Center", "tsunami_ptwc": "NOAA Pacific Tsunami Warning Center",
            "volcanoes_usgs": "USGS Volcano Hazards Program", "earthquakes_jma": "Japan Meteorological Agency",
            "earthquakes_geonet": "GeoNet (New Zealand)", "volcanoes_geonet": "GeoNet (New Zealand)",
            "outbreaks_who": "World Health Organization", "aviation_sigmet": "NOAA Aviation Weather Center",
            "floods_uk": "Environment Agency (England)", "earthquakes_bmkg": "BMKG (Indonesia)",
            "fireballs_jpl": "NASA/JPL CNEOS", "asteroids_jpl": "NASA/JPL CNEOS", "geomagnetic_kp": "NOAA SWPC",
            "solar_flares": "NOAA SWPC (GOES satellites)", "air_quality_sg": "National Environment Agency (Singapore)",
            "warnings_hko": "Hong Kong Observatory"}
#: New-lookups per refresh, so a storm of new flood areas cannot stall a refresh.
LOOKUPS_PER_REFRESH = 15
VOLCANO_LOOKUP = "https://volcanoes.usgs.gov/hans-public/api/volcano/getVolcano/{vnum}"
FLOOD_AREA_LOOKUP = "https://environment.data.gov.uk/flood-monitoring/id/floodAreas/{area}"


def _plain(html: str, limit: int = 1500) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", str(html or ""))).split())[:limit]


# ----------------------------------------------------------------- tsunami
def parse_atom(content: bytes) -> list[dict[str, Any]]:
    """Entries of a tsunami.gov Atom feed with their geo point and bulletin link."""
    from app.security import xml as ElementTree

    root = ElementTree.fromstring(content)
    atom, geo = "{http://www.w3.org/2005/Atom}", "{http://www.w3.org/2003/01/geo/wgs84_pos#}"
    headline = (root.findtext(f"{atom}title") or "").strip()
    entries = []
    for entry in root.iter(f"{atom}entry"):
        links = {link.get("title") or link.get("rel"): link.get("href") for link in entry.iter(f"{atom}link")}
        summary = entry.find(f"{atom}summary")
        entries.append({
            "headline": headline, "region": (entry.findtext(f"{atom}title") or "").strip(),
            "updated": (entry.findtext(f"{atom}updated") or "").strip(), "id": (entry.findtext(f"{atom}id") or "").strip(),
            "lat": entry.findtext(f"{geo}lat"), "long": entry.findtext(f"{geo}long"),
            "summary": _plain(" ".join(summary.itertext()) if summary is not None else ""),
            "bulletin": links.get("Bulletin") or links.get("alternate") or "",
        })
    return entries


def tsunami(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    summary = item.get("summary") or ""
    category = (re.search(r"Category:\s*([A-Za-z ]+?)\s+Bulletin", summary) or [None, "Information"])[1].strip()
    magnitude = finite((re.search(r"Preliminary Magnitude:\s*([\d.]+)", summary) or [None, None])[1])
    title = f"Tsunami {category.lower()}: {item.get('region') or 'unknown region'}"
    if magnitude is not None:
        title += f" (M{magnitude:.1f})"
    return event(feed, item.get("id") or item.get("updated"), title, f"tsunami {category.lower()}",
                 timestamp(item.get("updated")), item.get("bulletin") or feed.documentation,
                 [finite(item.get("long")), finite(item.get("lat"))], provider=PROVIDER[feed.key],
                 tsunami_category=category, magnitude=magnitude, headline=item.get("headline"),
                 summary=summary[:900], region=item.get("region"))


# -------------------------------------------------------------- volcanoes
def _cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        log_swallowed(_log, f"saving the lookup cache {path.name}")


def place_volcanoes(records: Any, get_json: Callable[[str], Any], cache_path: Path) -> list[dict[str, Any]]:
    """Elevated USGS volcanoes with their position, looked up once per volcano and cached."""
    if not isinstance(records, list):
        raise ValueError("USGS returned no volcano list.")
    cache, looked_up = _cache(cache_path), 0
    for record in records:
        vnum = str(record.get("vnum") or "") if isinstance(record, dict) else ""
        if not vnum:
            continue
        if vnum not in cache and looked_up < LOOKUPS_PER_REFRESH:
            looked_up += 1
            try:
                info = get_json(VOLCANO_LOOKUP.format(vnum=vnum))
                cache[vnum] = {"latitude": finite(info.get("latitude")), "longitude": finite(info.get("longitude")),
                               "region": info.get("region"), "url": info.get("volcano_url"),
                               "threat": info.get("nvews_threat")}
            except Exception:
                log_swallowed(_log, f"looking up USGS volcano {vnum}")
        record.update({f"_{key}": value for key, value in (cache.get(vnum) or {}).items()})
    if looked_up:
        _save(cache_path, cache)
    return records


_ALERT = {"WARNING": "high", "RED": "high", "WATCH": "medium", "ORANGE": "medium", "ADVISORY": "low", "YELLOW": "low"}


def usgs_volcano(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    name = text(item.get("volcano_name"), 80)
    level, colour = str(item.get("alert_level") or "").upper(), str(item.get("color_code") or "").upper()
    return event(feed, item.get("vnum"), f"{name} volcano: {level.lower() or 'elevated'} ({colour.lower() or '?'} aviation code)",
                 "volcanic unrest", timestamp(item.get("sent_unixtime")), item.get("notice_url") or feed.documentation,
                 [item.get("_longitude"), item.get("_latitude")], provider=PROVIDER[feed.key],
                 alert_level=level, aviation_colour=colour, observatory=item.get("obs_fullname"),
                 region=item.get("_region"), threat=item.get("_threat"), volcano_page=item.get("_url"))


def geonet_volcano(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    level = int(props.get("level") or 0)
    if level < 1:
        raise ValueError("Alert level 0 is no unrest; not an event.")
    return event(feed, props.get("volcanoID"), f"{props.get('volcanoTitle')} volcano: alert level {level}",
                 "volcanic unrest", None, f"https://www.geonet.org.nz/volcano/{props.get('volcanoID')}",
                 (item.get("geometry") or {}).get("coordinates"), provider=PROVIDER[feed.key], alert_level=level,
                 aviation_colour=props.get("acc"), activity=props.get("activity"), hazards=props.get("hazards"))


# ------------------------------------------------------------ earthquakes
_JMA_POINT = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([+-]\d+)?")
#: JMA seismic intensity, weakest to strongest.
SHINDO = ("1", "2", "3", "4", "5-", "5+", "6-", "6+", "7")


def jma_latest(payload: Any) -> list[dict[str, Any]]:
    """One record per earthquake: the newest JMA report that carries a hypocentre and magnitude."""
    if not isinstance(payload, list):
        raise ValueError("JMA returned no earthquake list.")
    latest: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict) or not item.get("cod") or not item.get("mag") or item.get("mag") == "Ｍ不明":
            continue
        current = latest.get(item.get("eid"))
        if current is None or str(item.get("rdt")) > str(current.get("rdt")):
            latest[item.get("eid")] = item
    return list(latest.values())


def jma_quake(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    point = _JMA_POINT.match(str(item.get("cod") or ""))
    if not point:
        raise ValueError("No hypocentre.")
    latitude, longitude = float(point[1]), float(point[2])
    depth = abs(int(point[3])) / 1000 if point[3] else None
    magnitude = finite(item.get("mag"))
    area = text(item.get("en_anm") or item.get("anm"), 120)
    shindo = str(item.get("maxi") or "")
    return event(feed, item.get("eid"), f"M{magnitude:.1f} - {' '.join(area.split())}" if magnitude is not None else area,
                 "earthquake", timestamp(item.get("at")), "https://www.jma.go.jp/bosai/map.html#contents=earthquake_map&lang=en",
                 [longitude, latitude], provider=PROVIDER[feed.key], magnitude=magnitude, depth_km=depth,
                 max_shindo=shindo or None, region=area, country="Japan")


def geonet_quake(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    props = item.get("properties") or {}
    magnitude = finite(props.get("magnitude"))
    return event(feed, props.get("publicID"), f"M{magnitude:.1f} - {props.get('locality')}" if magnitude is not None
                 else props.get("locality"), "earthquake", timestamp(props.get("time")),
                 f"https://www.geonet.org.nz/earthquake/{props.get('publicID')}", (item.get("geometry") or {}).get("coordinates"),
                 provider=PROVIDER[feed.key], magnitude=magnitude, depth_km=finite(props.get("depth")),
                 mmi=props.get("mmi"), quality=props.get("quality"), country="New Zealand")


# ---------------------------------------------------------------- outbreaks
def who_outbreak(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    from app.intelligence.gazetteer import locate

    title = text(item.get("Title") or item.get("OverrideTitle"), 300)
    if not title:
        raise ValueError("No title.")
    where = title.rsplit(" - ", 1)[-1] if " - " in title else title
    place = locate(where) or locate(title)
    disease = title.rsplit(" - ", 1)[0] if " - " in title else title
    return event(feed, item.get("DonId") or item.get("Id"), f"Outbreak: {title}", "disease outbreak",
                 timestamp(item.get("PublicationDateAndTime") or item.get("PublicationDate")),
                 f"https://www.who.int/emergencies/disease-outbreak-news/item/{item.get('UrlName')}",
                 [place["longitude"], place["latitude"]] if place else None, provider=PROVIDER[feed.key],
                 disease=disease, country=(place or {}).get("country") or where, place=(place or {}).get("place"),
                 location_precision=(place or {}).get("precision", "unknown"),
                 summary=_plain(item.get("Summary") or item.get("Overview"), 900))


# ----------------------------------------------------------------- airspace
SIGMET_HAZARDS = {"CONVECTIVE": "Thunderstorms", "TS": "Thunderstorms", "TURB": "Severe turbulence",
                  "ICE": "Severe icing", "IFR": "Low cloud and visibility", "MTN OBSCN": "Mountains obscured",
                  "ASH": "Volcanic ash", "VA": "Volcanic ash", "TC": "Tropical cyclone", "DS": "Dust storm",
                  "SS": "Sandstorm"}


def sigmet(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    coords = [(finite(point.get("lon")), finite(point.get("lat"))) for point in item.get("coords") or []
              if isinstance(point, dict)]
    coords = [(lon, lat) for lon, lat in coords if lon is not None and lat is not None]
    centre = ([round(sum(lon for lon, _ in coords) / len(coords), 4), round(sum(lat for _, lat in coords) / len(coords), 4)]
              if coords else None)
    hazard = str(item.get("hazard") or "")
    kind = str(item.get("airSigmetType") or "SIGMET")
    words = SIGMET_HAZARDS.get(hazard.upper(), hazard.title() or "Hazard")
    low, high = item.get("altitudeLow1") or item.get("altitudeLow2"), item.get("altitudeHi1") or item.get("altitudeHi2")
    return event(feed, f"{item.get('icaoId')}:{item.get('seriesId')}:{item.get('validTimeFrom')}",
                 f"{words} {kind} {item.get('seriesId') or ''}".strip(), f"{kind.lower()} {words.lower()}",
                 timestamp(item.get("creationTime")), "https://aviationweather.gov/gfa/#sigmet", centre,
                 provider=PROVIDER[feed.key], hazard=hazard, sigmet_type=kind, valid_from=timestamp(item.get("validTimeFrom")),
                 valid_to=timestamp(item.get("validTimeTo")), floor_ft=low, ceiling_ft=high,
                 area_points=len(coords), raw=text(item.get("rawAirSigmet"), 900),
                 limitation="The point is the centre of the warned area, not the hazard itself.")


# ------------------------------------------------------------------- floods
def place_floods(payload: Any, get_json: Callable[[str], Any], cache_path: Path) -> list[dict[str, Any]]:
    """Current flood warnings with their flood area's centre, each area looked up once and cached."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("The Environment Agency returned no warnings list.")
    cache, looked_up = _cache(cache_path), 0
    for item in items:
        area = str(item.get("floodAreaID") or "") if isinstance(item, dict) else ""
        if area and area not in cache and looked_up < LOOKUPS_PER_REFRESH:
            looked_up += 1
            try:
                info = (get_json(FLOOD_AREA_LOOKUP.format(area=area)) or {}).get("items") or {}
                cache[area] = {"latitude": finite(info.get("lat")), "longitude": finite(info.get("long")),
                               "label": info.get("label") or info.get("description")}
            except Exception:
                log_swallowed(_log, f"looking up flood area {area}")
        if area in cache:
            item.update({f"_{key}": value for key, value in cache[area].items()})
    if looked_up:
        _save(cache_path, cache)
    return items


_FLOOD_SEVERITY = {1: "high", 2: "medium", 3: "low", 4: "info"}


def uk_flood(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    level = int(finite(item.get("severityLevel")) or 4)
    if level >= 4:
        raise ValueError("A warning no longer in force is not a current hazard.")
    area = item.get("floodArea") or {}
    return event(feed, item.get("floodAreaID"), f"{item.get('severity')}: {item.get('description')}", "flood warning",
                 timestamp(item.get("timeSeverityChanged") or item.get("timeRaised")),
                 f"https://check-for-flooding.service.gov.uk/target-area/{item.get('floodAreaID')}",
                 [item.get("_longitude"), item.get("_latitude")], provider=PROVIDER[feed.key], severity_level=level,
                 severity_text=item.get("severity"), county=area.get("county"), river_or_sea=area.get("riverOrSea"),
                 message=text(item.get("message"), 900), tidal=bool(item.get("isTidal")), country="United Kingdom")


# --------------------------------------------- second wave (2026-10-15)
_BMKG_DIRECTIONS = {"baratlaut": "NW", "timurlaut": "NE", "baratdaya": "SW", "tenggara": "SE", "utara": "N",
                    "selatan": "S", "timur": "E", "barat": "W"}


def bmkg_quake(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    """Indonesia's BMKG: latest M5+ quakes, with the agency's own tsunami-potential statement."""
    latitude, longitude = (finite(part) for part in str(item.get("Coordinates") or ",").split(",")[:2])
    magnitude = finite(item.get("Magnitude"))
    region = str(item.get("Wilayah") or "")
    region = re.sub(r"(?i)\b(baratlaut|timurlaut|baratdaya|tenggara|utara|selatan|timur|barat)\b\s+",
                    lambda match: f"{_BMKG_DIRECTIONS[match[1].lower()]} of ", region)
    region = " ".join(word.title() if word.isupper() and word not in _BMKG_DIRECTIONS.values() else word
                      for word in region.split())
    potential = str(item.get("Potensi") or "")
    tsunami = "potential" if re.search(r"(?i)\bberpotensi tsunami\b", potential) and not re.search(r"(?i)\btidak\b", potential) else "none"
    return event(feed, item.get("DateTime"), f"M{magnitude:.1f} - {region}" if magnitude is not None else region, "earthquake",
                 timestamp(item.get("DateTime")), "https://www.bmkg.go.id/gempabumi/gempabumi-terkini.bmkg",
                 [longitude, latitude], provider=PROVIDER[feed.key], magnitude=magnitude,
                 depth_km=finite(re.sub(r"[^\d.]", "", str(item.get("Kedalaman") or ""))), region=region,
                 tsunami_potential=tsunami, tsunami_statement=potential, country="Indonesia")


def fireball(feed: Any, row: list[Any], fields: list[str]) -> dict[str, Any]:
    data = dict(zip(fields, row))
    lat, lon = finite(data.get("lat")), finite(data.get("lon"))
    if lat is not None and data.get("lat-dir") == "S":
        lat = -lat
    if lon is not None and data.get("lon-dir") == "W":
        lon = -lon
    impact = finite(data.get("impact-e"))
    return event(feed, data.get("date"), f"Fireball (meteor) of about {impact} kt" if impact is not None else "Fireball (meteor)",
                 "fireball", timestamp(str(data.get("date") or "").replace(" ", "T") + "Z"),
                 "https://cneos.jpl.nasa.gov/fireballs/", [lon, lat], provider=PROVIDER[feed.key],
                 impact_energy_kt=impact, radiated_energy_e10j=finite(data.get("energy")),
                 altitude_km=finite(data.get("alt")), speed_km_s=finite(data.get("vel")),
                 limitation="Detected by US government sensors; reported days to weeks later, and only the larger ones.")


#: Kilometres per astronomical unit and the Moon's mean distance, for plain-language approach distances.
AU_KM, LUNAR_KM = 149_597_870.7, 384_400


def asteroid(feed: Any, row: list[Any], fields: list[str]) -> dict[str, Any]:
    data = dict(zip(fields, row))
    distance_au = finite(data.get("dist"))
    if distance_au is None:
        raise ValueError("No distance.")
    lunar = distance_au * AU_KM / LUNAR_KM
    h = finite(data.get("h"))
    # Diameter from absolute magnitude with a typical albedo of 0.14; real albedos make it 0.5x-2x this.
    size_m = round(1329 / (0.14 ** 0.5) * 10 ** (-h / 5) * 1000) if h is not None else None
    name = str(data.get("des") or "?")
    title = f"Asteroid {name} passes {lunar:.2f} lunar distances from Earth" + (f" (about {size_m} m)" if size_m else "")
    return event(feed, f"{name}:{data.get('cd')}", title, "asteroid close approach",
                 timestamp(_jpl_date(data.get("cd"))), f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={name}",
                 None, provider=PROVIDER[feed.key], closest_approach=data.get("cd"), distance_km=round(distance_au * AU_KM),
                 lunar_distances=round(lunar, 2), speed_km_s=finite(data.get("v_rel")), estimated_size_m=size_m,
                 limitation="A close approach, not an impact: JPL lists every pass within 0.05 AU (about 19 lunar distances).")


def _jpl_date(value: Any) -> str | None:
    try:
        return datetime.strptime(str(value), "%Y-%b-%d %H:%M").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


def kp_index(feed: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    latest = rows[-1]
    kp = finite(latest.get("Kp"))
    storm = f"G{min(5, int(kp) - 4)}" if kp is not None and kp >= 5 else None
    words = "storm" if storm else "active" if kp is not None and kp >= 4 else "quiet" if kp is not None and kp < 3 else "unsettled"
    return event(feed, latest.get("time_tag"), f"Geomagnetic activity: Kp {kp:.1f} ({storm + ' storm' if storm else words})",
                 "geomagnetic activity", timestamp(latest.get("time_tag")), "https://www.swpc.noaa.gov/products/planetary-k-index",
                 None, provider=PROVIDER[feed.key], kp=kp, storm_scale=storm, level_words=words,
                 last_24h_max=max((finite(row.get("Kp")) or 0) for row in rows[-8:]),
                 aurora="Aurora may reach mid-latitudes" if kp is not None and kp >= 6 else
                 "Aurora mainly at high latitudes" if kp is not None and kp >= 4 else "Little aurora away from the poles")


def solar_flare(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    flare = str(item.get("max_class") or item.get("current_class") or "")
    return event(feed, item.get("begin_time"), f"Solar flare {flare}", "solar flare", timestamp(item.get("max_time")),
                 "https://www.swpc.noaa.gov/products/goes-x-ray-flux", None, provider=PROVIDER[feed.key], flare_class=flare,
                 began=timestamp(item.get("begin_time")), ended=timestamp(item.get("end_time")),
                 meaning={"X": "major: can cause radio blackouts on the sunlit side of Earth",
                          "M": "medium: can cause brief radio blackouts near the poles",
                          "C": "small: few effects", "B": "minor", "A": "background"}.get(flare[:1], ""))


PSI_BANDS = ((300, "hazardous", "high"), (200, "very unhealthy", "high"), (100, "unhealthy", "medium"),
             (50, "moderate", "info"), (-1, "good", "info"))


def singapore_psi(payload: Any) -> list[dict[str, Any]]:
    data = (payload or {}).get("data") or {}
    places = {item.get("name"): item.get("labelLocation") or {} for item in data.get("regionMetadata") or []}
    items = data.get("items") or []
    if not items:
        return []
    latest = items[-1]
    readings = latest.get("readings") or {}
    psi = readings.get("psi_twenty_four_hourly") or {}
    return [{"region": region, "psi": value, "pm25": (readings.get("pm25_twenty_four_hourly") or {}).get(region),
             "timestamp": latest.get("timestamp"), "latitude": (places.get(region) or {}).get("latitude"),
             "longitude": (places.get(region) or {}).get("longitude")} for region, value in psi.items()]


def psi_reading(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    psi = finite(item.get("psi"))
    if psi is None:
        raise ValueError("No PSI.")
    band = next(words for floor, words, _ in PSI_BANDS if psi > floor)
    return event(feed, f"{item['region']}:{item.get('timestamp')}", f"Air quality {band} in {item['region']} Singapore (PSI {psi:.0f})",
                 "air quality", timestamp(item.get("timestamp")), "https://www.haze.gov.sg/",
                 [item.get("longitude"), item.get("latitude")], provider=PROVIDER[feed.key], psi_24h=psi, band=band,
                 pm25_24h=item.get("pm25"), region=item["region"], country="Singapore")


#: Hong Kong Observatory warning codes that mean danger, then caution.
HKO_HIGH = re.compile(r"^(?:TC(?:8|9|10)|WRAINB|WRAINR|WFIRER)")
HKO_MEDIUM = re.compile(r"^(?:TC3|WRAINA|WTS|WMSGNL|WFIREY|WL|WFNTSA|WHOT|WCOLD)")
HONG_KONG = (114.17, 22.30)


def hko_warnings(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("The Hong Kong Observatory returned no warning summary.")
    return [{**value, "key": key} for key, value in payload.items()
            if isinstance(value, dict) and str(value.get("actionCode") or "").upper() != "CANCEL"]


def hko_warning(feed: Any, item: dict[str, Any]) -> dict[str, Any]:
    name = text(item.get("name") or item.get("type") or item.get("key"), 120)
    return event(feed, f"{item.get('key')}:{item.get('code')}:{item.get('issueTime')}", f"Hong Kong: {name}",
                 "weather warning", timestamp(item.get("updateTime") or item.get("issueTime")),
                 "https://www.hko.gov.hk/en/wxinfo/dailywx/wxwarntoday.htm", list(HONG_KONG), provider=PROVIDER[feed.key],
                 warning_code=item.get("code"), action=item.get("actionCode"), country="Hong Kong",
                 limitation="Territory-wide warning, placed at the centre of Hong Kong.")


def _jpl_rows(payload: Any, builder: Callable[[Any, list[Any], list[str]], dict[str, Any]]) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("fields"), list):
        raise ValueError("JPL returned no field list.")
    fields = payload["fields"]
    return [(builder, row, fields) for row in payload.get("data") or []]


# ----------------------------------------------------------------- dispatch
def normalize_hazards(feed: Any, payload: Any) -> tuple[dict[str, Any], int]:
    builders: dict[str, tuple[Callable[[Any], Any], Callable[[Any, dict[str, Any]], dict[str, Any]]]] = {
        "tsunami_ntwc": (lambda p: p, tsunami), "tsunami_ptwc": (lambda p: p, tsunami),
        "volcanoes_usgs": (lambda p: p, usgs_volcano),
        "earthquakes_jma": (jma_latest, jma_quake),
        "earthquakes_geonet": (lambda p: (p or {}).get("features"), geonet_quake),
        "volcanoes_geonet": (lambda p: (p or {}).get("features"), geonet_volcano),
        "outbreaks_who": (lambda p: (p or {}).get("value"), who_outbreak),
        "aviation_sigmet": (lambda p: p, sigmet),
        "floods_uk": (lambda p: p, uk_flood),
        "earthquakes_bmkg": (lambda p: ((p or {}).get("Infogempa") or {}).get("gempa"), bmkg_quake),
        "fireballs_jpl": (lambda p: _jpl_rows(p, fireball), lambda f, item: item[0](f, item[1], item[2])),
        "asteroids_jpl": (lambda p: _jpl_rows(p, asteroid), lambda f, item: item[0](f, item[1], item[2])),
        "geomagnetic_kp": (lambda p: [p] if isinstance(p, list) and p else [], kp_index),
        "solar_flares": (lambda p: p, solar_flare),
        "air_quality_sg": (singapore_psi, psi_reading),
        "warnings_hko": (hko_warnings, hko_warning),
    }
    if feed.key not in builders:
        raise ValueError("Unknown hazard feed.")
    select, builder = builders[feed.key]
    items = select(payload)
    if not isinstance(items, list):
        raise ValueError("Provider response must contain a bounded array.")
    rows, rejected = {}, 0
    for item in items[:2000]:
        try:
            row = builder(feed, item)
            rows[row["id"]] = row
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            rejected += 1
    # Volcano alert level 0 and expired warnings are dropped on purpose: an empty list is a quiet world, not a failure.
    if items and not rows and feed.key not in ("volcanoes_geonet", "floods_uk", "tsunami_ntwc", "tsunami_ptwc", "warnings_hko"):
        raise ValueError("All provider records failed validation; the prior snapshot is retained.")
    return rows, rejected


# ----------------------------------------------------------------- severity
def assess(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """(level, basis, badge, subtitle) from the publisher's own scale, or None for feeds not handled here."""
    feed, details = row.get("feed"), row.get("details") or {}
    if feed in ("tsunami_ntwc", "tsunami_ptwc"):
        category = str(details.get("tsunami_category") or "").lower()
        level = "high" if "warning" in category else "medium" if category in ("advisory", "watch", "threat") else "info"
        issued = {"information": "an information statement", "": "a bulletin"}.get(category, f"a {category}")
        return (level, f"The tsunami centre issued {issued}.", "TSUNAMI",
                f"{details.get('region') or ''} · {category}".strip(" ·"))
    if feed in ("volcanoes_usgs", "volcanoes_geonet"):
        if feed == "volcanoes_usgs":
            level = _ALERT.get(str(details.get("alert_level")), "low")
            basis = f"USGS alert level {details.get('alert_level')}, aviation colour code {details.get('aviation_colour')}."
        else:
            number = int(details.get("alert_level") or 0)
            level = "high" if number >= 3 else "medium" if number == 2 else "low"
            basis = f"GeoNet volcanic alert level {number} (0-5): {details.get('activity')}"
        return level, basis, "VOLCANO", str(details.get("aviation_colour") or "")
    if feed in ("earthquakes_jma", "earthquakes_geonet"):
        magnitude = details.get("magnitude") or 0
        if feed == "earthquakes_jma":
            shindo = str(details.get("max_shindo") or "")
            rank = SHINDO.index(shindo) if shindo in SHINDO else -1
            level = "high" if rank >= SHINDO.index("6-") or magnitude >= 7 else \
                "medium" if rank >= SHINDO.index("5-") or magnitude >= 6 else "low"
            basis = f"JMA magnitude {magnitude}, strongest measured shaking shindo {shindo or 'not reported'}."
        else:
            mmi = int(details.get("mmi") or 0)
            level = "high" if mmi >= 7 or magnitude >= 7 else "medium" if mmi >= 5 or magnitude >= 6 else "low"
            basis = f"GeoNet magnitude {magnitude:.1f}, intensity MMI {mmi}."
        return level, basis, f"M{magnitude:.1f}", f"{details.get('depth_km') or '?'} km deep"
    if feed == "outbreaks_who":
        return ("medium", "WHO published a Disease Outbreak News report, which it does for events of potential "
                "international concern.", "OUTBREAK", str(details.get("country") or ""))
    if feed == "aviation_sigmet":
        hazard = str(details.get("hazard") or "").upper()
        level = "high" if hazard in ("ASH", "VA") else "medium" if details.get("sigmet_type") == "SIGMET" else "low"
        floor, ceiling = details.get("floor_ft"), details.get("ceiling_ft")
        span = f"{floor or 'surface'}–{ceiling} ft" if ceiling else ""
        words = SIGMET_HAZARDS.get(hazard, hazard.title() or "a hazard").lower()
        return level, f"Aviation Weather Center {details.get('sigmet_type')} for {words}.", "SIGMET", span
    if feed == "earthquakes_bmkg":
        magnitude = details.get("magnitude") or 0
        tsunami = details.get("tsunami_potential") == "potential"
        level = "high" if tsunami or magnitude >= 7 else "medium" if magnitude >= 6 else "low"
        basis = f"BMKG magnitude {magnitude}; " + ("BMKG says it has tsunami potential." if tsunami else "no tsunami potential, per BMKG.")
        return level, basis, f"M{magnitude:.1f}", f"{details.get('depth_km') or '?'} km deep"
    if feed == "fireballs_jpl":
        energy = details.get("impact_energy_kt") or 0
        level = "high" if energy >= 10 else "medium" if energy >= 1 else "info"
        return level, f"Estimated impact energy {energy} kt (Chelyabinsk 2013 was about 440 kt).", "FIREBALL",             f"{details.get('altitude_km') or '?'} km up"
    if feed == "asteroids_jpl":
        lunar = details.get("lunar_distances") or 99
        return ("low" if lunar < 1 else "info", f"Passes {lunar} lunar distances away: a close approach, not an impact.",
                "NEO", f"{details.get('closest_approach')} UTC · {details.get('speed_km_s') or '?'} km/s")
    if feed == "geomagnetic_kp":
        kp = details.get("kp") or 0
        level = "high" if kp >= 7 else "medium" if kp >= 5 else "info"
        return level, f"NOAA planetary K-index {kp} ({details.get('storm_scale') or details.get('level_words')}).",             f"Kp {kp:.0f}", str(details.get("aurora") or "")
    if feed == "solar_flares":
        flare = str(details.get("flare_class") or "")
        level = {"X": "high", "M": "medium", "C": "low"}.get(flare[:1], "info")
        return level, f"GOES X-ray class {flare}: {details.get('meaning')}.", flare or "FLARE", ""
    if feed == "air_quality_sg":
        psi = details.get("psi_24h") or 0
        level = next(severity for floor, _, severity in PSI_BANDS if psi > floor)
        return level, f"24-hour PSI {psi:.0f} is {details.get('band')} on Singapore's scale.", f"PSI {psi:.0f}",             str(details.get("region") or "")
    if feed == "warnings_hko":
        code = str(details.get("warning_code") or "")
        level = "high" if HKO_HIGH.match(code) else "medium" if HKO_MEDIUM.match(code) else "low"
        return level, f"Hong Kong Observatory warning {code or 'in force'}.", "HKO", str(details.get("action") or "")
    if feed == "floods_uk":
        number = int(details.get("severity_level") or 4)
        return (_FLOOD_SEVERITY.get(number, "info"), f"Environment Agency: {details.get('severity_text')}.", "FLOOD",
                str(details.get("river_or_sea") or details.get("county") or ""))
    return None
