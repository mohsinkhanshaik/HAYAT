"""The Wi-Fi router this computer is connected to, and the home network around it. Read-only.

What HAYAT reads, and from where:

* **Connection**: SSID, access point, band, channel, signal and link rates from
  ``netsh wlan show interfaces``; IP address, gateway and DNS from Windows'
  own IP configuration.
* **The router itself**: its address is the default gateway Windows reports,
  and nothing else on the network is ever contacted. HAYAT reads its public
  front page (title only) and, for gateways that publish a status page without
  a login, that page -- the T-Mobile 5G Gateway's ``/TMI/v1/gateway`` gives
  model, firmware, uptime and the 5G/LTE signal (RSRP, RSRQ, SINR, band, cell).
  No password is ever sent or stored, and no setting is changed.
* **Air around it**: nearby Wi-Fi networks from Windows' last scan, with how
  busy each channel is (stations and channel utilisation where the access point
  advertises them), and which channel is least crowded.
* **Devices**: the IPv4 neighbours Windows already knows on this network (its
  ARP cache) -- devices this computer has exchanged traffic with recently. No
  scan is sent. Phones usually show a private (randomised) MAC address, which
  is reported as such rather than guessed. Manufacturer names come from the
  IEEE registry, downloaded once on request into data/network.
* **Latency**: time to open a TCP connection to the router and to a public
  resolver; no data is exchanged.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.csi import parse_wlan_interfaces
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

NETWORK_ROOT = PROJECT_ROOT / "data" / "network"
NETWORK_TOOL_NAMES = ("network_router", "network_nearby_wifi", "network_devices", "network_vendor_update", "network_guard",
                      "network_name_device")
OUI_SOURCE = "https://standards-oui.ieee.org/oui/oui.csv"
INTERNET_PROBE = ("1.1.1.1", 443)
HISTORY = 240

_IP_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue'; "
    "$c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | Select-Object -First 1; "
    "if (-not $c) { '{}' ; exit } ; "
    "$n = @(Get-NetNeighbor -AddressFamily IPv4 -InterfaceIndex $c.InterfaceIndex | "
    "Where-Object { $_.State -in 'Reachable','Stale','Delay','Probe' } | "
    "Select-Object IPAddress,LinkLayerAddress,@{n='State';e={$_.State.ToString()}}); "
    "@{interface=$c.InterfaceAlias; description=$c.InterfaceDescription; ipv4=$c.IPv4Address.IPAddress; "
    "prefix=$c.IPv4Address.PrefixLength; gateway=$c.IPv4DefaultGateway.NextHop; dns=@($c.DNSServer.ServerAddresses); "
    "neighbours=$n} | ConvertTo-Json -Depth 4 -Compress"
)


def _powershell(script: str, timeout: float) -> str:
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return completed.stdout.strip()


def _netsh(arguments: str, timeout: float = 10) -> str:
    completed = subprocess.run(["netsh", *arguments.split()], capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace")
    return completed.stdout


def _get_local_json(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - the router's own address only
        return json.loads(response.read(512 * 1024).decode("utf-8", "replace"))


def _get_local_text(url: str, timeout: float = 5.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - the router's own address only
        return response.read(256 * 1024).decode("utf-8", "replace")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normal_mac(value: str) -> str:
    return ":".join(re.findall(r"[0-9a-f]{2}", str(value or "").lower()))[:17]


def randomized(mac: str) -> bool:
    """A locally administered (private, randomised) address: the second bit of the first byte is set."""
    try:
        return bool(int(normal_mac(mac)[:2], 16) & 0b10)
    except ValueError:
        return False


# ------------------------------------------------------------------ ratings
def _rate(value: float | None, bands: tuple[tuple[float, str], ...]) -> str | None:
    if value is None:
        return None
    for floor, word in bands:
        if value >= floor:
            return word
    return bands[-1][1]


#: Usual 4G/5G thresholds, strongest first.
RSRP_BANDS = ((-80, "excellent"), (-90, "good"), (-100, "fair"), (-1e9, "poor"))
RSRQ_BANDS = ((-10, "excellent"), (-15, "good"), (-20, "fair"), (-1e9, "poor"))
SINR_BANDS = ((20, "excellent"), (13, "good"), (0, "fair"), (-1e9, "poor"))
WIFI_RSSI_BANDS = ((-55, "excellent"), (-67, "good"), (-75, "fair"), (-1e9, "poor"))


def cellular_quality(radio: dict[str, Any]) -> dict[str, Any]:
    rsrp, rsrq, sinr = radio.get("rsrp"), radio.get("rsrq"), radio.get("sinr")
    ratings = {"rsrp": _rate(rsrp, RSRP_BANDS), "rsrq": _rate(rsrq, RSRQ_BANDS), "sinr": _rate(sinr, SINR_BANDS)}
    order = ["poor", "fair", "good", "excellent"]
    known = [word for word in ratings.values() if word]
    overall = min(known, key=order.index) if known else None
    return {**ratings, "overall": overall}


# ------------------------------------------------------------- gateway pages
def tmobile_status(data: dict[str, Any]) -> dict[str, Any]:
    """The T-Mobile 5G Gateway's /TMI/v1/gateway?get=all, in plain fields."""
    device, signal = data.get("device") or {}, data.get("signal") or {}
    radios = {}
    for name in ("5g", "4g"):
        radio = signal.get(name)
        if isinstance(radio, dict) and radio:
            radios[name] = {key: radio.get(key) for key in ("bands", "bars", "rsrp", "rsrq", "rssi", "sinr", "cid",
                                                             "gNBID", "eNBID", "antennaUsed")} | {
                "quality": cellular_quality(radio)}
    uptime = (data.get("time") or {}).get("upTime")
    return {"maker": device.get("manufacturer"), "model": device.get("model"), "name": device.get("friendlyName"),
            "firmware": device.get("softwareVersion"), "firmware_state": device.get("updateState"),
            "mesh_supported": device.get("isMeshSupported"), "uptime_s": uptime,
            "registration": (signal.get("generic") or {}).get("registration"),
            "roaming": (signal.get("generic") or {}).get("roaming"), "cellular": radios,
            "source": "the gateway's own status page (/TMI/v1/gateway), read without a login"}


