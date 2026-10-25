"""An old but relevant memory must remain retrievable after many newer records.

Regression: candidate generation selected `ORDER BY timestamp DESC LIMIT 500`,
so anything older than 500 newer records was never scored, however well it
matched. These tests bury the correct answer and require it back.
"""

from __future__ import annotations

import time

import pytest

from app.memory.database import MemoryDatabase


ANSWER = "Project Atlas lives at D:/Projects/Atlas and uses the Kestrel scheduler."
QUESTION = "Where does Project Atlas live and what scheduler does it use?"


def _buried(tmp_path, burial: int, *, answer: str = ANSWER) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path / f"memory-{burial}.db")
    database.initialize()
    database.store_memory("semantic", answer, 0.95, "user")
    for index in range(burial):
        database.store_memory(
            "episodic", f"Routine log entry number {index} about unrelated build output and timings.", 0.4, "agent"
        )
    return database


@pytest.mark.parametrize("burial", [10, 200, 600, 1200])
def test_old_relevant_memory_survives_newer_records(tmp_path, burial):
    database = _buried(tmp_path, burial)
    rows = database.retrieve(QUESTION, limit=5)
    contents = [row["content"] for row in rows]
    assert any("Atlas" in item for item in contents), f"buried under {burial} newer records"


def test_correct_memory_ranks_first_when_buried(tmp_path):
    database = _buried(tmp_path, 800)
    rows = database.retrieve(QUESTION, limit=5)
    assert rows and "Atlas" in rows[0]["content"]


def test_recent_memory_behaviour_is_unchanged(tmp_path):
    """The recency path is preserved, not replaced."""
    database = MemoryDatabase(tmp_path / "recent.db")
    database.initialize()
    for index in range(20):
        database.store_memory("episodic", f"Build {index} completed successfully with no warnings.", 0.5, "agent")
    rows = database.retrieve("build completed successfully", limit=5)
    assert rows
    assert all("Build" in row["content"] for row in rows)


def test_candidate_limit_setting_is_consumed(tmp_path, monkeypatch):
    """Regression: memory_retrieval.candidate_limit was decorative configuration."""
    from app.memory import database as database_module

    seen: dict[str, int] = {}
    original = MemoryDatabase._candidates

    def spy(self, where, parameters, tokens, candidate_limit, *extra):
        seen["limit"] = candidate_limit
        return original(self, where, parameters, tokens, candidate_limit, *extra)

    monkeypatch.setattr(MemoryDatabase, "_candidates", spy)
    monkeypatch.setattr(database_module, "load_settings", lambda: {"memory_retrieval": {"candidate_limit": 137}})
    database = MemoryDatabase(tmp_path / "limit.db")
    database.initialize()
    database.store_memory("semantic", "a fact about scheduling", 0.6, "user")
    database.retrieve("scheduling", limit=3)
    assert seen["limit"] == 137


def test_retrieval_latency_stays_bounded(tmp_path):
    database = _buried(tmp_path, 1500)
    start = time.perf_counter()
    database.retrieve(QUESTION, limit=5)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 2000, f"retrieval took {elapsed_ms:.0f}ms"


def test_special_characters_in_query_do_not_break_the_like_prefilter(tmp_path):
    database = MemoryDatabase(tmp_path / "escape.db")
    database.initialize()
    database.store_memory("semantic", "The rate is 100% of the configured budget_value setting.", 0.9, "user")
    for index in range(600):
        database.store_memory("episodic", f"filler {index}", 0.1, "agent")
    rows = database.retrieve("what is the configured budget_value", limit=5)
    assert any("budget_value" in row["content"] for row in rows)


def test_memory_type_filter_still_applies_with_union_candidates(tmp_path):
    database = _buried(tmp_path, 600)
    rows = database.retrieve(QUESTION, memory_type="episodic", limit=5)
    assert rows
    assert all(row["memory_type"] == "episodic" for row in rows)


def test_memory_full_text_index_is_live_and_searchable(tmp_path):
    database = MemoryDatabase(tmp_path / "fts.db")
    database.initialize()
    memory_id = database.store_memory(
        "semantic", "Orchid protocol uses the Zephyr handshake.", 0.9, "owner"
    )
    with database.connect() as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "memory_fts" not in tables:
            pytest.skip("This SQLite build does not provide FTS5")
        row = db.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH 'zephyr'"
        ).fetchone()
    assert row and int(row[0]) == memory_id
    assert database.retrieve("Which protocol uses Zephyr?")[0]["id"] == memory_id
