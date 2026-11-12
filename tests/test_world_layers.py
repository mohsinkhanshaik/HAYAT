"""2026-11-06 layers from gods-eye-view: live transit, GPS interference, US airspace, fire perimeters, whales, street photos."""
import io
import struct
from pathlib import Path

import pytest

from app.intelligence import airspace as airspace_module
from app.intelligence.airspace import Airspace, altitude, contains, edge_km, in_us
from app.intelligence.explore import explore_report, explore_request
from app.intelligence.gnss import CAVEAT, GpsInterference, Observations, RateLimited, classify, level
from app.intelligence.ground import Ground
from app.intelligence.live import LiveData
from app.intelligence.transit import (FEEDS, WorldTransit, coverage, decode_vehicle_positions, route_name, transit_vehicles)

PLACES = {"toronto": (43.6532, -79.3832), "here": (43.6532, -79.3832), "london": (51.5074, -0.1278),
          "washington": (38.8977, -77.0365), "gdansk": (54.352, 18.6466), "friday harbor": (48.53, -123.01),
          "paris": (48.8584, 2.2945), "fresno": (36.74, -119.78)}


def resolve(name):
    latitude, longitude = PLACES[name.lower()]
    return {"name": name.title(), "latitude": latitude, "longitude": longitude, "country_code": ""}


# ------------------------------------------------------------------ protobuf, written independently of the decoder
def varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number, value):
    if isinstance(value, float):
        return varint(number << 3 | 5) + struct.pack("<f", value)
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    data = value.encode() if isinstance(value, str) else value
    return varint(number << 3 | 2) + varint(len(data)) + data


def vehicle_entity(entity_id, lat, lon, *, route="", label="", plate="", speed=None, bearing=None, status=None, stamp=1_800_000_000,
                   deleted=False):
    position = field(1, float(lat)) + field(2, float(lon)) + (field(3, float(bearing)) if bearing is not None else b"") \
        + (field(5, float(speed)) if speed is not None else b"")
    descriptor = field(1, entity_id) + (field(2, label) if label else b"") + (field(3, plate) if plate else b"")
    vehicle = field(1, field(5, route)) + field(2, position) + (field(4, status) if status is not None else b"") \
        + field(5, stamp) + field(8, descriptor)
    return field(2, field(1, entity_id) + (field(2, 1) if deleted else b"") + field(4, vehicle))


def feed_message(*entities, stamp=1_800_000_000):
    return field(1, field(1, "2.0") + field(3, stamp)) + b"".join(entities)


def test_gtfs_realtime_decoder_reads_positions_and_never_the_plate():
    content = feed_message(
        vehicle_entity("4631", 43.6535, -79.3830, route="505", label="4631", plate="ABC 123", speed=10.0, bearing=90.0, status=2),
        vehicle_entity("gone", 43.65, -79.38, deleted=True),
        vehicle_entity("dropout", 0.0, 0.0),  # GPS dropout reported as 0,0
        field(2, field(1, "alert-only")))  # an entity with no vehicle
    decoded = decode_vehicle_positions(content)
    assert decoded["timestamp"] == 1_800_000_000 and len(decoded["vehicles"]) == 1
    vehicle = decoded["vehicles"][0]
    assert vehicle["route"] == "505" and vehicle["id"] == "4631" and vehicle["status"] == "moving"
    assert vehicle["speed_kmh"] == 36.0 and vehicle["bearing"] == 90
    assert "ABC" not in str(decoded)
    with pytest.raises(ValueError):
        decode_vehicle_positions(feed_message(vehicle_entity("x", 1.0, 1.0))[:-3])  # truncated


def test_route_names_are_the_ones_riders_know():
    assert route_name("hamilton", "5688") == "2"
    assert route_name("rio_bus", "O0232AAA0A") == "232" and route_name("rio_brt", "O0232AAA0A") == "232"
    assert route_name("burlington", "351") == "1" and route_name("hamilton", "unknown-key") == "unknown-key"
    assert route_name("entur", "VYG:Line:R22") == "R22"
    assert route_name("entur", "NBU:Line:a7c15243-8873-4aee-a8b4-8d22e8ad31dc") == ""
    assert route_name("translink", "385-4") == "385" and route_name("ttc", "504") == "504"


