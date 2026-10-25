"""The deep look at a place, fire clusters with change, and traffic counts that remember and stay honest."""
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.intelligence.deep import DeepLook, deep_report, deep_request, register_deep_tools
from app.intelligence.explore import cluster_hotspots, compare_fire, explore_report, explore_request, placeholder, sun_elevation
from tests.test_explore import PLACES, Fetcher, resolve  # noqa: F401
from tests.test_explore import explore  # noqa: F401 -- fixture
from tests.test_gui_intelligence import explorer  # noqa: F401,E402 -- fixture

HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight\n"


def firms(*rows):
    return HEADER + "".join(f"{lat},{lon},330,0.4,0.4,2026-10-10,{time_},N20,{confidence},2.0NRT,290,{power},D\n"
                            for lat, lon, power, confidence, time_ in rows)


# ------------------------------------------------------------------ fires joined, and how they changed
def test_hotspots_in_touching_cells_are_one_fire():
    rows = [(34.10, -118.20, 20.0, "high", "2026-10-10 10:00", "D"), (34.12, -118.22, 10.0, "nominal", "2026-10-10 11:00", "D"),
            (34.14, -118.24, 5.0, "low", "2026-10-10 12:00", "D"),  # a chain: each touches the next
            (36.00, -115.00, 3.0, "nominal", "2026-10-10 09:00", "N")]  # far away: its own fire
    fires = sorted(cluster_hotspots(rows), key=lambda fire: -fire["power_mw"])
    assert [fire["detections"] for fire in fires] == [3, 1]
    big = fires[0]
    assert big["power_mw"] == 35 and big["max_power_mw"] == 20 and big["latest"] == "2026-10-10 12:00 UTC"
    assert 34.10 < big["latitude"] < 34.13 and 0 < big["extent_km"] < 5 and big["confident_share"] == pytest.approx(.67, abs=.01)


def test_a_fire_is_new_growing_steady_or_shrinking_against_the_earlier_download():
    fire = {"latitude": 34.1, "longitude": -118.2, "detections": 6, "power_mw": 60.0, "extent_km": 2.0}
    assert compare_fire(fire, []) == "new"
    assert compare_fire(fire, [{**fire, "latitude": 35.5}]) == "new"  # 150 km away: another fire
    assert compare_fire(fire, [{**fire, "detections": 3, "power_mw": 30.0}]) == "growing"
    assert compare_fire(fire, [{**fire, "detections": 6, "power_mw": 55.0}]) == "steady"
    assert compare_fire(fire, [{**fire, "detections": 12, "power_mw": 120.0}]) == "shrinking"


def test_fire_clusters_compare_with_the_previous_download(explore):  # noqa: F811
    import app.intelligence.explore as module
    explore.fetcher.payloads[module.FIRMS] = firms((34.10, -118.20, 20, "high", "1000"), (34.12, -118.22, 10, "nominal", "1100"))
    first = explore.fire_clusters("los angeles", 100)
    assert first["count"] == 1 and first["fires"][0]["change"] == "unknown" and first["compared_with"] is None
    assert "next hourly download" in explore_report(first)
    path = explore.folder / "cache" / "firms_viirs_24h.csv"
    os.utime(path, (time.time() - 4000, time.time() - 4000))  # an hour later
    explore._cache.clear()
    explore.fetcher.payloads[module.FIRMS] = firms((34.10, -118.20, 40, "high", "1000"), (34.12, -118.22, 30, "nominal", "1100"),
                                                   (34.14, -118.24, 25, "high", "1300"), (33.90, -117.50, 15, "high", "1400"))
    second = explore.fire_clusters("los angeles", 100)
    changes = {round(fire["latitude"], 1): fire["change"] for fire in second["fires"]}
    assert changes == {34.1: "growing", 33.9: "new"} and second["new"] == 1 and second["growing"] == 1
    assert second["compared_with"] and second["fires"][0]["distance_km"] < 20 and second["fires"][0]["direction"]
    text = explore_report(second)
    assert "1 new and 1 growing since" in text and "growing" in text and "Source: NASA FIRMS" in text
    assert (explore.folder / "cache" / "firms_viirs_24h_previous.csv").is_file()


