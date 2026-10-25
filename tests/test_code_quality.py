"""Keep undefined names and unused code out of application and test changes."""
import subprocess
import sys
from pathlib import Path


def test_ruff_check():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "app", "scripts", "tests",
         "main.py", "run.py", "bootstrap.py", "launch.pyw", "conftest.py"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