def test_world_transit_reads_only_feeds_covering_the_place_and_credits_them():
    calls = []
    now = [1_800_000_030.0]
    content = feed_message(vehicle_entity("4631", 43.6535, -79.3830, route="504", speed=5.0),
                           vehicle_entity("old", 43.654, -79.383, route="501", stamp=1_800_000_030 - 900),
                           vehicle_entity("far", 43.80, -79.20, route="504"), stamp=1_800_000_030)

    def fetch(url, headers):
        calls.append((url, headers))
        return content if "ttc" in url else feed_message(stamp=1_800_000_030)  # MiWay answers with no vehicles here
    world = WorldTransit(fetch, clock=lambda: now[0])
    assert {feed.key for feed in world.feeds_near(43.6532, -79.3832, 2)} == {"ttc", "miway"}
    result = world.near(43.6532, -79.3832, 2)
    assert [item["id"] for item in result["vehicles"]] == ["4631", "old"]
    assert result["vehicles"][1]["stale"] and not result["vehicles"][0]["stale"]
    assert any("Toronto Transit Commission" in credit for credit in result["credits"])
    assert world.near(43.6532, -79.3832, 2, route="504")["vehicles"][0]["id"] == "4631"
    assert len(calls) == 2  # each feed once: the second question was answered from the 20-second cache
    assert not world.feeds_near(51.5074, -0.1278, 5)  # London: no open feed
    entur = next(feed for feed in FEEDS if feed.key == "entur")
    world.snapshot(entur)
    assert calls[-1][1] == {"ET-Client-Name": "hayat-assistant"}  # Entur requires consumers to identify themselves


def test_world_transit_keeps_its_rate_limit_and_reports_a_failing_operator():
    now = [0.0]

    def fetch(url, headers):
        if "miapp" in url:
            raise ConnectionError("down")
        return feed_message(vehicle_entity("1", 43.6535, -79.3830, route="1"), stamp=0)
    world = WorldTransit(fetch, clock=lambda: now[0])
    result = world.near(43.6532, -79.3832, 2)
    assert result["feeds"] == ["TTC"] and "MiWay" in result["errors"]
    ttc = next(feed for feed in FEEDS if feed.key == "ttc")
    for _read in range(WorldTransit.PER_MINUTE - 1):  # near() above was the first read this minute
        world._cache.clear()
        world.snapshot(ttc)
    world._cache.clear()
    with pytest.raises(RuntimeError, match="last minute"):
        world.snapshot(ttc)
    now[0] = 61.0
    assert world.snapshot(ttc)["vehicles"]  # a minute later it reads again


def test_transit_tool_merges_operators_and_says_what_is_covered():
    live = LiveData(lambda url: pytest.fail(f"no JSON provider expected for Toronto, got {url}"))
    live.world_transit = WorldTransit(lambda url, headers: feed_message(vehicle_entity("9", 43.6535, -79.3830, route="5688"), stamp=0),
                                      clock=lambda: 10.0)
    value = transit_vehicles(live, resolve, "toronto", 1)
    assert value["count"] == 2 and value["operators"] == ["TTC", "MiWay"]  # both Toronto-area feeds answered with the fixture
    reply = explore_report(value)
    assert "live vehicles within 1 km of Toronto" in reply and "Data:" in reply
    with pytest.raises(ValueError, match="Covered: .*Toronto"):
        transit_vehicles(live, resolve, "london", 2)
    assert "Rio de Janeiro, Brazil (Rio buses)" in coverage()


# ------------------------------------------------------------------ GPS interference
def aircraft(hex_id, lat, lon, *, nic=8, nacp=10, kind="adsb_icao", version=2, seen=1.0, alt=35000, **extra):
    return {"hex": hex_id, "lat": lat, "lon": lon, "nic": nic, "nac_p": nacp, "type": kind, "version": version,
            "seen_pos": seen, "alt_baro": alt, **extra}


def test_classifier_follows_the_published_rule_and_skips_what_says_nothing():
    rows = classify({"ac": [
        aircraft("aaaaa1", 54.1, 19.1),                               # healthy
        aircraft("aaaaa2", 54.1, 19.2, nacp=5),                       # NACp below 8
        aircraft("aaaaa3", 54.1, 19.3, nic=0, nacp=10),               # NIC 0 with good NACp: not degraded
        aircraft("aaaaa4", 54.6, 19.4, nic=8, nacp=9, gpsOkBefore=1.0, gpsOkLat=54.2, gpsOkLon=19.4),  # GPS lost
        aircraft("aaaaa5", 54.1, 19.5, alt="ground"),                 # on the ground
        aircraft("aaaaa6", 54.1, 19.6, kind="mode_s"),                # no ADS-B quality fields
        aircraft("aaaaa7", 54.1, 19.7, seen=120),                     # position too old
        aircraft("aaaaa8", 54.1, 19.8, nic=2, mlat=["nic"], nacp=None),  # derived NIC, nothing aircraft-reported
        aircraft("aaaaa2", 54.1, 19.2)]})                             # duplicate hex
    by_hex = {row["hex"]: row for row in rows}
    assert set(by_hex) == {"aaaaa1", "aaaaa2", "aaaaa3", "aaaaa4"}
    assert [by_hex[key]["degraded"] for key in ("aaaaa1", "aaaaa2", "aaaaa3", "aaaaa4")] == [False, True, False, True]
    assert by_hex["aaaaa4"]["latitude"] == 54.2  # evidence placed where GPS was last good
    assert classify({"nope": []}) is None
    assert level(10, 1) == (0.0, "low") and level(10, 2)[1] == "medium" and level(10, 3)[1] == "high"


