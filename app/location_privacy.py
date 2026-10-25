"""Your location: HAYAT can read it, nobody else learns it through HAYAT, and you can see who else can.

The owner asked (2026-10-17) that HAYAT may look up his location but nobody else may detect it.
HAYAT cannot hide a computer from the whole internet; what it can do, and does here:

* **Place names on this computer.** A Windows location fix used to be sent to BigDataCloud to name
  the place, so a third party learned the exact coordinates every time HAYAT looked. Now the name
  comes from GeoNames' list of ~144,000 places (population over 1,000), installed once into
  data/places/ from a hash-pinned PyPI file, and the coordinates never leave the computer.
* **A guard on everything that leaves.** Before a tool that sends, posts or reaches the internet
  runs, its arguments are screened (ToolRegistry -> LocationShield.screen):
    - text holding the owner's precise coordinates, a map link to them, or the postcode of the
      latest fix is held, with how to share it once on purpose;
    - photos attached to a message have their GPS tags removed (a stripped copy is sent);
    - a position a public lookup needs (weather, a deep look) near the owner is rounded to one
      decimal (about 11 km), enough for the weather and too coarse to find a house.
  The latest fix is kept in memory only, never written to disk.
* **An exposure check** of the ways others can learn the location: which Windows apps may use the
  location service and when each last did, which websites the browsers allow, what any website
  can tell from the public IP address, and photos that carry GPS tags. (The Wi-Fi name is left alone:
  the owner chose a VPN and Tor over renaming any network.)
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger("hayat.location_privacy")
_ROOT = Path(__file__).resolve().parents[1]

PLACES_DIR = _ROOT / "data" / "places"
PLACES_FILE = PLACES_DIR / "places.csv"
#: reverse_geocoder 1.5.1 on PyPI bundles GeoNames cities1000 (CC BY 4.0). PyPI files never change, so
#: the hash is pinned. download.geonames.org itself asks robots not to fetch, so it is not used.
PLACES_SOURCE = ("https://files.pythonhosted.org/packages/0b/0f/b7d5d4b36553731f11983e19e1813a1059ad0732c5162c01b3220c927d31/"
                 "reverse_geocoder-1.5.1.tar.gz",
                 "2a2e781b5f69376d922b78fe8978f1350c84fce0ddb07e02c834ecf98b57c75c")
PLACES_MEMBER = "reverse_geocoder-1.5.1/reverse_geocoder/rg_cities1000.csv"
MAX_PLACES_DOWNLOAD = 8 * 1024 * 1024

#: Tool categories whose arguments leave the computer: messages and posts, and network reads.
SENDING = frozenset({"send_message", "external_post", "connector_write", "mcp_write"})
NETWORK = frozenset({"web_search", "public_web_read", "public_download", "bounded_crawl", "public_api_get",
                     "knowledge_research", "browser_read", "browser_click", "browser_type", "browser_download"})
#: How close to the latest fix a coordinate must be to count as the owner's location.
NEAR_KM = 5.0
ATTACHMENT_KEYS = ("attachments", "attachment", "files", "file", "path", "paths", "image", "images", "photo", "photos")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp")
GPS_IFD = 0x8825


def km_between(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    rad = math.radians
    d_lat, d_lon = rad(b_lat - a_lat), rad(b_lon - a_lon)
    h = math.sin(d_lat / 2) ** 2 + math.cos(rad(a_lat)) * math.cos(rad(b_lat)) * math.sin(d_lon / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def country_name(code: str) -> str:
    try:
        from babel import Locale

        return Locale("en").territories.get(code.upper(), code)
    except Exception:
        return code


# ------------------------------------------------------------------ place names on this computer

class PlaceIndex:
    """Nearest named place to a coordinate, from a local GeoNames list."""

    def __init__(self, path: Path = PLACES_FILE) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._rows: list[tuple[str, str, str, str]] | None = None
        self._tree: Any = None

    def available(self) -> bool:
        return self.path.is_file()

    def _load(self) -> None:
        import numpy as np
        from scipy.spatial import cKDTree

        coordinates, rows = [], []
        with self.path.open(encoding="utf-8") as handle:
            header = handle.readline().strip().split(",")
            if header[:6] != ["lat", "lon", "name", "admin1", "admin2", "cc"]:
                raise ValueError("The places file is not the expected GeoNames list.")
            for line in handle:
                parts = line.rstrip("\n").split(",")
                if len(parts) < 6:
                    continue
                try:
                    lat, lon = float(parts[0]), float(parts[1])
                except ValueError:
                    continue
                coordinates.append(_unit(lat, lon))
                rows.append((parts[2], parts[3], parts[4], parts[5]))
        self._tree, self._rows = cKDTree(np.array(coordinates)), rows
        self._coordinates = coordinates

    def nearest(self, lat: float, lon: float) -> dict[str, Any] | None:
        if not self.available():
            return None
        with self._lock:
            if self._tree is None:
                self._load()
        _, index = self._tree.query(_unit(lat, lon))
        name, region, county, code = self._rows[int(index)]
        x, y, z = self._coordinates[int(index)]
        place_lat, place_lon = math.degrees(math.asin(z)), math.degrees(math.atan2(y, x))
        distance = km_between(lat, lon, place_lat, place_lon)
        country = country_name(code)
        label = ", ".join(part for part in (name, region, country) if part)
        if distance > 10:
            label = f"near {label} ({distance:.0f} km)"
        return {"label": label, "city": name, "region": region or None, "county": county or None,
                "country": country, "country_code": code, "distance_km": round(distance, 1),
                "source": "GeoNames places on this computer (nothing was sent)"}

    def install(self, fetch: Callable[[str], bytes] | None = None) -> dict[str, Any]:
        """Download the pinned file once, keep only the places list, check it, and move it into place."""
        if self.available():
            return {"installed": True, "status": "already installed", "path": str(self.path)}
        url, expected = PLACES_SOURCE
        data = (fetch or _fetch_https)(url)
        if hashlib.sha256(data).hexdigest() != expected:
            return {"installed": False, "reason": "The places file did not match its published SHA-256, so it was not kept."}
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember(PLACES_MEMBER)  # read by name into memory: nothing in the archive is extracted
            content = archive.extractfile(member).read()
        if not content.startswith(b"lat,lon,name,admin1,admin2,cc"):
            return {"installed": False, "reason": "The places list inside the file had an unexpected format."}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".partial")
        partial.write_bytes(content)
        os.replace(partial, self.path)
        self._tree = self._rows = None
        return {"installed": True, "status": "downloaded", "path": str(self.path), "places": content.count(b"\n") - 1,
                "bytes_downloaded": len(data),
                "source": "GeoNames cities1000 (CC BY 4.0) from the reverse_geocoder 1.5.1 package on PyPI"}


def _unit(lat: float, lon: float) -> tuple[float, float, float]:
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def _fetch_https(url: str) -> bytes:
    import httpx

    chunks, total = [], 0
    with httpx.stream("GET", url, timeout=60, follow_redirects=True, headers={"User-Agent": "HAYAT-places/1.0"}) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(1024 * 1024):
            total += len(chunk)
            if total > MAX_PLACES_DOWNLOAD:
                raise OSError("The download was far larger than the places file.")
            chunks.append(chunk)
    return b"".join(chunks)


# ------------------------------------------------------------------ photos and GPS tags

def photo_gps(path: Path | str) -> tuple[float, float] | None:
    """(lat, lon) a photo's EXIF GPS tags give, or None."""
    try:
        from PIL import Image

        with Image.open(path) as image:
            gps = image.getexif().get_ifd(GPS_IFD)
    except Exception:
        return None
    if not gps:
        return None

    def degrees(values: Any, ref: Any) -> float | None:
        try:
            d, m, s = (float(v) for v in values)
        except Exception:
            return None
        value = d + m / 60 + s / 3600
        return -value if str(ref).upper() in ("S", "W") else value
    lat, lon = degrees(gps.get(2), gps.get(1)), degrees(gps.get(4), gps.get(3))
    return (lat, lon) if lat is not None and lon is not None else (0.0, 0.0)


