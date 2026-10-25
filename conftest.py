"""Collection rules for the whole project."""
from pathlib import Path

# runtime\ is HAYAT's own copy of Python and Ollama (scripts/make_portable.py):
# what HAYAT runs on, not HAYAT's tests.
collect_ignore = ["runtime"]


def pytest_configure(config):
    """Create the parent of --basetemp: pytest makes only the last folder.

    "--basetemp=.test-runs/ci" failed every temp-using test on a fresh clone
    (GitHub's first check run, 2026-10-25) because .test-runs did not exist yet.
    """
    basetemp = getattr(config.option, "basetemp", None)
    if basetemp:
        Path(basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)
