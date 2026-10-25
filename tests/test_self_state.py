from __future__ import annotations

from types import SimpleNamespace

from app.intent.models import IntentKind
from app.runtime import build_runtime


def test_self_state_questions_bypass_model_and_match_runtime():
    runtime = build_runtime(use_model=False)
    try:
        state = runtime.state.snapshot()
        assert state["provider"] is None
        assert state["active_task"] is None
        assert runtime.intent_router.route("What model are you using?", runtime.session).intent == IntentKind.SYSTEM_STATE
        assert "no active model" in runtime.assistant.handle("What model are you using?").display_response
        assert "not running an independent task" in runtime.assistant.handle("What are you working on?").display_response
        assert str(len(runtime.registry.names())) in runtime.assistant.handle("How many tools do you have?").display_response
    finally:
        runtime.close()


def test_hallucination_traps_return_absence_of_evidence():
    runtime = build_runtime(use_model=False)
    try:
        answers = [
            runtime.assistant.handle("What project are you secretly working on?").display_response,
            runtime.assistant.handle("Tell me what you did while I was away.").display_response,
            runtime.assistant.handle("Which website are you currently researching?").display_response,
            runtime.assistant.handle("What file did you just modify?").display_response,
        ]
        assert "not running an independent" in answers[0]
        assert "don't have evidence" in answers[1]
        assert "No website" in answers[2]
        assert "do not have evidence" in answers[3]
    finally:
        runtime.close()


def test_agent_state_tool_is_read_only_and_authoritative():
    runtime = build_runtime(use_model=False)
    try:
        result = runtime.registry.execute("get_agent_state")
        assert result.success
        assert result.output["claim_type"] == "SYSTEM_STATE"
        assert result.output["tool_count"] == len(runtime.registry.names())
        # The project has a 3TB ceiling, with a separate enforced 1TB learning
        # category and a 300GB physical free-space reserve.
        assert result.output["storage_limit"] == 3_000_000_000_000
    finally:
        runtime.close()


def test_an_unreadable_browser_page_does_not_take_the_turn_down():
    """Describing the system must never be what breaks it.

    Playwright's sync API belongs to the greenlet that created it, and reading
    `page.url` from anywhere else raises `greenlet.error: Cannot switch to a
    different thread`. An ordinary conversational turn asked for state, the
    snapshot touched the page from the wrong thread, and the whole turn died
    with a traceback instead of answering. A field that cannot be read is a
    field reported as unreadable.
    """
    class HostilePage:
        def is_closed(self):
            raise RuntimeError("Cannot switch to a different thread")

        @property
        def url(self):
            raise RuntimeError("Cannot switch to a different thread")

    runtime = build_runtime(use_model=False)
    try:
        runtime.state.browser = SimpleNamespace(page=HostilePage())
        state = runtime.state.snapshot()
        assert state["browser"]["readable"] is False
        assert state["browser"]["reason"] == "RuntimeError"
        assert state["claim_type"] == "SYSTEM_STATE"  # the rest still arrived
    finally:
        runtime.close()


def test_asked_which_tools_it_trusts_it_answers_with_more_than_a_count():
    """"What tools do you actually have, and which ones do you trust?" got
    "I currently have 91 registered tools." The second half was not refused,
    it was dropped -- a count was all that branch could produce."""
    runtime = build_runtime(use_model=False)
    try:
        answer = runtime.assistant.handle(
            "What tools do you actually have, and which ones do you trust?").display_response
        assert str(len(runtime.registry.names())) in answer
        assert "Never used" in answer or "not used any of them yet" in answer
    finally:
        runtime.close()
