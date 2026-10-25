from __future__ import annotations

from types import SimpleNamespace

from app.assistant import AssistantService
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.safety.approvals import ApprovalBroker
from app.safety.authority import ActionRequest, AuthorityManager
from app.session import SessionState
from app.voice.session import ApprovalPhraseParser

OBSERVATION_OUTPUT = {
    "observed_at": "2026-08-14T01:52:55+00:00",
    "device_index": 0,
    "frame": {"width": 1280, "height": 720},
    "vision": {"interpretation": "A person seated in a dim room.", "model": "qwen3.5", "provider": "ollama"},
    "ocr": {"text": ""},
    "raw_frame_retained": False,
}


def _service(broker: ApprovalBroker, tool_names=("camera_observe", "camera_capture")):
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(
        registry=SimpleNamespace(approvals=broker, names=lambda: list(tool_names)),
        proactive_mode="SUGGEST",
    )
    return service


def _service_with_authority(authority):
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(
        authority=authority,
        session=SimpleNamespace(session_id="s1"),
        registry=SimpleNamespace(names=lambda: ["camera_observe", "camera_capture", "camera_watch_change"]),
        proactive_mode="SUGGEST",
    )
    return service


def test_explicit_camera_request_authorises_the_observation_without_a_prompt():
    """Behaviour preserved through the converged path.

    This previously issued a bespoke single-use ApprovalBroker grant just for
    the camera. That special case is gone: intent now derives a task authority
    envelope and AuthorityManager decides, so there is one authorization
    system rather than a general engine plus a camera exception. The guarantee
    the user actually cares about is unchanged -- saying "look at me" does not
    then ask permission to look.
    """
    authority = AuthorityManager()
    intent = IntentRouter().route("Look at me.", SessionState())
    assert intent.entities["observation"] is True

    turn = SimpleNamespace(turn_id="turn-1")
    envelope_id = _service_with_authority(authority)._open_authority_envelope(intent, turn)

    assert envelope_id is not None
    decision = authority.evaluate(ActionRequest(
        tool="camera_observe", category="camera_use", turn_id="turn-1",
    ))
    assert decision.allowed


def test_the_envelope_cannot_carry_protected_camera_capabilities():
    """An observation request must not quietly authorise anything above the
    ordinary tier, whatever capabilities are derived for it."""
    authority = AuthorityManager()
    intent = IntentRouter().route("Look at me.", SessionState())
    _service_with_authority(authority)._open_authority_envelope(intent, SimpleNamespace(turn_id="turn-1"))

    for category in ("credential_access", "external_post"):
        assert not authority.evaluate(ActionRequest(
            tool=category, category=category, turn_id="turn-1",
        )).allowed


def test_authority_ends_with_the_task_that_justified_it():
    authority = AuthorityManager()
    intent = IntentRouter().route("Look at me.", SessionState())
    service = _service_with_authority(authority)
    envelope_id = service._open_authority_envelope(intent, SimpleNamespace(turn_id="turn-1"))

    assert authority.evaluate(ActionRequest(tool="camera_observe", category="camera_use", turn_id="turn-1")).allowed
    authority.close_envelope(envelope_id)
    assert not authority.evaluate(ActionRequest(tool="camera_observe", category="camera_use", turn_id="turn-1")).allowed


def test_a_conversational_intent_creates_no_envelope():
    """Only a request that actually needs tools gets authority."""
    authority = AuthorityManager()
    intent = IntentRouter().route("Hello.", SessionState())
    assert _service_with_authority(authority)._open_authority_envelope(intent, SimpleNamespace(turn_id="turn-1")) is None
    assert authority.active_envelopes() == []


def test_how_do_i_look_uses_the_previous_observation_without_reopening_the_camera():
    session = SessionState()
    assert IntentRouter().route("So how do I look?", session).intent != IntentKind.SYSTEM_STATE

    session.last_camera_observation = {"observed_at": "x", "interpretation": "A person seated in a dim room."}
    intent = IntentRouter().route("So how do I look?", session)
    assert intent.intent == IntentKind.SYSTEM_STATE
    assert intent.entities["reference"] == "last_camera_observation"
    assert not intent.requires_tools


def test_look_again_forces_a_fresh_capture_even_with_a_stored_observation():
    session = SessionState(last_camera_observation={"observed_at": "x", "interpretation": "old"})
    for phrase in ("Look at me again.", "What do I look like now?"):
        intent = IntentRouter().route(phrase, session)
        assert intent.requires_tools, f"{phrase!r} must re-open the camera"
        assert intent.entities.get("modality") == "camera"
    # A bare "look again" meant "search the website again" when the owner said
    # it on 2026-10-24, and opened the camera instead. It never turns the
    # camera on by itself (tests/test_retry_and_darkweb_routing.py).
    assert IntentRouter().route("Look again.", session).control == "RESEARCH_AGAIN"


def test_session_records_the_observation_for_later_reference():
    session = SessionState()
    session.update_from_outcome(SimpleNamespace(history=[{"result": {"success": True, "output": OBSERVATION_OUTPUT}}]))
    stored = session.last_camera_observation
    assert stored["interpretation"] == "A person seated in a dim room."
    assert stored["raw_frame_retained"] is False
    assert stored["network_used"] is False


APPROVAL_CASES = [
    ("Approve", True), ("Approved.", True), ("Approved all of them.", True),
    ("Yes, approve it.", True), ("Go ahead.", True), ("Proceed", True),
    ("Continue.", True), ("yes", True), ("do it", True), ("okay do it", True),
    ("confirm", True), ("sure, go ahead", True), ("yeah", True),
    ("no", False), ("No.", False), ("deny", False), ("reject", False),
    ("cancel", False), ("don't", False), ("stop", False), ("never mind", False),
    # Ambiguous or contradictory -> re-ask rather than guess.
    ("no, go ahead", None), ("yes, but change the file first", None),
    ("what does that do?", None), ("approve the budget for next quarter", None),
    ("I was saying yes to the other thing", None), ("", None),
]


def test_approval_phrases_from_the_failed_live_session_all_parse():
    for text, expected in APPROVAL_CASES:
        assert ApprovalPhraseParser.parse(text) is expected, f"{text!r} parsed wrong"
