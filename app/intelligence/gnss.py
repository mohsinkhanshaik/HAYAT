"""Where aircraft report poor satellite navigation: a public proxy for GPS jamming or spoofing.

Ported 2026-11-06 from the GNSS Integrity layer of github.com/bilawalsidhu/gods-eye-view (MIT),
keeping its method and its caveats:

* Aircraft broadcasting ADS-B (DO-260 versions 1 and 2) report how far they trust their own
  position: NIC (integrity containment) and NACp (position accuracy). An aircraft counts as
  degraded when NACp < 8, NIC < 7 (except NIC 0 with NACp >= 10), or readsb's `gpsOkBefore`
  says it lost GPS. The thresholds are the US 14 CFR 91.227(c) ADS-B Out minima, which are not
  a published interference classifier; nobody has validated this rule against known events.
* Aircraft are binned in 0.5° cells. A cell's level uses gpsjam.org's published formula,
  100 × max(0, degraded − 1) / aircraft: under 2 % low, 2–10 % medium, over 10 % high.
  Cells with fewer than 3 aircraft are not shown.
* Each aircraft counts once per cell over a rolling 30 minutes of the places HAYAT has looked at.

What the data shows is low navigation accuracy reported by aircraft. Interference is one
possible cause; avionics faults and old transponders report low values too.
"""
from __future__ import annotations

import math
import re
import threading
import time
from typing import Any, Callable

NIC_THRESHOLD, NACP_THRESHOLD = 7, 8
CELL_DEG = 0.5
MIN_AIRCRAFT = 3
WINDOW_SECONDS = 30 * 60
MEDIUM_PERCENT, HIGH_PERCENT = 2.0, 10.0
RADIUS_NM = 250
MAX_POSITION_AGE = 60
#: Aircraft whose position source can be MLAT after GPS loss; the quality fields must still be aircraft-reported.
QUALIFYING_TYPES = {"adsb_icao", "mlat"}
QUALIFYING_VERSIONS = {1, 2}
HEX = re.compile(r"~?[0-9a-f]{6}")
RULE = (f"gpsOkBefore set, or NACp < {NACP_THRESHOLD}, or NIC < {NIC_THRESHOLD} (except NIC 0 with NACp >= 10); "
        "cells use gpsjam.org's formula 100 × max(0, degraded − 1) / aircraft")
CAVEAT = ("Low navigation accuracy reported by aircraft. Jamming or spoofing is one possible cause, not a confirmed one: "
          "avionics faults and old transponders report low values too. The per-aircraft rule is unvalidated.")
