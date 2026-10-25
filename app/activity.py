"""What HAYAT is doing right now, as a fact rather than a guess.

Two questions this answers, and they are the same question underneath: "is it
still working?" and "what is it actually doing?". Both were unanswerable. A
long turn looked identical to a hung one, and the only way to find out which
was to wait and see -- which is precisely the situation a person cannot judge.

So work registers itself while it runs. A tool call, a model generation, a
search, an image: each opens an entry when it starts and closes it when it
finishes, including when it fails. The spinner reads the newest entry to say
what is happening, and `/running` reads all of them.

The register is deliberately not built from events. The event bus records what
*has* happened, which is the wrong tense: by the time an event says a tool
started, the interesting question is whether it is still going. This holds
only live work, so an entry existing means the thing is happening now.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
import statistics
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


@dataclass
class Activity:
    """One piece of work that is happening at this moment."""

    kind: str                      # tool | model | research | image | task | voice | service
    label: str                     # what a person would call it
    detail: str = ""
    activity_id: str = field(default_factory=lambda: f"act-{uuid.uuid4().hex[:8]}")
    started: float = field(default_factory=time.monotonic)
    task_id: str | None = None
    turn_id: str | None = None
    estimated_seconds: float = 0.0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def describe(self) -> str:
        """A short phrase for a status line."""
        text = self.label if not self.detail else f"{self.label}: {self.detail}"
        return text[:70]

    @property
    def remaining(self) -> float:
        return max(0.0, self.estimated_seconds - self.elapsed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id, "kind": self.kind, "label": self.label,
            "detail": self.detail[:200], "seconds": round(self.elapsed, 1),
            "estimated_seconds": round(self.estimated_seconds, 1),
            "remaining_seconds": round(self.remaining, 1),
            "task_id": self.task_id, "turn_id": self.turn_id,
        }


class ActivityRegistry:
    """Live work, opened and closed by whoever is doing it."""

    def __init__(self) -> None:
        self._active: dict[str, Activity] = {}
        self._lock = threading.RLock()
        self._listeners: list[Any] = []
        self._durations: dict[str, list[float]] = {}

    DEFAULT_ESTIMATES = {
        "model": 25.0, "research": 90.0, "learning": 150.0,
        "image": 120.0, "tool": 20.0, "task": 60.0,
        "voice": 12.0, "service": 10.0,
    }

    @staticmethod
    def _history_key(kind: str, label: str = "") -> str:
        return f"{kind.casefold()}:{label.casefold()}" if label else kind.casefold()

    def estimate(self, kind: str, label: str = "") -> float:
        """Median observed duration, falling back to a conservative phase ETA."""
        with self._lock:
            exact = list(self._durations.get(self._history_key(kind, label), ()))
            broad = list(self._durations.get(self._history_key(kind), ()))
        samples = exact if len(exact) >= 2 else [*exact, *broad]
        if samples:
            return max(1.0, min(float(statistics.median(samples[-20:])), 1800.0))
        return float(self.DEFAULT_ESTIMATES.get(kind.casefold(), 30.0))

    def start(self, kind: str, label: str, detail: str = "", **correlation: Any) -> Activity:
        from app.execution import current_context

        context = current_context()
        activity = Activity(
            kind=kind, label=label, detail=detail,
            task_id=correlation.get("task_id", context.task_id),
            turn_id=correlation.get("turn_id", context.turn_id),
            estimated_seconds=self.estimate(kind, label),
        )
        with self._lock:
            self._active[activity.activity_id] = activity
        self._notify()
        return activity

    def finish(self, activity: Activity | str | None) -> None:
        """Close an entry. Safe to call twice, and safe on None."""
        if activity is None:
            return
        key = activity if isinstance(activity, str) else activity.activity_id
        with self._lock:
            completed = self._active.pop(key, None)
            if completed is not None:
                duration = max(0.001, completed.elapsed)
                for history_key in (
                    self._history_key(completed.kind, completed.label),
                    self._history_key(completed.kind),
                ):
                    rows = self._durations.setdefault(history_key, [])
                    rows.append(duration)
                    del rows[:-20]
        self._notify()

    @contextmanager
    def track(self, kind: str, label: str, detail: str = "", **correlation: Any) -> Iterator[Activity]:
        """Register work for the duration of a block.

        The finally is the point: work that raises must not stay on the list
        forever, or the spinner keeps turning for something that stopped.
        """
        activity = self.start(kind, label, detail, **correlation)
        try:
            yield activity
        finally:
            self.finish(activity)

    # ------------------------------------------------------------- reading

    def current(self) -> list[Activity]:
        """Everything running, oldest first."""
        with self._lock:
            return sorted(self._active.values(), key=lambda item: item.started)

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._active)

    def headline(self) -> str:
        """The one thing worth putting on a status line.

        The newest entry rather than the oldest: work nests, and the innermost
        step is the one actually running. "Researching" is true while a search
        is in flight, but "searching the web" is what is happening.
        """
        with self._lock:
            if not self._active:
                return ""
            newest = max(self._active.values(), key=lambda item: item.started)
        return newest.describe()

    def summary(self) -> dict[str, Any]:
        items = self.current()
        return {
            "busy": bool(items),
            "count": len(items),
            "headline": self.headline(),
            "running": [item.to_dict() for item in items],
        }

    def report(self) -> str:
        """What `/running` prints."""
        items = self.current()
        if not items:
            return "Nothing is running."
        lines = [f"{len(items)} thing(s) running:"]
        for item in items:
            lines.append(f"  [{item.kind:8}] {item.describe():<70} {item.elapsed:6.1f}s")
        return "\n".join(lines)

    # ------------------------------------------------------------ watching

    def add_listener(self, listener: Any) -> None:
        """Called whenever the set of running work changes."""
        with self._lock:
            self._listeners.append(listener)

    def _notify(self) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(self)
            except Exception:
                # A watcher that breaks must not break the work it watches.
                log_swallowed(_log, "activity listener")


#: One registry per process. Work is registered from many layers -- the tool
#: registry, the providers, the research manager -- and threading a handle
#: through all of them would be a lot of plumbing for one shared fact.
ACTIVITY = ActivityRegistry()


def track(kind: str, label: str, detail: str = "", **correlation: Any):
    """Shorthand for the shared registry."""
    return ACTIVITY.track(kind, label, detail, **correlation)
