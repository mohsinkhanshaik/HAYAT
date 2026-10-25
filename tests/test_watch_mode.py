"""Watch mode: on only when asked, speaks when something starts, never saves.

No real camera is opened; frames are synthetic and time is a fake clock.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.perception.camera.tracking import EyeProcessor
from app.perception.camera.watch import WatchMode, watch_mode
from app.session import SessionState
from tests.test_camera_tracking import wall, with_object


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Backend:
    def __init__(self, active=False):
        self.active = active
        self.frame = wall()

    def read(self):
        if not self.active:
            raise RuntimeError("Camera is not active.")
        return self.frame


class Camera:
    """The camera manager's surface that watch mode uses."""

    def __init__(self, tmp_path: Path, active=False, seen="A person sat down at the desk."):
        self.capture_backend = Backend(active)
        self.temp_root = tmp_path
        self.opened = self.closed = 0
        self.prompts: list[str] = []
        self.vision = SimpleNamespace(analyze=lambda path, prompt: (
            self.prompts.append(prompt), {"interpretation": seen})[1])

    def on(self, device_index=None):
        self.opened += 1
        self.capture_backend.active = True
        return {"state": "ACTIVE"}

    def off(self):
        self.closed += 1
        self.capture_backend.active = False
        return {"state": "OFF"}


def _mode(tmp_path, privacy=False, active=False):
    notes = []
    camera = Camera(tmp_path, active=active)
    mode = WatchMode(camera, privacy=lambda: privacy, on_note=notes.append, clock=Clock())
    mode._processor = EyeProcessor()
    return mode, camera, notes


def _move(mode, camera, frames=10, start=40):
    """Something crossing the view for a few frames; returns notes written."""
    written = []
    for index in range(frames):
        # Bounce across the view, so long runs of movement stay in frame.
        travel = (index * 4) % 360
        camera.capture_backend.frame = with_object(start + (travel if travel < 180 else 360 - travel))
        note = mode.check_once()
        if note:
            written.append(note)
        mode.clock.now += .4
    return written


def _still(mode, camera, seconds):
    camera.capture_backend.frame = wall()
    mode.check_once()
    mode.clock.now += seconds


# ------------------------------------------------------------------ routing

@pytest.mark.parametrize("text, control", [
    ("keep watching me", "WATCH_ON"), ("keep an eye on me", "WATCH_ON"), ("can you keep an eye on the room?", "WATCH_ON"),
    ("start watching", "WATCH_ON"), ("watch me continuously", "WATCH_ON"), ("turn on watch mode", "WATCH_ON"),
    ("stop watching", "WATCH_OFF"), ("stop watching me.", "WATCH_OFF"), ("watch mode off", "WATCH_OFF"),
])
def test_watch_mode_is_switched_by_plain_words(text, control):
    intent = IntentRouter().route(text, SessionState())
    assert intent.intent == IntentKind.CONTROL and intent.control == control


@pytest.mark.parametrize("text", ["watch me", "stop", "keep watching the news for updates", "stop watching youtube"])
def test_other_watch_words_are_not_watch_mode(text):
    assert IntentRouter().route(text, SessionState()).control not in {"WATCH_ON", "WATCH_OFF"}


# ------------------------------------------------------------------ noticing

def test_it_speaks_when_something_starts_after_a_quiet_spell(tmp_path):
    mode, camera, notes = _mode(tmp_path, active=True)
    mode._last_motion = mode.clock.now
    _still(mode, camera, WatchMode.QUIET_BEFORE + 5)
    written = _move(mode, camera)
    assert written == ["HAYAT noticed: A person sat down at the desk."] and notes == written
    assert "Do not identify anyone" in camera.prompts[0]
    assert list(tmp_path.glob("watch-*.jpg")) == []                # the sample was deleted


