"""Wi-Fi channel state information: parse real device output, connect, check connected, record, sense movement.

No CSI hardware is needed: serial ports are faked, UDP runs on loopback, and
the built-in adapter's netsh output is a fixture.
"""

from __future__ import annotations

import json
import math
import random
import socket
import time

import pytest

from app.csi import (CsiFrame, CsiHub, CsiUnavailable, csi_request, direct_csi_plan, judge_motion, movement_score,
                     parse_line, parse_wlan_interfaces, report, _private_sender)

# ESP32-CSI-Tool (Steven Hernandez): type,role,mac,rssi,...,channel(16),secondary,local_timestamp(18),...,len,[data]
TOOL_LINE = ("CSI_DATA,AP,3c:71:bf:6d:2a:78,-73,11,1,7,1,1,1,1,0,0,0,-91,0,6,1,1529213,0,226,0,0,0,128,"
             "[3 4 0 5 -6 8 0 0]")
# Espressif esp-csi csi_recv_router: type,seq,mac,rssi,...,len,first_word,"[data]"
ESPRESSIF_LINE = ('CSI_DATA,12,1a:00:00:00:00:00,-40,11,1,6,0,1,0,1,0,1,0,-96,0,11,0,2245730,0,105,0,128,0,'
                  '"[0,0,4,3,8,6,-12,5]"')

WLAN = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : MediaTek Wi-Fi 6E MT7922 (RZ616) 160MHz Wireless LAN Card
    State                  : connected
    SSID                   : HOME
    AP BSSID               : 18:a5:ff:f5:bf:11
    Band                   : 5 GHz
    Channel                : 161
    Radio type             : 802.11ax
    Receive rate (Mbps)    : 1201
    Signal                 : 90%
    Rssi                   : -38
