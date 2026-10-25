"""The live intelligence network: on-demand providers, fusion around a place, change over time, one source catalog."""
from datetime import UTC, datetime, timedelta

import pytest

from app.intelligence.catalog import TYPES, source_catalog
from app.intelligence.feeds import BY_KEY
from app.intelligence.fusion import Fusion, camera_score, fusion_request, place_key, register_fusion_tools, reverse_place
from app.intelligence.live import PROVIDERS, LiveData, RateLimited
from app.intelligence.manager import IntelligenceManager
from app.intelligence.tools import direct_plan, register_intelligence_tools, report
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_intelligence_resources import ExtendedFetcher

#: ISS elements in CelesTrak's OMM JSON shape (values close to the real orbit).
ISS_OMM = {"OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-10-05T12:00:00.000000",
           "MEAN_MOTION": 15.50103472, "ECCENTRICITY": 0.0006703, "INCLINATION": 51.6416, "RA_OF_ASC_NODE": 247.4627,
           "ARG_OF_PERICENTER": 130.536, "MEAN_ANOMALY": 325.0288, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U",
           "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999, "REV_AT_EPOCH": 49000, "BSTAR": 0.0001,
           "MEAN_MOTION_DOT": 0.00002, "MEAN_MOTION_DDOT": 0}


class FakeProviders:
    """Answers each keyless provider the way it answered live on 2026-10-06, around Minneapolis."""

    def __init__(self):
        self.urls, self.fail = [], set()

    def __call__(self, url):
        self.urls.append(url)
        if any(part in url for part in self.fail):
            raise ConnectionError("provider down")
        if "geocoding-api.open-meteo.com" in url:
            return {"results": [{"name": "Minneapolis", "latitude": 44.98, "longitude": -93.26, "country": "United States",
                                 "country_code": "US", "admin1": "Minnesota", "population": 425336}]}
        if "api.met.no" in url:
            return {"properties": {"timeseries": [{"time": "2026-10-06T06:00:00Z", "data": {
                "instant": {"details": {"air_temperature": 9.5, "wind_speed": 4.1, "relative_humidity": 71}},
                "next_1_hours": {"summary": {"symbol_code": "rain"}, "details": {"precipitation_amount": 1.2}}}}]}}
        if "mesonet.agron.iastate.edu" in url:
            assert "network=MN_ASOS" in url
            return {"data": [{"station": "MSP", "name": "Minneapolis-St Paul", "lat": 44.88, "lon": -93.22,
                              "utc_valid": "2026-10-06T05:53:00Z", "tmpf": 49.0, "sknt": 8}]}
        if "data.sensor.community" in url:
            return [{"sensor": {"id": 1}, "sensordatavalues": [{"value_type": "P2", "value": "8.0"},
                                                                  {"value_type": "P1", "value": "12"}]},
                    {"sensor": {"id": 2}, "sensordatavalues": [{"value_type": "P2", "value": "12.0"}]}]
        if "waterservices.usgs.gov" in url:
            return {"value": {"timeSeries": [{"sourceInfo": {"siteName": "MISSISSIPPI RIVER AT ST. PAUL, MN",
                "geoLocation": {"geogLocation": {"latitude": 44.94, "longitude": -93.09}}},
                "variable": {"variableCode": [{"value": "00065"}]},
                "values": [{"value": [{"value": "6.2", "dateTime": "2026-10-06T05:45:00Z"}]}]}]}}
        if "api.adsb.lol" in url:
            return {"ac": [{"hex": "a1b2c3", "flight": "DAL123 ", "lat": 44.9, "lon": -93.2, "alt_baro": 12000, "gs": 300},
                           {"hex": "a4b5c6", "flight": "N123", "lat": 45.0, "lon": -93.3, "alt_baro": 3000,
                            "emergency": "general"},
                           {"hex": "a7b8c9", "r": "N9", "lat": 44.88, "lon": -93.22, "alt_baro": "ground"}]}
        if "epqs.nationalmap.gov" in url:
            return {"value": 264.1}
        if "celestrak.org" in url:
            return [ISS_OMM]
        if "bigdatacloud" in url:
            return {"countryCode": "US", "principalSubdivision": "Minnesota", "locality": "Minneapolis"}
        raise AssertionError(f"unexpected provider URL {url}")


