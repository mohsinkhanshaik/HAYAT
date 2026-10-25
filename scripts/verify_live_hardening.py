"""Run bounded model regressions on HAYAT's installed engine with isolated test data."""
from datetime import UTC, datetime
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.launcher import folder_runtime
    output = ROOT / '.test-runs' / 'hardening-live' / datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    output.mkdir(parents=True)
    with folder_runtime(lambda message: print(message, flush=True)) as environment:
        result = subprocess.run([sys.executable, '-m', 'pytest', '-v', '-s', 'tests/test_live_hardening.py',
                                 '--tb=short', '--basetemp=' + str(output / 'temp'),
                                 '--junitxml=' + str(output / 'result.xml')], cwd=ROOT,
                                env={**os.environ, **environment, 'HAYAT_LIVE_HARDENING': '1',
                                     'HAYAT_DISABLE_BACKGROUND': '1', 'PYTHONIOENCODING': 'utf-8'}, check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
