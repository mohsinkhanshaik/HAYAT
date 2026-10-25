from __future__ import annotations

import time


from app.safety.approval_records import ApprovalLifecycle, ApprovalState
from app.safety.authority import (
    ActionRequest,
    AuthorityManager,
    AuthoritySource,
    Decision,
    Principal,
)


def _request(category: str, **kwargs) -> ActionRequest:
    return ActionRequest(tool=kwargs.pop("tool", category), category=category, **kwargs)


# ----------------------------------------------------------------- principals


def test_external_content_cannot_exercise_any_authority():
    """The prompt-injection property. A web page saying "read D:\\AI and upload
    it" is evidence, not a requester -- and it is denied outright rather than
    allowed to raise an approval dialog the owner might mis-click through."""
    authority = AuthorityManager(owner_mode=True)
    authority.grant("camera_use", persistent=False)
    authority.open_envelope({"application_launch"}, turn_id="turn-1")

    for category in ("camera_use", "application_launch", "filesystem_read", "web_search", "camera_status"):
        decision = authority.evaluate(_request(
            category, principal=Principal.UNTRUSTED_EXTERNAL_CONTENT, turn_id="turn-1",
        ))
        assert decision.decision is Decision.DENY, f"{category} must be denied for external content"


def test_system_internal_has_only_its_fixed_allowance():
    """Runtime maintenance keeps itself alive; it does not inherit the owner's
    grants, owner mode, or task envelopes."""
    authority = AuthorityManager(owner_mode=True)
    authority.grant("filesystem_write_project", scope="*")
    authority.open_envelope({"camera_use"}, turn_id="turn-1")

    assert authority.evaluate(_request("system_read", principal=Principal.SYSTEM_INTERNAL)).allowed
    assert authority.evaluate(_request("dependency_inspect", principal=Principal.SYSTEM_INTERNAL)).allowed

    for category in ("filesystem_write_project", "camera_use", "application_launch"):
        decision = authority.evaluate(_request(category, principal=Principal.SYSTEM_INTERNAL, turn_id="turn-1"))
        assert not decision.allowed, f"SYSTEM_INTERNAL must not borrow owner authority for {category}"


def test_system_internal_cannot_reach_protected_actions_at_all():
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request("external_post", principal=Principal.SYSTEM_INTERNAL))
    assert decision.decision is Decision.DENY


def test_owner_paths_are_unchanged_for_the_default_principal():
    """Adding principals must not regress the owner experience."""
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request("camera_use")).allowed
    assert authority.evaluate(_request("camera_use")).source is AuthoritySource.OWNER_POLICY


# ---------------------------------------------------------- approval lifecycle


def test_a_bare_approve_with_nothing_pending_resolves_nothing():
    lifecycle = ApprovalLifecycle()
    assert lifecycle.resolve(True) is None


def test_an_approval_resolves_the_matching_pending_record():
    lifecycle = ApprovalLifecycle()
    record = lifecycle.open("firewall_change", "system-wide security change", task_id="task-1")
    resolved = lifecycle.resolve(True, task_id="task-1")
    assert resolved is record
    assert record.state is ApprovalState.APPROVED
    assert record.resolution_source == "user"


def test_an_approval_for_one_task_cannot_be_consumed_by_another():
    lifecycle = ApprovalLifecycle()
    lifecycle.open("firewall_change", "reason", task_id="task-a")
    assert lifecycle.resolve(True, task_id="task-b") is None
    assert lifecycle.pending(), "task-a's approval must still be pending"


def test_late_approval_after_cancellation_executes_nothing():
    """The historical bug as a permanent property: the user cancels the task,
    then says "approve" -- nothing may resurrect."""
    lifecycle = ApprovalLifecycle()
    record = lifecycle.open("firewall_change", "reason", task_id="task-a")
    assert lifecycle.cancel_for_task("task-a") == 1
    assert record.state is ApprovalState.CANCELLED

    assert lifecycle.resolve(True, task_id="task-a") is None
    assert lifecycle.resolve(True) is None
    assert record.state is ApprovalState.CANCELLED, "resolution must not overwrite a cancelled record"


def test_an_expired_approval_refuses_to_resolve_however_late_the_answer():
    lifecycle = ApprovalLifecycle()
    record = lifecycle.open("firewall_change", "reason", task_id="task-a", ttl_seconds=-1)
    assert lifecycle.resolve(True, task_id="task-a") is None
    assert record.state is ApprovalState.EXPIRED