@pytest.fixture
def network(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    manager = IntelligenceManager(tmp_path / "intelligence.db", ExtendedFetcher(), registry, extended=True)
    register_intelligence_tools(registry, manager)
    manager.refresh()
    providers = FakeProviders()
    manager.live = LiveData(providers)
    manager.fusion = Fusion(manager, manager.live, my_location=lambda: {"latitude": 44.98, "longitude": -93.26,
                                                                      "place": {"label": "Minneapolis"}},
                            reverse=lambda latitude, longitude: reverse_place(providers, latitude, longitude))
    register_fusion_tools(registry, manager.fusion, lambda type="": source_catalog(manager, manager.live, type))
    try:
        yield manager, providers, registry
    finally:
        manager.close()


def test_providers_are_rate_limited_cached_and_health_checked():
    calls, clock = [], [1000.0]
    live = LiveData(lambda url: calls.append(url) or {"ac": []}, clock=lambda: clock[0])
    assert live.aircraft(44.98, -93.26) == [] and live.aircraft(44.98, -93.26) == []
    assert len(calls) == 1  # the second answer came from the 15-second cache
    for step in range(PROVIDERS["adsb"]["per_minute"] - 1):
        live.aircraft(10 + step, 10)
    with pytest.raises(RateLimited):
        live.aircraft(50, 50)
    assert live.health["adsb"]["ok"]
    clock[0] += 61
    assert live.aircraft(50, 50) == []
    failing = LiveData(lambda url: (_ for _ in ()).throw(ConnectionError("down")))
    with pytest.raises(ConnectionError):
        failing.weather(1, 1)
    assert failing.health["met"]["ok"] is False and "ConnectionError" in failing.health["met"]["error"]


def test_providers_without_coverage_say_so_instead_of_guessing():
    live = LiveData(lambda url: pytest.fail(f"no request expected, got {url}"))
    assert live.vessels(44.98, -93.26) == []   # the only keyless AIS feed is the Baltic
    assert live.transit(44.98, -93.26) == []   # no keyless vehicle feed for Minneapolis
    assert live.stations(44.98, -93.26, "") == []


def test_iss_position_is_computed_from_published_elements():
    position = LiveData(lambda url: [ISS_OMM]).iss()
    assert 300 < position["altitude_km"] < 500 and -52 <= position["latitude"] <= 52
    assert -180 <= position["longitude"] <= 180 and "SGP4" in position["source"]


def test_everything_around_a_place_is_one_combined_answer(network):
    manager, providers, _registry = network
    value = manager.fusion.around("Minneapolis", radius_km=30)
    assert value["place"]["state_code"] == "MN" and value["radius_km"] == 30
    assert "roads_cars_mn:mn-1" in [row["id"] for row in value["events"]]
    assert value["weather"]["temperature_c"] == 9.5 and value["stations"][0]["station"] == "MSP"
    assert value["air"]["sensors"] == 2 and value["gauges"][0]["gauge_height_ft"] == 6.2
    assert value["elevation"] == 264.1 and len(value["aircraft"]) == 3
    text = value["interpretation"]
    assert "Around Minneapolis, Minnesota, United States (within 30 km)" in text
    assert "EMERGENCY squawk from N123" in text and "1.2 mm rain expected" in text
    assert "MN 10 closed" in text and "(MET Norway forecast)" in text
    assert any("Ships" in note for note in value["not_covered"])
    assert any("Transit" in note for note in value["not_covered"])
    stored = manager.store.observations(place_key(44.98, -93.26) + ":30km")  # counts depend on the radius
    assert stored and stored[0]["aircraft"] == 3 and stored[0]["road_closures"] == 1


def test_one_provider_failing_never_sinks_the_answer(network):
    manager, providers, _registry = network
    providers.fail = {"api.adsb.lol", "data.sensor.community"}
    value = manager.fusion.around(latitude=44.98, longitude=-93.26, radius_km=20, include=["aircraft", "air", "weather"])
    assert value["aircraft"] is None and "aircraft unavailable" in value["unavailable"]["aircraft"]
    assert value["weather"]["temperature_c"] == 9.5 and "Weather: 9.5°C" in value["interpretation"]
    assert "Unavailable right now (provider error or rate limit): air, aircraft." in value["interpretation"]


def test_what_changed_compares_an_hour_ago_yesterday_and_usual(network):
    manager, _providers, _registry = network
    moment = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    place = place_key(44.98, -93.26)
    history = [(timedelta(hours=1), 5), (timedelta(days=1), 4), (timedelta(days=2), 4), (timedelta(days=3), 5),
               (timedelta(days=4), 4)]
    for ago, aircraft in history:
        manager.store.save_observation(place, (moment - ago).isoformat(timespec="seconds"), {"aircraft": aircraft})
    changes = manager.fusion.compare(place, {"aircraft": 30, "temperature_c": 9.5}, moment)
    entry = next(item for item in changes if item["metric"] == "aircraft")
    assert entry["an_hour_ago"] == 5 and entry["yesterday"] == 4 and entry["usual"] == 4 and entry["unusual"]
    assert not any(item["metric"] == "temperature_c" for item in changes)  # nothing earlier to compare with
    assert manager.fusion.compare("0.00,0.00", {"aircraft": 1}, moment) == []


def test_best_camera_ranking_explains_itself():
    near_stream = {"feed": "cameras_delaware", "distance_km": 1.0, "details": {"video_url": "https://x/live.m3u8"}}
    far_still = {"feed": "cameras_spain", "distance_km": 20.0, "details": {"image_url": "https://x/a.jpg"}}
    reliability = {"cameras_delaware": 1.0, "cameras_spain": 0.4}
    best, reasons = camera_score(near_stream, reliability, 25)
    worse, _ = camera_score(far_still, reliability, 25)
    assert best > worse and "live video" in reasons and "network 100% reliable" in reasons


def test_source_catalog_describes_every_source_alike_and_why_each_type_helps(network):
    manager, _providers, registry = network
    catalog = source_catalog(manager, manager.live)
    required = {"provider", "types", "country", "coverage", "media_types", "url", "refresh_seconds", "authentication",
                "license", "reliability", "health", "last_checked", "last_success", "restrictions", "confidence"}
    assert catalog["sources"] and all(required <= set(profile) for profile in catalog["sources"])
    assert {entry["type"] for entry in catalog["types"]} >= {"LIVE_VIDEO", "SNAPSHOT", "TRAFFIC", "EVENT", "AIRCRAFT",
                                                              "VESSEL", "TRANSIT", "WEATHER", "SENSOR", "SATELLITE", "MAP"}
    assert all(entry["why"] == TYPES[entry["type"]]["why"] for entry in catalog["types"])
    assert 'NEWS' in {item['type'] for item in catalog['types']}
    assert 'NEWS' not in {item['type'] for item in catalog['missing_types']}
    roads = next(profile for profile in catalog["sources"] if profile["key"] == "roads_cars_mn")
    assert roads["health"] == "ok" and roads["reliability"] == 1.0 and roads["records"] == 1
    assert roads["coverage"]["located_records"] == 1 and roads["authentication"] == "none"
    only_aircraft = source_catalog(manager, manager.live, "aircraft")
    assert [profile["key"] for profile in only_aircraft["sources"]] == ["live:adsb", "live:adsb_global", "live:adsb_route"]
    with pytest.raises(ValueError):
        source_catalog(manager, manager.live, "mind reading")
    result = registry.execute("intelligence_sources", {})
    assert result.success
    text = report([{"tool": result.tool, "result": result.to_dict(), "verification": {"passed": True}}])
    assert "public sources available" in text and TYPES["AIRCRAFT"]["why"] in text


def test_observed_local_history_is_reported_without_claiming_worldwide_coverage(network, tmp_path):
    from app.intelligence.history import PositionHistory
    manager, _providers, _registry = network
    manager.live.recorder = PositionHistory(tmp_path / 'history.db')
    manager.live.recorder.record('aircraft', [{'hex': 'abc123', 'latitude': 44.9, 'longitude': -93.2}], 'public ADS-B')
    catalog = source_catalog(manager, manager.live, 'HISTORICAL')
    assert len(catalog['sources']) == 1
    profile = catalog['sources'][0]
    assert profile['records'] == 1 and not profile['official']
    assert 'previously observed' in profile['coverage']
    assert 'HISTORICAL' not in {item['type'] for item in catalog['missing_types']}


def test_keyed_sources_never_publish_the_owners_key(network, monkeypatch):
    manager, _providers, _registry = network
    monkeypatch.setenv("HAYAT_KEY_WSDOT", "secret-access-code")
    profiles = {profile["key"]: profile for profile in source_catalog(manager, manager.live)["sources"]}
    assert all("secret-access-code" not in str(profile) for profile in profiles.values())


def test_plain_requests_route_to_fusion_and_cameras_stay_with_the_camera_search(network):
    _manager, _providers, registry = network
    names = registry.names()
    plan = direct_plan("Show everything within 10 miles of Detroit", names)
    assert plan["tool"] == "intelligence_around" and plan["arguments"] == {"place": "Detroit", "radius_km": 16.1}
    plan = direct_plan("show aircraft, traffic and weather around Detroit", names)
    assert plan["arguments"]["include"] == ["aircraft", "events", "weather", "cameras"]
    assert direct_plan("What changed here during the last hour?", names)["tool"] == "intelligence_changes"
    assert direct_plan("What sources do you have and why are they useful?", names)["tool"] == "intelligence_sources"
    assert direct_plan("Which camera has the best view of Times Square?", names)["tool"] == "intelligence_best_camera"
    assert direct_plan("Find cameras near 40.71, -74.0 within 20 km", names)["tool"] == "intelligence_cameras"
    assert fusion_request("show me all your tools") is None and fusion_request("tell me everything about python") is None
    assert fusion_request("show me the world") is None and fusion_request("what is happening in the world") is None


def test_fusion_tools_run_through_the_registry_and_report_plainly(network):
    _manager, _providers, registry = network
    result = registry.execute("intelligence_around", {"place": "Minneapolis", "radius_km": 20})
    assert result.success, result.error
    text = report([{"tool": result.tool, "result": result.to_dict(), "verification": {"passed": True}}])
    assert "Around Minneapolis" in text and "Combined from public providers" in text
    here = registry.execute("intelligence_changes", {"place": "here"})
    assert here.success and here.output["place"]["name"].startswith("Minneapolis")
    best = registry.execute("intelligence_best_camera", {"latitude": 40.71, "longitude": -74.0})
    assert best.success and best.output["best_camera_only"] and set(best.output) >= {"cameras"}
    assert not registry.execute("intelligence_around", {"latitude": 95, "longitude": 0}).success


def test_road_events_carry_kind_and_urgency(network):
    from app.intelligence.classify import assess
    from app.intelligence.providers import road_event

    row = road_event(BY_KEY["roads_cars_co"], {"id": "c1", "tooltip": "I-70 closed",
                                              "representation": {"iconProperties": {"image": "tg_lane_closure_critical.png"}},
                                              "bounds": {"minLat": 39.6, "maxLat": 39.7, "minLon": -105.9, "maxLon": -105.8}})
    assert row["kind"] == "lane closure" and row["details"]["urgency"] == "critical"
    assert row["details"]["state"] == "Colorado" and assess(row)["severity"] == "high"
    assert assess(row)["category"] == "traffic"


def test_official_livestreams_route_and_play_through_the_resolver(network, monkeypatch):
    from app.intelligence import live
    from app.intelligence.camera_chat import camera_request

    manager, _providers, registry = network
    for phrase, stream in (("makkah live", "makkah"), ("play the kaaba live", "makkah"), ("nasa live", "nasa_iss"),
                           ("madinah live", "madinah")):
        assert camera_request(phrase) == ("intelligence_camera_play", {"event_id": f"livestreams:{stream}", "live_only": True})
    assert camera_request("makkah") is None
    monkeypatch.setattr(live, "resolve_livestream", lambda page: "https://manifest.googlevideo.com/api/manifest/hls/x.m3u8")
    played = registry.execute("intelligence_camera_play", {"event_id": "livestreams:makkah", "live_only": True})
    assert played.success and played.output["mode"] == "stream" and played.output["live"]
    assert played.output["video_url"].startswith("https://manifest.googlevideo.com/")

    def offline(page):
        raise ValueError("The channel is not broadcasting live right now.")

    monkeypatch.setattr(live, "resolve_livestream", offline)
    refused = registry.execute("intelligence_camera_play", {"event_id": "livestreams:makkah", "live_only": True})
    assert not refused.success or "not broadcasting" in str(refused.output)
    assert refused.success and refused.output['live_page'] == live.LIVESTREAMS[0]['page']
    assert refused.output['video_url'] == ''


def test_local_camera_catalog_updates_without_waiting_for_network_cooldown(network, monkeypatch):
    from app.intelligence import live
    manager, _providers, _registry = network
    original = live.LIVESTREAMS
    monkeypatch.setattr(live, 'LIVESTREAMS', original[:3])
    assert manager.refresh(['livestreams'])['updated'] == 1
    assert len(manager.cameras(query='India')['cameras']) == 0
    monkeypatch.setattr(live, 'LIVESTREAMS', original)
    assert manager.refresh(['livestreams'])['updated'] == 1
    assert len(manager.cameras(query='India')['cameras']) == 4
    assert {point[0] for point in manager.points(query='India')['points'] if point[3] == 'c'} == {
        'livestreams:siddhivinayak', 'livestreams:shirdi', 'livestreams:somnath', 'livestreams:arunachala'}
    assert manager.refresh(['livestreams'])['sources'][0]['cached']


def test_atlas_broadcast_resolution_uses_registered_camera_ids(network, monkeypatch):
    import httpx
    from app.intelligence import live
    manager, _providers, registry = network
    monkeypatch.setattr(live, 'resolve_livestream', lambda page: 'https://manifest.googlevideo.com/fresh.m3u8')
    with httpx.Client() as client:
        response = client.get(manager.open_map()['map_url'] + 'camera/play?id=livestreams:makkah')
        assert response.status_code == 200
        assert response.json()['live_page'] == live.LIVESTREAMS[0]['page']
        assert response.json()['video_url'].endswith('fresh.m3u8')
        assert 'https://*.googlevideo.com' in response.headers['content-security-policy']
        assert client.get(manager.open_map()['map_url'] + 'camera/play?id=https://example.org/arbitrary').status_code == 400


def test_feed_health_history_gives_a_measured_reliability(network):
    manager, _providers, _registry = network
    manager.store.record_health("roads_cars_mn", False, 0, "TimeoutError")
    summary = manager.store.health_summary()["roads_cars_mn"]
    assert summary["attempts"] == 2 and summary["successes"] == 1 and summary["counts"] == [1]


def test_atlas_serves_live_layers_the_iss_orbit_and_everything_around_a_click(network):
    import httpx

    manager, _providers, _registry = network
    url = manager.open_map()["map_url"]
    with httpx.Client() as client:
        aircraft = client.get(url + "live?kind=aircraft&lat=44.98&lon=-93.26&radius_km=9000").json()
        assert aircraft["count"] == 3 and aircraft["radius_km"] == 460 and aircraft["source"].startswith("adsb.lol")
        assert client.get(url + "live?kind=submarines&lat=1&lon=1").status_code == 400
        assert client.get(url + "live?kind=aircraft&lat=95&lon=1").status_code == 400
        iss = client.get(url + "iss").json()
        assert len(iss["track"]) == 96 and 300 < iss["altitude_km"] < 500
        around = client.get(url + "around?lat=44.98&lon=-93.26&radius_km=20").json()
        assert around["fusion"] and around["place"]["name"] == "near Minneapolis, Minnesota"
        policy = client.get(url).headers["Content-Security-Policy"]
        assert "https://mesonet.agron.iastate.edu" in policy and "https://gibs.earthdata.nasa.gov" in policy
        page = client.get(url).text
        assert all(f'id="{layer}"' in page for layer in ("radar", "satellite", "aircraft", "vessels", "transit", "iss"))


def test_map_points_carry_severity_and_category_for_pulsing_markers(network):
    manager, _providers, _registry = network
    points = {point[0]: point for point in manager.points()["points"]}
    closure = points["roads_cars_mn:mn-1"]
    assert len(closure) == 7 and "+" in closure[5] and closure[6] == "traffic"
    assert points["cameras_delaware:KCAM001"][5].startswith("s")  # camera flags unchanged


class FakeVision:
    def __init__(self):
        self.prompts = []

    def analyze(self, path, prompt):
        from pathlib import Path

        assert Path(path).is_file()
        self.prompts.append(prompt)
        return {"interpretation": f"Look {len(self.prompts)}: light traffic, dry road.", "model": "fake-vision"}


def test_hayat_looks_through_any_public_camera_and_remembers_what_it_saw(network):
    from app.intelligence.camera_eyes import CameraEyes

    manager, _providers, _registry = network
    grabbed = []

    def grab(url, destination):
        grabbed.append(url)
        from PIL import Image
        Image.new("RGB", (64, 48), "#335").save(destination, format="JPEG")

    eyes = CameraEyes(manager, FakeVision(), grab=grab)
    still = eyes.look(event_id="cameras_london:JamCams_test")
    assert still["picture"]["method"] == "publisher still image" and not grabbed
    assert still["seen"].startswith("Look 1") and still["previous"] is None
    assert "Do not identify or describe individual people" in eyes.vision.prompts[0]
    stream = eyes.look(event_id="cameras_delaware:KCAM001", question="Is it snowing?")
    assert grabbed and grabbed[0].endswith(".m3u8") and "live stream" in stream["picture"]["method"]
    assert "The owner asks: Is it snowing?" in eyes.vision.prompts[1]
    again = eyes.look(event_id="cameras_delaware:KCAM001")
    assert again["previous"]["seen"].startswith("Look 2")


def test_looking_needs_a_vision_model_and_skips_dead_cameras(network):
    from app.intelligence.camera_eyes import CameraEyes

    manager, _providers, _registry = network
    with pytest.raises(ValueError, match="No vision model"):
        CameraEyes(manager, None).look(event_id="cameras_london:JamCams_test")

    def dead(url, destination):
        raise ValueError("Server returned 404")

    with pytest.raises(ValueError, match="404"):
        CameraEyes(manager, FakeVision(), grab=dead).look(event_id="cameras_delaware:KCAM001")


def test_look_requests_route_to_the_camera_eyes():
    from app.intelligence.camera_eyes import look_request

    assert look_request("look at the camera near Times Square")["arguments"] == {"place": "Times Square"}
    assert look_request("what do you see on camera cameras_nyc:abc")["arguments"] == {"event_id": "cameras_nyc:abc"}
    assert look_request("look at makkah live")["arguments"] == {"event_id": "livestreams:makkah"}
    assert look_request("what is happening in Paris") is None and look_request("check the weather in Paris") is None
