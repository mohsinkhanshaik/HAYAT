from app.memory.database import MemoryDatabase
from app.memory.procedural import ProceduralMemory


def test_memory_and_task_lifecycle(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    database.create_task("t1", "inspect python files", [{"tool": "search_files"}])
    database.record_action("t1", 0, "search_files", {}, {"success": True}, {"passed": True})
    database.store_memory("episodic", "Inspected Python files successfully", 0.9, "task", "t1")
    database.update_task("t1", "complete", "done")
    assert database.retrieve("Python files")[0]["task_id"] == "t1"
    assert database.recent_tasks()[0]["status"] == "complete"
    assert database.stats()["actions"] == 1


def test_memory_consolidation_is_report_only(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    first = database.store_memory("semantic", "Docker uses containers.", source="test")
    second = database.store_memory("semantic", "Docker uses containers.", source="test")
    report = database.consolidation_report()
    assert report["duplicate_records"] == 1
    assert report["records_deleted"] == 0
    assert report["exact_duplicate_groups"][0]["ids"] == [first, second]


def test_procedure_experience_updates_confidence_and_latency(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    procedures = ProceduralMemory(database)
    procedures.save_candidate("run_tests", "Run project tests", {"tools": ["run_python"]}, trusted=True)
    first = procedures.record_result("run_tests", True, 100)
    second = procedures.record_result("run_tests", False, 300)
    assert first["confidence"] > second["confidence"]
    assert second["average_latency_ms"] == 200
    assert procedures.trusted()[0]["last_used_at"]


def test_latest_session_resumes_the_last_thread(tmp_path):
    """A restart should be able to pick up the conversation it left, not start

    blank. latest_session names the most recent thread so the runtime can
    adopt its id and reload the history."""
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    assert database.latest_session() is None  # nothing to resume yet

    database.record_message("session-old", "user", "first thread")
    database.record_message("session-new", "user", "what were we talking about")
    database.record_message("session-new", "assistant", "your $1000 startup plan")

    latest = database.latest_session()
    assert latest["session_id"] == "session-new"
    # And adopting that id reloads the thread that was in flight.
    history = database.conversation_history("session-new")
    assert [m["content"] for m in history] == [
        "what were we talking about", "your $1000 startup plan"]


def test_a_stale_thread_does_not_silently_reopen(tmp_path):
    """Resume means "pick up where we just were", not reopen a month-old chat.

    A recency window keeps an ancient thread from coming back as if no time
    had passed."""
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    database.record_message("session-x", "user", "old talk")
    # Just-now activity is within any sane window.
    assert database.latest_session(max_age_seconds=3600)["session_id"] == "session-x"
    # A zero-length window makes everything already too old to resume.
    assert database.latest_session(max_age_seconds=0) is None
