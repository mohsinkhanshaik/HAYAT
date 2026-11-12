"""Stopping a process HAYAT started stops what it spawned too (app/process.py).

2026-11-07, live: closing HAYAT stopped its ollama.exe but each time left the llama-server runner it had spawned
holding 7 GB of the 8 GB GPU, because the tree was swept only after the parent had exited.
"""
import subprocess
import sys
import time

import psutil

from app.process import stop_owned_process

# Detached from any console, as Ollama starts its runners: a console-attached child would die with the parent's
# console anyway and hide the bug.
PARENT = ("import subprocess, sys, time\n"
          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],"
          " creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))\n"
          "time.sleep(120)\n")


def test_the_child_a_stopped_parent_spawned_is_stopped_too():
    parent = subprocess.Popen([sys.executable, "-c", PARENT])
    try:
        deadline = time.monotonic() + 15
        children = []
        while not children and time.monotonic() < deadline:
            # The sleeping worker itself, not the console host Windows attaches (that one dies with its parent).
            children = [process for process in psutil.Process(parent.pid).children(recursive=True)
                        if "sleep(120)" in " ".join(process.cmdline())]
            time.sleep(.1)
        assert children, "the parent never started its child"
        child = children[0]
        stopped, detail = stop_owned_process(parent, parent.pid, graceful_timeout=5)
        assert stopped
        child.wait(timeout=10)  # raises if the child is still running
        assert not child.is_running()
    finally:
        for process in psutil.Process().children(recursive=True):
            if process.pid != parent.pid and "sleep(120)" in " ".join(process.cmdline()):
                process.kill()
        if parent.poll() is None:
            parent.kill()


def test_nothing_is_recorded_for_a_process_that_is_already_gone():
    from app.process import _descendants, _stop_recorded
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=30)
    assert _descendants(finished.pid) == [] and _stop_recorded([]) == 0
