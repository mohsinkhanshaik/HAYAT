"""Opt-in actual assistant probes; all stores are redirected by conftest."""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='opt-in actual local inference')


def test_actual_hayat_hardball_understanding():
    from app.runtime import build_runtime
    from app.understanding_benchmark import run_benchmark

    runtime = build_runtime(use_model=True, start_background_services=False, interactive_approvals=False)
    try:
        report = run_benchmark(runtime, live=True)
        path = Path(__file__).resolve().parents[1] / '.test-runs/hardball-live.json'
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        assert report['passed'], [row for row in report['cases'] if not row['passed']]
    finally:
        runtime.close()