def test_window_counts_each_aircraft_once_per_cell_and_ages_out():
    store = Observations()
    healthy = [{"hex": f"b{i:05d}", "latitude": 54.1, "longitude": 19.1, "degraded": False} for i in range(8)]
    store.add(healthy + [{"hex": "bad001", "latitude": 54.1, "longitude": 19.1, "degraded": True},
                         {"hex": "bad002", "latitude": 54.1, "longitude": 19.1, "degraded": True}], 1000.0, 1000.0)
    store.add([{"hex": "bad001", "latitude": 54.1, "longitude": 19.1, "degraded": False}], 1100.0, 1100.0)  # recovered, still counted
    cell = store.cells()[0]
    assert (cell["aircraft"], cell["degraded"], cell["percent"], cell["level"]) == (10, 2, 10.0, "medium")
    assert store.area(54.1, 19.1, 100)["aircraft"] == 10
    store.prune(1000.0 + 1801)
    assert store.area(54.1, 19.1, 100)["aircraft"] == 1  # only bad001, seen again at 1100, is still in the window...
    assert store.cells() == []                            # ...and one aircraft is below a cell's minimum of 3
    store.prune(1100.0 + 1801)
    assert store.area(54.1, 19.1, 100)["aircraft"] == 0


def test_gps_interference_answers_with_cells_area_and_caveat_and_respects_limits():
    now = [1_800_000_000.0]
    calls = []
    planes = [aircraft(f"c{i:05d}", 54.10 + i * .001, 19.10, nacp=5 if i < 4 else 10) for i in range(10)]

    def get_json(url):
        calls.append(url)
        return {"ac": planes, "now": now[0] * 1000}
    gnss = GpsInterference(get_json, resolve, clock=lambda: now[0])
    value = gnss.around("gdansk", 300)
    assert calls == ["https://api.adsb.lol/v2/lat/54/lon/19/dist/250"]
    assert value["high"] == 1 and value["anomalies"][0]["percent"] == 30.0 and value["area"]["degraded"] == 4
    reply = explore_report(value)
    assert "4 of 10 aircraft" in reply and CAVEAT in reply and "jamming" in reply.lower()
    gnss.around("gdansk")
    assert len(calls) == 1  # the same anchor is read at most once a minute
    gnss._read.clear()
    gnss._calls = [now[0]] * gnss.PER_MINUTE
    limited = gnss.around("gdansk")  # budget spent: answers from the window and says so
    assert limited["not_refreshed"] and "Not refreshed" in explore_report(limited)
    fresh = GpsInterference(get_json, resolve, clock=lambda: now[0])
    fresh._calls = [now[0]] * fresh.PER_MINUTE
    with pytest.raises(RateLimited):
        fresh.around("gdansk")  # nothing in the window to fall back on


def test_a_429_starts_a_cooldown_and_the_world_sample_reports_regions():
    import httpx
    now = [100.0]
    response = httpx.Response(429, headers={"retry-after": "120"}, request=httpx.Request("GET", "https://api.adsb.lol/"))
    gnss = GpsInterference(lambda url: (_ for _ in ()).throw(httpx.HTTPStatusError("429", request=response.request, response=response)),
                           resolve, clock=lambda: now[0])
    with pytest.raises(httpx.HTTPStatusError):
        gnss.scan(54, 19)
    assert gnss._cooldown_until == 220.0
    with pytest.raises(RateLimited, match="wait"):
        gnss.scan(30, 30)
    world = GpsInterference(lambda url: {"ac": [aircraft(f"d{i:05d}", 56.1, 21.1, nacp=5 if i < 3 else 10) for i in range(6)],
                                         "now": 100_000}, resolve, clock=lambda: 100.0)
    value = world.around("world")
    assert value["explore"] == "gnss_world" and value["regions"][0]["region"] == "Baltic Sea"
    assert "sampled regions" in explore_report(value)


