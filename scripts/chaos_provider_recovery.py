"""Chaos test: kill an Ollama D:\\AI started, and only that one.

Run manually. This is not part of `pytest -q` because it spawns a real model
server and loads a real model.

The safety design of this script matters as much as what it proves. The user's
own Ollama is normally serving on 11434, and it must be untouched -- so this
starts a *separate* instance on a free port, bound via OLLAMA_HOST, and kills
it by the exact PID that this runtime recorded when it spawned it. There is no
`taskkill /IM ollama.exe`. The user's instance is probed before and after and
the run fails if it was disturbed.

    .venv\\Scripts\\python.exe scripts\\chaos_provider_recovery.py
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.events import EventBus  # noqa: E402
from app.failures import FailureLog  # noqa: E402
from app.supervisor import (  # noqa: E402
    HealthLevel,
    OllamaService,
    ProcessOwnership,
    RuntimeSupervisor,
)

#: Smallest installed chat model, to keep VRAM pressure off the user's machine.
CHAOS_MODEL = "qwen3.5:latest"
USER_ENDPOINT = "http://localhost:11434"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def user_provider_alive() -> bool:
    import httpx

    try:
        return httpx.get(f"{USER_ENDPOINT}/api/tags", timeout=4).status_code == 200
    except Exception:
        return False


def kill_exact_pid(pid: int) -> bool:
    """Terminate one process by PID. Never by image name."""
    result = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True,
    )
    return result.returncode == 0


def main() -> int:
    report: dict[str, object] = {"model": CHAOS_MODEL}
    external_before = user_provider_alive()
    report["user_provider_before"] = "UP" if external_before else "DOWN"

    port = free_port()
    endpoint = f"http://127.0.0.1:{port}"
    report["owned_endpoint"] = endpoint

    events = EventBus(PROJECT_ROOT / "data/memory/events.db")
    service_events: list[str] = []
    for name in ("SERVICE_STARTING", "SERVICE_HEALTHY", "SERVICE_FAILED",
                 "SERVICE_RESTARTING", "SERVICE_RECOVERED", "SERVICE_DEGRADED"):
        events.subscribe(name, lambda event: service_events.append(event["event_type"]))

    failures = FailureLog(events)
    service = OllamaService(
        endpoint, CHAOS_MODEL, start_timeout=120.0,
        extra_env={"OLLAMA_HOST": f"127.0.0.1:{port}"},
    )
    supervisor = RuntimeSupervisor(service, events=events, failures=failures)

    try:
        # 1. Start a provider this runtime owns.
        started_at = time.perf_counter()
        started, detail = supervisor.ensure_started()
        report["start"] = {"ok": started, "detail": detail,
                           "ms": round((time.perf_counter() - started_at) * 1000, 1)}
        if not started:
            report["result"] = "FAILED: could not start an owned provider"
            print(json.dumps(report, indent=2)); return 1

        if service.record.ownership is not ProcessOwnership.STARTED_BY_AI:
            report["result"] = f"ABORTED: provider is {service.record.ownership.value}, not owned"
            print(json.dumps(report, indent=2)); return 1

        owned_pid = service.record.pid
        report["owned_pid"] = owned_pid

        # 2. Prove it really generates before breaking it.
        verified = service.probe(verify_generation=True)
        report["generation_before_kill"] = {
            "level": verified.level.value, "detail": verified.detail,
            "latency_ms": verified.latency_ms,
        }
        if verified.level is not HealthLevel.GENERATION:
            report["result"] = "ABORTED: owned provider never generated; nothing to test"
            print(json.dumps(report, indent=2)); return 1

        # 3. Kill exactly the PID we spawned.
        killed_at = time.perf_counter()
        report["kill"] = {"pid": owned_pid, "ok": kill_exact_pid(owned_pid), "by": "exact PID"}
        time.sleep(1.0)
        report["detected_down"] = not service.probe().http_ready
        report["detection_ms"] = round((time.perf_counter() - killed_at) * 1000, 1)

        # 4. Recover, and require verified generation before believing it.
        recovered, recovery_detail = supervisor.recover(reason="chaos test killed the owned provider")
        report["recovery"] = {
            "ok": recovered, "detail": recovery_detail,
            "timings": dict(service.record.recovery_timings),
            "restart_count": service.record.restart_count,
            "new_pid": service.record.pid,
        }

        after = service.probe(verify_generation=True)
        report["generation_after_recovery"] = {"level": after.level.value, "detail": after.detail}

        report["service_events"] = service_events
        external_after = user_provider_alive()
        report["user_provider_after"] = "UP" if external_after else "DOWN"
        report["user_provider_undisturbed"] = external_before == external_after

        passed = (
            recovered
            and after.level is HealthLevel.GENERATION
            and service.record.pid != owned_pid
            and report["user_provider_undisturbed"]
            and "SERVICE_RECOVERED" in service_events
        )
        report["result"] = "PASSED" if passed else "FAILED"
        print(json.dumps(report, indent=2, default=str))
        return 0 if passed else 1
    finally:
        # Always release what we own; never touch what we do not.
        try:
            print(f"cleanup: {supervisor.shutdown()}", file=sys.stderr)
        finally:
            events.close()


if __name__ == "__main__":
    raise SystemExit(main())