"""


def esp_line(amplitudes, mac="aa:bb:cc:dd:ee:01", rssi=-50):
    """A CSI_DATA line whose subcarriers have these amplitudes (real part only)."""
    pairs = " ".join(f"0 {round(value)}" for value in amplitudes)
    return f"CSI_DATA,STA,{mac},{rssi},11,1,7,1,1,1,1,0,0,0,-92,0,6,1,1000,0,128,0,0,0,{len(amplitudes) * 2},[{pairs}]"


def still_frames(count=60, start=1_700_000_000.0, jitter=0.4, seed=1):
    rng = random.Random(seed)
    base = [40 + 20 * math.sin(k / 5) for k in range(52)]
    return [CsiFrame(start + i * 0.1, "x", tuple(b + rng.uniform(-jitter, jitter) for b in base), mac="m")
            for i in range(count)]


def moving_frames(count=60, start=1_700_000_000.0, seed=2):
    rng = random.Random(seed)
    base = [40 + 20 * math.sin(k / 5) for k in range(52)]
    frames = []
    for i in range(count):
        # A body crossing the paths: each subcarrier fades in and out by up to a third, independently.
        frames.append(CsiFrame(start + i * 0.1, "x", tuple(b * (1 + 0.3 * math.sin(i / 3 + k) * rng.uniform(0.5, 1))
                                                          for k, b in enumerate(base)), mac="m"))
    return frames


def write_recording(path, frames):
    lines = [json.dumps({"hayat_csi_recording": 1, "source": "test"})]
    lines += [json.dumps(frame.to_record()) for frame in frames]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ parsing

def test_esp32_csi_tool_lines_become_amplitude_and_phase_per_subcarrier():
    frame = parse_line(TOOL_LINE, source="com5", now=10.0)
    assert frame.mac == "3c:71:bf:6d:2a:78" and frame.rssi == -73 and frame.channel == 6
    # Pairs are imaginary first: (3,4) -> real 4, imag 3 -> amplitude 5.
    assert frame.amplitudes == (5.0, 5.0, 10.0, 0.0)
    assert frame.phases[0] == pytest.approx(math.atan2(3, 4))
    assert frame.device_time == pytest.approx(1.529213)
    assert frame.received_at == 10.0 and frame.kind == "csi"


def test_espressif_esp_csi_lines_with_a_quoted_comma_array_parse_too():
    frame = parse_line(ESPRESSIF_LINE)
    assert frame.mac == "1a:00:00:00:00:00" and frame.rssi == -40 and frame.channel == 11
    assert frame.amplitudes == (0.0, 5.0, 10.0, 13.0)


def test_json_lines_in_every_documented_shape():
    assert parse_line('{"amplitude": [1, 2, 3], "rssi": -60, "mac": "AA:BB:CC:DD:EE:FF"}').amplitudes == (1, 2, 3)
    assert parse_line('{"real": [3], "imag": [4]}').amplitudes == (5.0,)
    assert parse_line('{"csi": [[3, 4], [6, 8]]}').amplitudes == (5.0, 10.0)
    assert parse_line('{"csi": [4, 3]}').amplitudes == (5.0,)  # flat list, ESP32 order: imaginary first
    link = parse_line('{"kind": "link", "rssi": -41, "t": 1700000000.5}')
    assert link.kind == "link" and link.rssi == -41 and link.received_at == 1700000000.5


def test_boot_logs_headers_and_junk_are_not_frames():
    for noise in ("", "I (312) wifi:mode : sta (24:0a:c4:00:00:01)", "ets Jun  8 2016 00:22:57",
                  '{"hayat_csi_recording": 1}', "CSI_DATA,STA,no array here", "{not json", "x" * 20_000):
        assert parse_line(noise) is None


def test_netsh_output_reads_the_adapter_and_its_signal():
    adapter = parse_wlan_interfaces(WLAN)[0]
    assert adapter["state"] == "connected" and adapter["channel"] == 161 and adapter["rssi"] == -38
    assert adapter["bssid"] == "18:a5:ff:f5:bf:11" and adapter["signal_percent"] == 90
    no_rssi = parse_wlan_interfaces(WLAN.replace("    Rssi                   : -38\n", ""))[0]
    assert no_rssi["rssi"] == -55  # Windows' quality-to-dBm mapping


# ------------------------------------------------------------------ sensing

def test_movement_is_far_above_a_still_room_and_judged_against_its_baseline():
    still, moving = movement_score(still_frames()), movement_score(moving_frames())
    assert still["basis"] == "csi_amplitude" and still["subcarriers_used"] == 52
    assert moving["score"] > 5 * still["score"]
    baseline = {"score": still["score"], "basis": "csi_amplitude"}
    assert judge_motion(movement_score(still_frames(seed=9)), baseline)["level"] == "still"
    verdict = judge_motion(moving, baseline)
    assert verdict["level"] == "strong movement" and verdict["calibrated"] and verdict["moving"]
    assert "a pet, a fan or a door" in verdict["summary"]


def test_gain_changes_that_move_every_subcarrier_together_are_not_movement():
    frames = still_frames()
    for i, frame in enumerate(frames):  # the radio's automatic gain stepping up and down
        frame.amplitudes = tuple(value * (1.5 if i % 2 else 0.7) for value in frame.amplitudes)
    assert movement_score(frames)["score"] < 0.03


def test_too_few_frames_or_an_uncalibrated_source_say_so():
    assert movement_score(still_frames(count=5))["score"] is None
    assert judge_motion(movement_score(still_frames(count=5)), None)["level"] == "unknown"
    uncalibrated = judge_motion(movement_score(still_frames()), None)
    assert not uncalibrated["calibrated"] and "calibrate CSI" in uncalibrated["summary"]
    link = [CsiFrame(float(i), kind="link", rssi=-40 + (6 if i % 2 else 0)) for i in range(10)]
    verdict = judge_motion(movement_score(link), None)
    assert verdict["level"] == "strong movement" and "not CSI" in verdict["summary"]


# ---------------------------------------------------------------- connecting

class FakePort:
    def __init__(self, lines):
        self.lines = list(lines)
        self.closed = False

    def readline(self):
        if self.lines:
            return (self.lines.pop(0) + "\n").encode()
        time.sleep(0.01)
        return b""

    def close(self):
        self.closed = True


class FakeSerial:
    """pyserial's surface that app/csi.py uses: Serial(port, baud, timeout) and tools.list_ports.comports()."""

    class _Info:
        def __init__(self, device, description, vid=None):
            self.device, self.description, self.vid = device, description, vid
            self.manufacturer = self.product = ""

    def __init__(self, lines=(), fail=False, ports=None):
        self._lines, self.fail, self.opened = list(lines), fail, []
        listed = ports if ports is not None else [("COM3", "Intel(R) Active Management Technology - SOL", None),
                                                  ("COM5", "Silicon Labs CP210x USB to UART Bridge", 0x10C4)]
        self.tools = type("tools", (), {"list_ports": type("lp", (), {
            "comports": staticmethod(lambda: [self._Info(*row) for row in listed])})})

    def Serial(self, port, baud, timeout):
        if self.fail:
            raise OSError(f"could not open port '{port}': FileNotFoundError")
        self.opened.append((port, baud))
        return FakePort(self._lines)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def hub(tmp_path):
    made = []

    def build(**kwargs):
        kwargs.setdefault("run", lambda command, timeout: WLAN)
        made.append(CsiHub(tmp_path / "csi", **kwargs))
        return made[-1]
    yield build
    for item in made:
        item.close_all()


