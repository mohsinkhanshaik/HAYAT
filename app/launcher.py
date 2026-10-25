"""Start HAYAT from its own folder: its Python, its Ollama, its window.

What ``HAYAT.lnk`` runs (through ``launch.pyw``). In order:

1. point ``.venv`` at the Python in ``runtime/`` (the folder may have moved);
2. start HAYAT's own Ollama on its own port, unless one already answers there;
3. open the HAYAT window with no console;
4. when the window closes, stop the Ollama this launch started, and only that.

The Ollama is put in a Windows job that ends with this launcher, so it and
the model runners it spawns cannot be left behind even if the launcher is
killed. An Ollama that was already serving on HAYAT's port belongs to
someone else's launch and is left alone.

Standard library only: this runs before the virtual environment is checked.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from app import portable

ROOT = portable.ROOT
LOGS = ROOT / "data" / "logs"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def venv_python(root: Path = ROOT) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ollama_answers(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for(check: Callable[[], bool], timeout: float, alive: Callable[[], bool] = lambda: True,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + timeout
    while clock() < deadline:
        if check():
            return True
        if not alive():
            return False
        sleep(.25)
    return check()


class Job:
    """A Windows job object whose processes end when this launcher does."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(handle)
            return
        self._kernel32, self.handle = kernel32, handle

    def add(self, process: subprocess.Popen) -> bool:
        if self.handle is None:
            return False
        return bool(self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)))

    def close(self) -> None:
        """Ends every process in the job."""
        if self.handle is not None:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


class OwnOllama:
    """HAYAT's Ollama for one launch: started here, or found already serving."""

    def __init__(self, root: Path = ROOT, *, popen: Callable[..., Any] = subprocess.Popen,
                 answers: Callable[[str], bool] = ollama_answers, job: Job | None = None,
                 log: Any = None, timeout: float = 90.0) -> None:
        self.root, self.popen, self.answers = root, popen, answers
        self.job, self.log, self.timeout = job, log, timeout
        self.process: Any = None
        self.detail = ""

    @property
    def owned(self) -> bool:
        return self.process is not None

    def start(self) -> bool:
        url = portable.base_url()
        if self.answers(url):
            self.detail = f"HAYAT's Ollama was already running on port {portable.PORT}"
            return True
        env = {**os.environ, **portable.environment(self.root)}
        # Applies to the server we own; a pre-existing server is never restarted.
        env["OLLAMA_NO_CLOUD"] = "1"
        self.process = self.popen(
            [str(portable.ollama_executable(self.root)), "serve"], cwd=str(self.root), env=env,
            stdin=subprocess.DEVNULL, stdout=self.log or subprocess.DEVNULL,
            stderr=self.log or subprocess.DEVNULL, creationflags=NO_WINDOW)
        if self.job is not None:
            self.job.add(self.process)
        ready = wait_for(lambda: self.answers(url), self.timeout, alive=lambda: self.process.poll() is None)
        self.detail = (f"started HAYAT's Ollama on port {portable.PORT}" if ready
                       else f"HAYAT's Ollama did not answer on port {portable.PORT}")
        return ready

    def stop(self) -> None:
        """Stop the Ollama this launch started, with the model runners it spawned."""
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, creationflags=NO_WINDOW)
        else:
            process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:
            process.kill()


def note(log: Any, message: str) -> None:
    if log is not None:
        log.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] launcher: {message}\n".encode("utf-8"))
        log.flush()


def tell(message: str) -> None:
    """A message box: the launcher has no console to print to."""
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("HAYAT", message, parent=root)
        root.destroy()
    except Exception:  # noqa: S110 -- runs before .venv exists; no app logging is importable here
        pass


def ensure_venv(log: Any) -> Path | None:
    python = venv_python()
    if python.is_file():
        probe = subprocess.run([str(python), "-c", "import tkinter, httpx"], capture_output=True,
                               creationflags=NO_WINDOW, cwd=str(ROOT))
        if probe.returncode == 0:
            return python
    # Building the environment takes minutes and downloads packages: done
    # in a visible window, so it is never a silent wait.
    note(log, "the environment is missing or broken; running bootstrap.py")
    runner = portable.python_home() / "python.exe"
    built = subprocess.run([str(runner if runner.is_file() else sys.executable), str(ROOT / "bootstrap.py")],
                           cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    portable.repair_venv()
    return python if built.returncode == 0 and python.is_file() else None


@contextmanager
def folder_runtime(say: Callable[[str], None] = lambda message: None, root: Path = ROOT) -> Iterator[dict[str, str]]:
    """The environment HAYAT's processes run in, with HAYAT's Ollama up throughout.

    With ``runtime/`` complete: HAYAT's own Ollama on HAYAT's port, stopped
    on the way out if this started it. Incomplete, or switched off with
    ``HAYAT_PORTABLE=0``: the machine's Ollama, as before, with what is
    missing said.
    """
    env = dict(os.environ)
    if env.get(portable.SWITCH, "").strip() == "0":
        yield env
        return
    gaps = portable.missing(root)
    if gaps:
        say("HAYAT's folder runtime is incomplete, so the machine's Ollama is used. Missing: " + "; ".join(gaps))
        env[portable.SWITCH] = "0"
        yield env
        return
    env.update(portable.environment(root))
    (root / "data" / "logs").mkdir(parents=True, exist_ok=True)
    job = Job()
    with open(root / "data" / "logs" / "ollama-hayat.log", "ab") as server_log:
        ollama = OwnOllama(root, job=job, log=server_log)
        ollama.start()
        say(ollama.detail)
        try:
            yield env
        finally:
            if ollama.owned:
                ollama.stop()
                say("stopped HAYAT's Ollama")
            job.close()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv) or ["--gui"]
    LOGS.mkdir(parents=True, exist_ok=True)
    with open(LOGS / "launcher.log", "ab") as log:
        note(log, f"starting from {ROOT}")
        if portable.repair_venv():
            note(log, f".venv re-pointed at {portable.python_home()}")
        python = ensure_venv(log)
        if python is None:
            tell(r"HAYAT's Python environment could not be prepared. See data\logs\launcher.log.")
            return 1
        with folder_runtime(lambda message: note(log, message)) as env:
            subprocess.run([str(python), "-m", "scripts.init_db"], cwd=str(ROOT), env=env,
                           stdout=log, stderr=log, creationflags=NO_WINDOW)
            note(log, "opening HAYAT " + " ".join(arguments))
            # python.exe with a hidden console rather than pythonw: the
            # console tools HAYAT runs then share it instead of each
            # flashing a window of its own.
            code = subprocess.run([str(python), str(ROOT / "main.py"), *arguments], cwd=str(ROOT), env=env,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                  creationflags=NO_WINDOW).returncode
            note(log, f"HAYAT exited with code {code}")
            return code
