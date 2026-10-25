from types import SimpleNamespace

import pytest

from app.gui_explorer import workspace_page
from app.memory.database import MemoryDatabase


@pytest.fixture
def runtime(tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    return SimpleNamespace(memory=memory, session=SimpleNamespace(session_id="current"))


def test_memory_pages_preserve_evidence_and_do_not_modify_records(runtime):
    for index in range(31):
        runtime.memory.store_memory("semantic", f"Saved research note {index}", .5, "attachment",
            metadata={"trust_level": "untrusted_evidence", "verified": False})
    first = workspace_page(runtime, "memory")
    second = workspace_page(runtime, "memory", page=2)
    assert len(first["rows"]) == 30 and first["has_more"]
    assert len(second["rows"]) == 1 and not second["has_more"]
    assert "untrusted_evidence" in first["rows"][0]["detail"]
    assert first["rows"][0]["title"] != second["rows"][0]["title"]
    assert workspace_page(runtime, "memory", "research")["rows"]
    with runtime.memory.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 31


def test_history_search_treats_sql_wildcards_as_literal_text(runtime):
    runtime.memory.record_message("old", "user", "Progress 50% complete")
    runtime.memory.record_message("current", "user", "An unrelated question")
    result = workspace_page(runtime, "history", "%")
    assert len(result["rows"]) == 1
    assert "Conversation: old" in result["rows"][0]["detail"]


def test_tasks_show_saved_lifecycle(runtime):
    runtime.memory.create_task("t1", "Inspect the release", [])
    result = workspace_page(runtime, "tasks", "release")
    assert len(result["rows"]) == 1
    assert "Task t1" in result["rows"][0]["detail"]


def test_model_inventory_reports_actual_context_and_loaded_state(runtime):
    runtime.storage = SimpleNamespace(model_inventory=lambda: {"models": [
        {"model": "test-model", "loaded": True, "context": 4096, "capabilities": ["completion"]}]})
    result = workspace_page(runtime, "models")
    assert result["rows"][0]["state"] == "Loaded"
    assert "4096 tokens" in result["rows"][0]["detail"]


def test_background_displays_wait_reason_and_existing_budget(runtime):
    runtime.learner = SimpleNamespace(status=lambda: {
        "worker_state": "waiting_budget", "waiting_because": "hourly cycle budget reached",
        "spent": {"cycles_this_hour": 5, "requests_this_hour": 9},
        "budget": {"max_cycles_per_hour": 5, "max_requests_per_hour": 20},
        "queue": {"topics": []}})
    result = workspace_page(runtime, "background")
    assert "5/5 cycles" in result["summary"]
    assert "hourly cycle budget reached" in result["summary"]
