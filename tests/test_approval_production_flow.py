"""Approval invariants proven through production dispatch, not just the lifecycle.

The lifecycle's own unit tests show that a cancelled or wrong-task resolution
returns None. That is necessary but not sufficient: the question that decides
whether the user is actually protected is whether `ToolRegistry.execute()` --
the single choke point every tool call passes through -- honours it. These
tests drive the real registry with a real AuthorityManager and assert on
whether the handler ran.
"""
from __future__ import annotations

import pytest

from app.execution import ExecutionContext, current_context, execution_scope
from app.safety.approval_records import ApprovalLifecycle, ApprovalState
from app.safety.authority import AuthorityManager
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


class Recorder:
    """A tool whose only job is to say whether it was allowed to run."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **kwargs) -> dict:
        self.calls += 1
        return {"ran": True}


def _registry(answer=lambda *a, **k: True) -> tuple[ToolRegistry, Recorder, ApprovalLifecycle]:
    """A registry wired exactly as the runtime wires it, minus the runtime."""
    authority = AuthorityManager(owner_mode=True)
    lifecycle = ApprovalLifecycle()
    authority.approvals_lifecycle = lifecycle
    registry = ToolRegistry(PermissionManager(), answer)
    registry.authority = authority
    handler = Recorder()
    # external_post is PROHIBITED-adjacent protected work: owner mode does not
    # auto-approve it, so it reaches the human every time.
    registry.register(
        "post_externally", "post something outward",
        {"type": "object", "properties": {"text": {"type": "string"}}}, "high",
        "external_post", handler,
    )
    return registry, handler, lifecycle


def _requires_approval(registry: ToolRegistry) -> bool:
    from app.safety.authority import ActionRequest, Decision
    decision = registry.authority.evaluate(ActionRequest(tool="post_externally", category="external_post"))
    return decision.decision is Decision.REQUIRE_APPROVAL


# --------------------------------------------------------- the mandatory one


def test_approval_never_binds_the_session_s_last_task():
    """The interrupted edit's bug, as a permanent property.

    A correlation source that reads "the task the session finished last" names
    Task A while Task B is the one actually executing. The record must carry
    Task B -- and if the task is genuinely unknown, None. Never Task A.
    """
    registry, handler, lifecycle = _registry()
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    # A supplier of exactly the shape the interrupted session used, still
    # advertising the stale task. Production supplies no task id at all, but
    # the registry must be safe even if some caller reintroduces one.
    registry.context_supplier = lambda: {
        "session_id": "session-1", "turn_id": "turn-2", "task_id": "task-A-finished-earlier",
    }

    with execution_scope(task_id="task-B-running-now"):
        registry.execute("post_externally", {"text": "hello"})

    record = lifecycle.get(lifecycle.pending()[0]["approval_id"]) if lifecycle.pending() else None
    record = record or next(iter(_all_records(lifecycle)))
    assert record.task_id == "task-B-running-now"
    assert record.task_id != "task-A-finished-earlier"


def test_unknown_task_stays_none_rather_than_borrowing_a_stale_one():
    """No scope bound: the honest answer is None, not the supplier's guess."""
    registry, handler, lifecycle = _registry()
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")
    registry.context_supplier = lambda: {
        "session_id": "session-1", "turn_id": "turn-2", "task_id": "task-A-finished-earlier",
    }

    registry.execute("post_externally", {"text": "hello"})

    record = next(iter(_all_records(lifecycle)))
    assert record.task_id is None, "a guessed task id is worse than no task id"
    # Session and turn are still carried: those are safe to read from ambient
    # state, and losing them would make the audit trail poorer for no gain.
    assert record.session_id == "session-1"
    assert record.turn_id == "turn-2"


def test_a_bound_context_beats_the_legacy_supplier_entirely():
    registry, handler, lifecycle = _registry()
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")
    registry.context_supplier = lambda: {"session_id": "stale", "turn_id": "stale"}

    registry.execute(
        "post_externally", {"text": "hello"},
        context=ExecutionContext(session_id="session-9", turn_id="turn-9", task_id="task-9"),
    )

    record = next(iter(_all_records(lifecycle)))
    assert (record.session_id, record.turn_id, record.task_id) == ("session-9", "turn-9", "task-9")


# ------------------------------------------------- production dispatch gating


def test_approved_protected_action_executes_and_records_approval():
    registry, handler, lifecycle = _registry(answer=lambda *a, **k: True)
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    result = registry.execute("post_externally", {"text": "hello"})

    assert result.success and handler.calls == 1
    assert next(iter(_all_records(lifecycle))).state is ApprovalState.APPROVED


def test_denied_protected_action_does_not_execute():
    registry, handler, lifecycle = _registry(answer=lambda *a, **k: False)
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    result = registry.execute("post_externally", {"text": "hello"})

    assert not result.success and handler.calls == 0
    assert next(iter(_all_records(lifecycle))).state is ApprovalState.DENIED


