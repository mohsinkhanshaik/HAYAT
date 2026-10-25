"""The public release builder: private history stays out, and updates are ordinary commits."""
from __future__ import annotations

import subprocess

import pytest


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def public_clone(tmp_path):
    repo = tmp_path / "public"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "keep.md").write_text("old\n", encoding="utf-8")
    (repo / "gone.md").write_text("private build history\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "first")
    return repo


def test_an_update_is_one_normal_commit_that_adds_changes_and_removes(tmp_path, public_clone):
    from scripts.make_public_release import sync_into

    build = tmp_path / "build"
    (build / "docs").mkdir(parents=True)
    (build / "keep.md").write_text("new\n", encoding="utf-8")
    (build / "docs" / "added.md").write_text("added\n", encoding="utf-8")
    commit = sync_into(build, public_clone, "Update")
    assert commit != "nothing changed"
    assert git(public_clone, "rev-list", "--count", "HEAD").strip() == "2"  # history kept, not rewritten
    assert sorted(git(public_clone, "ls-files").split()) == ["docs/added.md", "keep.md"]
    assert (public_clone / "keep.md").read_text(encoding="utf-8") == "new\n"
    assert sync_into(build, public_clone, "Again") == "nothing changed"


def test_an_update_refuses_a_clone_with_uncommitted_work(tmp_path, public_clone):
    from scripts.make_public_release import sync_into

    (public_clone / "keep.md").write_text("edited by hand\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="uncommitted"):
        sync_into(tmp_path, public_clone, "Update")


def test_build_history_and_private_owner_never_reach_the_public_tree():
    from scripts.make_public_release import DATED, PRIVATE_PATHS, PUBLIC_EDITS

    assert DATED.search("gui-review-2026-10-24.md") and not DATED.search("how-to-use-hayat.md")
    assert "release" in PRIVATE_PATHS and "data/knowledge" in PRIVATE_PATHS
    owner_edits = {(name, new.strip()) for name, _old, new in PUBLIC_EDITS}
    assert ("app/identity.py", 'OWNER_NAME = ""') in owner_edits
    assert ("tests/test_identity_and_watch.py", 'EXPECTED_OWNER = ("", "")') in owner_edits
