"""Deciding what a turn is worth, instead of giving every turn the same."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.context_budget import (
    ANSWERS, MAXIMUM_CONTEXT, MINIMUM_CONTEXT, SHAPES,
    ContextBudget, estimate_tokens, fit_to_budget,
)


@pytest.mark.parametrize('window', [2048, 4096, 8192, 16384])
def test_deep_prompt_and_output_share_the_declared_window(window):
    from app.models.intelligent_router import BoundedFallbackProvider
    from app.models.escalation import CappedProvider
    inner = SimpleNamespace(model='small', context_length=window)
    wrapped = CappedProvider(BoundedFallbackProvider([inner, SimpleNamespace(model='big', num_ctx=32768)]), 4000)
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(7 * 1024**3))).decide('analyze this in depth', model=wrapped)
    assert budget.context_tokens <= window
    assert budget.prompt_tokens + budget.answer_tokens + min(1024, window // 4) <= window
    inner.context_length = 1024
    assert wrapped.context_length == 1024


def test_final_prompt_fitting_preserves_instructions_and_current_request():
    from app.context_budget import fit_model_messages
    from app.models.base import Message, ModelError
    messages = [Message('system', 'Follow runtime evidence.'), Message('user', 'older ' * 200),
                Message('assistant', 'prior answer ' * 200), Message('user', 'Check this acquisition evidence.')]
    fitted = fit_model_messages(messages, 40)
    assert fitted == [messages[0], messages[-1]]
    with pytest.raises(ModelError, match='exceed'):
        fit_model_messages([messages[0], Message('user', 'large ' * 300)], 40)


# ------------------------------------------------------------- what it is


def _hardware(free_vram: float, free_ram: float = 8 * 1024**3):
    return SimpleNamespace(gpu=SimpleNamespace(free_bytes=free_vram),
                           available_ram_bytes=free_ram)


@pytest.mark.parametrize("text,shape", [
    ("hey", "greeting"),
    ("thanks", "greeting"),
    ("ok", "greeting"),
    # Begins with "how" and wants none of what a technical question wants. It
    # was getting an eight-thousand-token window and six recalled memories to
    # answer "good, you?", which is how a pleasantry gets answered with last
    # week's screen contents.
    ("how are you doing today", "greeting"),
    ("so how is it going", "greeting"),
    ("what's up", "greeting"),
    ("what is the difference between WAL and rollback journal", "question"),
    ("who wrote this file", "question"),
    ("explain how rust ownership and borrowing fit together", "deep"),
    ("compare sqlite and postgres for this workload", "deep"),
    ("open chrome and search for flights", "task"),
    ("run the tests and fix whatever breaks", "task"),
    ("", "chat"),
])
def test_a_turn_is_recognised_for_what_it_is(text, shape):
    assert ContextBudget.classify(text) == shape


def test_thinking_modes_keep_depth_for_substance_and_greetings_small():
    assert ContextBudget.classify("hey", modes={"DEEP", "ADVERSARIAL"}) == "greeting"
    assert ContextBudget.classify("hey", modes={"TEACH"}) == "greeting"
    assert ContextBudget.classify("and that one", modes={"DEEP"}) == "deep"


def test_a_long_thread_makes_a_short_remark_worth_more():
    """Eleven exchanges in, a short remark is rarely a fresh start."""
    history = [{"role": "user", "content": "x"}] * 14
    assert ContextBudget.classify("and that one", history=history) == "question"
    assert ContextBudget.classify("and that one") == "chat"


# ----------------------------------------------------------- what it spends


def test_bigger_turns_get_bigger_budgets():
    # Given a machine with room, so this measures the decision rather than
    # whatever the real GPU happens to have free while the suite runs.
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(7 * 1024**3), model=None))
    sizes = [budget.decide(text).context_tokens for text in
             ("hey", "tell me about it", "what is a b-tree", "run the tests")]
    assert sizes[0] < sizes[1] < sizes[2] < sizes[3]


def test_a_greeting_recalls_nothing():
    """A hello retrieving eight memories is how an ordinary greeting ends up
    answered with last week's screen contents."""
    decision = ContextBudget().decide("hey")
    assert decision.memory_items == 0
    assert decision.history_turns <= 4


def test_the_answer_ceiling_always_fits_inside_the_window():
    """A ceiling larger than the window asks for an answer that cannot be
    delivered."""
    budget = ContextBudget()
    for text in ("hey", "what is a b-tree", "explain lifetimes", "run the tests"):
        decision = budget.decide(text)
        assert decision.answer_tokens < decision.context_tokens


def test_every_shape_has_both_a_window_and_a_ceiling():
    assert set(SHAPES) == set(ANSWERS)


# ------------------------------------------------------ what the machine says


def test_a_busy_gpu_shrinks_the_window_and_says_so():
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(400 * 1024**2), model=None))
    decision = budget.decide("explain how rust lifetimes work")

    assert decision.context_tokens < SHAPES["deep"]
    assert decision.constrained_by == "little free VRAM, cache spills to RAM"
    assert any("VRAM" in reason for reason in decision.reasons)


def test_the_model_resident_case_still_gets_room_for_prompt_and_reply():
    """The bug Mohsin hit: model resident, ~1.3 GiB VRAM free, a long

    follow-up truncated mid-word. With plenty of RAM to spill the cache into,
    the low-VRAM branch must offer a window big enough to hold the prompt and
    a full reply -- not the old 8192 that jammed them together.
    """
    budget = ContextBudget(SimpleNamespace(
        hardware=_hardware(1.3 * 1024**3, 40 * 1024**3), model=None))
    decision = budget.decide("explain everything in detail with all the steps")
    assert decision.context_tokens > 8192
    assert decision.context_tokens <= 16384
    # And the reply is actually given room inside that window.
    assert decision.answer_tokens >= 4096


