"""One shared sense of what the owner is waiting for, so every background part acts accordingly.

The owner asked for HAYAT to work "the way a brain makes the whole body work accordingly". Until now each
background part decided on its own when to work. Only the learner paused while an answer was being written
(``Assistant._learning_paused``). The world-feed poller (116 feeds every minute), source discovery, the job
watcher, the code monitor and memory consolidation kept fetching, parsing and writing while the owner sat
waiting for a reply or a task, competing with it for the network, the CPU and the disk.

A body does not do that. When the brain is focused, digestion slows and attention narrows; reflexes (breathing,
blinking, pulling a hand from heat) never wait. This module is that arrangement in software:

* **Focus** (:meth:`NervousSystem.focus`) is held while the owner is waiting on HAYAT: during a turn and while an
  agent task runs. Holds nest and are released in ``finally``, so a crashed turn cannot leave the body frozen.
* **State** (:meth:`NervousSystem.state`) is read from real signals only: focus holds, and Windows' own
  last-input time. *focused* means someone is waiting; *present* means input in the last 2 minutes; *idle* and
  *away* mean no input for 2 or 30 minutes. If the input time cannot be read, the state says so.
* **Organs** (background loops) call :meth:`NervousSystem.yield_to_owner` before each round. While focused they
  wait, and they start the moment focus ends (a condition variable wakes them, not a poll). They never wait past
  their own limit (90 s by default), so feeds cannot go stale behind a long task: homeostasis, not paralysis.
  Every wait and every round is counted and shown by :meth:`NervousSystem.status`.
* **Reflexes** are deliberately not organs here and never wait: price alerts, camera and sensing watches, voice,
  the event writer and research jobs the owner asked for. They are listed in :data:`REFLEXES` so the status can
  say what keeps running and why.

Nothing here stops, kills or skips work. It only orders it, so the owner's request goes first.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Seconds without keyboard or mouse input before the owner counts as idle, then away.
IDLE_AFTER = 120.0
AWAY_AFTER = 1800.0
#: The longest a background round waits for the owner by default, so nothing starves.
MAX_WAIT = 90.0

#: Parts that never wait for focus, and why: they react to the world or to the owner directly.
REFLEXES = {
    "price alerts": "a price crossing your level is time-critical",
    "camera and Wi-Fi sensing watches": "they react to what happens in the room",
    "voice": "it is how you talk to HAYAT",
    "event log writer": "every action must be recorded as it happens",
    "research jobs you asked for": "they are your request, not background work",
}


@dataclass
class Organ:
    name: str
    rounds: int = 0
    failures: int = 0
    deferrals: int = 0
    waited_seconds: float = 0.0
    longest_wait: float = 0.0
    last_round_at: float | None = None
    last_note: str = ""
    waiting_since: float | None = None


def _windows_idle_seconds() -> float | None:
    try:
        from app.learning.continuous import _windows_idle_seconds as read
    except Exception:
        return None
    try:
        value = read()
    except Exception:
        log_swallowed(_log, "reading the time since the last keyboard or mouse input")
        return None
    return float(value) if value is not None and value >= 0 else None


class NervousSystem:
    def __init__(self, *, idle_reader: Callable[[], float | None] = _windows_idle_seconds,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._condition = threading.Condition()
        self._holds: dict[int, tuple[str, float]] = {}
        self._next = 0
        self._organs: dict[str, Organ] = {}
        self._idle_reader = idle_reader
        self._clock = clock
        self.focused_seconds = 0.0
        self._focused_from: float | None = None

    # ---------------------------------------------------------------- focus

    @contextmanager
    def focus(self, why: str) -> Iterator[None]:
        """Hold the body's attention on the owner's request for the duration of the block."""
        with self._condition:
            self._next += 1
            token = self._next
            if not self._holds:
                self._focused_from = self._clock()
            self._holds[token] = (str(why)[:160], self._clock())
        try:
            yield
        finally:
            with self._condition:
                self._holds.pop(token, None)
                if not self._holds and self._focused_from is not None:
                    self.focused_seconds += self._clock() - self._focused_from
                    self._focused_from = None
                self._condition.notify_all()

    @property
    def focused(self) -> bool:
        with self._condition:
            return bool(self._holds)

    def state(self) -> dict[str, Any]:
        """The body's state now, from focus holds and Windows' last-input time."""
        with self._condition:
            holds = sorted(self._holds.values(), key=lambda item: item[1])
        idle = self._idle_reader() if self._idle_reader is not None else None
        if holds:
            level, why = "focused", "; ".join(dict.fromkeys(reason for reason, _ in holds))
        elif idle is None:
            level, why = "unknown", "the time since the last keyboard or mouse input could not be read"
        elif idle < IDLE_AFTER:
            level, why = "present", f"you used the keyboard or mouse {int(idle)} s ago"
        elif idle < AWAY_AFTER:
            level, why = "idle", f"no keyboard or mouse input for {int(idle // 60)} min"
        else:
            level, why = "away", f"no keyboard or mouse input for {int(idle // 60)} min"
        since = holds[0][1] if holds else None
        return {"level": level, "why": why, "idle_seconds": None if idle is None else round(idle, 1),
                "focused_for_seconds": round(self._clock() - since, 1) if since is not None else 0.0}

    # ---------------------------------------------------------------- organs

    def organ(self, name: str) -> Organ:
        with self._condition:
            return self._organs.setdefault(name, Organ(name))

    def yield_to_owner(self, name: str, stop: threading.Event | None = None, *, max_wait: float = MAX_WAIT) -> float:
        """Wait while the owner is waiting on HAYAT, at most ``max_wait`` seconds; returns the seconds waited."""
        organ = self.organ(name)
        started = self._clock()
        with self._condition:
            if not self._holds:
                return 0.0
            organ.deferrals += 1
            organ.waiting_since = started
            deadline = started + max(0.0, float(max_wait))
            while self._holds and not (stop is not None and stop.is_set()):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    organ.last_note = f"ran after waiting the {int(max_wait)} s limit for the owner"
                    break
                # Short slices so a stop request is noticed; the release itself wakes us at once.
                self._condition.wait(min(remaining, 0.5))
            waited = self._clock() - started
            organ.waited_seconds += waited
            organ.longest_wait = max(organ.longest_wait, waited)
            organ.waiting_since = None
        return waited

    def ran(self, name: str, *, ok: bool = True, note: str = "") -> None:
        """Record that an organ finished a round."""
        organ = self.organ(name)
        with self._condition:
            organ.rounds += 1
            organ.failures += 0 if ok else 1
            organ.last_round_at = time.time()
            if note:
                organ.last_note = str(note)[:160]

    @contextmanager
    def round(self, name: str, stop: threading.Event | None = None, *, max_wait: float = MAX_WAIT) -> Iterator[float]:
        """Yield to the owner, then run one round of an organ and record how it went."""
        waited = self.yield_to_owner(name, stop, max_wait=max_wait)
        ok = False
        try:
            yield waited
            ok = True
        finally:
            self.ran(name, ok=ok)

    # ---------------------------------------------------------------- report

    def status(self) -> dict[str, Any]:
        state = self.state()
        now = self._clock()
        with self._condition:
            organs = [{
                "name": organ.name, "rounds": organ.rounds, "failed_rounds": organ.failures,
                "times_waited_for_you": organ.deferrals, "seconds_waited": round(organ.waited_seconds, 1),
                "longest_wait_seconds": round(organ.longest_wait, 1),
                "waiting_now_seconds": round(now - organ.waiting_since, 1) if organ.waiting_since is not None else None,
                "last_round": time.strftime("%H:%M:%S", time.localtime(organ.last_round_at)) if organ.last_round_at else None,
                "note": organ.last_note,
            } for organ in sorted(self._organs.values(), key=lambda item: item.name)]
            focused_total = self.focused_seconds + (now - self._focused_from if self._focused_from is not None else 0.0)
        return {**state, "organs": organs, "reflexes": dict(REFLEXES),
                "seconds_focused_on_you": round(focused_total, 1), "max_wait_seconds": MAX_WAIT}


def describe(status: dict[str, Any]) -> str:
    """Plain words for :meth:`NervousSystem.status`."""
    names = {"focused": "Focused on you", "present": "You are here", "idle": "You are idle", "away": "You are away",
             "unknown": "Not sure if you are here"}
    lines = [f"{names.get(status['level'], status['level'])}: {status['why']}."]
    if status["organs"]:
        lines.append("Background parts (they wait while you are waiting on me, never longer than "
                     f"{int(status['max_wait_seconds'])} s):")
        for organ in status["organs"]:
            line = f"- {organ['name']}: {organ['rounds']} rounds"
            if organ["failed_rounds"]:
                line += f", {organ['failed_rounds']} failed"
            if organ["times_waited_for_you"]:
                line += f"; waited for you {organ['times_waited_for_you']}× ({organ['seconds_waited']} s in all)"
            if organ["waiting_now_seconds"] is not None:
                line += f"; waiting now ({organ['waiting_now_seconds']} s)"
            if organ["last_round"]:
                line += f"; last round {organ['last_round']}"
            lines.append(line)
    else:
        lines.append("No background part has run a round yet in this session.")
    lines.append("Never held back: " + "; ".join(f"{name} ({why})" for name, why in status["reflexes"].items()) + ".")
    return "\n".join(lines)


#: The one nervous system of this process (like app.activity.ACTIVITY).
BODY = NervousSystem()


def register_body_tools(registry: Any, body: NervousSystem = BODY) -> None:
    def body_status() -> dict[str, Any]:
        status = body.status()
        return {**status, "summary": describe(status)}

    registry.register(
        "body_status",
        "Report how HAYAT's parts are coordinated right now: whether it is focused on the owner's request (a reply "
        "or task in progress), present, idle or away (from Windows' last-input time), each background part's rounds, "
        "how often and how long it waited for the owner, and which reflexes never wait. Read-only.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "low", "system_read", body_status,
    )
