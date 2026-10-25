"""Owner-confirmed outward actions: prepare, show, confirm, act once, verify.

Publishing a website, registering an account or a business, and submitting a
web form act as the owner toward the outside world. The owner asked
(2026-10-03) that HAYAT may do these, but must always ask, show what it will
do, act only once the owner has confirmed, and then check and let the owner
verify the result. This module is that rule, executable:

1. A tool *prepares* the action and proposes it here, with a plain summary,
   every value that will be sent, and a screenshot or file list as evidence.
   Nothing has left the machine yet.
2. Only the owner confirms: from the confirmation window, or by saying
   "confirm action N" at this computer. No tool can confirm, so neither the
   model nor a web page can approve its own action, and a remote device
   cannot confirm either.
3. Secrets such as passwords are never part of a proposal. The owner types
   them into the confirmation window; they are used once and never stored or
   logged.
4. The action runs once, within ten minutes of being prepared, and the
   executor re-checks that what it is about to do still matches what was shown.
5. The result comes back with evidence (where it ended up, success or error
   words, an after-screenshot or a fetched copy), and the owner marks it
   verified or disputed.

Payments and purchases are not here: they stay refused outright.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

KINDS = {
    "form": "Submit a web form",
    "register": "Register an account or a business",
    "login": "Sign in to a website",
    "publish": "Publish your website",
    "account": "Act through one of your connected accounts",
    "apply": "Send a job application",
    "reverse_engineer": "Open or change a reverse-engineering target",
}


def fingerprint(*parts: Any) -> str:
    """A short hash of exactly what was shown, so the executor can refuse if it changed."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


@dataclass
class PendingAction:
    id: int
    kind: str
    title: str
    target: str
    details: list[tuple[str, str]]
    executor: Callable[[dict[str, str]], dict[str, Any]]
    secrets_needed: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fingerprint: str = ""
    created: float = field(default_factory=time.monotonic)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    status: str = "awaiting confirmation"
    result: dict[str, Any] = field(default_factory=dict)
    verified_note: str = ""

    def public(self) -> dict[str, Any]:
        return {"action_id": self.id, "kind": self.kind, "what": KINDS.get(self.kind, self.kind), "title": self.title,
                "target": self.target, "details": [{"field": name, "value": value} for name, value in self.details],
                "you_will_type": list(self.secrets_needed), "evidence": list(self.evidence),
                "notes": list(self.notes), "status": self.status, "created_at": self.created_at,
                "result": self.result, "verified_note": self.verified_note}