#: Airspace sampled for a worldwide look, one 250 NM read each (never the whole sky): four regions gpsjam.org has
#: often shown, and Central Europe as a usually quiet comparison. Five reads fit GpsInterference.PER_MINUTE.
WORLD_SAMPLE = (("Baltic Sea", 56, 21), ("Eastern Mediterranean", 34, 34), ("Black Sea", 43, 34), ("Persian Gulf", 27, 52),
                ("Central Europe", 50, 10))


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def classify(payload: Any) -> list[dict[str, Any]] | None:
    """An adsb.lol / readsb snapshot → the integrity facts per aircraft, or None when it holds no aircraft list."""
    if not isinstance(payload, dict) or not isinstance(payload.get("ac"), list):
        return None
    rows, seen = [], set()
    for aircraft in payload["ac"]:
        if not isinstance(aircraft, dict):
            continue
        hex_id = aircraft.get("hex") if isinstance(aircraft.get("hex"), str) else ""
        if not HEX.fullmatch(hex_id) or hex_id in seen:
            continue
        if aircraft.get("type") not in QUALIFYING_TYPES or aircraft.get("version") not in QUALIFYING_VERSIONS:
            continue
        if not finite(aircraft.get("alt_baro")):  # "ground" or missing: taxiing aircraft say nothing about the sky
            continue
        derived = set(aircraft.get("tisb") or []) | set(aircraft.get("mlat") or [])
        nic = aircraft["nic"] if finite(aircraft.get("nic")) and "nic" not in derived else None
        nacp = aircraft["nac_p"] if finite(aircraft.get("nac_p")) and "nac_p" not in derived else None
        latitude, longitude = aircraft.get("lat"), aircraft.get("lon")
        if not finite(latitude) or abs(latitude) > 90 or not finite(longitude) or abs(longitude) > 180:
            continue
        age = aircraft.get("seen_pos")
        if not finite(age) or age < 0 or age > MAX_POSITION_AGE:
            continue
        lost = finite(aircraft.get("gpsOkBefore")) and aircraft["gpsOkBefore"] > 0
        if nic is None and nacp is None and not lost:
            continue
        if lost and finite(aircraft.get("gpsOkLat")) and abs(aircraft["gpsOkLat"]) <= 90 \
                and finite(aircraft.get("gpsOkLon")) and abs(aircraft["gpsOkLon"]) <= 180:
            latitude, longitude = aircraft["gpsOkLat"], aircraft["gpsOkLon"]  # the evidence sits where GPS was last good
        seen.add(hex_id)
        rows.append({"hex": hex_id, "latitude": float(latitude), "longitude": float(longitude), "nic": nic, "nacp": nacp,
                     "gps_lost": lost,
                     "degraded": lost or (nacp is not None and nacp < NACP_THRESHOLD)
                     or (nic is not None and nic < NIC_THRESHOLD and not (nic == 0 and nacp is not None and nacp >= 10))})
    return rows


def cell_key(latitude: float, longitude: float) -> tuple[int, int]:
    return (math.floor((min(latitude, 89.999999) + 90) / CELL_DEG), math.floor((min(longitude, 179.999999) + 180) / CELL_DEG))


def level(aircraft: int, degraded: int) -> tuple[float, str]:
    percent = 100 * max(0, degraded - 1) / aircraft if aircraft else 0.0
    return percent, "high" if percent > HIGH_PERCENT else "medium" if percent >= MEDIUM_PERCENT else "low"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


class Observations:
    """Each aircraft once per cell over the window; a degraded report keeps it degraded there for one window."""

    def __init__(self) -> None:
        self.entries: dict[tuple[str, tuple[int, int]], dict[str, Any]] = {}

    def add(self, rows: list[dict[str, Any]], observed_at: float, now: float) -> None:
        observed_at = min(observed_at, now)  # a cached snapshot never makes its evidence younger
        if now - observed_at <= WINDOW_SECONDS:
            for row in rows:
                cell = cell_key(row["latitude"], row["longitude"])
                previous = self.entries.get((row["hex"], cell)) or {}
                degraded_at = previous.get("degraded_at")
                self.entries[(row["hex"], cell)] = {
                    "cell": cell, "seen_at": max(previous.get("seen_at", observed_at), observed_at),
                    "degraded_at": max(degraded_at or observed_at, observed_at) if row["degraded"] else degraded_at}
        self.prune(now)

    def prune(self, now: float) -> None:
        for key, entry in list(self.entries.items()):
            if now - entry["seen_at"] > WINDOW_SECONDS:
                del self.entries[key]
            elif entry["degraded_at"] is not None and now - entry["degraded_at"] > WINDOW_SECONDS:
                entry["degraded_at"] = None

    def area(self, latitude: float, longitude: float, radius_km: float) -> dict[str, Any]:
        """All aircraft in the window whose cell centre lies within the radius, each once: coarser than cells, but it
        still says something at night, when traffic is too thin for any cell to reach MIN_AIRCRAFT."""
        aircraft: dict[str, bool] = {}
        for (hex_id, (row, column)), entry in self.entries.items():
            centre = (row * CELL_DEG - 90 + CELL_DEG / 2, column * CELL_DEG - 180 + CELL_DEG / 2)
            if distance_km(latitude, longitude, *centre) <= radius_km:
                aircraft[hex_id] = aircraft.get(hex_id, False) or entry["degraded_at"] is not None
        degraded = sum(aircraft.values())
        percent, band = level(len(aircraft), degraded)
        return {"aircraft": len(aircraft), "degraded": degraded, "percent": round(percent, 1),
                "level": band if len(aircraft) >= MIN_AIRCRAFT else None}

    def cells(self) -> list[dict[str, Any]]:
        tallies: dict[tuple[int, int], list[int]] = {}
        for entry in self.entries.values():
            tally = tallies.setdefault(entry["cell"], [0, 0])
            tally[0] += 1
            tally[1] += entry["degraded_at"] is not None
        found = []
        for (row, column), (aircraft, degraded) in sorted(tallies.items()):
            if aircraft < MIN_AIRCRAFT:
                continue
            percent, band = level(aircraft, degraded)
            south, west = row * CELL_DEG - 90, column * CELL_DEG - 180
            found.append({"south": south, "west": west, "north": south + CELL_DEG, "east": west + CELL_DEG,
                          "latitude": south + CELL_DEG / 2, "longitude": west + CELL_DEG / 2, "aircraft": aircraft,
                          "degraded": degraded, "percent": round(percent, 1), "level": band})
        return found


