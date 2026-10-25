"""Structured approval lifecycle: an approval is a record, not a boolean.

The callback model answered "may I?" with True/False and forgot the question.
That is how the historical failure modes happened: a stray "approve" with
nothing pending, an approval meant for one task consumed by another, and an
answer arriving after the user had already cancelled. Each of those needs the
approval to exist as an object with identity, binding, expiry, and a state
machine -- so a resolution can be checked against *which* approval it resolves,
and a stale one can refuse to resolve at all.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


_TERMINAL = {
    ApprovalState.APPROVED, ApprovalState.DENIED, ApprovalState.EXPIRED,
    ApprovalState.CANCELLED, ApprovalState.SUPERSEDED,
}


@dataclass
class ApprovalRecord:
    action: str
    reason: str = ""
    approval_id: str = field(default_factory=lambda: f"appr-{uuid.uuid4().hex[:12]}")
    principal: str = "LOCAL_OWNER"
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    risk_tier: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: float | None = None
    state: ApprovalState = ApprovalState.PENDING
    resolved_at: str | None = None
    resolution_source: str | None = None

    def is_pending(self, now: float | None = None) -> bool:
        if self.state is not ApprovalState.PENDING:
            return False
        if self.expires_at is not None and (now or time.time()) >= self.expires_at:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id, "action": self.action, "reason": self.reason[:500],
            "principal": self.principal, "session_id": self.session_id,
            "turn_id": self.turn_id, "task_id": self.task_id, "risk_tier": self.risk_tier,
            "created_at": self.created_at, "state": self.state.value,
            "resolved_at": self.resolved_at, "resolution_source": self.resolution_source,
            "expires_in_seconds": None if self.expires_at is None else max(0, round(self.expires_at - time.time())),
        }


class ApprovalLifecycle:
    """Owns every ApprovalRecord and the rules for resolving them."""

    DEFAULT_TTL_SECONDS = 120.0

    def __init__(self, bus: Any = None, store: Any = None, turns: Any = None) -> None:
        self._records: list[ApprovalRecord] = []
        self._lock = threading.RLock()
        self._bus = bus
        self._store = store
        # Optional TurnRegistry. When present, a turn blocked on a human
        # answer reports WAITING_APPROVAL instead of looking like it is still
        # working -- the difference between "thinking" and "waiting for you".
        self._turns = turns

    # ----------------------------------------------------------------- create

    def open(self, action: str, reason: str, *, session_id: str | None = None,
             turn_id: str | None = None, task_id: str | None = None,
             risk_tier: int | None = None, ttl_seconds: float | None = None) -> ApprovalRecord:
        record = ApprovalRecord(
            action=action, reason=reason, session_id=session_id, turn_id=turn_id,
            task_id=task_id, risk_tier=risk_tier,
            expires_at=time.time() + (ttl_seconds if ttl_seconds is not None else self.DEFAULT_TTL_SECONDS),
        )
        with self._lock:
            self._records.append(record)
            del self._records[:-200]
        self._publish("APPROVAL_REQUIRED", record)
        self._pause_turn(record)
        return record

    # ---------------------------------------------------------------- resolve

    def resolve(self, approved: bool, *, approval_id: str | None = None,
                task_id: str | None = None, source: str = "user") -> ApprovalRecord | None:
        """Resolve exactly one matching pending approval, or nothing.

        A bare "approve" with nothing pending resolves nothing -- the word is
        conversation, not authority. A resolution addressed to a specific task
        cannot land on a different task's approval, and an approval that has
        expired or was cancelled refuses to resolve however late the answer
        arrives. Returning None tells the caller nothing was authorised.
        """
        now = time.time()
        with self._lock:
            expired = self._sweep(now)
            candidates = [record for record in self._records if record.is_pending(now)]
            if approval_id is not None:
                candidates = [record for record in candidates if record.approval_id == approval_id]
            if task_id is not None:
                candidates = [record for record in candidates if record.task_id == task_id]
            if not candidates:
                self._announce_expired(expired)
                return None
            # Newest pending wins; anything older for the same task is stale.
            record = candidates[-1]
            record.state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
            record.resolved_at = datetime.now(UTC).isoformat()
            record.resolution_source = source
        self._announce_expired(expired)
        self._publish("APPROVAL_ACCEPTED" if approved else "APPROVAL_DENIED", record)
        if self._store is not None:
            try:
                self._store.record_audit(
                    "APPROVAL_ACCEPTED" if approved else "APPROVAL_DENIED",
                    capability=record.action, decision=record.state.value,
                    tier=record.risk_tier, source=source, reason=record.reason,
                )
            except Exception:
                log_swallowed(_log, "writing the approval audit record", level=logging.WARNING)
        self._resume_turn(record)
        return record

    def cancel_for_task(self, task_id: str, *, reason: str = "task cancelled") -> int:
        """Cancel pending approvals for a task the user took back.

        After this, a late "approve" cannot resurrect them: resolve() only ever
        touches PENDING records.
        """
        cancelled: list[ApprovalRecord] = []
        with self._lock:
            for record in self._records:
                if record.task_id == task_id and record.state is ApprovalState.PENDING:
                    record.state = ApprovalState.CANCELLED
                    record.resolved_at = datetime.now(UTC).isoformat()
                    record.resolution_source = reason
                    cancelled.append(record)
        for record in cancelled:
            self._publish("APPROVAL_CANCELLED", record)
            self._resume_turn(record)
        return len(cancelled)

    def cancel_for_turn(self, turn_id: str, *, reason: str = "turn superseded") -> int:
        """Cancel pending approvals raised by a turn the user replaced."""
        cancelled: list[ApprovalRecord] = []
        with self._lock:
            for record in self._records:
                if record.turn_id == turn_id and record.state is ApprovalState.PENDING:
                    record.state = ApprovalState.CANCELLED
                    record.resolved_at = datetime.now(UTC).isoformat()
                    record.resolution_source = reason
                    cancelled.append(record)
        for record in cancelled:
            self._publish("APPROVAL_CANCELLED", record)
            self._resume_turn(record)
        return len(cancelled)

    def _sweep(self, now: float) -> list[ApprovalRecord]:
        """Mark lapsed approvals. Caller announces them outside the lock.

        Publishing and turn transitions reach the event bus and the turn state
        machine; doing that while holding the approval lock would put unrelated
        subsystems behind it.
        """
        expired: list[ApprovalRecord] = []
        for record in self._records:
            if record.state is ApprovalState.PENDING and record.expires_at is not None and now >= record.expires_at:
                record.state = ApprovalState.EXPIRED
                record.resolved_at = datetime.now(UTC).isoformat()
                expired.append(record)
        return expired

    def _announce_expired(self, expired: list[ApprovalRecord]) -> None:
        for record in expired:
            self._publish("APPROVAL_EXPIRED", record)
            self._resume_turn(record)

    # ------------------------------------------------------------------ query

    def pending(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            expired = self._sweep(now)
            live = [record.to_dict() for record in self._records if record.is_pending(now)]
        self._announce_expired(expired)
        return live

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            return next((record for record in self._records if record.approval_id == approval_id), None)

    # ------------------------------------------------------------ turn state

    def _pause_turn(self, record: ApprovalRecord) -> None:
        """Show a turn as waiting on the human, not as still working.

        Only a RUNNING turn may pause: a turn that has already finished, been
        cancelled, or not yet started is not waiting on anything, and forcing
        a transition on it would be a lie the state machine correctly rejects.
        """
        self._move_turn(record.turn_id, waiting=True)

    def _resume_turn(self, record: ApprovalRecord) -> None:
        """Resume once nothing else in the turn is still awaiting an answer."""
        turn_id = record.turn_id
        if not turn_id:
            return
        with self._lock:
            now = time.time()
            still_waiting = any(
                other.turn_id == turn_id and other.is_pending(now) for other in self._records
            )
        if not still_waiting:
            self._move_turn(turn_id, waiting=False)

    def _move_turn(self, turn_id: str | None, *, waiting: bool) -> None:
        if self._turns is None or not turn_id:
            return
        from app.turns import TurnStatus

        try:
            turn = self._turns.get(turn_id)
            if turn is None or turn.is_terminal:
                return
            expected = TurnStatus.RUNNING if waiting else TurnStatus.WAITING_APPROVAL
            if turn.status is not expected:
                return
            turn.transition(TurnStatus.WAITING_APPROVAL if waiting else TurnStatus.RUNNING)
        except Exception:
            # Approval truth must not depend on turn bookkeeping succeeding.
            log_swallowed(_log, "updating turn state for an approval", level=logging.WARNING)

    def _publish(self, event_type: str, record: ApprovalRecord) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(
                event_type, "approvals", record.to_dict(),
                session_id=record.session_id, turn_id=record.turn_id, task_id=record.task_id,
            )
        except Exception:
            log_swallowed(_log, "publishing an approval event")