# ------------------------------------------------------------------ airspace
SQUARE = {"type": "Polygon", "coordinates": [[[-77.05, 38.89], [-77.02, 38.89], [-77.02, 38.91], [-77.05, 38.91], [-77.05, 38.89]]]}
FAR = {"type": "Polygon", "coordinates": [[[-77.30, 38.80], [-77.28, 38.80], [-77.28, 38.82], [-77.30, 38.82], [-77.30, 38.80]]]}


def faa(url, size):
    if "Special_Use_Airspace" in url:
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": SQUARE, "properties": {"NAME": "P-56A", "TYPE_CODE": "P", "LOWER_VAL": "0", "LOWER_CODE": "SFC",
                                                                   "UPPER_VAL": "18000", "UPPER_UOM": "FT", "UPPER_CODE": "MSL",
                                                                   "TIMESOFUSE": "CONTINUOUS", "CITY": "Washington", "STATE": "DC"}},
            {"type": "Feature", "geometry": FAR, "properties": {"NAME": "R-9999", "TYPE_CODE": "R", "LOWER_VAL": "0",
                                                                "UPPER_VAL": "-9998", "UPPER_UOM": "FT", "UPPER_CODE": "MSL"}}]}
    if "geoserver" in url:
        return {"features": [{"geometry": SQUARE, "properties": {"NOTAM_KEY": "4/9383-1-FDC-F", "TITLE": "WASHINGTON, DC"}},
                             {"geometry": SQUARE, "properties": {"NOTAM_KEY": "4/9383-2-FDC-F", "TITLE": "WASHINGTON, DC"}},
                             {"geometry": FAR, "properties": {"NOTAM_KEY": "1/1111-1-FDC-F", "TITLE": "Expired"}}]}
    if "exportTfrList" in url:
        return [{"notam_id": "4/9383", "type": "SECURITY", "description": "Washington, DC special flight rules", "state": "DC"}]
    raise AssertionError(url)


def test_geometry_helpers():
    assert contains(SQUARE, 38.90, -77.03) and not contains(SQUARE, 38.95, -77.03)
    holed = {"type": "Polygon", "coordinates": [SQUARE["coordinates"][0], [[-77.04, 38.895], [-77.03, 38.895], [-77.03, 38.905],
                                                                           [-77.04, 38.905], [-77.04, 38.895]]]}
    assert not contains(holed, 38.90, -77.035)
    assert 1.0 < edge_km(SQUARE, 38.90, -77.07) < 2.0
    assert altitude("-9998", "FT", "MSL") == "unlimited" and altitude("0", None, "SFC") == "surface"
    assert altitude("1500", "FT", "AGL") == "1500 ft above ground" and altitude("180", "FL", "STD") == "FL180"
    assert in_us(38.9, -77.0) and in_us(21.3, -157.8) and not in_us(51.5, -0.1)


def test_airspace_check_names_zones_once_and_claims_only_what_was_checked(tmp_path):
    space = Airspace(faa, tmp_path, resolve)
    value = space.check("washington", 40)
    assert [zone["kind"] for zone in value["inside"]] == ["Prohibited area", "Temporary flight restriction (Security)"]
    assert value["inside"][0]["floor"] == "surface" and value["inside"][0]["ceiling"] == "18000 ft MSL"
    assert value["nearby"][0]["name"] == "R-9999" and value["nearby"][0]["ceiling"] == "unlimited"
    assert all(zone.get("notam") != "1/1111" for zone in value["inside"] + value["nearby"])  # unlisted NOTAM: expired
    reply = explore_report(value)
    assert "INSIDE 2 restricted zones" in reply and "detail_4_9383" in reply and "not a flight briefing" in reply
    with pytest.raises(ValueError, match="US only"):
        space.check("london")
    # The FAA's shared quota refuses: the TFR half answers, and the reply does not claim the other half.
    refusing = Airspace(lambda url, size: {"error": {"message": "Too many requests"}} if "Special_Use" in url else faa(url, size),
                        tmp_path / "other", resolve)
    partial = refusing.check("fresno", 20)
    text = explore_report(partial)
    assert "not inside any temporary flight restriction." in text and "prohibited or restricted area" not in text.split("\n")[0]
    assert "unknown, not clear" in text


