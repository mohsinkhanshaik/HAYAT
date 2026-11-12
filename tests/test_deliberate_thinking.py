"""'Think harder', 'overthink this', 'take your time': that turn gets the full deliberate path (2026-11-09)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.intent.models import IntentKind, TaskComplexity
from app.modes import ExecutionMode, ModeManager, wants_deliberation


@pytest.mark.parametrize("text", ["think harder about this", "please overthink it", "over-think the plan",
                                  "take your time", "think it through", "thnik harder", "think this through carefully",
                                  "double check your reasoning", "challenge your assumptions", "don't rush",
                                  "think step by step", "deep think: why did rome fall"])
def test_plain_requests_for_more_thought_are_recognised(text):
    assert wants_deliberation(text)


@pytest.mark.parametrize("text", ["i think so", "what do you think of paris", "thinking of you", "take the time machine",
                                  "the deep end of the pool", "how long does it take"])
def test_ordinary_sentences_are_not(text):
    assert not wants_deliberation(text)


def intent(goal, kind=IntentKind.QUESTION, complexity=TaskComplexity.SIMPLE):
    return SimpleNamespace(intent=kind, complexity=complexity, goal=goal, entities={})


def test_a_simple_question_stays_fast_and_asking_for_thought_makes_it_deliberate(tmp_path: Path):
    modes = ModeManager(tmp_path / "modes.json")
    assert modes.resolve(intent("capital of france?")) == [ExecutionMode.FAST]
    asked = intent("capital of france? think harder")
    resolved = modes.resolve(asked)
    assert ExecutionMode.FAST not in resolved and {ExecutionMode.DEEP, ExecutionMode.ADVERSARIAL} <= set(resolved)
    assert asked.entities["deliberate"] is True


def test_it_applies_even_when_the_owner_fixed_a_depth(tmp_path: Path):
    modes = ModeManager(tmp_path / "modes.json")
    modes.set_reasoning("balanced")
    resolved = modes.resolve(intent("should i take this job offer? take your time"))
    assert {ExecutionMode.DEEP, ExecutionMode.ADVERSARIAL} <= set(resolved)
    assert modes.resolve(intent("should i take this job offer?")) == [ExecutionMode.BALANCED]  # only that turn


def test_a_real_turn_carries_the_deliberate_modes(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    # The real router reads the sentence, the real mode manager decides the depth (the path a turn takes).
    deliberate = rt.modes.resolve(rt.intent_router.route("why do some startups fail? think it through", rt.session))
    assert {ExecutionMode.DEEP, ExecutionMode.ADVERSARIAL} <= set(deliberate) and ExecutionMode.FAST not in deliberate
    plain = rt.modes.resolve(rt.intent_router.route("what is the capital of france?", rt.session))
    assert ExecutionMode.ADVERSARIAL not in plain


def test_deliberate_turns_get_self_questioning():
    from app.inquiry import SelfInquiry

    inquiry = SelfInquiry(executive=None)
    assert inquiry.eligible("why did the roman empire fall in the west really", {"DEEP", "ADVERSARIAL"})
    assert not inquiry.eligible("why did the roman empire fall in the west really", {"FAST"})