#: (title, path, parser) for routers that publish their status without a login.
STATUS_PAGES: tuple[tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("T-Mobile 5G Gateway", "/TMI/v1/gateway?get=all", tmobile_status),
)


# ------------------------------------------------------------------ nearby
def parse_wifi_scan(text: str) -> list[dict[str, Any]]:
    """``netsh wlan show networks mode=bssid``: one row per access point (BSSID)."""
    rows: list[dict[str, Any]] = []
    ssid, security = "", ""
    current: dict[str, Any] | None = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        match = re.match(r"^SSID \d+\s*:\s*(.*)$", line)
        if match:
            ssid, security, current = match[1].strip(), "", None
            continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        lowered = key.lower()
        if lowered == "authentication":
            security = value
        elif lowered.startswith("bssid"):
            current = {"ssid": ssid or "(hidden network)", "bssid": value.lower(), "security": security}
            rows.append(current)
        elif current is not None:
            if lowered == "signal":
                current["signal_percent"] = int(re.sub(r"\D", "", value) or 0)
                current["rssi"] = current["signal_percent"] / 2 - 100
            elif lowered == "radio type":
                current["radio"] = value
            elif lowered == "band":
                current["band"] = value
            elif lowered == "channel":
                current["channel"] = int(re.sub(r"\D", "", value) or 0) or None
            elif lowered == "connected stations":
                current["stations"] = int(re.sub(r"\D", "", value) or 0)
            elif lowered == "channel utilization":
                percent = re.search(r"\((\d+)\s*%\)", value)
                current["utilization_percent"] = int(percent[1]) if percent else None
    return rows


