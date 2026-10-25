"""Launches, satellites, aircraft details, weather maps, imagery, fires, routes, bike share and vehicle counts."""
import io
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.intelligence import explore as module
from app.intelligence.explore import (EXPLORE_TOOLS, Explore, explore_report, explore_request, look_angles, observer_ecef,
                                      register_explore_tools, sun_eci, sunlit)

ISS = {"OBJECT_NAME": "ISS (ZARYA)", "OBJECT_ID": "1998-067A", "EPOCH": "2026-10-10T18:56:31.282080", "MEAN_MOTION": 15.48817123,
       "ECCENTRICITY": 0.00067205, "INCLINATION": 51.6307, "RA_OF_ASC_NODE": 88.0087, "ARG_OF_PERICENTER": 246.7167,
       "MEAN_ANOMALY": 113.3114, "EPHEMERIS_TYPE": 0, "CLASSIFICATION_TYPE": "U", "NORAD_CAT_ID": 25544, "ELEMENT_SET_NO": 999,
       "REV_AT_EPOCH": 58967, "BSTAR": 0.000205821, "MEAN_MOTION_DOT": 0.00010839, "MEAN_MOTION_DDOT": 0}
PLACES = {"here": ("Detroit, United States", 42.33, -83.05, "US"), "london": ("London, United Kingdom", 51.51, -0.13, "GB"),
          "ann arbor": ("Ann Arbor, United States", 42.28, -83.74, "US"), "chicago": ("Chicago, United States", 41.88, -87.63, "US"),
          "los angeles": ("Los Angeles, United States", 34.05, -118.24, "US")}


def png(colour=(20, 40, 60, 255), mode="RGB", size=(64, 64)):
    from PIL import Image
    output = io.BytesIO()
    image_mode = "RGBA" if mode == "RGBA" else "RGB"
    Image.new(image_mode, size, colour if image_mode == "RGBA" else colour[:3]).save(output, "JPEG" if mode == "JPEG" else "PNG")
    return output.getvalue()


def launch(name, net, abbrev, provider="SpaceX"):
    return {"name": name, "net": net, "status": {"name": abbrev + " status", "abbrev": abbrev},
            "launch_service_provider": {"name": provider}, "pad": {"name": "SLC-4E", "location": {"name": "Vandenberg SFB, CA, USA"}},
            "mission": {"name": name.split("|")[-1].strip(), "orbit": {"name": "Low Earth Orbit"}, "description": "A mission."},
            "webcast_live": False, "probability": 80, "url": "https://ll.example/" + name}


