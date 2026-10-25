"""Camera activation that always ends, and always leaves a record.

The camera can already be turned on and off. What was missing is the property
that makes "activate when needed" safe to offer: an activation that cannot
outlive its reason. A camera switched on by a plan step and left on because
the plan failed three steps later is the exact shape of the thing HAYAT exists
not to be.

So a session is bounded by construction. It has a stated reason, a hard
maximum duration enforced by a watchdog rather than by the code remembering to
close, and it releases the device on every exit path including an exception.
Every activation and release is published and journalled, so the question "was
the camera on, when, and why" has an answer Mohsin can read rather than a
reassurance he has to accept.

Privacy mode is checked at activation, not only at capture. A privacy switch
that lets the light come on and merely declines to keep the picture is not a
privacy switch.
"""
from __future__ import annotations

import json
import inspect
import logging
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


JOURNAL = PROJECT_ROOT / "data" / "camera" / "activations.jsonl"

#: No single activation may exceed this. A minute is far longer than looking
#: at something takes, and short enough that a leak is a minute rather than a
#: day.
MAX_SESSION_SECONDS = 60.0
DEFAULT_SESSION_SECONDS = 20.0


@dataclass
class Activation:
    """One period during which the camera was on."""

    reason: str
    activation_id: str = field(default_factory=lambda: f"cam-{uuid.uuid4().hex[:10]}")
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    ended_at: str | None = None
    seconds: float = 0.0
    device_index: int | None = None
    frames: int = 0
    released: bool = False
    forced_off: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "activation_id": self.activation_id, "reason": self.reason[:300],
            "started_at": self.started_at, "ended_at": self.ended_at,
            "seconds": round(self.seconds, 2), "device_index": self.device_index,
            "frames": self.frames, "released": self.released,
            "forced_off": self.forced_off, "error": self.error,
        }


class CameraPrivacyError(PermissionError):
    """Raised when the camera may not be activated at all."""


class CameraSessions:
    """Bounded, recorded camera activation."""

    def __init__(self, manager: Any, authority: Any = None, events: Any = None,
                 journal: Path | None = None) -> None:
        self.manager = manager
        self.authority = authority
        self.events = events
        self.journal = journal or JOURNAL
        self._lock = threading.RLock()
        self._current: Activation | None = None
        self._history: list[Activation] = []

    # ------------------------------------------------------------ the gate

    def may_activate(self) -> tuple[bool, str]:
        """Whether the camera may come on right now, and why not if not.

        Checked before the device opens rather than before a frame is kept: a
        privacy switch that allows the light to come on and merely declines to
        save the picture is not a privacy switch.
        """
        if self.authority is None:
            return True, "ok"
        if getattr(self.authority, "privacy_mode", False):
            return False, "privacy mode is on; the camera stays off until you turn it off"
        try:
            from app.safety.authority import ActionRequest

            decision = self.authority.evaluate(ActionRequest(tool="camera_use", category="camera_use"))
        except Exception:
            return True, "ok"
        if not decision.allowed:
            return False, decision.reason
        return True, "ok"

    # -------------------------------------------------------- the session

    @property
    def active(self) -> Activation | None:
        with self._lock:
            return self._current

    @contextmanager
    def activate(self, reason: str, *, seconds: float = DEFAULT_SESSION_SECONDS,
                 device_index: int | None = None) -> Iterator[Activation]:
        """Turn the camera on for a stated reason, and guarantee it goes off.

        The watchdog is the point. Closing on the way out of the block handles
        the ordinary case and the exception case, but not a caller that blocks
        forever inside it -- and "the camera stayed on because something hung"
        is not a failure mode worth leaving open. The timer closes the device
        whatever the block is doing.
        """
        reason = " ".join(str(reason).split()).strip() or "unstated"
        allowed, refusal = self.may_activate()
        if not allowed:
            raise CameraPrivacyError(refusal)

        with self._lock:
            if self._current is not None:
                raise RuntimeError(
                    f"the camera is already on for: {self._current.reason}"
                )
            activation = Activation(reason=reason, device_index=device_index)
            self._current = activation

        budget = max(1.0, min(float(seconds), MAX_SESSION_SECONDS))
        started = datetime.now(UTC)
        watchdog = threading.Timer(budget, self._force_off, args=(activation,))
        watchdog.daemon = True

        try:
            self.manager.on(device_index)
            self._publish("CAMERA_CHANGED", activation, extra={"event": "activated"})
            watchdog.start()
            yield activation
        except Exception as exc:
            activation.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            watchdog.cancel()
            activation.seconds = (datetime.now(UTC) - started).total_seconds()
            activation.ended_at = datetime.now(UTC).isoformat()
            try:
                self.manager.off()
                activation.released = True
            except Exception as exc:
                activation.released = False
                activation.error = activation.error or f"could not release the camera: {exc}"
            with self._lock:
                self._current = None
                self._history.append(activation)
                del self._history[:-200]
            self._publish("CAMERA_CHANGED", activation, extra={"event": "released"})
            self._record(activation)

    def _force_off(self, activation: Activation) -> None:
        """Watchdog: close the device when the budget runs out."""
        with self._lock:
            if self._current is not activation:
                return
        activation.forced_off = True
        try:
            self.manager.off()
            activation.released = True
        except Exception:
            activation.released = False
        self._publish("CAMERA_FAILED", activation, extra={"event": "forced_off"})

    # -------------------------------------------------------- convenience

    def look(self, reason: str, prompt: str = "", *, seconds: float = DEFAULT_SESSION_SECONDS,
             shots: int = 4, interval_ms: int = 300) -> dict[str, Any]:
        """Open the camera, observe a short sequence, and close."""
        with self.activate(reason, seconds=seconds) as activation:
            parameters = inspect.signature(self.manager.observe).parameters
            if "shots" in parameters:
                observation = self.manager.observe(prompt, shots=shots, interval_ms=interval_ms) if prompt else self.manager.observe(shots=shots, interval_ms=interval_ms)
            else:  # compatibility with a small adapter implementing the old contract
                observation = self.manager.observe(prompt) if prompt else self.manager.observe()
            activation.frames = int(observation.get("frames_captured", 1))
        return {"observation": observation, "activation": activation.to_dict()}

    # ------------------------------------------------------------ records

    def _publish(self, event_type: str, activation: Activation, extra: dict[str, Any] | None = None) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(event_type, "camera", activation.to_dict() | (extra or {}))
        except Exception:
            log_swallowed(_log, "publishing a camera session event")

    def _record(self, activation: Activation) -> None:
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(activation.to_dict(), default=str) + "\n")
        except Exception:
            log_swallowed(_log, "writing the camera activation journal", level=logging.WARNING)

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in self._history[-max(1, limit):]]

    def status(self) -> dict[str, Any]:
        current = self.active
        allowed, reason = self.may_activate()
        return {
            "on": current is not None,
            "current": current.to_dict() if current else None,
            "may_activate": allowed, "reason": reason,
            "maximum_seconds": MAX_SESSION_SECONDS,
            "activations_recorded": len(self._history),
            "journal": str(self.journal),
        }
