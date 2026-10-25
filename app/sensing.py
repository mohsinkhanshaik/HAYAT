"""Wi-Fi sensing joined to the camera: a live movement timeline, fused checks, and announced changes.

app/csi.py reads the radio. This module watches what it reads:

* ``SensingMonitor`` scores the newest few seconds of the active connection
  once a second, keeps a timeline (the GUI draws it), and publishes
  ``SENSING_MOTION`` when the level changes -- so proactive rules can react to
  movement later. A level must hold for two readings before it counts, so one
  noisy window does not flip it.
* ``fuse`` combines a Wi-Fi verdict with a camera change check into one plain
  sentence that says which sensor saw what. They disagree for good reasons:
  Wi-Fi sees through walls and outside the picture; the camera sees light
  changes that move no radio path.

The camera is never switched on silently. It joins a check only when the owner
asks ("check with the camera") or ticks "Confirm with camera" in the Wi-Fi
Sensing window, and then runs through the registry, so camera_use authority
and privacy mode decide as they do for every other camera request. Privacy
mode also pauses the monitor and closes the Wi-Fi connections: a privacy
switch that leaves a through-wall motion sensor running is not one.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from app.csi import CsiHub, judge_motion, movement_score
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

SENSING_TOOL_NAMES = ("sensing_check", "sensing_status")
#: Seconds of frames scored per reading.
WINDOW_SECONDS = 4.0
#: Readings kept for the timeline: ten minutes at one a second.
HISTORY = 600
#: A new level must be seen this many readings in a row before it is announced.
HOLD = 2
CAMERA_CHECK_SECONDS = 3.0


def _where(camera: dict[str, Any]) -> str:
    return f" (in the {camera['where']} of the picture)" if camera.get("where") else ""


def fuse(wifi: dict[str, Any] | None, camera: dict[str, Any] | None) -> dict[str, Any]:
    """One verdict from a Wi-Fi motion judgement and a camera change check, saying which saw what."""
    wifi_moving = bool(wifi and wifi.get("moving"))
    wifi_known = bool(wifi and wifi.get("moving") is not None)
    camera_known = bool(camera and camera.get("changed") is not None and not camera.get("error"))
    camera_changed = bool(camera_known and camera.get("changed"))
    if not wifi_known and not camera_known:
        verdict, summary = "unknown", "Neither sensor gave a reading."
    elif not camera_known:
        verdict = "moving" if wifi_moving else "still"
        reason = (camera or {}).get("error") or "the camera was not part of this check"
        summary = (f"Wi-Fi only: {'movement' if wifi_moving else 'no movement'} ({reason}).")
    elif not wifi_known:
        verdict = "moving" if camera_changed else "still"
        summary = f"Camera only: {'the picture changed' if camera_changed else 'no change in the picture'} (no Wi-Fi reading)."
    elif wifi_moving and camera_changed:
        verdict, summary = "confirmed", "Movement confirmed: Wi-Fi and the camera both saw it" + _where(camera) + "."
    elif wifi_moving:
        verdict = "wifi_only"
        light = " (the camera saw only the light change)" if camera.get("kind") == "lighting" else ""
        summary = ("Wi-Fi saw movement the camera did not" + light + ": most likely outside the camera's view "
                   "(another part of the room, behind a wall or door), or something like a fan.")
    elif camera_changed:
        verdict = "camera_only"
        summary = ("The camera saw movement" + _where(camera) + " that Wi-Fi did not: movement too small or too far "
                   "from the radio path, or a screen or reflection changing.")
    else:
        light = " The camera saw only a change of light." if camera.get("kind") == "lighting" else ""
        verdict, summary = "still", "Still: neither Wi-Fi nor the camera saw movement." + light
    return {"verdict": verdict, "summary": summary, "moving": verdict in {"confirmed", "wifi_only", "camera_only", "moving"},
            "wifi": wifi, "camera": camera}


class SensingMonitor:
    """Scores the live CSI stream once a second and remembers what it found."""

    def __init__(self, hub: CsiHub, *, registry: Any = None, events: Any = None,
                 privacy: Callable[[], bool] = lambda: False, interval: float = 1.0,
                 window: float = WINDOW_SECONDS, clock: Callable[[], float] = time.time) -> None:
        self.hub, self.registry, self.events = hub, registry, events
        self.privacy, self.interval, self.window, self.clock = privacy, interval, window, clock
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY)
        self.level = "unknown"
        self.camera_confirm = False
        self.last_fused: dict[str, Any] | None = None
        self.paused_for_privacy = False
        self._candidate, self._candidate_count = "unknown", 0
        self._camera_busy = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        hub.subscribe(self._on_frame)

    # ---------------------------------------------------------------- loop
    def _on_frame(self, _name: str, _frame: Any) -> None:
        if self._thread is None or not self._thread.is_alive():
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="hayat-sensing", daemon=True)
            self._thread.start()

    def stop(self) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(3)
        return True

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                if self.tick() is None and not self.hub.status()["connections"]:
                    return  # nothing connected; the next frame starts the loop again
            except Exception:
                log_swallowed(_log, "scoring the Wi-Fi sensing window", level=logging.WARNING)

    def tick(self) -> dict[str, Any] | None:
        """Score the newest window of the active connection; None when nothing is receiving."""
        if self.privacy():
            if not self.paused_for_privacy:
                self.paused_for_privacy = True
                self.hub.close_all()
                self._publish("paused", {"reason": "privacy mode"})
            return None
        self.paused_for_privacy = False
        status = self.hub.status()
        live = [item for item in status["connections"] if item["receiving"] or item["state"] == "finished"]
        if not live:
            return None
        active = live[-1]
        frames = self.hub.frames(active["name"], 0 if active["kind"] == "file" else self.window)
        if active["kind"] == "file":
            frames = frames[-max(10, int(self.window * 100)):]
        measured = movement_score(frames)
        judged = judge_motion(measured, self.hub._baselines().get(active["name"]))
        reading = {"t": self.clock(), "name": active["name"], "kind": active["kind"], "score": measured.get("score"),
                   "autocorrelation": measured.get("autocorrelation"), "basis": measured.get("basis"),
                   "level": judged["level"], "ratio": judged.get("ratio_to_still"), "calibrated": judged["calibrated"],
                   "summary": judged["summary"]}
        self.history.append(reading)
        self._settle(reading)
        return reading

    def _settle(self, reading: dict[str, Any]) -> None:
        level = reading["level"]
        if level == "unknown":
            return
        if level == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate, self._candidate_count = level, 1
        if self._candidate_count < HOLD or level == self.level:
            return
        previous, self.level = self.level, level
        self._publish(level, {"from": previous, "to": level, "connection": reading["name"],
                              "score": reading["score"], "ratio_to_still": reading["ratio"]})
        if self.camera_confirm and level != "still" and previous in {"still", "unknown"}:
            self._confirm_with_camera()

    def _publish(self, level: str, payload: dict[str, Any]) -> None:
        if self.events is None:
            return
        try:
            self.events.publish("SENSING_MOTION", "wifi_sensing", {"level": level, **payload})
        except Exception:
            log_swallowed(_log, "publishing a Wi-Fi sensing change")

    def _confirm_with_camera(self) -> None:
        if self._camera_busy or self.registry is None:
            return
        self._camera_busy = True

        def run() -> None:
            try:
                camera = self._camera_check(CAMERA_CHECK_SECONDS)
                wifi = self.history[-1] if self.history else None
                self.last_fused = {**fuse({"moving": self.level != "still", **(wifi or {})}, camera),
                                   "at": self.clock(), "trigger": "movement started"}
            finally:
                self._camera_busy = False
        threading.Thread(target=run, name="hayat-sensing-camera", daemon=True).start()

    def _camera_check(self, seconds: float) -> dict[str, Any]:
        if self.registry is None or "camera_watch_change" not in self.registry.names():
            return {"error": "no camera is set up"}
        result = self.registry.execute("camera_watch_change", {"seconds": float(seconds), "interval": 0.3})
        if not result.success:
            return {"error": result.error or "the camera check was not allowed"}
        output = result.output if isinstance(result.output, dict) else {}
        return {"changed": bool(output.get("changed")), "kind": output.get("kind"), "where": output.get("where"),
                "changed_area_pct": output.get("changed_area_pct"), "peak_difference": output.get("peak_difference"),
                "elapsed_seconds": output.get("elapsed_seconds")}

    # ----------------------------------------------------------- reading
    def check(self, with_camera: bool = False, seconds: float = 5.0) -> dict[str, Any]:
        """Wi-Fi motion now, optionally with a camera change check over the same seconds, fused."""
        seconds = max(2.0, min(float(seconds or 5), 30.0))
        camera_result: dict[str, Any] = {}
        worker = None
        if with_camera:
            worker = threading.Thread(target=lambda: camera_result.update(self._camera_check(seconds)),
                                      name="hayat-sensing-camera-check", daemon=True)
            worker.start()
        try:
            wifi = self.hub.motion(seconds=seconds)
        except ValueError as exc:
            wifi = {"moving": None, "summary": str(exc)}
        if worker is not None:
            worker.join(seconds + 20)
        fused = fuse(wifi, camera_result if with_camera else None)
        self.last_fused = {**fused, "at": self.clock(), "trigger": "asked"}
        lines = [fused["summary"]]
        if wifi.get("summary") and wifi.get("moving") is not None:
            lines.append("Wi-Fi: " + wifi["summary"])
        elif wifi.get("moving") is None:
            lines.append(wifi.get("summary", ""))
        return {**fused, "fused_view": True, "text": " ".join(line for line in lines if line)}

    def snapshot(self, history: int = 120) -> dict[str, Any]:
        status = self.hub.status()
        recent = list(self.history)[-history:]
        latest = recent[-1] if recent else None
        return {"status": status, "level": self.level, "latest": latest, "history": recent,
                "camera_confirm": self.camera_confirm, "last_fused": self.last_fused,
                "paused_for_privacy": self.paused_for_privacy, "monitoring": bool(self._thread and self._thread.is_alive())}

    def status_report(self) -> dict[str, Any]:
        """What Wi-Fi sensing sees right now, for chat and the sensory-state report."""
        snap = self.snapshot(history=60)
        latest = snap["latest"]
        moving = sum(1 for item in snap["history"] if item["level"] not in {"still", "unknown"})
        if snap["paused_for_privacy"]:
            summary = "Wi-Fi sensing is paused: privacy mode is on."
        elif not snap["status"]["connections"]:
            summary = snap["status"]["summary"]
        elif latest is None:
            summary = snap["status"]["summary"].rstrip(".") + ". No movement reading yet."
        else:
            summary = (f"{snap['status']['summary'].rstrip('.')}. Now: {latest['level']}"
                       + (f" ({latest['ratio']}x the still room)" if latest.get("ratio") else "")
                       + f". Moving in {moving} of the last {len(snap['history'])} seconds.")
        if snap["last_fused"]:
            summary += " Last camera cross-check: " + snap["last_fused"]["summary"]
        return {"summary": summary, "level": snap["level"], "latest": latest, "connected": snap["status"]["connected"],
                "receiving": snap["status"]["receiving"], "real_csi": snap["status"]["real_csi"],
                "camera_confirm": snap["camera_confirm"], "sensing_status_view": True}

    def set_camera_confirm(self, enabled: bool) -> None:
        self.camera_confirm = bool(enabled)


def register_sensing_tools(registry: Any, monitor: SensingMonitor) -> SensingMonitor:
    registry.sensing = monitor
    registry.register(
        "sensing_check",
        "Check for movement with Wi-Fi sensing (CSI) and, when with_camera is true, the camera at the same time; "
        "says which sensor saw what (both, Wi-Fi only, camera only, neither). The camera opens only for this check.",
        {"type": "object", "properties": {
            "with_camera": {"type": "boolean"},
            "seconds": {"type": "number", "minimum": 2, "maximum": 30}},
         "additionalProperties": False},
        "low", "computer_observe", monitor.check)
    registry.register(
        "sensing_status",
        "What Wi-Fi sensing sees right now: the connection, the current movement level, how much of the last minute "
        "had movement, and the last camera cross-check.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", lambda: monitor.status_report())
    return monitor