def test_scan_marks_the_esp32_bridge_and_says_the_wifi_card_gives_no_csi(hub):
    found = hub(serial_module=FakeSerial()).scan()
    assert [p["port"] for p in found["serial_ports"]] == ["COM5", "COM3"]
    assert found["serial_ports"][0]["likely_csi_device"] and not found["serial_ports"][1]["likely_csi_device"]
    assert found["real_csi_source_found"] and "connect CSI on COM5" in found["advice"]
    assert found["wifi_adapters"][0]["csi_capable"] is False and found["wifi_adapters"][0]["rssi"] == -38
    text = report([{"tool": "csi_scan", "result": {"success": True, "output": found}, "verification": {"passed": True}}])
    assert "COM5" in text and "looks like an ESP32" in text and "no CSI" in text


def test_a_serial_esp32_connects_and_status_says_it_is_receiving_real_csi(hub):
    lines = ["I (312) wifi:mode : sta"] + [esp_line([30 + k for k in range(64)]) for _ in range(40)]
    serial = FakeSerial(lines)
    csi = hub(serial_module=serial)
    connected = csi.connect(port="com5")
    assert serial.opened == [("COM5", 921600)]
    assert connected["connected"] and connected["kind"] == "serial" and connected["name"] == "com5"
    assert wait_until(lambda: csi.status()["connections"][0]["frames_total"] == 40)
    status = csi.status()
    item = status["connections"][0]
    assert status["connected"] and status["receiving"] and status["real_csi"]
    assert item["subcarriers"] == 64 and item["rssi"] == -50 and item["unparsed_lines"] == 1
    assert item["transmitters"] == ["aa:bb:cc:dd:ee:01"]
    assert "real CSI, 64 subcarriers" in status["summary"]


def test_auto_picks_the_esp32_port_and_falls_back_to_the_adapter_without_one(hub):
    csi = hub(serial_module=FakeSerial([esp_line([5] * 8)]))
    assert csi.connect()["kind"] == "serial"
    fallback = hub(serial_module=FakeSerial(ports=[]), link_interval=0.05).connect()
    assert fallback["kind"] == "wifi_link" and "not CSI" in fallback["note"]


def test_an_unplugged_port_is_reported_as_not_connected_and_retried(hub):
    csi = hub(serial_module=FakeSerial(fail=True))
    result = csi.connect(port="COM9", wait_seconds=0.3)
    assert wait_until(lambda: csi.status()["connections"][0]["state"] == "reconnecting")
    status = csi.status()
    assert not status["connected"] and not status["receiving"]
    assert "could not open port" in status["connections"][0]["error"] and "retrying" in status["summary"]
    assert result["name"] == "com9"


def test_an_open_port_with_no_frames_is_not_called_working(hub):
    csi = hub(serial_module=FakeSerial(["just boot noise"]))
    csi.connect(port="COM5", wait_seconds=0.2)
    status = csi.status()
    assert status["connected"] and not status["receiving"]
    assert status["summary"].startswith("Connected but nothing is arriving") and "no frames have arrived" in status["summary"]


