"""The details panel says how the last answer was made: code, a fixed rule, or the brain and its checks."""
from __future__ import annotations

from types import SimpleNamespace

from app.answer_provenance import describe
from app.answers import Depth


def runtime(source, **conversation):
    session = SimpleNamespace(last_answer_source=source, standing_instruction={})
    return SimpleNamespace(session=session, model=SimpleNamespace(model="hayat:latest"),
                           conversation=SimpleNamespace(**conversation))


def test_code_and_rules_are_named_as_such():
    assert "Worked out by code" in describe(runtime("exact"))[0]
    assert "payments are stopped in code" in describe(runtime("money_guard"))[0]
    assert describe(runtime("")) == ["No answer yet."]


def test_a_brain_answer_lists_its_checks():
    lines = describe(runtime("brain",
                             last_assessment=SimpleNamespace(depth=Depth.NORMAL, word_count=12),
                             last_inquiry={"state": "examined", "questions": 3, "open_points": 1},
                             last_dual_review={"state": "reviewed", "revised": True}))
    text = "\n".join(lines)
    assert "hayat:latest" in text and "Answer depth: normal, 12 words." in text
    assert "Questioned itself first: 3 questions, 1 left open and said so." in text
    assert "revised before you saw it" in text
