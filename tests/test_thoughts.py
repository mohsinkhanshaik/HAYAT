"""Watching HAYAT work, instead of waiting through a silent pause.

The spinner answers "is it stuck?". This answers "what is it actually doing?"
-- and it does so from events the work already publishes, so a line appearing
means the thing behind it genuinely happened.
"""
from __future__ import annotations

import pytest

from app.thoughts import ThoughtStream


class FakeBus:
    """Records subscriptions and lets a test fire events at them."""

    def __init__(self) -> None:
        self.handlers: dict[str, list] = {}

    def subscribe(self, name, handler):
        self.handlers.setdefault(name, []).append(handler)
        return lambda: self.handlers[name].remove(handler)

    def fire(self, name, payload=None):
        for handler in self.handlers.get(name, []):
            handler({"event_type": name, "payload": payload or {}})


@pytest.fixture
def stream() -> ThoughtStream:
    return ThoughtStream(FakeBus())


# ------------------------------------------------------------- narration


def test_it_narrates_a_plan_step_by_step(stream):
    bus = stream.events
    bus.fire("TASK_CREATED", {"goal": "write a note in Notepad"})
    bus.fire("TOOL_STARTED", {"tool": "launch_application", "arguments": {"application": "notepad"}})
    bus.fire("TOOL_COMPLETED", {"tool": "launch_application"})

    lines = stream.recent()
    assert any("planning" in line and "Notepad" in line for line in lines)
    assert any("using launch_application on notepad" in line for line in lines)
    assert any("launch_application done" in line for line in lines)


def test_a_failure_is_narrated_rather_than_hidden(stream):
    stream.events.fire("TOOL_FAILED", {"tool": "notepad_edit", "error": "window not found"})
    assert any("notepad_edit failed" in line and "window not found" in line
               for line in stream.recent())


def test_waiting_for_approval_is_visible(stream):
    """A pause because HAYAT is waiting should never look like a hang."""
    stream.events.fire("APPROVAL_REQUIRED", {"action": "external_post"})
    assert any("waiting for you" in line for line in stream.recent())


def test_events_it_has_no_phrasing_for_are_ignored(stream):
    stream.events.fire("SOME_INTERNAL_EVENT", {"x": 1})
    assert stream.recent() == []


def test_an_event_with_nothing_to_say_produces_no_line(stream):
    """An empty goal should not render as a bare "planning:"."""
    stream.events.fire("TASK_CREATED", {"goal": ""})
    assert stream.recent() == []


def test_long_values_are_shortened_for_reading(stream):
    stream.events.fire("RESEARCH_STARTED", {"query": "x" * 300})
    line = stream.recent()[0]
    assert len(line) < 120 and line.endswith("...")


# --------------------------------------------------------------- the switch


def test_narration_is_off_by_default_but_still_recorded():
    """Narration during a quick reply is noise; the record is still useful
    afterwards, which is what "what did you just do" reads."""
    stream = ThoughtStream(FakeBus())
    assert not stream.enabled

    emitted: list[str] = []
    from app import output

    original = output.get_output().emit
    output.get_output().emit = lambda text, **kw: emitted.append(text)
    try:
        stream.events.fire("TOOL_STARTED", {"tool": "web_search"})
    finally:
        output.get_output().emit = original

    assert emitted == [], "nothing should print while narration is off"
    assert stream.recent(), "but it should still be remembered"


def test_turning_it_on_prints_as_work_happens():
    stream = ThoughtStream(FakeBus(), enabled=True)
    emitted: list[str] = []
    from app import output

    original = output.get_output().emit
    output.get_output().emit = lambda text, **kw: emitted.append(text)
    try:
        stream.events.fire("TOOL_STARTED", {"tool": "web_search"})
    finally:
        output.get_output().emit = original

    assert emitted and "web_search" in emitted[0]


def test_the_switch_reports_its_own_state(stream):
    assert stream.set_enabled(True) is True
    assert stream.status()["narrating"] is True
    stream.set_enabled(False)
    assert stream.status()["narrating"] is False


# ----------------------------------------------------------------- reading


def test_the_plan_view_shows_only_the_steps(stream):
    bus = stream.events
    bus.fire("TASK_CREATED", {"goal": "do a thing"})
    bus.fire("MODEL_STARTED", {})
    bus.fire("TOOL_STARTED", {"tool": "search_files"})

    plan = stream.plan_only()
    assert any("planning" in line for line in plan)
    assert any("search_files" in line for line in plan)
    assert not any(line.strip() == "thinking" for line in plan)


def test_an_empty_stream_says_how_to_turn_it_on(stream):
    assert "/thinking on" in stream.report()


def test_history_is_bounded():
    stream = ThoughtStream(FakeBus(), limit=10)
    for index in range(50):
        stream.events.fire("TOOL_STARTED", {"tool": f"tool_{index}"})
    assert len(stream.recent(1000)) <= 10


def test_detach_stops_the_narration(stream):
    stream.detach()
    stream.events.fire("TOOL_STARTED", {"tool": "web_search"})
    assert stream.recent() == []


def test_safe_operational_listener_works_when_chat_narration_is_off(stream):
    observed = []
    unsubscribe = stream.add_listener(observed.append)

    stream.events.fire("RESEARCH_STARTED", {"query": "local memory indexes"})
    assert len(observed) == 1
    assert observed[0].event == "RESEARCH_STARTED"
    assert "local memory indexes" in observed[0].text
    assert stream.status()["listeners"] == 1

    unsubscribe()
    unsubscribe()  # interface teardown is allowed to be defensive
    stream.events.fire("RESEARCH_RESULT", {"count": 2})
    assert len(observed) == 1
    assert stream.status()["listeners"] == 0


# ------------------------------------------------------------- integration


def test_asking_in_words_reaches_the_thinking_view():
    from app.intent.router import IntentRouter
    from app.session import SessionState

    router = IntentRouter()
    for question in ("show me your thinking", "show your reasoning", "what did you just do",
                     "how did you do that", "let me see your plan"):
        assert router.route(question, SessionState()).control == "SHOW_THINKING", question


def test_the_runtime_records_thoughts_without_being_asked():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, interactive_approvals=False)
    try:
        assert runtime.thoughts is not None
        assert runtime.thoughts.status()["subscribed"] > 0
    finally:
        runtime.learner.request_stop("test")
        runtime.learner.stop()
        runtime.close()