def test_the_builtin_adapter_streams_signal_strength_labelled_not_csi(hub):
    csi = hub(serial_module=FakeSerial(ports=[]), link_interval=0.02)
    csi.connect(kind="wifi_link")
    assert wait_until(lambda: csi.status()["connections"][0]["frames_total"] >= 5)
    status = csi.status()
    assert status["receiving"] and not status["real_csi"]
    assert "signal strength only (not CSI)" in status["summary"] and "RSSI -38 dBm" in status["summary"]


def test_an_adapter_without_a_network_cannot_stand_in(hub):
    with pytest.raises(CsiUnavailable, match="not connected"):
        hub(run=lambda command, timeout: WLAN.replace("connected", "disconnected")).connect(kind="wifi_link")


def test_udp_frames_from_the_local_network_are_received_and_recorded(hub, tmp_path):
    csi = hub(serial_module=FakeSerial(ports=[]))
    seen = []
    csi.subscribe(lambda name, frame: seen.append(name))
    connected = csi.connect(kind="udp", udp_port=0, host="127.0.0.1", record=True, wait_seconds=0)
    port = int(connected["target"].split()[-1])
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(12):
            sender.sendto((esp_line([20] * 32) + "\n" + esp_line([21] * 32)).encode(), ("127.0.0.1", port))
    finally:
        sender.close()
    assert wait_until(lambda: csi.status()["connections"][0]["frames_total"] == 24)
    status = csi.status()["connections"][0]
    assert status["receiving"] and status["is_csi"] and status["subcarriers"] == 32
    assert seen and set(seen) == {connected["name"]}
    recording = status["recording"]
    csi.disconnect()
    assert csi.status()["connections"] == [] and not csi.status()["connected"]

    listed = csi.recordings()
    assert listed["count"] == 1 and listed["recordings"][0]["frames"] == 24 and listed["recordings"][0]["kind"] == "udp"
    # The recording replays as a source of its own.
    replay = csi.connect(kind="file", path=listed["recordings"][0]["file"])
    assert wait_until(lambda: csi.status()["connections"][0]["state"] == "finished")
    assert csi.status()["connections"][0]["frames_total"] == 24 and replay["kind"] == "file"
    assert recording.endswith(".jsonl")


def test_udp_drops_packets_from_a_sender_the_owner_did_not_name(hub):
    csi = hub(serial_module=FakeSerial(ports=[]))
    connected = csi.connect(kind="udp", udp_port=0, host="127.0.0.1", allow_from="10.9.9.9", wait_seconds=0)
    port = int(connected["target"].split()[-1])
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(esp_line([20] * 8).encode(), ("127.0.0.1", port))
    finally:
        sender.close()
    assert wait_until(lambda: csi.status()["connections"][0].get("dropped_outside_packets") == 1)
    assert csi.status()["connections"][0]["frames_total"] == 0
    assert _private_sender("192.168.1.20") and _private_sender("127.0.0.1") and not _private_sender("8.8.8.8")


def test_calibrate_then_motion_on_replayed_recordings(hub, tmp_path):
    csi = hub(serial_module=FakeSerial(ports=[]))
    still = write_recording(tmp_path / "still.jsonl", still_frames())
    csi.connect(kind="file", path=str(still), name="room")
    assert wait_until(lambda: csi.status()["connections"][0]["state"] == "finished")
    saved = csi.calibrate("room")
    assert saved["baseline"]["frames"] == 60 and (tmp_path / "csi" / "baselines.json").is_file()
    assert csi.motion("room")["level"] == "still"

    moving = write_recording(tmp_path / "moving.jsonl", moving_frames())
    csi.connect(kind="file", path=str(moving), name="room")  # the same name: judged against the same baseline
    assert wait_until(lambda: csi.status("room")["connections"][0]["frames_total"] == 60)
    verdict = csi.motion("room")
    assert verdict["level"] == "strong movement" and verdict["calibrated"] and verdict["ratio_to_still"] > 3


