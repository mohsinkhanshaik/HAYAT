"""Watch mode: HAYAT keeping an eye on the room by itself, while switched on.

Off unless the owner turns it on ("keep watching me", or the Eye window), and
never remembered across restarts. While on, the cheap local motion tracker
runs continuously; the vision model is asked to look only when something
starts happening after a quiet spell, and a short note goes to the chat at
most once every ``MIN_GAP`` seconds. Privacy mode stops it at once. Frames
live in memory; the one sample handed to the vision model is written to the
camera's temporary folder and deleted straight after.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class WatchMode:
    #: Seconds between motion checks: frequent enough to notice a person
    #: arriving, light enough to leave the machine alone.
    INTERVAL = .4
    #: Stillness needed before new movement counts as "something happened".
    QUIET_BEFORE = 45.0
    #: Shortest gap between two notes in the chat.
    MIN_GAP = 90.0
    PROMPT = ("In one or two plain sentences, say what is happening in this camera view right now. "
              "Do not identify anyone by name or guess who they are. If it is too dark or blurred to tell, say so.")

    def __init__(self, camera: Any, *, privacy: Callable[[], bool],
                 on_note: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] | None = None) -> None:
        self.camera = camera
        self.privacy = privacy
        self.on_note = on_note
        self.clock = clock
        self._stop = threading.Event()
        self._sleep = sleep or (lambda seconds: self._stop.wait(seconds))
        self._thread: threading.Thread | None = None
        self._opened_camera = False
        self._processor: Any = None
        self._last_motion = 0.0
        self._last_note = -1e9
        self.notes = 0
        self.last_note = ""
        self.ended_because = ""

    # ------------------------------------------------------------ control

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> str:
        if self.active:
            return "Watch mode is already on. Say \"stop watching\" to turn it off."
        if self.privacy():
            return "Privacy mode is on, so I won't watch. Turn privacy mode off first."
        backend = self.camera.capture_backend
        if not backend.active:
            try:
                self.camera.on()
            except Exception as exc:
                return f"I couldn't start the camera: {type(exc).__name__}: {exc}"
            if not backend.active:
                return "I couldn't start the camera."
            self._opened_camera = True
            from app.perception.camera import visibility

            visibility.settle(backend.read)
        from app.perception.camera.tracking import EyeProcessor

        self._processor = EyeProcessor()
        self._stop.clear()
        self.ended_because = ""
        now = self.clock()
        self._last_motion, self._last_note = now, -1e9
        self._thread = threading.Thread(target=self._run, name="hayat-watch", daemon=True)
        self._thread.start()
        return ("Watch mode is on. I'll keep an eye on the room and tell you when something happens. "
                "Nothing is saved; say \"stop watching\" to turn it off.")

    def stop(self, reason: str = "asked") -> str:
        was_active = self.active
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self.ended_because = reason
        if self._opened_camera:
            self._opened_camera = False
            try:
                self.camera.off()
            except Exception:
                log_swallowed(_log, "turning the camera off after watch mode", level=logging.WARNING)
        return "Watch mode is off." if was_active else "Watch mode wasn't on."

    def status(self) -> dict[str, Any]:
        return {"active": self.active, "notes": self.notes, "last_note": self.last_note,
                "ended_because": self.ended_because, "min_gap_seconds": self.MIN_GAP}

    # ------------------------------------------------------------ watching

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.privacy():
                self._end("privacy")
                return
            if not self.camera.capture_backend.active:
                self._end("camera_closed")
                return
            try:
                self.check_once()
            except Exception:
                # one bad frame must not end the watch
                log_swallowed(_log, "checking a watch-mode frame", level=logging.WARNING)
            self._sleep(self.INTERVAL)

    def check_once(self) -> str | None:
        """Look at one frame; returns the note written, if any."""
        import cv2

        frame = self.camera.capture_backend.read()
        small = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
        scene = self._processor(small).info["tracking"].get("scene") or {}
        if not scene.get("regions"):
            return None
        now = self.clock()
        quiet_for = now - self._last_motion
        self._last_motion = now
        if quiet_for < self.QUIET_BEFORE or now - self._last_note < self.MIN_GAP:
            return None
        return self._note(frame, scene)

    def _note(self, frame: Any, scene: dict[str, Any]) -> str:
        """Ask the vision model about this moment, and say it in the chat."""
        import cv2
        from app.perception.camera import visibility

        root = getattr(self.camera, "temp_root", None)
        description = ""
        if root is not None and getattr(self.camera, "vision", None) is not None:
            path = root / f"watch-{uuid.uuid4().hex}.jpg"
            try:
                root.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(path), frame)
                view = visibility.assess_file(path)
                if view["state"] != "clear":
                    description = view["message"]
                else:
                    answer = self.camera.vision.analyze(str(path), self.PROMPT) or {}
                    description = str(answer.get("interpretation") or "").strip()
            except Exception:
                description = ""
            finally:
                path.unlink(missing_ok=True)
        note = f"HAYAT noticed: {description or scene.get('summary') or 'movement in view.'}"
        bus = getattr(self.camera, "event_bus", None)
        if bus is not None:
            # When, not what: the note itself stays out of the durable log.
            try:
                bus.publish("CAMERA_OBSERVATION", "camera", {"source": "watch"})
            except Exception:
                log_swallowed(_log, "publishing a watch-mode observation")
        self._last_note = self.clock()
        self.notes += 1
        self.last_note = note
        if self.on_note is not None:
            try:
                self.on_note(note)
            except Exception:
                log_swallowed(_log, "delivering a watch-mode note")
        return note

    def _end(self, reason: str) -> None:
        self._stop.set()
        self._thread = None
        self.ended_because = reason
        if self._opened_camera:
            self._opened_camera = False
            try:
                self.camera.off()
            except Exception:
                log_swallowed(_log, "turning the camera off when watch mode ended", level=logging.WARNING)
        if self.on_note is not None and reason == "privacy":
            try:
                self.on_note("Watch mode stopped: privacy mode is on.")
            except Exception:
                log_swallowed(_log, "delivering the privacy stop note")


def watch_mode(runtime: Any) -> WatchMode:
    """The runtime's single watch mode, created on first use."""
    existing = getattr(runtime, "watch", None)
    if isinstance(existing, WatchMode):
        return existing
    authority = getattr(runtime, "authority", None)
    mode = WatchMode(runtime.camera, privacy=lambda: bool(getattr(authority, "privacy_mode", False)))
    runtime.watch = mode
    return mode
