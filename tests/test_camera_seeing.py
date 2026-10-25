"""The camera says what it sees, or plainly that it sees nothing -- never invents.

From real use: "can u watch me?" got three offers to look instead of a look,
an internal correction note was shown in the reply, and a near-black frame
was described as "a dark, abstract background" with OCR text "WtuCOMtTO".
No real camera is opened here.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from app.agent.conversation import owner_words
from app.agent.verifier import Verifier
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.perception.camera.manager import CameraManager
from app.perception.camera.visibility import assess, readable_text, settle
from app.session import SessionState
from app.tools.registry import ToolResult


# ------------------------------------------------------------------ routing

@pytest.mark.parametrize("request_text", [
    "can u watch me?", "can you watch me", "watch me", "watch this!", "show me the room",
    "what's in front of you?", "what do you see", "what do you see right now?", "how do i look",
    # Questions only sight can answer: it looks rather than asking whether it may.
    "what am i holding?", "what am I wearing", "how many fingers am i holding up", "what colour is my shirt?",
    "do i look tired?", "what am i doing right now?",
])
def test_asking_it_to_watch_makes_it_look(request_text):
    intent = IntentRouter().route(request_text, SessionState())
    assert intent.intent == IntentKind.COMPUTER_CONTROL
    assert intent.entities.get("modality") == "camera" and intent.entities.get("observation") is True


@pytest.mark.parametrize("request_text", [
    "watch this video about rust", "i want to watch a movie", "what do you see as the future of ai",
    "what am I doing wrong in this code?", "what am i doing tomorrow", "how many fingers does a hand have",
    "what color is my car in the photo",
])
def test_other_uses_of_watch_and_see_are_not_the_camera(request_text):
    assert IntentRouter().route(request_text, SessionState()).entities.get("modality") != "camera"


# ------------------------------------------------------- the leaked instruction

def test_a_retry_instruction_is_never_treated_as_the_owners_words():
    note = "\n\n[Correction: you just said you cannot see through the camera. That is false. ...]"
    assert owner_words("can u watch me?" + note) == "can u watch me?"
    assert owner_words("explain it\n\n[You just declined this. It is a question...]") == "explain it"
    assert owner_words("what is [x]?\n\nand [y]") == "what is [x]?\n\nand [y]"   # his own brackets stay


def test_the_coverage_note_cannot_quote_a_retry_instruction(monkeypatch):
    """The reply said "I could not properly cover: can u watch me? [Correction:
    you just said you cannot see through the camera...]"."""
    import app.answers as answers
    from app.agent.conversation import ConversationService

    # Report everything asked as uncovered, so the note quotes exactly the
    # question the checker was given.
    monkeypatch.setattr(answers, "assess", lambda response, question, **_kw: SimpleNamespace(
        complete=False, missing=[question], describe=lambda: "incomplete"))
    service = ConversationService.__new__(ConversationService)
    service.MAX_CONTINUATIONS = 0
    reply, _ = service._finish("Yes.", SimpleNamespace(finish_reason="stop"),
                               "can u watch me?\n\n[Correction: you just said you cannot see.]",
                               [], None, set(), None)
    assert "I could not properly cover: can u watch me?" in reply
    assert "[Correction" not in reply


# ------------------------------------------------------------------ visibility

def test_a_frame_is_judged_before_any_model_sees_it():
    rng = np.random.default_rng(1)
    assert assess(rng.normal(18, 8, (90, 160)).clip(0, 255))["state"] == "dark"
    assert assess(np.full((90, 160), 128.0))["state"] == "covered"
    assert assess((np.full((90, 160), 245.0) + rng.normal(0, 12, (90, 160))).clip(0, 255))["state"] == "overexposed"
    assert assess(rng.normal(120, 45, (90, 160)).clip(0, 255))["state"] == "clear"
    assert "too dark" in assess(np.zeros((9, 16)))["message"]


def test_ocr_smears_are_dropped_and_real_words_kept():
    # The owner's actual reading from a dark frame.
    assert readable_text("WELCOME TO\nWtuCOMtTO\nWELCOME\n. auz\n-n") == "WELCOME TO"
    assert readable_text("Exit\nUSB 3.0 port\nxQzT7\nCoffee mug") == "Exit\nUSB 3.0 port\nCoffee mug"
    assert readable_text("") == ""