def test_airspace_serves_a_stale_copy_and_says_so(tmp_path):
    import os
    clock = [1000.0]
    space = Airspace(faa, tmp_path, resolve, clock=lambda: clock[0])
    space.check("washington")
    for saved in tmp_path.glob("airspace-*.json"):
        os.utime(saved, (1000.0, 1000.0))  # written at the fake clock's "now"
    clock[0] += airspace_module.SUA_TTL + 3600
    space._memory.clear()
    space.get_json = lambda url, size: (_ for _ in ()).throw(ConnectionError("FAA down"))
    value = space.check("washington")
    assert value["stale_hours"]["restricted areas"] == 25.0 and "copy from 25 h ago" in explore_report(value)
    clock[0] += airspace_module.SUA_STALE
    with pytest.raises(ValueError, match="did not answer"):
        space.check("washington")  # too old to use: an error, not an old answer


# ------------------------------------------------------------------ ground
class FakeExplore:
    def __init__(self, documents, folder, images=b""):
        self.documents, self.folder, self.images, self.saved = documents, Path(folder), images, []
        self.fetcher = self

    def _place(self, place):
        found = resolve(place)
        return {"name": found["name"], "latitude": found["latitude"], "longitude": found["longitude"], "country_code": ""}

    def _json(self, key, url, ttl, max_bytes=0, disk=False):
        return next(value for prefix, value in self.documents.items() if url.startswith(prefix))

    def fetch(self, url, max_bytes, accept):
        return type("R", (), {"content": self.images})()

    def _save(self, content, prefix):
        path = self.folder / f"{prefix}.png"
        path.write_bytes(content)
        self.saved.append(content)
        return path


def test_fire_perimeters_inside_edge_and_stale(tmp_path):
    ring = [[-119.80, 36.72], [-119.76, 36.72], [-119.76, 36.76], [-119.80, 36.76], [-119.80, 36.72]]
    far = [[-119.0, 37.5], [-118.9, 37.5], [-118.9, 37.6], [-119.0, 37.6], [-119.0, 37.5]]
    documents = {"https://services3.arcgis.com": {"features": [
        {"geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {
            "poly_IncidentName": "Fixture", "attr_IncidentTypeCategory": "WF", "attr_IncidentSize": 1200.0, "attr_PercentContained": 20,
            "attr_POOState": "US-CA", "poly_DateCurrent": 4_000_000_000_000, "attr_FireCause": "Human"}},
        {"geometry": {"type": "MultiPolygon", "coordinates": [[far]]}, "properties": {
            "poly_IncidentName": "Old burn", "attr_IncidentTypeCategory": "RX", "poly_DateCurrent": 1_000_000_000_000}}]}}
    ground = Ground(FakeExplore(documents, tmp_path))
    value = ground.fire_perimeters("fresno", 200)
    assert value["inside"] and value["fires"][0]["name"] == "Fixture" and value["fires"][0]["edge_km"] == 0.0
    old = value["fires"][1]
    assert old["kind"] == "prescribed burn" and old["perimeter_stale"] and old["direction"] == "NE"
    assert "INSIDE one" in explore_report(value)
    assert len(ground.fire_perimeters("fresno", 200, include_prescribed=False)["fires"]) == 1


def test_whale_sightings_drop_observers_and_round_places(tmp_path):
    from datetime import UTC, datetime, timedelta
    recent = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now(UTC) - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"type": "Southern Resident Orca", "no_sighted": 12, "created": recent, "latitude": 48.51234, "longitude": -123.05678,
             "trusted": 1, "data_source_comments": "J pod heading north (Jane Observer)", "data_source_witness": "jane",
             "submitter_did": "did:ethr:0xabc", "data_source_name": "Orca Network"},
            {"type": "Humpback", "created": old, "latitude": 48.5, "longitude": -123.0}]
    ground = Ground(FakeExplore({"https://acartia.io": rows}, tmp_path))
    value = ground.whale_sightings("friday harbor", 60, 3)
    assert value["count"] == 1 and value["sightings"][0]["latitude"] == 48.51 and value["sightings"][0]["longitude"] == -123.06
    assert "Jane" not in str(value) and "did:ethr" not in str(value) and "jane" not in str(value)
    assert "Southern Resident Orca ×12" in explore_report(value)
    with pytest.raises(ValueError, match="Salish Sea"):
        ground.whale_sightings("london")


