r"""The situation summary rides on every reply and plan, so it must stay cheap. Live, on request only.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_situation_speed.py
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def test_situation_brief_is_cheap_on_the_real_runtime(monkeypatch):
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.runtime import build_runtime

    runtime = build_runtime(interactive_approvals=False, use_model=False, start_background_services=False)
    runtime.situation.observe_turn("search the web for the latest AI news")
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        brief = runtime.situation.brief()
        timings.append((time.perf_counter() - started) * 1000)
    print(f"situation brief: {[round(value) for value in timings]} ms, {len(str(brief))} chars")
    assert sorted(timings)[2] < 300, timings