def channel_advice(networks: list[dict[str, Any]], mine: dict[str, Any] | None) -> dict[str, Any]:
    """How crowded this router's channel is, and which channel in its band is least crowded."""
    if not mine or not mine.get("channel"):
        return {"summary": "Not connected to Wi-Fi, so there is no channel to judge."}
    band, channel = str(mine.get("band") or ""), mine["channel"]
    same_band = [row for row in networks if str(row.get("band") or "") == band and row.get("bssid") != mine.get("bssid")]
    sharing = [row for row in same_band if row.get("channel") == channel]
    counts: dict[int, int] = {}
    for row in same_band:
        if row.get("channel"):
            counts[row["channel"]] = counts.get(row["channel"], 0) + 1
    others = sorted({row["channel"] for row in networks if str(row.get("band") or "") == band and row.get("channel")} | {channel})
    quietest = min(others, key=lambda value: (counts.get(value, 0), value != channel)) if others else channel
    if not sharing:
        summary = f"Channel {channel} ({band}) is not shared with any network Windows can see."
    else:
        summary = (f"Channel {channel} ({band}) is shared with {len(sharing)} other network(s): "
                   + ", ".join(sorted({row['ssid'] for row in sharing})[:5]) + ".")
        if quietest != channel:
            summary += f" Channel {quietest} looks least crowded; the router chooses its channel itself unless set by hand."
    return {"channel": channel, "band": band, "sharing": len(sharing), "least_crowded": quietest, "summary": summary,
            "note": "From Windows' last Wi-Fi scan; networks out of range of this computer are not seen."}


# ------------------------------------------------------------------ vendors
class VendorTable:
    """IEEE MA-L assignments (first three bytes of a MAC address) -> organisation, loaded once."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "oui.csv"
        self._table: dict[str, str] | None = None
        self._lock = threading.Lock()

    def available(self) -> bool:
        return self.path.is_file()

    def lookup(self, mac: str) -> str | None:
        if randomized(mac):
            return None
        table = self._load()
        return table.get(normal_mac(mac).replace(":", "")[:6].upper()) if table else None

    def _load(self) -> dict[str, str]:
        with self._lock:
            if self._table is None:
                self._table = {}
                if self.path.is_file():
                    with open(self.path, encoding="utf-8", errors="replace", newline="") as handle:
                        for row in csv.reader(handle):
                            if len(row) >= 3 and len(row[1]) == 6:
                                self._table[row[1].upper()] = " ".join(row[2].split())
            return self._table

    def update(self, fetch: Callable[[str], bytes]) -> dict[str, Any]:
        content = fetch(OUI_SOURCE)
        rows = sum(1 for row in csv.reader(io.StringIO(content.decode("utf-8", "replace"))) if len(row) >= 3)
        if rows < 1000:
            raise ValueError("The IEEE registry download looked incomplete; the previous table is kept.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(content)
        with self._lock:
            self._table = None
        return {"organisations": rows, "path": str(self.path), "source": OUI_SOURCE,
                "summary": f"Saved {rows:,} manufacturer prefixes from the IEEE registry."}


# -------------------------------------------------------------------- guard
class DeviceBook:
    """Devices ever seen on the owner's networks, with the names he gives them. data/network/known-devices.json."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "known-devices.json"
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def observe(self, devices: list[dict[str, Any]], network: str, now: str) -> dict[str, Any]:
        """Record what is on the network now; return the devices never seen before (none on the very first look)."""
        with self._lock:
            data = self.load()
            baseline = not data
            fresh = []
            for row in devices:
                mac = row.get("mac")
                if not mac:
                    continue
                known = data.get(mac)
                if known is None:
                    data[mac] = {"first_seen": now, "last_seen": now, "ip": row.get("ip"), "vendor": row.get("vendor"),
                                 "network": network, "name": "router" if row.get("role") == "router" else "",
                                 "private_address": row.get("private_address", False)}
                    if not baseline:
                        fresh.append({**row, "first_seen": now})
                else:
                    known.update({"last_seen": now, "ip": row.get("ip"), "vendor": known.get("vendor") or row.get("vendor")})
            self.save(data)
        return {"baseline": baseline, "new": fresh, "known": len(data)}

    def name(self, device: str, name: str) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            target = normal_mac(device) if re.search(r"[0-9a-f]{2}[:-][0-9a-f]{2}", device, re.I) else ""
            if not target:
                target = next((mac for mac, entry in data.items() if entry.get("ip") == device.strip()), "")
            if target not in data:
                raise ValueError(f"No device {device!r} has been seen; check the device list first.")
            data[target]["name"] = " ".join(str(name).split())[:60]
            self.save(data)
            return {"mac": target, **data[target]}