def test_street_photo_keeps_panoramax_media_and_no_email_credit(tmp_path):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (2048, 1024), "gray").save(buffer, "JPEG")
    feature = {"id": "pic-1", "geometry": {"coordinates": [2.2946, 48.8585]},
               "assets": {"sd": {"href": "https://panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net/derivates/x/sd.jpg"}},
               "providers": [{"name": "someone@example.org"}, {"name": "Ville de Paris"}],
               "properties": {"datetime": "2025-10-10T10:00:00Z", "license": "etalab-2.0", "view:azimuth": 45}}
    elsewhere = {**feature, "id": "pic-2", "assets": {"sd": {"href": "https://evil.example.com/sd.jpg"}}}
    fake = FakeExplore({"https://api.panoramax.xyz": {"features": [elsewhere, feature]}}, tmp_path, buffer.getvalue())
    value = Ground(fake).street_photo("paris", 200)
    assert value["page"].endswith("pic=pic-1") and value["facing"] == "NE" and value["license"] == "etalab-2.0"
    assert value["author"] == "a Panoramax contributor, Ville de Paris" and "@" not in explore_report(value)
    with Image.open(io.BytesIO(fake.saved[0])) as saved:
        assert saved.format == "PNG" and saved.width == 1280
    with pytest.raises(ValueError, match="No open street-level photo"):
        Ground(FakeExplore({"https://api.panoramax.xyz": {"features": [elsewhere]}}, tmp_path)).street_photo("paris")


# ------------------------------------------------------------------ chat routing
@pytest.mark.parametrize("text,tool,arguments", [
    ("Where are the buses in Toronto right now?", "transit_vehicles", {"place": "Toronto"}),
    ("live trams near Amsterdam", "transit_vehicles", {"place": "Amsterdam"}),
    ("Where is bus 504 in Toronto?", "transit_vehicles", {"place": "Toronto", "route": "504", "radius_km": 25}),
    ("Is there GPS jamming near Gdansk?", "gps_interference", {"place": "Gdansk"}),
    ("Where is GPS being jammed right now?", "gps_interference", {"place": "world"}),
    ("gps interference", "gps_interference", {"place": "world"}),
    ("Is GPS being spoofed over the Black Sea?", "gps_interference", {"place": "the Black Sea"}),
    ("Can I fly a drone in Washington DC?", "airspace_check", {"place": "Washington DC"}),
    ("Are there any TFRs near Las Vegas?", "airspace_check", {"place": "Las Vegas"}),
    ("no fly zones near Seattle", "airspace_check", {"place": "Seattle"}),
    ("Show fire perimeters near Fresno", "fire_perimeters", {"place": "Fresno"}),
    ("How big is the fire near Paradise?", "fire_perimeters", {"place": "Paradise"}),
    ("Any orca sightings near Friday Harbor?", "whale_sightings", {"place": "Friday Harbor"}),
    ("Are there whales near Seattle?", "whale_sightings", {"place": "Seattle"}),
    ("Show me a street view of the Eiffel Tower", "street_photo", {"place": "the Eiffel Tower"}),
    ("What does Times Square look like from the street?", "street_photo", {"place": "Times Square"}),
])
def test_everyday_phrasings_route_to_the_new_tools(text, tool, arguments):
    assert explore_request(text) == {"tool": tool, "arguments": arguments}


def test_the_new_phrasings_reach_the_tools_through_the_whole_router():
    from app.intelligence.tools import direct_plan
    names = ["transit_vehicles", "gps_interference", "airspace_check", "fire_perimeters", "whale_sightings", "street_photo",
             "intelligence_around", "intelligence_refresh", "intelligence_events", "fire_clusters", "intelligence_deep_look"]
    for text, tool in (("Where are the buses in Oslo?", "transit_vehicles"), ("Is there GPS jamming near Riga?", "gps_interference"),
                       ("Can I fly a drone in Denver?", "airspace_check"), ("Show fire perimeters near Fresno", "fire_perimeters"),
                       ("Any whale sightings near Victoria?", "whale_sightings"), ("Street view of Big Ben", "street_photo")):
        assert direct_plan(text, names)["tool"] == tool, text


# ------------------------------------------------------------------ the Explore tab
from tests.test_gui_intelligence import explorer, pump_until  # noqa: E402,F401 -- fixture


