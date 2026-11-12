"""Reply speed: the owner's setting is the default, and every message is still read for what it needs."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.intent.router import IntentRouter
from app.modes import ExecutionMode, ModeManager
from app.reply_speed import DEEP, MEDIUM, REFLEX, pace

ROUTER = IntentRouter()


@pytest.mark.parametrize("text,expected", [
    ("haha that is funny, what do you think about pineapple pizza?", REFLEX),
    ("I am so tired today", REFLEX), ("what about you?", REFLEX), ("tell me a joke", REFLEX),
    ("kya haal hai bhai", REFLEX), ("yaar aaj bahut thak gaya hoon", REFLEX), ("capital of france?", REFLEX),
    ("open notepad", REFLEX), ("ok and then what happened", REFLEX),
    ("Explain how a fridge works", MEDIUM), ("should i take this job offer?", MEDIUM),
    ("How do I reset my router?", MEDIUM),
    ("Prove that the square root of 2 is irrational", DEEP),
    ("Write a python script that renames all files in a folder", DEEP),
    ("Compare renting and buying a house in Austin for me", DEEP),
    ("why do some startups fail? think it through", DEEP),
])
def test_each_message_is_read_for_the_thought_it_needs(text, expected):
    assert pace(text, ROUTER.route(text)) == expected


def _resolved(manager, text):
    return set(manager.resolve(ROUTER.route(text)))


def test_reflex_talk_is_fast_even_in_deep_and_hard_work_is_lifted_in_fast(tmp_path: Path):
    """Live 2026-11-11/12: in Deep mode a casual chat turn took 30-170 s (self-questioning, a second model, a minimum
    length). Talk stays conversational; a proof never gets the careless path."""
    modes = ModeManager(tmp_path / "modes.json")
    modes.set_reasoning("deep")
    assert _resolved(modes, "what about you?") == {ExecutionMode.FAST}
    assert {ExecutionMode.DEEP, ExecutionMode.ADVERSARIAL} <= _resolved(modes, "Explain how a fridge works")
    modes.set_reasoning("fast")
    assert _resolved(modes, "Explain how a fridge works") == {ExecutionMode.FAST}
    assert _resolved(modes, "Prove that the square root of 2 is irrational") == {ExecutionMode.BALANCED}
    modes.set_reasoning("medium")
    assert _resolved(modes, "Explain how a fridge works") == {ExecutionMode.BALANCED}
    assert _resolved(modes, "kya haal hai bhai") == {ExecutionMode.FAST}
    modes.set_reasoning("auto")
    assert modes.snapshot()["speed"] == "auto"
    routed = ROUTER.route("Prove that the square root of 2 is irrational")
    modes.resolve(routed)
    assert routed.entities["pace"]["message"] == DEEP and "work that needs thought" in routed.entities["pace"]["why"]


def test_speed_is_set_in_plain_words(tmp_path):
    for said, setting in (("fast mode", "fast"), ("reply speed medium", "medium"), ("auto mode", "auto"),
                          ("reply faster", "fast"), ("deep mode", "deep"), ("speed: fast", "fast")):
        routed = ROUTER.route(said)
        assert routed.control == "REPLY_SPEED" and routed.entities["speed"] == setting, said
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    reply = rt.assistant.handle("fast mode").display_response
    assert reply.startswith("Reply speed: Fast.") and rt.modes.snapshot()["speed"] == "fast"
