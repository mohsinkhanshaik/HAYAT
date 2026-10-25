"""Manually verify the installed local chat/embedding engines, without paid APIs."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.config import load_settings
    from app.launcher import folder_runtime
    from app.policy.cost import CostPolicy

    CostPolicy.from_settings(load_settings()['cost_policy']).assert_zero_cost()
    output = ROOT / '.test-runs' / 'zero-cost-live' / datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    with folder_runtime(lambda message: print(message, flush=True)) as environment:
        result = subprocess.run([
            sys.executable, '-m', 'pytest', '-v', '-s', 'tests/test_live_zero_cost.py',
            '--tb=short', '--basetemp=' + str(output / 'temp'), '--junitxml=' + str(output / 'result.xml'),
        ], cwd=ROOT, env={**environment, 'HAYAT_LIVE_ZERO_COST': '1', 'HAYAT_DISABLE_BACKGROUND': '1',
                        'PYTHONIOENCODING': 'utf-8'}, check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
