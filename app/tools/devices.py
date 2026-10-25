"""What is plugged into this computer, and where it is.

Two read-only tools, built after a live conversation in which HAYAT was asked
"where are we located now" and answered "Charlotte, North Carolina" from an
"IP address trace" that never ran -- no tool was called at all, and the real
Windows location fix was hundreds of miles away. Location and the device list
are facts about this machine; they are read from it, never recalled.

* ``list_devices`` reads Windows Plug and Play (the same list Device Manager
  shows) and groups it into things a person recognises: cameras, microphones and
  speakers, Bluetooth, USB, displays, printers, network, keyboards and mice,
  drives and the battery. It opens nothing -- the camera light does not come on.
* ``device_location`` asks the Windows Location service first (GPS, Wi-Fi or
  cell positioning, with its own accuracy radius). Only when that is off or
  denied does it fall back to an approximate city from the public IP address,
  and it says which one it used and how far off that can be.

Both are bound by the authority kernel like any other tool: the location tool is
a sensor (computer_observe), so privacy mode switches it off.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

DEVICE_TOOL_NAMES = ("list_devices", "device_location")

#: Windows PnP classes, grouped the way a person would describe them.
_GROUPS: dict[str, tuple[str, ...]] = {
    "cameras": ("Camera", "Image"),
    "audio": ("AudioEndpoint", "MEDIA"),
    "bluetooth": ("Bluetooth",),
    "usb": ("USB", "USBDevice"),
    "displays": ("Monitor", "Display"),
    "printers": ("Printer", "PrintQueue"),
    "network": ("Net",),
    "keyboards_and_mice": ("Keyboard", "Mouse"),
    "drives": ("DiskDrive", "CDROM"),
    "battery": ("Battery",),
    "ports": ("Ports",),
}

GROUP_TITLES = {
    "cameras": "Cameras", "audio": "Microphones and speakers", "bluetooth": "Bluetooth",
    "usb": "USB", "displays": "Displays and graphics", "printers": "Printers",
    "network": "Network adapters", "keyboards_and_mice": "Keyboards and mice",
    "drives": "Drives", "battery": "Battery", "ports": "Ports",
}

#: Plumbing Windows lists beside the real devices. Hiding it is what makes the
#: list readable: 29 Bluetooth rows on this machine were mostly service stubs.
_NOISE = re.compile(
    r"(?i)(?:\bwan miniport\b|kernel debug|\benumerator\b|generic attribute|generic access|"
    r"\brfcomm\b|\bavrcp\b|\broot hub\b|host controller|generic usb hub|usb composite device|"
    r"\bteredo\b|\bisatap\b|6to4|\bvirtual\b|\bmicrosoft print to pdf\b|\bonenote\b|\bfax\b|"
    r"microsoft xps|bluetooth le generic|device information service|\btransport\b|\bservice\b$|"
    r"streaming service proxy|root print queue)"
)

_PNP_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue'; "
    "$d = @(Get-PnpDevice -PresentOnly | Select-Object Class,FriendlyName,Status); "
    "$b = @(Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining,BatteryStatus); "
    "@{devices=$d; battery=$b} | ConvertTo-Json -Depth 4 -Compress"
)

#: Win32_Battery.BatteryStatus values worth saying out loud.
_BATTERY_STATUS = {1: "discharging", 2: "on mains power", 3: "fully charged", 4: "low", 5: "critical",
                   6: "charging", 7: "charging", 8: "charging", 9: "charging", 11: "partly charged"}

_LOCATION_SCRIPT = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher([System.Device.Location.GeoPositionAccuracy]::High)
$started = $w.TryStart($false, [TimeSpan]::FromSeconds(__WAIT__))
$deadline = (Get-Date).AddSeconds(__WAIT__)
while ($w.Status.ToString() -ne 'Ready' -and $w.Permission.ToString() -ne 'Denied' -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
$c = $w.Position.Location
$out = [ordered]@{ started=$started; status=$w.Status.ToString(); permission=$w.Permission.ToString();
  unknown=$c.IsUnknown; latitude=$c.Latitude; longitude=$c.Longitude; accuracy_m=$c.HorizontalAccuracy;
  altitude_m=$c.Altitude; timestamp=$w.Position.Timestamp.ToString('o') }
$w.Stop()
$out | ConvertTo-Json -Compress
"""

#: Free, keyless services. Each sees this computer's public IP address -- that
#: is how they work -- and nothing else is sent.
IP_SERVICES = ("https://ipwho.is/", "https://ipapi.co/json/")
#: OpenStreetMap Nominatim and Photon both disallow automated use in robots.txt,
#: which the fetcher honours; BigDataCloud's client endpoint is free and allows it.
REVERSE_GEOCODER = ("https://api.bigdatacloud.net/data/reverse-geocode-client"
                    "?latitude={lat}&longitude={lon}&localityLanguage=en")

