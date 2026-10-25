"""Detects code/config changes on disk since this process started.

This project has no hot-reload mechanism: any change under the watched
directories while the process is running requires a restart to take effect.
That makes the state model simple -- CURRENT or DISK_CHANGED_RUNTIME_OLD --
rather than needing to actually detect whether specific modules were
re-imported.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WATCHED_DIRS = ("app", "config", "scripts", "tests")
WATCHED_FILES = ("main.py", "requirements.txt")


def _git(project_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=project_root, capture_output=True, text=True, timeout=5)
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


@dataclass(slots=True)
class SelfChangeTracker:
    """Snapshots Git HEAD and watched-file state at construction; compares on demand."""

    project_root: Path
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    startup_commit: str | None = field(init=False, default=None)
    startup_dirty_files: tuple[str, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        self.startup_commit = _git(self.project_root, "rev-parse", "HEAD")
        self.startup_dirty_files = tuple(self._dirty_files())

    def _dirty_files(self) -> list[str]:
        # Shared parser: `_git` strips stdout, which removes the leading space
        # of the first porcelain line, so a fixed offset loses one character
        # from exactly one path per call.
        from app.agents.repair import _parse_porcelain

        return _parse_porcelain(_git(self.project_root, "status", "--porcelain"))

    def _watched_files(self):
        for name in WATCHED_FILES:
            path = self.project_root / name
            if path.is_file():
                yield path
        for name in WATCHED_DIRS:
            root = self.project_root / name
            if not root.is_dir():
                continue
            stack = [root]
            while stack:
                current = stack.pop()
                try:
                    entries = os.scandir(current)
                except OSError:
                    continue
                with entries:
                    for entry in entries:
                        try:
                            if entry.is_symlink() or entry.name == "__pycache__":
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                yield Path(entry.path)
                        except OSError:
                            continue

    def status(self) -> dict[str, Any]:
        current_commit = _git(self.project_root, "rev-parse", "HEAD")
        current_dirty = self._dirty_files()
        changed_since_start: list[str] = []
        for path in self._watched_files():
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            except OSError:
                continue
            if mtime > self.started_at:
                changed_since_start.append(str(path.relative_to(self.project_root)))
        commit_changed = current_commit != self.startup_commit
        dirty_changed = tuple(current_dirty) != self.startup_dirty_files
        disk_changed = bool(changed_since_start) or commit_changed or dirty_changed
        return {
            "runtime_started_at": self.started_at.isoformat(),
            "startup_commit": self.startup_commit,
            "current_commit": current_commit,
            "commit_changed_since_start": commit_changed,
            "working_tree_dirty": bool(current_dirty),
            "dirty_file_count": len(current_dirty),
            "changed_files_since_start": sorted(changed_since_start)[:50],
            "changed_file_count": len(changed_since_start),
            "state": "DISK_CHANGED_RUNTIME_OLD" if disk_changed else "CURRENT",
            "restart_required": disk_changed,
        }
