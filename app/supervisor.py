"""Keeps the local inference dependency alive without ever killing what it did not start.

The proven reliability problem on this machine is a single one: Ollama is not
running, or stops running, and every reasoning path fails at once. This module
supervises exactly that, and nothing else -- not a general service framework.

Two rules shape the design.

The first is ownership. A process this runtime started is a process it may
stop. A process it merely found is somebody else's -- the user's own Ollama,
started before D:\\AI opened, serving other things -- and killing it by name
would be a destructive act on a resource that was never ours. So there is no
kill-by-image-name anywhere here: recovery acts on an exact PID that this
runtime recorded when it did the spawning.

The second is that a running process is not a working service. `ollama.exe`
existing proves only that a process exists; it proves nothing about whether
the port answers, whether the configured model is installed, or whether
generation actually returns tokens. Health is therefore layered, and HEALTHY
is reserved for the level that was really observed.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class ServiceState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    RESTARTING = "RESTARTING"
    COOLDOWN = "COOLDOWN"
    STOPPING = "STOPPING"
    UNKNOWN = "UNKNOWN"


class ProcessOwnership(StrEnum):
    """Who started the process. Decides whether we may ever stop it."""

    STARTED_BY_AI = "STARTED_BY_AI"
    EXTERNAL = "EXTERNAL"
    UNKNOWN = "UNKNOWN"


class HealthLevel(StrEnum):
    """How far readiness was actually confirmed.

    Deliberately ordered: each level implies the ones before it. Nothing may
    report HEALTHY on the strength of a lower level having passed.
    """

    NONE = "NONE"
    PROCESS = "PROCESS"          # something is running
    HTTP = "HTTP"                # the API answers
    MODEL = "MODEL"              # the configured model is installed
    GENERATION = "GENERATION"    # it actually produced tokens


@dataclass
class HealthResult:
    level: HealthLevel = HealthLevel.NONE
    latency_ms: float | None = None
    detail: str = ""
    models: list[str] = field(default_factory=list)
    error: BaseException | None = None

    @property
    def http_ready(self) -> bool:
        return self.level in {HealthLevel.HTTP, HealthLevel.MODEL, HealthLevel.GENERATION}

    @property
    def model_ready(self) -> bool:
        return self.level in {HealthLevel.MODEL, HealthLevel.GENERATION}

    @property
    def generation_verified(self) -> bool:
        return self.level is HealthLevel.GENERATION


@dataclass
class ServiceRecord:
    service_id: str
    service_type: str = "LOCAL_INFERENCE"
    state: ServiceState = ServiceState.UNKNOWN
    configured: bool = False
    endpoint: str | None = None
    model: str | None = None
    pid: int | None = None
    ownership: ProcessOwnership = ProcessOwnership.UNKNOWN
    executable: str | None = None
    started_at: str | None = None
    ready_at: str | None = None
    last_health_check: str | None = None
    health_latency_ms: float | None = None
    health_level: HealthLevel = HealthLevel.NONE
    restart_count: int = 0
    consecutive_failures: int = 0
    last_failure_id: str | None = None
    next_retry_at: float | None = None
    last_error: str | None = None
    recovery_timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_id": self.service_id, "service_type": self.service_type,
            "state": self.state.value, "configured": self.configured,
            "endpoint": self.endpoint, "model": self.model, "pid": self.pid,
            "ownership": self.ownership.value, "executable": self.executable,
            "started_at": self.started_at, "ready_at": self.ready_at,
            "last_health_check": self.last_health_check,
            "health_latency_ms": self.health_latency_ms,
            "health_level": self.health_level.value,
            "http_ready": self.health_level in {HealthLevel.HTTP, HealthLevel.MODEL, HealthLevel.GENERATION},
            "model_ready": self.health_level in {HealthLevel.MODEL, HealthLevel.GENERATION},
            "generation_verified": self.health_level is HealthLevel.GENERATION,
            "restart_count": self.restart_count,
            "consecutive_failures": self.consecutive_failures,
            "last_failure_id": self.last_failure_id, "next_retry_at": self.next_retry_at,
            "last_error": self.last_error, "recovery_timings": dict(self.recovery_timings),
        }


# --------------------------------------------------------------- circuit breaker


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    COOLDOWN = "COOLDOWN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Bounds restart attempts so a service that cannot start cannot spin.

    A dependency that crashes on every launch would otherwise be restarted
    forever, burning CPU and spawning processes faster than they die. After
    `threshold` consecutive failures the circuit opens and stays open for a
    cooldown; one trial attempt is then allowed, and only a real success
    closes it again.
    """

    def __init__(self, threshold: int = 3, cooldown_seconds: float = 60.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.threshold = max(1, threshold)
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._state = CircuitState.CLOSED
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._refresh()
            return self._state

    def _refresh(self) -> None:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if self._clock() - self._opened_at >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN

    def allows(self) -> bool:
        """Whether another attempt may be made right now."""
        with self._lock:
            self._refresh()
            return self._state in {CircuitState.CLOSED, CircuitState.HALF_OPEN}

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self.threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    def retry_after(self) -> float | None:
        with self._lock:
            self._refresh()
            if self._state is not CircuitState.OPEN or self._opened_at is None:
                return None
            return max(0.0, self.cooldown_seconds - (self._clock() - self._opened_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value, "consecutive_failures": self._failures,
            "threshold": self.threshold, "retry_after_seconds": self.retry_after(),
        }


# ------------------------------------------------------------------ the service


#: Where Ollama installs itself on Windows when it is not on PATH.
_WINDOWS_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
    r"%ProgramFiles%\Ollama\ollama.exe",
    r"%ProgramFiles(x86)%\Ollama\ollama.exe",
    r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe",
)


class OllamaService:
    """Discovery, health, and owned-process lifecycle for the local model server."""

    service_type = "LOCAL_INFERENCE"

    def __init__(self, base_url: str = "", model: str = "", *, executable: str | None = None,
                 start_timeout: float = 60.0, http_client: Any = None,
                 extra_env: dict[str, str] | None = None) -> None:
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model
        self.start_timeout = start_timeout
        self._http = http_client
        # Environment for an owned process. OLLAMA_HOST is how a managed
        # instance is bound to its own port, which is what makes it possible
        # to test owned-process recovery without going anywhere near a
        # provider the user started on the default port.
        self._extra_env = dict(extra_env or {})
        if extra_env is None and self._portable():
            # Launched from HAYAT's own folder: a restart after a crash must
            # bring back HAYAT's Ollama on HAYAT's port with HAYAT's models,
            # never the machine's on the default port.
            from app import portable

            self._extra_env = portable.environment()
        self._process: subprocess.Popen | None = None
        self.record = ServiceRecord(
            service_id="local_inference", service_type=self.service_type,
            endpoint=self.base_url, model=model or None,
            executable=executable or self.locate_executable(),
        )
        self.record.configured = bool(self.record.endpoint)
        if not self.record.configured:
            self.record.state = ServiceState.NOT_CONFIGURED

    # ------------------------------------------------------------- discovery

    @staticmethod
    def locate_executable() -> str | None:
        """Find Ollama on PATH, then in the places its installer uses.

        Returns None rather than guessing a path that does not exist: an
        executable we cannot find is a fact worth reporting, not something to
        paper over with a hard-coded default that will fail later and less
        clearly.
        """
        if OllamaService._portable():
            from app import portable

            own = portable.ollama_executable()
            if own.is_file():
                return str(own)
        found = shutil.which("ollama")
        if found:
            return found
        for candidate in _WINDOWS_CANDIDATES:
            expanded = os.path.expandvars(candidate)
            if "%" not in expanded and Path(expanded).is_file():
                return expanded
        return None

    @staticmethod
    def _portable() -> bool:
        """Whether this process was launched to use the Ollama in runtime/."""
        return os.getenv("HAYAT_PORTABLE", "").strip() == "1"

    # ---------------------------------------------------------------- health

    def probe(self, *, verify_generation: bool = False) -> HealthResult:
        """Establish how far this service is actually ready.

        Each level is observed, never assumed. A service answering /api/tags
        without the configured model installed is DEGRADED, not HEALTHY: the
        very next real request would fail.
        """
        import httpx

        client = self._http or httpx
        started = time.perf_counter()
        try:
            response = client.get(f"{self.base_url}/api/tags", timeout=5.0)
            response.raise_for_status()
            models = [item["name"] for item in response.json().get("models", [])]
        except Exception as exc:
            return HealthResult(HealthLevel.NONE, detail=f"endpoint unreachable: {exc}", error=exc)
        latency = (time.perf_counter() - started) * 1000

        if self.model and not any(name == self.model or name.split(":")[0] == self.model.split(":")[0] for name in models):
            return HealthResult(
                HealthLevel.HTTP, latency, f"model {self.model!r} is not installed", models,
            )
        if not verify_generation:
            return HealthResult(HealthLevel.MODEL if self.model else HealthLevel.HTTP, latency, "api reachable", models)

        try:
            generation = client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model or (models[0] if models else ""),
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False, "options": {"num_predict": 1},
                },
                timeout=60.0,
            )
            generation.raise_for_status()
            generation.json()
        except Exception as exc:
            return HealthResult(HealthLevel.MODEL, latency, f"generation failed: {exc}", models, error=exc)
        return HealthResult(HealthLevel.GENERATION, latency, "generation verified", models)

    # ------------------------------------------------------------- lifecycle

    def adopt_external_if_running(self) -> bool:
        """Recognise an Ollama that was already serving before we started.

        Reusing it is the right behaviour -- starting a second one would fight
        over the port -- but it must be marked EXTERNAL so nothing later
        decides it is ours to stop.
        """
        health = self.probe()
        if not health.http_ready:
            return False
        if self.record.ownership is not ProcessOwnership.STARTED_BY_AI:
            self.record.ownership = ProcessOwnership.EXTERNAL
        self._apply_health(health)
        return True

    def start(self) -> tuple[bool, str]:
        """Start an Ollama this runtime will own, if one is not already serving."""
        if self.adopt_external_if_running():
            return True, "reused an Ollama that was already running"
        executable = self.record.executable or self.locate_executable()
        if not executable:
            self.record.state = ServiceState.NOT_CONFIGURED
            self.record.last_error = "the ollama executable could not be located"
            return False, self.record.last_error
        self.record.executable = executable
        self.record.state = ServiceState.STARTING
        self.record.started_at = datetime.now(UTC).isoformat()
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._process = subprocess.Popen(
                [executable, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=creationflags,
                env={**os.environ, **self._extra_env} if self._extra_env else None,
            )
        except Exception as exc:
            self.record.state = ServiceState.FAILED
            self.record.last_error = f"could not start ollama: {exc}"
            return False, self.record.last_error
        # The PID is recorded before readiness: if this runtime spawned it,
        # this runtime owns it even if it never becomes healthy, and only an
        # owned PID may be stopped later.
        self.record.pid = self._process.pid
        self.record.ownership = ProcessOwnership.STARTED_BY_AI
        return self.wait_until_ready()

    def wait_until_ready(self, timeout: float | None = None) -> tuple[bool, str]:
        deadline = time.monotonic() + (self.start_timeout if timeout is None else timeout)
        last = "no readiness probe completed"
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                self.record.state = ServiceState.FAILED
                self.record.last_error = f"ollama exited during startup with code {self._process.returncode}"
                return False, self.record.last_error
            health = self.probe()
            self._apply_health(health)
            last = health.detail
            if health.model_ready or (health.http_ready and not self.model):
                self.record.ready_at = datetime.now(UTC).isoformat()
                return True, "ready"
            time.sleep(0.25)
        self.record.state = ServiceState.DEGRADED if self.record.health_level is not HealthLevel.NONE else ServiceState.FAILED
        self.record.last_error = f"ollama did not become ready within {self.start_timeout:.0f}s: {last}"
        return False, self.record.last_error

    def stop(self) -> tuple[bool, str]:
        """Stop only a process this runtime started, addressed by exact PID.

        An EXTERNAL Ollama is refused outright. This is the rule that makes
        automatic recovery safe to run at all: the worst case is that we
        decline to act, never that we terminate somebody else's service.
        """
        if self.record.ownership is not ProcessOwnership.STARTED_BY_AI:
            return False, (
                f"this Ollama is {self.record.ownership.value}; HAYAT did not start it "
                "and will not stop it"
            )
        if self._process is None or self.record.pid is None:
            return False, "no owned process handle is recorded"
        from app.process import stop_owned_process

        self.record.state = ServiceState.STOPPING
        pid = self.record.pid
        stopped, detail = stop_owned_process(self._process, pid)
        self._process = None
        self.record.pid = None
        self.record.ownership = ProcessOwnership.UNKNOWN
        self.record.state = ServiceState.STOPPED
        self.record.health_level = HealthLevel.NONE
        if not stopped:
            return False, f"could not stop the owned ollama process: {detail}"
        return True, f"stopped the Ollama process HAYAT started ({detail})"

    @property
    def owned_process_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _apply_health(self, health: HealthResult) -> None:
        record = self.record
        record.last_health_check = datetime.now(UTC).isoformat()
        record.health_latency_ms = health.latency_ms
        record.health_level = health.level
        if health.generation_verified or health.model_ready:
            record.state = ServiceState.HEALTHY
            record.consecutive_failures = 0
            record.last_error = None
        elif health.http_ready:
            record.state = ServiceState.DEGRADED
            record.last_error = health.detail
        else:
            record.state = ServiceState.FAILED
            record.last_error = health.detail


# --------------------------------------------------------------- the supervisor


class RuntimeSupervisor:
    """Watches the local inference service and recovers it, once at a time."""

    def __init__(self, service: OllamaService, *, events: Any = None, failures: Any = None,
                 breaker: CircuitBreaker | None = None) -> None:
        self.service = service
        self.events = events
        self.failures = failures
        self.breaker = breaker or CircuitBreaker()
        # Guards the act of recovering, not the service record. Held only
        # around bookkeeping; the slow parts (spawn, readiness polling, HTTP)
        # run outside it so status stays answerable during a recovery.
        self._lock = threading.RLock()
        self._recovering: threading.Event | None = None
        self._recovery_result: tuple[bool, str] = (False, "no recovery has run")

    # ------------------------------------------------------------- reporting

    def inspect(self, *, verify_generation: bool = False) -> dict[str, Any]:
        """Probe, then report. What `/provider status` should show a person.

        Reports what is true now rather than what was true at the last
        request. Classification of an already-running instance as EXTERNAL
        happens here too, so simply looking at status can never make this
        runtime believe it owns a process it did not start.
        """
        health = self.service.probe(verify_generation=verify_generation)
        if health.http_ready and self.service.record.ownership is ProcessOwnership.UNKNOWN:
            self.service.record.ownership = ProcessOwnership.EXTERNAL
        if not health.http_ready and self.service.record.ownership is ProcessOwnership.UNKNOWN:
            self.service.record.state = ServiceState.STOPPED
            self.service.record.last_health_check = datetime.now(UTC).isoformat()
            self.service.record.last_error = health.detail
            return self.status()
        self.service._apply_health(health)
        return self.status()

    def status(self) -> dict[str, Any]:
        """Deterministic, model-free service status. Reports last known state."""
        return {
            **self.service.record.to_dict(),
            "circuit": self.breaker.to_dict(),
            "recovery_in_progress": self._recovering is not None,
        }

    def _publish(self, event_type: str, **payload: Any) -> None:
        if self.events is None:
            return
        try:
            from app.execution import current_context

            context = current_context()
            self.events.publish(
                event_type, "supervisor", {**self.service.record.to_dict(), **payload},
                session_id=context.session_id, turn_id=context.turn_id, task_id=context.task_id,
            )
        except Exception:
            log_swallowed(_log, "publishing a supervisor event")

    def _record_failure(self, message: str, exc: BaseException | None = None) -> str | None:
        if self.failures is None:
            return None
        try:
            from app.failures import FailureClass

            fields = {
                "stage": "service", "service": self.service.record.service_id,
                "provider": "ollama", "dependency": "Ollama",
                "endpoint": self.service.record.endpoint,
                "failure_class": FailureClass.DEPENDENCY_DOWN,
            }
            failure = (
                self.failures.from_exception(exc, **fields) if exc is not None
                else self.failures.record(exception_message=message, **fields)
            )
            self.service.record.last_failure_id = failure.failure_id
            return failure.failure_id
        except Exception:
            return None

    # -------------------------------------------------------------- startup

    def ensure_started(self) -> tuple[bool, str]:
        """Bring the service up at startup, reusing an external one if present."""
        if not self.service.record.configured:
            return False, "no local inference endpoint is configured"
        if self.service.adopt_external_if_running():
            self._publish("SERVICE_HEALTHY", reason="reused an existing Ollama")
            return True, "reused an Ollama that was already running"
        self._publish("SERVICE_STARTING")
        started, detail = self.service.start()
        if started:
            self.breaker.record_success()
            self._publish("SERVICE_HEALTHY", reason=detail)
        else:
            self.breaker.record_failure()
            self._record_failure(detail)
            self._publish("SERVICE_FAILED", reason=detail)
        return started, detail

    # -------------------------------------------------------------- recovery

    def recover(self, reason: str = "provider request failed", *, timeout: float = 120.0) -> tuple[bool, str]:
        """Restore the service. Exactly one caller does the work.

        Several turns discovering the provider is down at the same moment is
        the normal case, not the exceptional one -- and each of them starting
        its own Ollama would be the worst possible response. The first caller
        performs the recovery; the rest wait for its result and are told what
        happened, rather than duplicating it.
        """
        with self._lock:
            in_flight = self._recovering
            if in_flight is None:
                if not self.breaker.allows():
                    retry = self.breaker.retry_after()
                    self.service.record.state = ServiceState.COOLDOWN
                    self.service.record.next_retry_at = retry
                    return False, (
                        f"recovery is in cooldown after repeated failures; "
                        f"retry in {retry:.0f}s" if retry else "recovery is in cooldown"
                    )
                self._recovering = threading.Event()
                owner = True
            else:
                owner = False

        if not owner:
            in_flight.wait(timeout=timeout)
            return self._recovery_result

        result: tuple[bool, str] = (False, "recovery did not complete")
        try:
            result = self._perform_recovery(reason)
        except Exception as exc:  # a crash here must still release the waiters
            self._record_failure(f"recovery raised: {exc}", exc)
            result = (False, f"recovery failed: {type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._recovery_result = result
                event, self._recovering = self._recovering, None
            if event is not None:
                event.set()
        return result

    def _perform_recovery(self, reason: str) -> tuple[bool, str]:
        record = self.service.record
        timings: dict[str, float] = {}
        began = time.perf_counter()

        health = self.service.probe()
        timings["failure_detection_ms"] = round((time.perf_counter() - began) * 1000, 1)
        if health.model_ready or (health.http_ready and not self.service.model):
            # It came back on its own, or was never actually down.
            self.service._apply_health(health)
            self.breaker.record_success()
            self._publish("SERVICE_HEALTHY", reason="service was already reachable")
            return True, "the service was already reachable"

        if record.ownership is ProcessOwnership.EXTERNAL:
            # Someone else's Ollama stopped answering. We may report it and we
            # may start our own if the port is now free, but we never assume
            # authority over the process that was there before us.
            record.state = ServiceState.FAILED
            detail = "the external Ollama is not responding; HAYAT did not start it and will not manage it"
            self._record_failure(detail, health.error)
            self._publish("SERVICE_FAILED", reason=detail)
            self.breaker.record_failure()
            return False, detail

        record.state = ServiceState.RESTARTING
        record.restart_count += 1
        record.consecutive_failures += 1
        self._record_failure(f"{reason}: {health.detail}", health.error)
        self._publish("SERVICE_RESTARTING", reason=reason)

        if self.service.owned_process_alive:
            self.service.stop()

        spawn_started = time.perf_counter()
        started, detail = self.service.start()
        timings["spawn_ms"] = round((time.perf_counter() - spawn_started) * 1000, 1)
        if not started:
            record.state = ServiceState.FAILED
            self.breaker.record_failure()
            self._record_failure(detail)
            self._publish("SERVICE_FAILED", reason=detail)
            record.next_retry_at = self.breaker.retry_after()
            return False, detail

        timings["http_ready_ms"] = round((time.perf_counter() - spawn_started) * 1000, 1)
        verified = self.service.probe(verify_generation=True)
        timings["generation_verified_ms"] = round((time.perf_counter() - spawn_started) * 1000, 1)
        timings["total_recovery_ms"] = round((time.perf_counter() - began) * 1000, 1)
        record.recovery_timings = timings
        self.service._apply_health(verified)

        if not verified.generation_verified:
            # Reachable but not proven to generate: DEGRADED is the honest
            # state, and RECOVERED is not claimed without the evidence for it.
            record.state = ServiceState.DEGRADED
            self._publish("SERVICE_DEGRADED", reason=verified.detail, timings=timings)
            self.breaker.record_failure()
            return False, f"the service restarted but generation was not verified: {verified.detail}"

        record.consecutive_failures = 0
        self.breaker.record_success()
        self._publish("SERVICE_RECOVERED", reason="generation verified after restart", timings=timings)
        return True, "the local model service was restarted and verified"

    # -------------------------------------------------------------- shutdown

    def shutdown(self, *, stop_owned: bool = True) -> str:
        """Release what we own and leave alone what we do not."""
        record = self.service.record
        if record.ownership is not ProcessOwnership.STARTED_BY_AI:
            return f"left the {record.ownership.value} Ollama running"
        if not stop_owned:
            return "left the AI-owned Ollama running by configuration"
        stopped, detail = self.service.stop()
        return detail if stopped else f"could not stop the owned Ollama: {detail}"


def retry_is_safe(failure_class: Any, *, side_effecting: bool) -> bool:
    """Whether a failed operation may be retried automatically after recovery.

    Read-only inference can be replayed once the dependency returns. Anything
    that may have already acted on the world cannot be: the provider call
    failing does not prove the side effect did not happen, and a duplicate
    send or a duplicate file write is a worse outcome than an error the user
    can see and repeat deliberately.
    """
    from app.failures import RETRYABLE_CLASSES

    if side_effecting:
        return False
    return failure_class in RETRYABLE_CLASSES