class Fetcher:
    """Answers by URL prefix; records every URL it was asked for."""

    def __init__(self):
        self.urls = []
        soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        past = (datetime.now(UTC) - timedelta(hours=20)).isoformat()
        self.payloads = {
            module.LL2 + "upcoming": {"results": [launch("Falcon 9 | Flew yesterday", past, "Success"),
                                                  launch("Falcon 9 | Starlink 15-25", soon, "Go"),
                                                  launch("PSLV | EOS-10", soon, "TBD", "ISRO")]},
            module.LL2 + "previous": {"results": [launch("Falcon 9 | Flew yesterday", past, "Success")]},
            "https://celestrak.org/": [ISS],
            module.ADSBDB + "aircraft/896450": {"response": {"aircraft": {
                "type": "777 300ER", "icao_type": "B77W", "manufacturer": "Boeing", "mode_s": "896450", "registration": "A6-EQA",
                "registered_owner": "A Private Owner Name", "registered_owner_country_name": "United Arab Emirates"}}},
            module.ADSBDB + "callsign/UAE1": {"response": {"flightroute": {
                "callsign": "UAE1", "airline": {"name": "Emirates"},
                "origin": {"name": "Dubai International", "iata_code": "DXB", "municipality": "Dubai"},
                "destination": {"name": "London Heathrow", "iata_code": "LHR", "municipality": "London"}}}},
            "https://routing.openstreetmap.de/routed-car/": {"code": "Ok", "routes": [{"distance": 70100, "duration": 3180, "geometry": {
                "coordinates": [[-83.05, 42.33], [-83.74, 42.28]]}, "legs": [{"steps": [
                    {"maneuver": {"type": "depart"}, "name": "Cadillac Square", "distance": 100},
                    {"maneuver": {"type": "turn", "modifier": "left"}, "name": "", "distance": 20},
                    {"maneuver": {"type": "merge", "modifier": "slight left"}, "name": "Jeffries Freeway", "distance": 7740},
                    {"maneuver": {"type": "arrive"}, "name": "Depot Street", "distance": 0}]}]}]},
            module.GBFS_SYSTEMS: ("Country Code,Name,Location,System ID,URL,Auto-Discovery URL,Supported Versions,Authentication Info URL,"
                                  "Authentication Type,Authentication Parameter Name\n"
                                  "US,Scooters Chicago,\"Chicago, IL\",scoot,https://s.example,https://scoot.example/gbfs.json,2.3,,,\n"
                                  "US,Divvy,\"Chicago, IL\",divvy,https://d.example,https://divvy.example/gbfs.json,2.3,,,\n"
                                  "US,Keyed Bikes,\"Chicago, IL\",keyed,https://k.example,https://keyed.example/gbfs.json,2.3,https://k.example/key,api_key,key\n"),
            "https://scoot.example/gbfs.json": {"data": {"en": {"feeds": [{"name": "station_information", "url": "https://scoot.example/info"},
                                                                          {"name": "station_status", "url": "https://scoot.example/status"}]}}},
            "https://scoot.example/info": {"data": {"stations": [{"station_id": "v1", "name": "id_1", "lat": 41.8801, "lon": -87.6301}]}},
            "https://scoot.example/status": {"data": {"stations": [{"station_id": "v1", "num_vehicles_available": 0}]}},
            "https://divvy.example/gbfs.json": {"data": {"en": {"feeds": [{"name": "station_information", "url": "https://divvy.example/info"},
                                                                         {"name": "station_status", "url": "https://divvy.example/status"}]}}},
            "https://divvy.example/info": {"data": {"stations": [
                {"station_id": "a", "name": "State St & Madison", "lat": 41.882, "lon": -87.628},
                {"station_id": "b", "name": "Far Away", "lat": 42.2, "lon": -87.9}]}},
            "https://divvy.example/status": {"data": {"stations": [
                {"station_id": "a", "num_bikes_available": 7, "num_docks_available": 12, "is_renting": True}]}},
            module.FIRMS: ("latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"
                           "34.154,-118.195,330,0.4,0.4,2026-10-10,0958,N20,nominal,2.0NRT,290,12.5,N\n"
                           "36.357,-114.914,320,0.4,0.4,2026-10-10,0956,N20,high,2.0NRT,290,2.0,N\n"
                           "-13.021,-41.812,367,0.4,0.4,2026-10-10,1601,N20,low,2.0NRT,300,1197.0,D\n"
                           "bad,row\n"),
            "https://nowcoast.noaa.gov/": png(),
            module.GIBS_WMS: png((40, 90, 40, 255), "JPEG"),
            module.WORLDVIEW: png((120, 100, 80, 255), "JPEG"),
        }

    def fetch(self, url, max_bytes=0, accept=None, *args, **kwargs):
        self.urls.append(url)
        for prefix, value in sorted(self.payloads.items(), key=lambda item: -len(item[0])):
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                content = value if isinstance(value, bytes) else value.encode() if isinstance(value, str) else json.dumps(value).encode()
                return SimpleNamespace(content=content, retrieved_at=datetime.now(UTC).isoformat(), final_url=url)
        raise PermissionError("no fixture for " + url)


def resolve(place):
    name, lat, lon, country = PLACES[(place or "here").lower()]
    return {"name": name, "latitude": lat, "longitude": lon, "country_code": country}