def test_a_yes_arriving_after_cancellation_executes_nothing():
    """The historical failure, through the real dispatch path.

    The approval is cancelled while the human is still deciding -- the user
    took the task back. The human then says yes. The broker returns True, and
    it must not matter: the record it belonged to is terminal, so nothing runs.
    """
    registry, handler, lifecycle = _registry()

    def cancel_then_approve(*args, **kwargs) -> bool:
        # Whatever is pending at this moment is what the user took back.
        for pending in lifecycle.pending():
            lifecycle.cancel_for_task(pending["task_id"]) if pending["task_id"] else None
        lifecycle.cancel_for_turn("turn-live")
        return True

    registry.approval_callback = cancel_then_approve
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    with execution_scope(session_id="session-1", turn_id="turn-live", task_id="task-live"):
        result = registry.execute("post_externally", {"text": "hello"})

    assert handler.calls == 0, "a cancelled approval must not execute, however the human answered"
    assert not result.success
    assert "expired or was cancelled" in result.error
    assert next(iter(_all_records(lifecycle))).state is ApprovalState.CANCELLED


def test_a_yes_arriving_after_expiry_executes_nothing():
    registry, handler, lifecycle = _registry()
    lifecycle.DEFAULT_TTL_SECONDS = -1  # every record is born already expired
    registry.approval_callback = lambda *a, **k: True
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    result = registry.execute("post_externally", {"text": "hello"})

    assert handler.calls == 0, "an expired approval must not execute a late yes"
    assert not result.success
    assert next(iter(_all_records(lifecycle))).state is ApprovalState.EXPIRED


def test_approving_a_different_task_cannot_release_this_one():
    """Resolution is addressed to a record, not to the word "approve".

    While Task B's approval is pending, an answer aimed at Task A arrives.
    Task B must stay pending and must not execute.
    """
    registry, handler, lifecycle = _registry()
    other = lifecycle.open("post_externally", "other task's approval", task_id="task-A")

    def approve_the_other_task(*args, **kwargs) -> bool:
        resolved = lifecycle.resolve(True, task_id="task-A")
        assert resolved is other
        return False  # this task's own prompt was not answered yes

    registry.approval_callback = approve_the_other_task
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    with execution_scope(task_id="task-B"):
        result = registry.execute("post_externally", {"text": "hello"})

    assert handler.calls == 0
    assert not result.success
    assert other.state is ApprovalState.APPROVED
    task_b = [record for record in _all_records(lifecycle) if record.task_id == "task-B"]
    assert task_b and task_b[0].state is ApprovalState.DENIED


def test_ordinary_owner_work_raises_no_approval_record_at_all():
    """Approval plumbing must not reintroduce approval fatigue."""
    registry, _, lifecycle = _registry()
    handler = Recorder()
    registry.register(
        "open_app", "launch something",
        {"type": "object", "properties": {}}, "medium", "application_launch", handler,
    )

    result = registry.execute("open_app", {})

    assert result.success and handler.calls == 1
    assert not list(_all_records(lifecycle)), "owner-authorised work must not create approval records"


# ------------------------------------------------------------ context hygiene


def test_scope_unwinds_and_cannot_leak_into_later_work():
    assert current_context().task_id is None
    with execution_scope(session_id="s", turn_id="t", task_id="task-1"):
        assert current_context().task_id == "task-1"
        with execution_scope(task_id="task-2"):
            # The inner task inherits the turn without having to know it.
            assert current_context().task_id == "task-2"
            assert current_context().turn_id == "t"
        assert current_context().task_id == "task-1"
    assert current_context().task_id is None


def test_a_turn_boundary_clears_an_inherited_task():
    with execution_scope(task_id="task-old"):
        with execution_scope(session_id="s", turn_id="turn-new", task_id=None):
            assert current_context().task_id is None
            assert current_context().turn_id == "turn-new"


def test_a_new_thread_inherits_no_task():
    """Threads start empty, so a worker cannot silently reuse a caller's task."""
    import threading

    seen: list[str | None] = []
    with execution_scope(task_id="task-main"):
        thread = threading.Thread(target=lambda: seen.append(current_context().task_id))
        thread.start()
        thread.join()
    assert seen == [None]


# ------------------------------------------------------------ turn reporting


def _running_turn(bus=None):
    from app.turns import TurnLog, TurnStatus

    turns = TurnLog()
    turn = turns.start(session_id="s1", runtime_id="r1", raw_input="do it", input_source="text", bus=bus)
    turn.transition(TurnStatus.NORMALIZED)
    turn.transition(TurnStatus.ROUTED)
    turn.transition(TurnStatus.RUNNING)
    return turns, turn


def test_a_turn_blocked_on_a_human_reports_waiting_not_running():
    """"Working on it" and "waiting for you" are different facts."""
    from app.turns import TurnStatus

    turns, turn = _running_turn()
    lifecycle = ApprovalLifecycle(turns=turns)

    record = lifecycle.open("post_externally", "protected", turn_id=turn.turn_id)
    assert turn.status is TurnStatus.WAITING_APPROVAL

    lifecycle.resolve(True, approval_id=record.approval_id)
    assert turn.status is TurnStatus.RUNNING, "an answered turn goes back to working"


