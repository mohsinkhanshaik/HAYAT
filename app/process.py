"""Stopping a process this runtime started -- including everything it spawned.

`Popen.terminate()` signals one process. An inference engine is not one
process: llama-server spawns workers, and terminating the parent can leave
them holding memory and a port after the runtime believes it has cleaned up.
That is not theoretical -- two orphaned engines were found running on this
machine after a test run that reported a clean shutdown, and had to be killed
by hand.

The rule that makes this safe is unchanged: a tree is only ever rooted at a
PID this runtime recorded when it did the spawning. There is no kill by image
name anywhere here, so the worst case is failing to stop our own process,
never stopping somebody else's.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def child_pids(pid: int) -> list[int]:
    """Direct children of a process, for verifying a tree is really gone.

    Uses CIM rather than a third-party library so this adds no dependency to
    answer a question Windows already answers. Returns an empty list when the
    answer cannot be obtained -- unknown is not the same as none, and callers
    treat it only as evidence, never as proof of cleanliness.
    """
    if os.name != "nt":
        try:
            import psutil

            return [int(child.pid) for child in psutil.Process(pid).children(recursive=True)]
        except Exception:
            return []
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={int(pid)}')"
             ".ProcessId -join ','"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    return [int(part) for part in completed.stdout.strip().split(",") if part.strip().isdigit()]


def _tree_kill(pid: int, timeout: float = 15.0) -> bool:
    """Force-terminate a PID and its descendants. Windows-specific."""
    if os.name != "nt":
        try:
            import psutil

            root = psutil.Process(pid)
            processes = [*root.children(recursive=True), root]
            for process in reversed(processes):
                process.terminate()
            _, alive = psutil.wait_procs(processes, timeout=min(timeout, 5.0))
            for process in alive:
                process.kill()
            return True
        except Exception as exc:
            return type(exc).__name__ == "NoSuchProcess"
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True, text=True, timeout=timeout,
        )
        # 128 means the process was already gone, which is the outcome we want.
        return completed.returncode in (0, 128)
    except Exception:
        return False


def stop_owned_process(
    process: subprocess.Popen | None,
    pid: int | None = None,
    *,
    graceful_timeout: float = 10.0,
    kill_timeout: float = 5.0,
) -> tuple[bool, str]:
    """Stop a process this runtime started, and everything it spawned.

    Escalates rather than reaching straight for force: a graceful terminate
    lets an engine flush and close its port, and only a process that ignores
    it gets killed. The tree kill runs last and unconditionally, because the
    parent exiting cleanly is not evidence that its workers did.
    """
    if process is None and pid is None:
        return False, "no owned process is recorded"
    target = pid if pid is not None else process.pid  # type: ignore[union-attr]

    detail = "stopped"
    if process is not None:
        try:
            process.terminate()
            try:
                process.wait(timeout=graceful_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=kill_timeout)
                except subprocess.TimeoutExpired:
                    detail = "the parent ignored terminate and kill"
        except Exception as exc:
            detail = f"terminate raised: {type(exc).__name__}"

    # Always sweep the tree. A parent that exited cleanly says nothing about
    # the workers it left behind, which is exactly the bug this exists for.
    survivors = child_pids(target)
    if survivors:
        _tree_kill(target)
        detail = f"{detail}; also stopped {len(survivors)} child process(es)"
    else:
        _tree_kill(target)
    return True, detail


def is_running(pid: int | None) -> bool:
    """Whether a PID is currently alive."""
    if pid is None:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
            capture_output=True, text=True, timeout=15,
        )
        return str(pid) in completed.stdout
    except Exception:
        return False


def describe(pid: int | None) -> dict[str, Any]:
    """A small factual snapshot, for status output and tests."""
    return {"pid": pid, "running": is_running(pid), "children": child_pids(pid) if pid else []}