def test_a_free_gpu_leaves_the_budget_alone():
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(7 * 1024**3), model=None))
    assert budget.decide("explain how rust lifetimes work").context_tokens == SHAPES["deep"]


def test_the_ceiling_is_a_real_number_not_arithmetic_nobody_checked():
    """This first reported six million tokens, which is not a ceiling."""
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(8 * 1024**3, 40 * 1024**3),
                                           model=None))
    ceiling = budget.status()["machine_ceiling"]
    assert MINIMUM_CONTEXT <= ceiling <= MAXIMUM_CONTEXT


def test_a_model_that_accepts_less_is_believed():
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(7 * 1024**3)))
    decision = budget.decide("explain how rust lifetimes work",
                             model=SimpleNamespace(context=4096))
    assert decision.context_tokens == 4096
    assert decision.constrained_by == "the model's own window"


def test_nothing_ever_drops_below_the_floor():
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(1, 1)))
    assert budget.decide("hey").context_tokens >= MINIMUM_CONTEXT


def test_a_machine_it_cannot_read_does_not_stop_it_deciding():
    budget = ContextBudget(SimpleNamespace(hardware=SimpleNamespace()))
    assert budget.decide("what is a b-tree").context_tokens > 0


# --------------------------------------------------------------- explaining


def test_every_decision_carries_its_reasons():
    """A budget that silently shrinks is indistinguishable from a model that
    has started forgetting."""
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(300 * 1024**2), model=None))
    decision = budget.decide("explain how rust lifetimes work")

    assert decision.reasons
    assert "deep" in decision.describe()
    assert "limited by" in decision.describe()
    assert decision.to_dict()["constrained_by"]


# ------------------------------------------------------------------ fitting


def test_what_gets_dropped_is_the_oldest():
    """Dropping the most recent turn to keep an older one is how an assistant
    loses the thread it is in."""
    items = [f"item {index} " + "x" * 100 for index in range(20)]
    kept = fit_to_budget(items, budget_tokens=100)

    assert kept, "everything was dropped"
    assert kept[-1] == items[-1]
    assert len(kept) < len(items)


def test_one_oversized_item_is_still_kept():
    """Returning nothing at all is worse than returning one thing too big."""
    assert fit_to_budget(["x" * 100_000], budget_tokens=10) == ["x" * 100_000]


def test_token_estimates_are_roughly_right():
    assert estimate_tokens("") == 0
    assert 20 <= estimate_tokens("word " * 25) <= 40


# ------------------------------------------------------------- in the runtime


def test_the_conversation_uses_the_budget_it_is_given():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False)
    try:
        assert runtime.context_budget is not None
        assert runtime.conversation.budget is runtime.context_budget
        # It reads the real machine, so the ceiling must be sane rather than
        # the six-million-token answer the first arithmetic produced.
        ceiling = runtime.context_budget.status()["machine_ceiling"]
        assert ceiling is None or MINIMUM_CONTEXT <= ceiling <= MAXIMUM_CONTEXT
    finally:
        runtime.close()


# ----------------------------------------------- room for the reply, and history


def test_output_space_is_reserved_not_borrowed():
    """Prompt and reply share one window. Without reserving, a long
    conversation fills it and the reply is squeezed into what remains --
    truncation caused by the prompt rather than by the model."""
    budget = ContextBudget(SimpleNamespace(hardware=_hardware(7 * 1024**3), model=None))
    decision = budget.decide("explain how rust lifetimes work")

    assert decision.prompt_tokens > 0
    assert decision.answer_tokens + decision.prompt_tokens <= decision.context_tokens
    assert any("reserved for the reply" in reason for reason in decision.reasons)


def test_a_long_conversation_is_folded_rather_than_forgotten():
    """Dropping old turns is the cheap option and it is why an assistant
    forgets what you told it twenty minutes ago."""
    from app.context_budget import compress_history

    history = [{"role": "user" if index % 2 == 0 else "assistant",
                "content": f"turn {index} " + "x" * 400} for index in range(40)]
    kept = compress_history(history, budget_tokens=800, keep_recent=6)

    assert len(kept) < len(history)
    # The live thread survives whole.
    assert kept[-1] == history[-1]
    assert kept[-6:] == history[-6:]
    # And the older half is present as a recap rather than absent.
    assert kept[0]["role"] == "system"
    assert "Earlier in this conversation" in kept[0]["content"]
    # The opening survives. How a conversation started is often where Mohsin
    # said what he actually wanted, and keeping only the tail discards it.
    assert "turn 0" in kept[0]["content"]
    # As does the most recent of what came before the live thread.
    assert "turn 33" in kept[0]["content"] or "turn 32" in kept[0]["content"]


def test_a_conversation_that_fits_is_left_exactly_as_it_is():
    from app.context_budget import compress_history

    history = [{"role": "user", "content": "short"} for _ in range(5)]
    assert compress_history(history, budget_tokens=10_000) == history


def test_the_recap_shrinks_before_the_live_turns_do():
    from app.context_budget import compress_history

    history = [{"role": "user", "content": f"turn {index} " + "y" * 900}
               for index in range(30)]
    kept = compress_history(history, budget_tokens=400, keep_recent=4)
    assert kept[-4:] == history[-4:], "the live thread was sacrificed"
