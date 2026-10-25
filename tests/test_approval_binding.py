"""Approval grants must bind to tool, exact arguments, session, expiry, and one use."""

from __future__ import annotations


from app.safety.approvals import ApprovalBroker, CallBudget, fingerprint, normalize_arguments
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CountingCallback:
    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, tool: str, reason: str, arguments: dict) -> bool:
        self.calls.append((tool, dict(arguments)))
        return self.approve


def test_normalization_is_order_independent():
    assert normalize_arguments({"b": 1, "a": 2}) == normalize_arguments({"a": 2, "b": 1})
    assert fingerprint("t", {"a": 1, "b": 2}) == fingerprint("t", {"b": 2, "a": 1})


def test_approval_is_single_use_by_default():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    arguments = {"command": "Remove-Item x.txt"}
    assert broker.authorize("terminal", "destructive", arguments) is True
    assert broker.authorize("terminal", "destructive", arguments) is True
    assert len(callback.calls) == 2, "second invocation must ask again, not reuse the grant"


def test_explicit_multi_use_grant_is_consumed_then_expires():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    arguments = {"command": "Remove-Item x.txt"}
    broker.grant("terminal", arguments, uses=2, scope="task")
    assert broker.authorize("terminal", "r", arguments) is True
    assert broker.authorize("terminal", "r", arguments) is True
    assert callback.calls == [], "granted uses must not prompt"
    assert broker.authorize("terminal", "r", arguments) is True
    assert len(callback.calls) == 1, "third use must prompt again"


def test_grant_does_not_transfer_to_different_arguments():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    broker.grant("terminal", {"command": "Remove-Item a.txt"}, uses=5)
    broker.authorize("terminal", "r", {"command": "Remove-Item b.txt"})
    assert callback.calls, "different arguments must not reuse a grant"


def test_grant_does_not_transfer_to_a_different_tool():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    arguments = {"path": "notes.txt"}
    broker.grant("delete_file", arguments, uses=5)
    broker.authorize("write_file", "w", arguments)
    assert callback.calls, "a different tool must not reuse a grant"


def test_grant_does_not_transfer_across_sessions():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    broker.session_id = "session-a"
    arguments = {"command": "Remove-Item x.txt"}
    broker.grant("terminal", arguments, uses=5)
    broker.session_id = "session-b"
    broker.authorize("terminal", "r", arguments)
    assert callback.calls, "a new session must not inherit approvals"


def test_grant_expires_with_time():
    clock = Clock()
    callback = CountingCallback()
    broker = ApprovalBroker(callback, ttl_seconds=60, clock=clock)
    arguments = {"command": "Remove-Item x.txt"}
    broker.grant("terminal", arguments, uses=5)
    clock.advance(61)
    broker.authorize("terminal", "r", arguments)
    assert callback.calls, "an expired grant must prompt again"


def test_declined_approval_records_and_returns_false():
    broker = ApprovalBroker(CountingCallback(approve=False), clock=Clock())
    assert broker.authorize("terminal", "r", {"command": "x"}) is False
    assert broker.audit[-1]["allowed"] is False
    assert broker.audit[-1]["source"] == "user_declined"


def test_revoke_all_invalidates_outstanding_grants():
    callback = CountingCallback()
    broker = ApprovalBroker(callback, clock=Clock())
    broker.grant("terminal", {"command": "x"}, uses=5)
    assert broker.active_grants()
    assert broker.revoke_all() == 1
    assert broker.active_grants() == []


def test_audit_never_stores_raw_arguments():
    broker = ApprovalBroker(CountingCallback(), clock=Clock())
    broker.authorize("terminal", "r", {"command": "echo hunter2"})
    entry = broker.audit[-1]
    assert "hunter2" not in str(entry)
    assert len(entry["arguments_fingerprint"]) == 16


# ------------------------------------------------------------------ budgets


def test_call_budget_blocks_runaway_loops():
    budget = CallBudget(max_total_calls=5, max_calls_per_tool=3)
    for _ in range(3):
        assert budget.check("a") is None
        budget.consume("a")
    assert budget.check("a") is not None
    assert budget.check("b") is None


def test_call_budget_resets():
    budget = CallBudget(max_total_calls=2, max_calls_per_tool=2)
    budget.consume("a")
    budget.consume("a")
    assert budget.check("a") is not None
    budget.reset()
    assert budget.check("a") is None


def test_registry_enforces_budget_and_uses_broker(tmp_path):
    permissions = PermissionManager(
        {"default": "ASK", "permissions": {"filesystem_read": "ALLOW"}}, tmp_path
    )
    registry = ToolRegistry(permissions)
    registry.budget = CallBudget(max_total_calls=2, max_calls_per_tool=2)
    registry.register(
        "ping", "test tool", {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "filesystem_read", lambda: {"ok": True},
    )
    assert registry.execute("ping").success is True
    assert registry.execute("ping").success is True
    exhausted = registry.execute("ping")
    assert exhausted.success is False
    assert "budget" in exhausted.error.lower()
    assert exhausted.permission == "BUDGET"


def test_registry_ask_path_goes_through_broker(tmp_path):
    callback = CountingCallback()
    permissions = PermissionManager({"default": "ASK", "permissions": {}}, tmp_path)
    registry = ToolRegistry(permissions, callback)
    registry.register(
        "risky", "test tool", {"type": "object", "properties": {}, "additionalProperties": False},
        "high", "some_unknown_category", lambda: {"ok": True},
    )
    assert registry.execute("risky").success is True
    assert registry.execute("risky").success is True
    assert len(callback.calls) == 2, "each ASK invocation must be approved separately"


def test_registry_unattended_default_denies(tmp_path):
    permissions = PermissionManager({"default": "ASK", "permissions": {}}, tmp_path)
    registry = ToolRegistry(permissions)
    registry.register(
        "risky", "test tool", {"type": "object", "properties": {}, "additionalProperties": False},
        "high", "some_unknown_category", lambda: {"ok": True},
    )
    result = registry.execute("risky")
    assert result.success is False
    assert "approval" in result.error.lower()