TURN_ON_LOCATION = ("For an exact position, turn on Windows location: Settings > Privacy & security > Location, "
                    "switch on Location services and 'Let desktop apps access your location'.")


def _powershell(script: str, timeout: float) -> str:
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0 and not completed.stdout.strip():
        raise RuntimeError((completed.stderr or "PowerShell failed").strip()[:300])
    return completed.stdout.strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def group_devices(rows: list[dict[str, Any]], *, include_hidden: bool = False) -> dict[str, Any]:
    """Sort raw PnP rows into named groups, separating devices with a problem."""
    groups: dict[str, list[dict[str, str]]] = {key: [] for key in _GROUPS}
    problems: list[dict[str, str]] = []
    hidden = 0
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("Class") or "")
        name = " ".join(str(row.get("FriendlyName") or "").split())
        status = str(row.get("Status") or "Unknown")
        if not name:
            continue
        group = next((key for key, classes in _GROUPS.items() if cls in classes), None)
        if cls == "Image" and not re.search(r"(?i)camera|webcam", name):
            group = "printers"  # a scanner, usually the scan half of a printer
        if group is None:
            continue
        if not include_hidden and _NOISE.search(name):
            hidden += 1
            continue
        if (group, name) in seen:
            continue
        seen.add((group, name))
        entry = {"name": name, "status": status, "class": cls}
        groups[group].append(entry)
        if status.upper() not in {"OK", ""}:
            problems.append({**entry, "group": group})
    return {"groups": {key: value for key, value in groups.items() if value}, "problems": problems,
            "hidden_system_entries": hidden}


