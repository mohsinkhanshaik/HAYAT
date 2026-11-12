"""Opt-in profile of what blocks the interface thread (HAYAT_UI_PROFILE=1). Prints; asserts nothing about speed.

Every callback scheduled with ``after`` is timed, and the gaps between turns of the event loop are recorded: a gap
is time during which the window could not repaint or answer the mouse. Run:
    $env:HAYAT_UI_PROFILE='1'; .venv/Scripts/python.exe -m pytest -q -s tests/test_ui_latency_profile.py
"""
from __future__ import annotations

import collections
import os
import time
import tkinter

import pytest

from tests.test_gui import _window, gui  # noqa: F401 -- the sandboxed main window

SECONDS = float(os.environ.get("HAYAT_UI_PROFILE_SECONDS", "6"))


@pytest.mark.skipif(os.environ.get("HAYAT_UI_PROFILE") != "1", reason="a diagnostic, run on request")
@pytest.mark.parametrize("view", ["chat", "operator"])
def test_profile_the_interface_thread(gui, monkeypatch, view):  # noqa: F811
    spent: dict[str, list[float]] = collections.defaultdict(lambda: [0, 0.0, 0.0])
    real_after = tkinter.Misc.after

    def after(self, ms, func=None, *args):
        if func is None:
            return real_after(self, ms)
        inner = getattr(func, "func", func)
        name = getattr(inner, "__qualname__", repr(inner))
        code = getattr(inner, "__code__", None)
        if "<lambda>" in name and code is not None:
            name += f" {os.path.basename(code.co_filename)}:{code.co_firstlineno}"

        def timed(*values):
            started = time.perf_counter()
            try:
                return func(*values)
            finally:
                taken = time.perf_counter() - started
                row = spent[name]
                row[0] += 1
                row[1] += taken
                row[2] = max(row[2], taken)
        return real_after(self, ms, timed, *args)

    monkeypatch.setattr(tkinter.Misc, "after", after)
    # The main window schedules through one wrapper; name what it runs, not the wrapper.
    real_run = type(gui)._run_scheduled

    def run_scheduled(self, callback, *values):
        inner = getattr(callback, "func", callback)
        name = "  via _schedule: " + getattr(inner, "__qualname__", repr(inner))
        code = getattr(inner, "__code__", None)
        if "<lambda>" in name and code is not None:
            name += f" {os.path.basename(code.co_filename)}:{code.co_firstlineno}"
        started = time.perf_counter()
        try:
            return real_run(self, callback, *values)
        finally:
            taken = time.perf_counter() - started
            row = spent[name]
            row[0] += 1
            row[1] += taken
            row[2] = max(row[2], taken)

    monkeypatch.setattr(type(gui), "_run_scheduled", run_scheduled)
    gui.show_view(view)
    gui.root.update()
    gaps, started, last = [], time.perf_counter(), time.perf_counter()
    while time.perf_counter() - started < SECONDS:
        gui.root.update()
        now = time.perf_counter()
        gaps.append(now - last)
        last = now
        time.sleep(0.001)
    gaps.sort()
    busy = sum(row[1] for row in spent.values())
    print(f"\n=== {view}: {len(gaps)} loop turns in {SECONDS:.0f} s; callbacks used {busy:.2f} s ({busy / SECONDS:.0%})")
    print(f"    longest freeze {gaps[-1] * 1000:.0f} ms; freezes over 50 ms: {sum(g > 0.05 for g in gaps)}; "
          f"over 16 ms: {sum(g > 0.016 for g in gaps)}; median turn {gaps[len(gaps) // 2] * 1000:.1f} ms")
    for name, (calls, total, worst) in sorted(spent.items(), key=lambda item: -item[1][1])[:14]:
        print(f"    {total * 1000:8.0f} ms total  {worst * 1000:7.1f} ms worst  {int(calls):5d} calls  {name[:110]}")
