"""Authoritative record of what actually went wrong.

A real session produced a WinError 10061 (connection refused to the local
model endpoint) and, asked about it moments later, the assistant said there
was no system error -- because nothing had recorded one, so the model was
left to guess. Failures are now recorded as facts with correlation
identifiers, and "what's wrong?" reads that record instead of asking the
model to invent a diagnosis.
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class FailureClass(StrEnum):
    """One canonical vocabulary for what kind of failure something was.

    Kept small on purpose. Every boundary maps into this set so "what
    happened?" can be answered from the record rather than from prose, and so
    a retry policy can key on the class instead of on message text.
    """

    POLICY_DENIED = "POLICY_DENIED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    DEPENDENCY_DOWN = "DEPENDENCY_DOWN"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    DATA_INTEGRITY = "DATA_INTEGRITY"
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    #: A thread-confined resource touched from the wrong thread. Playwright's
    #: driver is the one here: its objects belong to the thread that made
    #: them, and contact from any other raises `greenlet.error: Cannot switch
    #: to a different thread`. It arrived as UNKNOWN, which said nothing about
    #: a failure that has one cause and one fix, and got attributed to
    #: whichever subsystem happened to be on the stack -- twice to Ollama,
    #: which was not involved at all.
    THREAD_CONFINEMENT = "THREAD_CONFINEMENT"
    UNKNOWN = "UNKNOWN"


# Failure classes where re-running the *same* read-only request is reasonable
# once the cause clears. Side-effecting work is never replayed on this basis
# alone -- that decision belongs to the caller, which knows what the action did.
RETRYABLE_CLASSES = frozenset({
    FailureClass.DEPENDENCY_DOWN, FailureClass.TIMEOUT,
    FailureClass.RATE_LIMITED, FailureClass.TRANSIENT,
})

# Windows socket errors seen from a local service that is not listening.
_WINDOWS_REFUSED = ("10061", "actively refused")


def classify_exception(exc: BaseException) -> FailureClass:
    """Map an exception to the taxonomy from evidence, not from guesswork.

    This is deliberately code, not a prompt. The real incident behind this
    module was a WinError 10061 -- the local Ollama endpoint refusing a
    connection -- being explained to the user as a possible internet problem.
    A refused connection to localhost is a local dependency that is not
    running, and nothing about it is ambiguous enough to need a model.
    """
    name = type(exc).__name__
    text = f"{exc}".lower()

    # Checked first, and by message rather than by type: greenlet raises a
    # bare `error` whose name collides with half the standard library, and the
    # sentence it carries is the only unambiguous evidence. Everything below
    # would mislabel it -- "cannot switch" has no timeout, no connection, and
    # nothing wrong with the input.
    if "switch to a different thread" in text or "cannot switch to a different" in text:
        return FailureClass.THREAD_CONFINEMENT

    if isinstance(exc, TimeoutError) or "timeout" in name.lower() or "timed out" in text:
        return FailureClass.TIMEOUT
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError)):
        return FailureClass.DEPENDENCY_DOWN
    if name in {"ConnectError", "ConnectTimeout", "ReadTimeout", "PoolTimeout", "NetworkError"}:
        return FailureClass.TIMEOUT if "timeout" in name.lower() else FailureClass.DEPENDENCY_DOWN
    if any(marker in text for marker in _WINDOWS_REFUSED):
        return FailureClass.DEPENDENCY_DOWN
    if isinstance(exc, PermissionError):
        return FailureClass.PERMISSION_DENIED
    if isinstance(exc, FileNotFoundError) or name == "NotADirectoryError":
        return FailureClass.NOT_FOUND
    if isinstance(exc, (IsADirectoryError, OSError)) and not isinstance(exc, ConnectionError):
        return FailureClass.PERMANENT
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return FailureClass.INVALID_INPUT
    if isinstance(exc, MemoryError):
        return FailureClass.RESOURCE_EXHAUSTED
    return FailureClass.UNKNOWN


def classify_http_status(status: int) -> FailureClass:
    """HTTP status to taxonomy, for providers that answer but answer badly."""
    if status == 404:
        return FailureClass.NOT_FOUND
    if status == 429:
        return FailureClass.RATE_LIMITED
    if status in {401, 403}:
        return FailureClass.PERMISSION_DENIED
    if status == 408:
        return FailureClass.TIMEOUT
    if 500 <= status < 600:
        return FailureClass.DEPENDENCY_DOWN
    if 400 <= status < 500:
        return FailureClass.INVALID_INPUT
    return FailureClass.UNKNOWN


@dataclass
class FailureRecord:
    failure_id: str = field(default_factory=lambda: f"fail-{uuid.uuid4().hex[:12]}")
    runtime_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    stage: str = "unknown"
    service: str | None = None
    provider: str | None = None
    endpoint: str | None = None
    tool: str | None = None
    exception_type: str | None = None
    exception_message: str = ""
    user_request: str = ""
    normalized_request: str = ""
    verification_failure: str | None = None
    retry_count: int = 0
    recovery_attempted: bool = False
    recovery_result: str | None = None
    failure_class: FailureClass = FailureClass.UNKNOWN
    # What the failing thing depended on, when the failure was about reaching
    # something else -- "ollama", not "the network". Lets the explanation name
    # the actual dependency instead of speculating about connectivity.
    dependency: str | None = None
    http_status: int | None = None
    traceback: str | None = None

    MAX_TRACEBACK_CHARS = 4000

    @property
    def retryable(self) -> bool:
        """Whether re-running the same *read-only* request could now succeed."""
        return self.failure_class in RETRYABLE_CLASSES

    def summary(self) -> str:
        """A short, factual explanation suitable for showing a person."""
        where = self.service or self.provider or self.tool or self.stage
        detail = self.exception_message.strip() or self.verification_failure or "no further detail was recorded"
        lines = [f"The last failure was in {where}: {self.exception_type or 'failure'} -- {detail}"]
        lines.append(self._classification_line())
        if self.endpoint:
            lines.append(f"Endpoint: {self.endpoint}")
        if self.http_status:
            lines.append(f"HTTP status: {self.http_status}")
        if self.user_request:
            lines.append(f"It happened while handling: {self.user_request[:200]!r}")
        if self.recovery_attempted:
            lines.append(f"Recovery was attempted: {self.recovery_result or 'result not recorded'}")
        lines.append(f"Recorded at {self.timestamp} (failure {self.failure_id}).")
        return "\n".join(lines)

    def _classification_line(self) -> str:
        """State the classification, and for a downed dependency say which one.

        The historical wrong answer was "this may be an internet problem" for
        a refused connection to localhost. Naming the dependency makes that
        specific mistake impossible to repeat from this record.
        """
        if self.failure_class is FailureClass.DEPENDENCY_DOWN and self.dependency:
            local = self._endpoint_is_local()
            where = "on this machine" if local else "that this system depends on"
            return (
                f"Classification: {self.failure_class.value}. "
                f"{self.dependency} {where} did not accept the connection"
                + (". This is a local service, not an internet problem." if local else ".")
            )
        return f"Classification: {self.failure_class.value}."

    def _endpoint_is_local(self) -> bool:
        endpoint = (self.endpoint or "").lower()
        return any(marker in endpoint for marker in ("localhost", "127.0.0.1", "::1", "0.0.0.0"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id, "runtime_id": self.runtime_id,
            "session_id": self.session_id, "turn_id": self.turn_id, "task_id": self.task_id,
            "timestamp": self.timestamp, "stage": self.stage, "service": self.service,
            "provider": self.provider, "endpoint": self.endpoint, "tool": self.tool,
            "exception_type": self.exception_type, "exception_message": self.exception_message[:2000],
            "user_request": self.user_request[:500], "normalized_request": self.normalized_request[:500],
            "verification_failure": self.verification_failure, "retry_count": self.retry_count,
            "recovery_attempted": self.recovery_attempted, "recovery_result": self.recovery_result,
            "failure_class": self.failure_class.value, "dependency": self.dependency,
            "http_status": self.http_status, "retryable": self.retryable,
            "traceback": self.traceback,
        }


class FailureLog:
    """Bounded failure history with a distinguished most-recent entry."""

    def __init__(self, bus: Any = None, limit: int = 100) -> None:
        self._failures: list[FailureRecord] = []
        self._limit = max(10, limit)
        self._lock = threading.RLock()
        self._bus = bus

    def record(self, **fields: Any) -> FailureRecord:
        fields = self._redact(fields)
        fields = self._correlate(fields)
        failure = FailureRecord(**fields)
        if failure.traceback:
            failure.traceback = failure.traceback[-FailureRecord.MAX_TRACEBACK_CHARS:]
        with self._lock:
            self._failures.append(failure)
            del self._failures[: -self._limit]
        if self._bus is not None:
            try:
                self._bus.publish(
                    "FAILURE_RECORDED", failure.service or failure.stage, failure.to_dict(),
                    session_id=failure.session_id, turn_id=failure.turn_id, task_id=failure.task_id,
                )
            except Exception:
                log_swallowed(_log, "publishing a recorded failure")
        return failure

    def from_exception(self, exc: BaseException, *, stage: str, **fields: Any) -> FailureRecord:
        """Record an exception, classifying it unless the caller knows better.

        A boundary that has evidence the generic classifier lacks -- a provider
        that knows its own endpoint was refused, a verifier that knows the
        action ran but did not achieve the goal -- may pass failure_class
        explicitly. Otherwise the classification is derived from the exception.
        """
        evidence = getattr(exc, 'failure_fields', None)
        if callable(evidence):
            for key, value in evidence().items():
                fields.setdefault(key, value)
        fields.setdefault("failure_class", classify_exception(exc))
        fields.setdefault("traceback", self._format_traceback(exc))
        return self.record(
            stage=stage, exception_type=type(exc).__name__, exception_message=str(exc), **fields
        )

    @staticmethod
    def _format_traceback(exc: BaseException) -> str | None:
        import traceback as traceback_module

        try:
            return "".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))
        except Exception:
            return None

    @staticmethod
    def _redact(fields: dict[str, Any]) -> dict[str, Any]:
        """Strip credential material before a failure becomes a stored fact.

        An exception message routinely carries the request that produced it,
        and a traceback carries local variables' repr in some formats. A
        diagnostic record that leaks an Authorization header is a worse
        problem than the failure it describes.
        """
        from app.safety.sensitive import redact

        return {key: redact(value) if isinstance(value, str) else value for key, value in fields.items()}

    @staticmethod
    def _correlate(fields: dict[str, Any]) -> dict[str, Any]:
        """Fill in session/turn/task from the call in flight when not given.

        The boundary that catches an exception often knows nothing about which
        turn it belongs to. The execution scope does, and it is the same truth
        approvals bind to -- so a failure and the approval that preceded it can
        be lined up against the same task.
        """
        from app.execution import current_context

        context = current_context()
        for key, value in (
            ("session_id", context.session_id), ("turn_id", context.turn_id),
            ("task_id", context.task_id), ("runtime_id", context.runtime_id),
        ):
            if value is not None and fields.get(key) is None:
                fields[key] = value
        return fields

    @property
    def last(self) -> FailureRecord | None:
        with self._lock:
            return self._failures[-1] if self._failures else None

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return [failure.to_dict() for failure in self._failures[-max(1, limit):]]

    def get(self, failure_id: str) -> FailureRecord | None:
        with self._lock:
            return next((item for item in self._failures if item.failure_id == failure_id), None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()