@pytest.mark.parametrize(("text", "arguments"), [("which fires are growing near Los Angeles", {"place": "Los Angeles"}),
                                                 ("are any fires growing?", {}), ("show fire clusters near Denver", {"place": "Denver"}),
                                                 ("new fires in Australia", {"place": "Australia"})])
def test_fire_cluster_sentences(text, arguments):
    assert explore_request(text) == {"tool": "fire_clusters", "arguments": arguments}


# ------------------------------------------------------------------ traffic that remembers, and says when it cannot see
def still(tmp_path, name="still.jpg", colour=None):
    import cv2
    import numpy as np
    rng = np.random.default_rng(1)
    image = rng.integers(0, 255, (60, 80, 3), dtype=np.uint8) if colour is None else np.full((60, 80, 3), colour, dtype=np.uint8)
    path = tmp_path / name
    cv2.imwrite(str(path), image)
    return path


def test_a_placeholder_card_is_not_counted_as_an_empty_road(explore, tmp_path):  # noqa: F811
    import cv2
    import numpy as np
    card = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.putText(card, "Temporarily", (40, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 0, 0), 3)
    assert placeholder(card) and not placeholder(cv2.imread(str(still(tmp_path))))
    cv2.imwrite(str(tmp_path / "card.jpg"), card)
    explore.frame = lambda event_id: {"path": str(tmp_path / "card.jpg"), "title": "Card"}
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [])
    with pytest.raises(ValueError, match="placeholder"):
        explore.traffic_count("cameras_test:1")
    assert not (explore.folder / "traffic.db").exists()  # nothing remembered from a card


def test_counts_are_remembered_and_compared_with_the_usual_for_this_hour(explore, tmp_path):  # noqa: F811
    import sqlite3
    path = still(tmp_path)
    explore.frame = lambda event_id: {"path": str(path), "title": "A12 East", "fetched_at": "now"}
    labels = ["car"] * 4
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [SimpleNamespace(label=label) for label in labels])
    first = explore.traffic_count("cameras_tfl:1")
    assert first["history"] == {"samples": 1, "compared": None, "needed": 3}
    # Three earlier days at this hour, seeded the way HAYAT stores them.
    now = datetime.now(UTC)
    with sqlite3.connect(explore.folder / "traffic.db") as db:
        for days, total in ((1, 5), (2, 4), (3, 6)):
            moment = now - timedelta(days=days)
            db.execute("INSERT INTO counts VALUES (?, ?, ?, ?, ?, ?)", ("cameras_tfl:1", moment.isoformat(timespec="seconds"),
                                                                     moment.hour, moment.weekday(), total, "{}"))
    db.close()
    labels[:] = ["car"] * 14
    busy = explore.traffic_count("cameras_tfl:1")
    assert busy["history"]["compared"] == "busier than usual" and busy["history"]["usual"] == 5
    assert "busier than usual for this hour (usually about 5" in explore_report(busy)
    labels[:] = ["car"] * 5
    assert explore.traffic_count("cameras_tfl:1")["history"]["compared"] == "about usual"
    assert explore.traffic_count("cameras_other:2")["history"]["samples"] == 1  # each camera has its own memory


def test_counts_in_the_dark_say_they_are_lower_bounds(explore, tmp_path):  # noqa: F811
    path = still(tmp_path)
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [SimpleNamespace(label="car")])
    night = datetime.now(UTC)
    # A place where the Sun is down right now: opposite the subsolar longitude.
    longitude = next(lon for lon in range(-180, 180, 15) if sun_elevation(0.0, float(lon), night) < -30)
    explore.frame = lambda event_id: {"path": str(path), "title": "Night", "latitude": 0.0, "longitude": float(longitude)}
    value = explore.traffic_count("cameras_test:9")
    assert value["dark"] is True and "likely higher" in explore_report(value)
    explore.frame = lambda event_id: {"path": str(path), "title": "Unknown place"}
    assert explore.traffic_count("cameras_test:10")["dark"] is None


def test_sun_elevation():
    assert sun_elevation(0, 0, datetime(2026, 3, 20, 12, 7, tzinfo=UTC)) == pytest.approx(90, abs=1)
    assert sun_elevation(39.74, -104.98, datetime(2026, 10, 11, 19, 0, tzinfo=UTC)) == pytest.approx(43, abs=2)
    assert sun_elevation(39.74, -104.98, datetime(2026, 10, 11, 7, 0, tzinfo=UTC)) < -30  # 01:00 in Denver


