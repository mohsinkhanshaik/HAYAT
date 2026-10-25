"""Official hazard warnings: tsunami bulletins, volcano alert levels, JMA and GeoNet quakes, WHO outbreaks, SIGMETs, UK floods.

Fixtures keep the exact shape of each publisher's response as fetched live on 2026-10-14.
"""

from __future__ import annotations

import json

import pytest

from app.intelligence import hazards
from app.intelligence.classify import assess, category
from app.intelligence.feeds import BY_KEY, normalize
from app.intelligence.objects import OBJECT_CATEGORIES, is_official
from app.intelligence.world_requests import world_request

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:geo="http://www.w3.org/2003/01/geo/wgs84_pos#">
<title>Tsunami Information Statement Number 1</title>
<entry><title>near the central Mid-Atlantic Ridge</title><updated>2026-10-13T12:36:06Z</updated>
<geo:lat>-0.200</geo:lat><geo:long>-19.200</geo:long>
<summary type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml"><strong>Category:</strong> Information<br/>
<strong>Bulletin Issue Time: </strong> 2026.10.13 12:36:06 UTC <br/><strong>Preliminary Magnitude: </strong>6.8(Mwp)<br/>
<b>Note:</b> a damaging tsunami is not expected.</div></summary>
<id>urn:uuid:802162bb</id><link rel="alternate" title="Bulletin" href="https://www.tsunami.gov/events/PAAQ/WEXX32.txt"/></entry>
</feed>"""
WARNING_ATOM = ATOM.replace(b"Information<br/>", b"Warning<br/>")
VOLCANOES = [{"obs_fullname": "Alaska Volcano Observatory", "volcano_name": "Great Sitkin", "vnum": "311120",
              "sent_unixtime": 1792008211, "color_code": "ORANGE", "alert_level": "WATCH",
              "notice_url": "https://volcanoes.usgs.gov/hans-public/notice/DOI-USGS-AVO-2026-10-14"},
             {"volcano_name": "Kilauea", "vnum": "332010", "sent_unixtime": 1792008360, "color_code": "RED",
              "alert_level": "WARNING", "notice_url": "https://volcanoes.usgs.gov/hans-public/notice/x"}]
PLACES = {"311120": {"latitude": 52.0765, "longitude": -176.1109, "region": "Aleutians", "volcano_url": "https://avo"},
          "332010": {"latitude": 19.421, "longitude": -155.287, "region": "Hawaii"}}
JMA = [
    {"eid": "20261015074108", "rdt": "2026-10-15T07:43:00+09:00", "at": "2026-10-15T07:41:00+09:00", "cod": "+32.5+130.5+0/",
     "mag": "3.6", "maxi": "2", "anm": "熊本県天草・芦北地方", "en_anm": "Amakusa and Ashikita Region,  Kumamoto Prefecture"},
    # An earlier report of the same quake (intensity only, no hypocentre) and a later revision.
    {"eid": "20261015074108", "rdt": "2026-10-15T07:42:00+09:00", "at": "2026-10-15T07:41:00+09:00", "cod": "", "mag": "",
     "maxi": "2", "en_anm": "Kumamoto"},
    {"eid": "20261014010000", "rdt": "2026-10-14T01:05:00+09:00", "at": "2026-10-14T01:00:00+09:00",
     "cod": "+38.1+142.9-40000/", "mag": "7.1", "maxi": "6-", "en_anm": "Off the coast of Miyagi"},
]
GEONET_QUAKES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [173.79, -42.31]},
     "properties": {"publicID": "2026p774994", "time": "2026-10-14T17:56:54.899Z", "depth": 21.8, "magnitude": 2.56,
                    "mmi": 3, "locality": "10 km north-east of Kaikoura", "quality": "best"}}]}
GEONET_VOLCANOES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [175.896, -38.784]},
     "properties": {"acc": "Green", "activity": "No volcanic unrest.", "level": 0, "volcanoID": "taupo", "volcanoTitle": "Taupo"}},
    {"type": "Feature", "geometry": {"type": "Point", "coordinates": [177.183, -37.521]},
     "properties": {"acc": "Orange", "activity": "Moderate to heightened volcanic unrest.", "level": 2,
                    "volcanoID": "whiteisland", "volcanoTitle": "White Island"}}]}
WHO = {"value": [{"Id": "9cca", "DonId": "2026-DON620", "UrlName": "2026-DON620", "Title": "Marburg virus disease - Rwanda",
                  "PublicationDateAndTime": "2026-10-10T15:00:00Z", "Summary": "<p>On 2 October 2026, WHO was notified.</p>"}]}
SIGMETS = [{"icaoId": "KKCI", "seriesId": "ROMEO 3", "creationTime": "2026-10-14T21:34:00.000Z", "validTimeFrom": 1792013640,
            "validTimeTo": 1792027920, "airSigmetType": "SIGMET", "hazard": "TURB", "altitudeLow1": 25000, "altitudeHi2": 33000,
            "coords": [{"lon": -95.0, "lat": 45.0}, {"lon": -93.0, "lat": 44.0}, {"lon": -97.0, "lat": 43.0}],
            "rawAirSigmet": "SIGMET ROMEO 3 VALID UNTIL 150132"},
           {"icaoId": "KKCI", "seriesId": "1", "creationTime": "2026-10-14T21:40:00Z", "validTimeFrom": 1792013000,
            "airSigmetType": "SIGMET", "hazard": "ASH", "coords": [{"lon": -152.0, "lat": 60.0}]}]
FLOODS = {"items": [
    {"floodAreaID": "054WACDV4A", "description": "The tidal Deben estuary", "severity": "Flood Warning", "severityLevel": 2,
     "timeSeverityChanged": "2026-10-14T08:47:00", "isTidal": True, "message": "Flooding is expected.",
     "floodArea": {"county": "Suffolk", "riverOrSea": "River Deben, North Sea"}},
    {"floodAreaID": "OLD1", "description": "Old area", "severity": "Warning no longer in force", "severityLevel": 4,
     "floodArea": {}}]}


def rows(key, payload):
    found, rejected = normalize(BY_KEY[key], payload)
    return list(found.values()), rejected


def test_every_new_source_is_a_registered_feed_from_its_issuing_agency():
    for key in hazards.HAZARD_FEEDS:
        feed = BY_KEY[key]
        assert feed.url.startswith("https://") and feed.documentation.startswith("https://")
        assert is_official({"feed": key}) and category({"feed": key, "kind": ""}) == hazards.CATEGORY[key]
    assert {"tsunami", "health"} <= set(OBJECT_CATEGORIES)


def test_tsunami_bulletins_carry_their_category_place_and_magnitude():
    found, _ = rows("tsunami_ntwc", hazards.parse_atom(ATOM))
    row = found[0]
    assert row["title"] == "Tsunami information: near the central Mid-Atlantic Ridge (M6.8)"
    assert (row["latitude"], row["longitude"]) == (-0.2, -19.2) and row["url"].endswith("WEXX32.txt")
    assessed = assess(row)
    assert assessed["severity"] == "info" and "information statement" in assessed["severity_basis"]
    warning, _ = rows("tsunami_ptwc", hazards.parse_atom(WARNING_ATOM))
    assert assess(warning[0])["severity"] == "high"
    empty = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>No bulletins</title></feed>'
    assert rows("tsunami_ntwc", hazards.parse_atom(empty)) == ([], 0)  # a quiet ocean is not a failure


def test_usgs_volcanoes_are_placed_once_and_cached(tmp_path):
    calls = []

    def get_json(url):
        calls.append(url)
        return PLACES[url.rsplit("/", 1)[1]]
    cache = tmp_path / "volcano-places.json"
    placed = hazards.place_volcanoes([dict(item) for item in VOLCANOES], get_json, cache)
    assert len(calls) == 2 and json.loads(cache.read_text())["311120"]["region"] == "Aleutians"
    hazards.place_volcanoes([dict(item) for item in VOLCANOES], get_json, cache)
    assert len(calls) == 2  # volcanoes do not move: no second lookup
    found, _ = rows("volcanoes_usgs", placed)
    sitkin = next(row for row in found if "Great Sitkin" in row["title"])
    assert (sitkin["latitude"], sitkin["longitude"]) == (52.0765, -176.1109)
    assert assess(sitkin)["severity"] == "medium" and assess(next(r for r in found if "Kilauea" in r["title"]))["severity"] == "high"


def test_a_failed_volcano_lookup_leaves_the_alert_unplaced_rather_than_guessed(tmp_path):
    def broken(url):
        raise TimeoutError("slow server")
    found, _ = rows("volcanoes_usgs", hazards.place_volcanoes([dict(VOLCANOES[0])], broken, tmp_path / "v.json"))
    assert found[0]["latitude"] is None and found[0]["longitude"] is None


def test_jma_keeps_one_record_per_quake_with_shindo_and_english_names():
    found, _ = rows("earthquakes_jma", JMA)
    assert len(found) == 2
    kumamoto = next(row for row in found if row["details"]["magnitude"] == 3.6)
    assert kumamoto["title"] == "M3.6 - Amakusa and Ashikita Region, Kumamoto Prefecture"
    assert (kumamoto["latitude"], kumamoto["longitude"]) == (32.5, 130.5) and kumamoto["details"]["max_shindo"] == "2"
    miyagi = next(row for row in found if row["details"]["magnitude"] == 7.1)
    assert miyagi["details"]["depth_km"] == 40.0 and assess(miyagi)["severity"] == "high"
    assert assess(kumamoto)["severity"] == "low"


def test_geonet_quakes_and_only_volcanoes_in_unrest():
    quakes, _ = rows("earthquakes_geonet", GEONET_QUAKES)
    assert quakes[0]["title"] == "M2.6 - 10 km north-east of Kaikoura" and quakes[0]["details"]["mmi"] == 3
    volcanoes, rejected = rows("volcanoes_geonet", GEONET_VOLCANOES)
    assert [row["title"] for row in volcanoes] == ["White Island volcano: alert level 2"] and rejected == 1
    assert assess(volcanoes[0])["severity"] == "medium"


def test_who_outbreaks_are_placed_at_the_country_and_say_so():
    found, _ = rows("outbreaks_who", WHO)
    row = found[0]
    assert row["title"] == "Outbreak: Marburg virus disease - Rwanda" and row["details"]["country"] == "Rwanda"
    assert "country" in row["details"]["location_precision"] and row["latitude"] is not None
    assert row["url"] == "https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON620"
    assert row["details"]["summary"] == "On 2 October 2026, WHO was notified."


def test_sigmets_sit_at_the_centre_of_their_area_and_ash_is_high():
    found, _ = rows("aviation_sigmet", SIGMETS)
    turbulence = next(row for row in found if row["details"]["hazard"] == "TURB")
    assert turbulence["title"] == "Severe turbulence SIGMET ROMEO 3"
    assert (turbulence["latitude"], turbulence["longitude"]) == (44.0, -95.0)
    assert "severe turbulence" in assess(turbulence)["severity_basis"]
    ash = next(row for row in found if row["details"]["hazard"] == "ASH")
    assert assess(ash)["severity"] == "high"


def test_uk_flood_warnings_in_force_are_placed_by_their_area(tmp_path):
    def get_json(url):
        return {"items": {"lat": "52.03963", "long": "1.36909", "label": "The tidal Deben estuary"}}
    found, rejected = rows("floods_uk", hazards.place_floods(json.loads(json.dumps(FLOODS)), get_json, tmp_path / "f.json"))
    assert len(found) == 1 and rejected == 1  # the warning no longer in force is not a current hazard
    assert (found[0]["latitude"], found[0]["longitude"]) == (52.03963, 1.36909)
    assert assess(found[0])["severity"] == "medium" and found[0]["details"]["county"] == "Suffolk"


@pytest.mark.parametrize("text, feeds", [
    ("any tsunami warnings right now?", ["tsunami_ntwc", "tsunami_ptwc"]),
    ("any disease outbreaks in the world right now", ["outbreaks_who"]),
    ("any volcano alerts right now", ["volcanoes_usgs", "volcanoes_geonet"]),
    ("any earthquakes near Tokyo", ["earthquakes_jma"]),
])
def test_everyday_questions_refresh_the_new_sources_first(text, feeds):
    request = world_request(text)
    assert request is not None, text
    assert request["tool"] == "intelligence_objects"
    assert set(feeds) <= set(request["refresh"])


# ------------------------------------------------------- second wave (2026-10-15)

BMKG = {"Infogempa": {"gempa": [
    {"DateTime": "2026-10-11T21:34:53+00:00", "Coordinates": "0.64,121.75", "Magnitude": "5.7", "Kedalaman": "76 km",
     "Wilayah": "29 km BaratLaut POHUWATO-GORONTALO", "Potensi": "Tidak berpotensi tsunami"},
    {"DateTime": "2026-10-10T01:00:00+00:00", "Coordinates": "-3.5,128.2", "Magnitude": "6.4", "Kedalaman": "10 km",
     "Wilayah": "50 km Tenggara AMBON-MALUKU", "Potensi": "Berpotensi tsunami untuk diteruskan pada masyarakat"}]}}
FIREBALLS = {"fields": ["date", "energy", "impact-e", "lat", "lat-dir", "lon", "lon-dir", "alt", "vel"],
             "data": [["2026-10-04 03:14:45", "5.4", "0.18", "41.8", "S", "173.1", "W", "26.9", "13.8"],
                      ["2026-09-11 01:12:11", "24.6", "12.5", "54.4", "N", "100.1", "W", "38.0", "20.7"]]}
APPROACHES = {"fields": ["des", "orbit_id", "jd", "cd", "dist", "dist_min", "dist_max", "v_rel", "v_inf", "t_sigma_f", "h"],
              "data": [["2026 TP6", "6", "2461328.5", "2026-Oct-15 00:01", "0.00146363563551003", "0.0014", "0.0015",
                        "10.29", "10.1", "< 00:01", "27.259"],
                       ["2026 TS", "11", "2461331.7", "2026-Oct-18 06:16", "0.00606186873313466", "0.006", "0.0061",
                        "8.1", "8.0", "00:01", "24.1"]]}
KP = [{"time_tag": "2026-10-14T21:00:00", "Kp": 3.0}, {"time_tag": "2026-10-15T00:00:00", "Kp": 6.33}]
PSI = {"data": {"regionMetadata": [{"name": "west", "labelLocation": {"latitude": 1.357, "longitude": 103.7}}],
                "items": [{"timestamp": "2026-10-15T16:00:00+08:00",
                           "readings": {"psi_twenty_four_hourly": {"west": 192}, "pm25_twenty_four_hourly": {"west": 120}}}]}}


def test_bmkg_quakes_carry_bmkg_tsunami_potential_and_english_directions():
    found, _ = rows("earthquakes_bmkg", BMKG)
    quiet = next(row for row in found if row["details"]["magnitude"] == 5.7)
    assert quiet["title"] == "M5.7 - 29 km NW of Pohuwato-Gorontalo" and (quiet["latitude"], quiet["longitude"]) == (0.64, 121.75)
    assert quiet["details"]["tsunami_potential"] == "none" and assess(quiet)["severity"] == "low"
    warned = next(row for row in found if row["details"]["magnitude"] == 6.4)
    assert warned["details"]["tsunami_potential"] == "potential" and assess(warned)["severity"] == "high"
    assert "SE of Ambon-Maluku" in warned["title"]


def test_fireballs_are_placed_with_signed_coordinates_and_rated_by_energy():
    found, _ = rows("fireballs_jpl", FIREBALLS)
    small = next(row for row in found if row["details"]["impact_energy_kt"] == 0.18)
    assert (small["latitude"], small["longitude"]) == (-41.8, -173.1) and assess(small)["severity"] == "info"
    big = next(row for row in found if row["details"]["impact_energy_kt"] == 12.5)
    assert (big["latitude"], big["longitude"]) == (54.4, -100.1) and assess(big)["severity"] == "high"
    assert category(small) == "space"


def test_asteroid_approaches_are_close_passes_not_impacts():
    found, _ = rows("asteroids_jpl", APPROACHES)
    near = next(row for row in found if "2026 TP6" in row["title"])
    assert near["title"] == "Asteroid 2026 TP6 passes 0.57 lunar distances from Earth (about 13 m)"
    assert near["latitude"] is None and near["details"]["distance_km"] == 218957
    assessed = assess(near)
    assert assessed["severity"] == "low" and "not an impact" in assessed["severity_basis"]
    assert assess(next(row for row in found if "2026 TS" in row["title"]))["severity"] == "info"


def test_space_weather_kp_and_flares():
    found, _ = rows("geomagnetic_kp", KP)
    assert found[0]["title"] == "Geomagnetic activity: Kp 6.3 (G2 storm)" and assess(found[0])["severity"] == "medium"
    assert "mid-latitudes" in found[0]["details"]["aurora"]
    flares, _ = rows("solar_flares", [{"begin_time": "2026-10-15T07:20:00Z", "max_time": "2026-10-15T07:27:00Z", "max_class": "X1.2"}])
    assert flares[0]["title"] == "Solar flare X1.2" and assess(flares[0])["severity"] == "high"


def test_singapore_air_quality_and_hong_kong_warnings():
    found, _ = rows("air_quality_sg", PSI)
    assert found[0]["title"] == "Air quality unhealthy in west Singapore (PSI 192)" and assess(found[0])["severity"] == "medium"
    assert category(found[0]) == "air_quality"
    assert rows("warnings_hko", {}) == ([], 0)
    signal, _ = rows("warnings_hko", {"WTCSGNL": {"name": "Tropical Cyclone Warning Signal", "code": "TC8NE",
                                                    "actionCode": "ISSUE", "issueTime": "2026-10-15T08:00:00+08:00"},
                                       "WRAIN": {"name": "Rainstorm", "code": "WRAINA", "actionCode": "CANCEL"}})
    assert len(signal) == 1 and signal[0]["title"] == "Hong Kong: Tropical Cyclone Warning Signal"
    assert assess(signal[0])["severity"] == "high" and (signal[0]["latitude"], signal[0]["longitude"]) == (22.3, 114.17)


@pytest.mark.parametrize("text, feeds", [
    ("any asteroids passing earth this week", ["asteroids_jpl"]),
    ("air quality in Singapore right now", ["air_quality_sg"]),
    ("any earthquakes in Indonesia today", ["earthquakes_bmkg"]),
])
def test_second_wave_questions_refresh_their_sources(text, feeds):
    request = world_request(text)
    assert request is not None, text
    assert set(feeds) <= set(request["refresh"])
