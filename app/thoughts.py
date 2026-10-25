"""Letting Mohsin watch HAYAT think.

The spinner says something is happening and names the current step. That
answers "is it stuck?" but not "what is it actually doing, and why did it
decide that?" -- and when HAYAT plans a task, chooses a tool, and verifies a
result, all of that reasoning happened invisibly. From the outside a
three-minute task looked like a pause followed by an answer.

So the work narrates itself. This subscribes to lifecycle events that already
exist -- the plan, each step, each tool, each verification, each failure --
and turns them into lines a person can read while they happen. Nothing new is
computed and nothing is inferred: if a line appears, the event behind it was
published by the code that did the thing.

Off by default, because narration during a quick reply is noise. `/thinking
on` turns it on for the session.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.output import get_output
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: Events worth showing, and how to say them. The phrasing is deliberately
#: first-person: this is HAYAT describing its own work, not a log.
_NARRATION: dict[str, str] = {
    "TURN_ROUTED": "understood: {intent}",
    "TASK_STARTED": "working on: {goal}",
    "TASK_CREATED": "planning: {goal}",
    "TASK_PROGRESS": "{detail}",
    "TOOL_STARTED": "using {tool}{on}",
    "TOOL_COMPLETED": "{tool} done",
    "TOOL_FAILED": "{tool} failed: {error}",
    "MODEL_STARTED": "thinking",
    "RESEARCH_STARTED": "researching: {query}",
    "RESEARCH_RESULT": "found {count} source(s)",
    "APPROVAL_REQUIRED": "waiting for you: {action}",
    "CAMERA_OBSERVATION": "looked through the camera",
    "FAILURE_RECORDED": "hit a problem: {detail}",
    "SERVICE_RESTARTING": "restarting {service}",
    "SERVICE_RECOVERED": "{service} is back",
}

#: The step-by-step of a plan, which is the part Mohsin most wants to see.
_PLAN_EVENTS = {"TASK_CREATED", "TOOL_STARTED", "TOOL_COMPLETED", "TOOL_FAILED"}


@dataclass
class Thought:
    """One line of narration, with what produced it."""

    text: str
    event: str
    at: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def render(self, prefix: str = "  ") -> str:
        return f"{prefix}{self.text}"


class ThoughtStream:
    """Narrates work as it happens, from events the work already publishes."""

    def __init__(self, events: Any = None, enabled: bool = False, limit: int = 400) -> None:
        self.events = events
        self.enabled = enabled
        self._lock = threading.RLock()
        self._history: list[Thought] = []
        self._limit = limit
        self._unsubscribe: list[Any] = []
        self._listeners: list[Any] = []
        if events is not None:
            self.attach(events)

    # ------------------------------------------------------------- plumbing

    def attach(self, events: Any) -> None:
        """Subscribe to the events that describe work in progress."""
        self.events = events
        for name in _NARRATION:
            try:
                self._unsubscribe.append(events.subscribe(name, self._on_event))
            except Exception:
                # An event this build does not define is not an error; the
                # taxonomy is allowed to differ from this narration table.
                log_swallowed(_log, "subscribing narration to an event type")
                continue

    def detach(self) -> None:
        for stop in self._unsubscribe:
            try:
                stop()
            except Exception:
                log_swallowed(_log, "unsubscribing narration")
        self._unsubscribe.clear()

    # ------------------------------------------------------------ narration

    def _on_event(self, event: dict[str, Any]) -> None:
        thought = self._describe(event)
        if thought is None:
            return
        with self._lock:
            self._history.append(thought)
            del self._history[: -self._limit]
            listeners = list(self._listeners)
        # Operational observers (the desktop Mindstream, for example) receive
        # the already-sanitised narration, never model scratch work or hidden
        # chain-of-thought. A broken observer must not break the work itself.
        for listener in listeners:
            try:
                listener(thought)
            except Exception:
                log_swallowed(_log, "narration listener")
        if self.enabled:
            # Through the coordinator, so narration never lands on top of a
            # reply or the prompt Mohsin is typing into.
            get_output().emit(thought.render(), source="thinking")

    def _describe(self, event: dict[str, Any]) -> Thought | None:
        name = str(event.get("event_type", ""))
        template = _NARRATION.get(name)
        if template is None:
            return None
        payload = event.get("payload") or {}
        fields = {
            "intent": payload.get("intent") or payload.get("kind") or "",
            "goal": _short(payload.get("goal") or payload.get("raw_input")),
            "tool": payload.get("tool") or payload.get("name") or "a tool",
            "on": _target(payload),
            "error": _short(payload.get("error") or payload.get("reason"), 90),
            "detail": _short(payload.get("detail") or payload.get("summary")
                             or payload.get("exception_message"), 90),
            "query": _short(payload.get("query")),
            "count": payload.get("count") or payload.get("results") or 0,
            "action": payload.get("action") or payload.get("capability") or "an action",
            "service": payload.get("service_id") or payload.get("service") or "a service",
        }
        try:
            text = template.format(**fields).strip()
        except (KeyError, IndexError):
            return None
        if not text or text.endswith(":"):
            return None
        return Thought(text=text, event=name, detail=payload)

    # -------------------------------------------------------------- reading

    def recent(self, limit: int = 30) -> list[str]:
        with self._lock:
            return [item.render("") for item in self._history[-max(1, limit):]]

    def plan_only(self, limit: int = 30) -> list[str]:
        """Just the steps, for when the full stream is too chatty."""
        with self._lock:
            return [item.render("") for item in self._history if item.event in _PLAN_EVENTS][-limit:]

    def report(self) -> str:
        lines = self.recent()
        if not lines:
            return ("Nothing recorded yet. Ask me to do something, or turn narration on "
                    "with /thinking on to watch it happen live.")
        return "\n".join(f"  {line}" for line in lines)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {"narrating": self.enabled, "recorded": len(self._history),
                    "subscribed": len(self._unsubscribe), "listeners": len(self._listeners)}

    def add_listener(self, listener: Any) -> Any:
        """Observe safe operational narration even when chat narration is off.

        Returns an idempotent unsubscribe callback so short-lived interfaces
        do not remain referenced after they close.
        """
        with self._lock:
            self._listeners.append(listener)
        removed = False

        def unsubscribe() -> None:
            nonlocal removed
            if removed:
                return
            removed = True
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return unsubscribe

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        return self.enabled


def _short(value: Any, limit: int = 70) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _target(payload: dict[str, Any]) -> str:
    """The thing a tool is acting on, phrased to read inside a sentence."""
    arguments = payload.get("arguments") or {}
    for key in ("query", "prompt", "path", "application", "url", "target"):
        value = arguments.get(key) or payload.get(key)
        if value:
            return f" on {_short(value, 50)}"
    return ""
