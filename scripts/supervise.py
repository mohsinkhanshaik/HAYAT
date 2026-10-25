"""User-session HAYAT supervisor: bounded crash restarts, never task replay."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "supervision"


def restart_delay(failures):
    return min(300, 5 * 3 ** max(0, failures - 1))


def acquire_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0)
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def existing_runtime(root=ROOT):
    import psutil
    try:
        marker = json.loads((root / "data/runtime-alive.json").read_text(encoding="utf-8"))
        process = psutil.Process(int(marker["pid"]))
        return process.is_running() and Path(process.cwd()).resolve() == root.resolve() and any(
            Path(arg).name.lower() == "main.py" for arg in process.cmdline())
    except (OSError, ValueError, KeyError, psutil.NoSuchProcess):
        return False
    except psutil.AccessDenied:
        return True


def write_status(state, **values):
    STATE.mkdir(parents=True, exist_ok=True)
    target = STATE / "status.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"state": state, "supervisor_pid": os.getpid(),
                                     "updated_at": time.time(), **values}, indent=2), encoding="utf-8")
    temporary.replace(target)


def main():
    lock = acquire_lock(STATE / "supervisor.lock")
    if lock is None:
        return 0
    process = None
    try:
        if existing_runtime():
            write_status("existing_instance", message="HAYAT is already running; no duplicate started")
            return 0
        if (STATE / "disabled").exists():
            write_status("disabled")
            return 0
        failures = 0
        while failures < 5:
            if (STATE / "disabled").exists():
                write_status("disabled")
                return 0
            log = STATE / "application.log"
            if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
                log.replace(STATE / "application.previous.log")
            started = time.monotonic()
            with log.open("ab") as output:
                process = subprocess.Popen([sys.executable, str(ROOT / "main.py"), "--gui"], cwd=ROOT,
                    stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                while process.poll() is None:
                    write_status("running", application_pid=process.pid, restarts=failures)
                    time.sleep(5)
                code = process.returncode
            process = None
            if code in (0, 130):
                write_status("closed", exit_code=code, restarts=failures)
                return 0
            # A healthy hour breaks a crash streak; short crash loops stop.
            failures = 1 if time.monotonic() - started > 3600 else failures + 1
            if failures >= 5:
                break
            delay = restart_delay(failures)
            write_status("restart_wait", exit_code=code, restarts=failures, delay_seconds=delay)
            for _ in range(delay):
                if (STATE / "disabled").exists():
                    write_status("disabled")
                    return 0
                time.sleep(1)
        write_status("failed", exit_code=code, restarts=failures,
                     message="Repeated startup failures; launch with --safe-mode to investigate")
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
