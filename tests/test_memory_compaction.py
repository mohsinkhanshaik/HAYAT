"""Keeping what a memory meant, and letting go of its bulk."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.memory.compaction import (
    COMPACTED_MARKER, WORTH_COMPACTING, MemoryCompactor, digest, slim_metadata,
)
from app.memory.database import MemoryDatabase


@pytest.fixture
def memory(tmp_path):
    database = MemoryDatabase(tmp_path / "agent.db")
    database.initialize()
    return database


def _old(days: int = 30) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _write(memory, content, metadata, timestamp):
    with memory.connect() as db:
        cursor = db.execute(
            "INSERT INTO memories(timestamp, memory_type, content, importance, source, "
            "metadata_json) VALUES(?,?,?,?,?,?)",
            (timestamp, "episodic", content, 0.8, "task", json.dumps(metadata)))
        return cursor.lastrowid


#: The shape of a real one: a task history with the whole tool output inlined.
BIG_CONTENT = (
    "Task succeeded. Verified 1 action(s). Goal: diagnose the NCCL timeout on the "
    "8-GPU Kubernetes inference cluster where p95 latency rose 2.4x. "
) + ("Long transcript of every tool result inlined here. " * 400)

BIG_METADATA = {
    "goal": "diagnose the NCCL timeout on the 8-GPU Kubernetes inference cluster",
    "complete": True,
    "history": [{"step": 1, "tool": "research_topic", "arguments": {"topic": "NCCL"},
                 "result": {"output": "x" * 20000}, "verification": {"passed": True}}],
}


# --------------------------------------------------------------- digesting


def test_a_huge_memory_becomes_a_small_one():
    """The real one was 540,802 characters, because a tool returned a page of
    text and the page went in whole."""
    small = digest(BIG_CONTENT, BIG_METADATA)
    assert len(small) < len(BIG_CONTENT) / 10
    assert small.startswith(COMPACTED_MARKER)


def test_the_goal_survives_verbatim():
    """It is the sentence someone would search with. Paraphrasing it would
    quietly break the retrieval this is supposed to preserve."""
    small = digest(BIG_CONTENT, BIG_METADATA)
    assert "NCCL timeout" in small and "Kubernetes" in small


def test_the_tools_and_the_specifics_survive():
    small = digest(BIG_CONTENT, BIG_METADATA)
    assert "research_topic" in small
    assert "Details:" in small


def test_a_memory_with_no_metadata_still_digests():
    small = digest("Task failed. Goal: install the thing. " + "noise " * 500, {})
    assert "install the thing" in small


def test_the_transcript_is_dropped_from_the_metadata_but_not_the_tools():
    """`history` holds every tool result in full and is where the half-megabyte
    row came from."""
    slim = slim_metadata(BIG_METADATA)
    assert "history" not in slim
    assert slim["tools"] == ["research_topic"]
    assert slim["steps"] == 1
    assert slim["goal"] == BIG_METADATA["goal"]
    assert len(json.dumps(slim)) < len(json.dumps(BIG_METADATA)) / 10


# ---------------------------------------------------------------- running


def test_old_and_large_memories_are_compacted(memory):
    identifier = _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    report = MemoryCompactor(memory).run()

    assert report.compacted == 1
    assert report.saved > 10_000
    with memory.connect() as db:
        row = db.execute("SELECT content FROM memories WHERE id = ?", (identifier,)).fetchone()
    assert row["content"].startswith(COMPACTED_MARKER)
    assert "NCCL timeout" in row["content"]


def test_recent_memories_are_left_completely_alone(memory):
    """A memory from this week is still being used to follow the current
    thread. Compressing it trades something needed now for disk that is not
    short."""
    identifier = _write(memory, BIG_CONTENT, BIG_METADATA, datetime.now(UTC).isoformat())
    report = MemoryCompactor(memory).run()

    assert report.compacted == 0 and report.skipped_recent == 1
    with memory.connect() as db:
        row = db.execute("SELECT content FROM memories WHERE id = ?", (identifier,)).fetchone()
    assert row["content"] == BIG_CONTENT


def test_small_memories_are_not_worth_touching(memory):
    _write(memory, "Task succeeded. Goal: something short.", {"goal": "something short"}, _old())
    assert MemoryCompactor(memory).run().compacted == 0


def test_compacting_twice_does_nothing_the_second_time(memory):
    _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    compactor = MemoryCompactor(memory)
    assert compactor.run().compacted == 1
    assert compactor.run().compacted == 0


def test_a_dry_run_changes_nothing(memory):
    identifier = _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    preview = MemoryCompactor(memory).preview()

    assert preview["compacted"] == 1
    with memory.connect() as db:
        row = db.execute("SELECT content FROM memories WHERE id = ?", (identifier,)).fetchone()
    assert row["content"] == BIG_CONTENT, "a preview wrote to the database"


def test_the_row_is_never_deleted(memory):
    identifier = _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    MemoryCompactor(memory).run()
    with memory.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memories WHERE id = ?",
                          (identifier,)).fetchone()[0] == 1


# --------------------------------------------------------------- retrieval


def test_a_compacted_memory_can_still_be_found(memory):
    """The whole point of keeping the goal and the specifics. If compaction
    breaks search it has not saved space, it has lost the memory."""
    _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    MemoryCompactor(memory).run()

    found = memory.retrieve("NCCL timeout Kubernetes inference", limit=5)
    assert found, "compaction made the memory unfindable"
    assert any("NCCL" in str(item.get("content", "")) for item in found)


def test_status_says_how_much_is_still_compressible(memory):
    _write(memory, BIG_CONTENT, BIG_METADATA, _old())
    compactor = MemoryCompactor(memory)

    before = compactor.status()
    assert before["compressible"] == 1 and before["already_compacted"] == 0
    compactor.run()
    after = compactor.status()
    assert after["compressible"] == 0 and after["already_compacted"] == 1
    assert after["bytes"] < before["bytes"]


def test_the_threshold_is_where_compressing_starts_paying():
    assert 500 <= WORTH_COMPACTING <= 8000


def test_it_is_wired_into_the_runtime():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False)
    try:
        assert runtime.compaction is not None
        assert "memories" in runtime.compaction.status()
    finally:
        runtime.close()
