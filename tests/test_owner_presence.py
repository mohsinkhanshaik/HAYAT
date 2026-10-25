"""Owner presence is a yes/no Windows Hello check -- never a lookup.

The real WinRT prompt is interactive and hardware-bound, so these tests stand
in for the two raw calls and check the mapping, the guards, and that the two
registered tools run read-only.
"""
from __future__ import annotations

from app.owner_presence import OwnerPresence
from app.safety.permissions import PermissionManager
from app.tools.owner_verification import register_owner_verification_tools
from app.tools.registry import ToolRegistry


class _Fake(OwnerPresence):
    """An OwnerPresence whose WinRT calls are replaced by fixed codes."""

    def __init__(self, availability: int, result: int = 0) -> None:
        self._error = None
        self._verifier = object()
        self._availability = availability
        self._result = result
        self.prompts: list[str] = []

    def _check_availability(self) -> int:
        return self._availability

    def _request(self, message: str) -> int:
        self.prompts.append(message)
        return self._result


def test_status_reports_ready_when_hello_is_available():
    status = _Fake(availability=0).status()
    assert status == {"available": True, "reason": "Windows Hello is set up and ready.", "code": 0, "provider": "windows_hello"}


def test_status_explains_when_hello_is_not_configured():
    status = _Fake(availability=2).status()
    assert status["available"] is False
    assert "not set up" in status["reason"]


def test_verify_passes_on_a_verified_result_and_shows_only_the_reason_line():
    presence = _Fake(availability=0, result=0)
    out = presence.verify("Approve the wallet payout")
    assert out["verified"] is True and out["reason"] == "Verified."
    assert presence.prompts == ["Approve the wallet payout"]  # the reason is all that is sent to the prompt


def test_verify_reports_a_cancelled_prompt_as_not_verified():
    out = _Fake(availability=0, result=6).verify()
    assert out["verified"] is False and "cancelled" in out["reason"].lower()
    assert out["prompt"]  # a default line is shown when the caller gives none


def test_verify_does_not_prompt_when_hello_is_unavailable():
    presence = _Fake(availability=1, result=0)
    out = presence.verify("do a thing")
    assert out["verified"] is False and presence.prompts == []  # never prompted
    assert "no fingerprint reader" in out["reason"]


def test_non_windows_is_reported_not_crashed(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    presence = OwnerPresence()
    assert presence.status()["available"] is False
    assert presence.verify()["verified"] is False


def test_tools_are_read_only_and_return_the_presence_result(tmp_path):
    registry = ToolRegistry(PermissionManager({"default": "DENY", "permissions": {"system_read": "ALLOW"}}, tmp_path))
    register_owner_verification_tools(registry, _Fake(availability=0, result=0))
    names = set(registry.names())
    assert {"verify_owner_present", "owner_verification_status"} <= names
    verified = registry.execute("verify_owner_present", {"reason": "confirm it's you"})
    assert verified.success and verified.output["verified"] is True
    status = registry.execute("owner_verification_status", {})
    assert status.success and status.output["available"] is True
