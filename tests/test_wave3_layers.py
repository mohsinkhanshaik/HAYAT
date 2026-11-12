"""2026-11-08 third gods-eye-view wave: heights anywhere, hurricane forecasts, mapped infrastructure, plate readers."""
import math
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.intelligence.cyclones import TropicalCyclones, category
from app.intelligence.explore import EXPLORE_TOOLS, explore_report, explore_request
from app.intelligence.infrastructure import Infrastructure, decode_points, tile_of
from app.intelligence.terrain import Terrain, climb, great_circle

PLACES = {"here": (21.3069, -157.8583), "honolulu": (21.3069, -157.8583), "hilo": (19.7071, -155.0885),
          "london": (51.5074, -0.1278), "dead sea": (31.559, 35.4444), "denver": (39.7392, -104.9903),
          "boulder": (40.0150, -105.2705), "ashburn": (39.0438, -77.4874), "grand coulee": (47.9564, -118.9817),
          "clarksville": (36.5298, -87.3595), "atlanta": (33.749, -84.388), "paris": (48.8566, 2.3522),
          "mid atlantic": (0.0, -30.0)}


def resolve(name):
    latitude, longitude = PLACES[name.lower()]
    return {"name": name.title(), "latitude": latitude, "longitude": longitude, "country_code": ""}


class FakeExplore:
    """The parts of Explore the new modules use; documents are matched by URL prefix."""

    def __init__(self, documents=None, tiles=None):
        import threading
        self.documents, self.tiles, self.asked, self.fetched = documents or {}, tiles or {}, [], []
        self.fetcher, self._cache, self._lock = self, {}, threading.RLock()

    def _place(self, place):
        found = resolve(place)
        return {"name": found["name"], "latitude": found["latitude"], "longitude": found["longitude"], "country_code": ""}

    def _json(self, key, url, ttl, max_bytes=0, disk=False):
        self.asked.append(url)
        for prefix, value in self.documents.items():
            if url.startswith(prefix):
                return value(url) if callable(value) else value
        raise ValueError(f"unexpected {url}")

    def fetch(self, url, max_bytes, accept):
        self.fetched.append(url)
        for suffix, content in self.tiles.items():
            if url.endswith(suffix):
                return type("R", (), {"content": content})()
        raise ValueError("Client error '404 Not Found' for url " + url)


# ------------------------------------------------------------------ terrain
def heights_document(values):
    def answer(url):
        from urllib.parse import parse_qs, urlsplit
        points = parse_qs(urlsplit(url).query)["points"][0].split(";")
        assert len(points) <= 64
        source = values(len(points)) if callable(values) else values
        return {"tileset": "mapterhorn-egm08", "results": [{"elevation": value, "geoid": 10.0, "ellipsoid": value + 10}
                                                            for value in source]}
    return {"https://terrain.reearth.land/heights.json": answer}


def test_elevation_above_below_and_at_sea_level():
    terrain = Terrain(FakeExplore(heights_document([1609.4])))
    value = terrain.elevation("denver")
    assert value["elevation_m"] == 1609.4 and value["elevation_ft"] == 5280 and not value["sea_or_sea_level"]
    assert "1,609 m (5,280 ft) above sea level" in explore_report(value) and "CC BY 4.0" in explore_report(value)
    assert "432 m (1,417 ft) below sea level" in explore_report(Terrain(FakeExplore(heights_document([-432.0]))).elevation("dead sea"))
    sea = Terrain(FakeExplore(heights_document([0]))).elevation("mid atlantic")
    assert sea["sea_or_sea_level"] and "no sea-floor depths" in explore_report(sea)
    with pytest.raises(ValueError, match="not a height on Earth"):
        Terrain(FakeExplore(heights_document([99999.0]))).elevation("denver")