@pytest.fixture
def explore(tmp_path):
    land = tmp_path / "land.json"
    land.write_text(json.dumps({"polygons": [[[-90, 40], [-80, 40], [-80, 45], [-90, 45], [-90, 40]]]}), encoding="utf-8")
    service = Explore(Fetcher(), tmp_path / "explore", resolve, land=land)
    ISS["EPOCH"] = (datetime.now(UTC) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S.%f")
    return service


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text, tool, arguments", [
    ("When is the next rocket launch?", "space_launches", {"mode": "upcoming"}),
    ("next SpaceX launch", "space_launches", {"mode": "upcoming", "query": "spacex"}),
    ("show me the latest launches", "space_launches", {"mode": "recent"}),
    ("any launches this week?", "space_launches", {"mode": "upcoming"}),
    ("What satellites are overhead?", "satellites_overhead", {"place": "here", "group": "visual"}),
    ("which starlink satellites are above London now", "satellites_overhead", {"place": "London", "group": "starlink"}),
    ("When can I see the ISS?", "satellite_pass", {"place": "here", "name": "ISS"}),
    ("when will the space station pass over Hyderabad", "satellite_pass", {"place": "Hyderabad", "name": "ISS"}),
    ("next ISS pass from Lahore", "satellite_pass", {"place": "Lahore", "name": "ISS"}),
    ("what plane is UAE1", "aircraft_info", {"query": "UAE1"}),
    ("aircraft info for 896450", "aircraft_info", {"query": "896450"}),
    ("show me the weather radar over Chicago", "weather_map", {"place": "Chicago", "kind": "radar"}),
    ("cloud map over Hyderabad", "weather_map", {"place": "Hyderabad", "kind": "clouds"}),
    ("lightning near Miami", "weather_map", {"place": "Miami", "kind": "lightning"}),
    ("satellite image of Dubai", "satellite_image", {"place": "Dubai"}),
    ("show me a satellite view of Karachi on 2026-10-01", "satellite_image", {"place": "Karachi", "date": "2026-10-01"}),
    ("any fire hotspots near Los Angeles?", "fire_hotspots", {"place": "Los Angeles"}),
    ("show fire hotspots", "fire_hotspots", {}),
    ("directions from Detroit to Ann Arbor", "route_directions", {"origin": "Detroit", "destination": "Ann Arbor", "mode": "car"}),
    ("walking directions from Westminster to Camden Town", "route_directions",
     {"origin": "Westminster", "destination": "Camden Town", "mode": "foot"}),
    ("how far is it from Paris to Lyon by bike", "route_directions", {"origin": "Paris", "destination": "Lyon", "mode": "bike"}),
    ("bike share near Chicago", "bike_share", {"place": "Chicago"}),
    ("where can I get city bikes in Paris?", "bike_share", {"place": "Paris"}),
])
def test_everyday_sentences_route_to_the_right_tool(text, tool, arguments):
    assert explore_request(text) == {"tool": tool, "arguments": arguments}


@pytest.mark.parametrize("text", ["what is the weather in London", "planes over Dubai", "what is a satellite", "launch the browser",
                                  "find cameras in Chicago", "how do rockets work", "play the second camera", "bitcoin price"])
def test_other_requests_are_left_alone(text):
    assert explore_request(text) is None


def test_direct_plan_routes_explore_sentences_and_keeps_world_routes():
    from app.intelligence.tools import ROUTED_NAMES, TOOL_NAMES, direct_plan
    step = direct_plan("When can I see the ISS from London?", ROUTED_NAMES)
    assert step["tool"] == "satellite_pass" and step["arguments"] == {"place": "London", "name": "ISS"}
    assert direct_plan("When can I see the ISS from London?", TOOL_NAMES) is None  # tools not registered: not routed
    assert direct_plan("planes over Dubai", ROUTED_NAMES)["tool"] == "intelligence_aircraft"


@pytest.mark.parametrize("text", ["When can I see the ISS?", "next rocket launch", "directions from Detroit to Ann Arbor",
                                  "satellite image of Dubai"])
