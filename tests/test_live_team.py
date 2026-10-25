r"""End-to-end run of a temporary worker team with HAYAT's real brain and tools, run only on request.

    $env:HAYAT_LIVE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_live_team.py

The test suite's conftest redirects every data store, so the run never touches HAYAT's real memory.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def test_a_real_team_designs_runs_verifies_and_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("HAYAT_DISABLE_BACKGROUND", "1")
    from app.agents.team import VERIFIER, WorkerTeam
    from app.runtime import build_runtime

    runtime = build_runtime(interactive_approvals=False, use_model=True, start_background_services=False)
    team = WorkerTeam(runtime.model, runtime.registry, root=tmp_path)
    report = team.run("Research 5 AI infrastructure companies and find jobs suitable for me.")
    print(f"\nplanned by {report.planned_by} · {report.seconds:.0f}s · {report.tool_calls} tool calls")
    for result in report.results:
        print(f"- {result.role} ok={result.ok} {result.seconds:.0f}s used={result.tools_used} "
              f"sources={len(result.sources)} {result.note}")
    print("\nANSWER:\n" + report.answer[:3000])
    assert report.finished and report.results[-1].role == VERIFIER
    assert sum(result.ok for result in report.results) >= len(report.results) - 1
    assert report.answer and os.path.isfile(report.report_path)
