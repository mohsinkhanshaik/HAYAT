"""Fixes found by driving HAYAT's real brain end to end on 2026-10-04 (tests/test_live_brain.py).

* A word problem that said "marked price" went to web research and came back as snippets, not $16.
* "What time is it right now?" went to a web search, and the brain alone answered "10:42 PM" at 3:40 AM.
* "Describe the image D:/.../invoice.png" never reached the vision model; the brain invented an invoice.
* "great what have learn about today" was answered as a world question instead of the learning report.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.intent.live_web import is_local_clock_question, is_self_contained_calculation, local_clock_answer, needs_live_web
from app.intent.router import IntentRouter, image_file_in
from app.intent.models import IntentKind


def test_a_question_that_brings_its_own_numbers_is_worked_out_not_searched():
    problem = ("A shop buys a jacket for $80, marks it up 50%, then sells it at 20% off the marked price. "
               "What is the profit in dollars?")
    assert is_self_contained_calculation(problem) and needs_live_web(problem) is None
    assert needs_live_web("what is the price of bitcoin today") is not None
    assert needs_live_web("search the web for the price of gold") is not None


def test_the_local_time_comes_from_the_clock_and_other_places_do_not():
    assert is_local_clock_question("What time is it right now?")
    assert is_local_clock_question("what's today's date")
    assert not is_local_clock_question("what time is it in Tokyo")
    answer = local_clock_answer(datetime(2026, 10, 4, 3, 40))
    assert answer.startswith("It's 3:40 AM on Sunday, 4 October 2026")
    routed = IntentRouter(None).route("What time is it right now?")
    assert routed.intent is IntentKind.CONVERSATION and routed.response.startswith("It's ")


def test_a_named_image_file_goes_to_the_vision_model(tmp_path):
    picture = tmp_path / "invoice 4721.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = f"Describe the image {picture}. What number is on it?"
    assert image_file_in(text) == str(picture)
    routed = IntentRouter(None).route(text)
    assert routed.entities.get("modality") == "vision" and routed.entities.get("image_path") == str(picture)
    assert image_file_in(f"Describe {tmp_path / 'missing.png'}") is None


def test_the_owners_way_of_asking_what_was_learned_gets_the_learning_report():
    routed = IntentRouter(None).route("great what have learn about today")
    assert routed.control == "LEARNING_REPORT"
    assert IntentRouter(None).route("great what is the capital of france").control != "LEARNING_REPORT"


def test_the_image_tool_shows_the_model_only_what_is_there(tmp_path):
    from app.tools.vision import VisionTools

    seen = {}
    vision = SimpleNamespace(analyze=lambda path, prompt: seen.update(path=path, prompt=prompt) or
                             {"path": path, "interpretation": "INVOICE 4721", "model": "m", "provider": "ollama"})
    result = VisionTools(computer=None, vision=vision).describe_image(str(tmp_path / "a.png"), "What number?")
    assert result["interpretation"] == "INVOICE 4721"
    assert "only what is actually visible" in seen["prompt"] and "What number?" in seen["prompt"]


def test_common_small_talk_is_answered_instantly_without_the_brain():
    """Measured 2026-10-04: these took 29-71 s each through the brain, with an explanation of the word appended."""
    router = IntentRouter(None)
    for text in ("yo", "salam", "assalamu alaikum", "HAYAT", "HAYAT HAYAT HAYAT HAYAT.", "hey whats up",
                 "how was your day", "i'm back", "nice", "hi there"):
        routed = router.route(text)
        assert routed.intent is IntentKind.CONVERSATION and routed.response, text
    assert router.route("salam, can you help me with taxes").response is None
    assert router.route("hayat open notepad").intent is not IntentKind.CONVERSATION