class ConfirmationGate:
    """Holds prepared outward actions until the owner confirms, runs them once, records the outcome."""

    TTL_SECONDS = 600

    def __init__(self, log_path: Path | None = None) -> None:
        self._actions: dict[int, PendingAction] = {}
        self._next = 1
        self._lock = threading.RLock()
        self.log_path = log_path
        #: Called with each new PendingAction, e.g. to open the confirmation window.
        self.listeners: list[Callable[[PendingAction], None]] = []

    # ------------------------------------------------------------------ propose
    def propose(self, kind: str, title: str, target: str, details: list[tuple[str, str]],
                executor: Callable[[dict[str, str]], dict[str, Any]], *, secrets_needed: list[str] | None = None,
                evidence: list[str] | None = None, notes: list[str] | None = None, fingerprint_value: str = "") -> dict:
        if kind not in KINDS:
            raise ValueError(f"Unknown action kind {kind!r}.")
        with self._lock:
            action = PendingAction(self._next, kind, title, target, list(details), executor,
                                   list(secrets_needed or []), list(evidence or []), list(notes or []),
                                   fingerprint_value)
            self._actions[action.id] = action
            self._next += 1
        self._log(action, "proposed")
        for listener in list(self.listeners):
            try:
                listener(action)
            except Exception:
                log_swallowed(_log, "notifying a confirmation listener", level=logging.WARNING)
        summary = action.public()
        summary["how_to_confirm"] = (
            f"Nothing has been sent yet. Check the details, then confirm in the window that opened, or say "
            f"\"confirm action {action.id}\" at this computer. Say \"cancel action {action.id}\" to drop it."
            + (" It needs you to type: " + ", ".join(action.secrets_needed) + " (in the window only)."
               if action.secrets_needed else "")
            + f" It expires in {self.TTL_SECONDS // 60} minutes.")
        return summary

    # ------------------------------------------------------------------ owner decisions
    def get(self, action_id: int) -> PendingAction:
        with self._lock:
            action = self._actions.get(int(action_id))
        if action is None:
            raise KeyError(f"No action #{action_id}.")
        return action

    def pending(self) -> list[dict]:
        self._expire()
        with self._lock:
            return [action.public() for action in self._actions.values() if action.status == "awaiting confirmation"]

    def history(self, limit: int = 20) -> list[dict]:
        self._expire()
        with self._lock:
            actions = sorted(self._actions.values(), key=lambda item: item.id, reverse=True)[:limit]
        return [action.public() for action in actions]

    def confirm(self, action_id: int, *, by_owner: bool, secrets: dict[str, str] | None = None) -> dict[str, Any]:
        """Run the action once. Only the owner's own confirmation reaches here."""
        if not by_owner:
            raise PermissionError("Only the owner can confirm an outward action.")
        self._expire()
        action = self.get(action_id)
        with self._lock:
            if action.status != "awaiting confirmation":
                raise ValueError(f"Action #{action.id} is {action.status}; it can only be confirmed once.")
            secrets = dict(secrets or {})
            missing = [name for name in action.secrets_needed if not secrets.get(name)]
            if missing:
                raise ValueError("Type " + ", ".join(missing) + " in the confirmation window first. "
                                 "Passwords are never taken from chat.")
            action.status = "running"
        self._log(action, "confirmed")
        try:
            result = action.executor(secrets)
        except Exception as exc:
            action.status = "failed"
            action.result = {"error": f"{type(exc).__name__}: {exc}"}
            self._log(action, "failed")
            return {**action.public(), "next_step": "Nothing more was tried. Check the page yourself before retrying."}
        finally:
            secrets.clear()
        action.result = dict(result or {})
        action.status = "done, please verify"
        self._log(action, "executed")
        return {**action.public(), "next_step": "Check the evidence, then mark it Looks right or Something's wrong."}

    def cancel(self, action_id: int) -> dict:
        action = self.get(action_id)
        with self._lock:
            if action.status != "awaiting confirmation":
                raise ValueError(f"Action #{action.id} is {action.status}; only a waiting action can be cancelled.")
            action.status = "cancelled"
        self._log(action, "cancelled")
        return action.public()

    def verify(self, action_id: int, ok: bool, note: str = "") -> dict:
        action = self.get(action_id)
        if action.status not in {"done, please verify", "verified by you", "disputed by you"}:
            raise ValueError(f"Action #{action.id} is {action.status}; there is nothing to verify yet.")
        action.status = "verified by you" if ok else "disputed by you"
        action.verified_note = (note or "").strip()
        self._log(action, action.status)
        return action.public()

    # ------------------------------------------------------------------ internals
    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            for action in self._actions.values():
                if action.status == "awaiting confirmation" and now - action.created > self.TTL_SECONDS:
                    action.status = "expired"
                    self._log(action, "expired")

    def _log(self, action: PendingAction, event: str) -> None:
        """An audit line per step. Field values are summarised; secrets never reach it."""
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "event": event, "action_id": action.id,
                     "kind": action.kind, "title": action.title, "target": action.target,
                     "fields": [name for name, _value in action.details], "status": action.status,
                     "result": {key: value for key, value in action.result.items() if key in
                                {"url", "final_url", "verdict", "error", "site_url", "matches"}}}
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            log_swallowed(_log, "writing the confirmed-actions log")
