r"""The held-out set asked through HAYAT's whole assistant, on the real brain. Run on request only.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_holdout_pipeline.py
    $env:HAYAT_HOLDOUT_PER_FAMILY = "5"   # optional: the first N cases of each family (0 = all 220)
    $env:HAYAT_HOLDOUT_FAMILIES = "dates,letters"   # optional: only these families

The conftest sandboxes every data store, so these turns never reach the owner's memory.
The report is written beside the bare-model reports in data/benchmarks/holdout/ with
transport "assistant-pipeline-v1", graded by the same code.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def test_holdout_through_the_assistant_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.config import PROJECT_ROOT
    from app.evaluation.holdout import build_cases, markdown, run
    from app.evaluation.pipeline import TRANSPORT, confine_files, pipeline_generate
    from app.runtime import build_runtime

    per_family = int(os.environ.get("HAYAT_HOLDOUT_PER_FAMILY", "0") or 0)
    families = {name.strip() for name in os.environ.get("HAYAT_HOLDOUT_FAMILIES", "").split(",") if name.strip()}
    cases = [case for case in build_cases() if not families or case["family"] in families]
    if per_family:
        kept = []
        for case in cases:
            if sum(1 for chosen in kept if chosen["family"] == case["family"]) < per_family:
                kept.append(case)
        cases = kept
    # The data sandbox redirects databases, not a file a misrouted turn writes into the project:
    # the first pipeline run wrote sky_colour.txt and five others. Any such side effect fails the run.
    before = {path.name for path in PROJECT_ROOT.iterdir()}
    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    confined = confine_files(runtime, tmp_path / "files")
    try:
        model = getattr(runtime.model, "model", "unknown")
        report = run(pipeline_generate(runtime), cases, model=model,
                     on_progress=lambda done, total: print(f"{done}/{total}", flush=True) if done % 5 == 0 else None)
    finally:
        runtime.close()
    side_effects = sorted({path.name for path in PROJECT_ROOT.iterdir()} - before)
    # Writes that a misrouted turn made into the scratch root still count against the run.
    side_effects += sorted("files/" + str(path.relative_to(confined)) for path in confined.rglob("*") if path.is_file())
    report.update(transport=TRANSPORT, partial=bool(per_family or families), endpoint="http://127.0.0.1:11435",
                  evaluation_complete=report["summary"]["evaluation_complete"])
    out = PROJECT_ROOT / "data" / "benchmarks" / "holdout"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("pipeline-%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / f"{stamp}.md").write_text(markdown(report).replace("# Held-out evaluation", "# Held-out evaluation (assistant pipeline)", 1),
                                     encoding="utf-8")
    print(json.dumps({"report": str(out / f"{stamp}.md"), "cases": report["summary"]["cases"],
                      "accuracy": report["summary"]["accuracy"]}, indent=2))
    assert report["summary"]["cases"] == len(cases)
    assert not side_effects, f"files written into the project during the evaluation: {side_effects}"