# ------------------------------------------------------------------ the deep look
def around(**extra):
    base = {"place": {"name": "Testville", "latitude": 34.05, "longitude": -118.24}, "observed_at": "2026-10-11T12:00:00+00:00",
            "events": [], "cameras": [], "weather": {"wind_from_deg": 30.0, "wind_speed_ms": 6.0, "temperature_c": 20,
                                                     "precipitation_next_hour_mm": 0, "source": "MET Norway"},
            "air": {}, "metrics": {"temperature_c": 20}, "changes": [], "not_covered": [], "unavailable": {},
            "interpretation": "Around Testville."}
    return {**base, **extra}


def fire(**extra):
    return {"latitude": 34.30, "longitude": -118.00, "detections": 8, "power_mw": 140.0, "max_power_mw": 40.0, "extent_km": 2.0,
            "latest": "2026-10-11 10:00 UTC", "confident_share": .8, "change": "growing", "distance_km": 35.0, "direction": "NE",
            **extra}


def test_a_growing_fire_upwind_is_found_with_its_evidence():
    gathered = {"around": around(), "fires": {"fires": [fire()]}, "traffic": [], "gaps": {}}
    findings = DeepLook.reason(gathered, 25)
    top = findings[0]
    assert top["title"] == "Growing fire 35.0 km away" and top["level"] == "watch"
    assert "blows from the fire toward here" in top["detail"] and any("wind from 30°" in item for item in top["evidence"])
    calm = DeepLook.reason({**gathered, "around": around(weather={"wind_from_deg": 210.0, "wind_speed_ms": 6.0})}, 25)
    assert "toward here" not in calm[0]["detail"]  # wind blowing the smoke away
    close = DeepLook.reason({**gathered, "fires": {"fires": [fire(distance_km=20.0)]}}, 25)
    assert close[0]["level"] == "alert" and DeepLook.situation(close) == "alert"


def test_small_single_detections_are_one_low_confidence_line_not_fires():
    small = [fire(detections=1, power_mw=2.0, distance_km=10.0, change="steady"), fire(detections=1, power_mw=1.0, distance_km=80.0)]
    findings = DeepLook.reason({"around": around(), "fires": {"fires": small}, "traffic": [], "gaps": {}}, 25)
    assert [item["title"] for item in findings] == ["2 small heat detections within 150 km"]
    assert findings[0]["confidence"] == "low" and findings[0]["level"] == "info" and DeepLook.situation(findings) == "calm"


def test_events_roads_weather_air_history_sky_and_blind_spots():
    events = [{"title": "M4.8 quake", "category": "earthquakes", "severity": "medium", "distance_km": 12.0, "direction": "S",
               "provider": "USGS"},
              {"title": "I-5 closed", "category": "traffic", "kind": "closure", "severity": "medium", "distance_km": 3.0, "direction": "N"},
              {"title": "I-5 closed", "category": "traffic", "kind": "closure", "severity": "medium", "distance_km": 3.0, "direction": "N"},
              {"title": "Works", "category": "traffic", "kind": "construction", "distance_km": 5.0, "direction": "E"}]
    soon = (datetime.now(UTC) + timedelta(hours=5)).isoformat(timespec="seconds")
    gathered = {"around": around(events=events, weather={"wind_from_deg": 0, "wind_speed_ms": 18, "precipitation_next_hour_mm": 5,
                                                         "temperature_c": 40, "source": "MET Norway"},
                                 air={"pm2_5_median": 60, "sensors": 4},
                                 changes=[{"label": "aircraft nearby", "unit": "", "now": 90, "usual": 20, "unusual": True}],
                                 not_covered=["Ships: no feed here"]),
                "fires": {"fires": []}, "sky": {"next_visible": {"rise": soon, "peak_elevation": 60, "from": "W", "to": "E",
                                                                 "visible_minutes": 4}},
                "launches": {"launches": [{"name": "Falcon 9", "location": "Vandenberg", "time": soon, "status": "Go",
                                           "pad_latitude": 34.63, "pad_longitude": -120.61}]},
                "traffic": [{"title": "I-5 cam", "vehicles": 30, "level": "busy", "fetched_at": "now",
                             "history": {"compared": "busier than usual", "usual": 9, "baseline_samples": 5}}],
                "gaps": {"traffic on camera 2": "The camera is showing a placeholder"}}
    findings = DeepLook.reason(gathered, 25)
    titles = [item["title"] for item in findings]
    assert titles[:1] == ["1 earthquake within 25 km"]
    road = next(item for item in findings if item["title"] == "3 road reports nearby")
    assert road["level"] == "watch" and "2 closure, 1 construction" in road["detail"] and len(road["evidence"]) == 2  # deduplicated
    for title in ("Rain expected within the hour", "Strong wind", "Extreme temperature", "Air quality: unhealthy",
                  "Unusual aircraft nearby", "Traffic busier than usual at I-5 cam", "The ISS is visible soon",
                  "Rocket launch 227 km away", "Could not read traffic on camera 2", "Not covered here"):
        assert any(item["title"] == title for item in findings), title
    assert [item["level"] for item in findings] == sorted((item["level"] for item in findings), key=lambda level: {
        "alert": 0, "watch": 1, "info": 2, "gap": 3}[level])
    assert next(item for item in findings if item["title"] == "Air quality: unhealthy")["confidence"] == "low"


