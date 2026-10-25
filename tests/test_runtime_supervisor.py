"""Supervision of the local inference dependency.

Two properties matter more than the rest and are tested hardest: a process
D:\\AI did not start is never stopped by D:\\AI, and HEALTHY is never claimed
on weaker evidence than was actually observed.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace


from app.failures import FailureClass, FailureLog
from app.supervisor import (
    CircuitBreaker,
    CircuitState,
    HealthLevel,
    OllamaService,
    ProcessOwnership,
    RuntimeSupervisor,
    ServiceState,
    retry_is_safe,
)


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def test_model_free_runtime_never_selects_a_live_conversation_provider(monkeypatch):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        def unexpected_provider(*args, **kwargs):
            raise AssertionError("Model-free mode must not select a live provider")

        monkeypatch.setattr(runtime.intelligent_router, "provider", unexpected_provider)
        for capability in ("conversation", "summarization", "reasoning"):
            assert runtime.conversation.model_selector(capability, ["DEEP"]) is None
        reply = runtime.conversation.reply("Explain a fictional book.", ["DEEP"])
        assert "unavailable" in reply or "No model provider" in reply
    finally:
        runtime.close()


class FakeHttp:
    """A stand-in endpoint whose behaviour the test controls exactly."""

    def __init__(self, *, up: bool = True, models: list[str] | None = None, generates: bool = True) -> None:
        self.up = up
        self.models = models if models is not None else ["qwen3.5:latest"]
        self.generates = generates
        self.generation_calls = 0

    def get(self, url, **kwargs):
        if not self.up:
            raise ConnectionRefusedError(
                "[WinError 10061] No connection could be made because the target machine actively refused it"
            )
        return FakeResponse({"models": [{"name": name} for name in self.models]})

    def post(self, url, **kwargs):
        self.generation_calls += 1
        if not self.up:
            raise ConnectionRefusedError("refused")
        if not self.generates:
            raise RuntimeError("model failed to generate")
        return FakeResponse({"message": {"content": "ok"}})


def _service(http: FakeHttp, model: str = "qwen3.5:latest") -> OllamaService:
    return OllamaService("http://localhost:11434", model, executable="C:/fake/ollama.exe", http_client=http)


# ---------------------------------------------------------------- health levels


def test_a_reachable_endpoint_with_the_model_present_is_model_ready():
    health = _service(FakeHttp()).probe()
    assert health.level is HealthLevel.MODEL
    assert health.http_ready and health.model_ready
    assert not health.generation_verified


def test_a_reachable_endpoint_missing_the_model_is_not_healthy():
    """§29. The API answering proves the port is open, nothing more."""
    service = _service(FakeHttp(models=["something-else:latest"]))
    health = service.probe()

    assert health.level is HealthLevel.HTTP
    assert not health.model_ready
    service._apply_health(health)
    assert service.record.state is ServiceState.DEGRADED


def test_generation_is_only_claimed_when_generation_actually_happened():
    http = FakeHttp()
    health = _service(http).probe(verify_generation=True)
    assert health.level is HealthLevel.GENERATION
    assert http.generation_calls == 1


def test_a_service_that_answers_but_cannot_generate_is_degraded_not_healthy():
    service = _service(FakeHttp(generates=False))
    health = service.probe(verify_generation=True)
    assert not health.generation_verified
    assert health.level is HealthLevel.MODEL


def test_an_unreachable_endpoint_reports_the_refusal_as_evidence():
    health = _service(FakeHttp(up=False)).probe()
    assert health.level is HealthLevel.NONE
    assert isinstance(health.error, ConnectionRefusedError)
    from app.failures import classify_exception

    assert classify_exception(health.error) is FailureClass.DEPENDENCY_DOWN


# ------------------------------------------------------------------- ownership


def test_an_ollama_that_was_already_running_is_marked_external():
    service = _service(FakeHttp())
    assert service.adopt_external_if_running()
    assert service.record.ownership is ProcessOwnership.EXTERNAL


def test_an_external_ollama_is_never_stopped():
    """The rule that makes automatic recovery safe to enable at all."""
    service = _service(FakeHttp())
    service.adopt_external_if_running()

    stopped, detail = service.stop()

    assert not stopped
    assert "did not start it" in detail
    assert service.record.ownership is ProcessOwnership.EXTERNAL


def test_an_unknown_process_is_never_stopped_either():
    service = _service(FakeHttp(up=False))
    assert service.record.ownership is ProcessOwnership.UNKNOWN
    stopped, _ = service.stop()
    assert not stopped


def test_shutdown_leaves_an_external_provider_running():
    service = _service(FakeHttp())
    service.adopt_external_if_running()
    supervisor = RuntimeSupervisor(service)

    message = supervisor.shutdown()

    assert "left the EXTERNAL Ollama running" in message
    assert service.record.ownership is ProcessOwnership.EXTERNAL


def test_startup_reuses_an_external_provider_instead_of_starting_a_second():
    service = _service(FakeHttp())
    supervisor = RuntimeSupervisor(service)

    started, detail = supervisor.ensure_started()

    assert started and "already running" in detail
    assert service.record.pid is None, "no process was spawned"


# -------------------------------------------------------------- circuit breaker


def test_the_circuit_opens_after_repeated_failures_and_bounds_attempts():
    clock = SimpleNamespace(now=0.0)
    breaker = CircuitBreaker(threshold=3, cooldown_seconds=30, clock=lambda: clock.now)

    for _ in range(2):
        breaker.record_failure()
        assert breaker.allows()

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert not breaker.allows(), "a crash loop must stop attempting"


def test_the_circuit_half_opens_after_cooldown_and_closes_on_success():
    clock = SimpleNamespace(now=0.0)
    breaker = CircuitBreaker(threshold=1, cooldown_seconds=30, clock=lambda: clock.now)
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN

    clock.now = 31.0
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.allows(), "exactly one trial attempt is permitted"

    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


def test_a_failed_trial_reopens_the_circuit_immediately():
    clock = SimpleNamespace(now=0.0)
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=30, clock=lambda: clock.now)
    breaker.record_failure(); breaker.record_failure()
    clock.now = 31.0
    assert breaker.state is CircuitState.HALF_OPEN

    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN, "one failed trial must not need the full threshold again"


def test_recovery_refuses_while_the_circuit_is_open():
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    breaker = CircuitBreaker(threshold=1, cooldown_seconds=300)
    breaker.record_failure()
    supervisor = RuntimeSupervisor(service, breaker=breaker)

    recovered, detail = supervisor.recover()

    assert not recovered
    assert "cooldown" in detail
    assert service.record.state is ServiceState.COOLDOWN


# ---------------------------------------------------------- single-flight


def test_only_one_recovery_runs_when_many_turns_discover_the_failure_at_once():
    """§31. Five simultaneous failures must not start five Ollamas."""
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    supervisor = RuntimeSupervisor(service)

    attempts: list[int] = []
    barrier = threading.Barrier(5)

    def slow_recovery(reason):
        attempts.append(1)
        time.sleep(0.2)
        return True, "recovered"

    supervisor._perform_recovery = slow_recovery

    results: list[tuple[bool, str]] = []

    def worker():
        barrier.wait()
        results.append(supervisor.recover())

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(attempts) == 1, "exactly one recovery owner"
    assert len(results) == 5
    assert all(result == (True, "recovered") for result in results), "waiters learn the outcome"


def test_status_stays_answerable_while_a_recovery_is_in_flight():
    """§28/§44. The control plane must not disappear behind a slow restart."""
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    supervisor = RuntimeSupervisor(service)

    entered = threading.Event()

    def slow_recovery(reason):
        entered.set()
        time.sleep(0.3)
        return True, "recovered"

    supervisor._perform_recovery = slow_recovery
    worker = threading.Thread(target=supervisor.recover)
    worker.start()
    entered.wait(timeout=2)

    started = time.perf_counter()
    status = supervisor.status()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.15, "status blocked behind the recovery lock"
    assert status["recovery_in_progress"] is True
    worker.join()


# -------------------------------------------------------------- recovery flow


def test_recovery_reports_success_without_restarting_a_service_that_returned():
    service = _service(FakeHttp())
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    supervisor = RuntimeSupervisor(service)

    recovered, detail = supervisor.recover()

    assert recovered and "already reachable" in detail
    assert service.record.restart_count == 0


def test_recovery_will_not_manage_an_external_provider_that_died():
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.EXTERNAL
    failures = FailureLog()
    supervisor = RuntimeSupervisor(service, failures=failures)

    recovered, detail = supervisor.recover()

    assert not recovered
    assert "will not manage" in detail
    assert service.record.restart_count == 0
    assert failures.last is not None and failures.last.failure_class is FailureClass.DEPENDENCY_DOWN


def test_a_provider_failure_becomes_a_failure_record_naming_the_dependency():
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.EXTERNAL
    failures = FailureLog()
    RuntimeSupervisor(service, failures=failures).recover()

    failure = failures.last
    assert failure.dependency == "Ollama"
    assert failure.service == "local_inference"
    assert "not an internet problem" in failure.summary()


def test_service_events_use_the_canonical_names(tmp_path):
    from app.events import EventBus

    bus = EventBus(tmp_path / "events.db")
    try:
        seen: list[str] = []
        for name in ("SERVICE_STARTING", "SERVICE_HEALTHY", "SERVICE_FAILED",
                     "SERVICE_RESTARTING", "SERVICE_RECOVERED", "SERVICE_DEGRADED"):
            bus.subscribe(name, lambda event: seen.append(event["event_type"]))

        service = _service(FakeHttp(up=False))
        service.record.ownership = ProcessOwnership.EXTERNAL
        RuntimeSupervisor(service, events=bus).recover()

        assert "SERVICE_FAILED" in seen
    finally:
        bus.close()


def test_recovered_is_not_published_without_verified_generation():
    """§29/§30. RECOVERED is a claim about generation, not about a port."""
    http = FakeHttp(up=False)
    service = _service(http)
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    published: list[str] = []
    supervisor = RuntimeSupervisor(
        service, events=SimpleNamespace(publish=lambda name, *a, **k: published.append(name)),
    )

    def come_back_but_cannot_generate():
        http.up = True
        http.generates = False
        return True, "started"

    service.start = come_back_but_cannot_generate

    recovered, detail = supervisor.recover()

    assert not recovered
    assert "generation was not verified" in detail
    assert "SERVICE_RECOVERED" not in published
    assert "SERVICE_DEGRADED" in published
    assert service.record.state is ServiceState.DEGRADED


def test_a_verified_restart_publishes_recovered_with_real_timings():
    http = FakeHttp(up=False)
    service = _service(http)
    service.record.ownership = ProcessOwnership.STARTED_BY_AI
    published: list[str] = []
    supervisor = RuntimeSupervisor(
        service, events=SimpleNamespace(publish=lambda name, *a, **k: published.append(name)),
    )

    def come_back():
        http.up = True
        return True, "started"

    service.start = come_back

    recovered, _ = supervisor.recover()

    assert recovered
    assert "SERVICE_RECOVERED" in published
    timings = service.record.recovery_timings
    # Measured, not invented: against an in-process fake these are legitimately
    # sub-millisecond, so the assertion is on presence and coherence rather
    # than on a magnitude the test would have to make up.
    assert set(timings) >= {"failure_detection_ms", "spawn_ms", "http_ready_ms",
                            "generation_verified_ms", "total_recovery_ms"}
    assert all(value >= 0 for value in timings.values())
    assert timings["total_recovery_ms"] >= timings["spawn_ms"]
    assert timings["generation_verified_ms"] >= timings["http_ready_ms"]
    assert service.record.restart_count == 1


# ------------------------------------------------------------------ safe retry


def test_read_only_work_may_be_retried_but_side_effecting_work_may_not():
    """§32. A failed provider call does not prove the side effect did not run."""
    assert retry_is_safe(FailureClass.DEPENDENCY_DOWN, side_effecting=False)
    assert retry_is_safe(FailureClass.TIMEOUT, side_effecting=False)
    assert not retry_is_safe(FailureClass.DEPENDENCY_DOWN, side_effecting=True)
    assert not retry_is_safe(FailureClass.POLICY_DENIED, side_effecting=False)


# --------------------------------------- control plane without the provider


def test_inspect_reports_stopped_rather_than_healthy_when_nothing_answers():
    supervisor = RuntimeSupervisor(_service(FakeHttp(up=False)))
    status = supervisor.inspect()

    assert status["state"] == ServiceState.STOPPED.value
    assert status["http_ready"] is False
    assert status["generation_verified"] is False
    assert status["ownership"] == ProcessOwnership.UNKNOWN.value


def test_inspect_never_claims_ownership_of_a_process_it_found():
    supervisor = RuntimeSupervisor(_service(FakeHttp()))
    assert supervisor.inspect()["ownership"] == ProcessOwnership.EXTERNAL.value


def test_the_control_plane_answers_with_the_provider_unavailable(tmp_path, monkeypatch, capsys):
    """§34. When inference is down is exactly when status has to work.

    Drives the real CLI command handler with a runtime whose provider cannot
    be reached, and requires every listed control command to answer without
    raising and without needing a model.
    """
    import httpx

    from app.cli import handle_extended_command
    from app.runtime import build_runtime

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("[WinError 10061] the target machine actively refused it")

    runtime = build_runtime(use_model=False, interactive_approvals=False)
    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)

    commands = [
        "/provider", "/provider status", "/lastfailure", "/turns", "/doctor",
        "/authority", "/privacy", "/camera status", "/storage", "/cost",
    ]
    for command in commands:
        capsys.readouterr()
        handled = handle_extended_command(runtime, command)
        output = capsys.readouterr().out
        assert handled, f"{command} was not handled"
        assert output.strip(), f"{command} produced no output with the provider down"

    assert runtime.model is None, "this runtime has no model; the answers were model-free"


def test_provider_restart_is_refused_for_an_external_provider_without_a_model():
    """§36. Ownership is explained rather than acted on."""
    service = _service(FakeHttp(up=False))
    service.record.ownership = ProcessOwnership.EXTERNAL
    ok, detail = RuntimeSupervisor(service).recover(reason="restart requested by the user")

    assert not ok
    assert "did not start it" in detail


# -------------------------------------------------------------- real discovery


def test_executable_discovery_reports_absence_rather_than_guessing():
    """Whatever this machine has, the answer is a real path or None."""
    found = OllamaService.locate_executable()
    if found is not None:
        from pathlib import Path

        assert Path(found).is_file(), "discovery must not return a path that does not exist"
