from __future__ import annotations

from pathlib import Path
import os
import re
import signal
import subprocess
from typing import Any, Sequence

from app.config import PROJECT_ROOT


class ProjectSandbox:
    def __init__(self, root: Path = PROJECT_ROOT) -> None:
        self.root = root.resolve()

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        resolved = (self.root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(f"Access outside project sandbox denied: {resolved}")
        return resolved


def shell_argv(command: str) -> list[str]:
    """Explicit interpreter argument vector for text that needs shell builtins.

    Using an explicit argv instead of ``shell=True`` keeps the invocation
    auditable and removes Python's implicit shell-quoting layer. It is not a
    security boundary on its own; ``CommandPolicy`` is the boundary.
    """
    if os.name == "nt":
        return ["cmd.exe", "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


class ProcessSandbox:
    """Bounded non-elevated child execution with secret filtering and process-tree cleanup."""

    SECRET_NAME = re.compile(r"(?i)(api[_-]?key|token|password|secret|credential|authorization)")

    def __init__(self, output_limit: int = 1_000_000) -> None:
        self.output_limit = output_limit

    def _terminate_tree(self, process: subprocess.Popen) -> None:
        """Kill the child and its descendants on either platform."""
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=10, check=False,
            )
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()

    def environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        safe = {key: value for key, value in os.environ.items() if not self.SECRET_NAME.search(key)}
        safe.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        safe.update(extra or {})
        return safe

    def run(
        self,
        command: str | Sequence[str],
        cwd: str | Path,
        timeout: int,
        shell: bool = False,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # `shell` is accepted for backwards compatibility but never forwarded to
        # subprocess. Text needing builtins is wrapped in an explicit argv so the
        # exact interpreter invocation is visible in logs and audits.
        argv = shell_argv(command) if shell and isinstance(command, str) else command
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
            encoding="utf-8",
            errors="replace",
            env=env or self.environment(),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_tree(process)
            stdout, stderr = process.communicate(timeout=5)
        stdout, stdout_truncated = truncate(stdout, self.output_limit)
        stderr, stderr_truncated = truncate(stderr, self.output_limit)
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": process.returncode if not timed_out else -1,
            "timed_out": timed_out,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }


def truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
