"""Wi-Fi sensing joined to the camera: the live timeline, announced changes, fused checks and the window."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.csi import CsiHub, csi_request, report
from app.sensing import HOLD, SensingMonitor, fuse, register_sensing_tools
from tests.test_csi import FakeSerial, WLAN, moving_frames, still_frames, wait_until, write_recording


class Events:
    def __init__(self):
        self.published = []

    def publish(self, event_type, source, payload=None, **_):
        self.published.append((event_type, source, payload))


class Registry:
    """Just enough registry: register(), names(), execute() for the camera tool."""

    def __init__(self, camera_output=None, camera_error=""):
        self.tools, self.calls = {}, []
        self.camera_output, self.camera_error = camera_output, camera_error

    def register(self, name, description, schema, risk, permission, handler):
        self.tools[name] = (permission, handler)

    def names(self):
        return list(self.tools) + (["camera_watch_change"] if self.camera_output is not None or self.camera_error else [])

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.camera_error:
            return SimpleNamespace(success=False, output=None, error=self.camera_error)
        return SimpleNamespace(success=True, output=self.camera_output, error="")


@pytest.fixture
def hub(tmp_path):
    made = CsiHub(tmp_path / "csi", run=lambda command, timeout: WLAN, serial_module=FakeSerial(ports=[]))
    yield made
    made.close_all()


def replay(hub, tmp_path, frames, name="room"):
    path = write_recording(tmp_path / f"{name}-{len(frames)}-{time.monotonic_ns()}.jsonl", frames)
    hub.connect(kind="file", path=str(path), name=name)
    assert wait_until(lambda: hub.status(name)["connections"][0]["state"] == "finished")


# ------------------------------------------------------------------ fusion

@pytest.mark.parametrize("wifi, camera, verdict", [
    ({"moving": True}, {"changed": True}, "confirmed"),
    ({"moving": True}, {"changed": False}, "wifi_only"),
    ({"moving": False}, {"changed": True}, "camera_only"),
    ({"moving": False}, {"changed": False}, "still"),
    ({"moving": True}, None, "moving"),
    ({"moving": True}, {"error": "the camera check was not allowed"}, "moving"),
    (None, {"changed": True}, "moving"),
    (None, None, "unknown"),
])
def test_fusion_says_which_sensor_saw_what(wifi, camera, verdict):
    fused = fuse(wifi, camera)
    assert fused["verdict"] == verdict
    if verdict == "wifi_only":
        assert "outside the camera's view" in fused["summary"]
    if verdict == "camera_only":
        assert "too small or too far from the radio path" in fused["summary"]
    if camera and camera.get("error"):
        assert "not allowed" in fused["summary"]


def test_fusion_says_where_the_camera_saw_it_and_that_light_is_not_movement():
    confirmed = fuse({"moving": True}, {"changed": True, "kind": "movement", "where": "bottom left"})
    assert confirmed["summary"] == "Movement confirmed: Wi-Fi and the camera both saw it (in the bottom left of the picture)."
    lamp = fuse({"moving": False}, {"changed": False, "kind": "lighting"})
    assert lamp["verdict"] == "still" and "only a change of light" in lamp["summary"]
    elsewhere = fuse({"moving": True}, {"changed": False, "kind": "lighting"})
    assert elsewhere["verdict"] == "wifi_only" and "saw only the light change" in elsewhere["summary"]


# ----------------------------------------------------------------- monitor

def test_the_monitor_scores_the_stream_and_announces_a_change_only_after_it_holds(hub, tmp_path):
    events = Events()
    monitor = SensingMonitor(hub, events=events, interval=60)  # ticks are driven by hand
    replay(hub, tmp_path, still_frames())
    first = monitor.tick()
    assert first["level"] == "still" and first["basis"] == "csi_amplitude" and first["autocorrelation"] < 0.2
    assert monitor.level == "unknown" and not events.published  # one reading is not enough
    for _ in range(HOLD - 1):
        monitor.tick()
    assert monitor.level == "still"
    assert events.published[-1][0] == "SENSING_MOTION" and events.published[-1][2]["to"] == "still"

    replay(hub, tmp_path, moving_frames())
    assert monitor.tick()["level"] == "strong movement" and monitor.level == "still"
    monitor.tick()
    assert monitor.level == "strong movement"
    change = events.published[-1][2]
    assert change["from"] == "still" and change["to"] == "strong movement" and change["connection"] == "room"
    assert len(monitor.snapshot()["history"]) == HOLD + 2


def test_frames_arriving_start_the_monitor_by_themselves(hub, tmp_path):
    monitor = SensingMonitor(hub, interval=0.05)
    replay(hub, tmp_path, still_frames())
    assert wait_until(lambda: len(monitor.history) >= 2)
    assert monitor.snapshot()["monitoring"]
    monitor.stop()


def test_privacy_mode_pauses_sensing_and_closes_the_radio(hub, tmp_path):
    events, private = Events(), {"on": False}
    monitor = SensingMonitor(hub, events=events, privacy=lambda: private["on"], interval=60)
    replay(hub, tmp_path, still_frames())
    assert monitor.tick() is not None
    private["on"] = True
    assert monitor.tick() is None
    assert hub.status()["connections"] == []
    assert events.published[-1][2] == {"level": "paused", "reason": "privacy mode"}
    assert monitor.status_report()["summary"] == "Wi-Fi sensing is paused: privacy mode is on."


def test_confirm_with_camera_runs_one_camera_check_when_movement_starts(hub, tmp_path):
    registry = Registry(camera_output={"changed": True, "peak_difference": 30.1})
    monitor = SensingMonitor(hub, registry=registry, interval=60)
    replay(hub, tmp_path, still_frames())
    for _ in range(HOLD):
        monitor.tick()
    assert registry.calls == []  # still: no camera
    monitor.set_camera_confirm(True)
    replay(hub, tmp_path, moving_frames())
    for _ in range(HOLD):
        monitor.tick()
    assert wait_until(lambda: monitor.last_fused is not None)
    assert registry.calls == [("camera_watch_change", {"seconds": 3.0, "interval": 0.3})]
    assert monitor.last_fused["verdict"] == "confirmed" and monitor.last_fused["trigger"] == "movement started"


def test_without_confirm_the_camera_never_opens(hub, tmp_path):
    registry = Registry(camera_output={"changed": True})
    monitor = SensingMonitor(hub, registry=registry, interval=60)
    replay(hub, tmp_path, moving_frames())
    for _ in range(HOLD + 2):
        monitor.tick()
    assert monitor.level == "strong movement" and registry.calls == []


def test_a_check_with_the_camera_fuses_both_and_a_refused_camera_says_so(hub, tmp_path):
    replay(hub, tmp_path, moving_frames())
    seen = SensingMonitor(hub, registry=Registry(camera_output={"changed": False})).check(with_camera=True, seconds=2)
    assert seen["verdict"] == "wifi_only" and seen["fused_view"] and "Wi-Fi:" in seen["text"]
    refused = SensingMonitor(hub, registry=Registry(camera_error="Privacy mode blocks camera_use")).check(
        with_camera=True, seconds=2)
    assert refused["verdict"] == "moving" and "Privacy mode blocks camera_use" in refused["summary"]
    alone = SensingMonitor(hub).check(with_camera=False, seconds=2)
    assert alone["camera"] is None and "camera was not part of this check" in alone["summary"]


def test_a_check_with_nothing_connected_says_so(hub):
    result = SensingMonitor(hub, registry=Registry(camera_output={"changed": False})).check(with_camera=True, seconds=2)
    assert result["verdict"] == "still" and "Camera only" in result["summary"]
    assert "No CSI source is connected" in result["text"]


def test_tools_and_chat(hub, tmp_path):
    registry = Registry(camera_output={"changed": True})
    monitor = register_sensing_tools(registry, SensingMonitor(hub, registry=registry, interval=60))
    assert registry.sensing is monitor
    assert registry.tools["sensing_check"][0] == "computer_observe" and registry.tools["sensing_status"][0] == "system_read"
    replay(hub, tmp_path, still_frames())
    monitor.tick()
    status = registry.tools["sensing_status"][1]()
    assert status["sensing_status_view"] and "Now: still" in status["summary"]
    text = report([{"tool": "sensing_status", "result": {"success": True, "output": status}, "verification": {"passed": True}}])
    assert text == status["summary"]
    assert csi_request("is anyone moving? check wifi sensing with the camera too") == {
        "tool": "sensing_check", "arguments": {"with_camera": True}}
    assert csi_request("what does the wifi sensing see")["tool"] == "sensing_status"
    assert csi_request("wifi sensing status")["tool"] == "sensing_status"


def test_runtime_has_the_monitor_wired_to_events_and_privacy():
    from app.events import EVENT_TYPES
    from app.runtime import build_runtime

    assert "SENSING_MOTION" in EVENT_TYPES
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        monitor = runtime.registry.sensing
        assert monitor.hub is runtime.registry.csi and monitor.events is runtime.events
        assert {"sensing_check", "sensing_status"} <= set(runtime.registry.names())
        state = runtime.registry.execute("sensory_fusion_status", {}).output
        assert state["wifi_sensing"]["available"] and state["wifi_sensing"]["connected"] is False
    finally:
        runtime.close()


# ------------------------------------------------------------------ window

def test_the_window_shows_the_live_waterfall_movement_and_camera_panel(shared_tk, hub, tmp_path):
    from app.gui_sensing import SensingStudio, reading_height, waterfall_pixels
    from app.gui_theme import Palette

    class Gui(Palette):
        pass

    registry = Registry(camera_output={"changed": False})
    registry.csi = hub
    monitor = register_sensing_tools(registry, SensingMonitor(hub, registry=registry, interval=60))
    registry.tools["csi_scan"] = ("system_read", hub.scan)

    def execute(name, arguments):
        if name == "camera_watch_change":
            return SimpleNamespace(success=True, output={"changed": False}, error="")
        handler = registry.tools[name][1] if name in registry.tools else getattr(hub, name.removeprefix("csi_"))
        return SimpleNamespace(success=True, output=handler(**arguments), error="")
    registry.execute = execute
    gui = Gui()
    gui.root = shared_tk
    gui.runtime = SimpleNamespace(registry=registry, authority=SimpleNamespace(privacy_mode=False))
    studio = SensingStudio(gui)
    try:
        studio.update()
        assert wait_until(lambda: (studio.update(), not studio.busy)[1], timeout=10)
        assert "CSI sources on this computer" in studio.log.get("1.0", "end")
        assert studio.lights["connected"].cget("text") == "○ Not connected"

        replay(hub, tmp_path, moving_frames())
        for _ in range(HOLD):
            monitor.tick()
        studio.paint()
        assert studio.lights["kind"].cget("text") == "Real CSI · 52 subcarriers"
        assert studio.level_var.get() == "Strong movement"
        assert studio._photo is not None and studio.waterfall.find_all()
        assert len(studio.timeline.find_withtag("all")) >= HOLD

        studio.check_with_camera()
        assert wait_until(lambda: (studio.update(), not studio.busy)[1], timeout=15)
        assert "outside the camera's view" in studio.fused_var.get()
        studio.confirm_var.set(True)
        studio._toggle_confirm()
        assert monitor.camera_confirm
    finally:
        studio.destroy()
    pixels = waterfall_pixels(still_frames(count=20), 100)
    assert pixels.shape == (52, 20) and pixels.dtype.name == "uint8"
    assert reading_height({"ratio": 12}) == 1.0 and reading_height({"basis": "csi_amplitude", "autocorrelation": 0.5}) == 0.5
