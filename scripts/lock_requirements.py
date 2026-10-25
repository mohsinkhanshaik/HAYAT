"""Record the exact package versions the test suite last passed with.

`requirements.txt` states ranges; this file pins the versions inside those
ranges that were actually tested, so a fresh install reproduces a known-good
environment instead of whatever resolved on the day.

It is used as a pip *constraints* file (``-c``), not a requirements file: it
pins whatever requirements.txt pulls in and installs nothing extra, so the
optional training stack recorded here is not installed by setup.

Usage, after a full test run passes:
    .\\.venv\\Scripts\\python.exe -m scripts.lock_requirements
"""
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"


def host_tag() -> str:
    """The host a lock applies to: platform plus Python major.minor."""
    return f"{sys.platform}-py{sys.version_info.major}.{sys.version_info.minor}"


def frozen() -> list[str]:
    output = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
        check=True, capture_output=True, text=True,
    ).stdout
    # Direct URL or local-path installs cannot be reproduced from an index.
    return sorted(
        (line for line in output.splitlines() if line and "@" not in line and not line.startswith("-")),
        key=str.casefold,
    )


def main() -> None:
    header = [
        "# Tested package versions -- a pip constraints file, not a requirements file.",
        "# Regenerate after a passing full test run: python -m scripts.lock_requirements",
        f"# host: {host_tag()}",
        f"# python: {platform.python_version()}",
        f"# recorded: {date.today().isoformat()}",
    ]
    LOCK.write_text("\n".join(header + frozen()) + "\n", encoding="utf-8")
    print(f"Wrote {LOCK.name} for {host_tag()}")


if __name__ == "__main__":
    main()