def test_connection_limits_and_unknown_names(hub, tmp_path):
    csi = hub(serial_module=FakeSerial(ports=[]))
    with pytest.raises(ValueError, match="No CSI source is connected"):
        csi.motion()
    with pytest.raises(ValueError, match="No CSI file"):
        csi.connect(kind="file", path="missing.jsonl")
    with pytest.raises(ValueError, match="Name the serial port"):
        csi.connect(kind="serial")
    for index in range(4):
        csi.connect(kind="udp", udp_port=0, host="127.0.0.1", name=f"u{index}", wait_seconds=0)
    with pytest.raises(ValueError, match="disconnect one first"):
        csi.connect(kind="udp", udp_port=0, host="127.0.0.1", name="u5", wait_seconds=0)
    assert csi.disconnect("u0")["count"] == 1
    with pytest.raises(ValueError, match="No CSI connection named"):
        csi.status("nope")


# ------------------------------------------------------------------- chat

@pytest.mark.parametrize("text, tool, arguments", [
    ("is csi connected?", "csi_status", {}),
    ("check if CSI is connected", "csi_status", {}),
    ("check the wifi sensing", "csi_status", {}),
    ("CSI status", "csi_status", {}),
    ("connect CSI", "csi_connect", {}),
    ("connect csi on com5", "csi_connect", {"kind": "serial", "port": "COM5"}),
    ("Hayat, connect CSI on UDP port 5500 and record", "csi_connect", {"kind": "udp", "udp_port": 5500, "record": True}),
    ("connect csi from file office.jsonl", "csi_connect", {"kind": "file", "path": "office.jsonl"}),
    ("connect channel state information using the built-in adapter", "csi_connect", {"kind": "wifi_link"}),
    ("Is anyone moving? Check the Wi-Fi sensing", "csi_motion", {}),
    ("calibrate CSI", "csi_calibrate", {}),
    ("show CSI recordings", "csi_recordings", {}),
    ("scan for CSI devices", "csi_scan", {}),
    ("disconnect csi", "csi_disconnect", {}),
])
def test_owner_phrases_reach_the_csi_tools(text, tool, arguments):
    assert csi_request(text) == {"tool": tool, "arguments": arguments}
    assert direct_csi_plan(text, [tool])[0]["tool"] == tool


@pytest.mark.parametrize("text", ["what is channel state information?", "explain CSI", "watch CSI Miami",
                                  "open notepad", "is the camera connected"])
def test_questions_and_unrelated_sentences_stay_out(text):
    assert csi_request(text) is None


def test_router_sends_csi_requests_to_the_tools():
    from app.intent.router import IntentRouter
    from app.session import SessionState

    assert IntentRouter(None).route("is CSI connected?", SessionState()).entities == {"modality": "csi"}


def test_chat_report_is_written_from_the_status_not_the_model():
    status = {"status_view": True, "summary": "No CSI source is connected. Windows Wi-Fi drivers do not expose CSI."}
    event = {"tool": "csi_status", "result": {"success": True, "output": status}, "verification": {"passed": True}}
    assert report([event]) == status["summary"]
    assert report([{**event, "verification": {"passed": False}}]) is None
    assert report([{"tool": "list_devices", "result": {"success": True, "output": {}}, "verification": {"passed": True}}]) is None


def test_the_assistant_answers_csi_questions_from_the_tools(tmp_path):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        assert {"csi_connect", "csi_status"} <= set(runtime.registry.names())
        from app.config import PROJECT_ROOT

        # conftest moves recordings and baselines into the test sandbox, away from the owner's data/.
        assert (PROJECT_ROOT / "data") not in runtime.registry.csi.root.resolve().parents
        reply = runtime.assistant.handle("is CSI connected?").display_response
        assert "No CSI source is connected" in reply and "ESP32" in reply
        recording = write_recording(tmp_path / "room.jsonl", still_frames())
        reply = runtime.assistant.handle(f"connect csi from file {recording.as_posix()}").display_response
        assert "replayed 60 frames" in reply
    finally:
        runtime.close()


def test_a_replay_in_progress_is_described_without_a_frame_age():
    from app.csi import _status_sentence

    status = {"name": "room", "target": "room.jsonl", "state": "open", "kind": "file", "receiving": False,
              "frames_total": 40, "connected_for_s": 0.2, "last_frame_age_s": None}
    assert _status_sentence(status) == "room: replaying room.jsonl, 40 frames so far"