def test_continuous_movement_is_one_note_not_a_stream(tmp_path):
    mode, camera, notes = _mode(tmp_path, active=True)
    mode._last_motion = mode.clock.now - WatchMode.QUIET_BEFORE - 1
    _move(mode, camera, frames=10)
    assert len(notes) == 1
    # Still moving, frame after frame, well past the gap: no quiet spell in
    # between, so no new note.
    _move(mode, camera, frames=int((WatchMode.MIN_GAP + 10) / .4), start=40)
    assert len(notes) == 1


def test_notes_are_spaced_out_even_after_quiet_spells(tmp_path):
    mode, camera, notes = _mode(tmp_path, active=True)
    mode._last_motion = mode.clock.now - WatchMode.QUIET_BEFORE - 1
    _move(mode, camera)
    _still(mode, camera, WatchMode.QUIET_BEFORE + 1)                 # quiet, but inside the gap
    mode._processor = EyeProcessor()
    _move(mode, camera)
    assert len(notes) == 1
    _still(mode, camera, WatchMode.MIN_GAP)
    mode._processor = EyeProcessor()
    _move(mode, camera)
    assert len(notes) == 2


def test_a_dark_moment_is_said_plainly_without_asking_the_model(tmp_path):
    mode, camera, notes = _mode(tmp_path, active=True)
    mode._last_motion = mode.clock.now - WatchMode.QUIET_BEFORE - 1
    scene = {"regions": [{"heading": "left"}], "summary": "Something is moving on the left."}
    note = mode._note(np.full((240, 320, 3), 5, np.uint8), scene)
    assert "too dark" in note and camera.prompts == []


# ------------------------------------------------------------------ on and off

def test_it_opens_the_camera_it_needs_and_releases_only_what_it_opened(tmp_path, monkeypatch):
    monkeypatch.setattr("app.perception.camera.visibility.settle", lambda read, **kw: {"settled": True})
    mode, camera, _notes = _mode(tmp_path)
    assert mode.start().startswith("Watch mode is on")
    assert camera.opened == 1 and mode.active
    assert "already on" in mode.start()
    assert mode.stop() == "Watch mode is off." and not mode.active
    assert camera.closed == 1 and not camera.capture_backend.active

    mode, camera, _notes = _mode(tmp_path, active=True)             # the owner had it on already
    mode.start()
    mode.stop()
    assert camera.closed == 0 and camera.capture_backend.active
    assert mode.stop() == "Watch mode wasn't on."


def test_privacy_mode_refuses_it_and_stops_it(tmp_path, monkeypatch):
    monkeypatch.setattr("app.perception.camera.visibility.settle", lambda read, **kw: {"settled": True})
    mode, camera, _notes = _mode(tmp_path, privacy=True)
    assert "Privacy mode is on" in mode.start() and camera.opened == 0

    private = {"on": False}
    notes = []
    camera = Camera(tmp_path)
    mode = WatchMode(camera, privacy=lambda: private["on"], on_note=notes.append)
    mode.start()
    private["on"] = True
    deadline = time.time() + 5
    while mode.active and time.time() < deadline:
        time.sleep(.05)
    assert not mode.active and mode.ended_because == "privacy"
    assert camera.closed == 1 and notes == ["Watch mode stopped: privacy mode is on."]


def test_the_runtime_has_one_watch_mode_and_the_assistant_switches_it(tmp_path, monkeypatch):
    from app.assistant import AssistantService

    monkeypatch.setattr("app.perception.camera.visibility.settle", lambda read, **kw: {"settled": True})
    runtime = SimpleNamespace(camera=Camera(tmp_path), authority=SimpleNamespace(privacy_mode=False), voice=None)
    assert watch_mode(runtime) is watch_mode(runtime)
    service = AssistantService.__new__(AssistantService)
    service.runtime = runtime
    replies = []
    monkeypatch.setattr(service, "_direct", lambda intent, text, **kw: replies.append(text) or text, raising=False)
    router = IntentRouter()
    service._control(router.route("keep watching me", SessionState()))
    assert replies[-1].startswith("Watch mode is on") and runtime.watch.active
    service._control(router.route("stop watching", SessionState()))
    assert replies[-1] == "Watch mode is off." and not runtime.watch.active
