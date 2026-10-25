"""Authoritative per-turn record and its state machine.

One user request -- typed or spoken -- is one TurnRecord. It owns the
correlation identifiers everything else hangs off, and it owns its own
lifecycle events: callers transition the record and the record publishes,
rather than forty call sites each remembering to publish the right string.

The state machine exists to make a specific class of bug impossible rather
than merely unlikely: a cancelled or superseded turn cannot later present a
visible final answer, and no turn can present two.
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


class TurnStatus(StrEnum):
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    ROUTED = "ROUTED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


TERMINAL_STATUSES = {TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED}

# A turn that reached one of these must never render a visible final answer:
# the user either took it back or replaced it, and showing the old result
# would contradict what they asked for.
NON_PRESENTABLE_STATUSES = {TurnStatus.CANCELLED, TurnStatus.SUPERSEDED}

_LEGAL_TRANSITIONS: dict[TurnStatus, set[TurnStatus]] = {
    TurnStatus.RECEIVED: {TurnStatus.NORMALIZED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED, TurnStatus.FAILED},
    TurnStatus.NORMALIZED: {TurnStatus.ROUTED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED, TurnStatus.FAILED},
    TurnStatus.ROUTED: {TurnStatus.RUNNING, TurnStatus.COMPLETED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED, TurnStatus.FAILED},
    TurnStatus.RUNNING: {TurnStatus.WAITING_APPROVAL, TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED},
    TurnStatus.WAITING_APPROVAL: {TurnStatus.RUNNING, TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED, TurnStatus.SUPERSEDED},
    TurnStatus.COMPLETED: set(),
    TurnStatus.FAILED: set(),
    TurnStatus.CANCELLED: set(),
    TurnStatus.SUPERSEDED: set(),
}

_STATUS_EVENTS = {
    TurnStatus.NORMALIZED: "TURN_NORMALIZED",
    TurnStatus.ROUTED: "TURN_ROUTED",
    TurnStatus.RUNNING: "TURN_RUNNING",
    TurnStatus.WAITING_APPROVAL: "TURN_WAITING_APPROVAL",
    TurnStatus.COMPLETED: "TURN_COMPLETED",
    TurnStatus.FAILED: "TURN_FAILED",
    TurnStatus.CANCELLED: "TURN_CANCELLED",
    TurnStatus.SUPERSEDED: "TURN_SUPERSEDED",
}


class IllegalTurnTransition(RuntimeError):
    """Raised when code tries to move a turn somewhere it cannot go."""


@dataclass
class TurnRecord:
    turn_id: str = field(default_factory=lambda: f"turn-{uuid.uuid4().hex[:12]}")
    session_id: str = ""
    runtime_id: str = ""
    input_source: str = "text"
    raw_input: str = ""
    normalized_input: str = ""
    corrections: list[str] = field(default_factory=list)
    intent: str | None = None
    intent_confidence: float | None = None
    subject: str | None = None
    modes: list[str] = field(default_factory=list)
    task_id: str | None = None
    provider: str | None = None
    model: str | None = None
    tool_calls: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    failure_id: str | None = None
    response_id: str | None = None
    status: TurnStatus = TurnStatus.RECEIVED
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    latency: dict[str, float] = field(default_factory=dict)
    _started: float = field(default_factory=time.monotonic, repr=False)
    _bus: Any = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _final_emitted: bool = field(default=False, repr=False)

    # ---------------------------------------------------------------- lifecycle

    def transition(self, status: TurnStatus, **payload: Any) -> None:
        """Move to `status`, refusing illegal moves, and publish the event."""
        with self._lock:
            current = self.status
            if status == current:
                return
            if status not in _LEGAL_TRANSITIONS[current]:
                raise IllegalTurnTransition(f"{current.value} -> {status.value} is not a legal turn transition")
            self.status = status
            if status in TERMINAL_STATUSES:
                self.completed_at = datetime.now(UTC).isoformat()
                self.latency.setdefault("total_ms", round((time.monotonic() - self._started) * 1000, 3))
        self._publish(_STATUS_EVENTS[status], payload, final=status in TERMINAL_STATUSES)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def can_present_final(self) -> bool:
        """Whether a visible final answer for this turn is still legitimate.

        False once the user cancelled or replaced the request, and false a
        second time around -- exactly one visible final answer per turn.
        """
        with self._lock:
            return self.status not in NON_PRESENTABLE_STATUSES and not self._final_emitted

    def claim_final(self) -> bool:
        """Atomically take the right to emit this turn's one visible answer."""
        with self._lock:
            if self.status in NON_PRESENTABLE_STATUSES or self._final_emitted:
                return False
            self._final_emitted = True
            return True

    def mark(self, stage: str, milliseconds: float) -> None:
        with self._lock:
            self.latency[stage] = round(milliseconds, 3)

    def elapsed_ms(self) -> float:
        return round((time.monotonic() - self._started) * 1000, 3)

    # ------------------------------------------------------------------ events

    def _publish(self, event_type: str, payload: dict[str, Any], *, final: bool = False) -> None:
        if self._bus is None:
            return
        try:
            self._bus.publish(
                event_type, self.input_source, payload or {},
                session_id=self.session_id or None, turn_id=self.turn_id,
                task_id=self.task_id, final=final,
            )
        except Exception:
            # Telemetry must never break the turn it is describing.
            log_swallowed(_log, "publishing a turn event")

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "turn_id": self.turn_id, "session_id": self.session_id, "runtime_id": self.runtime_id,
                "input_source": self.input_source, "raw_input": self.raw_input[:2000],
                "normalized_input": self.normalized_input[:2000], "corrections": list(self.corrections),
                "intent": self.intent, "intent_confidence": self.intent_confidence,
                "subject": self.subject, "modes": list(self.modes), "task_id": self.task_id,
                "provider": self.provider, "model": self.model, "tool_calls": list(self.tool_calls),
                "observations": list(self.observations), "memory_refs": list(self.memory_refs),
                "provenance": list(self.provenance), "failure_id": self.failure_id,
                "response_id": self.response_id, "status": self.status.value,
                "created_at": self.created_at, "completed_at": self.completed_at,
                "latency": dict(self.latency),
            }