def _battery(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in rows:
        if isinstance(row, dict) and row.get("EstimatedChargeRemaining") is not None:
            status = row.get("BatteryStatus")
            return {"percent": int(row["EstimatedChargeRemaining"]),
                    "state": _BATTERY_STATUS.get(int(status), "unknown") if isinstance(status, int) else "unknown"}
    return None


class DeviceInspector:
    """Reads the device list and the location. `run` and `get_json` are injectable for tests."""

    def __init__(self, get_json: Callable[[str], Any] | None = None,
                 run: Callable[[str, float], str] = _powershell, places: Any = None) -> None:
        self.get_json = get_json
        self.run = run
        if places is None:
            from app.location_privacy import PlaceIndex
            places = PlaceIndex()
        self.places = places

    # ------------------------------------------------------------------ devices
    def list_devices(self, kind: str = "", include_hidden: bool = False) -> dict[str, Any]:
        if os.name != "nt":
            return {"available": False, "groups": {}, "problems": [], "count": 0,
                    "note": "The device list reads Windows Plug and Play; this computer is not running Windows."}
        raw = json.loads(self.run(_PNP_SCRIPT, 30) or "{}")
        grouped = group_devices(_as_list(raw.get("devices")), include_hidden=include_hidden)
        groups = grouped["groups"]
        wanted = _kind(kind)
        if wanted:
            groups = {wanted: groups.get(wanted, [])}
        result = {
            "available": True,
            "observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "Windows Plug and Play (the list Device Manager shows), read just now",
            "groups": groups,
            "count": sum(len(items) for items in groups.values()),
            "problems": [item for item in grouped["problems"] if not wanted or item["group"] == wanted],
            "hidden_system_entries": grouped["hidden_system_entries"],
            "battery": _battery(_as_list(raw.get("battery"))),
            "note": "Listing a device does not open it; cameras and microphones stay off.",
        }
        if wanted and not groups[wanted]:
            result["note"] = f"No {GROUP_TITLES[wanted].lower()} are connected right now."
        return result

    # ----------------------------------------------------------------- location
    def device_location(self, wait_seconds: int = 10, allow_network: bool = True) -> dict[str, Any]:
        wait = max(2, min(int(wait_seconds or 10), 30))
        result: dict[str, Any] = {"observed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                  "time_zone": _time_zone()}
        windows = self._windows_fix(wait) if os.name == "nt" else {"error": "not Windows"}
        result["windows_location"] = {key: windows.get(key) for key in ("status", "permission", "error") if windows.get(key)}
        if windows.get("latitude") is not None:
            result.update({
                "method": "windows_location_service",
                "precise": True,
                "latitude": round(float(windows["latitude"]), 5),
                "longitude": round(float(windows["longitude"]), 5),
                "accuracy_m": round(float(windows.get("accuracy_m") or 0)) or None,
                "source": "Windows Location service on this computer (GPS, Wi-Fi or cell positioning)",
            })
            # The guard on outgoing messages needs to know where the owner is; held in memory only.
            from app.location_privacy import SHIELD
            SHIELD.remember_fix(result["latitude"], result["longitude"])
            place = self._reverse_geocode(result["latitude"], result["longitude"])
            if place:
                result["place"] = place
            elif not self.places.available():
                result["place_note"] = ('Place names are named on this computer once they are set up: say "set up private '
                                        'place names". Your coordinates are not sent to an online service to name them.')
            return result
        result["why_not_precise"] = _why_not_precise(windows)
        if not allow_network:
            result.update({"method": "none", "precise": False,
                           "note": "Windows location gave no position, and network lookup was not allowed.",
                           "how_to_get_precise": TURN_ON_LOCATION})
            return result
        approximate = self._ip_location()
        if approximate:
            result.update(approximate)
            result.update({"method": "public_ip_lookup", "precise": False,
                           "accuracy": "city level at best; often tens of kilometres off, and a VPN or mobile "
                                       "network shows where that network exits, not where you are",
                           "how_to_get_precise": TURN_ON_LOCATION})
            return result
        result.update({"method": "none", "precise": False,
                       "note": "No location could be read: Windows location gave no position and the public IP "
                               "lookup services did not answer.",
                       "how_to_get_precise": TURN_ON_LOCATION})
        return result

    def _windows_fix(self, wait: int) -> dict[str, Any]:
        try:
            data = json.loads(self.run(_LOCATION_SCRIPT.replace("__WAIT__", str(wait)), wait * 2 + 15) or "{}")
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"[:240]}
        if not isinstance(data, dict):
            return {"error": "unexpected reply from the location service"}
        latitude, longitude = data.get("latitude"), data.get("longitude")
        usable = (not data.get("unknown") and isinstance(latitude, (int, float)) and isinstance(longitude, (int, float))
                  and not (latitude != latitude or longitude != longitude))  # NaN means no fix
        if not usable:
            data["latitude"] = data["longitude"] = None
        return data

    def _reverse_geocode(self, latitude: float, longitude: float) -> dict[str, Any] | None:
        """The place's name from GeoNames on this computer (app/location_privacy.py). The owner's coordinates
        used to go to BigDataCloud for this, so an outside service learned them on every look; never again."""
        try:
            return self.places.nearest(latitude, longitude)
        except Exception:
            log_swallowed(_log, "naming the place from the local GeoNames list")
            return None

    def _ip_location(self) -> dict[str, Any] | None:
        if self.get_json is None:
            return None
        for url in IP_SERVICES:
            try:
                data = self.get_json(url)
            except Exception:
                log_swallowed(_log, f"public IP location from {url}")
                continue
            if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
                continue
            city, region = data.get("city"), data.get("region")
            country = data.get("country") or data.get("country_name")
            latitude, longitude = data.get("latitude"), data.get("longitude")
            if not (city or country):
                continue
            connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
            return {"place": {"label": _place_label(city, region, country), "city": city, "region": region,
                              "country": country, "source": url},
                    "latitude": latitude, "longitude": longitude,
                    "network": connection.get("isp") or connection.get("org") or data.get("org"),
                    "source": f"public IP address lookup ({url})"}
        return None


def _place_label(city: Any, region: Any, country: Any) -> str:
    return ", ".join(str(part) for part in (city, region, country) if part)


def _why_not_precise(windows: dict[str, Any]) -> str:
    if windows.get("error"):
        return "The Windows location service could not be read: " + str(windows["error"])
    if str(windows.get("permission")) == "Denied":
        return "Windows location is turned off or desktop apps are not allowed to use it."
    return "Windows location gave no position within the wait (no GPS, Wi-Fi positioning or recent fix)."


def _time_zone() -> str:
    moment = datetime.now().astimezone()
    offset = moment.strftime("%z")
    return f"{moment.tzname()} (UTC{offset[:3]}:{offset[3:]})" if offset else str(moment.tzname())


_KIND_WORDS = {
    "cameras": ("camera", "webcam"), "audio": ("audio", "microphone", "mic", "speaker", "headphone", "headset", "sound"),
    "bluetooth": ("bluetooth",), "usb": ("usb",), "displays": ("display", "monitor", "screen", "graphics", "gpu"),
    "printers": ("printer",), "network": ("network", "wifi", "wi-fi", "ethernet", "adapter"),
    "keyboards_and_mice": ("keyboard", "mouse", "mice"), "drives": ("drive", "disk", "ssd", "storage"),
    "battery": ("battery",), "ports": ("port", "serial"),
}


def _kind(kind: str) -> str:
    value = str(kind or "").lower().strip()
    if not value or value in {"all", "any", "every"}:
        return ""
    if value in _GROUPS:
        return value
    for key, words in _KIND_WORDS.items():
        if any(word in value for word in words):
            return key
    return ""


# ---------------------------------------------------------------- routing
_LOCATION_REQUEST = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:"
    # Bare "where are we" is usually about a lesson or a task, so it needs a
    # word that makes it about place; "where am I" alone is about place.
    r"(?:where\s+(?:am\s+i|is\s+(?:this|my)\s+(?:computer|pc|laptop|device))"
    r"(?:\s+(?:located|right\s+now|now|at\s+the\s+moment|currently))*"
    r"|where\s+are\s+we(?:\s+(?:located|right\s+now|now|at\s+the\s+moment|currently))+)"
    r"|(?:(?:what(?:'s|\s+is)|show|tell\s+me|find|get|check|look\s+(?:for|up)|give\s+me|detect)\s+)?"
    r"(?:my|our|this\s+(?:computer|pc|laptop|device)'?s?|the\s+(?:computer|pc|laptop|device)'?s?|current)\s+"
    r"(?:current\s+|exact\s+|precise\s+|gps\s+)?(?:location|loaction|locaton|locatoin|position|coordinates|gps|whereabouts)"
    r"(?:\s+(?:now|right\s+now|please))*"
    r"|locate\s+(?:me|us|this\s+(?:computer|pc|laptop|device)|my\s+(?:computer|pc|laptop|device))"
    r")[\s?.!]*$"
)

_DEVICE_REQUEST = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:"
    r"(?:list|show|check|scan|find|detect|see|look\s+(?:at|for)|access|read|get|what\s+are|which\s+are)\s+"
    r"(?:(?:me|all|of)\s+)*(?:the\s+|my\s+|this\s+)?(?:(?:computer|pc|laptop)'?s?\s+)?"
    r"(?:(?:connected|attached|plugged[- ]in|available|computer|pc|laptop|hardware|usb|bluetooth|audio|"
    r"input|output)\s+)*(?:devices|peripherals|device\s+list|hardware\s+devices)"
    r"(?:\s+(?:connected|attached|plugged\s+in)(?:\s+to\s+(?:this|my|the)\s+(?:computer|pc|laptop))?)?"
    r"(?:\s+(?:on|in|of)\s+(?:this|my|the)\s+(?:computer|pc|laptop|machine))?"
    r"(?:\s+(?:now|right\s+now|please))*"
    r"|what(?:'s|\s+is)\s+(?:plugged|connected)\s+(?:in(?:to)?|to)\s+(?:this|my|the)\s+(?:computer|pc|laptop)"
    r"|what\s+(?:devices|peripherals)\s+(?:are|do\s+i\s+have)(?:\s+\w+){0,6}"
    r")[\s?.!]*$"
)