def test_the_router_treats_explore_sentences_as_actions(text):
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter
    from app.session import SessionState
    intent = IntentRouter().route(text, SessionState())
    assert intent.intent == IntentKind.ACTION and intent.entities.get("modality") == "public_intelligence"


# ------------------------------------------------------------------ orbital math
def test_look_angles_overhead_and_on_the_horizon():
    observer = observer_ecef(0.0, 0.0)
    above = observer_ecef(0.0, 0.0, 400.0)
    elevation, _azimuth, distance = look_angles(above, observer, 0.0, 0.0)
    assert elevation == pytest.approx(90, abs=.01) and distance == pytest.approx(400, abs=.01)
    north = observer_ecef(1.0, 0.0, 0.0)
    elevation, azimuth, _distance = look_angles(north, observer, 0.0, 0.0)
    assert azimuth == pytest.approx(0, abs=.5) and -1 < elevation < 0


def test_sun_position_at_the_equinox_and_earth_shadow():
    from sgp4.api import jday
    jd, fraction = jday(2026, 3, 20, 14, 46, 0)  # March equinox: the Sun crosses the equator
    x, y, z = sun_eci(jd + fraction)
    assert math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z))) == pytest.approx(0, abs=.1)
    sun = (1.496e8, 0.0, 0.0)
    assert sunlit((6771.0, 0.0, 0.0), sun)  # day side
    assert not sunlit((-6771.0, 0.0, 0.0), sun)  # directly behind Earth
    assert sunlit((-6771.0, 0.0, 7000.0), sun)  # behind, but above the shadow cylinder


def test_satellites_overhead_and_passes_are_computed_from_elements(explore):
    overhead = explore.satellites_overhead("London", "stations", min_elevation=-90)
    assert overhead["count"] == 1 and overhead["satellites"][0]["name"] == "ISS (ZARYA)"
    item = overhead["satellites"][0]
    assert -90 <= item["elevation"] <= 90 and 0 <= item["azimuth"] < 360 and item["range_km"] > 300
    passes = explore.satellite_pass("London", "space station", days=2)
    assert passes["satellite"] == "ISS (ZARYA)" and passes["passes"]
    first = passes["passes"][0]
    assert first["rise"] <= first["peak"] <= first["set"] and first["rise"] < first["set"] and first["peak_elevation"] >= 10
    assert 0 < first["minutes"] < 15  # an ISS pass lasts minutes
    text = explore_report(passes)
    assert "ISS (ZARYA) over London" in text and "UTC" in text and "CelesTrak" in text
    with pytest.raises(ValueError, match="No satellite named"):
        explore.satellite_pass("London", "Not A Satellite")
    with pytest.raises(ValueError, match="Satellite groups"):
        explore.satellites_overhead("London", "spies")


def test_celestrak_groups_are_fetched_at_most_every_two_hours(explore, tmp_path):
    explore.satellites_overhead("London", "stations")
    explore._cache.clear()  # a new session: the file on disk is still fresh
    explore.satellites_overhead("London", "stations")
    assert sum(url.startswith("https://celestrak.org/") for url in explore.fetcher.urls) == 1