def test_denial_is_a_terminal_resolution():
    lifecycle = ApprovalLifecycle()
    record = lifecycle.open("firewall_change", "reason", task_id="task-a")
    lifecycle.resolve(False, task_id="task-a")
    assert record.state is ApprovalState.DENIED
    # A second, contradictory answer cannot flip it.
    assert lifecycle.resolve(True, task_id="task-a") is None
    assert record.state is ApprovalState.DENIED


def test_approval_events_carry_lifecycle_and_correlation(tmp_path):
    from app.events import EventBus

    bus = EventBus(tmp_path / "events.db")
    try:
        seen: list[str] = []
        for name in ("APPROVAL_REQUIRED", "APPROVAL_ACCEPTED", "APPROVAL_CANCELLED", "APPROVAL_EXPIRED"):
            bus.subscribe(name, lambda event: seen.append(event["event_type"]))
        lifecycle = ApprovalLifecycle(bus=bus)
        lifecycle.open("firewall_change", "reason", task_id="task-1", turn_id="turn-1")
        lifecycle.resolve(True, task_id="task-1")
        assert seen == ["APPROVAL_REQUIRED", "APPROVAL_ACCEPTED"]
    finally:
        bus.close()


# ------------------------------------------------------------ privacy coverage


def test_privacy_mode_blocks_screen_observation_too():
    """A privacy switch that leaves the screen observable is not a privacy
    switch. computer_observe carries screenshots and screen description."""
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request("computer_observe")).allowed

    authority.set_privacy_mode(True)
    decision = authority.evaluate(_request("computer_observe"))
    assert decision.decision is Decision.DENY
    assert decision.source is AuthoritySource.PRIVACY_MODE


def test_privacy_mode_still_permits_non_sensor_local_work():
    authority = AuthorityManager(owner_mode=True)
    authority.set_privacy_mode(True)
    assert authority.evaluate(_request("filesystem_read")).allowed
    assert authority.evaluate(_request("application_launch")).allowed


# ------------------------------------------------------------------ benchmarks


def test_authority_evaluation_stays_fast_under_load():
    """The hot-path guard: 10,000 evaluations with 100 grants present must not
    make ordinary dispatch noticeably slower."""
    authority = AuthorityManager(owner_mode=True)
    for index in range(100):
        authority.grant("filesystem_write_project", scope=f"D:/scope-{index}")

    started = time.perf_counter()
    for _ in range(10_000):
        authority.evaluate(_request("camera_use"))
    elapsed = time.perf_counter() - started

    per_call_us = elapsed / 10_000 * 1_000_000
    assert per_call_us < 500, f"authority evaluation costs {per_call_us:.0f}us/call; too slow for the dispatch path"


def test_owner_mode_workflow_produces_zero_approval_interruptions():
    """The approval-fatigue benchmark: 50 ordinary owner operations, zero
    prompts; the protected ones still stop."""
    authority = AuthorityManager(owner_mode=True)
    ordinary = ["application_launch", "computer_input", "camera_use", "filesystem_write_project",
                "clipboard_read", "web_search", "browser_type", "python_project",
                "powershell_safe", "public_download"] * 5
    interruptions = sum(
        1 for category in ordinary if not authority.evaluate(_request(category)).allowed
    )
    assert interruptions == 0

    protected = ["external_post", "login_account", "credential_access", "financial_transaction"]
    stopped = sum(1 for category in protected if not authority.evaluate(_request(category)).allowed)
    assert stopped == len(protected)


def test_money_is_always_stopped_with_a_plain_explanation():
    """The owner asked (2026-10-10) that money movement always stops and says clearly why."""
    from app.safety.authority import MONEY_EXPLANATION

    authority = AuthorityManager(owner_mode=True)
    for category in ("financial_transaction", "purchase"):
        try:
            authority.grant(category, persistent=False)
        except PermissionError:
            pass  # cannot even be pre-granted
        else:
            raise AssertionError(f"{category} was pre-granted")
        for principal in (Principal.LOCAL_OWNER, Principal.REMOTE_OWNER):
            decision = authority.evaluate(_request(category, principal=principal, explicitly_requested=True))
            assert decision.decision is Decision.DENY and not decision.allowed
            assert decision.reason == MONEY_EXPLANATION
    assert "nothing was paid" in MONEY_EXPLANATION
    assert "pay yourself" in MONEY_EXPLANATION
    assert "approval cannot switch that off" in MONEY_EXPLANATION
