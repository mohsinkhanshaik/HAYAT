from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.assistant import AssistantReply, AssistantService, deliverable_for
from app.intent.models import IntentKind, RequestIntent, SuccessCriterion
from app.responses import AssistantResponse, DeliverableType, ResponseStatus
from app.turns import TurnLog, TurnStatus


def _intent(kind: IntentKind, criteria=()):
    return RequestIntent(kind, 0.9, "goal", success_criteria=list(criteria))


def _service():
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(
        session=SimpleNamespace(session_id="s1"), events=None, proactive_mode="SUGGEST",
    )
    return service


# --------------------------------------------------------- deliverable mapping


@pytest.mark.parametrize(
    "kind, expected",
    [
        (IntentKind.CONVERSATION, DeliverableType.CONVERSATION),
        (IntentKind.QUESTION, DeliverableType.ANSWER),
        (IntentKind.RESEARCH, DeliverableType.RESEARCH_RESULT),
        (IntentKind.SYSTEM_STATE, DeliverableType.SYSTEM_STATUS),
        (IntentKind.DIAGNOSTIC, DeliverableType.DIAGNOSTIC),
        (IntentKind.MEMORY, DeliverableType.MEMORY_RESULT),
        (IntentKind.COMPUTER_CONTROL, DeliverableType.ACTION_RESULT),
    ],
)
def test_request_classes_map_to_their_real_deliverable(kind, expected):
    """Defaulting everything to CONVERSATION is what let a camera request and a
    research request both answer "Done. I verified the requested result."."""
    assert deliverable_for(_intent(kind)) is expected


def test_success_criteria_refine_the_deliverable():
    camera = _intent(IntentKind.COMPUTER_CONTROL, [SuccessCriterion("camera_observation", "d")])
    assert deliverable_for(camera) is DeliverableType.CAMERA_OBSERVATION

    screen = _intent(IntentKind.COMPUTER_CONTROL, [SuccessCriterion("vision_interpretation", "d")])
    assert deliverable_for(screen) is DeliverableType.SCREEN_OBSERVATION

    files = _intent(IntentKind.ACTION, [SuccessCriterion("file_exists", "d")])
    assert deliverable_for(files) is DeliverableType.FILE_RESULT


# ------------------------------------------------------------ response is truth


def test_reply_reads_through_to_the_response_rather_than_copying_it():
    """One source of truth: the legacy view must not hold a second copy that
    can drift from the response it describes."""
    response = AssistantResponse(
        display_text="I can see you.", status=ResponseStatus.SUCCESS,
        deliverable_type=DeliverableType.CAMERA_OBSERVATION, task_id="task-1",
    )
    reply = AssistantReply(response, _intent(IntentKind.COMPUTER_CONTROL))

    assert reply.display_response == "I can see you."
    assert reply.task_id == "task-1"
    assert reply.deliverable_type is DeliverableType.CAMERA_OBSERVATION

    response.display_text = "changed upstream"
    assert reply.display_response == "changed upstream", "the view must not cache"


def test_an_empty_deliverable_cannot_be_reported_as_success():
    """A research or camera request that produced nothing is PARTIAL, never a
    success with an empty body."""
    reply = _service()._build_reply(_intent(IntentKind.RESEARCH), "", complete=True)
    assert reply.status is ResponseStatus.PARTIAL
    assert reply.response.metadata["missing_deliverable"] is True
    assert reply.display_response.strip()


def test_conversation_may_legitimately_be_brief():
    reply = _service()._build_reply(_intent(IntentKind.CONVERSATION), "Hi.", complete=True)
    assert reply.status is ResponseStatus.SUCCESS
    assert reply.deliverable_type is DeliverableType.CONVERSATION


def test_failed_outcomes_report_failed_status():
    reply = _service()._build_reply(_intent(IntentKind.RESEARCH), "I couldn't complete that.", complete=False)
    assert reply.status is ResponseStatus.FAILED
    assert reply.complete is False


def test_waiting_approval_is_its_own_status():
    outcome = SimpleNamespace(needs_user_input=True, complete=None)
    reply = _service()._build_reply(
        _intent(IntentKind.COMPUTER_CONTROL), "Save or discard?", complete=None, outcome=outcome,
    )
    assert reply.status is ResponseStatus.WAITING_APPROVAL


# --------------------------------------------------------------- final claiming


def test_a_superseded_turn_result_is_suppressed_not_shown():
    """The stale-completion bug end to end: work the user replaced finishes
    late and must not reach the screen."""
    log = TurnLog()
    turn = log.start(session_id="s1", runtime_id="r1", raw_input="search A", input_source="text")
    turn.transition(TurnStatus.NORMALIZED)
    turn.transition(TurnStatus.ROUTED)
    turn.transition(TurnStatus.RUNNING)
    turn.transition(TurnStatus.SUPERSEDED)

    service = _service()
    reply = service._build_reply(_intent(IntentKind.RESEARCH), "stale answer about A", complete=True)
    assert turn.claim_final() is False

    suppressed = service._suppressed_reply(reply)
    assert suppressed.display_response == ""
    assert suppressed.spoken_response == ""
    assert suppressed.status is ResponseStatus.CANCELLED
    assert suppressed.response.metadata["suppressed"] is True


def test_a_new_request_supersedes_in_flight_work():
    log = TurnLog()
    first = log.start(session_id="s1", runtime_id="r1", raw_input="search A", input_source="text")
    first.transition(TurnStatus.NORMALIZED)
    first.transition(TurnStatus.ROUTED)
    first.transition(TurnStatus.RUNNING)

    superseded = log.supersede_active()

    assert superseded == [first.turn_id]
    assert first.status is TurnStatus.SUPERSEDED
    assert first.claim_final() is False