def test_settle_waits_out_the_dark_warm_up_frames():
    levels = iter([2, 5, 30, 70, 95, 101, 102, 102, 102])
    clock = SimpleNamespace(now=0.0)

    def read():
        return np.full((4, 4), next(levels), np.uint8)

    result = settle(read, clock=lambda: clock.now, sleep=lambda s: setattr(clock, "now", clock.now + s))
    assert result["settled"] and result["brightness"] >= 100 and result["reads"] >= 7
    stuck = settle(lambda: np.zeros((4, 4), np.uint8), timeout=.5, clock=lambda: clock.now,
                   sleep=lambda s: setattr(clock, "now", clock.now + s))
    assert stuck["settled"] is False                       # never settles: gives up, does not hang


# ------------------------------------------------------------------ observe()

class Camera:
    """A device that starts dark while it adjusts, then shows a textured room."""

    def __init__(self, bright: bool = True, dark_frames: int = 5):
        self.active, self.device_index, self.reads = False, None, 0
        self.bright, self.dark_frames = bright, dark_frames

    def open(self, index, width, height):
        self.active, self.device_index = True, index
        return {"active": True}

    def read(self):
        self.reads += 1
        if not self.bright or self.reads <= self.dark_frames:
            return np.full((72, 128, 3), 6, np.uint8)
        rng = np.random.default_rng(4)
        return (rng.normal(120, 45, (72, 128, 3))).clip(0, 255).astype(np.uint8)

    def save(self, path):
        cv2.imwrite(str(path), self.read())
        return {"path": str(path), "width": 128, "height": 72, "bytes": path.stat().st_size}

    def close(self):
        self.active, self.device_index = False, None


def _manager(tmp_path, camera, calls):
    manager = CameraManager({"retain_frames": False}, vision=SimpleNamespace(
        analyze=lambda path, prompt: calls.append(prompt) or {"interpretation": "A person at a desk."}))
    manager.temp_root = tmp_path
    manager.ocr = SimpleNamespace(status=lambda: {"available": True},
                                  read=lambda path: {"available": True, "text": "WELCOME TO\nWtuCOMtTO\n. auz"})
    manager.capture_backend = camera
    return manager


def test_the_camera_warms_up_before_looking(tmp_path):
    calls = []
    camera = Camera(dark_frames=5)
    result = _manager(tmp_path, camera, calls).observe(shots=2, interval_ms=0)
    assert result["warm_up"]["settled"] is True
    assert result["visibility"]["state"] == "clear"        # the dark start never reached the model
    assert calls and "do not describe darkness" in calls[0]
    assert result["ocr"]["text"] == "WELCOME TO"


def test_a_dark_view_is_said_plainly_and_the_model_is_not_asked(tmp_path):
    from app.assistant import AssistantService

    calls = []
    result = _manager(tmp_path, Camera(bright=False), calls).observe(shots=2, interval_ms=0)
    assert result["visibility"]["state"] == "dark" and calls == []
    assert result["ocr"]["text"] == ""                      # no "WtuCOMtTO" from darkness
    # Still a real, verified result: "nothing to see" is the honest answer.
    assert Verifier().check({"type": "camera_observation"}, ToolResult("camera_observe", True, result)).passed
    reply = AssistantService._camera_observation(SimpleNamespace(history=[{"result": {"output": result}}]))
    assert reply.startswith("I looked, but the picture is too dark")
    assert "abstract" not in reply and "raw frame retained: False" in reply


def test_a_clear_view_reads_as_what_it_sees(tmp_path):
    from app.assistant import AssistantService

    result = _manager(tmp_path, Camera(), []).observe(shots=2, interval_ms=0)
    reply = AssistantService._camera_observation(SimpleNamespace(history=[{"result": {"output": result}}]))
    assert reply.startswith("Here's what I see:") and "A person at a desk." in reply
    assert "Readable text (untrusted): WELCOME TO" in reply and "WtuCOMtTO" not in reply


def test_a_busy_vision_model_keeps_the_text_reading_instead_of_failing(tmp_path):
    calls = []
    manager = _manager(tmp_path, Camera(), calls)

    def busy(path, prompt):
        calls.append(prompt)
        raise TimeoutError("Ollama request failed: timed out")
    manager.vision = SimpleNamespace(analyze=busy)
    result = manager.observe(shots=3, interval_ms=0, analyze_frames=3)
    assert len(calls) == 1  # a busy model is not asked again for the other frames
    assert result["vision"]["available"] is False and "did not answer" in result["vision"]["reason"]
    assert result["ocr"]["text"] == "WELCOME TO" and result["frames_analyzed"] == 0
