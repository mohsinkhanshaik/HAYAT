r"""End-to-end check of HAYAT's real brain and tools, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_brain.py

It drives the real assistant against HAYAT's own Ollama (port 11435) with the real model and tools. The test
suite's conftest redirects every data store to a temporary folder, so nothing reaches the owner's memory or
knowledge. Without HAYAT_LIVE it is skipped, so the normal suite never loads a model.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")

ROOT = Path(__file__).resolve().parents[1]


def _image(path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    picture = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(picture)
    try:
        font = ImageFont.truetype("arialbd.ttf", 64)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 100), "INVOICE 4721", fill="black", font=font)
    picture.save(path)
    return path


def test_hayats_brain_and_tools_work_end_to_end(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.runtime import build_runtime

    work = ROOT / ".test-runs" / "live-brain"
    work.mkdir(parents=True, exist_ok=True)
    note = work / "note.txt"
    note.write_text("The secret word is lighthouse.\n", encoding="utf-8")
    picture = _image(work / "invoice.png")

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    checks = [
        ("brain: identity", "Who are you? One sentence.", r"\bHAYAT\b"),
        ("brain: arithmetic", "What is 17 multiplied by 23?", r"\b391\b"),
        ("brain: multi-step", "A shop buys a jacket for $80, marks it up 50%, then sells it at 20% off the marked "
                              "price. What is the profit in dollars?", r"\$?\b16\b"),
        ("memory: remember", "Remember this for this chat: my favourite colour is teal.", r"."),
        # 2026-10-10 the brain recalled teal, then said it had no permanent memory across sessions.
        ("memory: recall", "What is my favourite colour?",
         r"(?is)^(?!.*\b(?:no|don't have|do not have|lack)\s+(?:a\s+)?(?:permanent|persistent|long-term)?\s*memory)"
         r".*\bteal\b"),
        ("tool: read a file", f"Read the file {note} and tell me the secret word.", r"(?i)lighthouse"),
        # The real hour, not any time: the brain once answered "10:42 PM" at 3:40 AM.
        ("tool: clock", "What time is it right now?", rf"\b{datetime.now().strftime('%I').lstrip('0')}:\d{{2}}"),
        ("tool: vision", f"Describe the image {picture}. What number is written on it?", r"4721"),
        ("tool: web search", "Search the web: what is the capital city of Australia?", r"(?i)canberra"),
    ]
    results = []
    try:
        for name, prompt, expected in checks:
            started = time.monotonic()
            local_start = datetime.now()
            previous = {job.job_id for job in runtime.research_jobs.recent()}
            error = False
            try:
                reply = runtime.assistant.handle(prompt).response.display_text
                if name == "tool: web search":
                    jobs = [job for job in runtime.research_jobs.recent() if job.job_id not in previous]
                    assert len(jobs) == 1, "The request must start one identifiable research job."
                    job = jobs[0]
                    assert "Australia" in job.query
                    assert job._done.wait(240), "The background search did not finish within four minutes."
                    assert job.status == "done", job.error
                    assert job.sources > 0, "A researched answer must contain source evidence."
                    reply = job.answer
                if name == "brain: identity":
                    assert len((reply or "").split()) <= 60, "The one-sentence request was ignored."
                    assert len(re.findall(r"[.!?](?:\s+|$)", reply or "")) <= 1, "Expected one sentence."
                if name == "tool: clock":
                    moments = (local_start, datetime.now())
                    clocks = [re.escape(moment.strftime('%I').lstrip('0') + moment.strftime(':%M')) for moment in moments]
                    expected = r"\b(?:" + "|".join(clocks) + r")\b"
            except Exception as exc:  # report every check, not just the first failure
                error = True
                reply = f"{type(exc).__name__}: {exc}"
            ok = not error and bool(re.search(expected, reply or ""))
            results.append((name, ok, round(time.monotonic() - started, 1), " ".join(str(reply).split())[:150]))
        for name, ok, seconds, reply in results:
            line = f"{'PASS' if ok else 'FAIL'} {name:20} {seconds:6.1f}s  {reply}"
            # The Windows console cannot print every emoji a reply may contain.
            print(line.encode("ascii", "replace").decode("ascii"))
        assert all(ok for _name, ok, _s, _r in results), [r for r in results if not r[1]]
    finally:
        runtime.close()
