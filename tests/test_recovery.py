"""Knowing it was cut off, and being careful about what that means."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.memory.database import MemoryDatabase
from app.recovery import INTERRUPTED, CrashRecovery


@pytest.fixture
def memory(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path / "agent.db")
    database.initialize()
    return database


@pytest.fixture
def recovery(memory, tmp_path: Path) -> CrashRecovery:
    return CrashRecovery(memory, tmp_path / "alive.json")


def _half_finished(memory, task_id="task-1", steps=3, reached=0):
    memory.create_task(task_id, "back up the photo library", [
        {"description": f"step {n}", "tool": "copy_file"} for n in range(steps)])
    memory.record_checkpoint(task_id, reached, "RUNNING", {"resume_policy": "manual_review_required"})


def _previous_run_died(recovery, pid=999999):
    """Leave the marker a crashed process would have left."""
    recovery.marker.write_text(json.dumps(
        {"pid": pid, "session_id": "old", "started_at": "2026-08-19T06:00:00+00:00"}),
        encoding="utf-8")


# ---------------------------------------------------------------- detecting


def test_a_clean_shutdown_leaves_nothing_to_report(recovery):
    recovery.mark_started("session-a")
    assert recovery.marker.is_file()
    recovery.mark_clean_exit()
    assert not recovery.marker.is_file()

    second = CrashRecovery(recovery.memory, recovery.marker)
    second.mark_started("session-b")
    assert not second.unclean
    assert second.greeting() == ""  # silence is the right output here


def test_a_marker_left_behind_is_how_a_crash_is_known(recovery):
    _previous_run_died(recovery)
    recovery.mark_started("session-b")
    assert recovery.unclean
    assert recovery.previous["session_id"] == "old"


def test_its_own_marker_is_not_mistaken_for_a_crash(recovery):
    """A marker naming this very process is this run's own."""
    _previous_run_died(recovery, pid=os.getpid())
    recovery.mark_started("session-b")
    assert not recovery.unclean


def test_a_corrupt_marker_does_not_break_startup(recovery):
    """A crash detector that crashes on a truncated file is a strange way to
    make startup more reliable. Half a JSON object is what a power cut during
    the write actually leaves."""
    recovery.marker.write_text('{"pid": 12', encoding="utf-8")
    assert recovery.mark_started("session-b") == {"unclean_shutdown": False, "previous": {}}
    assert recovery.greeting() == ""


# --------------------------------------------------------------- reporting


def test_it_says_what_was_in_flight_and_how_far_it_got(recovery, memory):
    _half_finished(memory, reached=1)
    _previous_run_died(recovery)
    recovery.mark_started("session-b")

    said = recovery.greeting()
    assert "didn't shut down cleanly" in said
    assert "back up the photo library" in said
    assert "2 of 3 steps" in said
    assert "step 2" in said  # what would have come next


def test_an_interrupted_task_stops_claiming_to_be_running(recovery, memory):
    """Left as "running" forever, it reads as work still underway."""
    _half_finished(memory)
    _previous_run_died(recovery)
    recovery.mark_started("session-b")
    recovery.greeting()

    with memory.connect() as db:
        status = db.execute("SELECT status FROM tasks WHERE id='task-1'").fetchone()[0]
    assert status == INTERRUPTED
    assert recovery.unfinished_tasks() == []  # and it is not reported twice


def test_a_crash_with_nothing_in_flight_says_only_that(recovery):
    _previous_run_died(recovery)
    recovery.mark_started("session-b")
    said = recovery.greeting()
    assert "nothing was lost" in said


# ------------------------------------------------------- and does not resume


def test_it_never_resumes_anything_by_itself(recovery, memory):
    """The checkpoints have said manual_review_required since they were first
    written, and they are right. A half-executed plan replayed blind repeats
    whatever the finished steps already did -- a file written twice, a message
    sent twice, a delete re-attempted against something no longer there.

    HAYAT may say where it stopped. Whether step 4 should happen now depends
    on whether the world still looks the way the plan assumed, and only Mohsin
    can see that.
    """
    _half_finished(memory, reached=1)
    _previous_run_died(recovery)
    recovery.mark_started("session-b")

    said = recovery.greeting()
    assert "haven't picked any of them back up" in said
    assert "could do it twice" in said  # the reason, not just the refusal

    # Nothing was executed on the way to saying so: no actions were recorded
    # against the old task, and its remaining steps are still unrun.
    with memory.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM actions WHERE task_id='task-1'").fetchone()[0] == 0
    assert all(item["resume_policy"] == "manual_review_required"
               for item in CrashRecovery(memory, recovery.marker).unfinished_tasks())


def test_the_runtime_claims_liveness_and_releases_it(tmp_path, monkeypatch):
    import app.runtime as runtime_module

    # A live desktop instance may own the production marker. Integration
    # tests must neither declare it crashed nor remove its liveness evidence.
    monkeypatch.setattr(runtime_module, "CrashRecovery", lambda memory:
                        CrashRecovery(memory, tmp_path / "runtime-alive.json"))

    runtime = runtime_module.build_runtime(use_model=False)
    try:
        assert runtime.recovery is not None
        assert runtime.recovery.marker.is_file()
    finally:
        runtime.close()
    assert not runtime.recovery.marker.is_file()
