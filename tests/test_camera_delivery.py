from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.assistant import AssistantService
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.perception.camera import CameraManager, register_camera_tools
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.tools.registry import ToolRegistry, ToolResult
from app.voice.manager import SpokenResponseFormatter

OBSERVATION = {
    "observed_at": "2026-08-14T01:12:02+00:00",
    "device_index": 0,
    "frame": {"width": 1280, "height": 720, "bytes": 91234},
    "ocr": {"available": True, "text": ""},
    "vision": {"interpretation": "A person is seated in front of the camera in a dim room.", "model": "qwen3.5"},
    "raw_frame_retained": False,
}


def _camera_registry(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, Path(tmp_path)))
    register_camera_tools(registry, CameraManager({"device": 0}))
    return registry


def _outcome(output=OBSERVATION, tool="camera_observe"):
    return SimpleNamespace(
        complete=True, needs_user_input=False, summary="Task completed.", task_id="task-1",
        history=[{"tool": tool, "result": {"success": True, "output": output}}],
    )


def test_look_at_me_plans_a_real_capture_not_a_status_read(tmp_path):
    """Regression test for a live voice session: "Can you access the camera and
    look at me?" matched none of the planner's observe phrases and silently fell
    through to camera_status -- which never opens the device or captures a
    frame -- yet still reported success."""
    router, planner = IntentRouter(), Planner(_camera_registry(tmp_path))
    for phrase in ("Can you access the camera and look at me?", "Look at me."):
        intent = router.route(phrase, SessionState())
        assert intent.intent == IntentKind.COMPUTER_CONTROL
        assert [item.type for item in intent.success_criteria] == ["camera_observation"]
        plan = planner.create_plan(intent.goal, intent=intent, session=SessionState())
        assert [step.tool for step in plan] == ["camera_observe"]
        assert plan[0].expected["type"] == "camera_observation"


def test_camera_status_and_off_still_route_normally(tmp_path):
    router, planner = IntentRouter(), Planner(_camera_registry(tmp_path))
    for phrase, tool in (("Camera status.", "camera_status"), ("Turn the camera off.", "camera_off")):
        intent = router.route(phrase, SessionState())
        plan = planner.create_plan(intent.goal, intent=intent, session=SessionState())
        assert [step.tool for step in plan] == [tool]
        assert not intent.success_criteria


def test_camera_observation_verifier_requires_frame_and_interpretation():
    verifier = Verifier()
    assert verifier.check({"type": "camera_observation"}, ToolResult("camera_observe", True, OBSERVATION)).passed

    status_only = {"state": "OFF", "active": False, "devices_detected": 1}
    assert not verifier.check({"type": "camera_observation"}, ToolResult("camera_status", True, status_only)).passed

    no_vision = dict(OBSERVATION, vision={"available": False, "reason": "No local vision model is configured."}, ocr={"text": ""})
    assert not verifier.check({"type": "camera_observation"}, ToolResult("camera_observe", True, no_vision)).passed

    no_frame = dict(OBSERVATION, frame={"width": 0, "height": 0})
    assert not verifier.check({"type": "camera_observation"}, ToolResult("camera_observe", True, no_frame)).passed


def test_camera_request_delivers_the_observation_not_generic_done():
    intent = IntentRouter().route("Can you access the camera and look at me?", SessionState())
    service = AssistantService.__new__(AssistantService)
    service.runtime = SimpleNamespace(proactive_mode="SUGGEST")
    response = service._outcome_response(intent, _outcome())
    assert "seated in front of the camera" in response
    assert response != "Done. I verified the requested result."
    assert "raw frame retained: False" in response


def test_spoken_camera_response_reports_what_was_seen():
    response = SpokenResponseFormatter.format(_outcome(), "Can you access the camera and look at me?")
    assert "seated in front of the camera" in response.spoken_response
    assert response.spoken_response != "Done. The task completed successfully."