def strip_gps(path: Path | str, folder: Path | None = None) -> Path:
    """A copy of the photo without its GPS tags (other EXIF kept). The original is untouched."""
    from PIL import Image

    source = Path(path)
    folder = folder or (_ROOT / "data" / "work" / "no-gps")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{source.stem}-no-location{source.suffix.lower()}"
    with Image.open(source) as image:
        exif = image.getexif()
        if GPS_IFD in exif:
            del exif[GPS_IFD]
        options: dict[str, Any] = {"exif": exif.tobytes()}
        if (image.format or "").upper() == "JPEG":
            options["quality"] = "keep"
        image.save(target, format=image.format, **options)
    return target


# ------------------------------------------------------------------ the guard

#: A position is written many ways (hard-tested 2026-10-17; the first guard knew only "51.5074, -0.1278"):
#: "lat=51.5074&lon=-0.1278", "51.5074 N, 0.1278 W", 51°30'26.6"N, 51° 30.44' N, longitude first (GeoJSON),
#: two decimals (about 1 km), "%2C", a Unicode minus, plus codes ("9C3XGV4C+XX", "GV4C+XX London") and geohashes.
#: Each number is read with its hemisphere letter if it has one; neighbours a few characters apart are tried both
#: ways round. One decimal ("51.5, -0.1", about 11 km) is what rounding gives and is not a precise position.
_DECIMAL = re.compile(r"(?<![\w.])([-+]?\d{1,3}\.\d{2,})(?![\d.])(?:\s*[°º])?(?:\s*([NSEW])\b)?", re.IGNORECASE)
_DMS = re.compile(r"(?<![\d.])(\d{1,3})\s*[°º]\s*(?:(\d{1,2}(?:\.\d+)?)\s*['′’]\s*(?:(\d{1,2}(?:\.\d+)?)\s*(?:[\"″”]|'')\s*)?)?"
                  r"([NSEW])\b", re.IGNORECASE)
_PAIR_GAP = 24  # characters between the two numbers of one position ("&lon=", " lon: ", ", ")
_OLC = "23456789CFGHJMPQRVWX"
_PLUS_CODE = re.compile(r"(?<![\w+])([23456789CFGHJMPQRVWX]{2,8})\+([23456789CFGHJMPQRVWX]{0,3})(?!\w)", re.IGNORECASE)
_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"
_GEOHASH = re.compile(r"(?<![\w])(?=[0-9bcdefghjkmnpqrstuvwxyz]*\d)(?=[0-9bcdefghjkmnpqrstuvwxyz]*[b-z])"
                      r"([0-9bcdefghjkmnpqrstuvwxyz]{6,12})(?![\w])")
