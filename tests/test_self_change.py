from __future__ import annotations

import subprocess
import time
from pathlib import Path

from app.self_change import SelfChangeTracker


def _init_repo(root: Path) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "module.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


def test_clean_tree_reports_current_and_no_restart(tmp_path):
    _init_repo(tmp_path)
    status = SelfChangeTracker(tmp_path).status()
    assert status["state"] == "CURRENT"
    assert status["restart_required"] is False
    assert status["changed_file_count"] == 0
    assert status["startup_commit"] == status["current_commit"]


def test_file_edited_after_start_requires_restart(tmp_path):
    """The user may edit D:\\AI outside the agent. A file changed on disk
    after the process started is not loaded by the running interpreter, so
    the agent must report DISK_CHANGED_RUNTIME_OLD rather than claiming it
    has no evidence of the change."""
    _init_repo(tmp_path)
    tracker = SelfChangeTracker(tmp_path)
    time.sleep(1.1)  # filesystem mtime granularity
    (tmp_path / "app" / "module.py").write_text("x = 2\n", encoding="utf-8")

    status = tracker.status()
    assert status["state"] == "DISK_CHANGED_RUNTIME_OLD"
    assert status["restart_required"] is True
    assert "app\\module.py" in status["changed_files_since_start"] or "app/module.py" in status["changed_files_since_start"]


def test_untracked_directories_are_not_watched(tmp_path):
    """Model/cache trees must not be scanned -- only app/config/scripts/tests."""
    _init_repo(tmp_path)
    (tmp_path / "data" / "models").mkdir(parents=True)
    tracker = SelfChangeTracker(tmp_path)
    time.sleep(1.1)
    (tmp_path / "data" / "models" / "weights.bin").write_bytes(b"0" * 16)

    status = tracker.status()
    assert status["changed_file_count"] == 0
