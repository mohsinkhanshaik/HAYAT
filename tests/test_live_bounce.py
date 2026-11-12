r"""Twisted-instruction and metamorphic tests through HAYAT's whole assistant, on the real brain. On request only.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_bounce.py

Sandboxed by the conftest. Writes data/benchmarks/bounce/bounce-<time>.json and .md.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def test_bounce_through_the_assistant_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.config import PROJECT_ROOT
    from app.evaluation.bounce import markdown, run
    from app.evaluation.pipeline import confine_files, pipeline_converse
    from app.runtime import build_runtime

    # The data sandbox redirects databases, not a file a misrouted turn writes into the project:
    # the first pipeline run wrote sky_colour.txt and five others. Any such side effect fails the run.
    before = {path.name for path in PROJECT_ROOT.iterdir()}
    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    confined = confine_files(runtime, tmp_path / "files")
    try:
        report = run(pipeline_converse(runtime), model=getattr(runtime.model, "model", "unknown"))
    finally:
        runtime.close()
    side_effects = sorted({path.name for path in PROJECT_ROOT.iterdir()} - before)
    # Writes that a misrouted turn made into the scratch root still count against the run.
    side_effects += sorted("files/" + str(path.relative_to(confined)) for path in confined.rglob("*") if path.is_file())
    out = PROJECT_ROOT / "data" / "benchmarks" / "bounce"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("bounce-%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / f"{stamp}.md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"report": str(out / f"{stamp}.md"), **report["summary"]}, indent=2))
    assert report["summary"]["conversations"] == len(report["cases"])
    assert not side_effects, f"files written into the project during the evaluation: {side_effects}"
