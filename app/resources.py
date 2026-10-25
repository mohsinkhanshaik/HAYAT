"""One heavy job at a time, and none at all when the machine has no room.

Mohsin found the machine at 94% memory and asked HAYAT to do its work in
sequence rather than all at once. Measuring first showed HAYAT was not the
cause -- LM Studio was holding 18.8GB and Ollama 1.2GB, against 536MB for
HAYAT itself -- but the request stands regardless, because the failure it
prevents is real: an image model and a language model both loading into what
is left of 2.4GB free do not fail politely, they take the machine down with
them.

So the expensive things queue. Loading a model, generating an image, starting
an engine: each takes gigabytes, none benefits from running beside another,
and serialising them costs a little waiting instead of a crash.

Two properties matter more than the queue itself. Waiting is bounded, so a
stuck job cannot silently freeze everything behind it. And when there is
genuinely not enough memory, the answer is a clear refusal now rather than an
allocation that fails halfway through -- HAYAT can say "there is 2.4GB free
and this needs 4" long before Windows starts killing processes.

This deliberately does not try to free anything. The memory belongs to other
programs the owner is using, and quietly unloading someone else's model to
make room is the kind of helpfulness nobody asks for twice.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


#: Below this, starting anything large is asking for trouble. Windows begins
#: paging hard well before it reports an actual failure, so the useful limit
#: sits above zero by a wide margin.
CRITICAL_FREE_BYTES = 1_500_000_000
#: Enough headroom that the system stays responsive while a big job runs.
COMFORTABLE_FREE_BYTES = 3_000_000_000

#: How long to wait for the machine to free up before giving up on a job.
DEFAULT_WAIT_SECONDS = 120.0


@dataclass
class Reservation:
    """One heavy job, while it holds the lane."""

    label: str
    needs_bytes: int = 0
    waited: float = 0.0
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "needs_gb": round(self.needs_bytes / 1024**3, 2),
            "waited_seconds": round(self.waited, 1), "seconds": round(self.elapsed, 1),
        }


class NotEnoughMemory(RuntimeError):
    """Raised when a job cannot start because the machine has no room."""


class ResourceGate:
    """Serialises memory-hungry work and refuses it when there is no room."""

    def __init__(self, critical_free: int = CRITICAL_FREE_BYTES) -> None:
        self._lane = threading.Lock()
        self._state = threading.RLock()
        self._current: Reservation | None = None
        self._waiting = 0
        self._admitted = 0
        self._refused = 0
        self.critical_free = critical_free

    # ------------------------------------------------------------ measuring

    def free_bytes(self) -> int | None:
        from app.hardware import profile

        return profile(refresh=True).available_ram_bytes

    def pressure(self) -> dict[str, Any]:
        """What the memory situation is, in terms a person can act on."""
        from app.hardware import profile

        hardware = profile(refresh=True)
        total = hardware.total_ram_bytes or 0
        # Through free_bytes() rather than the profile directly, so every
        # decision and every report read the same number. Two sources of
        # truth here would mean refusing a job while reporting there is room.
        free = self.free_bytes() or 0
        used_percent = round((1 - free / total) * 100) if total else None
        return {
            "total_gb": round(total / 1024**3, 1) if total else None,
            "free_gb": round(free / 1024**3, 1) if free else None,
            "used_percent": used_percent,
            "critical": free < self.critical_free if free else False,
            "tight": free < COMFORTABLE_FREE_BYTES if free else False,
            "busy_with": self._current.to_dict() if self._current else None,
            "waiting": self._waiting,
        }

    def _room_for(self, needs_bytes: int) -> bool:
        free = self.free_bytes()
        if free is None:
            # Unknown is not permission. Refusing on a machine we cannot
            # measure is safer than allocating gigabytes into the dark.
            return needs_bytes <= 0
        return free - needs_bytes >= self.critical_free

    # -------------------------------------------------------------- the lane

    @contextmanager
    def heavy(self, label: str, needs_bytes: int = 0,
              wait: float = DEFAULT_WAIT_SECONDS) -> Iterator[Reservation]:
        """Run one expensive job, alone, once there is room for it.

        Blocks while another heavy job holds the lane, then again while the
        machine is too full. Both waits are bounded: a job that cannot start
        raises rather than queueing behind a problem nobody is watching.
        """
        reservation = Reservation(label=label, needs_bytes=needs_bytes)
        deadline = time.monotonic() + max(0.0, wait)
        waited_from = time.monotonic()

        with self._state:
            self._waiting += 1
        try:
            remaining = max(0.0, deadline - time.monotonic())
            if not self._lane.acquire(timeout=remaining):
                with self._state:
                    self._refused += 1
                raise NotEnoughMemory(
                    f"{label} waited {wait:.0f}s for "
                    f"{self._current.label if self._current else 'another job'} and gave up."
                )
        finally:
            with self._state:
                self._waiting -= 1

        try:
            while not self._room_for(needs_bytes):
                if time.monotonic() >= deadline:
                    with self._state:
                        self._refused += 1
                    pressure = self.pressure()
                    raise NotEnoughMemory(
                        f"Not enough memory for {label}: {pressure['free_gb']}GB free, "
                        f"and it needs about {needs_bytes / 1024**3:.1f}GB with room to spare. "
                        "Close something large and ask again."
                    )
                time.sleep(1.0)

            reservation.waited = time.monotonic() - waited_from
            with self._state:
                self._current = reservation
                self._admitted += 1
            yield reservation
        finally:
            with self._state:
                self._current = None
            self._lane.release()

    # ------------------------------------------------------------- reporting

    @property
    def busy(self) -> bool:
        with self._state:
            return self._current is not None

    def status(self) -> dict[str, Any]:
        with self._state:
            counters = {"admitted": self._admitted, "refused": self._refused}
        return self.pressure() | counters

    def report(self) -> str:
        state = self.status()
        lines = [
            f"Memory: {state['free_gb']}GB free of {state['total_gb']}GB "
            f"({state['used_percent']}% in use)."
        ]
        if state["busy_with"]:
            lines.append(f"  running: {state['busy_with']['label']}")
        if state["waiting"]:
            lines.append(f"  queued: {state['waiting']}")
        if state["critical"]:
            lines.append("  Memory is very tight -- I'll hold off on anything large.")
        elif state["tight"]:
            lines.append("  Memory is a bit tight, so big jobs will run one at a time.")
        return "\n".join(lines)


#: One gate per process. Heavy work is requested from several places and they
#: all need to queue behind the same lane.
GATE = ResourceGate()


def heavy(label: str, needs_bytes: int = 0, wait: float = DEFAULT_WAIT_SECONDS):
    """Shorthand for the shared gate."""
    return GATE.heavy(label, needs_bytes, wait)