# ------------------------------------------------------------------ latency
def connect_ms(host: str, port: int, tries: int = 3, timeout: float = 2.0) -> float | None:
    """Median time to open a TCP connection (no data sent), or None when it cannot connect."""
    times = []
    for _ in range(tries):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                times.append((time.perf_counter() - started) * 1000)
        except OSError:
            continue
    return round(sorted(times)[len(times) // 2], 1) if times else None


# --------------------------------------------------------------- inspector
class NetworkInspector:
    """Everything above, with injectable readers for tests."""

    def __init__(self, root: Path | None = None, *, powershell: Callable[[str, float], str] = _powershell,
                 netsh: Callable[[str], str] = _netsh, get_json: Callable[[str], Any] = _get_local_json,
                 get_text: Callable[[str], str] = _get_local_text, fetch: Callable[[str], bytes] | None = None,
                 latency: Callable[[str, int], float | None] = connect_ms, clock: Callable[[], float] = time.time) -> None:
        self.root = Path(root or NETWORK_ROOT)
        self.powershell, self.netsh, self.get_json, self.get_text = powershell, netsh, get_json, get_text
        self.fetch, self.latency, self.clock = fetch, latency, clock
        self.vendors = VendorTable(self.root)
        self.book = DeviceBook(self.root)
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY)

    def _ip(self) -> dict[str, Any]:
        if os.name != "nt":
            return {}
        try:
            data = json.loads(self.powershell(_IP_SCRIPT, 20) or "{}")
        except (ValueError, subprocess.SubprocessError, OSError):
            log_swallowed(_log, "reading the IP configuration")
            return {}
        return data if isinstance(data, dict) else {}

    def _wifi(self) -> dict[str, Any] | None:
        try:
            adapters = parse_wlan_interfaces(self.netsh("wlan show interfaces"))
        except (subprocess.SubprocessError, OSError):
            return None
        return next((item for item in adapters if str(item.get("state", "")).lower() == "connected"), None)

    def _gateway_pages(self, gateway: str) -> dict[str, Any]:
        found: dict[str, Any] = {"admin_page": f"http://{gateway}/"}
        try:
            page = self.get_text(f"http://{gateway}/")
            title = re.search(r"(?is)<title>(.*?)</title>", page)
            found["admin_title"] = " ".join(title[1].split())[:120] if title else ""
            found["admin_reachable"] = True
        except (OSError, ValueError):
            found["admin_reachable"] = False
        for title, path, parser in STATUS_PAGES:
            try:
                found.update(parser(self.get_json(f"http://{gateway}{path}")))
                found["status_page"] = title
                break
            except (OSError, ValueError, AttributeError, TypeError):
                continue
        return found

    def router(self, measure_internet: bool = True) -> dict[str, Any]:
        ip, wifi = self._ip(), self._wifi()
        gateway = str(ip.get("gateway") or "")
        result: dict[str, Any] = {"observed_at": datetime.fromtimestamp(self.clock()).astimezone().isoformat(timespec="seconds"),
                                  "router_view": True, "connected": bool(gateway)}
        if wifi:
            result["wifi"] = {"ssid": wifi.get("ssid"), "access_point": wifi.get("bssid"), "band": wifi.get("band"),
                              "channel": wifi.get("channel"), "radio": wifi.get("radio"),
                              "signal_percent": wifi.get("signal_percent"), "rssi": wifi.get("rssi"),
                              "rssi_quality": _rate(wifi.get("rssi"), WIFI_RSSI_BANDS),
                              "receive_mbps": wifi.get("receive_mbps"), "adapter": wifi.get("description")}
        result["ip"] = {"interface": ip.get("interface"), "address": ip.get("ipv4"), "prefix": ip.get("prefix"),
                        "gateway": gateway or None, "dns": _as_list(ip.get("dns"))}
        if not gateway:
            result["summary"] = "This computer has no default gateway: it is not connected to a router right now."
            return result
        try:
            private = ipaddress.ip_address(gateway).is_private
        except ValueError:
            private = False
        neighbour = next((row for row in _as_list(ip.get("neighbours")) if isinstance(row, dict)
                          and row.get("IPAddress") == gateway), {})
        mac = normal_mac(neighbour.get("LinkLayerAddress", ""))
        router: dict[str, Any] = {"address": gateway, "mac": mac or None, "vendor": self.vendors.lookup(mac) if mac else None}
        if private:  # only ever the gateway on this computer's own network
            router.update(self._gateway_pages(gateway))
        result["router"] = router
        result["latency_ms"] = {"router": self.latency(gateway, 80),
                                "internet": self.latency(*INTERNET_PROBE) if measure_internet else None,
                                "internet_measured": bool(measure_internet)}
        self.history.append({"t": self.clock(), "wifi_rssi": (result.get("wifi") or {}).get("rssi"),
                             "router_ms": result["latency_ms"]["router"], "internet_ms": result["latency_ms"]["internet"],
                             **{f"{name}_{key}": (router.get("cellular") or {}).get(name, {}).get(key)
                                for name in ("5g", "4g") for key in ("sinr", "rsrp")}})
        result["summary"] = router_sentence(result)
        return result

    def nearby(self) -> dict[str, Any]:
        try:
            networks = parse_wifi_scan(self.netsh("wlan show networks mode=bssid"))
        except (subprocess.SubprocessError, OSError):
            networks = []
        wifi = self._wifi()
        mine = next((row for row in networks if wifi and row.get("bssid") == str(wifi.get("bssid") or "").lower()), None)
        if mine is None and wifi:
            mine = {"bssid": str(wifi.get("bssid") or "").lower(), "band": wifi.get("band"), "channel": wifi.get("channel")}
        advice = channel_advice(networks, mine)
        networks.sort(key=lambda row: -(row.get("signal_percent") or 0))
        return {"nearby_view": True, "networks": networks, "count": len(networks), "mine": mine, "channel": advice,
                "summary": f"{len(networks)} access point(s) in Windows' last Wi-Fi scan. " + advice["summary"]}

    def devices(self) -> dict[str, Any]:
        ip = self._ip()
        gateway = ip.get("gateway")
        rows = []
        for row in _as_list(ip.get("neighbours")):
            if not isinstance(row, dict):
                continue
            address, mac = str(row.get("IPAddress") or ""), normal_mac(row.get("LinkLayerAddress", ""))
            if not mac or mac in ("ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00") or address.endswith(".255"):
                continue
            private_mac = randomized(mac)
            rows.append({"ip": address, "mac": mac, "role": "router" if address == gateway else "device",
                         "vendor": self.vendors.lookup(mac),
                         "private_address": private_mac, "state": str(row.get("State") or "").lower()})
        rows.sort(key=lambda item: tuple(int(part) for part in item["ip"].split(".")) if item["ip"].count(".") == 3 else (999,))
        note = ("Devices this computer has exchanged traffic with recently (Windows' neighbour cache); no scan is sent, "
                "so quiet devices may be missing. Phones usually use a private, randomised address.")
        if not self.vendors.available():
            note += " Manufacturer names need the IEEE registry: say 'update device vendor names'."
        names = self.book.load()
        for row in rows:
            entry = names.get(row["mac"]) or {}
            row["name"] = entry.get("name") or ""
            row["first_seen"] = entry.get("first_seen")
        others = [row for row in rows if row["role"] == "device"]
        return {"devices_view": True, "devices": rows, "count": len(others), "this_computer": ip.get("ipv4"),
                "note": note, "summary": f"{len(others)} other device(s) seen recently on {ip.get('interface') or 'this network'}."}

    def guard(self) -> dict[str, Any]:
        """Compare the devices on the network now with every device seen before; report the new ones."""
        view = self.devices()
        now = datetime.fromtimestamp(self.clock()).astimezone().isoformat(timespec="seconds")
        seen = self.book.observe(view["devices"], view.get("this_computer") or "", now)
        if seen["baseline"]:
            summary = (f"Started watching this network: {len(view['devices'])} device(s) recorded as known. "
                       "From now on a device never seen before is reported.")
        elif seen["new"]:
            summary = f"{len(seen['new'])} new device(s) on your network: " + "; ".join(
                f"{row['ip']} ({row.get('vendor') or ('private address' if row.get('private_address') else 'unknown maker')})"
                for row in seen["new"])
        else:
            summary = f"No new devices; {view['count']} other device(s) seen recently, {seen['known']} known in all."
        return {"guard_view": True, **seen, "devices": view["devices"], "summary": summary,
                "note": "Detection uses Windows' neighbour cache: a device that has not talked on the network recently "
                        "is not seen. Name a device to recognise it: 'name device 192.168.12.203 as living room TV'."}

    def name_device(self, device: str, name: str) -> dict[str, Any]:
        entry = self.book.name(device, name)
        return {"named_view": True, **entry, "summary": f"Named {entry['mac']} ({entry.get('ip')}) '{entry['name']}'."}

    def update_vendors(self) -> dict[str, Any]:
        if self.fetch is None:
            raise ValueError("No public fetcher is set up to download the IEEE registry.")
        return {"vendor_update_view": True, **self.vendors.update(self.fetch)}


def router_sentence(result: dict[str, Any]) -> str:
    wifi, router, latency = result.get("wifi") or {}, result.get("router") or {}, result.get("latency_ms") or {}
    name = " ".join(str(part) for part in (router.get("maker"), router.get("model")) if part) or router.get("admin_title") \
        or router.get("vendor") or "The router"
    parts = [f"{name} at {router.get('address')}"]
    if wifi:
        parts.append(f"Wi-Fi '{wifi.get('ssid')}' on {wifi.get('band')} channel {wifi.get('channel')}, signal "
                     f"{wifi.get('signal_percent') or 0:.0f}% ({wifi.get('rssi_quality') or '?'})")
    for radio_name, radio in (router.get("cellular") or {}).items():
        quality = radio.get("quality") or {}
        parts.append(f"{radio_name.upper()} {','.join(radio.get('bands') or [])}: SINR {radio.get('sinr')} dB, RSRP "
                     f"{radio.get('rsrp')} dBm ({quality.get('overall') or '?'})")
    if router.get("uptime_s"):
        parts.append(f"up {router['uptime_s'] // 3600} h {router['uptime_s'] % 3600 // 60} min")
    if latency.get("router") is not None:
        internet = (f", internet {latency['internet']} ms" if latency.get("internet") is not None
                    else ", internet unreachable" if latency.get("internet_measured") else "")
        parts.append(f"router {latency['router']} ms" + internet)
    return "; ".join(parts) + "."


# ---------------------------------------------------------------- chat
def report(history: list[dict[str, Any]]) -> str | None:
    sections = []
    for event in history or []:
        if not str(event.get("tool", "")).startswith("network_"):
            continue
        result = event.get("result") or {}
        if not result.get("success") or not (event.get("verification") or {}).get("passed"):
            continue
        value = result.get("output") or {}
        if value.get("router_view"):
            lines = [value["summary"]]
            router = value.get("router") or {}
            if router.get("firmware"):
                lines.append(f"Firmware {router['firmware']} ({router.get('firmware_state') or 'update state unknown'}).")
            if router.get("admin_page"):
                lines.append(f"Admin page: {router['admin_page']}" + (f" ({router['admin_title']})" if router.get("admin_title") else ""))
        elif value.get("nearby_view"):
            lines = [value["summary"]]
            lines += [f"  {row['ssid']}: {row.get('signal_percent')}% · {row.get('band')} ch {row.get('channel')}"
                      + (f" · {row['stations']} stations" if row.get("stations") is not None else "")
                      + (f" · {row['utilization_percent']}% busy" if row.get("utilization_percent") is not None else "")
                      for row in value["networks"][:12]]
        elif value.get("devices_view"):
            lines = [value["summary"]]
            lines += [f"  {row['ip']}  {row['mac']}  " + ("router" if row["role"] == "router" else
                      (row.get("vendor") or ("private (randomised) address" if row["private_address"] else "maker unknown")))
                      + (f"  '{row['name']}'" if row.get("name") and row["role"] != "router" else "")
                      for row in value["devices"][:40]]
            lines.append(value["note"])
        elif value.get("vendor_update_view") or value.get("named_view"):
            lines = [value["summary"]]
        elif value.get("guard_view"):
            lines = [value["summary"], value["note"]]
        else:
            continue
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else None


_ROUTER = re.compile(
    r"(?i)\b(?:(?:my|the|wi-?fi|home|internet|5g|4g|lte)\s+)*(?:router|gateway|modem)\b|"
    r"\b(?:5g|4g|lte|cellular)\s+(?:signal|connection|reception|strength|bars)\b|"
    r"\bhow(?:'s|\s+is)\s+(?:my|the)\s+(?:internet|wi-?fi|connection|network)\b|"
    r"\b(?:what|which)\s+(?:wi-?fi|network)\s+am\s+i\s+(?:on|connected\s+to)\b")
_DEVICES = re.compile(
    r"(?i)\b(?:who(?:'s|\s+is)|what(?:'s|\s+is)|which\s+devices\s+are|devices|show\s+(?:the\s+)?devices)\s+"
    r"(?:(?:are\s+)?(?:connected\s+|on\s+)?(?:to\s+)?)?(?:on\s+|to\s+)?(?:my|the|our)\s+(?:wi-?fi|network|router|lan|home\s+network)\b|"
    r"\bdevices\s+(?:on|connected\s+to)\s+(?:my|the|our)\s+(?:wi-?fi|network|router|lan)\b")
_NEARBY = re.compile(
    r"(?i)\b(?:nearby|other|neighbou?rs?'?|available)\s+wi-?fi(?:\s+networks?)?\b|\bscan\s+(?:for\s+)?wi-?fi\b|"
    r"\bwi-?fi\s+(?:channels?|interference|congestion|networks?\s+(?:near|around)\s+(?:me|here))\b|"
    r"\bbest\s+wi-?fi\s+channel\b")
_GUARD = re.compile(r"(?i)\b(?:any\s+)?new\s+devices?\s+(?:on|joined|connected\s+to)\s+(?:my|the|our)\s+(?:wi-?fi|network|router)\b|"
                    r"\b(?:network|wi-?fi)\s+(?:guard|watch|intruders?)\b|\bwatch\s+(?:my|the)\s+(?:wi-?fi|network)\b|"
                    r"\b(?:unknown|strange|intruder)\s+devices?\b")
_NAME = re.compile(r"(?i)^(?:please\s+)?(?:name|call|label)\s+(?:the\s+)?device\s+(?P<device>\S+)\s+(?:as\s+)?(?P<name>.{1,60}?)[.!]*$")
_VENDORS = re.compile(r"(?i)\bupdate\s+(?:the\s+)?(?:device\s+)?(?:vendor|manufacturer)\s+(?:names|list|table)\b")


def network_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if not value or len(value) > 200 or re.match(r"(?i)^(?:what\s+is|explain|define|how\s+does)\s+an?\s+(?:router|gateway|modem)\b", value):
        return None
    if _VENDORS.search(value):
        return {"tool": "network_vendor_update", "arguments": {}}
    if named := _NAME.match(value):
        return {"tool": "network_name_device", "arguments": {"device": named["device"], "name": named["name"].strip("'\"")}}
    if _GUARD.search(value):
        return {"tool": "network_guard", "arguments": {}}
    if _DEVICES.search(value):
        return {"tool": "network_devices", "arguments": {}}
    if _NEARBY.search(value):
        return {"tool": "network_nearby_wifi", "arguments": {}}
    if _ROUTER.search(value) and not re.search(r"(?i)\b(?:buy|recommend|best\s+router|setup\s+a\s+new|which\s+router\s+should)\b", value):
        return {"tool": "network_router", "arguments": {}}
    return None


_DESCRIPTIONS = {"network_router": "Read the Wi-Fi router this computer is connected to: connection, router status, signal and latency.",
                 "network_nearby_wifi": "List nearby Wi-Fi networks from Windows' last scan and judge channel crowding.",
                 "network_devices": "List devices recently seen on this network (Windows' neighbour cache).",
                 "network_vendor_update": "Download the IEEE manufacturer registry for device names.",
                 "network_guard": "Compare the devices on the network with every device seen before and report new ones.",
                 "network_name_device": "Give a device on the network a name."}


def direct_network_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = network_request(text)
    if not request or request["tool"] not in set(names):
        return None
    return [{"description": _DESCRIPTIONS[request["tool"]], "tool": request["tool"], "arguments": request["arguments"],
             "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_network_tools(registry: Any, fetch: Callable[[str], bytes] | None = None) -> NetworkInspector:
    inspector = NetworkInspector(fetch=fetch)
    registry.network = inspector
    registry.register(
        "network_router",
        "Read the Wi-Fi router this computer is connected to: SSID, band, channel and signal; IP, gateway and DNS; the "
        "router's model, firmware, uptime and 5G/LTE signal when it publishes a status page without a login (T-Mobile "
        "5G Gateway); and latency to the router and the internet. Read-only; contacts only the default gateway.",
        {"type": "object", "properties": {"measure_internet": {"type": "boolean"}}, "additionalProperties": False},
        "low", "system_read", inspector.router)
    registry.register(
        "network_nearby_wifi",
        "Nearby Wi-Fi networks from Windows' last scan (signal, band, channel, stations, channel utilisation) and how "
        "crowded this router's channel is.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "system_read", lambda: inspector.nearby())
    registry.register(
        "network_devices",
        "Devices recently seen on this network from Windows' neighbour (ARP) cache: IP, MAC, manufacturer when known, "
        "and whether the address is private (randomised). Sends no scan.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "system_read", lambda: inspector.devices())
    registry.register(
        "network_vendor_update",
        "Download the IEEE MAC-prefix registry (about 4 MB) into data/network so device manufacturers can be named.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "public_download",
        lambda: inspector.update_vendors())
    registry.register(
        "network_guard",
        "Network guard: compare the devices on this network now with every device seen before and report new ones "
        "(the first look records what is there). Uses Windows' neighbour cache; sends no scan.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "system_read", lambda: inspector.guard())
    registry.register(
        "network_name_device",
        "Give a device seen on the network a name (by IP or MAC), so the device list and new-device alerts recognise it.",
        {"type": "object", "properties": {"device": {"type": "string", "maxLength": 40},
                                          "name": {"type": "string", "minLength": 1, "maxLength": 60}},
         "required": ["device", "name"], "additionalProperties": False},
        "low", "filesystem_write_project", inspector.name_device)
    return inspector