def test_the_deep_look_tool_gathers_reasons_and_reports(explore, tmp_path):  # noqa: F811
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    explore.fetcher.payloads["https://firms"] = "x"  # unused prefix guard
    import app.intelligence.explore as module
    explore.fetcher.payloads[module.FIRMS] = firms((34.30, -118.00, 90, "high", "1000"), (34.31, -118.01, 50, "high", "1100"))
    path = still(tmp_path)
    explore.frame = lambda event_id: {"path": str(path), "title": "Cam " + event_id, "fetched_at": "now"}
    explore._vehicle_model = SimpleNamespace(predict=lambda image: [SimpleNamespace(label="car")] * 3)

    class Fusion:
        def around(self, place, radius_km, latitude=None, longitude=None):
            assert place == "los angeles" and radius_km == 25
            return around(place={"name": "Los Angeles", "latitude": 34.05, "longitude": -118.24},
                          cameras=[{"id": "cameras_a:1", "image": True}, {"id": "cameras_a:2", "image": False},
                                   {"id": "cameras_a:3", "image": True}])
    manager = SimpleNamespace(fusion=Fusion(), model=None)
    PLACES["34.0500, -118.2400"] = ("Los Angeles, United States", 34.05, -118.24, "US")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_deep_tools(registry, DeepLook(manager, explore))
    result = registry.execute("intelligence_deep_look", {"place": "los angeles", "reason": True})
    assert result.success, result.error
    value = result.output
    assert value["situation"] == "watch" and [count["title"] for count in value["traffic"]] == ["Cam cameras_a:1", "Cam cameras_a:3"]
    assert value["reading"]["note"].startswith("HAYAT's local model is not loaded")
    text = deep_report(value)
    assert text.startswith("Deep look · Los Angeles · within 25 km") and "Situation: WATCH" in text
    assert "fire" in text.lower() and "evidence:" in text and "Traffic on the 2 nearest cameras" in text
    from app.intelligence.tools import report
    assert report([{"tool": "intelligence_deep_look", "result": {"success": True, "output": value},
                    "verification": {"passed": True}}]).startswith("Deep look")


def test_a_failing_source_becomes_a_blind_spot_not_an_error(explore):  # noqa: F811
    import app.intelligence.explore as module
    explore.fetcher.payloads[module.FIRMS] = ConnectionError("FIRMS is down")

    class Fusion:
        def around(self, place, radius_km, latitude=None, longitude=None):
            return around(place={"name": "Los Angeles", "latitude": 34.05, "longitude": -118.24})
    PLACES["34.0500, -118.2400"] = ("Los Angeles, United States", 34.05, -118.24, "US")
    value = DeepLook(SimpleNamespace(fusion=Fusion(), model=None), explore).look("los angeles")
    gaps = [item for item in value["findings"] if item["level"] == "gap"]
    assert any(item["title"] == "Could not read fires" and "FIRMS is down" in item["detail"] for item in gaps)
    assert "Blind spots: Could not read fires" in deep_report(value)