class RateLimited(RuntimeError):
    pass


def retry_after(exc: Exception) -> float | None:
    """Seconds a 429 asks HAYAT to wait (Retry-After, bounded to 5 s–1 h), or None when the error is not a 429."""
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 429:
        return None
    raw = str((getattr(response, "headers", None) or {}).get("retry-after") or "").strip()
    return max(5.0, min(3600.0, float(raw))) if re.fullmatch(r"\d+(?:\.\d+)?", raw) else 60.0


class GpsInterference:
    #: 250 NM reads are heavy for a volunteer-run service: at most this many a minute, and none during a cooldown.
    PER_MINUTE = 5

    def __init__(self, get_json: Callable[[str], Any], resolve: Callable[[str], dict[str, Any]],
                 clock: Callable[[], float] = time.time) -> None:
        self.get_json, self.resolve, self.clock = get_json, resolve, clock
        self.observations = Observations()
        self._read: dict[tuple[int, int], float] = {}
        self._calls: list[float] = []
        self._cooldown_until = 0.0
        self._lock = threading.Lock()

    def scan(self, latitude: float, longitude: float) -> int:
        """Read the 250 NM around a whole-degree anchor (once a minute per anchor); returns aircraft classified."""
        anchor = (round(latitude), round(longitude))
        now = self.clock()
        with self._lock:
            if now - self._read.get(anchor, -1e9) < 60:
                return 0
            if now < self._cooldown_until:
                raise RateLimited(f"adsb.lol asked HAYAT to wait; next read in {self._cooldown_until - now:.0f} s.")
            self._calls = [stamp for stamp in self._calls if now - stamp < 60]
            if len(self._calls) >= self.PER_MINUTE:
                raise RateLimited(f"HAYAT's limit of {self.PER_MINUTE} adsb.lol area reads a minute is reached.")
            self._calls.append(now)
            self._read[anchor] = now
        try:
            payload = self.get_json(f"https://api.adsb.lol/v2/lat/{anchor[0]}/lon/{anchor[1]}/dist/{RADIUS_NM}")
        except Exception as exc:
            with self._lock:
                self._read.pop(anchor, None)  # a failed read must not block the next try for a minute
                if (wait := retry_after(exc)) is not None:
                    self._cooldown_until = max(self._cooldown_until, now + wait)
            raise
        rows = classify(payload)
        if rows is None:
            raise ValueError("adsb.lol returned no aircraft list.")
        stamp = payload.get("now")
        observed = (stamp / 1000 if stamp > 1e10 else stamp) if finite(stamp) and stamp > 0 else now
        if now - observed > WINDOW_SECONDS:
            raise ValueError("adsb.lol returned a snapshot older than 30 minutes.")
        with self._lock:
            self.observations.add(rows, observed, now)
        return len(rows)

    def around(self, place: str = "here", radius_km: float = 300) -> dict[str, Any]:
        if (place or "").strip().lower() in ("world", "the world", "worldwide", "everywhere", "global"):
            return self.world()
        found = self.resolve(place or "here")
        return self.at(float(found["latitude"]), float(found["longitude"]), radius_km, found.get("name") or place)

    def at(self, latitude: float, longitude: float, radius_km: float = 300, name: str = "") -> dict[str, Any]:
        """The same answer for a map position (the atlas's GPS interference layer)."""
        radius = max(50.0, min(float(radius_km), 460.0))
        try:
            classified, unavailable = self.scan(latitude, longitude), None
        except RateLimited as exc:  # answer from the window if it already holds this area
            classified, unavailable = 0, str(exc)
            if not self.observations.area(latitude, longitude, radius)["aircraft"]:
                raise
        cells = self.grid(latitude, longitude, radius)
        with self._lock:
            area = self.observations.area(latitude, longitude, radius)
        anomalies = sorted((cell for cell in cells if cell["level"] != "low"), key=lambda cell: (-cell["percent"], cell["distance_km"]))
        overhead = next((cell for cell in cells if cell["south"] <= latitude < cell["north"] and cell["west"] <= longitude < cell["east"]), None)
        aircraft = sum(cell["aircraft"] for cell in cells)
        return {"intelligence": True, "explore": "gnss", "place": name or f"{latitude:.2f}, {longitude:.2f}", "latitude": latitude,
                "longitude": longitude, "radius_km": radius, "cells": len(cells), "aircraft": aircraft,
                "classified_now": classified, "anomalies": anomalies[:12], "here": overhead, "area": area,
                "not_refreshed": unavailable,
                "high": sum(cell["level"] == "high" for cell in cells), "medium": sum(cell["level"] == "medium" for cell in cells),
                "rule": RULE, "caveat": CAVEAT, "source": "adsb.lol aircraft (ODbL), HAYAT's 30-minute window",
                "basis": "Cells of 0.5° with at least 3 aircraft. " + CAVEAT}

    def grid(self, latitude: float, longitude: float, radius_km: float) -> list[dict[str, Any]]:
        """Every cell of the window within the radius (low ones too), each with its distance: what the atlas draws."""
        with self._lock:
            self.observations.prune(self.clock())
            cells = self.observations.cells()
        found = []
        for cell in cells:
            distance = distance_km(latitude, longitude, cell["latitude"], cell["longitude"])
            if distance <= radius_km:
                found.append({**cell, "distance_km": round(distance)})
        return found

    def world(self) -> dict[str, Any]:
        regions, failed = [], {}
        for name, latitude, longitude in WORLD_SAMPLE:
            try:
                self.scan(latitude, longitude)
            except Exception as exc:  # one region failing leaves the others
                failed[name] = str(exc)[:120] or type(exc).__name__
        with self._lock:
            self.observations.prune(self.clock())
            cells = self.observations.cells()
            areas = {name: self.observations.area(latitude, longitude, 460) for name, latitude, longitude in WORLD_SAMPLE}
        for name, latitude, longitude in WORLD_SAMPLE:
            near = [cell for cell in cells if distance_km(latitude, longitude, cell["latitude"], cell["longitude"]) <= 460]
            if near or areas[name]["aircraft"]:
                regions.append({"region": name, "cells": len(near), "area": areas[name],
                                "medium": sum(cell["level"] == "medium" for cell in near),
                                "high": sum(cell["level"] == "high" for cell in near),
                                "worst": max(near, key=lambda cell: cell["percent"]) if near else None})
        regions.sort(key=lambda region: (-region["high"], -region["medium"], -region["area"]["percent"]))
        return {"intelligence": True, "explore": "gnss_world", "regions": regions, "unavailable": failed,
                "sampled": [name for name, _lat, _lon in WORLD_SAMPLE], "rule": RULE, "caveat": CAVEAT,
                "source": "adsb.lol aircraft (ODbL), HAYAT's 30-minute window",
                "basis": f"A sample of {len(WORLD_SAMPLE)} regions, 250 NM each, not the whole sky. " + CAVEAT}


