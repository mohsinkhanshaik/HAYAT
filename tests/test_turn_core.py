from __future__ import annotations

import pytest

from app.events import EventBus
from app.failures import FailureLog
from app.responses import (
    AssistantResponse,
    DeliverableType,
    MissingDeliverable,
    ResponseStatus,
)
from app.turns import IllegalTurnTransition, TurnLog, TurnRecord, TurnStatus


@pytest.fixture()
def bus(tmp_path):
    instance = EventBus(tmp_path / "events.db")
    yield instance
    instance.close()


# ------------------------------------------------------------- turn lifecycle


def test_a_normal_turn_walks_the_legal_path_and_emits_each_transition(bus):
    seen: list[str] = []
    for name in ("TURN_RECEIVED", "TURN_NORMALIZED", "TURN_ROUTED", "TURN_RUNNING", "TURN_COMPLETED"):
        bus.subscribe(name, lambda event: seen.append(event["event_type"]))

    log = TurnLog()
    turn = log.start(session_id="s1", runtime_id="r1", raw_input="hello", input_source="text", bus=bus)
    for status in (TurnStatus.NORMALIZED, TurnStatus.ROUTED, TurnStatus.RUNNING, TurnStatus.COMPLETED):
        turn.transition(status)

    assert seen == ["TURN_RECEIVED", "TURN_NORMALIZED", "TURN_ROUTED", "TURN_RUNNING", "TURN_COMPLETED"]
    assert turn.is_terminal
    assert turn.latency["total_ms"] >= 0


def test_turn_events_carry_correlation(bus):
    captured: list[dict] = []
    bus.subscribe("TURN_COMPLETED", captured.append)
    log = TurnLog()
    turn = log.start(session_id="s7", runtime_id="r7", raw_input="hi", input_source="voice", bus=bus)
    turn.task_id = "task-9"
    for status in (TurnStatus.NORMALIZED, TurnStatus.ROUTED, TurnStatus.COMPLETED):
        turn.transition(status)

    event = captured[0]
    assert event["session_id"] == "s7"
    assert event["turn_id"] == turn.turn_id
    assert event["task_id"] == "task-9"
    assert event["final"] is True


@pytest.mark.parametrize(
    "path, illegal",
    [
        ([TurnStatus.NORMALIZED, TurnStatus.ROUTED, TurnStatus.RUNNING, TurnStatus.COMPLETED], TurnStatus.RUNNING),
        ([TurnStatus.CANCELLED], TurnStatus.COMPLETED),
        ([TurnStatus.SUPERSEDED], TurnStatus.COMPLETED),
        ([TurnStatus.NORMALIZED, TurnStatus.ROUTED, TurnStatus.RUNNING, TurnStatus.FAILED], TurnStatus.COMPLETED),
    ],
)
def test_terminal_turns_cannot_be_resurrected(path, illegal):
    """A finished turn is finished. Completed cannot go back to running, and a
    cancelled or failed turn cannot quietly become a success."""
    turn = TurnRecord(session_id="s", runtime_id="r")
    for status in path:
        turn.transition(status)
    with pytest.raises(IllegalTurnTransition):
        turn.transition(illegal)


def test_only_one_visible_final_answer_per_turn():
    turn = TurnRecord(session_id="s", runtime_id="r")
    assert turn.claim_final() is True
    assert turn.claim_final() is False, "a turn must not present two final answers"


def test_a_superseded_turn_can_never_present_its_result():
    """The stale-result bug: the user replaced the request, the old task
    finished later, and its answer appeared as if it were current."""
    turn = TurnRecord(session_id="s", runtime_id="r")
    turn.transition(TurnStatus.NORMALIZED)
    turn.transition(TurnStatus.ROUTED)
    turn.transition(TurnStatus.RUNNING)
    turn.transition(TurnStatus.SUPERSEDED)

    assert turn.can_present_final is False
    assert turn.claim_final() is False


def test_a_cancelled_turn_can_never_present_its_result():
    turn = TurnRecord(session_id="s", runtime_id="r")
    turn.transition(TurnStatus.CANCELLED)
    assert turn.claim_final() is False


def test_newer_request_supersedes_in_flight_turns(bus):
    log = TurnLog()
    first = log.start(session_id="s", runtime_id="r", raw_input="search for A", input_source="voice", bus=bus)
    first.transition(TurnStatus.NORMALIZED)
    first.transition(TurnStatus.ROUTED)
    first.transition(TurnStatus.RUNNING)
    second = log.start(session_id="s", runtime_id="r", raw_input="no, search for B", input_source="voice", bus=bus)

    superseded = log.supersede_active(except_turn=second.turn_id)

    assert superseded == [first.turn_id]
    assert first.status is TurnStatus.SUPERSEDED
    assert second.status is TurnStatus.RECEIVED
    assert first.claim_final() is False
    assert second.claim_final() is True


# ------------------------------------------------------- response deliverables


def test_success_requires_the_deliverable_it_promised():
    """The structural end of generic "Done": a response type whose whole point
    is to hand something back cannot claim success while empty."""
    for deliverable in (
        DeliverableType.CAMERA_OBSERVATION,
        DeliverableType.RESEARCH_RESULT,
        DeliverableType.ANSWER,
        DeliverableType.FILE_RESULT,
        DeliverableType.DIAGNOSTIC,
        DeliverableType.ACTION_RESULT,
    ):
        with pytest.raises(MissingDeliverable):
            AssistantResponse(display_text="   ", status=ResponseStatus.SUCCESS, deliverable_type=deliverable)


def test_success_with_real_content_is_accepted():
    response = AssistantResponse(
        display_text="I can see you sitting in front of an open closet.",
        status=ResponseStatus.SUCCESS,
        deliverable_type=DeliverableType.CAMERA_OBSERVATION,
    )
    assert response.complete is True
    assert response.spoken_text == response.display_text


def test_a_response_cannot_be_success_and_carry_a_failure():
    with pytest.raises(MissingDeliverable):
        AssistantResponse(
            display_text="fine", status=ResponseStatus.SUCCESS,
            deliverable_type=DeliverableType.CONVERSATION, failure={"failure_id": "f1"},
        )


def test_non_success_statuses_may_be_empty():
    response = AssistantResponse.failure_response("The camera could not start.")
    assert response.status is ResponseStatus.FAILED
    assert response.complete is False
    assert response.deliverable_type is DeliverableType.ERROR


# ---------------------------------------------------------------- failure log


def test_failures_are_recorded_with_correlation_and_publish_an_event(bus):
    captured: list[dict] = []
    bus.subscribe("FAILURE_RECORDED", captured.append)
    log = FailureLog(bus)

    failure = log.from_exception(
        ConnectionRefusedError("[WinError 10061] connection refused"),
        stage="model", service="ollama", endpoint="http://localhost:11434",
        session_id="s1", turn_id="t1", user_request="hello there",
    )

    assert log.last is failure
    assert captured and captured[0]["turn_id"] == "t1"
    summary = failure.summary()
    assert "ollama" in summary
    assert "10061" in summary
    assert "http://localhost:11434" in summary
    assert failure.failure_id in summary


def test_last_failure_is_the_most_recent_one():
    log = FailureLog()
    log.record(stage="model", exception_message="first")
    second = log.record(stage="tool", exception_message="second")
    assert log.last is second
    assert len(log.recent(10)) == 2