_LAT_KEYS = ("lat", "latitude", "mlat")
_LON_KEYS = ("lon", "lng", "long", "longitude", "mlon")


def _plain(text: str) -> str:
    """The text as a reader sees it: URL escapes undone, Unicode minus and thin spaces made plain."""
    if "%" in text:
        from urllib.parse import unquote
        text = unquote(text)
    return text.translate({0x2212: "-", 0x2013: "-", 0x2009: " ", 0x202F: " ", 0x00A0: " "})


def _olc_encode(lat: float, lon: float) -> str:
    lat, lon = min(max(lat, -90.0), 90.0 - 1e-9) + 90.0, (lon + 180.0) % 360.0
    digits, resolution = "", 20.0
    for _ in range(5):
        a, b = int(lat // resolution), int(lon // resolution)
        digits += _OLC[a] + _OLC[b]
        lat, lon, resolution = lat - a * resolution, lon - b * resolution, resolution / 20
    return digits


def _olc_decode(digits: str) -> tuple[float, float] | None:
    """The centre of a plus code's cell, from its digits without the '+' (an even count, up to ten)."""
    if len(digits) < 2 or len(digits) % 2:
        return None
    lat = lon = 0.0
    resolution = 20.0
    for index in range(0, min(len(digits), 10), 2):
        lat += _OLC.index(digits[index]) * resolution
        lon += _OLC.index(digits[index + 1]) * resolution
        last, resolution = resolution, resolution / 20
    if lat >= 180 or lon >= 360:
        return None
    return lat - 90 + last / 2, lon - 180 + last / 2


def _plus_positions(text: str, reference: tuple[float, float] | None) -> list[tuple[float, float]]:
    found = []
    for match in _PLUS_CODE.finditer(text):
        prefix, suffix = match[1].upper(), match[2].upper()[:2]
        if len(prefix) == 8:
            position = _olc_decode(prefix + suffix)
        elif reference and len(prefix) in (2, 4, 6) and len(suffix) == 2:
            # A short code ("GV4C+XX London") names a cell near a reference: recovered against the owner's own fix.
            missing = 8 - len(prefix)
            position = _olc_decode(_olc_encode(*reference)[:missing] + prefix + suffix)
            if position:
                resolution = 20.0 ** (2 - missing / 2)
                lat, lon = position
                lat += -resolution if reference[0] + resolution / 2 < lat else resolution if reference[0] - resolution / 2 > lat else 0
                lon += -resolution if reference[1] + resolution / 2 < lon else resolution if reference[1] - resolution / 2 > lon else 0
                position = (lat, lon)
        else:
            position = None
        if position:
            found.append(position)
    return found


def _geohash_decode(value: str) -> tuple[float, float]:
    lat, lon, even = [-90.0, 90.0], [-180.0, 180.0], True
    for char in value:
        bits = _GEOHASH_ALPHABET.index(char)
        for shift in range(4, -1, -1):
            span = lon if even else lat
            middle = (span[0] + span[1]) / 2
            span[0 if bits >> shift & 1 else 1] = middle
            even = not even
    return (lat[0] + lat[1]) / 2, (lon[0] + lon[1]) / 2


def text_positions(text: str, reference: tuple[float, float] | None = None) -> list[tuple[float, float]]:
    """Every (lat, lon) a text could be giving, precise to about a kilometre or better."""
    text = _plain(text)
    numbers: list[tuple[int, int, float, str | None]] = []  # (start, end, value, 'lat' | 'lon' | None)
    taken: list[tuple[int, int]] = []
    for match in _DMS.finditer(text):
        if not (match[2] or match[3]):
            continue  # "51° N" alone is a whole degree, not a position
        value = float(match[1]) + float(match[2] or 0) / 60 + float(match[3] or 0) / 3600
        hemisphere = match[4].upper()
        numbers.append((match.start(), match.end(), -value if hemisphere in "SW" else value, "lat" if hemisphere in "NS" else "lon"))
        taken.append(match.span())
    for match in _DECIMAL.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue
        value = float(match[1])
        hemisphere = (match[2] or "").upper()
        if hemisphere in ("S", "W"):
            value = -abs(value)
        numbers.append((match.start(), match.end(), value, "lat" if hemisphere in ("N", "S") else "lon" if hemisphere else None))
    numbers.sort()
    positions = []
    for (_s1, end, first, axis1), (start, _e2, second, axis2) in zip(numbers, numbers[1:]):
        if start - end > _PAIR_GAP or "\n\n" in text[end:start]:
            continue
        if axis1 == "lon" or axis2 == "lat":
            orders = [(second, first)]
        elif axis1 == "lat" or axis2 == "lon":
            orders = [(first, second)]
        else:
            orders = [(first, second), (second, first)]
        positions += [(lat, lon) for lat, lon in orders if -90 <= lat <= 90 and -180 <= lon <= 180]
    positions += _plus_positions(text, reference)
    positions += [_geohash_decode(match[1]) for match in _GEOHASH.finditer(text.lower())]
    return positions


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(_plain(value).strip())
        except ValueError:
            return None
    return None


def _coarse(value: float) -> bool:
    """One decimal or none: about 11 km, what the guard itself rounds to."""
    return abs(value * 10 - round(value * 10)) < 1e-6


def structured_positions(value: Any, depth: int = 0) -> list[tuple[float, float]]:
    """Positions held as data: {"lat": …, "lng": …} at any depth, and [lon, lat] / [lat, lon] number pairs."""
    if depth > 12:
        return []
    found = []
    if isinstance(value, dict):
        lowered = {str(key).casefold(): item for key, item in value.items()}
        lat = next((_number(lowered[key]) for key in _LAT_KEYS if key in lowered), None)
        lon = next((_number(lowered[key]) for key in _LON_KEYS if key in lowered), None)
        if lat is not None and lon is not None and not (_coarse(lat) and _coarse(lon)):
            found.append((lat, lon))
        for item in value.values():
            found += structured_positions(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) in (2, 3) and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            a, b = float(value[0]), float(value[1])
            if not (_coarse(a) and _coarse(b)):
                found += [(lat, lon) for lat, lon in ((a, b), (b, a)) if -90 <= lat <= 90 and -180 <= lon <= 180]
        else:
            for item in value:
                found += structured_positions(item, depth + 1)
    return found


class LocationShield:
    """Screens what leaves the computer for the owner's location. Holds the latest fix in memory only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fix: dict[str, Any] | None = None
        self._allow_until = 0.0
        self.enabled = True
        self.events: list[dict[str, Any]] = []

    def remember_fix(self, lat: float, lon: float, postcode: str | None = None) -> None:
        with self._lock:
            self._fix = {"lat": float(lat), "lon": float(lon), "postcode": str(postcode or "").strip() or None,
                         "at": time.time()}

    def forget_fix(self) -> None:
        with self._lock:
            self._fix = None

    def allow_once(self, minutes: float = 5) -> dict[str, Any]:
        with self._lock:
            self._allow_until = time.time() + max(1, min(float(minutes), 30)) * 60
        return {"allowed": True, "minutes": minutes,
                "note": "The next message or post may include your location; after that, or after "
                        f"{minutes:g} minutes, the guard is back."}

    def _near(self, lat: float, lon: float) -> bool:
        fix = self._fix
        return bool(fix) and km_between(fix["lat"], fix["lon"], lat, lon) <= NEAR_KM

    def _record(self, tool: str, action: str, detail: str) -> None:
        self.events.append({"at": datetime.now().isoformat(timespec="seconds"), "tool": tool, "action": action,
                            "detail": detail})
        del self.events[:-50]

    def _texts(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [text for item in value.values() for text in self._texts(item)]
        if isinstance(value, (list, tuple)):
            return [text for item in value for text in self._texts(item)]
        return []

    def _leak_in_text(self, text: str, sending: bool) -> str | None:
        fix = self._fix
        if not fix:
            return None
        if any(self._near(lat, lon) for lat, lon in text_positions(text, (fix["lat"], fix["lon"]))):
            return "your precise coordinates"
        postcode = re.sub(r"[\s-]+", "", fix.get("postcode") or "")
        # "SW1A 1AA", "sw1a 1aa" and "SW1A1AA" are one postcode; "94103-1234" still starts with "94103".
        if sending and postcode and re.search(r"(?<!\w)" + r"[\s-]?".join(map(re.escape, postcode)) + r"(?!\w)",
                                              _plain(text), re.IGNORECASE):
            return f"your postcode ({fix['postcode']})"
        return None

    def _round_position(self, arguments: dict[str, Any], tool: str) -> tuple[dict[str, Any], list[str]]:
        """Positions near the owner held as data, at any depth, rounded to one decimal (about 11 km)."""
        rounded = False

        def walk(value: Any, depth: int = 0) -> Any:
            nonlocal rounded
            if depth > 12:
                return value
            if isinstance(value, dict):
                lowered = {str(key).casefold(): key for key in value}
                lat_key = next((lowered[key] for key in _LAT_KEYS if key in lowered), None)
                lon_key = next((lowered[key] for key in _LON_KEYS if key in lowered), None)
                out = {key: walk(item, depth + 1) for key, item in value.items()}
                if lat_key is not None and lon_key is not None:
                    lat, lon = _number(value[lat_key]), _number(value[lon_key])
                    if lat is not None and lon is not None and self._near(lat, lon) and not (_coarse(lat) and _coarse(lon)):
                        as_text = isinstance(value[lat_key], str)
                        out[lat_key] = f"{lat:.1f}" if as_text else round(lat, 1)
                        out[lon_key] = f"{lon:.1f}" if as_text else round(lon, 1)
                        rounded = True
                return out
            if isinstance(value, list):
                if (len(value) in (2, 3) and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
                        and any(self._near(lat, lon) for lat, lon in structured_positions(value))):
                    rounded = True
                    return [round(float(item), 1) for item in value]
                return [walk(item, depth + 1) for item in value]
            return value

        arguments = walk(arguments)
        if rounded:
            self._record(tool, "rounded", "a position near you, sent to a public service, rounded to about 11 km")
            return arguments, ["Your position was rounded to about 11 km before the public lookup."]
        return arguments, []

    def _strip_attachments(self, arguments: dict[str, Any], tool: str) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        changed = dict(arguments)
        for key in ATTACHMENT_KEYS:
            value = arguments.get(key)
            items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
            replaced = []
            for item in items:
                path = Path(str(item)).expanduser()
                if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file() and photo_gps(path):
                    try:
                        clean = strip_gps(path)
                    except Exception as exc:
                        raise PermissionError(f"{path.name} carries GPS tags and they could not be removed ({exc}), "
                                              "so it was not sent.") from exc
                    replaced.append(str(clean))
                    notes.append(f"GPS location removed from {path.name} before sending.")
                    self._record(tool, "stripped", f"GPS tags removed from {path.name}")
                else:
                    replaced.append(item)
            if items and replaced != items:
                changed[key] = replaced if isinstance(value, list) else replaced[0]
        return changed, notes

    def screen(self, tool: str, category: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], str | None, list[str]]:
        """(arguments to use, reason it is held or None, notes for the result)."""
        if not self.enabled or category not in SENDING | NETWORK:
            return arguments, None, []
        with self._lock:
            allowed = time.time() < self._allow_until
        sending = category in SENDING
        notes: list[str] = []
        if not allowed:
            leaks = (self._leak_in_text(text, sending) for text in self._texts(arguments))
            if sending and any(self._near(lat, lon) for lat, lon in structured_positions(arguments)):
                leaks = iter(["your precise coordinates"])  # a message carrying {"lat": …, "lng": …} or [lon, lat]
            for leak in leaks:
                if leak:
                    self._record(tool, "held", f"{leak} in {'a message' if sending else 'an internet request'}")
                    return arguments, (f"Held by location privacy: this would send {leak} "
                                       f"{'to someone' if sending else 'to a website'}. If you mean to share it, say "
                                       "\"share my location once\" (you will be asked to approve) and send again."), []
        elif sending:
            with self._lock:
                self._allow_until = 0.0  # one send, then the guard is back
            self._record(tool, "allowed", "shared once with your approval")
        if not sending:
            arguments, rounded = self._round_position(arguments, tool)
            notes += rounded
        try:
            arguments, stripped = self._strip_attachments(arguments, tool)
        except PermissionError as exc:
            self._record(tool, "held", str(exc))
            return arguments, str(exc), []
        return arguments, None, notes + stripped

    def status(self) -> dict[str, Any]:
        with self._lock:
            fix, until = self._fix, self._allow_until
        return {"guard_on": self.enabled, "fix_known": fix is not None,
                "fix_age_minutes": round((time.time() - fix["at"]) / 60, 1) if fix else None,
                "share_allowed_for_minutes": round((until - time.time()) / 60, 1) if until > time.time() else 0,
                "recent": list(self.events[-10:]),
                "basis": "The latest location fix is kept in memory only; it is never written to disk."}


SHIELD = LocationShield()


# ------------------------------------------------------------------ who can learn the location

def _filetime(value: Any) -> str | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return (datetime(1601, 1, 1, tzinfo=UTC) + timedelta(microseconds=number // 10)).astimezone().isoformat(timespec="minutes")


def windows_location_apps(read_key: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Windows' own record of which apps may use the location service, and when each last did."""
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location"
    read = read_key or _read_registry_tree
    try:
        tree = read(base)
    except OSError as exc:
        return {"available": False, "reason": f"Windows' location settings could not be read: {exc}"}
    apps = []
    for name, values in (tree.get("apps") or {}).items():
        apps.append(_app_row(name, values, packaged=True))
    for name, values in (tree.get("desktop") or {}).items():
        apps.append(_app_row(name, values, packaged=False))
    apps.sort(key=lambda row: row["last_used"] or "", reverse=True)
    return {"available": True, "service": tree.get("value") or "unknown", "apps": apps,
            "allowed": [row for row in apps if row["allowed"]],
            "used": [row for row in apps if row["last_used"]]}


def _app_row(name: str, values: dict[str, Any], packaged: bool) -> dict[str, Any]:
    if packaged:
        label = name.split("_")[0].split(".")[-1]
    else:
        label = name.replace("#", "\\").rsplit("\\", 1)[-1]
    setting = values.get("Value")
    return {"app": label, "id": name, "desktop_program": not packaged, "setting": setting or ("allowed" if not packaged else None),
            "allowed": (setting == "Allow") or (not packaged and setting != "Deny"),
            "last_used": _filetime(values.get("LastUsedTimeStop") or values.get("LastUsedTimeStart"))}


#: Programs HAYAT itself uses to read the location (PowerShell) and scan Wi-Fi (netsh).
HAYAT_PROGRAMS = frozenset({"powershell.exe", "netsh.exe", "python.exe", "pythonw.exe"})


def _when(stamp: str | None) -> str:
    """'today 10:00', 'yesterday', or '13 Oct', for a last-used time."""
    if not stamp:
        return "unknown"
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp[:16]
    days = (datetime.now(moment.tzinfo).date() - moment.date()).days
    return f"today {moment:%H:%M}" if days == 0 else "yesterday" if days == 1 else f"{moment.day} {moment:%b}"


def _read_registry_tree(base: str) -> dict[str, Any]:
    import winreg

    def values(key: Any) -> dict[str, Any]:
        found, index = {}, 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, index)
            except OSError:
                return found
            found[name] = value
            index += 1

    def children(key: Any) -> list[str]:
        names, index = [], 0
        while True:
            try:
                names.append(winreg.EnumKey(key, index))
            except OSError:
                return names
            index += 1

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as root:
        tree: dict[str, Any] = {"value": values(root).get("Value"), "apps": {}, "desktop": {}}
        for child in children(root):
            with winreg.OpenKey(root, child) as key:
                if child == "NonPackaged":
                    for program in children(key):
                        with winreg.OpenKey(key, program) as entry:
                            tree["desktop"][program] = values(entry)
                else:
                    tree["apps"][child] = values(key)
    return tree


