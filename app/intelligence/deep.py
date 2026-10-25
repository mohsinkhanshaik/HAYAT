"""A deep look at one place: every public source HAYAT has, gathered at once and reasoned over.

`intelligence_around` (fusion.py) gathers; this module also reads fires, traffic on the
nearest cameras, the sky and launches, then turns the readings into findings. Each finding
names the measurements behind it and how sure it is, and the blind spots are listed as
findings too: what HAYAT could not see is part of the answer. The rules are written out
below, not learned, so every conclusion can be checked against its evidence. A local-model
reading is added only on request and is labelled as inference.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from app.intelligence.explore import bearing_deg, compass, distance_km
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
PRIORITY = {"alert": 0, "watch": 1, "info": 2, "gap": 3}
#: US EPA 24-hour PM2.5 breakpoints (µg/m³); citizen sensors are uncalibrated, so these are indications.
PM25_LEVELS = ((55.5, "unhealthy"), (35.5, "unhealthy for sensitive groups"), (12.1, "moderate"))


def angle_between(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


def finding(level: str, title: str, detail: str, evidence: list[str], confidence: str,
            target: dict[str, Any] | None = None) -> dict[str, Any]:
    """`target` is what the finding is about, for the windows to open: an event, a camera, or a point on the map."""
    value = {"level": level, "title": title, "detail": detail, "evidence": list(dict.fromkeys(evidence)), "confidence": confidence}
    if target:
        value["target"] = target
    return value


HAZARDS = {"earthquakes": "earthquake", "volcanoes": "volcano report", "wildfires": "wildfire incident", "severe_weather": "weather warning",
           "floods": "flood report", "emergency": "emergency report", "conflict": "conflict report", "attack": "attack report"}
#: A fire worth naming on its own: more than one detection, or real heat. Single small detections are often
#: industry, gas flares or field burning, and are summed up in one line instead.
SIGNIFICANT_FIRE = {"detections": 2, "power_mw": 10.0}


class DeepLook:
    def __init__(self, manager: Any, explore: Any) -> None:
        self.manager, self.explore = manager, explore

    # ----------------------------------------------------------------- gathering
    def gather(self, place: str, radius_km: float, traffic_cameras: int, latitude: Any = None, longitude: Any = None) -> dict[str, Any]:
        fusion = getattr(self.manager, "fusion", None)
        if fusion is None:
            raise ValueError("Place fusion is not available in this session.")
        if latitude is not None and longitude is not None:
            # A point on the map: fusion names it by reverse geocoding ("near Denver, Colorado").
            around = fusion.around(place, latitude=latitude, longitude=longitude, radius_km=radius_km)
        else:
            around = fusion.around(place or "here", radius_km=radius_km)
        where = around["place"]
        name = f"{where['latitude']:.4f}, {where['longitude']:.4f}"  # resolved once: no second geocoding
        jobs = {
            "fires": lambda: self.explore.fire_clusters(name, radius_km=max(150.0, radius_km), limit=10),
            "sky": lambda: self.explore.satellite_pass(name, "ISS", days=2),
            "launches": lambda: self.explore.launches("upcoming", limit=40),
        }
        cameras = [camera for camera in around.get("cameras") or [] if camera.get("image")][:max(0, min(traffic_cameras, 5))]
        for index, camera in enumerate(cameras):
            jobs[f"traffic:{index}"] = lambda camera=camera: self.explore.traffic_count(camera["id"])
        found: dict[str, Any] = {}
        gaps: dict[str, str] = dict(around.get("unavailable") or {})
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {key: pool.submit(job) for key, job in jobs.items()}
            for key, future in futures.items():
                try:
                    found[key] = future.result(timeout=120)
                except Exception as exc:  # each source may fail alone; the gap is reported, not hidden
                    label = f"traffic on camera {int(key.split(':')[1]) + 1}" if key.startswith("traffic:") else key
                    gaps[label] = str(exc)[:160] or type(exc).__name__
                    log_swallowed(_log, f"deep look: {key}")
        found["traffic"] = [found.pop(key) for key in list(found) if key.startswith("traffic:")]
        return {"around": around, **found, "gaps": gaps}

    # ----------------------------------------------------------------- reasoning
    @staticmethod
    def reason(gathered: dict[str, Any], radius_km: float) -> list[dict[str, Any]]:
        around = gathered["around"]
        where = around["place"]
        lat, lon = where["latitude"], where["longitude"]
        weather = around.get("weather") or {}
        findings: list[dict[str, Any]] = []

        # Hazards reported by publishers, one finding per kind; roads have their own.
        events = around.get("events") or []
        for category, noun in HAZARDS.items():
            reported = [item for item in events if item.get("category") == category]
            if not reported:
                continue
            nearest = min(reported, key=lambda item: item["distance_km"])
            level = ("alert" if any(item.get("severity") == "high" for item in reported)
                     else "watch" if any(item.get("severity") == "medium" for item in reported) or nearest["distance_km"] <= radius_km / 2
                     else "info")
            findings.append(finding(level, f"{len(reported)} {noun}{'s' if len(reported) > 1 else ''} within {radius_km:g} km",
                                    f"Nearest: {nearest['title']}, {nearest['distance_km']} km {nearest['direction']}.",
                                    [f"{item['title']} ({item.get('provider') or category}"
                                     + (f", {item['severity']}" if item.get("severity") else "") + f", {item['distance_km']} km)"
                                     for item in reported[:5]], "high",
                                    {"kind": "event", "id": nearest["id"]} if nearest.get("id") else None))
        roads = [item for item in events if item.get("category") == "traffic" or item.get("kind") in ("closure", "lane closure")]
        if roads:
            kinds = {}
            for item in roads:
                kinds[item.get("kind") or "report"] = kinds.get(item.get("kind") or "report", 0) + 1
            closed = sum(count for kind, count in kinds.items() if "closure" in kind)
            findings.append(finding("watch" if closed else "info", f"{len(roads)} road report{'s' if len(roads) > 1 else ''} nearby",
                                    ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items(), key=lambda pair: -pair[1]))
                                    + ". They change travel times; check before driving.",
                                    [f"{item['title']} ({item['distance_km']} km)" for item in roads[:6]], "high",
                                    {"kind": "event", "id": roads[0]["id"]} if roads[0].get("id") else None))

        # Fires, with the wind: is the place downwind of a fire?
        fires = (gathered.get("fires") or {}).get("fires") or []
        wind_from, wind = weather.get("wind_from_deg"), weather.get("wind_speed_ms")
        significant = [fire for fire in fires if fire.get("distance_km") is not None and fire["distance_km"] <= max(150.0, radius_km)
                       and (fire["detections"] >= SIGNIFICANT_FIRE["detections"] or fire["power_mw"] >= SIGNIFICANT_FIRE["power_mw"])]
        small = [fire for fire in fires if fire not in significant and fire.get("distance_km") is not None
                 and fire["distance_km"] <= max(150.0, radius_km)]
        if small:
            findings.append(finding("info", f"{len(small)} small heat detection{'s' if len(small) > 1 else ''} within "
                                    f"{max(150.0, radius_km):g} km", "Single detections under 10 MW: often industry, gas flares or "
                                    "field burning rather than wildfire.",
                                    [f"{fire['power_mw']:.0f} MW, {fire['distance_km']} km {fire['direction']}, {fire['latest']}"
                                     for fire in small[:3]], "low"))
        for fire in significant[:3]:
            distance = fire["distance_km"]
            level = "alert" if distance <= 50 and fire["change"] in ("new", "growing") else "watch" if distance <= 100 else "info"
            evidence = [f"{fire['detections']} satellite detections, {fire['power_mw']:.0f} MW, over {fire['extent_km']:g} km, "
                        f"latest {fire['latest']}", f"change since the previous download: {fire['change']}"]
            detail = f"{distance} km {fire['direction']} of {where['name']}."
            if wind_from is not None and wind and wind >= 2:
                toward = (wind_from + 180) % 360
                if angle_between(toward, bearing_deg(fire["latitude"], fire["longitude"], lat, lon)) <= 35:
                    detail += f" The wind ({wind:.0f} m/s from the {compass(wind_from)}) blows from the fire toward here: smoke is possible."
                    evidence.append(f"wind from {wind_from:.0f}° at {wind:.1f} m/s (forecast model)")
                    # Smoke reaching here is possible, not shown: an alert only when the fire is close.
                    level = "alert" if distance <= 30 else "watch"
            confidence = "medium" if fire.get("confident_share", 0) >= .5 else "low"
            findings.append(finding(level, f"{'Growing' if fire['change'] == 'growing' else 'New' if fire['change'] == 'new' else 'Active'}"
                                    f" fire {distance} km away", detail, evidence, confidence,
                                    {"kind": "point", "latitude": fire["latitude"], "longitude": fire["longitude"], "zoom": 9}))

        # Weather in the next hours (a forecast model, not a measurement).
        rain = weather.get("precipitation_next_hour_mm")
        if rain is not None and rain >= .5:
            findings.append(finding("watch" if rain >= 4 else "info", "Rain expected within the hour",
                                    f"{rain:g} mm forecast for the next hour.", [weather.get("source", "forecast")], "medium"))
        if wind is not None and wind >= 10:
            findings.append(finding("watch" if wind >= 17 else "info", "Strong wind",
                                    f"{wind:.0f} m/s ({wind * 3.6:.0f} km/h) from the {compass(wind_from or 0)}.",
                                    [weather.get("source", "forecast")], "medium"))
        temperature = weather.get("temperature_c")
        if temperature is not None and (temperature >= 38 or temperature <= -15):
            findings.append(finding("watch", "Extreme temperature", f"{temperature:.0f} °C now (model).",
                                    [weather.get("source", "forecast")], "medium"))
        air = around.get("air") or {}
        pm = air.get("pm2_5_median")
        if pm is not None:
            label = next((name for limit, name in PM25_LEVELS if pm >= limit), None)
            if label:
                findings.append(finding("watch" if pm >= 35.5 else "info", f"Air quality: {label}",
                                        f"Median PM2.5 {pm:.0f} µg/m³ from nearby sensors.",
                                        [f"{air.get('sensors', '?')} citizen sensors (uncalibrated)"], "low"))
                if fires and any((fire.get("distance_km") or 1e9) <= 150 for fire in fires):
                    findings[-1]["detail"] += " A fire within 150 km may be contributing (possible, not shown)."

        # Change against this place's own history.
        for change in around.get("changes") or []:
            if change.get("unusual"):
                findings.append(finding("watch", f"Unusual {change['label']}",
                                        f"Now {change['now']}{change['unit']}"
                                        + (f"; usually {change['usual']}{change['unit']} at this hour" if change.get("usual") is not None else ""),
                                        ["HAYAT's earlier observations of this place"], "medium"))

        # Traffic on the nearest cameras.
        for count in gathered.get("traffic") or []:
            history = count.get("history") or {}
            if history.get("compared") in ("busier than usual", "quieter than usual"):
                findings.append(finding("watch" if history["compared"] == "busier than usual" else "info",
                                        f"Traffic {history['compared']} at {count['title']}",
                                        f"{count['vehicles']} vehicles now; usually about {history['usual']:g} at this hour.",
                                        [f"vehicle count on the camera's current still ({count['fetched_at']})",
                                         f"{history['baseline_samples']} earlier counts"], "medium",
                                        {"kind": "camera", "id": count["event_id"]} if count.get("event_id") else None))
            elif count.get("level") == "busy":
                findings.append(finding("info", f"Busy traffic at {count['title']}", f"{count['vehicles']} vehicles in the current still.",
                                        ["vehicle count on the camera's current still"], "medium",
                                        {"kind": "camera", "id": count["event_id"]} if count.get("event_id") else None))
        counted = gathered.get("traffic") or []
        if counted and not any("raffic" in item["title"] for item in findings):
            learning = min((count.get("history") or {}).get("needed") or 0 for count in counted)
            lit = sorted({count["level"] for count in counted if not count.get("dark")})
            findings.append(finding("info", f"Traffic on the {len(counted)} nearest camera{'s' if len(counted) > 1 else ''}: "
                                    + (", ".join(lit) if lit else "counts are lower bounds in the dark"),
                                    "; ".join(f"{count['title']}: {count['vehicles']}" for count in counted)
                                    + (" (dark there: headlights-only vehicles are missed, so likely more)"
                                       if any(count.get("dark") for count in counted) else "")
                                    + (f". Each count is kept; after {learning} more near this hour HAYAT can say whether this is "
                                       "usual." if learning else "."),
                                    ["vehicle counts on the cameras' current stills (nothing identified)"],
                                    "low" if any(count.get("dark") for count in counted) else "medium",
                                    {"kind": "camera", "id": counted[0]["event_id"]} if counted[0].get("event_id") else None))

        # The sky: things worth stepping outside for.
        sky = gathered.get("sky") or {}
        visible = sky.get("next_visible")
        if visible:
            start = datetime.fromisoformat(visible["rise"])
            if (start - datetime.now(UTC)).total_seconds() < 36 * 3600:
                findings.append(finding("info", "The ISS is visible soon",
                                        f"{start:%d %b %H:%M} UTC, up to {visible['peak_elevation']}° high, from the {visible['from']} "
                                        f"to the {visible['to']}, for about {visible['visible_minutes']} min (clouds permitting).",
                                        ["CelesTrak elements, SGP4"], "high"))
        for launch in (gathered.get("launches") or {}).get("launches") or []:
            if launch.get("pad_latitude") is None:
                continue
            distance = distance_km(lat, lon, launch["pad_latitude"], launch["pad_longitude"])
            if distance <= 800:
                findings.append(finding("info", f"Rocket launch {round(distance)} km away",
                                        f"{launch['name']} from {launch['location']}, no earlier than {launch['time']} ({launch['status']}).",
                                        ["Launch Library 2"], "medium",
                                        {"kind": "point", "latitude": launch["pad_latitude"], "longitude": launch["pad_longitude"],
                                         "zoom": 8}))
                break

        # Blind spots are findings too.
        for part, problem in (gathered.get("gaps") or {}).items():
            findings.append(finding("gap", f"Could not read {part}", problem[:200], [], "high"))
        for note in around.get("not_covered") or []:
            findings.append(finding("gap", "Not covered here", str(note)[:200], [], "high"))
        findings.sort(key=lambda item: PRIORITY[item["level"]])
        return findings

    @staticmethod
    def situation(findings: list[dict[str, Any]]) -> str:
        levels = {item["level"] for item in findings}
        return "alert" if "alert" in levels else "watch" if "watch" in levels else "calm"

    # ----------------------------------------------------------------- the tool
    def look(self, place: str = "here", radius_km: float = 25, traffic_cameras: int = 3, reason: bool = False,
             latitude: float | None = None, longitude: float | None = None) -> dict[str, Any]:
        radius = max(2.0, min(float(radius_km), 200.0))
        gathered = self.gather("" if latitude is not None else place, radius, int(traffic_cameras), latitude, longitude)
        findings = self.reason(gathered, radius)
        around = gathered["around"]
        situation = self.situation(findings)
        counted = [{key: count.get(key) for key in ("title", "vehicles", "level", "history", "dark", "event_id")} for count in gathered.get("traffic") or []]
        value = {"intelligence": True, "deep_look": True, "place": around["place"], "radius_km": radius,
                 "observed_at": around["observed_at"], "situation": situation, "findings": findings,
                 "metrics": around.get("metrics") or {}, "interpretation": around.get("interpretation"),
                 "cameras": (around.get("cameras") or [])[:5], "traffic": counted,
                 "fires": ((gathered.get("fires") or {}).get("fires") or [])[:5],
                 "sources_read": sorted(set(around.get("metrics") or {}) | {key for key in ("fires", "sky", "launches") if gathered.get(key)}),
                 "basis": "Every finding names its evidence. Rules, not guesses: fires are joined satellite detections, wind and "
                          "rain are model forecasts, traffic is a count on one still, 'unusual' compares with HAYAT's own earlier "
                          "observations. Blind spots are listed; nothing missing is assumed to be fine."}
        if reason:
            value["reading"] = self.read(value)
        return value

    def read(self, value: dict[str, Any]) -> dict[str, Any]:
        """The local model's reading of the findings: inference, labelled, never new facts."""
        model = getattr(self.manager, "model", None)
        if model is None:
            return {"text": "", "note": "HAYAT's local model is not loaded; the findings above stand on their own."}
        from app.agent.context import UNTRUSTED_DATA_NOTICE
        from app.models.base import Message
        from app.models.streaming import PublicTextFilter
        evidence = {key: value[key] for key in ("place", "radius_km", "observed_at", "situation", "findings", "metrics", "traffic", "fires")}
        messages = [Message("system", "You are HAYAT's analyst. Use only the JSON findings supplied. In at most 150 words: "
                            "what matters most here right now and why, how the findings connect (say 'possibly' for any link "
                            "that is not shown), and what to watch next. Mention the blind spots that limit the picture. "
                            "Never invent numbers, events, causes or sources. A 'severity' is HAYAT's label from the provider's fields, "
                            "not a measured intensity. " + UNTRUSTED_DATA_NOTICE),
                    Message("user", "Findings (from public provider data):\n" + json.dumps(evidence, ensure_ascii=False, default=str)[:12000])]
        try:
            reply = model.generate(messages, temperature=0.2, max_tokens=700)
        except Exception as exc:
            log_swallowed(_log, "the deep look model reading")
            return {"text": "", "note": f"The local model could not answer ({type(exc).__name__}); the findings stand on their own."}
        text = PublicTextFilter().feed(str(getattr(reply, "content", "") or ""), final=True).strip()
        return {"text": text, "model": getattr(reply, "model", "") or getattr(model, "model", ""),
                "note": "Local model inference over the findings above, not independent verification."}