def test_explore_tab_runs_the_new_buttons_and_shows_the_street_photo(explorer, tmp_path):  # noqa: F811
    from PIL import Image
    from app.gui_intelligence_explore import ACTIONS, ICONS
    from app.intelligence.ground import register_ground_tools
    window, gui = explorer
    labels = [label for label, _tool, _build in ACTIONS]
    for label in ('Live transit', 'GPS interference', 'Airspace (US)', 'Fire perimeters (US)', 'Whale sightings', 'Street photo'):
        assert label in labels and label in ICONS and label in window.explore_buttons
    window.notebook.select(window.tabs['explore'])
    window.explore_place.set('paris')
    assert window.explore_run('Live transit') is False and 'not loaded' in window.explore_status.get()
    buffer = io.BytesIO()
    Image.new('RGB', (800, 400), 'navy').save(buffer, 'JPEG')
    feature = {'id': 'pic-9', 'geometry': {'coordinates': [2.2946, 48.8585]},
               'assets': {'sd': {'href': 'https://panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net/x/sd.jpg'}},
               'providers': [{'name': 'Ville de Paris'}], 'properties': {'datetime': '2025-10-10T10:00:00Z', 'license': 'etalab-2.0'}}
    register_ground_tools(gui.runtime.registry, Ground(FakeExplore({'https://api.panoramax.xyz': {'features': [feature]}}, tmp_path,
                                                                   buffer.getvalue())))
    assert window.explore_run('Street photo')
    pump_until(window, lambda: not window._busy and window._explore_pending is None)
    assert 'Street photo' in window.explore_text.get('1.0', 'end') and window._explore_photo is not None
    assert 'Panoramax' in window.explore_status.get()


# ------------------------------------------------------------------ the deep look
def test_deep_look_reads_the_new_layers_and_turns_them_into_findings():
    from types import SimpleNamespace
    from app.intelligence.deep import DeepLook, deep_report
    asked = []
    where = {"fresno": {"name": "Fresno", "latitude": 36.74, "longitude": -119.78}, "london": {"name": "London", "latitude": 51.5, "longitude": -0.12}}
    fusion = SimpleNamespace(around=lambda place, radius_km=25, **_: {"place": where[place], "observed_at": "2026-11-06T12:00:00+00:00",
                                                                      "events": [], "weather": {}})
    sky = SimpleNamespace(fire_clusters=lambda *a, **k: {"fires": []}, satellite_pass=lambda *a, **k: {},
                          launches=lambda *a, **k: {"launches": []})
    manager = SimpleNamespace(
        fusion=fusion,
        gnss=SimpleNamespace(around=lambda name, radius: asked.append("gnss") or {
            "here": {"level": "high", "degraded": 5, "aircraft": 12, "percent": 33.3}, "area": {}}),
        airspace=SimpleNamespace(check=lambda name, radius: asked.append("airspace") or {"inside": [
            {"kind": "Temporary flight restriction (Hazards)", "name": "Fresno fire TFR", "notam": "6/1234", "description": "Fire fighting"}]}),
        ground=SimpleNamespace(fire_perimeters=lambda name, radius: asked.append("perimeters") or {"fires": [
            {"name": "Fixture", "kind": "wildfire", "inside": True, "edge_km": 0.0, "direction": None, "acres": 1200.0,
             "contained_pct": 20, "perimeter_updated": "2026-11-05T10:00", "perimeter_stale": False, "state": "CA"}]}))
    deep = DeepLook(manager, sky)
    gathered = deep.gather("fresno", 25, 0)
    assert sorted(asked) == ["airspace", "gnss", "perimeters"]
    findings = {item["title"]: item for item in DeepLook.reason(gathered, 25)}
    assert findings["Inside the Fixture fire perimeter"]["level"] == "alert"
    assert findings["Inside temporary flight restriction (hazards): Fresno fire TFR"]["level"] == "watch"
    gps = findings["Aircraft here report degraded satellite navigation"]
    assert gps["level"] == "watch" and gps["confidence"] == "low" and "possible cause" in gps["detail"]
    asked.clear()
    deep.gather("london", 25, 0)
    assert asked == ["gnss"]  # FAA airspace and NIFC perimeters cover the US only
    value = deep.look("fresno", 25, 0)
    assert {"gnss", "airspace", "perimeters"} <= set(value["sources_read"]) and "fire perimeter" in deep_report(value)


