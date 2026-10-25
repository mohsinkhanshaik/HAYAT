"""Around me (the hazard radar) and the network guard."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.intelligence.radar import HeadsUp, bbox_around, bearing_degrees, radar, report
from app.network import NetworkInspector, network_request
from tests.test_network import IP_CONFIG, inspector


def row(identifier, lat, lon, severity="medium", category="earthquakes", observed="2026-10-15T00:00:00+00:00", **details):
    return {"id": identifier, "title": identifier, "headline": f"{identifier} headline", "category": category,
            "category_title": category.title(), "severity": severity,
            "severity_rank": {"high": 3, "medium": 2, "low": 1, "info": 0}[severity], "severity_basis": "rule",
            "latitude": lat, "longitude": lon, "observed_at": observed, "provider": "Agency", "url": "https://x",
            "details": details}


class Manager:
    def __init__(self, rows):
        self.rows, self.asked = rows, []
        self.fusion = SimpleNamespace(resolve=lambda place: {"name": "Southfield, MI", "latitude": 42.47, "longitude": -83.22})

    def _matching(self, query, feed, bbox, located_only, category, min_severity, since_hours):
        self.asked.append((bbox, located_only, since_hours))
        return [], self.rows


HOME = (42.47, -83.22)
ROWS = [
    row("quake-north", 43.5, -83.22, "high"),                                  # ~115 km N
    row("storm-east", 42.47, -80.0, "medium", "severe_weather"),              # ~260 km E
    row("road-close", 42.50, -83.20, "low", "traffic"),                         # 4 km: kept, roads matter close by
    row("road-far", 43.40, -83.22, "low", "traffic"),                           # 100 km: dropped
    row("camera", 42.48, -83.21, "info", "cameras"),                            # not a hazard
    row("far-away", 10.0, 10.0, "high"),                                        # outside the radius
    row("outbreak", 42.0, -84.0, "medium", "health", location_precision="country (centre of the country)"),
    row("roadworks-next-year", 42.46, -83.23, "medium", "traffic", observed="2099-01-01T00:00:00+00:00"),
]


def test_radar_keeps_hazards_within_reach_most_serious_then_nearest():
    manager = Manager(ROWS)
    view = radar(manager, radius_km=500, since_hours=72)
    titles = [item["id"] for item in view["hazards"]]
    assert titles[0] == "quake-north" and "far-away" not in titles and "camera" not in titles and "road-far" not in titles
    assert "road-close" in titles and view["home"]["name"] == "Southfield, MI"
    quake = view["hazards"][0]
    assert quake["bearing"] == "N" and 100 < quake["distance_km"] < 130 and quake["bearing_deg"] in (0.0, 360.0)
    storm = next(item for item in view["hazards"] if item["id"] == "storm-east")
    assert storm["bearing"] == "E" and 80 < storm["bearing_deg"] < 100
    assert next(item for item in view["hazards"] if item["id"] == "outbreak")["note"].startswith("placed at the centre")
    assert next(item for item in view["hazards"] if item["id"] == "roadworks-next-year")["scheduled"] is True
    assert view["serious"] == 3  # quake, storm, outbreak; scheduled road works do not count
    assert "Most serious: quake-north headline (high)" in view["summary"]
    bbox, located_only, hours = manager.asked[0]
    assert located_only is True and hours == 72 and bbox[1] < HOME[0] < bbox[3]


def test_radar_with_named_coordinates_and_an_empty_sky():
    view = radar(Manager([]), latitude=1.35, longitude=103.8, place="Singapore", radius_km=100)
    assert view["total"] == 0 and view["summary"] == "No hazards within 100 km of Singapore in the last 72 h."


def test_heads_up_once_per_serious_nearby_hazard():
    heads = HeadsUp(inner_km=300)
    view = radar(Manager(ROWS))
    first = [item["id"] for item in heads.new(view)]
    # The quake (115 km) and storm (260 km); the outbreak is country-level and the road works are scheduled.
    assert first == ["quake-north", "storm-east"]
    assert heads.new(view) == []


def test_geometry_helpers():
    assert bearing_degrees(0, 0, 1, 0) == 0.0 and bearing_degrees(0, 0, 0, 1) == 90.0
    west, south, east, north = bbox_around(42.0, -83.0, 111)
    assert round(north - south, 1) == 2.0 and east - west > 2.0


def test_radar_report_and_phrases():
    view = radar(Manager(ROWS))
    text = report([{"tool": "intelligence_radar", "result": {"success": True, "output": view}}])
    assert text.startswith(view["summary"]) and "HIGH" in text
    from app.intelligence.world_requests import world_request
    assert world_request("am I safe?") == {"tool": "intelligence_radar", "arguments": {}}
    assert world_request("any hazards near Dubai") == {"tool": "intelligence_radar", "arguments": {"place": "Dubai"}}
    assert world_request("what is happening") is None


def test_the_network_guard_learns_then_reports_new_devices(tmp_path):
    made = inspector(tmp_path)
    first = made.guard()
    assert first["baseline"] and first["new"] == [] and "Started watching" in first["summary"]
    assert made.guard()["new"] == []
    joined = {**IP_CONFIG, "neighbours": IP_CONFIG["neighbours"] + [
        {"IPAddress": "192.168.12.240", "LinkLayerAddress": "AA-BB-CC-11-22-33", "State": "Reachable"}]}
    made.powershell = lambda script, timeout: json.dumps(joined)
    later = made.guard()
    assert [row["ip"] for row in later["new"]] == ["192.168.12.240"] and "1 new device" in later["summary"]
    assert made.guard()["new"] == []  # reported once


def test_devices_can_be_named_by_ip_or_mac(tmp_path):
    made = inspector(tmp_path)
    made.guard()
    assert made.name_device("192.168.12.203", "living room TV")["name"] == "living room TV"
    assert made.name_device("5A-05-D9-4D-99-94", "Mohsin's phone")["mac"] == "5a:05:d9:4d:99:94"
    names = {row["ip"]: row["name"] for row in made.devices()["devices"]}
    assert names["192.168.12.203"] == "living room TV" and names["192.168.12.228"] == "Mohsin's phone"
    with pytest.raises(ValueError, match="has been seen"):
        made.name_device("192.168.12.99", "ghost")
    assert network_request("name device 192.168.12.203 as kitchen speaker") == {
        "tool": "network_name_device", "arguments": {"device": "192.168.12.203", "name": "kitchen speaker"}}
    assert network_request("any new devices on my wifi") == {"tool": "network_guard", "arguments": {}}


def test_guard_tick_announces_new_devices_in_the_chat():
    from app.gui_network import guard_tick
    import queue

    said = []
    gui = SimpleNamespace(_append=lambda tag, text: said.append(text),
                          runtime=SimpleNamespace(registry=SimpleNamespace(names=lambda: []),
                                                  authority=SimpleNamespace(privacy_mode=False)))
    gui._network_guard = {"next": 0, "busy": True, "results": queue.Queue()}
    gui._network_guard["results"].put({"new": [{"ip": "192.168.12.240", "mac": "aa:bb:cc:11:22:33", "private_address": True}]})
    guard_tick(gui)
    assert said and said[0].startswith("New device on your network: 192.168.12.240 (a private (randomised) address")


def test_the_radar_scope_draws_blips(shared_tk):
    from app.gui_radar import RadarScope
    from app.gui_theme import Palette

    scope = RadarScope(shared_tk, Palette, size=200)
    try:
        scope.show(radar(Manager(ROWS)))
        assert len(scope.find_withtag("blip")) == len(radar(Manager(ROWS))["hazards"])
        quake = next(item for _, item in scope._blips if item["id"] == "quake-north")
        x, y = scope._position(quake, 500)
        assert abs(x - 100) < 1 and y < 100  # north is up
    finally:
        scope.destroy()


def test_vendor_less_inspector_has_an_empty_book(tmp_path):
    assert NetworkInspector(tmp_path).book.load() == {}


def test_a_request_made_during_a_read_runs_next_and_replaces_it():
    from app.gui_radar import RadarReader
    import time

    calls = []

    def execute(name, arguments):
        calls.append(arguments)
        return SimpleNamespace(success=True, output={"home": {"latitude": 1.0, "longitude": 2.0, "name": "Here"},
                                                     "radius_km": arguments["radius_km"]}, error="")
    reader = RadarReader(SimpleNamespace(execute=execute, names=lambda: ["intelligence_radar"]))
    reader.read(radius_km=500)
    reader.read(radius_km=1000)  # while the first is running
    deadline = time.monotonic() + 5
    seen = []
    while time.monotonic() < deadline and not any(view.get("radius_km") == 1000 for view in seen):
        view = reader.take()
        if view:
            seen.append(view)
        time.sleep(0.02)
    assert [view["radius_km"] for view in seen] == [1000] and [call["radius_km"] for call in calls] == [500, 1000]
    assert calls[1]["latitude"] == 1.0  # 'here' is remembered after the first read


def test_the_radar_window_labels_its_rings_and_says_when_nothing_is_near(shared_tk):
    from app.gui_radar import RadarWindow
    from app.gui_theme import Palette

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(registry=SimpleNamespace(execute=lambda name, arguments: SimpleNamespace(
        success=False, output=None, error="not in this test"), names=lambda: []))
    window = RadarWindow(gui)
    try:
        labels = [window.scope.itemcget(item, "text") for item in window.scope.find_withtag("label")]
        assert labels == ["167 km", "333 km", "500 km"]  # before any reading arrives
        assert window.table._hayat_empty.cget("text").startswith("Reading the hazards")
        window.paint({"radius_km": 500, "hazards": [], "summary": "Nothing near Charlotte.", "by_category": {}, "note": ""})
        assert window.table._hayat_empty.cget("text").startswith("Nothing within 500 km in the last 3 days")
    finally:
        window.destroy()
