"""Publicly broadcast aviation: who is flying, where, and what kind of aircraft it is.

Everything here comes from ADS-B / Mode-S transponder messages that aircraft broadcast
in the clear and volunteers receive (adsb.lol, ODbL). That includes military aircraft
that choose to broadcast; aircraft that do not are simply absent, and HAYAT says so.
Nothing here infers an operation from tracking data -- changes are reported as changes.

Kinds (from public fields only): military (the receiver database's military flag),
helicopter (emitter category A7 or a rotorcraft type), cargo (cargo airline callsigns),
commercial (airline ICAO callsign: three letters and a number), private (registration
used as callsign, or a light/small emitter category), drone (category B6), unknown.
"""

from __future__ import annotations

import re
from typing import Any

from app.intelligence.live import distance_km

#: ICAO airline designators of large cargo operators.
CARGO = frozenset("FDX UPS GTI CLX BOX ABW CKS GEC DHK DHX BCS ATN ABX KYE CAO CKK SQC MPH NCA TAY AHK LCO ICV "
                  "AJT PAC WGN SWN RCH".split())
#: Rotorcraft type designators that do not always report category A7.
ROTORCRAFT = re.compile(r"^(?:H\d{2}|EC\d{2,3}|AS\d{2,3}|A1\d{2}|AW\d{3}|B\d{3}|S\d{2}|R\d{2}|BK17|UH\d{2}|CH\d{2}|AH\d{2}|MI\d{1,2}|NH90|EH10|V22)$")
CATEGORY_NAMES = {"A1": "light aircraft", "A2": "small aircraft", "A3": "large aircraft", "A4": "high-vortex large aircraft",
                  "A5": "heavy aircraft", "A6": "high-performance aircraft", "A7": "rotorcraft", "B1": "glider",
                  "B2": "lighter-than-air", "B3": "parachutist", "B4": "ultralight", "B6": "unmanned aerial vehicle",
                  "B7": "space vehicle", "C1": "surface emergency vehicle", "C2": "surface service vehicle"}
#: Squawk codes with an internationally fixed meaning.
SQUAWKS = {"7500": "unlawful interference (hijack)", "7600": "radio failure", "7700": "general emergency"}


def classify(plane: dict[str, Any]) -> str:
    flight = str(plane.get("flight") or "").strip().upper()
    category = str(plane.get("category") or "")
    aircraft_type = str(plane.get("t") or "").upper()
    if int(plane.get("dbFlags") or 0) & 1:
        return "military"
    if category == "B6":
        return "drone"
    if category == "A7" or ROTORCRAFT.match(aircraft_type):
        return "helicopter"
    airline = re.match(r"^([A-Z]{3})\d", flight)
    if airline and airline[1] in CARGO:
        return "cargo"
    if airline:
        return "commercial"
    registration = str(plane.get("r") or "").replace("-", "").upper()
    if (registration and flight.replace("-", "") == registration) or category in ("A1", "A2", "B1", "B4"):
        return "private"
    return "unknown"


def normalize(plane: dict[str, Any], origin: tuple[float, float] | None = None) -> dict[str, Any] | None:
    """One aircraft in HAYAT's common shape, or None without a position."""
    if plane.get("lat") is None or plane.get("lon") is None:
        return None
    altitude = plane.get("alt_baro")
    squawk = str(plane.get("squawk") or "")
    item = {
        "id": plane.get("hex"), "hex": plane.get("hex"),
        "flight": str(plane.get("flight") or "").strip() or plane.get("r") or plane.get("hex"),
        "callsign": str(plane.get("flight") or "").strip(), "registration": plane.get("r"), "type": plane.get("t"),
        "description": plane.get("desc"), "operator": plane.get("ownOp"), "kind": classify(plane),
        "category": CATEGORY_NAMES.get(str(plane.get("category") or ""), plane.get("category")),
        "latitude": plane["lat"], "longitude": plane["lon"],
        "altitude_ft": altitude if isinstance(altitude, (int, float)) else 0, "on_ground": altitude == "ground",
        "speed_kt": plane.get("gs"), "track": plane.get("track") if isinstance(plane.get("track"), (int, float)) else None,
        "vertical_rate_fpm": plane.get("baro_rate"), "squawk": squawk or None,
        "squawk_meaning": SQUAWKS.get(squawk),
        "emergency": plane.get("emergency") not in (None, "none") or squawk in SQUAWKS,
        "seen_seconds_ago": plane.get("seen_pos"),
    }
    if origin is not None:
        item["distance_km"] = round(distance_km(origin[0], origin[1], plane["lat"], plane["lon"]), 1)
    return item


class Aviation:
    """Worldwide public aviation on top of LiveData's rate limits and cache."""

    def __init__(self, live: Any) -> None:
        self.live = live

    def _aircraft(self, provider: str, url: str, origin: tuple[float, float] | None = None) -> list[dict[str, Any]]:
        data = self.live._fetch(provider, url)
        found = [item for item in (normalize(plane, origin) for plane in (data or {}).get("ac") or []) if item]
        self.live.record("aircraft", found, "adsb.lol")
        return found

    def military(self) -> list[dict[str, Any]]:
        """Every military aircraft broadcasting ADS-B right now, worldwide (those that do not are absent)."""
        return self._aircraft("adsb_global", "https://api.adsb.lol/v2/mil")

    def emergencies(self) -> list[dict[str, Any]]:
        """Aircraft worldwide squawking 7500, 7600 or 7700."""
        found = []
        for code in SQUAWKS:
            found += self._aircraft("adsb_global", f"https://api.adsb.lol/v2/sqk/{code}")
        return found

    def lookup(self, value: str) -> list[dict[str, Any]]:
        """Aircraft by callsign, registration or ICAO 24-bit hex address."""
        text = re.sub(r"[^A-Za-z0-9-]", "", str(value or "")).upper()
        if not 2 <= len(text) <= 10:
            raise ValueError("Give a callsign, registration or ICAO hex address.")
        if re.fullmatch(r"[0-9A-F]{6}", text):
            found = self._aircraft("adsb_global", f"https://api.adsb.lol/v2/hex/{text.lower()}")
            if found:
                return found
        if "-" in text or re.fullmatch(r"N\d{1,5}[A-Z]{0,2}", text):
            found = self._aircraft("adsb_global", f"https://api.adsb.lol/v2/reg/{text}")
            if found:
                return found
        return self._aircraft("adsb_global", f"https://api.adsb.lol/v2/callsign/{text}")

    def route(self, callsign: str) -> dict[str, Any] | None:
        """Origin and destination filed for a callsign (adsb.lol route database; not every flight has one)."""
        text = re.sub(r"[^A-Za-z0-9]", "", str(callsign or "")).upper()
        if not text:
            return None
        data = self.live._fetch("adsb_route", f"https://api.adsb.lol/api/0/route/{text}")
        airports = (data or {}).get("_airports") or []
        if not airports:
            return None
        return {"callsign": text, "airports": [{"icao": item.get("icao"), "iata": item.get("iata"), "name": item.get("name"),
                                                "city": item.get("location"), "country": item.get("countryiso2"),
                                                "latitude": item.get("lat"), "longitude": item.get("lon")} for item in airports],
                "basis": "Route database of adsb.lol: the usual route for this callsign, not a filed flight plan."}

    def summary(self, aircraft: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in aircraft:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        return counts