def register_gnss_tools(registry: Any, gnss: GpsInterference) -> None:
    registry.register(
        "gps_interference",
        "Where aircraft near a place report poor satellite-navigation accuracy (a public proxy for GPS jamming or "
        "spoofing), in 0.5° cells with gpsjam.org's levels; place 'world' samples 5 regions. A possible cause, never a confirmed one.",
        {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                          "radius_km": {"type": "number", "minimum": 50, "maximum": 460}},
         "required": [], "additionalProperties": False},
        "low", "public_web_read", gnss.around)


def report_lines(value: dict[str, Any]) -> list[str]:
    if value["explore"] == "gnss_world":
        lines = [f"Satellite-navigation accuracy reported by aircraft in {len(value['sampled'])} sampled regions (last 30 minutes):"]
        for region in value["regions"]:
            area, worst = region["area"], region["worst"]
            lines.append(f"• {region['region']}: {area['degraded']} of {area['aircraft']} aircraft degraded"
                         + (f" ({area['percent']:g}%, {area['level']})" if area.get("level") else "")
                         + (f"; {region['high']} high and {region['medium']} medium of {region['cells']} cells, worst "
                            f"{worst['percent']:g}% near {worst['latitude']:.1f}, {worst['longitude']:.1f}" if worst else
                            "; no cell has the 3 aircraft needed for a cell level"))
        if value.get("unavailable"):
            lines.append("Not read: " + ", ".join(value["unavailable"]) + ".")
        lines.append(value["caveat"])
        return lines
    if not (value.get("area") or {}).get("aircraft") and not value["cells"]:
        return [f"No aircraft reporting navigation quality were seen within {value['radius_km']:g} km of {value['place']} in the "
                "last 30 minutes, so there is nothing to judge yet (traffic is often thin at night, and ADS-B coverage varies)."
                + (f" Not refreshed just now: {value['not_refreshed']}." if value.get("not_refreshed") else ""), value["caveat"]]
    lines = [f"Satellite-navigation accuracy reported by {value['aircraft']} aircraft within {value['radius_km']:g} km of "
             f"{value['place']} ({value['cells']} cells of 0.5°, last 30 minutes): {value['high']} high, {value['medium']} medium."]
    here = value.get("here")
    if here:
        lines.append(f"Overhead: {here['level']} ({here['degraded']} of {here['aircraft']} aircraft degraded, {here['percent']:g}%).")
    for cell in value["anomalies"][:8]:
        lines.append(f"• {cell['level']}: {cell['percent']:g}% of {cell['aircraft']} aircraft degraded around "
                     f"{cell['latitude']:.2f}, {cell['longitude']:.2f} ({cell['distance_km']} km away)")
    if value.get("not_refreshed"):
        lines.append(f"Not refreshed just now ({value['not_refreshed']}); this is HAYAT's window from the last 30 minutes.")
    area = value.get("area") or {}
    if area.get("aircraft"):
        lines.append(f"Across the whole area: {area['degraded']} of {area['aircraft']} aircraft report degraded navigation"
                     + (f" ({area['percent']:g}% by the same formula: {area['level']}; coarser than cells)." if area.get("level")
                        else " (too few to give a level)."))
    if not value["anomalies"]:
        lines.append("No cell shows an anomaly." if value["cells"] else
                     "No 0.5° cell has the 3 aircraft needed for a cell level (traffic is thin, often at night); asking again "
                     "over the next 30 minutes adds aircraft to the window.")
    lines.append(value["caveat"])
    return lines
