"""Wi-Fi channel state information (CSI): connect a source, check it is live, record it, sense movement.

CSI is what a Wi-Fi receiver measures about the channel for every subcarrier
of a packet: an amplitude and a phase. People moving through a room bend and
block those paths, so the amplitudes start to wobble; that wobble is what
Wi-Fi sensing reads.

What hardware gives CSI matters more than the code:

* Windows Wi-Fi drivers do not hand CSI to programs. This computer's card
  (MediaTek MT7922) is no exception, so HAYAT never calls anything from it CSI.
* Real CSI comes from a device whose firmware exports it: an ESP32 running
  ESP32-CSI-Tool or Espressif's esp-csi example (USB serial, ``CSI_DATA,...``
  lines), or a Linux machine with Nexmon / Intel 5300 / Atheros tools that
  forwards frames over UDP as ``CSI_DATA`` lines or JSON lines.
* Without one, the built-in adapter still reports its signal strength (RSSI)
  about once a second. That is offered as ``wifi_link``, labelled as link
  quality, never as CSI. It can notice someone walking between this computer
  and the router; it cannot see breathing or gestures.

``CsiHub`` keeps the connections. Each runs on its own thread with a bounded
frame buffer, reconnects when a port is unplugged, can record every frame to
``data/sensing/csi/recordings`` (JSON lines, replayable with kind="file"), and
calls subscribers with each frame so later features can build on the same
stream. Movement is judged against a still-room baseline the owner records with
``csi_calibrate``; without one the answer says it is uncalibrated.

Sensing is observation, so the tools use computer_observe and privacy mode
switches them off like the camera and screen.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import re
import socket
import subprocess
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

CSI_ROOT = PROJECT_ROOT / "data" / "sensing" / "csi"
SOURCE_KINDS = ("serial", "udp", "file", "wifi_link")
CSI_TOOL_NAMES = ("csi_scan", "csi_connect", "csi_status", "csi_disconnect", "csi_motion", "csi_calibrate",
                  "csi_recordings")

#: ESP32-CSI-Tool and esp-csi both print at this rate; 115200 drops most frames.
DEFAULT_BAUD = 921600
DEFAULT_UDP_PORT = 5500
MAX_CONNECTIONS = 4
#: Frames kept in memory per connection: about 30 s at 100 frames a second.
BUFFER_FRAMES = 3000
#: A recording stops by itself here (about 2 hours at 100 frames a second).
MAX_RECORDING_BYTES = 512 * 1024 * 1024
MAX_LINE_CHARS = 16_384
#: A connection whose last frame is older than this is open but not receiving.
STALE_SECONDS = {"wifi_link": 5.0}
DEFAULT_STALE_SECONDS = 3.0
RETRY_SECONDS = 2.0

#: USB-serial bridges ESP32 boards ship with, and the ESP32-S3/C3 built-in USB.
_ESP32_HINTS = re.compile(r"(?i)cp210|silicon labs|ch34\d|ch910|wch|ftdi|usb.serial|usb jtag|esp32|espressif")
_ESP32_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}
_MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.I)

HOW_TO_GET_CSI = (
    "Windows Wi-Fi drivers do not expose CSI. For real CSI, flash an ESP32 with ESP32-CSI-Tool or Espressif "
    "esp-csi and plug it in by USB (then: connect CSI on COM<n>), or have a Linux box with Nexmon or an "
    "Intel 5300 forward frames as CSI_DATA or JSON lines over UDP (connect CSI on UDP port 5500)."
)


class CsiUnavailable(RuntimeError):
    """A source cannot be opened on this computer, with the reason in plain words."""


# --------------------------------------------------------------------- frames
@dataclass(slots=True)
class CsiFrame:
    received_at: float
    source: str = ""
    amplitudes: tuple[float, ...] = ()
    phases: tuple[float, ...] = ()
    rssi: float | None = None
    mac: str = ""
    channel: int | None = None
    device_time: float | None = None
    #: "csi" for per-subcarrier data, "link" for signal strength only.
    kind: str = "csi"

    @property
    def subcarriers(self) -> int:
        return len(self.amplitudes)

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {"t": round(self.received_at, 4), "kind": self.kind}
        for key, value in (("rssi", self.rssi), ("mac", self.mac), ("channel", self.channel),
                           ("device_time", self.device_time)):
            if value not in (None, ""):
                record[key] = value
        if self.amplitudes:
            record["amplitude"] = [round(value, 2) for value in self.amplitudes]
        if self.phases:
            record["phase"] = [round(value, 3) for value in self.phases]
        return record


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).strip().strip('"'))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _from_pairs(values: list[float], imaginary_first: bool) -> tuple[tuple[float, ...], tuple[float, ...]]:
    if len(values) % 2:
        values = values[:-1]
    amplitudes, phases = [], []
    for index in range(0, len(values), 2):
        first, second = values[index], values[index + 1]
        imag, real = (first, second) if imaginary_first else (second, first)
        amplitudes.append(math.hypot(real, imag))
        phases.append(math.atan2(imag, real))
    return tuple(amplitudes), tuple(phases)


def _parse_esp32(line: str, source: str, now: float) -> CsiFrame | None:
    """ESP32-CSI-Tool / esp-csi: ``CSI_DATA,<header fields...>,[imag real imag real ...]``.

    Both print mac at field 2, rssi at 3, channel at 16 and the radio's
    microsecond clock at 18; each subcarrier is an int8 pair, imaginary first
    (ESP-IDF's wifi_csi_info_t layout).
    """
    start, end = line.find("["), line.rfind("]")
    if start < 0 or end <= start:
        return None
    raw = re.split(r"[\s,]+", line[start + 1:end].strip())
    values = [number for number in (_number(item) for item in raw if item) if number is not None]
    if len(values) < 2:
        return None
    header = [part.strip().strip('"') for part in line[line.find("CSI_DATA"):start].split(",")]
    amplitudes, phases = _from_pairs(values, imaginary_first=True)

    def field_at(index: int) -> str:
        return header[index] if len(header) > index else ""

    mac = field_at(2) if _MAC.match(field_at(2)) else ""
    channel = _number(field_at(16))
    device_time = _number(field_at(18))
    return CsiFrame(now, source, amplitudes, phases, rssi=_number(field_at(3)), mac=mac.lower(),
                    channel=int(channel) if channel is not None and 0 < channel < 200 else None,
                    device_time=device_time / 1e6 if device_time is not None else None)


def _parse_json(line: str, source: str, now: float) -> CsiFrame | None:
    """JSON lines from any forwarder, and HAYAT's own recordings.

    Accepted: ``amplitude`` (+ optional ``phase``); ``real`` and ``imag``
    lists; or ``csi`` as [[real, imag], ...] pairs or a flat ESP32-order list
    (imaginary first). Optional: rssi, mac, channel, t / timestamp.
    """
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict) or "hayat_csi_recording" in data:
        return None
    amplitudes: tuple[float, ...] = ()
    phases: tuple[float, ...] = ()
    if isinstance(data.get("amplitude"), list):
        amplitudes = tuple(float(value) for value in data["amplitude"] if _number(value) is not None)
        if isinstance(data.get("phase"), list):
            phases = tuple(float(value) for value in data["phase"] if _number(value) is not None)
    elif isinstance(data.get("real"), list) and isinstance(data.get("imag"), list):
        pairs = [(float(r), float(i)) for r, i in zip(data["real"], data["imag"])
                 if _number(r) is not None and _number(i) is not None]
        amplitudes = tuple(math.hypot(r, i) for r, i in pairs)
        phases = tuple(math.atan2(i, r) for r, i in pairs)
    elif isinstance(data.get("csi"), list) and data["csi"]:
        if isinstance(data["csi"][0], (list, tuple)):
            pairs = [(float(p[0]), float(p[1])) for p in data["csi"] if isinstance(p, (list, tuple)) and len(p) >= 2]
            amplitudes = tuple(math.hypot(r, i) for r, i in pairs)
            phases = tuple(math.atan2(i, r) for r, i in pairs)
        else:
            amplitudes, phases = _from_pairs([float(v) for v in data["csi"] if _number(v) is not None], True)
    rssi = _number(data.get("rssi"))
    kind = str(data.get("kind") or ("csi" if amplitudes else "link"))
    if not amplitudes and rssi is None:
        return None
    stamp = _number(data.get("t", data.get("timestamp")))
    channel = _number(data.get("channel"))
    return CsiFrame(stamp if stamp is not None and stamp > 1e9 else now, source, amplitudes, phases, rssi=rssi,
                    mac=str(data.get("mac") or "").lower(), channel=int(channel) if channel else None,
                    device_time=_number(data.get("device_time")), kind="link" if kind == "link" else "csi")


def parse_line(line: str, *, source: str = "", now: float | None = None) -> CsiFrame | None:
    """One text line from a CSI source as a frame, or None for boot logs and noise."""
    text = str(line or "").strip()
    if not text or len(text) > MAX_LINE_CHARS:
        return None
    moment = time.time() if now is None else now
    if text.startswith("{"):
        return _parse_json(text, source, moment)
    if "CSI_DATA" in text:
        return _parse_esp32(text, source, moment)
    return None


# -------------------------------------------------------- built-in adapter
def _command(command: str, timeout: float) -> str:
    """Run a command directly; netsh answers in ~50 ms, PowerShell would add a second of start-up per reading."""
    completed = subprocess.run(command.split(), capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
    return completed.stdout


def parse_wlan_interfaces(text: str) -> list[dict[str, Any]]:
    """``netsh wlan show interfaces`` as one dict per adapter (English Windows labels)."""
    adapters: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for raw in str(text or "").splitlines():
        if ":" not in raw:
            continue
        key, value = (part.strip() for part in raw.split(":", 1))
        key = key.lower()
        if key == "name":
            if current:
                adapters.append(current)
            current = {"name": value}
        elif current:
            current[key] = value
    if current:
        adapters.append(current)
    result = []
    for item in adapters:
        signal = _number(str(item.get("signal", "")).rstrip("%"))
        rssi = _number(item.get("rssi"))
        if rssi is None and signal is not None:
            rssi = signal / 2 - 100  # Windows' own mapping from quality % to dBm
        channel = _number(item.get("channel"))
        result.append({
            "name": item.get("name", ""), "description": item.get("description", ""),
            "state": item.get("state", ""), "ssid": item.get("ssid", ""), "bssid": item.get("ap bssid", ""),
            "band": item.get("band", ""), "channel": int(channel) if channel else None,
            "radio": item.get("radio type", ""), "signal_percent": signal, "rssi": rssi,
            "receive_mbps": _number(item.get("receive rate (mbps)")),
        })
    return result


def _read_wlan(run: Callable[[str, float], str]) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        return parse_wlan_interfaces(run("netsh wlan show interfaces", 10))
    except Exception:
        log_swallowed(_log, "reading the Wi-Fi adapter")
        return []


# ----------------------------------------------------------------- readers
def _serial_module() -> Any:
    try:
        import serial  # type: ignore[import-untyped]
        import serial.tools.list_ports  # type: ignore[import-untyped]  # noqa: F401
    except ImportError as exc:
        raise CsiUnavailable("The pyserial package is missing; install requirements.txt to read a USB CSI device.") from exc
    return serial


# Each opener opens its source before returning, so a missing port or a port
# already in use is an error at connect time, not a silent "open" state.
def _lines_from_serial(settings: dict[str, Any], stop: threading.Event, serial_module: Any) -> Iterator[str]:
    port = serial_module.Serial(settings["port"], int(settings.get("baud") or DEFAULT_BAUD), timeout=0.5)

    def lines() -> Iterator[str]:
        try:
            while not stop.is_set():
                raw = port.readline()
                # An empty read is a heartbeat, so the loop sees stop() promptly.
                yield raw.decode("utf-8", errors="replace") if raw else ""
        finally:
            port.close()
    return lines()


def _private_sender(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _lines_from_udp(settings: dict[str, Any], stop: threading.Event) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Devices on the local network send here, so the default listens on every interface.
        sock.bind((settings.get("host") or "0.0.0.0", int(settings["udp_port"])))
        sock.settimeout(0.5)
    except OSError:
        sock.close()
        raise
    settings["bound_port"] = sock.getsockname()[1]
    allowed = str(settings.get("allow_from") or "").strip()

    def lines() -> Iterator[str]:
        try:
            while not stop.is_set():
                try:
                    data, (sender, _port) = sock.recvfrom(65535)
                except socket.timeout:
                    yield ""
                    continue
                # A CSI device sits on the owner's own network; packets from the
                # internet are dropped unless the owner named the sender.
                if (allowed and sender != allowed) or (not allowed and not _private_sender(sender)):
                    settings["dropped"] = int(settings.get("dropped") or 0) + 1
                    continue
                yield from data.decode("utf-8", errors="replace").splitlines()
        finally:
            sock.close()
    return lines()


def _lines_from_file(settings: dict[str, Any], stop: threading.Event) -> Iterator[str]:
    handle = open(settings["path"], encoding="utf-8", errors="replace")  # closed by the generator below

    def lines() -> Iterator[str]:
        with handle:
            for line in handle:
                if stop.is_set():
                    return
                yield line
    return lines()


# -------------------------------------------------------------- connection
@dataclass
class Connection:
    name: str
    kind: str
    settings: dict[str, Any]
    opened_at: float
    state: str = "connecting"
    error: str = ""
    frames: deque = field(default_factory=lambda: deque(maxlen=BUFFER_FRAMES))
    total: int = 0
    unparsed: int = 0
    last_frame_at: float | None = None
    transmitters: Counter = field(default_factory=Counter)
    recording: Path | None = None
    recording_bytes: int = 0
    recording_note: str = ""
    stop_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def target(self) -> str:
        if self.kind == "serial":
            return f"{self.settings['port']} at {self.settings.get('baud') or DEFAULT_BAUD} baud"
        if self.kind == "udp":
            return f"UDP port {self.settings.get('bound_port') or self.settings['udp_port']}"
        if self.kind == "file":
            return str(self.settings["path"])
        return str(self.settings.get("adapter") or "the built-in Wi-Fi adapter")


class CsiHub:
    """Every CSI connection HAYAT holds, and what each one is receiving."""

    def __init__(self, root: Path | None = None, *, run: Callable[[str, float], str] = _command,
                 serial_module: Any = None, clock: Callable[[], float] = time.time,
                 link_interval: float = 1.0) -> None:
        self.root = Path(root or CSI_ROOT)
        self.run = run
        self._serial = serial_module
        self.clock = clock
        self.link_interval = link_interval
        self._connections: dict[str, Connection] = {}
        self._subscribers: list[Callable[[str, CsiFrame], None]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------- discovery
    def _serial_api(self) -> Any:
        if self._serial is None:
            self._serial = _serial_module()
        return self._serial

    def serial_ports(self) -> list[dict[str, Any]]:
        try:
            serial = self._serial_api()
            ports = serial.tools.list_ports.comports()
        except CsiUnavailable:
            return []
        except Exception:
            log_swallowed(_log, "listing serial ports")
            return []
        result = []
        for port in ports:
            described = " ".join(str(part or "") for part in (port.description, getattr(port, "manufacturer", ""),
                                                                getattr(port, "product", "")))
            likely = bool(_ESP32_HINTS.search(described)) or getattr(port, "vid", None) in _ESP32_VIDS
            result.append({"port": port.device, "description": " ".join(described.split()),
                           "likely_csi_device": likely})
        return sorted(result, key=lambda item: (not item["likely_csi_device"], item["port"]))

    def scan(self) -> dict[str, Any]:
        """What could give CSI here: USB serial devices, the Wi-Fi adapter, and open connections."""
        ports = self.serial_ports()
        try:
            self._serial_api()
            pyserial = True
        except CsiUnavailable:
            pyserial = False
        adapters = _read_wlan(self.run)
        wifi = [{**adapter, "csi_capable": False,
                 "why": "Windows Wi-Fi drivers do not give programs per-subcarrier CSI; HAYAT can read this "
                        "adapter's signal strength instead (kind wifi_link)."} for adapter in adapters]
        candidates = [item["port"] for item in ports if item["likely_csi_device"]]
        if candidates:
            advice = f"{candidates[0]} looks like an ESP32 bridge. Say: connect CSI on {candidates[0]}."
        elif ports:
            advice = "Serial ports are present but none looks like an ESP32 bridge; name one to try it."
        else:
            advice = HOW_TO_GET_CSI
        return {"observed_at": _iso(self.clock()), "serial_ports": ports, "pyserial_installed": pyserial,
                "wifi_adapters": wifi, "real_csi_source_found": bool(candidates),
                "connections": [self._status(item) for item in self._snapshot()],
                "advice": advice, "scan_view": True}

    # ----------------------------------------------------------- connecting
    def connect(self, kind: str = "auto", port: str = "", baud: int = DEFAULT_BAUD, udp_port: int = DEFAULT_UDP_PORT,
                host: str = "0.0.0.0", allow_from: str = "", path: str = "", name: str = "",
                record: bool = False, wait_seconds: float = 3.0) -> dict[str, Any]:
        kind = str(kind or "auto").lower().strip()
        note = ""
        if kind == "auto":
            kind, port, note = self._auto_source(port)
        if kind not in SOURCE_KINDS:
            raise ValueError(f"Unknown CSI source {kind!r}; use one of {', '.join(SOURCE_KINDS)} or auto.")
        settings: dict[str, Any] = {}
        if kind == "serial":
            if not port:
                raise ValueError("Name the serial port, for example COM5 (csi_scan lists them).")
            self._serial_api()
            settings = {"port": port.upper() if os.name == "nt" else port, "baud": int(baud or DEFAULT_BAUD)}
        elif kind == "udp":
            if not 0 <= int(udp_port) <= 65535:
                raise ValueError("The UDP port must be between 0 and 65535.")
            settings = {"udp_port": int(udp_port), "host": host or "0.0.0.0", "allow_from": allow_from}
        elif kind == "file":
            source = Path(path)
            if not source.is_absolute() and not source.exists():
                source = self.root / "recordings" / path
            if not source.is_file():
                raise ValueError(f"No CSI file at {path!r}.")
            settings = {"path": str(source)}
        else:
            adapters = [item for item in _read_wlan(self.run) if item.get("state", "").lower() == "connected"]
            if not adapters:
                raise CsiUnavailable("The built-in Wi-Fi adapter is not connected to a network, so it has no "
                                     "signal to report. " + HOW_TO_GET_CSI)
            settings = {"adapter": adapters[0]["description"] or adapters[0]["name"]}
        label = _slug(name) or self._default_name(kind, settings)
        with self._lock:
            existing = self._connections.get(label)
            if existing and existing.state not in {"closed", "finished"}:
                return {**self._status(existing), "note": "Already connected; this is its current state.",
                        "connect_view": True}
            live = [item for item in self._connections.values() if item.state not in {"closed", "finished"}]
            if len(live) >= MAX_CONNECTIONS:
                raise ValueError(f"{MAX_CONNECTIONS} CSI connections are open; disconnect one first.")
            connection = Connection(label, kind, settings, self.clock())
            if record:
                self._start_recording(connection)
            self._connections[label] = connection
        connection.thread = threading.Thread(target=self._pump, args=(connection,), name=f"csi-{label}", daemon=True)
        connection.thread.start()
        self._wait_for_first_frame(connection, wait_seconds)
        status = self._status(connection)
        status["connect_view"] = True
        if note:
            status["note"] = note
        return status

    def _auto_source(self, port: str) -> tuple[str, str, str]:
        if port:
            return "serial", port, ""
        candidates = [item for item in self.serial_ports() if item["likely_csi_device"]]
        if candidates:
            return "serial", candidates[0]["port"], f"Picked {candidates[0]['port']} ({candidates[0]['description']})."
        return "wifi_link", "", ("No CSI device is plugged in, so this reads the built-in adapter's signal strength "
                                 "(link quality, not CSI). " + HOW_TO_GET_CSI)

    def _default_name(self, kind: str, settings: dict[str, Any]) -> str:
        if kind == "serial":
            return _slug(settings["port"])
        if kind == "udp":
            return f"udp-{settings['udp_port']}"
        if kind == "file":
            return _slug(Path(settings["path"]).stem)[:40] or "file"
        return "wifi-link"

    def _wait_for_first_frame(self, connection: Connection, wait_seconds: float) -> None:
        # Always wait for the open itself, so the reply says whether it opened (and which UDP port it bound).
        opened = time.monotonic() + 5.0
        while connection.state == "connecting" and time.monotonic() < opened:
            time.sleep(0.01)
        deadline = time.monotonic() + max(0.0, min(float(wait_seconds or 0), 15.0))
        while time.monotonic() < deadline:
            # A replay is quick: wait for it to finish, so the answer says how many frames it held.
            if connection.state in {"error", "finished", "closed"} or (connection.total and connection.kind != "file"):
                return
            time.sleep(0.05)

    # ------------------------------------------------------------- the pump
    def _lines(self, connection: Connection) -> Iterator[str]:
        if connection.kind == "serial":
            return _lines_from_serial(connection.settings, connection.stop_event, self._serial_api())
        if connection.kind == "udp":
            return _lines_from_udp(connection.settings, connection.stop_event)
        if connection.kind == "file":
            return _lines_from_file(connection.settings, connection.stop_event)
        return self._link_lines(connection)

    def _link_lines(self, connection: Connection) -> Iterator[str]:
        while not connection.stop_event.is_set():
            started = time.monotonic()
            adapters = [item for item in _read_wlan(self.run) if item.get("state", "").lower() == "connected"]
            if adapters and adapters[0].get("rssi") is not None:
                item = adapters[0]
                connection.error = ""
                yield json.dumps({"kind": "link", "rssi": item["rssi"], "mac": item.get("bssid", ""),
                                  "channel": item.get("channel")})
            else:
                connection.error = "The Wi-Fi adapter is not connected to a network."
            connection.stop_event.wait(max(0.05, self.link_interval - (time.monotonic() - started)))

    def _pump(self, connection: Connection) -> None:
        while not connection.stop_event.is_set():
            try:
                lines = self._lines(connection)
                connection.state = "open"
                connection.error = ""
                for line in lines:
                    if connection.stop_event.is_set():
                        break
                    if line:
                        self._accept(connection, line)
                if connection.kind == "file":
                    connection.state = "finished"
                    return
            except Exception as exc:
                connection.state = "reconnecting"
                connection.error = f"{type(exc).__name__}: {exc}"[:240]
                _log.info("CSI source %s failed: %s", connection.name, connection.error)
                if connection.kind == "file":
                    connection.state = "error"
                    return
            connection.stop_event.wait(RETRY_SECONDS)
        connection.state = "closed"

    def _accept(self, connection: Connection, line: str) -> None:
        frame = parse_line(line, source=connection.name, now=self.clock())
        if frame is None:
            connection.unparsed += 1
            return
        if connection.kind == "file":
            frame.source = connection.name
        else:
            frame.received_at = self.clock()
        with connection.lock:
            connection.frames.append(frame)
            connection.total += 1
            connection.last_frame_at = frame.received_at
            if frame.mac:
                connection.transmitters[frame.mac] += 1
        if connection.recording is not None:
            self._write_record(connection, frame)
        for subscriber in list(self._subscribers):
            try:
                subscriber(connection.name, frame)
            except Exception:
                log_swallowed(_log, "a CSI subscriber")

    # ------------------------------------------------------------ recording
    def _start_recording(self, connection: Connection) -> None:
        folder = self.root / "recordings"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(self.clock()).strftime("%Y%m%d-%H%M%S")
        path = folder / f"{connection.name}-{stamp}.jsonl"
        header = {"hayat_csi_recording": 1, "source": connection.name, "kind": connection.kind,
                  "target": connection.target(), "started": _iso(self.clock())}
        path.write_text(json.dumps(header) + "\n", encoding="utf-8")
        connection.recording = path

    def _write_record(self, connection: Connection, frame: CsiFrame) -> None:
        line = json.dumps(frame.to_record(), separators=(",", ":")) + "\n"
        if connection.recording_bytes + len(line) > MAX_RECORDING_BYTES:
            connection.recording_note = f"Recording stopped at the {MAX_RECORDING_BYTES // 2**20} MB limit."
            connection.recording = None
            return
        try:
            with open(connection.recording, "a", encoding="utf-8") as handle:  # type: ignore[arg-type]
                handle.write(line)
            connection.recording_bytes += len(line)
        except OSError as exc:
            connection.recording_note = f"Recording stopped: {exc}"[:200]
            connection.recording = None

    def recordings(self) -> dict[str, Any]:
        folder = self.root / "recordings"
        items = []
        for path in sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []:
            with open(path, encoding="utf-8", errors="replace") as handle:
                header_line = handle.readline()
                frames = sum(1 for _ in handle)
            try:
                header = json.loads(header_line)
            except ValueError:
                header = {}
            items.append({"file": path.name, "path": str(path), "frames": frames,
                          "size_mb": round(path.stat().st_size / 2**20, 2), "source": header.get("source", ""),
                          "kind": header.get("kind", ""), "started": header.get("started", "")})
        return {"folder": str(folder), "recordings": items, "count": len(items), "recordings_view": True,
                "note": "Replay one with: connect CSI from file <name>." if items else
                        "No recordings yet. Connect with record on to keep every frame for later."}

    # --------------------------------------------------------------- status
    def _snapshot(self) -> list[Connection]:
        with self._lock:
            return list(self._connections.values())

    def _find(self, name: str = "") -> Connection:
        connections = self._snapshot()
        if name:
            wanted = _slug(name)
            for connection in connections:
                if connection.name == wanted:
                    return connection
            raise ValueError(f"No CSI connection named {name!r}.")
        live = [item for item in connections if item.state not in {"closed"}]
        if not live:
            raise ValueError("No CSI source is connected. Say 'connect CSI' first (csi_scan shows what is available).")
        return max(live, key=lambda item: item.opened_at)

    def _status(self, connection: Connection) -> dict[str, Any]:
        now = self.clock()
        with connection.lock:
            frames = list(connection.frames)
            last = connection.last_frame_at
            transmitters = [mac for mac, _count in connection.transmitters.most_common(5)]
        # A replayed file keeps the times it was recorded at, so its "age" would be meaningless.
        age = None if last is None or connection.kind == "file" else round(max(0.0, now - last), 2)
        stale = STALE_SECONDS.get(connection.kind, DEFAULT_STALE_SECONDS)
        recent = [frame for frame in frames if frame.received_at >= now - 5]
        rate = round(len(recent) / 5, 1) if connection.kind != "file" else None
        receiving = connection.kind != "file" and age is not None and age <= stale
        csi_frames = [frame for frame in frames if frame.kind == "csi" and frame.amplitudes]
        latest = frames[-1] if frames else None
        status = {
            "name": connection.name, "kind": connection.kind, "target": connection.target(),
            "state": connection.state,
            "connected": connection.state == "open",
            "receiving": receiving,
            "is_csi": connection.kind != "wifi_link" and bool(csi_frames),
            "frames_total": connection.total,
            "frames_per_second": rate,
            "last_frame_age_s": age,
            "subcarriers": Counter(frame.subcarriers for frame in csi_frames).most_common(1)[0][0] if csi_frames else 0,
            "rssi": latest.rssi if latest else None,
            "channel": latest.channel if latest else None,
            "transmitters": transmitters,
            "unparsed_lines": connection.unparsed,
            "connected_for_s": round(now - connection.opened_at, 1),
        }
        if connection.error:
            status["error"] = connection.error
        if connection.settings.get("dropped"):
            status["dropped_outside_packets"] = connection.settings["dropped"]
        if connection.recording is not None:
            status["recording"] = str(connection.recording)
        if connection.recording_note:
            status["recording_note"] = connection.recording_note
        status["summary"] = _status_sentence(status)
        return status

    def status(self, name: str = "") -> dict[str, Any]:
        """Is anything connected, and is it receiving right now?"""
        connections = self._snapshot()
        if name:
            connections = [self._find(name)]
        items = [self._status(item) for item in connections if item.state != "closed"]
        receiving = [item for item in items if item["receiving"]]
        if not items:
            summary = "No CSI source is connected. " + HOW_TO_GET_CSI
        elif receiving:
            summary = "; ".join(item["summary"] for item in items)
        else:
            summary = "Connected but nothing is arriving: " + "; ".join(item["summary"] for item in items)
        return {"observed_at": _iso(self.clock()), "connected": any(item["connected"] for item in items),
                "receiving": bool(receiving), "real_csi": any(item["is_csi"] for item in receiving),
                "connections": items, "summary": summary, "status_view": True}

    def disconnect(self, name: str = "") -> dict[str, Any]:
        targets = [self._find(name)] if name else [item for item in self._snapshot() if item.state != "closed"]
        closed = []
        for connection in targets:
            self._stop(connection)
            closed.append({"name": connection.name, "frames_total": connection.total,
                           "recording": str(connection.recording) if connection.recording else ""})
        return {"disconnected": closed, "count": len(closed), "disconnect_view": True,
                "summary": f"Disconnected {len(closed)} CSI source(s)." if closed else "Nothing was connected."}

    def _stop(self, connection: Connection, timeout: float = 3.0) -> None:
        connection.stop_event.set()
        if connection.thread is not None and connection.thread is not threading.current_thread():
            connection.thread.join(timeout)
        connection.state = "closed"

    def close_all(self) -> bool:
        for connection in self._snapshot():
            self._stop(connection, timeout=2.0)
        return True

    # ----------------------------------------------------- for later features
    def subscribe(self, callback: Callable[[str, CsiFrame], None]) -> Callable[[], None]:
        """Call ``callback(connection_name, frame)`` for every frame; returns the unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        return unsubscribe

    def frames(self, name: str = "", seconds: float = 10.0) -> list[CsiFrame]:
        """The frames a connection received in the last ``seconds`` (all of a replayed file)."""
        connection = self._find(name)
        with connection.lock:
            frames = list(connection.frames)
        if connection.kind == "file" or not seconds:
            return frames
        since = self.clock() - float(seconds)
        return [frame for frame in frames if frame.received_at >= since]

    # ------------------------------------------------------------- sensing
    def _baselines_path(self) -> Path:
        return self.root / "baselines.json"

    def _baselines(self) -> dict[str, Any]:
        try:
            data = json.loads(self._baselines_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _collect(self, connection: Connection, seconds: float, fresh: bool = False) -> list[CsiFrame]:
        """Frames covering the last ``seconds``: waits only for the part not yet buffered (all of it when fresh)."""
        if connection.kind == "file":
            return self.frames(connection.name, 0)
        seconds = max(2.0, min(float(seconds or 10), 60.0))
        now = self.clock()
        missing = seconds if fresh else max(0.0, connection.opened_at + seconds - now)
        deadline = time.monotonic() + missing
        while time.monotonic() < deadline and connection.state not in {"closed", "error"}:
            time.sleep(0.1)
        frames = self.frames(connection.name, seconds)
        return [frame for frame in frames if frame.received_at >= now] if fresh else frames

    def motion(self, name: str = "", seconds: float = 10.0) -> dict[str, Any]:
        """Is something moving near the link? Judged against the still-room baseline when there is one."""
        connection = self._find(name)
        frames = self._collect(connection, seconds)
        measured = movement_score(frames)
        baseline = self._baselines().get(connection.name)
        result = {"name": connection.name, "kind": connection.kind, "motion_view": True, **measured}
        result.update(judge_motion(measured, baseline))
        return result

    def calibrate(self, name: str = "", seconds: float = 10.0) -> dict[str, Any]:
        """Record how much the signal wobbles in a still room, so movement can be told apart from noise."""
        connection = self._find(name)
        frames = self._collect(connection, seconds, fresh=True)
        measured = movement_score(frames)
        if measured.get("score") is None:
            raise ValueError(measured.get("note") or "Not enough frames to calibrate.")
        baselines = self._baselines()
        baselines[connection.name] = {"score": measured["score"], "basis": measured["basis"],
                                      "frames": measured["frames"], "target": connection.target(),
                                      "recorded": _iso(self.clock())}
        self.root.mkdir(parents=True, exist_ok=True)
        self._baselines_path().write_text(json.dumps(baselines, indent=2), encoding="utf-8")
        return {"name": connection.name, "calibrate_view": True, "baseline": baselines[connection.name],
                "summary": f"Saved a still-room baseline for {connection.name} from {measured['frames']} frames "
                           f"({measured['basis']}). Movement is now judged against it."}


# ------------------------------------------------------------------ sensing
def movement_score(frames: list[CsiFrame]) -> dict[str, Any]:
    """How much the channel changed over the frames, and whether the change looks like motion.

    CSI: each frame's amplitudes are divided by that frame's mean (the radio's
    automatic gain moves every subcarrier together; people do not), and one-off
    spikes -- the occasional corrupted packet an ESP32 prints -- are replaced by
    the subcarrier's median (a Hampel filter, 3 scaled MADs). Two numbers come out:

    * ``score``: the median over subcarriers of how much each varies in time.
      Comparable against the same link's still-room baseline.
    * ``autocorrelation``: the median lag-1 autocorrelation of each subcarrier.
      A still room leaves white noise (near 0); a body moving through the paths
      changes amplitudes smoothly from packet to packet (near 1). It needs no
      calibration, so it is what an uncalibrated answer is judged on.

    Link only: the standard deviation of RSSI in dB.
    """
    csi = [frame for frame in frames if frame.kind == "csi" and frame.amplitudes]
    if csi:
        transmitter = Counter(frame.mac for frame in csi).most_common(1)[0][0]
        same = [frame for frame in csi if frame.mac == transmitter]
        width = Counter(frame.subcarriers for frame in same).most_common(1)[0][0]
        rows = [frame.amplitudes for frame in same if frame.subcarriers == width]
        if len(rows) < 10:
            return {"score": None, "frames": len(rows), "basis": "csi_amplitude",
                    "note": f"Only {len(rows)} CSI frames in the window; at least 10 are needed."}
        import numpy as np

        matrix = np.asarray(rows, dtype=float)
        matrix = matrix[:, matrix.mean(axis=0) > 1e-6]  # guard and null subcarriers carry nothing
        if matrix.shape[1] == 0:
            return {"score": None, "frames": len(rows), "basis": "csi_amplitude",
                    "note": "Every subcarrier was empty."}
        scale = matrix.mean(axis=1, keepdims=True)
        scale[scale == 0] = 1.0
        normalised = matrix / scale
        median = np.median(normalised, axis=0)
        spread = 1.4826 * np.median(np.abs(normalised - median), axis=0)
        spikes = np.abs(normalised - median) > 3 * np.maximum(spread, 1e-9)
        # A spike is a lone packet; a run of them is the room changing, so only isolated ones are replaced.
        lone = spikes & ~np.roll(spikes, 1, axis=0) & ~np.roll(spikes, -1, axis=0)
        cleaned = np.where(lone, median, normalised)
        score = float(np.median(cleaned.std(axis=0)))
        centred = cleaned - cleaned.mean(axis=0)
        power = (centred ** 2).sum(axis=0)
        lagged = (centred[1:] * centred[:-1]).sum(axis=0)
        usable = power > 1e-12
        autocorrelation = float(np.median(lagged[usable] / power[usable])) if usable.any() else 0.0
        span = same[-1].received_at - same[0].received_at
        rate = (len(rows) - 1) / span if span > 0 else None
        return {"score": round(score, 5), "autocorrelation": round(autocorrelation, 3), "frames": len(rows),
                "rate_hz": round(rate, 1) if rate else None,
                "basis": "csi_amplitude", "subcarriers_used": int(matrix.shape[1]), "transmitter": transmitter,
                "spikes_removed": int(lone.sum()), "span_s": round(span, 1)}
    link = [frame.rssi for frame in frames if frame.rssi is not None]
    if len(link) < 5:
        return {"score": None, "frames": len(link), "basis": "rssi",
                "note": f"Only {len(link)} signal readings in the window; at least 5 are needed."}
    mean = sum(link) / len(link)
    score = math.sqrt(sum((value - mean) ** 2 for value in link) / len(link))
    return {"score": round(score, 3), "frames": len(link), "basis": "rssi", "mean_rssi": round(mean, 1),
            "span_s": round(frames[-1].received_at - frames[0].received_at, 1)}


#: Without a baseline. CSI is judged on lag-1 autocorrelation (calibration-free);
#: RSSI on rough dB thresholds, said to be rough.
_UNCALIBRATED = {"csi_amplitude": (0.35, 0.65), "rssi": (1.0, 2.5)}
#: Below this the variation is noise however large it is (interference, a busy channel), not a body moving --
#: provided packets arrive fast enough (SMOOTH_RATE_HZ) for a moving body to look smooth between them.
NOISE_AUTOCORRELATION = 0.2
SMOOTH_RATE_HZ = 8.0
#: Variation this small is the radio's own floor, whatever its autocorrelation.
QUIET_SCORE = 0.004
LEVELS = ("still", "some movement", "strong movement")


def judge_motion(measured: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    score = measured.get("score")
    basis = measured.get("basis", "")
    caveat = ("Movement means the radio paths changed: a person, a pet, a fan or a door all count. "
              if basis == "csi_amplitude" else
              "This is signal strength from the built-in adapter, not CSI: it catches someone walking between "
              "this computer and the router, not breathing or small gestures. ")
    if score is None:
        return {"level": "unknown", "moving": None, "calibrated": False,
                "summary": measured.get("note") or "Not enough data."}
    correlation = measured.get("autocorrelation")
    if baseline and baseline.get("basis") == basis and float(baseline.get("score") or 0) > 0:
        ratio = score / float(baseline["score"])
        level = "still" if ratio < 1.6 else "some movement" if ratio < 3.0 else "strong movement"
        note = ""
        rate = measured.get("rate_hz")
        if (level != "still" and correlation is not None and correlation < NOISE_AUTOCORRELATION
                and rate is not None and rate >= SMOOTH_RATE_HZ):
            level, note = "still", ("The extra variation is packet-to-packet noise (interference or a busy channel), "
                                    "not the smooth change a moving body makes. ")
        return {"level": level, "moving": level != "still", "calibrated": True, "ratio_to_still": round(ratio, 2),
                "baseline_recorded": baseline.get("recorded", ""),
                "summary": f"{level.capitalize()}: the signal varies {ratio:.1f}x as much as in the still room "
                           f"({measured['frames']} frames). " + note + caveat}
    low, high = _UNCALIBRATED.get(basis, (0.35, 0.65))
    value = correlation if basis == "csi_amplitude" else score
    level = "still" if value < low else "some movement" if value < high else "strong movement"
    if basis == "csi_amplitude" and score < QUIET_SCORE:
        level = "still"
    how = ("judged on how smoothly the subcarriers change, which needs no calibration"
           if basis == "csi_amplitude" else "uncalibrated guess")
    return {"level": level, "moving": level != "still", "calibrated": False,
            "summary": f"{level.capitalize()} ({how}; {measured['frames']} frames). " + caveat
                       + "For the most dependable answer, keep the room still and say: calibrate CSI."}


# ------------------------------------------------------------------ helpers
def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment).astimezone().isoformat(timespec="seconds")


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(value or "").lower()).strip("-")[:48]


def _status_sentence(status: dict[str, Any]) -> str:
    name, target = status["name"], status["target"]
    if status["state"] == "finished":
        return f"{name}: replayed {status['frames_total']} frames from {target}"
    if status["state"] in {"reconnecting", "error"}:
        return f"{name} ({target}) is not connected: {status.get('error') or 'the source failed'}; retrying"
    if status["kind"] == "file":
        # A replay has no "age" (its frames keep the times they were recorded at), so it is described by progress.
        return f"{name}: replaying {target}, {status['frames_total']} frames so far"
    if not status["receiving"]:
        waited = status["connected_for_s"]
        return (f"{name} ({target}) is open but no frames have arrived in {waited:.0f} s"
                if not status["frames_total"] else
                f"{name} ({target}) is open but silent for {status['last_frame_age_s']:.0f} s")
    what = (f"real CSI, {status['subcarriers']} subcarriers" if status["is_csi"] else
            "signal strength only (not CSI)")
    rssi = f", RSSI {status['rssi']:.0f} dBm" if status.get("rssi") is not None else ""
    return f"{name} ({target}) is receiving {status['frames_per_second']} frames/s: {what}{rssi}"


# ------------------------------------------------------------------- chat
def report(history: list[dict[str, Any]]) -> str | None:
    """Plain text for verified CSI results, written from the data (no model)."""
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith(("csi_", "sensing_")):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if not isinstance(value, dict):
            continue
        if value.get("scan_view"):
            lines = ["CSI sources on this computer:"]
            for port in value["serial_ports"]:
                lines.append(f"  {port['port']}: {port['description']}"
                             + (" (looks like an ESP32)" if port["likely_csi_device"] else ""))
            if not value["serial_ports"]:
                lines.append("  No USB serial devices are plugged in.")
            for adapter in value["wifi_adapters"]:
                lines.append(f"  Wi-Fi: {adapter['description'] or adapter['name']} ({adapter['state']}"
                             + (f", RSSI {adapter['rssi']:.0f} dBm" if adapter.get("rssi") is not None else "")
                             + "): signal strength only; Windows gives no CSI from it.")
            for item in value["connections"]:
                lines.append("  Connected: " + item["summary"])
            lines.append(value["advice"])
        elif value.get("connect_view"):
            lines = [value["summary"] + "."]
            if value.get("note"):
                lines.append(value["note"])
            if value.get("recording"):
                lines.append(f"Recording every frame to {value['recording']}.")
        elif value.get("status_view"):
            lines = [value["summary"] + ("." if not value["summary"].endswith(".") else "")]
        elif value.get("recordings_view"):
            lines = [f"{value['count']} CSI recording(s) in {value['folder']}:"]
            lines += [f"  {item['file']}: {item['frames']:,} frames, {item['size_mb']} MB, {item['kind']}"
                      for item in value["recordings"][:10]]
            lines.append(value["note"])
        elif value.get("motion_view") or value.get("calibrate_view") or value.get("disconnect_view"):
            lines = [value["summary"]]
        elif value.get("fused_view"):
            lines = [value["text"]]
        elif value.get("sensing_status_view"):
            lines = [value["summary"]]
        else:
            continue
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else None


_CSI_WORDS = r"(?:csi|channel\s+state(?:\s+information)?|wi-?fi\s+sens(?:ing|or)|wifi\s+csi|esp32(?:\s+csi)?)"
_PORT = re.compile(r"(?i)\b(com\d{1,3})\b")
_UDP = re.compile(r"(?i)\budp(?:\s+port)?\s+(\d{2,5})\b")
_FILE = re.compile(r"(?i)\bfrom\s+(?:the\s+)?(?:file|recording)\s+(\S+)")


def csi_request(text: str) -> dict[str, Any] | None:
    """The CSI tool a sentence asks for, as {"tool", "arguments"}, or None."""
    value = " ".join(str(text or "").split()).lower().rstrip("?.! ")
    value = re.sub(r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?", "", value)
    if not re.search(rf"\b{_CSI_WORDS}\b", value) or re.search(r"\bcsi\s+(?:miami|ny|vegas|cyber|tv|show|episode|season)", value):
        return None
    if re.fullmatch(_CSI_WORDS, value):
        return {"tool": "csi_status", "arguments": {}}
    # "What is CSI?" is a question for the conversation, not a hardware check.
    if re.match(r"(?:what(?:'s|\s+is|\s+are)|explain|define|describe|tell\s+me\s+about|how\s+does)\b", value) \
            and not re.search(r"\b(?:my|connected|available|plugged|here|this\s+(?:computer|pc)|status|sources?|devices?)\b", value):
        return None
    starts_connect = re.match(r"(?:connect|start|open|attach|listen|record|use)\b", value)
    if re.search(r"\b(?:disconnect|stop|close|turn\s+off|unplug)\b", value):
        return {"tool": "csi_disconnect", "arguments": {}}
    if re.search(r"\b(?:calibrate|calibration|baseline)\b", value):
        return {"tool": "csi_calibrate", "arguments": {}}
    if re.search(r"\b(?:recordings|recorded|saved\s+data)\b", value) and not starts_connect:
        return {"tool": "csi_recordings", "arguments": {}}
    if re.search(r"\b(?:motion|movement|moving|anyone|someone|somebody|presence|present|occupied|sense|detect)\b", value) \
            and not starts_connect:
        if re.search(r"\b(?:camera|webcam|cam)\b", value):
            return {"tool": "sensing_check", "arguments": {"with_camera": True}}
        return {"tool": "csi_motion", "arguments": {}}
    if re.search(r"\bwhat\s+(?:does|do|is|can)\b.*\b(?:see|sense|sensing|feel|showing)\b", value) \
            or re.search(r"\bsensing\s+(?:status|now|timeline|report)\b", value):
        return {"tool": "sensing_status", "arguments": {}}
    if not starts_connect and (re.match(r"(?:check|verify|test)\b", value) or re.search(
            r"\b(?:connected|connection|status|working|receiving|live|alive|online)\b", value)):
        return {"tool": "csi_status", "arguments": {}}
    if re.search(r"\b(?:connect|start|open|attach|listen|record|use)\b", value):
        arguments: dict[str, Any] = {}
        port, udp, source = _PORT.search(value), _UDP.search(value), _FILE.search(text or "")
        if port:
            arguments.update({"kind": "serial", "port": port.group(1).upper()})
        elif udp:
            arguments.update({"kind": "udp", "udp_port": int(udp.group(1))})
        elif source:
            arguments.update({"kind": "file", "path": source.group(1).strip("'\"")})
        elif re.search(r"\b(?:built-?in|adapter|rssi|signal\s+strength|wifi\s+link)\b", value):
            arguments["kind"] = "wifi_link"
        if re.search(r"\brecord(?:ing)?\b|\bsave\b", value):
            arguments["record"] = True
        return {"tool": "csi_connect", "arguments": arguments}
    if re.search(r"\b(?:scan|find|list|available|which|what|show|sources?|devices?|support|supports)\b", value):
        return {"tool": "csi_scan", "arguments": {}}
    return None


_DESCRIPTIONS = {
    "csi_scan": "Look for CSI sources: USB serial devices (ESP32) and the Wi-Fi adapter.",
    "csi_connect": "Connect a CSI source and wait for its first frames.",
    "csi_status": "Check whether the CSI connection is open and frames are arriving.",
    "csi_disconnect": "Disconnect the CSI source.",
    "csi_motion": "Measure how much the Wi-Fi channel is changing and say whether something is moving.",
    "csi_calibrate": "Record the still-room baseline for the CSI source.",
    "csi_recordings": "List saved CSI recordings.",
    "sensing_check": "Check for movement with Wi-Fi sensing and the camera together, and say which saw what.",
    "sensing_status": "Report what Wi-Fi sensing sees right now and over the last minute.",
}


def direct_csi_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = csi_request(text)
    if not request or request["tool"] not in set(names):
        return None
    return [{"description": _DESCRIPTIONS[request["tool"]], "tool": request["tool"],
             "arguments": request["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}]


# ------------------------------------------------------------------- tools
def register_csi_tools(registry: Any, hub: CsiHub | None = None) -> CsiHub:
    hub = hub or CsiHub()
    registry.csi = hub
    registry.register(
        "csi_scan",
        "Find Wi-Fi channel state information (CSI) sources: USB serial devices that look like an ESP32 CSI "
        "board, the built-in Wi-Fi adapter (signal strength only; Windows gives no CSI), and open connections.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", lambda: hub.scan())
    registry.register(
        "csi_connect",
        "Connect a Wi-Fi CSI source and report whether frames arrive: kind serial (ESP32-CSI-Tool / esp-csi on a "
        "COM port), udp (CSI_DATA or JSON lines from a device on the local network), file (replay a recording), "
        "wifi_link (built-in adapter RSSI, not CSI) or auto. record=true saves every frame for later use.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["auto", *SOURCE_KINDS]},
            "port": {"type": "string", "description": "Serial port, e.g. COM5."},
            "baud": {"type": "integer", "minimum": 9600, "maximum": 4000000},
            "udp_port": {"type": "integer", "minimum": 0, "maximum": 65535},
            "allow_from": {"type": "string", "description": "Only accept UDP packets from this IP address."},
            "path": {"type": "string", "description": "Recording or log file to replay."},
            "name": {"type": "string"},
            "record": {"type": "boolean"},
            "wait_seconds": {"type": "number", "minimum": 0, "maximum": 15}},
         "additionalProperties": False},
        "low", "computer_observe", hub.connect)
    registry.register(
        "csi_status",
        "Check the CSI connection: whether it is connected and receiving, frames per second, subcarriers, RSSI, "
        "transmitters seen, the last frame's age and any error.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": False},
        "low", "system_read", hub.status)
    registry.register(
        "csi_disconnect",
        "Disconnect one CSI source by name, or all of them.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": False},
        "low", "computer_observe", hub.disconnect)
    registry.register(
        "csi_motion",
        "Wi-Fi sensing: measure how much the CSI (or signal strength) changed over the last seconds and say "
        "whether something is moving, against the still-room baseline when one is saved.",
        {"type": "object", "properties": {"name": {"type": "string"},
                                          "seconds": {"type": "number", "minimum": 2, "maximum": 60}},
         "additionalProperties": False},
        "low", "computer_observe", hub.motion)
    registry.register(
        "csi_calibrate",
        "Save a still-room baseline for a CSI source (keep the room empty and still while it records).",
        {"type": "object", "properties": {"name": {"type": "string"},
                                          "seconds": {"type": "number", "minimum": 2, "maximum": 60}},
         "additionalProperties": False},
        "low", "computer_observe", hub.calibrate)
    registry.register(
        "csi_recordings",
        "List saved CSI recordings (JSON lines, replayable with csi_connect kind=file).",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", lambda: hub.recordings())
    return hub