def test_the_model_reading_is_labelled_inference(explore):  # noqa: F811
    replies = []

    class Model:
        def generate(self, messages, **options):
            replies.append(messages)
            return SimpleNamespace(content="Fire to the north-east is what matters; possibly smoke later.", model="local")
    value = {"place": {"name": "X"}, "radius_km": 25, "observed_at": "now", "situation": "watch", "findings": [], "metrics": {},
             "traffic": [], "fires": []}
    reading = DeepLook(SimpleNamespace(model=Model()), explore).read(value)
    assert reading["text"].startswith("Fire to the north-east") and "inference" in reading["note"]
    assert "Never invent" in replies[0][0].content and "not a measured intensity" in replies[0][0].content
    report_text = deep_report({**value, "deep_look": True, "basis": "b", "interpretation": "", "reading": reading})
    assert "HAYAT's reading (model inference): Fire to the north-east" in report_text


@pytest.mark.parametrize(("text", "arguments"), [
    ("deep look at Denver", {"place": "Denver"}), ("take a deep look at Makkah right now", {"place": "Makkah"}),
    ("sitrep for London", {"place": "London"}), ("give me a situation report on Lahore", {"place": "Lahore"}),
    ("deep analysis of Chicago", {"place": "Chicago", "reason": True}), ("analyse Dubai in depth", {"place": "Dubai", "reason": True}),
    ("deep look at me", {"place": "here"})])
def test_deep_look_sentences(text, arguments):
    assert deep_request(text) == {"tool": "intelligence_deep_look", "arguments": arguments}


@pytest.mark.parametrize("text", ["tell me everything about Einstein", "look at camera 3", "what is a deep look", "analyse this code"])
def test_other_sentences_are_not_deep_looks(text):
    assert deep_request(text) is None


def test_direct_plan_and_router_send_deep_looks_to_the_tool():
    from app.intelligence.tools import ROUTED_NAMES, direct_plan
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter
    from app.session import SessionState
    assert direct_plan("deep look at Denver", ROUTED_NAMES)["tool"] == "intelligence_deep_look"
    intent = IntentRouter().route("deep analysis of Chicago", SessionState())
    assert intent.intent == IntentKind.ACTION and intent.entities.get("modality") == "public_intelligence"


def test_the_explore_tab_offers_the_deep_look():
    from app.gui_intelligence_explore import ACTIONS, ICONS
    labels = [label for label, _tool, _build in ACTIONS]
    assert labels[:2] == ["Deep look", "Growing fires"] and ICONS["Deep look"] == "brain"
    tool, build = next((tool, build) for label, tool, build in ACTIONS if label == "Deep look")
    assert tool == "intelligence_deep_look" and build({"place": "Denver"}) == {"place": "Denver", "reason": True}
    assert Path(__file__).name and json  # keep imports honest


# ------------------------------------------------------------------ the interactive deep look
def sample_look():
    return {"deep_look": True, "place": {"name": "Denver, Colorado", "latitude": 39.74, "longitude": -104.98}, "radius_km": 25,
            "observed_at": "2026-10-11T20:45:00+00:00", "situation": "watch", "metrics": {"temperature_c": 28.5, "events": 25},
            "findings": [
                {"level": "watch", "title": "25 road reports nearby", "detail": "17 lane closure.", "evidence": ["I-25 (3 km)"],
                 "confidence": "high", "target": {"kind": "event", "id": "roads_cars_co:1"}},
                {"level": "watch", "title": "Active fire 91 km away", "detail": "SW.", "evidence": ["6 detections", "wind"],
                 "confidence": "medium", "target": {"kind": "point", "latitude": 39.2, "longitude": -105.8, "zoom": 9}},
                {"level": "info", "title": "Traffic on the 3 nearest cameras: light", "detail": "12; 2; 18.", "evidence": [],
                 "confidence": "medium", "target": {"kind": "camera", "id": "cameras_cars_co:7"}},
                {"level": "gap", "title": "Not covered here", "detail": "Ships: Baltic only.", "evidence": [], "confidence": "high"}],
            "reading": {"text": "Roads matter most; the fire is possibly worth watching.", "note": "inference"},
            "basis": "Every finding names its evidence.", "interpretation": ""}