SYMBOL = {"alert": "▲", "watch": "●", "info": "·", "gap": "○"}


def deep_report(value: dict[str, Any]) -> str | None:
    if not value.get("deep_look"):
        return None
    place = value["place"]
    counts = {level: sum(item["level"] == level for item in value["findings"]) for level in PRIORITY}
    lines = [f"Deep look · {place['name']} · within {value['radius_km']:g} km · {value['observed_at'][11:16]} UTC",
             f"Situation: {value['situation'].upper()} — {counts['alert']} alert, {counts['watch']} to watch, "
             f"{counts['info']} for information, {counts['gap']} blind spots."]
    for item in value["findings"]:
        if item["level"] == "gap":
            continue
        lines.append(f"{SYMBOL[item['level']]} {item['title']} — {item['detail']} [confidence {item['confidence']}]")
        lines.extend(f"    evidence: {entry}" for entry in item["evidence"][:3])
    if counts["alert"] + counts["watch"] + counts["info"] == 0:
        lines.append("Nothing notable in the sources read. That is not proof that nothing is happening; see the blind spots.")
    gaps = [item for item in value["findings"] if item["level"] == "gap"]
    if gaps:
        lines.append("Blind spots: " + "; ".join(f"{item['title']} ({item['detail'][:80]})" for item in gaps[:6]))
    if value.get("interpretation"):
        lines.append(value["interpretation"])
    reading = value.get("reading") or {}
    if reading.get("text"):
        lines.append("HAYAT's reading (model inference): " + reading["text"])
    elif reading.get("note"):
        lines.append(reading["note"])
    lines.append(value["basis"])
    return "\n".join(lines)


