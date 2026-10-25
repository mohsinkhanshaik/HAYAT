"""Run code HAYAT did not write inside a disposable Docker container.

The project's own Python runner is bounded and secret-filtered, but it runs
on this machine as this user -- fine for HAYAT's code, not for a script from
the internet. Windows Sandbox would be the native answer and is not part of
Windows 11 Home; Docker Desktop is, and a container gives the same shape:

- no network (``--network none``), so nothing can phone home or download more
- a read-only root file system, with only a small scratch ``/tmp``
- the code mounted read-only; no other host path is visible
- every Linux capability dropped, no privilege escalation, an unprivileged user
- memory, CPU and process-count ceilings, and a hard time limit
- the container removed afterwards

If Docker is not running, the answer is that it is not running. There is no
fallback to executing the code outside the container: that would be the
exact risk this exists to avoid.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable

IMAGE = "python:3.13-slim"
OUTPUT_LIMIT = 200_000


class SandboxUnavailable(RuntimeError):
    """Docker cannot run the container; nothing was executed."""


class ContainerSandbox:
    def __init__(self, image: str = IMAGE, *, memory: str = "512m", cpus: str = "1", pids: int = 128,
                 run: Callable[..., Any] = subprocess.run) -> None:
        self.image, self.memory, self.cpus, self.pids = image, memory, cpus, pids
        self._run = run

    def available(self) -> tuple[bool, str]:
        if not shutil.which("docker"):
            return False, "Docker is not installed, so there is no isolated place to run untrusted code."
        try:
            probe = self._run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            return False, "Docker did not answer. Start Docker Desktop and try again."
        if probe.returncode != 0:
            return False, "Docker Desktop is not running. Start it and try again; nothing was run."
        if probe.stdout.strip() != "linux":
            return False, "Docker is in Windows-container mode; switch Docker Desktop to Linux containers."
        return True, "ready"

    def command(self, work: Path, name: str, timeout: int = 60) -> list[str]:
        return [
            "docker", "run", "--rm", "--name", name, "--pull", "missing",
            "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,size=64m",
            "--memory", self.memory, "--memory-swap", self.memory, "--cpus", self.cpus,
            "--pids-limit", str(self.pids), "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", "65534:65534", "--workdir", "/work",
            "-v", f"{work}:/work:ro",
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "HOME=/tmp",
            # The limit is enforced inside the container: the outer timeout
            # has headroom for a first image pull, so it cannot be the limit.
            # coreutils timeout sends TERM, then KILL 2 s later, and exits 124.
            self.image, "timeout", "-k", "2", str(timeout), "python", "-I", "/work/main.py",
        ]

    def run_python(self, code: str, timeout_seconds: int = 60) -> dict[str, Any]:
        ready, reason = self.available()
        if not ready:
            raise SandboxUnavailable(reason)
        timeout = max(1, min(int(timeout_seconds), 600))
        name = f"hayat-sandbox-{uuid.uuid4().hex[:12]}"
        with tempfile.TemporaryDirectory(prefix="hayat-sandbox-") as folder:
            work = Path(folder)
            (work / "main.py").write_text(str(code), encoding="utf-8")
            try:
                completed = self._run(self.command(work, name, timeout), capture_output=True, text=True,
                                      timeout=timeout + 120)  # headroom for a first image pull
                timed_out = completed.returncode == 124
            except subprocess.TimeoutExpired as exc:
                self._run(["docker", "rm", "-f", name], capture_output=True, text=True, timeout=30)
                completed, timed_out = exc, True
        stdout = (completed.stdout or "") if not isinstance(completed.stdout, bytes) else completed.stdout.decode("utf-8", "replace")
        stderr = (completed.stderr or "") if not isinstance(completed.stderr, bytes) else completed.stderr.decode("utf-8", "replace")
        return {
            "exit_code": None if timed_out else completed.returncode,
            "killed_for_memory": (not timed_out) and completed.returncode == 137,
            "timed_out": timed_out,
            "stdout": stdout[:OUTPUT_LIMIT], "stderr": stderr[:OUTPUT_LIMIT],
            "truncated": len(stdout) > OUTPUT_LIMIT or len(stderr) > OUTPUT_LIMIT,
            "isolation": {"network": "none", "filesystem": "read-only", "memory": self.memory,
                          "cpus": self.cpus, "image": self.image},
        }


def register_container_sandbox_tools(registry: Any, sandbox: ContainerSandbox | None = None) -> ContainerSandbox:
    sandbox = sandbox or ContainerSandbox()
    registry.register(
        "sandbox_run_python",
        "Run Python code that HAYAT did not write (from the internet, a file, another person) inside a disposable "
        "Docker container with no network, a read-only file system, no host files and memory/CPU/time limits. "
        "Refuses, rather than running unprotected, when Docker Desktop is not running.",
        {"type": "object", "properties": {
            "code": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600}},
         "required": ["code"], "additionalProperties": False},
        "medium", "python_project", sandbox.run_python,
    )
    return sandbox