# ------------------------------------------------------------------ the atlas
def test_the_atlas_draws_gps_cells_and_names_transit_operators():
    import json as json_module
    import urllib.request
    from types import SimpleNamespace
    from app.intelligence.map_server import IntelligenceMapServer
    planes = [aircraft(f"e{i:05d}", 54.10 + i * .001, 19.10, nacp=5 if i < 3 else 10) for i in range(8)]
    gnss = GpsInterference(lambda url: {"ac": planes, "now": 1_800_000_000_000}, resolve, clock=lambda: 1_800_000_000.0)
    live = LiveData(lambda url: pytest.fail(url))
    live.world_transit = WorldTransit(lambda url, headers: feed_message(vehicle_entity("9", 43.6535, -79.3830, route="504"), stamp=0)
                                      if "ttc" in url else feed_message(stamp=0), clock=lambda: 10.0)
    server = IntelligenceMapServer(SimpleNamespace(gnss=gnss, live=live))
    try:
        with urllib.request.urlopen(server.url + "gnss?lat=54.3&lon=18.6", timeout=10) as response:
            value = json_module.loads(response.read())
        assert len(value["cells"]) == 1 and value["cells"][0]["level"] == "high" and "possible cause" in value["caveat"]
        with urllib.request.urlopen(server.url + "live?kind=transit&lat=43.6532&lon=-79.3832&radius_km=2", timeout=10) as response:
            transit = json_module.loads(response.read())
        assert transit["count"] == 1 and transit["source"] == "TTC"
    finally:
        server.close()
    root = Path(__file__).parents[1] / "app/assets/intelligence-map"
    assert 'id="gnss"' in (root / "index.html").read_text(encoding="utf-8")
    script = (root / "map.js").read_text(encoding="utf-8")
    assert "gnss?lat=" in script and "refreshGnss" in script and "item.operator + ':'" in script


# ------------------------------------------------------------------ finding places: "Washington DC", landmarks
def test_geocode_retries_with_the_region_as_a_filter_and_falls_back_to_landmarks():
    from urllib.parse import parse_qs, urlsplit
    asked = []

    def open_meteo(url):
        query = parse_qs(urlsplit(url).query)
        name, count = query["name"][0], int(query["count"][0])
        asked.append((name, count))
        towns = {"Washington": [{"name": "Washington", "admin1": "Pennsylvania", "country_code": "US", "latitude": 40.17, "longitude": -80.24},
                                {"name": "Washington D.C.", "admin1": "District of Columbia", "country": "United States",
                                 "country_code": "US", "latitude": 38.895, "longitude": -77.036}],
                 "Paris": [{"name": "Paris", "admin1": "Texas", "country_code": "US", "latitude": 33.66, "longitude": -95.55},
                           {"name": "Paris", "admin1": "Île-de-France", "country": "France", "country_code": "FR",
                            "latitude": 48.85, "longitude": 2.35}]}
        return {"results": towns.get(name, [])[:count]}
    live = LiveData(open_meteo)
    live.landmarks = lambda name: asked.append(("landmark", name)) or {"name": "Eiffel Tower", "latitude": 48.858, "longitude": 2.294}
    assert live.geocode("Washington DC")["region"] == "District of Columbia"
    assert asked[:2] == [("Washington DC", 1), ("Washington", 10)]
    assert live.geocode("Paris, France")["country_code"] == "FR" and live.geocode("Paris TX")["region"] == "Texas"
    asked.clear()
    assert live.geocode("the Eiffel Tower")["latitude"] == 48.858 and ("landmark", "Eiffel Tower") in asked
    live.landmarks = lambda name: (_ for _ in ()).throw(ConnectionError("Wikidata down"))
    assert live.geocode("Nowhere Landmark") is None  # a failed lookup is "not found", never a crash


def test_wikidata_place_needs_a_coordinate_on_earth():
    from app.intelligence.live import wikidata_place
    search = {"search": [{"id": "Q3533063"}, {"id": "Q999"}, {"id": "Q243"}, {"id": "not-an-id"}]}
    entities = {"entities": {
        "Q3533063": {"claims": {}, "labels": {"en": {"value": "Eiffel Tower with Trees"}}},       # a painting
        "Q999": {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 1, "longitude": 2,
                                                                          "globe": "http://www.wikidata.org/entity/Q111"}}}}]}},  # on Mars
        "Q243": {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 48.858296, "longitude": 2.294479,
                                                                          "globe": "http://www.wikidata.org/entity/Q2"}}}}]},
                 "labels": {"en": {"value": "Eiffel Tower"}}, "descriptions": {"en": {"value": "tower in Paris"}}}}}
    urls = []
    place = wikidata_place(lambda url: urls.append(url) or (search if "wbsearchentities" in url else entities), "Eiffel Tower")
    assert place["name"] == "Eiffel Tower" and place["latitude"] == 48.858296 and place["source"] == "Wikidata"
    assert "ids=Q3533063|Q999|Q243" in urls[1]
    assert wikidata_place(lambda url: {"search": []}, "zzzz") is None


def test_ovapi_internal_route_keys_are_not_shown_as_line_numbers():
    assert route_name("ovapi", "152342") == "" and route_name("ovapi", "M51") == "M51"
