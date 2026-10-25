"""Causal identity that travels with a call instead of being guessed from state.

An approval record has to bind to the work that actually raised it. The
tempting shortcut is to reconstruct that identity at the point of use --
"whatever task the session last saw" -- but `SessionState.last_task` is only
assigned *after* an outcome completes, so while Task B is running it still
holds Task A. Binding an approval that way means the human approves Task B and
the record says Task A: the wrong-task invariant the approval lifecycle exists
to enforce would be defeated by its own correlation data.

So identity flows downward from the caller that knows it. The assistant knows
the session and turn; the agent knows the task; a direct deterministic command
knows there is no task at all. Each binds a scope around the work it owns, and
`ToolRegistry` reads what is actually in scope. When nothing is in scope the
answer is None -- an unknown task is recorded as unknown, never as a stale one.

contextvars rather than a thread-local because the value must nest and unwind
with the call, and because a scope entered in one place cannot leak into
unrelated later work on the same thread. New threads start with an empty
context, which is the safe direction: a voice task worker inherits no task id
and immediately binds its own inside `Agent._run`.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any, Iterator


class _Unset:
    """Distinguishes "not mentioned" from an explicit None.

    `execution_scope(task_id=None)` must be able to *clear* an inherited task
    for work that genuinely has none, while `execution_scope(turn_id="t1")`
    leaves any surrounding task id intact.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<unset>"


UNSET: Any = _Unset()


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Who is asking, and on behalf of which turn and task."""

    runtime_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    principal: str | None = None
    explicitly_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id, "session_id": self.session_id,
            "turn_id": self.turn_id, "task_id": self.task_id,
            "principal": self.principal, "explicitly_requested": self.explicitly_requested,
        }

    @property
    def is_empty(self) -> bool:
        return self == EMPTY_CONTEXT


EMPTY_CONTEXT = ExecutionContext()

_CURRENT: contextvars.ContextVar[ExecutionContext] = contextvars.ContextVar(
    "ai_execution_context", default=EMPTY_CONTEXT
)


def current_context() -> ExecutionContext:
    """The context of the call in flight; empty when nothing bound one."""
    return _CURRENT.get()


@contextmanager
def execution_scope(
    *,
    runtime_id: Any = UNSET,
    session_id: Any = UNSET,
    turn_id: Any = UNSET,
    task_id: Any = UNSET,
    principal: Any = UNSET,
    explicitly_requested: Any = UNSET,
    context: ExecutionContext | None = None,
) -> Iterator[ExecutionContext]:
    """Bind causal identity for the duration of the enclosed work.

    Fields not mentioned are inherited from any enclosing scope, so the agent
    can add a task id to the assistant's session and turn without having to
    know them. Passing an explicit None clears that field instead.
    """
    updates: dict[str, Any] = {}
    if context is not None:
        updates.update({key: value for key, value in context.to_dict().items() if value is not None})
    for name, value in (
        ("runtime_id", runtime_id), ("session_id", session_id), ("turn_id", turn_id),
        ("task_id", task_id), ("principal", principal),
        ("explicitly_requested", explicitly_requested),
    ):
        if not isinstance(value, _Unset):
            updates[name] = value
    bound = replace(_CURRENT.get(), **updates) if updates else _CURRENT.get()
    token = _CURRENT.set(bound)
    try:
        yield bound
    finally:
        _CURRENT.reset(token)