class TurnLog:
    """Bounded in-memory history of recent turns, newest last."""

    def __init__(self, limit: int = 200) -> None:
        self._turns: list[TurnRecord] = []
        self._limit = max(10, limit)
        self._lock = threading.RLock()

    def start(self, *, session_id: str, runtime_id: str, raw_input: str, input_source: str, bus: Any = None) -> TurnRecord:
        turn = TurnRecord(
            session_id=session_id, runtime_id=runtime_id, raw_input=raw_input,
            normalized_input=raw_input, input_source=input_source, _bus=bus,
        )
        turn._publish("TURN_RECEIVED", {"source": input_source, "characters": len(raw_input)})
        with self._lock:
            self._turns.append(turn)
            del self._turns[: -self._limit]
        return turn

    def current(self) -> TurnRecord | None:
        with self._lock:
            for turn in reversed(self._turns):
                if not turn.is_terminal:
                    return turn
        return None

    def last(self) -> TurnRecord | None:
        with self._lock:
            return self._turns[-1] if self._turns else None

    def get(self, turn_id: str) -> TurnRecord | None:
        with self._lock:
            return next((turn for turn in self._turns if turn.turn_id == turn_id), None)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [turn.to_dict() for turn in self._turns[-max(1, limit):]]

    def supersede_active(self, *, except_turn: str | None = None) -> list[str]:
        """Mark in-flight turns superseded because a newer request replaced them."""
        superseded: list[str] = []
        with self._lock:
            candidates = [turn for turn in self._turns if not turn.is_terminal and turn.turn_id != except_turn]
        for turn in candidates:
            try:
                turn.transition(TurnStatus.SUPERSEDED, reason="replaced_by_newer_request")
                superseded.append(turn.turn_id)
            except IllegalTurnTransition:
                continue
        return superseded