def test_a_turn_stays_waiting_until_the_last_approval_is_answered():
    from app.turns import TurnStatus

    turns, turn = _running_turn()
    lifecycle = ApprovalLifecycle(turns=turns)
    first = lifecycle.open("post_externally", "one", turn_id=turn.turn_id)
    second = lifecycle.open("post_externally", "two", turn_id=turn.turn_id)

    lifecycle.resolve(True, approval_id=first.approval_id)
    assert turn.status is TurnStatus.WAITING_APPROVAL, "another approval is still outstanding"

    lifecycle.resolve(True, approval_id=second.approval_id)
    assert turn.status is TurnStatus.RUNNING


def test_an_expired_approval_does_not_strand_the_turn():
    from app.turns import TurnStatus

    turns, turn = _running_turn()
    lifecycle = ApprovalLifecycle(turns=turns)
    lifecycle.open("post_externally", "protected", turn_id=turn.turn_id, ttl_seconds=-1)
    assert turn.status is TurnStatus.WAITING_APPROVAL

    assert lifecycle.resolve(True) is None
    assert turn.status is TurnStatus.RUNNING, "a lapsed approval must release the turn"


def test_a_cancelled_approval_releases_the_turn():
    from app.turns import TurnStatus

    turns, turn = _running_turn()
    lifecycle = ApprovalLifecycle(turns=turns)
    lifecycle.open("post_externally", "protected", turn_id=turn.turn_id, task_id="task-1")
    lifecycle.cancel_for_task("task-1")
    assert turn.status is TurnStatus.RUNNING


def test_a_finished_turn_is_never_dragged_back_into_waiting():
    """Approval bookkeeping must not resurrect a turn the user already saw end."""
    from app.turns import TurnStatus

    turns, turn = _running_turn()
    turn.transition(TurnStatus.COMPLETED)
    lifecycle = ApprovalLifecycle(turns=turns)

    lifecycle.open("post_externally", "late", turn_id=turn.turn_id)
    assert turn.status is TurnStatus.COMPLETED


def test_waiting_approval_reaches_the_event_bus(tmp_path):
    from app.events import EventBus

    bus = EventBus(tmp_path / "events.db")
    try:
        seen: list[str] = []
        for name in ("TURN_WAITING_APPROVAL", "TURN_RUNNING", "APPROVAL_REQUIRED"):
            bus.subscribe(name, lambda event: seen.append(event["event_type"]))
        turns, turn = _running_turn(bus=bus)
        lifecycle = ApprovalLifecycle(bus=bus, turns=turns)
        record = lifecycle.open("post_externally", "protected", turn_id=turn.turn_id)
        lifecycle.resolve(True, approval_id=record.approval_id)

        assert seen == ["TURN_RUNNING", "APPROVAL_REQUIRED", "TURN_WAITING_APPROVAL", "TURN_RUNNING"]
    finally:
        bus.close()


# ------------------------------------------------- through a real agent run


def test_a_real_agent_run_binds_its_own_task_not_the_session_s_last(tmp_path):
    """End to end: the Agent executes a plan while the session still points at
    an older finished task. The approval must name the task that is running.

    This is the interrupted edit's failure mode assembled from real parts --
    Agent, Executor, ToolRegistry, AuthorityManager, ApprovalLifecycle -- so
    the guarantee is a property of the system, not of a hand-built context.
    """
    from app.agent.core import Agent, TaskOutcome
    from app.agent.planner import PlanStep
    from app.agent.verifier import Verifier
    from app.memory.database import MemoryDatabase
    from app.session import SessionState

    registry, handler, lifecycle = _registry()
    if not _requires_approval(registry):
        pytest.skip("external_post is not gated in this policy build")

    session = SessionState()
    # The session's memory of the last completed task -- stale by construction.
    session.last_task = TaskOutcome("task-A-finished-earlier", "older goal", True, "", [])
    registry.context_supplier = lambda: {
        "session_id": session.session_id, "turn_id": session.active_turn_id,
    }

    class StubPlanner:
        knowledge = None
        last_knowledge_ids: list[str] = []
        last_direct_response = None

        def create_plan(self, goal, context=None, intent=None, session=None):
            return [PlanStep("post it", "post_externally", {"text": "hello"})]

        def replan(self, *args, **kwargs):
            return []

    from app.agent.executor import Executor

    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    agent = Agent(StubPlanner(), Executor(registry), Verifier(), memory)
    outcome = agent.run("post something", session=session)

    record = next(iter(_all_records(lifecycle)))
    assert record.task_id == outcome.task_id, "the approval must name the executing task"
    assert record.task_id != "task-A-finished-earlier"
    assert record.session_id == session.session_id
    assert handler.calls == 1


def _all_records(lifecycle: ApprovalLifecycle):
    return list(lifecycle._records)
