"""Camera activation that always ends, and always leaves a record.

The property under test is not "the camera can be turned on" -- it already
could. It is that an activation cannot outlive its reason. A camera switched
on by a plan step and left on because the plan failed three steps later is
the exact shape of the thing HAYAT exists not to be.
"""
from __future__ import annotations

import time

import pytest

from app.perception.camera.session import (
    MAX_SESSION_SECONDS,
    CameraPrivacyError,
    CameraSessions,
)


class FakeCamera:
    """Records every open and close, so leaks are visible."""

    def __init__(self, fail_on: str = "") -> None:
        self.opens = 0
        self.closes = 0
        self.on_now = False
        self.fail_on = fail_on

    def on(self, device_index=None):
        if self.fail_on == "on":
            raise RuntimeError("device busy")
        self.opens += 1
        self.on_now = True
        return {"state": "ACTIVE"}

    def off(self):
        if self.fail_on == "off":
            raise RuntimeError("cannot release")
        self.closes += 1
        self.on_now = False
        return {"state": "OFF", "device_released": True}

    def observe(self, prompt="describe"):
        return {"interpretation": "a desk", "observed_at": "now"}


class FakeAuthority:
    def __init__(self, privacy_mode=False, allowed=True):
        self.privacy_mode = privacy_mode
        self._allowed = allowed

    def evaluate(self, request):
        from types import SimpleNamespace

        return SimpleNamespace(allowed=self._allowed, reason="denied by policy")


def _sessions(tmp_path, camera=None, authority=None):
    return CameraSessions(camera or FakeCamera(), authority=authority,
                          journal=tmp_path / "activations.jsonl")


# ----------------------------------------------------- it always turns off


def test_the_camera_is_released_when_the_block_ends(tmp_path):
    camera = FakeCamera()
    with _sessions(tmp_path, camera).activate("check the desk"):
        assert camera.on_now
    assert not camera.on_now
    assert camera.opens == 1 and camera.closes == 1


def test_the_camera_is_released_when_the_block_raises(tmp_path):
    """A plan that fails mid-step must not leave the light on."""
    camera = FakeCamera()
    with pytest.raises(ValueError):
        with _sessions(tmp_path, camera).activate("look"):
            raise ValueError("the plan fell over")
    assert not camera.on_now
    assert camera.closes == 1


def test_a_watchdog_closes_the_camera_if_the_caller_hangs(tmp_path):
    """Closing on the way out covers the ordinary and the exception case, but
    not a caller that blocks forever inside the block."""
    camera = FakeCamera()
    sessions = _sessions(tmp_path, camera)
    with sessions.activate("long look", seconds=1.0) as activation:
        time.sleep(1.8)
        assert activation.forced_off, "the watchdog should have fired"
    assert not camera.on_now


def test_a_release_failure_is_recorded_rather_than_swallowed(tmp_path):
    camera = FakeCamera(fail_on="off")
    sessions = _sessions(tmp_path, camera)
    with sessions.activate("look"):
        pass
    last = sessions.history()[-1]
    assert last["released"] is False
    assert "release" in (last["error"] or "")


def test_a_session_that_cannot_open_does_not_claim_to_be_active(tmp_path):
    sessions = _sessions(tmp_path, FakeCamera(fail_on="on"))
    with pytest.raises(RuntimeError):
        with sessions.activate("look"):
            pass
    assert sessions.active is None


# ------------------------------------------------------------- the bounds


def test_no_activation_can_outlast_the_hard_ceiling(tmp_path):
    """A caller asking for an hour gets the ceiling, not an hour."""
    camera = FakeCamera()
    sessions = _sessions(tmp_path, camera)
    with sessions.activate("look", seconds=99999) as activation:
        assert sessions.status()["maximum_seconds"] == MAX_SESSION_SECONDS
    assert activation.seconds < MAX_SESSION_SECONDS


def test_two_activations_cannot_overlap(tmp_path):
    """Concurrent sessions would mean one closing the other's device."""
    sessions = _sessions(tmp_path)
    with sessions.activate("first"):
        with pytest.raises(RuntimeError, match="already on"):
            with sessions.activate("second"):
                pass


# ------------------------------------------------------------- the gate


def test_privacy_mode_stops_the_camera_before_it_opens(tmp_path):
    """A switch that lets the light come on and merely declines to keep the
    picture is not a privacy switch."""
    camera = FakeCamera()
    sessions = _sessions(tmp_path, camera, FakeAuthority(privacy_mode=True))

    with pytest.raises(CameraPrivacyError, match="privacy mode"):
        with sessions.activate("look"):
            pass
    assert camera.opens == 0, "the device must never have been opened"


def test_an_authority_refusal_stops_activation(tmp_path):
    camera = FakeCamera()
    sessions = _sessions(tmp_path, camera, FakeAuthority(allowed=False))
    with pytest.raises(CameraPrivacyError):
        with sessions.activate("look"):
            pass
    assert camera.opens == 0


def test_status_says_whether_the_camera_could_come_on(tmp_path):
    blocked = _sessions(tmp_path, authority=FakeAuthority(privacy_mode=True)).status()
    assert blocked["may_activate"] is False
    assert "privacy mode" in blocked["reason"]


# ------------------------------------------------------------- the record


def test_every_activation_is_journalled_with_its_reason(tmp_path):
    """"Was the camera on, when, and why" should have an answer Mohsin can
    read rather than a reassurance he has to accept."""
    import json

    sessions = _sessions(tmp_path)
    with sessions.activate("checking whether Mohsin is at the desk"):
        pass

    lines = (tmp_path / "activations.jsonl").read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["reason"] == "checking whether Mohsin is at the desk"
    assert record["released"] is True
    assert record["started_at"] and record["ended_at"]


def test_activation_and_release_are_both_published(tmp_path):
    published: list[tuple[str, dict]] = []

    class Bus:
        def publish(self, event_type, source, payload, **kwargs):
            published.append((event_type, payload))

    sessions = CameraSessions(FakeCamera(), events=Bus(), journal=tmp_path / "a.jsonl")
    with sessions.activate("look"):
        pass

    events = [payload.get("event") for _, payload in published]
    assert "activated" in events and "released" in events


def test_an_unstated_reason_is_recorded_as_unstated_not_blank(tmp_path):
    sessions = _sessions(tmp_path)
    with sessions.activate("   "):
        pass
    assert sessions.history()[-1]["reason"] == "unstated"


# ---------------------------------------------------------- the common case


def test_look_opens_observes_and_closes(tmp_path):
    camera = FakeCamera()
    result = _sessions(tmp_path, camera).look("what is on the desk")

    assert result["observation"]["interpretation"] == "a desk"
    assert result["activation"]["released"] is True
    assert result["activation"]["frames"] == 1
    assert not camera.on_now