def browser_sites(local: Path | None = None, roaming: Path | None = None) -> list[dict[str, Any]]:
    """Websites the browsers on this computer allow (or block) to read the location."""
    local = local or Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = roaming or Path(os.environ.get("APPDATA", ""))
    found = []
    for browser, folder in (("Edge", "Microsoft/Edge/User Data"), ("Chrome", "Google/Chrome/User Data"),
                            ("Brave", "BraveSoftware/Brave-Browser/User Data"), ("Opera", "Opera Software/Opera Stable")):
        for preferences in sorted((local / folder).glob("*/Preferences")) + sorted((roaming / folder).glob("Preferences")):
            try:
                data = json.loads(preferences.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            exceptions = data.get("profile", {}).get("content_settings", {}).get("exceptions", {}).get("geolocation", {})
            for pattern, entry in exceptions.items():
                setting = entry.get("setting") if isinstance(entry, dict) else None
                if setting in (1, 2):
                    found.append({"browser": browser, "profile": preferences.parent.name,
                                  "site": pattern.split(",")[0].replace(":443", ""), "allowed": setting == 1})
    for permissions in sorted((roaming / "Mozilla" / "Firefox" / "Profiles").glob("*/permissions.sqlite")):
        handle, name = tempfile.mkstemp(suffix=".sqlite")
        os.close(handle)
        copy = Path(name)
        try:
            shutil.copy2(permissions, copy)  # Firefox keeps the file locked while it runs
            with closing(sqlite3.connect(copy)) as db:  # closed, not just committed, so the copy can be removed
                for origin, permission in db.execute("SELECT origin, permission FROM moz_perms WHERE type='geo'"):
                    if permission in (1, 2):
                        found.append({"browser": "Firefox", "profile": permissions.parent.name, "site": origin,
                                      "allowed": permission == 1})
        except (OSError, sqlite3.Error):
            log_swallowed(_log, "reading Firefox location permissions")
        finally:
            copy.unlink(missing_ok=True)
    return found


def photos_with_gps(folders: list[Path], limit: int = 600) -> dict[str, Any]:
    """Photos in the owner's usual folders whose GPS tags would give away where they were taken."""
    found, scanned = [], 0
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if scanned >= limit:
                break
            if path.suffix.lower() not in (".jpg", ".jpeg", ".tif", ".tiff", ".webp"):
                continue
            scanned += 1
            position = photo_gps(path)
            if position:
                found.append({"path": str(path), "name": path.name})
    return {"scanned": scanned, "limit": limit, "with_location": found[:50], "count": len(found),
            "folders": [str(folder) for folder in folders if folder.is_dir()]}


class LocationPrivacy:
    """Puts the exposure report together, and installs the local place names."""

    def __init__(self, shield: LocationShield = SHIELD, places: PlaceIndex | None = None, *,
                 get_json: Callable[[str], Any] | None = None,
                 apps: Callable[[], dict[str, Any]] = windows_location_apps,
                 sites: Callable[[], list[dict[str, Any]]] = browser_sites,
                 photo_folders: list[Path] | None = None, privacy: Callable[[], bool] = lambda: False) -> None:
        self.shield, self.places = shield, places or PlaceIndex()
        self.get_json, self.apps, self.sites = get_json, apps, sites
        home = Path.home()
        self.photo_folders = photo_folders if photo_folders is not None else [home / "Pictures", home / "Desktop",
                                                                             home / "Downloads"]
        self.privacy = privacy

    def public_ip_view(self) -> dict[str, Any]:
        if self.privacy():
            return {"checked": False, "reason": "Privacy mode is on, so no website was asked."}
        if self.get_json is None:
            return {"checked": False, "reason": "No way to ask a public service."}
        try:
            data = self.get_json("https://ipwho.is/")
        except Exception as exc:
            return {"checked": False, "reason": f"The public IP service did not answer ({type(exc).__name__})."}
        if not isinstance(data, dict) or data.get("success") is False:
            return {"checked": False, "reason": "The public IP service gave no answer."}
        connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
        view = {"checked": True, "city": data.get("city"), "region": data.get("region"), "country": data.get("country"),
                "network": connection.get("isp") or connection.get("org"), "latitude": data.get("latitude"),
                "longitude": data.get("longitude")}
        fix = self.shield._fix
        if fix and isinstance(view["latitude"], (int, float)) and isinstance(view["longitude"], (int, float)):
            view["km_from_you"] = round(km_between(fix["lat"], fix["lon"], view["latitude"], view["longitude"]))
        return view

    def check(self, scan_photos: bool = True) -> dict[str, Any]:
        apps = self.apps()
        sites = self.sites()
        ip = self.public_ip_view()
        photos = photos_with_gps(self.photo_folders) if scan_photos else None
        findings = []
        recent = [row for row in apps.get("used", []) if row["last_used"]
                  and row["last_used"] >= (datetime.now() - timedelta(days=30)).astimezone().isoformat()]
        if apps.get("available"):
            if apps.get("service") == "Allow":
                findings.append({"level": "info", "what": f"The Windows location service is on; {len(apps['allowed'])} "
                                 f"apps may use it and {len(recent)} did in the last 30 days.",
                                 "fix": "Settings → Privacy & security → Location: switch off apps that do not need it.",
                                 "action": "windows"})
            # HAYAT reads the location through PowerShell and scans Wi-Fi through netsh; Windows counts both.
            others = [row for row in recent if row["app"].lower() not in HAYAT_PROGRAMS]
            own = [row for row in recent if row["app"].lower() in HAYAT_PROGRAMS]
            if others:
                findings.append({"level": "watch", "what": "In the last 30 days these apps read your location: "
                                 + ", ".join(f"{row['app']} ({_when(row['last_used'])})" for row in others[:10]) + ".",
                                 "fix": "Settings → Privacy & security → Location: switch off any that do not need it.",
                                 "action": "windows"})
            if own:
                findings.append({"level": "info", "what": "HAYAT's own reads show up as "
                                 + ", ".join(f"{row['app']} ({_when(row['last_used'])})" for row in own) + ".",
                                 "fix": "Nothing to do: that is HAYAT reading your location or Wi-Fi for you."})
        for site in [site for site in sites if site["allowed"]]:
            findings.append({"level": "watch", "what": f"{site['browser']} lets {site['site']} see your location.",
                             "fix": f"{site['browser']} → Settings → Site permissions → Location: remove {site['site']}.",
                             "action": site["browser"].lower()})
        if ip.get("checked"):
            distance = ip.get("km_from_you")
            where = ", ".join(str(part) for part in (ip.get("city"), ip.get("region"), ip.get("country")) if part)
            findings.append({"level": "watch" if distance is None or distance < 50 else "info",
                             "what": f"Any website sees your internet address and can place you near {where}"
                                     + (f" ({distance} km from you)" if distance is not None else "")
                                     + (f", through {str(ip['network']).rstrip('.')}" if ip.get("network") else "") + ".",
                             "fix": "Mask it: HAYAT through Tor, and your own browsing in HAYAT's masked browser (see Masks).",
                             "action": "masks"})
        if photos and photos["count"]:
            findings.append({"level": "watch", "what": f"{photos['count']} photos in your folders carry GPS tags that show "
                             "where they were taken.",
                             "fix": "Share stripped copies: say \"remove the location from <photo>\". HAYAT strips photos it "
                                    "sends by itself."})
        protections = [
            "Place names come from GeoNames on this computer; your coordinates are not sent anywhere to name the place."
            if self.places.available() else
            "Place names are not set up on this computer yet: say \"set up private place names\" (about 2 MB).",
            "Messages, posts and web requests holding your precise coordinates or postcode are held before they leave.",
            "Photos HAYAT sends have their GPS tags removed first.",
            "Positions sent to public lookups near you are rounded to about 11 km.",
            "Your latest location fix is kept in memory only, never written to disk.",
        ]
        return {"kind": "location_privacy", "findings": findings, "protections": protections,
                "windows": apps, "browser_sites": sites, "public_ip": ip, "photos": photos,
                "guard": self.shield.status(),
                "basis": "Read from this computer's Windows settings and browser profiles (nothing changed); the public "
                         "IP check asks one service what every website already sees. No location was sent."}


# ------------------------------------------------------------------ opening the page that fixes a finding

#: Where each finding is fixed. HAYAT opens the page; the owner flips the switch, so nothing is changed behind
#: their back and every change is theirs to undo.
SETTING_PAGES = {
    "windows": (None, "ms-settings:privacy-location"),
    "edge": ("msedge.exe", "edge://settings/content/location"),
    "chrome": ("chrome.exe", "chrome://settings/content/location"),
    "brave": ("brave.exe", "brave://settings/content/location"),
    "opera": ("opera.exe", "opera://settings/content/location"),
    "firefox": ("firefox.exe", "about:preferences#privacy"),
}


def _registered_program(exe: str) -> str | None:
    """A browser's path from Windows' App Paths registration (never guessed from a folder name)."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value = winreg.QueryValue(key, None)
        except OSError:
            continue
        if value and Path(value.strip('"')).is_file():
            return value.strip('"')
    return None


def open_setting(target: str, launch: Callable[[list[str] | str], Any] | None = None,
                 find: Callable[[str], str | None] = _registered_program) -> dict[str, Any]:
    target = str(target or "").lower()
    if target not in SETTING_PAGES:
        raise ValueError(f"No location setting is known for {target!r}; choose from {', '.join(SETTING_PAGES)}.")
    exe, page = SETTING_PAGES[target]
    if launch is None:
        import subprocess

        def launch(command):
            if isinstance(command, str):
                os.startfile(command)  # noqa: S606 -- a fixed ms-settings: page, never owner text
            else:
                subprocess.Popen(command)  # noqa: S603 -- a registered browser and a fixed settings page
    if exe is None:
        launch(page)
    else:
        program = find(exe)
        if not program:
            return {"opened": False, "target": target, "page": page,
                    "note": f"{exe} is not registered on this computer; open {page} in that browser yourself."}
        launch([program, page])
    return {"opened": True, "target": target, "page": page,
            "note": "Opened. Switch off what does not need your location there; HAYAT does not change it for you."}


# ------------------------------------------------------------------ requests and tools

def privacy_request(text: str) -> dict[str, Any] | None:
    original = " ".join(str(text or "").split()).strip(" ?!")
    value = original.lower().strip(" .")
    if re.fullmatch(r"(?:please\s+)?(?:set\s*up|install|download|get)\s+(?:the\s+)?(?:private|offline|local)\s+place\s*names?", value):
        return {"tool": "location_places_setup", "arguments": {}}
    opening = re.fullmatch(r"(?:please\s+)?open\s+(?:the\s+|my\s+)?(windows|edge|chrome|brave|opera|firefox)?\s*"
                           r"(?:'s\s+)?location\s+(?:settings?|permissions?|privacy)", value)
    if opening:
        return {"tool": "location_open_setting", "arguments": {"target": opening[1] or "windows"}}
    if re.fullmatch(r"(?:please\s+)?share\s+my\s+location\s+(?:just\s+)?(?:once|this\s+once|one\s+time)", value):
        return {"tool": "location_share_once", "arguments": {}}
    photo = re.fullmatch(r"(?i)(?:please\s+)?(?:remove|strip|delete|clear)\s+(?:the\s+)?(?:gps|location|geotags?)(?:\s+(?:data|tags?|info))?"
                         r"\s+(?:from|of|in)\s+[\"']?(.+?\.(?:jpe?g|tiff?|png|webp))[\"']?\.?", original)
    if photo:
        return {"tool": "photo_location_strip", "arguments": {"path": photo[1]}}
    if re.search(r"\b(?:who|which\s+apps?|what\s+apps?|anyone|anybody|someone|websites?|sites?)\b.{0,40}"
                 r"\b(?:see|track|know|detect|find|use|access|read)s?\b.{0,20}\bmy\s+location\b", value) \
            or re.search(r"\b(?:location\s+privacy|privacy\s+of\s+my\s+location|protect\s+my\s+location|hide\s+my\s+location|"
                         r"(?:can|could)\s+(?:anyone|someone|people|others)\s+(?:see|track|find|detect)\s+(?:me|my\s+location))\b", value):
        return {"tool": "location_privacy_check", "arguments": {}}
    return None


def direct_privacy_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = privacy_request(text)
    if not request or request["tool"] not in set(names):
        return None
    return [{"description": "Location privacy: " + request["tool"].replace("_", " ") + ".", "tool": request["tool"],
             "arguments": request["arguments"], "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_location_privacy_tools(registry: Any, privacy: LocationPrivacy) -> LocationPrivacy:
    registry.location_shield = privacy.shield

    def strip(path: str) -> dict[str, Any]:
        source = Path(path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(str(source))
        if not photo_gps(source):
            return {"stripped": False, "path": str(source), "note": "This photo has no GPS location in it."}
        clean = strip_gps(source)
        return {"stripped": True, "original": str(source), "path": str(clean),
                "note": "A copy without the location; the original is unchanged."}

    registry.register(
        "location_privacy_check",
        "Who can learn where you are: Windows apps allowed to use the location service and when each last did, websites "
        "your browsers allow, what any website can tell from your internet address, and photos carrying GPS tags; plus "
        "the protections HAYAT applies. Read-only.",
        {"type": "object", "properties": {"scan_photos": {"type": "boolean"}}, "additionalProperties": False},
        "low", "system_read", privacy.check)
    registry.register(
        "location_places_setup",
        "Install GeoNames place names (about 144,000 places, 2 MB download, hash-pinned) into data/places, so HAYAT names "
        "where you are on this computer instead of sending your coordinates to an online service.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "public_download",
        lambda: privacy.places.install())
    registry.register(
        "location_share_once",
        "Let the next message or post include your location, once (the location guard holds it otherwise). Needs your "
        "approval; the guard returns after one send or 5 minutes.",
        {"type": "object", "properties": {"minutes": {"type": "number", "minimum": 1, "maximum": 30}},
         "additionalProperties": False},
        "high", "send_message", lambda minutes=5: privacy.shield.allow_once(minutes))
    registry.register(
        "location_open_setting",
        "Open the page where a location permission is switched off: Windows' location settings (target windows) or "
        "a browser's site location permissions (edge, chrome, brave, opera, firefox). It only opens the page; the "
        "owner makes the change.",
        {"type": "object", "properties": {"target": {"type": "string", "enum": sorted(SETTING_PAGES)}},
         "required": ["target"], "additionalProperties": False},
        "low", "application_launch", lambda target: open_setting(target))
    registry.register(
        "photo_location_strip",
        "Make a copy of a photo without its GPS location tags (other details kept), in data/work/no-gps. The original "
        "is not changed.",
        {"type": "object", "properties": {"path": {"type": "string", "maxLength": 500}}, "required": ["path"],
         "additionalProperties": False},
        "low", "filesystem_write_project", strip)
    return privacy