def test_profile_climbs_and_descends_along_a_straight_line():
    rising = heights_document(lambda count: [1600.0 + 40 * index - (200 if index == count // 2 else 0) for index in range(count)])
    value = Terrain(FakeExplore(rising)).elevation("denver", "boulder", samples=16)
    assert value["explore"] == "elevation_profile" and len(value["profile"]) == 16
    assert value["climb_m"] == 40 * 13 + 240 and value["descent_m"] == 160 and value["net_m"] == 600
    assert 35 < value["distance_km"] < 45 and value["highest"]["at_km"] == value["distance_km"]
    assert "climbs 760 m and descends 160 m" in explore_report(value) and "not along roads" in explore_report(value)
    with pytest.raises(ValueError, match="2,000 km"):
        Terrain(FakeExplore(rising)).elevation("london", "denver")


def test_great_circle_and_climb_helpers():
    points = great_circle(51.5074, -0.1278, 48.8566, 2.3522, 5)
    assert points[0] == pytest.approx((51.5074, -0.1278)) and points[-1] == pytest.approx((48.8566, 2.3522))
    assert climb([0, 10, 5, 20]) == (25, 5)


# ------------------------------------------------------------------ cyclones
ISSUED = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
STATUS = {"activeStorms": [{"id": "cp042026", "binNumber": "CP4", "name": "Pena", "classification": "TS", "intensity": "45",
                            "pressure": "998", "latitudeNumeric": 18.0, "longitudeNumeric": -150.0, "movementDir": 285,
                            "movementSpeed": 9, "lastUpdate": ISSUED.isoformat().replace("+00:00", "Z"),
                            "publicAdvisory": {"advNum": "005", "issuance": ISSUED.isoformat().replace("+00:00", "Z"),
                                               "url": "https://www.nhc.noaa.gov/text/HFOTCPCP4.shtml"}}]}


def gis(layer):
    day = ISSUED.day
    if layer == 5:
        return {"features": [{"geometry": {"type": "Point", "coordinates": [-150.0 - tau / 12, 18.0 + tau / 48]},
                              "properties": {"binnumber": "CP4", "advisnum": "5", "tau": tau, "maxwind": wind, "gust": wind + 10,
                                             "validtime": f"{day:02d}/{(ISSUED.hour + tau) % 24:02d}00", "tcdvlp": kind}}
                             for tau, wind, kind in ((0, 45, "Tropical Storm"), (24, 60, "Tropical Storm"), (72, 90, "Hurricane"))]}
    if layer == 6:
        return {"features": [{"geometry": {"type": "LineString", "coordinates": [[-150, 18], [-156, 19.5]]},
                              "properties": {"binnumber": "CP4", "advisnum": "5"}}]}
    if layer == 7:  # a cone over Hawaii's Big Island, around Hilo
        ring = [[-150.5, 17.0], [-157.0, 18.5], [-157.0, 21.0], [-150.5, 19.5], [-150.5, 17.0]]
        return {"features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"binnumber": "CP4", "advisnum": "5"}}]}
    if layer == 8:
        return {"features": [{"geometry": {"type": "LineString", "coordinates": [[-155.1, 19.8], [-155.0, 19.6]]},
                              "properties": {"binnumber": "CP4", "advisnum": "5", "tcww": "TWA"}}]}
    if layer == 30:  # donut bands: 20-30% around Hilo, <5% further out
        inner = [[-156.0, 19.0], [-154.0, 19.0], [-154.0, 20.5], [-156.0, 20.5], [-156.0, 19.0]]
        outer = [[-160.0, 17.0], [-150.0, 17.0], [-150.0, 23.0], [-160.0, 23.0], [-160.0, 17.0]]
        return {"features": [{"geometry": {"type": "Polygon", "coordinates": [outer, inner]}, "properties": {"percentage": "<5%"}},
                             {"geometry": {"type": "Polygon", "coordinates": [inner]}, "properties": {"percentage": "20-30%"}},
                             {"geometry": None, "properties": {"percentage": "90%"}}]}
    if layer == 3:
        area = [[-120.0, 10.0], [-110.0, 10.0], [-110.0, 15.0], [-120.0, 15.0], [-120.0, 10.0]]
        return {"features": [{"geometry": {"type": "Polygon", "coordinates": [area]},
                              "properties": {"basin": "Pacific", "prob2day": "10%", "prob7day": "40%", "risk7day": "Medium"}},
                             {"geometry": None, "properties": {"basin": "Atlantic", "prob2day": "0%", "prob7day": "0%"}}]}
    raise AssertionError(layer)


def cyclone_documents(status=STATUS):
    base = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather_summary/MapServer/"
    documents = {"https://www.nhc.noaa.gov/CurrentStorms.json": status}
    for layer in (5, 6, 7, 8, 30, 3):
        documents[f"{base}{layer}/query"] = gis(layer)
    return documents


def test_cyclone_forecast_for_a_place_inside_the_cone():
    explore = FakeExplore(cyclone_documents())
    value = TropicalCyclones(explore).forecast("hilo")
    storm = value["storms"][0]
    assert storm["kind"] == "tropical storm" and storm["wind_mph"] == 52 and storm["speed_kt"] == 8 and storm["moving"] == "W"
    assert storm["in_cone"] and storm["map_matches"] and storm["warnings"] == ["tropical storm watch"]
    assert storm["peak"] == {"wind_kt": 90, "hours": 72, "category": "category 2", "kind": "hurricane"}
    assert storm["nearest_warning_km"] < 20 and storm["closest"]["hours"] == 72
    assert value["wind_chance"]["band"] == "20-30%" and value["outlook"][0]["chance_7_days"] == "40%"
    report = explore_report(value)
    assert "Hilo is INSIDE the forecast cone" in report and "Tropical Storm Pena" in report
    assert "39 mph+) at Hilo in the next 5 days: 20-30%" in report and "Pacific 40% in 7 days" in report
    assert "not the western Pacific" in report


def test_cyclone_outside_the_cone_names_the_edge_and_flags_a_lagging_map():
    status = {"activeStorms": [{**STATUS["activeStorms"][0], "publicAdvisory": {**STATUS["activeStorms"][0]["publicAdvisory"],
                                                                                "advNum": "006"}}]}
    value = TropicalCyclones(FakeExplore(cyclone_documents(status))).forecast("honolulu")
    storm = value["storms"][0]
    assert storm["in_cone"] is False and storm["cone_edge_km"] is not None and not storm["map_matches"]
    assert "outside the cone" in explore_report(value) and "has not caught up" in explore_report(value)
    assert value["wind_chance"]["band"] == "<5%"


def test_no_storms_reads_only_the_outlook_and_a_wrong_name_says_who_is_active():
    explore = FakeExplore(cyclone_documents({"activeStorms": []}))
    value = TropicalCyclones(explore).forecast()
    assert value["count"] == 0 and set(value) >= {"outlook"} and len(explore.asked) == 2  # status + outlook only
    assert "No active tropical cyclones" in explore_report(value)
    with pytest.raises(ValueError, match="Active now: Pena"):
        TropicalCyclones(FakeExplore(cyclone_documents())).forecast(name="Milton")
    assert category(64) == "category 1" and category(137) == "category 5" and category(50) == ""


# ------------------------------------------------------------------ mapped infrastructure (the real bundled extract)
def test_mapped_infrastructure_reads_the_bundled_extract():
    infrastructure = Infrastructure(FakeExplore())
    centres = infrastructure.mapped("ashburn", "datacenter", 15)
    assert centres["counts"]["datacenter"] >= 20 and centres["items"]["datacenter"][0]["osm"].startswith("https://www.openstreetmap.org/")
    dams = infrastructure.mapped("grand coulee", "dam", 10)
    assert any("Grand Coulee" in entry["name"] for entry in dams["items"]["dam"])
    bases = infrastructure.mapped("clarksville", "military", 40)
    assert any("Campbell" in entry["name"] for entry in bases["items"]["military"])
    everything = infrastructure.mapped("ashburn", "all", 30)
    report = explore_report(everything)
    assert "OpenStreetMap" in report and "not a statement about current activity" in report and "@" not in report
    with pytest.raises(ValueError, match="kind must be"):
        infrastructure.mapped("ashburn", "prisons")


def test_bundled_extract_holds_no_contact_details():
    import json
    import re
    data = json.loads((Path("app/intelligence/data/osm_infrastructure.json")).read_text(encoding="utf-8"))
    assert len(data["datacenter"]) > 4000 and len(data["dam"]) > 700 and len(data["military"]) > 36000
    text = json.dumps([row[1:3] for kind in ("datacenter", "dam") for row in data[kind]], ensure_ascii=False)
    assert not re.search(r"@|https?://", text) and "ODbL" in data["about"]


# ------------------------------------------------------------------ plate readers: a vector tile written independently
def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def message(number, payload):
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def uint(number, value):
    return varint(number << 3) + varint(value)


def zigzag(value):
    return (value << 1) ^ (value >> 31)


def mvt(points, zoom, tile_x, tile_y, extent=4096):
    """A one-layer tile 'cameras' with point features at (lat, lon, tags)."""
    keys, values, features = [], [], b""
    for latitude, longitude, tags in points:
        fx, fy = tile_of(latitude, longitude, zoom)
        px, py = round((fx - tile_x) * extent), round((fy - tile_y) * extent)
        tag_ids = []
        for key, value in tags.items():
            if key not in keys:
                keys.append(key)
            if value not in values:
                values.append(value)
            tag_ids += [keys.index(key), values.index(value)]
        packed_tags = b"".join(varint(item) for item in tag_ids)
        geometry = varint(1 | 1 << 3) + varint(zigzag(px)) + varint(zigzag(py))
        features += message(2, uint(1, len(features) + 1) + message(2, packed_tags) + uint(3, 1) + message(4, geometry))
    def encode(value):
        return message(1, value.encode()) if isinstance(value, str) else uint(5, value)
    layer = (uint(15, 2) + message(1, b"cameras") + features + b"".join(message(3, key.encode()) for key in keys)
             + b"".join(message(4, encode(value)) for value in values) + uint(5, extent))
    return message(3, layer)


def test_vector_tile_points_decode_to_their_places():
    zoom = 13
    x, y = (int(value) for value in tile_of(33.749, -84.388, zoom))
    content = mvt([(33.7495, -84.3885, {"brand": "Flock Safety", "osmId": 123, "osmType": "node"})], zoom, x, y)
    points = decode_points(content, zoom, x, y)
    assert len(points) == 1 and points[0]["brand"] == "Flock Safety" and points[0]["osmId"] == 123
    assert points[0]["latitude"] == pytest.approx(33.7495, abs=1e-4) and points[0]["longitude"] == pytest.approx(-84.3885, abs=1e-4)
    assert decode_points(content, zoom, x, y, "other") == []


def test_plate_readers_near_a_place_and_outside_the_coverage():
    zoom = 13
    x, y = (int(value) for value in tile_of(33.749, -84.388, zoom))
    tile = mvt([(33.7500, -84.3880, {"brand": "Flock Safety", "operator": "Atlanta Police Department", "osmId": 1, "osmType": "node",
                                     "directionCardinal": "n", "osmTimestamp": "2025-05-01T00:00:00Z"}),
                (33.7520, -84.3900, {"brand": "Flock Safety", "operator": "Atlanta Police Department", "osmId": 2, "osmType": "node",
                                     "direction": 270}),
                (33.7800, -84.3000, {"brand": "Far", "osmId": 3, "osmType": "node"})], zoom, x, y)
    explore = FakeExplore(tiles={f"cameras-us-hourly/{zoom}/{x}/{y}.mvt": tile})
    value = Infrastructure(explore).plate_readers("atlanta", 1)
    assert value["count"] == 2 and value["readers"][0]["facing"] == "N" and value["readers"][1]["facing"] == "W"
    assert value["operators"] == [("Atlanta Police Department", 2)] and value["readers"][0]["osm"].endswith("/node/1")
    assert all("cameras-us-hourly" in url for url in explore.fetched) and len(explore.fetched) <= 9
    report = explore_report(value)
    assert "2 mapped licence-plate readers within 1 km of Atlanta" in report and "not plate records" in report
    with pytest.raises(ValueError, match="United States and Canada"):
        Infrastructure(FakeExplore()).plate_readers("paris")


# ------------------------------------------------------------------ chat routing
@pytest.mark.parametrize("text,tool,arguments", [
    ("How high is Denver above sea level?", "place_elevation", {"place": "Denver"}),
    ("What is the elevation of Kathmandu?", "place_elevation", {"place": "Kathmandu"}),
    ("elevation of La Paz", "place_elevation", {"place": "La Paz"}),
    ("How high is Mount Kilimanjaro?", "place_elevation", {"place": "Mount Kilimanjaro"}),
    ("Is Amsterdam below sea level?", "place_elevation", {"place": "Amsterdam"}),
    ("Show me the elevation profile from Denver to Boulder", "place_elevation", {"place": "Denver", "to": "Boulder"}),
    ("How much climbing from Chamonix to Courmayeur?", "place_elevation", {"place": "Chamonix", "to": "Courmayeur"}),
    ("Is Miami in the hurricane cone?", "tropical_cyclones", {"place": "Miami"}),
    ("Will the hurricane hit Tampa?", "tropical_cyclones", {"place": "Tampa"}),
    ("Will hurricane Pena hit Hawaii?", "tropical_cyclones", {"place": "Hawaii", "name": "Pena"}),
    ("Where is the hurricane heading?", "tropical_cyclones", {}),
    ("Where is tropical storm Pena going?", "tropical_cyclones", {"name": "Pena"}),
    ("Show me the hurricane forecast", "tropical_cyclones", {}),
    ("tropical storm forecast for Honolulu", "tropical_cyclones", {"place": "Honolulu"}),
    ("What's the chance of tropical storm winds in Hilo?", "tropical_cyclones", {"place": "Hilo"}),
    ("tropical weather outlook", "tropical_cyclones", {}),
    ("Is a hurricane forming?", "tropical_cyclones", {}),
    ("Data centers near Ashburn", "mapped_infrastructure", {"place": "Ashburn", "kind": "datacenter"}),
    ("Are there any dams near Las Vegas?", "mapped_infrastructure", {"place": "Las Vegas", "kind": "dam"}),
    ("military bases near San Diego", "mapped_infrastructure", {"place": "San Diego", "kind": "military"}),
    ("Is there an air force base near Colorado Springs?", "mapped_infrastructure", {"place": "Colorado Springs", "kind": "military"}),
    ("What infrastructure is near Houston?", "mapped_infrastructure", {"place": "Houston"}),
    ("Are there license plate readers near Atlanta?", "plate_readers", {"place": "Atlanta"}),
    ("flock cameras in Denver", "plate_readers", {"place": "Denver"}),
    ("Are there any ALPR cameras near me?", "plate_readers", {"place": "here"}),
])
def test_everyday_phrasings_route_to_the_wave3_tools(text, tool, arguments):
    assert explore_request(text) == {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize("text", ["What is the elevation of privilege attack?", "how do hurricanes form", "what is a dam",
                                  "elevation of the sun right now", "How high is the ISS?"])
def test_questions_about_things_are_not_place_lookups(text):
    found = explore_request(text)
    assert found is None or found["tool"] not in ("place_elevation", "tropical_cyclones", "mapped_infrastructure")


def test_the_wave3_phrasings_reach_the_tools_through_the_whole_router():
    from app.intelligence.tools import direct_plan
    names = ["place_elevation", "tropical_cyclones", "mapped_infrastructure", "plate_readers", "intelligence_around",
             "intelligence_refresh", "intelligence_events", "intelligence_deep_look", "fire_perimeters", "satellite_pass"]
    for text, tool in (("How high is Denver above sea level?", "place_elevation"), ("Is Miami in the hurricane cone?", "tropical_cyclones"),
                       ("Where is the hurricane heading?", "tropical_cyclones"), ("Show me the hurricane forecast", "tropical_cyclones"),
                       ("data centres near Frankfurt", "mapped_infrastructure"), ("military bases near Norfolk", "mapped_infrastructure"),
                       ("Are there license plate readers near Atlanta?", "plate_readers")):
        assert direct_plan(text, names)["tool"] == tool, text
    assert set(("place_elevation", "tropical_cyclones", "mapped_infrastructure", "plate_readers")) <= set(EXPLORE_TOOLS)


def test_registration_and_capability_classification():
    from app.capabilities import CATALOG
    from app.intelligence.explore import register_explore_tools

    class Registry:
        def __init__(self):
            self.names = {}

        def register(self, name, description, schema, risk, category, handler):
            self.names[name] = (schema, handler)

    class AnyExplore(FakeExplore):
        def __getattr__(self, name):  # the older Explore tools' handlers are not under test here
            return lambda *args, **kwargs: None

    registry, explore = Registry(), AnyExplore()
    register_explore_tools(registry, explore)
    assert isinstance(explore.terrain, Terrain) and isinstance(explore.cyclones, TropicalCyclones)
    assert {"place_elevation", "tropical_cyclones", "mapped_infrastructure", "plate_readers"} <= set(registry.names)
    tools = {tool for capability in CATALOG for tool in capability.tools}
    assert {"place_elevation", "tropical_cyclones", "mapped_infrastructure", "plate_readers"} <= tools


# ------------------------------------------------------------------ live (opt-in): HAYAT_LIVE_WAVE3=1
@pytest.mark.skipif(os.environ.get("HAYAT_LIVE_WAVE3") != "1", reason="opt-in live check of the wave-3 sources")
def test_live_wave3_sources(tmp_path):
    from app.intelligence.explore import Explore
    from app.research.fetcher import PublicFetcher
    explore = Explore(PublicFetcher("HAYAT-Explore/1.0", timeout=40, delay=.35), tmp_path / "explore", resolve)
    terrain = Terrain(explore)
    denver, dead_sea = terrain.elevation("denver"), terrain.elevation("dead sea")
    profile = terrain.elevation("denver", "boulder", 24)
    print("\nLIVE elevation", denver["elevation_m"], dead_sea["elevation_m"], profile["climb_m"], profile["descent_m"])
    assert 1500 < denver["elevation_m"] < 1700 and dead_sea["elevation_m"] < -380 and profile["distance_km"] > 30
    cyclones = TropicalCyclones(explore).forecast("honolulu")
    print("LIVE cyclones", cyclones["count"], [(s["name"], s["kind"], s.get("wind_kt"), s.get("in_cone"), s.get("map_matches"),
                                               len(s.get("forecast") or [])) for s in cyclones["storms"]],
          cyclones.get("wind_chance"), [(a["basin"], a["chance_7_days"]) for a in cyclones.get("outlook") or []],
          cyclones["unavailable"])
    print(explore_report(cyclones))
    assert not cyclones["unavailable"] and isinstance(cyclones["outlook"], list)
    readers = Infrastructure(explore).plate_readers("atlanta", 3)
    print("LIVE plate readers", readers["count"], readers["tiles_read"], readers["operators"][:3], readers["brands"][:3])
    assert readers["tiles_read"] >= 1 and not readers["unavailable"]
    infrastructure = Infrastructure(explore).mapped("ashburn", "all", 20)
    print("LIVE infrastructure", infrastructure["counts"])
    assert math.isfinite(infrastructure["radius_km"])
