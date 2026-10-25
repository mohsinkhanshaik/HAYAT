"""Tests never write into the owner's live data/ folder (see conftest.py)."""
from __future__ import annotations

import sqlite3

from tests.conftest import LIVE_DATA, SANDBOX, sandboxed


def _file_of(connection: sqlite3.Connection) -> str:
    return connection.execute("PRAGMA database_list").fetchone()[2]


def test_a_live_store_opens_in_the_sandbox_instead():
    connection = sqlite3.connect(LIVE_DATA / "memory" / "agent.db")
    try:
        assert _file_of(connection).startswith(str(SANDBOX))
    finally:
        connection.close()


def test_read_only_uris_and_relative_paths_are_caught_too(monkeypatch):
    monkeypatch.chdir(LIVE_DATA.parent)
    assert sandboxed("data/knowledge/knowledge.db").startswith(str(SANDBOX))
    moved = sandboxed(f"file:{(LIVE_DATA / 'memory' / 'events.db').as_posix()}?mode=ro")
    assert moved.startswith("file:") and moved.endswith("?mode=ro") and SANDBOX.as_posix() in moved


def test_the_full_runtime_remembers_nothing_in_the_owners_memory():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    marker = "isolation check: what model are you using?"
    try:
        runtime.assistant.handle(marker)
        stored = sandboxed(runtime.memory.path)
        assert str(stored).startswith(str(SANDBOX))
        with sqlite3.connect(stored) as db:
            assert db.execute("SELECT count(*) FROM conversations WHERE content = ?", (marker,)).fetchone()[0] >= 1
    finally:
        runtime.close()


def test_other_paths_are_left_alone(tmp_path):
    assert sandboxed(":memory:") == ":memory:"
    assert sandboxed(tmp_path / "x.db") == tmp_path / "x.db"
    assert sandboxed(LIVE_DATA / "work" / "scratch.db") == LIVE_DATA / "work" / "scratch.db"


def test_gallery_projects_workspace_and_voice_log_go_to_the_sandbox():
    from app import projects
    from app.gui import HayatGUI
    from app.models import diffusion
    from app.logging import TaskLogger
    from app.voice import manager as voice

    for path in (diffusion.IMAGE_ROOT, diffusion.IMAGE_MANIFEST, diffusion.VIDEO_ROOT,
                 projects.ProjectManager().path, HayatGUI._workspace_file.fget(None),
                 voice.PROJECT_ROOT, TaskLogger("task-isolation-check").path):
        assert str(path).startswith(str(SANDBOX)), path
    assert diffusion.ImageGenerator().output_root == diffusion.IMAGE_ROOT