def register_deep_tools(registry: Any, deep: DeepLook) -> None:
    registry.register(
        "intelligence_deep_look",
        "A deep look at one place: reported events, closures, cameras and traffic counts, weather, air, fires (new or "
        "growing, and whether the wind blows their way), the ISS and nearby launches, compared with HAYAT's earlier "
        "observations. Returns findings with evidence and confidence, the blind spots, and optionally a model reading.",
        {"type": "object", "properties": {"place": {"type": "string", "maxLength": 200},
                                          "radius_km": {"type": "number", "minimum": 2, "maximum": 200},
                                          "traffic_cameras": {"type": "integer", "minimum": 0, "maximum": 5},
                                          "reason": {"type": "boolean"},
                                          "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                                          "longitude": {"type": "number", "minimum": -180, "maximum": 180}},
         "required": [], "additionalProperties": False},
        "low", "public_web_read", deep.look)


# ---------------------------------------------------------------------- chat
_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_PLACE = r"(?P<place>[^\s,?.!].{0,60}?)"
_END = r"(?:\s+(?:now|right\s+now|today|currently))?\s*[?.!]*$"
DEEP_ROUTES = (
    (re.compile(_LEAD + r"(?:take\s+|have\s+|give\s+me\s+)?(?:a\s+)?deep\s+look\s+(?:at|into|around)\s+" + _PLACE + _END, re.I), False),
    (re.compile(_LEAD + r"look\s+deep(?:ly)?\s+(?:at|into)\s+" + _PLACE + _END, re.I), False),
    (re.compile(_LEAD + r"(?:give\s+me\s+)?(?:a\s+)?(?:full\s+)?(?:situation\s+report|sitrep)\s+(?:for|on|of|about)\s+" + _PLACE + _END, re.I), False),
    (re.compile(_LEAD + r"(?:give\s+me\s+)?(?:a\s+|an\s+)?(?:deep|in[\s-]depth)\s+analy[sz]is\s+(?:of|for|on)\s+" + _PLACE + _END, re.I), True),
    (re.compile(_LEAD + r"analy[sz]e\s+" + _PLACE + r"\s+(?:deeply|in\s+depth|thoroughly)" + _END, re.I), True),
)


def deep_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if not value or len(value) > 200:
        return None
    for pattern, reason in DEEP_ROUTES:
        if match := pattern.match(value):
            place = match["place"].strip()
            if place.lower() in ("me", "us", "my location", "where i am", "here", "my area"):
                place = "here"
            return {"tool": "intelligence_deep_look", "arguments": {"place": place, **({"reason": True} if reason else {})}}
    return None
