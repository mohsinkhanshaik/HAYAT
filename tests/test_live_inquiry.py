r"""Live check of self-inquiry on HAYAT's real brain, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_inquiry.py

A hard question goes through the real runtime with self-inquiry and review on;
a greeting must still take the reflex path. The suite's conftest redirects all
data stores, so nothing reaches the owner's memory. Times are printed, not
asserted: they depend on what else the machine is doing.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")

HARD = ("Explain why two threads that always lock A before B can still deadlock, and what I should check "
        "in my own code.")


def test_real_brain_questions_itself_on_hard_questions_only(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.runtime import build_runtime

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    try:
        chat = runtime.conversation
        assert runtime.inquiry is not None and runtime.inquiry.scope == "deep_turns"

        started = time.monotonic()
        greeting = chat.reply("hey hayat how are you doing today my friend")
        greeting_seconds = time.monotonic() - started
        assert greeting.strip() and chat.last_inquiry is None

        started = time.monotonic()
        answer = chat.reply(HARD)
        hard_seconds = time.monotonic() - started
        report = chat.last_inquiry
        print(f"\ngreeting {greeting_seconds:.1f}s; hard question {hard_seconds:.1f}s")
        print("self-inquiry:", report)
        print("review:", chat.last_dual_review)
        print("answer:", answer[:1200])

        assert report is not None and report["state"] == "examined" and report["questions"] >= 1
        assert len(answer.split()) >= 60
        assert "deadlock" in answer.lower()
    finally:
        runtime.close()