def test_the_deep_look_view_draws_cards_filters_and_opens_targets(shared_tk):
    from app.gui_deep_look import DeepLookView
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    window = open_window(shared_tk, "1200x800-4000-4000")
    opened = []
    actions = {kind: (lambda value, kind=kind: opened.append((kind, value))) for kind in ("event", "camera", "point", "refresh", "reason", "atlas")}
    view = DeepLookView(window, Palette(), actions)
    view.pack(fill="both", expand=True)
    try:
        view.show(sample_look())
        window.update()
        assert len(view.cards) == 4 and view.place_label.cget("text") == "Denver, Colorado"
        assert view.gauge.find_all() and not view.compact
        view.set_filter("gap")
        assert [item["title"] for _index, item in view.visible_findings()] == ["Not covered here"] and len(view.cards) == 1
        view.set_filter("all")
        view.toggle(1)
        assert view.expanded == {1}
        for target in (sample_look()["findings"][index]["target"] for index in range(3)):
            view.open_target(target)
        assert [kind for kind, _value in opened] == ["event", "point", "camera"]
        view._act("reason")
        assert opened[-1][0] == "reason" and opened[-1][1]["situation"] == "watch"
        window.geometry("700x560-4000-4000")
        window.update()
        view.event_generate("<Configure>", width=700, height=560)
        window.update()
        assert view.compact and not view.metrics.winfo_ismapped()  # a narrow window: the strip, not the column
    finally:
        close_window(window)


def test_deep_look_scrolls_over_labels_and_keeps_position_when_opening_evidence(shared_tk):
    from app.gui_deep_look import DeepLookView
    from app.gui_motion import MOTION, set_motion
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    window = open_window(shared_tk, "1100x600-4000-4000")
    view = DeepLookView(window, Palette(), {})
    view.pack(fill="both", expand=True)
    value = sample_look()
    value['findings'] = [dict(value['findings'][0], title=f'Finding {index}') for index in range(40)]
    old_motion = MOTION['enabled']
    try:
        view.show(value)
        window.update()
        label = view.cards[0].winfo_children()[1].winfo_children()[1]
        set_motion(False)
        label.event_generate('<MouseWheel>', delta=-60)
        window.update()
        assert view.canvas.yview()[0] > 0  # small trackpad delta over a child label
        view.canvas.yview_moveto(.5)
        window.update()
        before = view.canvas.yview()[0]
        view.toggle(20)
        window.update()
        assert view.canvas.yview()[0] == pytest.approx(before, abs=.01)
        card = view.cards[20]
        window.geometry('1000x600-4000-4000')
        window.update()
        assert view.cards[20] is card  # resize rewraps; it must not rebuild the whole list
        assert view.canvas.yview()[0] == pytest.approx(before, abs=.02)
    finally:
        set_motion(old_motion)
        close_window(window)


def test_the_explore_tab_switches_to_the_deep_look_view_and_back(explorer, explore):  # noqa: F811
    from types import SimpleNamespace
    window, _gui = explorer
    window.notebook.select(window.tabs['explore'])
    window.handle_explore_result(SimpleNamespace(output=sample_look()))
    window.update()
    assert window.deep_view.winfo_ismapped() and not window.explore_body.winfo_ismapped()
    assert "situation watch" in window.explore_status.get()
    window.handle_explore_result(SimpleNamespace(output=explore.launches()))
    window.update()
    assert window.explore_body.winfo_ismapped() and not window.deep_view.winfo_ismapped()


def test_the_atlas_offers_a_deep_look_at_any_point(explore):  # noqa: F811
    import urllib.request
    from app.intelligence.map_server import IntelligenceMapServer
    asked = []

    class Deep:
        def look(self, **options):
            asked.append(options)
            return sample_look()
    server = IntelligenceMapServer(SimpleNamespace(deep=Deep()))
    try:
        with urllib.request.urlopen(server.url + "deep?lat=39.74&lon=-104.98&reason=1", timeout=10) as response:
            value = json.loads(response.read())
        assert value["situation"] == "watch" and asked == [{"radius_km": 25.0, "reason": True, "latitude": 39.74, "longitude": -104.98}]
    finally:
        server.close()
    root = Path(__file__).parents[1] / "app" / "assets" / "intelligence-map"
    assert 'id="deep-centre"' in (root / "index.html").read_text(encoding="utf-8")
    script = (root / "map.js").read_text(encoding="utf-8")
    assert "async function deepLook" in script and "findingCard" in script and "'Deep look here'" in script
