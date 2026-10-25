"""Start HAYAT's folder model if needed; generate and browser-test a local app."""
from __future__ import annotations

import os
from datetime import UTC, datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.launcher import folder_runtime
    output = ROOT / ".test-runs/live-replica" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=True)
    with folder_runtime(lambda message: print(message, flush=True)) as environment:
        result = subprocess.run([sys.executable, "-m", "pytest", "tests/test_live_workspace.py", "-v", "-s", "--tb=short",
                                 "--basetemp=" + str(output / "temp"), "--junitxml=" + str(output / "result.xml")],
                                cwd=ROOT, env={**os.environ, **environment, "HAYAT_LIVE": "1", "HAYAT_DISABLE_BACKGROUND": "1",
                                               "PYTHONIOENCODING": "utf-8"}, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