def device_request(text: str) -> dict[str, Any] | None:
    """The device tool a sentence asks for, as {"tool", "arguments"}, or None."""
    value = " ".join(str(text or "").split())
    if _LOCATION_REQUEST.match(value):
        return {"tool": "device_location", "arguments": {}}
    if _DEVICE_REQUEST.match(value):
        lowered = value.lower()
        kind = next((key for key, words in _KIND_WORDS.items() if key not in {"drives", "network", "ports"}
                     and any(re.search(rf"\b{re.escape(word)}s?\b", lowered) for word in words)), "")
        return {"tool": "list_devices", "arguments": {"kind": kind} if kind else {}}
    return None


def direct_device_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = device_request(text)
    if not request or request["tool"] not in set(names):
        return None
    description = ("Read this computer's location from the Windows Location service, or an approximate one "
                   "from its public IP address, and say which." if request["tool"] == "device_location" else
                   "Read the devices connected to this computer from Windows Plug and Play.")
    return [{"description": description, "tool": request["tool"], "arguments": request["arguments"],
             "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_device_tools(registry: Any, get_json: Callable[[str], Any] | None = None) -> DeviceInspector:
    inspector = DeviceInspector(get_json=get_json)
    registry.register(
        "list_devices",
        "List the devices connected to this computer (cameras, microphones and speakers, Bluetooth, USB, displays, "
        "printers, network adapters, keyboards and mice, drives, battery) from Windows Plug and Play, with any "
        "device that reports a problem. Read-only; opens nothing.",
        {"type": "object", "properties": {
            "kind": {"type": "string", "description": "Optional group: cameras, audio, bluetooth, usb, displays, "
                     "printers, network, keyboards_and_mice, drives, battery or ports."},
            "include_hidden": {"type": "boolean"}},
         "additionalProperties": False},
        "low", "system_read", inspector.list_devices)
    registry.register(
        "device_location",
        "Find where this computer is: the Windows Location service (GPS/Wi-Fi, with an accuracy radius) first, "
        "else an approximate city from the public IP address. Always reports which method was used.",
        {"type": "object", "properties": {
            "wait_seconds": {"type": "integer", "minimum": 2, "maximum": 30},
            "allow_network": {"type": "boolean"}},
         "additionalProperties": False},
        "low", "computer_observe", inspector.device_location)
    return inspector