def test_single_sample_orbit_crossing_is_reported_without_zero_duration(explore, monkeypatch):
    # This public-element fixture produced the full-suite failure at this exact time.
    # Preserve the real SGP4 calculation while removing wall-clock dependence.
    moment = datetime(2026, 10, 24, 2, 13, 22, 653543, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(module, 'datetime', FrozenDateTime)
    monkeypatch.setitem(ISS, 'EPOCH', '2026-10-23T20:13:22.653543')
    result = explore.satellite_pass('London', 'space station', days=2)
    assert result['unresolved_short_passes'] >= 1
    assert result['passes']
    assert all(row['rise'] < row['set'] and row['minutes'] > 0 for row in result['passes'])
    assert all(0 <= row['visible_minutes'] <= row['minutes'] for row in result['passes'])
    assert 'brief threshold crossing' in explore_report(result)
    assert 'no duration or visibility claim' in explore_report(result)


def test_satellite_azimuth_wraps_rounded_north_back_to_zero(explore, monkeypatch):
    monkeypatch.setattr(module, 'look_angles', lambda *_args: (15, 359.9, 400))
    result = explore.satellites_overhead('London', 'stations')
    assert result['satellites'][0]['azimuth'] == 0
    assert result['satellites'][0]['direction'] == 'N'


# ------------------------------------------------------------------ launches and aircraft
def test_launches_skip_a_flown_launch_in_upcoming_and_filter_by_name(explore):
    upcoming = explore.launches("upcoming")
    assert [item["name"] for item in upcoming["launches"]] == ["Falcon 9 | Starlink 15-25", "PSLV | EOS-10"]
    assert [item["provider"] for item in explore.launches("upcoming", "isro")["launches"]] == ["ISRO"]
    recent = explore.launches("recent")
    assert recent["launches"][0]["status"] == "Success status"
    text = explore_report(upcoming)
    assert text.startswith("Upcoming launches") and "Starlink 15-25" in text and "in 2 days" in text
    with pytest.raises(ValueError):
        explore.launches("someday")


def test_aircraft_details_never_include_the_registered_owner(explore):
    value = explore.aircraft_info("896450")
    assert value["aircraft"]["registration"] == "A6-EQA" and "route" not in value
    assert "registered_owner" not in value["aircraft"]
    assert "Private Owner" not in json.dumps(value) and "Private Owner" not in explore_report(value)
    route = explore.aircraft_info("uae1")
    assert route["route"]["origin"]["iata_code"] == "DXB"
    assert "Dubai (DXB) → London (LHR)" in explore_report(route)
    with pytest.raises(ValueError, match="no aircraft or flight route"):
        explore.aircraft_info("ZZZ999")
    with pytest.raises(ValueError, match="callsign"):
        explore.aircraft_info("x")


# ------------------------------------------------------------------ pictures
def test_weather_maps_are_saved_as_captioned_images(explore):
    from PIL import Image
    value = explore.weather_map("here", "clouds", 500)
    path = Path(value["path"])
    assert path.is_file() and path.parent == explore.folder / "images"
    with Image.open(path) as image:
        assert image.size[0] == 64 and image.getpixel((2, image.size[1] - 5)) == (10, 16, 24)  # the caption strip
    request = next(url for url in explore.fetcher.urls if url.startswith("https://nowcoast"))
    assert "global_longwave_imagery_mosaic" in request and "transparent=false" in request
    assert value["path"] in explore_report(value)
    with pytest.raises(ValueError, match="Map kinds"):
        explore.weather_map("here", "tornado")


def test_radar_is_drawn_over_a_relief_base_map_kept_on_disk(explore):
    explore.fetcher.payloads["https://nowcoast.noaa.gov/"] = png((0, 0, 0, 0), "RGBA")  # empty radar: fully transparent
    value = explore.weather_map("chicago", "radar")
    from PIL import Image
    with Image.open(value["path"]) as image:
        red, green, _blue = image.getpixel((30, 30))
        assert green > red  # the relief map shows through the transparent radar
    assert "transparent=true" in next(url for url in explore.fetcher.urls if url.startswith("https://nowcoast"))
    explore.weather_map("chicago", "radar")
    assert sum(url.startswith(module.GIBS_WMS) for url in explore.fetcher.urls) == 1  # the base map is cached
    explore.fetcher.payloads[module.GIBS_WMS] = ConnectionError("down")
    assert Path(explore.weather_map("london", "radar")["path"]).is_file()  # without the base map, still a map


def test_satellite_image_defaults_to_yesterday_and_checks_the_date(explore):
    value = explore.satellite_image("london")
    assert value["date"] == (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    assert "VIIRS_SNPP_CorrectedReflectance_TrueColor" in explore.fetcher.urls[-1]
    assert Path(value["path"]).is_file()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        explore.satellite_image("london", "yesterday")


def test_saved_pictures_are_bounded(explore, monkeypatch):
    for index in range(205):
        explore._save(png((index, 0, 0, 255)), "test")
    assert len(list((explore.folder / "images").glob("*.png"))) == 200


# ------------------------------------------------------------------ fires, routes, bikes
def test_fire_hotspots_near_a_place_and_worldwide(explore):
    near = explore.fire_hotspots("los angeles", 500)
    assert near["count"] == 2 and near["world_count"] == 3
    assert near["hotspots"][0]["power_mw"] == 12.5 and near["hotspots"][0]["distance_km"] < 20
    assert near["hotspots"][0]["detected"] == "2026-10-10 09:58 UTC" and near["hotspots"][0]["daynight"] == "night"
    world = explore.fire_hotspots()
    assert world["place"] == "world" and world["hotspots"][0]["power_mw"] == 1197.0
    assert "worldwide" in explore_report(world)
    inside = explore.hotspots_in(30, -120, 40, -110)
    assert [round(item["power_mw"], 1) for item in inside] == [12.5, 2.0]
    assert explore.hotspots_in(-20, 170, 0, -40)[0]["power_mw"] == 1197.0  # a view across the date line
    explore.fire_hotspots()
    assert sum(url == module.FIRMS for url in explore.fetcher.urls) == 1  # one 6 MB download an hour


def test_route_directions(explore):
    value = explore.route("here", "ann arbor")
    assert value["distance_km"] == 70.1 and value["duration_minutes"] == 53
    assert [step["instruction"] for step in value["steps"]] == [
        "Depart onto Cadillac Square", "Merge slight left onto Jeffries Freeway", "Arrive"]
    assert "-83.050000,42.330000;-83.740000,42.280000" in explore.fetcher.urls[-1]
    assert "Driving from Detroit" in explore_report(value) and "about 53 min" in explore_report(value)
    with pytest.raises(ValueError, match="car, bike or foot"):
        explore.route("here", "ann arbor", "plane")
    explore.fetcher.payloads["https://routing.openstreetmap.de/routed-car/"] = {"code": "NoRoute", "message": "Impossible route"}
    explore._cache.clear()
    with pytest.raises(ValueError, match="Impossible route"):
        explore.route("london", "chicago")


def test_bike_share_prefers_docked_systems_and_skips_keyed_ones(explore):
    value = explore.bikeshare("chicago")
    assert value["systems"] == ["Divvy"] and value["count"] == 1
    station = value["stations"][0]
    assert station["name"] == "State St & Madison" and station["bikes"] == 7 and station["docks"] == 12
    assert not any("keyed.example" in url for url in explore.fetcher.urls)
    assert "7 bikes, 12 free docks" in explore_report(value)
    with pytest.raises(ValueError, match="No open bike-share system"):
        explore.bikeshare("london")


# ------------------------------------------------------------------ vehicles in a camera still
def test_traffic_count_counts_vehicles_and_nothing_else(explore, tmp_path):
    import cv2
    import numpy as np
    still = tmp_path / "still.jpg"
    cv2.imwrite(str(still), np.random.default_rng(1).integers(0, 255, (40, 60, 3), dtype=np.uint8))
    explore.frame = lambda event_id: {"path": str(still), "title": "A12 East", "fetched_at": "2026-10-11T08:00:00+00:00"}
    labels = ["car", "car", "bus", "person", "truck", "traffic light", "bicycle"]
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [SimpleNamespace(label=label) for label in labels])
    value = explore.traffic_count("cameras_tfl:1")
    assert value["vehicles"] == 5 and value["counts"] == {"cars": 2, "buses": 1, "trucks": 1, "bicycles": 1}
    assert value["level"] == "light" and "person" not in json.dumps(value["counts"])
    assert "no plates, faces or people" in value["basis"]
    assert explore_report(value).startswith("A12 East: 5 vehicles visible (2 cars, 1 buses, 1 trucks, 1 bicycles) — light.")
    explore.frame = None
    with pytest.raises(ValueError, match="not available"):
        explore.traffic_count("cameras_tfl:1")


def test_the_vehicle_detector_is_kept_inside_hayat(explore, monkeypatch):
    import open_image_models
    import open_image_models.detection.core.hub as hub
    created = {}
    monkeypatch.setattr(hub, "MODEL_CACHE_DIR", hub.MODEL_CACHE_DIR)
    monkeypatch.setattr(open_image_models, "create_detector", lambda name, **options: created.update(name=name, **options) or "model")
    assert explore._vehicles().model == "model" and explore._vehicles().device == "CPU"
    assert created["name"] == "rf-detr-small-512-coco"
    assert Path(hub.MODEL_CACHE_DIR) == explore.folder.parent.parent / "models" / "vision"


# ------------------------------------------------------------------ tools and replies
def test_tools_register_and_report_through_the_intelligence_report(explore, tmp_path):
    from app.intelligence.tools import report
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_explore_tools(registry, explore)
    assert set(EXPLORE_TOOLS) <= set(registry.names())
    result = registry.execute("route_directions", {"origin": "here", "destination": "ann arbor"})
    assert result.success, result.error
    text = report([{"tool": "route_directions", "result": {"success": True, "output": result.output}, "verification": {"passed": True}}])
    assert text.startswith("Driving from Detroit")
    assert not registry.execute("aircraft_info", {"query": "x"}).success


def test_every_reply_names_its_source(explore):
    values = [explore.launches(), explore.satellites_overhead("london", "stations", -90), explore.aircraft_info("896450"),
              explore.fire_hotspots("los angeles"), explore.route("here", "ann arbor"), explore.bikeshare("chicago"),
              explore.weather_map("london"), explore.satellite_image("london")]
    for value in values:
        assert "Source: " in explore_report(value)
    assert explore_report({"not": "explore"}) is None


# ------------------------------------------------------------------ the atlas fire layer
def test_the_atlas_serves_fire_hotspots_in_view(explore, tmp_path):
    import urllib.request
    from app.intelligence.map_server import IntelligenceMapServer
    manager = SimpleNamespace(explore=explore)
    server = IntelligenceMapServer(manager)
    try:
        with urllib.request.urlopen(server.url + "fires?s=30&w=-120&n=40&e=-110", timeout=10) as response:
            value = json.loads(response.read())
        assert value["count"] == 2 and value["items"][0]["power_mw"] == 12.5 and "FIRMS" in value["source"]
        try:
            urllib.request.urlopen(server.url + "fires?s=x", timeout=10)
            raise AssertionError("bad bounds must be refused")
        except urllib.error.HTTPError as error:
            assert error.code == 400
    finally:
        server.httpd.shutdown()
    html = (Path(module.__file__).parents[1] / "assets/intelligence-map/index.html").read_text(encoding="utf-8")
    script = (Path(module.__file__).parents[1] / "assets/intelligence-map/map.js").read_text(encoding="utf-8")
    assert 'id="fires"' in html and "fires?" in script and "refreshFires" in script


# ------------------------------------------------------------------ the Explore tab and Count vehicles
from tests.test_gui_intelligence import explorer, pump_until  # noqa: E402,F401 -- fixture


def test_explore_tab_runs_tools_and_shows_text_and_pictures(explorer, explore):  # noqa: F811
    window, gui = explorer
    window.notebook.select(window.tabs['explore'])
    window.update()
    assert window.notebook.tab(window.tabs['explore'], 'text') == 'Explore'
    assert 'not loaded' in window.explore_status.get()
    assert window.explore_run('Launches') is False and 'not loaded' in window.explore_status.get()
    register_explore_tools(gui.runtime.registry, explore)
    window.explore_place.set('london')
    window.explore_destination.set('')
    assert window.explore_run('Directions') is False and 'To' in window.explore_status.get()
    assert window.explore_run('Aircraft info') is False and 'callsign' in window.explore_status.get()
    assert window.explore_run('Satellite image')
    pump_until(window, lambda: not window._busy and window._explore_pending is None)
    assert 'Satellite image, ' in window.explore_text.get('1.0', 'end') and window._explore_photo is not None
    assert 'done' in window.explore_status.get()
    explore.fetcher.payloads[module.ADSBDB + "callsign/ZZZ9"] = ConnectionError('offline')
    window.explore_aircraft.set('ZZZ9')
    assert window.explore_run('Aircraft info')
    pump_until(window, lambda: not window._busy and window._explore_pending is None)
    assert 'Aircraft info:' in window.explore_status.get() and 'no aircraft or flight route' in window.explore_status.get()
    window.explore_use_center()
    assert ',' in window.explore_place.get()
    window.geometry('760x580-4000-4000')
    window.update()
    assert window.tabs['explore'].winfo_height() >= 200


def test_count_vehicles_in_the_selected_camera_still(explorer, explore, tmp_path):  # noqa: F811
    import cv2
    import numpy as np
    window, gui = explorer
    # Less frequent camera actions live in the More camera actions menu (2026-10-17).
    menu = window.nametowidget(window.camera_more.cget('menu'))
    assert 'Count vehicles' in [menu.entrycget(index, 'label') for index in range(menu.index('end') + 1)]
    assert window.count_vehicles() is False  # nothing selected
    still = tmp_path / 'still.jpg'
    cv2.imwrite(str(still), np.random.default_rng(1).integers(0, 255, (40, 60, 3), dtype=np.uint8))
    explore.frame = lambda event_id: {'path': str(still), 'title': 'Test camera', 'fetched_at': 'now'}
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [SimpleNamespace(label='car')] * 9)
    window.camera_selection = 'cameras_test:1'
    assert window.count_vehicles() is False and 'not loaded' in window.feedback.get()
    register_explore_tools(gui.runtime.registry, explore)
    assert window.count_vehicles()
    pump_until(window, lambda: not window._busy)
    assert window.camera_message.get().startswith('Test camera: 9 vehicles visible (9 cars) — moderate.')


