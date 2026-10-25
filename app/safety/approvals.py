"""Approval callbacks and bound, expiring, single-use approval grants.

An approval is not a mood. It authorises one exact tool with one exact set of
normalized arguments, inside one session, until one expiry time, for one
invocation unless a wider scope was explicitly requested. Anything else must ask
again.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


ApprovalCallback = Callable[[str, str, dict], bool]

DEFAULT_TTL_SECONDS = 300

# Guards the single stdin against two threads reading it at once.
_STDIN_LOCK = threading.Lock()


def deny_unattended(tool_name: str, reason: str, arguments: dict) -> bool:
    return False


def console_approval(tool_name: str, reason: str, arguments: dict) -> bool:
    """Ask on the console, serialized against every other console writer.

    This can be called from a background tool-execution thread while the main
    CLI thread is already blocked in input(). Two concurrent readers on one
    stdin is unrecoverable -- whichever thread wins takes the other's line --
    so the prompt is written through the coordinator and the read is guarded
    by a process-wide lock. If another read already owns stdin, this denies
    rather than racing for the user's keystrokes: refusing is the safe answer.
    """
    from app.output import get_output

    output = get_output()
    output.emit(f"\nApproval required for {tool_name}: {reason}", source="approval")
    output.emit(f"Arguments: {arguments}", source="approval")
    if not _STDIN_LOCK.acquire(blocking=False):
        output.emit("Another console read is already active; denying this request. Re-issue it when the console is free.", source="approval")
        return False
    try:
        with output.prompt("Allow once? [y/N] "):
            output.emit("Allow once? [y/N] ", source="approval")
            return input().strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False
    finally:
        _STDIN_LOCK.release()


def normalize_arguments(arguments: dict[str, Any] | None) -> str:
    """Canonical, order-independent representation used for approval binding."""
    return json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(tool: str, arguments: dict[str, Any] | None) -> str:
    payload = f"{tool}\x00{normalize_arguments(arguments)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApprovalGrant:
    tool: str
    arguments_fingerprint: str
    session_id: str
    granted_at: float
    expires_at: float
    uses_remaining: int
    scope: str = "once"
    reason: str = ""

    def valid_at(self, now: float) -> bool:
        return self.uses_remaining > 0 and now < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments_fingerprint": self.arguments_fingerprint[:16],
            "session_id": self.session_id,
            "scope": self.scope,
            "uses_remaining": self.uses_remaining,
            "expires_in_seconds": max(0, round(self.expires_at - time.time())),
            "reason": self.reason,
        }


class ApprovalBroker:
    """Mediates every ASK decision and records what was actually authorised."""

    def __init__(
        self,
        callback: ApprovalCallback = deny_unattended,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.callback = callback
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._grants: list[ApprovalGrant] = []
        self.session_id: str = "default"
        self.audit: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ grants

    def grant(
        self,
        tool: str,
        arguments: dict[str, Any] | None,
        *,
        session_id: str | None = None,
        uses: int = 1,
        ttl_seconds: int | None = None,
        scope: str = "once",
        reason: str = "",
    ) -> ApprovalGrant:
        now = self.clock()
        record = ApprovalGrant(
            tool=tool,
            arguments_fingerprint=fingerprint(tool, arguments),
            session_id=session_id or self.session_id,
            granted_at=now,
            expires_at=now + (ttl_seconds if ttl_seconds is not None else self.ttl_seconds),
            uses_remaining=max(1, uses),
            scope=scope,
            reason=reason,
        )
        self._grants.append(record)
        return record

    def _matching(self, tool: str, arguments: dict[str, Any] | None) -> ApprovalGrant | None:
        now = self.clock()
        target = fingerprint(tool, arguments)
        for record in self._grants:
            if (
                record.tool == tool
                and record.arguments_fingerprint == target
                and record.session_id == self.session_id
                and record.valid_at(now)
            ):
                return record
        return None

    def active_grants(self) -> list[dict[str, Any]]:
        now = self.clock()
        return [record.to_dict() for record in self._grants if record.valid_at(now)]

    def revoke_all(self) -> int:
        count = sum(1 for record in self._grants if record.valid_at(self.clock()))
        self._grants.clear()
        return count

    def purge_expired(self) -> int:
        now = self.clock()
        keep = [record for record in self._grants if record.valid_at(now)]
        removed = len(self._grants) - len(keep)
        self._grants = keep
        return removed

    # --------------------------------------------------------------- authorize

    def authorize(self, tool: str, reason: str, arguments: dict[str, Any] | None) -> bool:
        """Consume a matching grant, or ask once and record what was approved."""
        existing = self._matching(tool, arguments)
        if existing is not None:
            existing.uses_remaining -= 1
            self._record(tool, arguments, True, "existing_grant", existing.scope)
            return True
        approved = bool(self.callback(tool, reason, dict(arguments or {})))
        if approved:
            record = self.grant(tool, arguments, uses=1, scope="once", reason=reason)
            record.uses_remaining -= 1
            self._record(tool, arguments, True, "user_approved", "once")
            return True
        self._record(tool, arguments, False, "user_declined", "none")
        return False

    def _record(self, tool: str, arguments: dict[str, Any] | None, allowed: bool, source: str, scope: str) -> None:
        self.audit.append(
            {
                "at": self.clock(),
                "tool": tool,
                "arguments_fingerprint": fingerprint(tool, arguments)[:16],
                "session_id": self.session_id,
                "allowed": allowed,
                "source": source,
                "scope": scope,
            }
        )
        del self.audit[:-500]

    def __call__(self, tool_name: str, reason: str, arguments: dict) -> bool:
        """Usable anywhere an ApprovalCallback is expected."""
        return self.authorize(tool_name, reason, arguments)


@dataclass
class CallBudget:
    """Bounds runaway tool loops within one task."""

    max_total_calls: int = 200
    max_calls_per_tool: int = 60
    counts: dict[str, int] = field(default_factory=dict)
    total: int = 0

    def reset(self) -> None:
        self.counts.clear()
        self.total = 0

    def check(self, tool: str) -> str | None:
        """Return a refusal reason when the budget is exhausted, else None."""
        if self.total >= self.max_total_calls:
            return f"Tool-call budget exhausted for this task ({self.max_total_calls} calls)."
        if self.counts.get(tool, 0) >= self.max_calls_per_tool:
            return f"Repeated-call budget exhausted for {tool} ({self.max_calls_per_tool} calls)."
        return None

    def consume(self, tool: str) -> None:
        self.counts[tool] = self.counts.get(tool, 0) + 1
        self.total += 1

    def status(self) -> dict[str, Any]:
        return {
            "total_calls": self.total,
            "max_total_calls": self.max_total_calls,
            "max_calls_per_tool": self.max_calls_per_tool,
            "per_tool": dict(sorted(self.counts.items(), key=lambda item: -item[1])[:10]),
        }
