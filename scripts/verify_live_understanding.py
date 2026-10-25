"""Run contextual-choice checks on HAYAT's actual local model, with isolated test data."""
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.launcher import folder_runtime
    output = ROOT / '.test-runs/live-understanding' / datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    with folder_runtime(lambda message: print(message, flush=True)) as environment:
        run = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_live_understanding.py', '-v', '-s',
                              '--tb=short', '--basetemp=' + str(output / 'temp'),
                              '--junitxml=' + str(output / 'result.xml')], cwd=ROOT,
                             env={**os.environ, **environment, 'HAYAT_LIVE': '1',
                                  'HAYAT_DISABLE_BACKGROUND': '1', 'PYTHONIOENCODING': 'utf-8'}, check=False)
    return run.returncode


if __name__ == '__main__':
    raise SystemExit(main())