# ------------------------------------------------------------------ atlas navigation and the video wall
def test_the_atlas_search_box_can_take_the_map_to_a_place():
    from app.intelligence.map_server import IntelligenceMapServer
    asked = []

    def resolve(name):
        asked.append(name)
        if name == 'nowhere':
            raise ValueError("I couldn't find a place called 'nowhere'.")
        return {'name': 'Denver, Colorado, United States', 'latitude': 39.74, 'longitude': -104.98, 'country_code': 'US'}
    server = IntelligenceMapServer(SimpleNamespace(fusion=SimpleNamespace(resolve=resolve)))
    try:
        assert server.place('Denver') == {'found': True, 'name': 'Denver, Colorado, United States', 'latitude': 39.74, 'longitude': -104.98}
        assert server.place('21.4225, 39.8262')['latitude'] == 21.4225 and asked == ['Denver']  # coordinates need no lookup
        assert server.place('nowhere') == {'found': False} and server.place('  ') == {'found': False}
        assert server.place('here')['found'] and asked[-1] == 'here'  # "My location"
        assert server.place('95, 200')['found'] and asked[-1] == '95, 200'  # out of range: not taken as coordinates
    finally:
        server.close()
    without = IntelligenceMapServer(SimpleNamespace())
    try:
        assert without.place('Denver') == {'found': False}  # no geocoder in this session: the box filters records
    finally:
        without.close()


def test_the_atlas_page_has_back_location_auto_streets_and_a_video_wall():
    root = Path(module.__file__).parents[1] / "assets/intelligence-map"
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "map.js").read_text(encoding="utf-8")
    for element in ('id="view-back"', 'id="locate"', 'id="video-wall"', 'id="wall"', 'id="wall-grid"', 'id="wall-next"', 'id="wall-close"'):
        assert element in html
    assert "place?name=" in script and "RECORD_WORDS" in script and "STREET_ZOOM" in script
    assert "const WALL_SIZE = 6" in script and "stopWall" in script and "video.muted = true" in script
    assert "'pagehide', stopWall" in script  # nothing keeps playing after the atlas closes
