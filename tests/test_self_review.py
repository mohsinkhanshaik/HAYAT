"""Self-review of each reply (app/self_review.py) and forgetting on request (MemoryDatabase.forget), 2026-11-09.

The review must come only from recorded facts, say plainly what it does not know, learn from the owner's
verdicts, and never claim to be an experience. Forgetting must remove a memory and everything derived from it.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.memory.database import MemoryDatabase
from app.self_review import SelfReviewLog, level_of, lines, research_signal, review

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
GOOD_RESEARCH = {"run_id": "r", "sources": 5, "independent": 4, "supported": 3, "disputed": 0, "unverified": 0,
                 "complete": True}


def test_a_well_sourced_answer_is_rated_high_and_needs_no_revision():
    item = review(request="When was Python 3.13 released?", intent="RESEARCH", confidence=.95, routed_by="deterministic",
                  answer_source="research", research={**GOOD_RESEARCH, "sources": 6}, now=NOW)
    assert item["level"] == "high" and not item["revise"]["needed"]
    assert "4 independent sources" in " ".join(item["reasons"])
    assert "read as research (95% sure, by deterministic)" in item["trigger"]


def test_weak_signals_lower_reliability_and_say_how_to_revise():
    stale = {"id": 7, "memory_type": "semantic", "content": "The owner lives in Lahore.",
             "timestamp": (NOW - timedelta(days=400)).isoformat(), "metadata": {"successful_uses": 1, "failed_uses": 3}}
    item = review(request="where do i live", intent="QUESTION", confidence=.6, answer_source="brain",
                  spelling=[["liv", "live"]], recalled=[stale], meaning_assumed="jaguar as the animal",
                  research={"run_id": "r2", "sources": 1, "independent": 1, "supported": 0, "disputed": 1,
                            "unverified": 1, "complete": False}, now=NOW)
    assert item["level"] == "low" and item["revise"]["needed"]
    how = " ".join(item["revise"]["how"])
    assert "Check memory #7, which has been wrong before." in how and "second, independent source" in how
    assert "Say which meaning you intended." in how and "Confirm what you asked for." in how
    assert any("liv" in text and "live" in text for text in item["assumptions"])
    assert any("memory #7" in text and "400 days old" in text for text in item["influences"])


def test_an_answer_from_code_is_trusted_and_a_bare_model_answer_says_so():
    assert review(request="2+2", answer_source="exact", now=NOW)["level"] == "high"
    bare = review(request="who wrote hamlet", answer_source="brain", now=NOW)
    assert bare["level"] == "medium"
    assert "rests on the model's own knowledge" in " ".join(bare["reasons"])
    assert "not a view of the model's internal reasoning" in bare["basis"]


def test_verdicts_calibrate_what_the_review_reports(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    for index in range(6):
        item = review(request=f"q{index}", answer_source="research", research=GOOD_RESEARCH, now=NOW)
        log.record(item, "s", f"answer {index}")
        log.judge("s", f"answer {index}", +1 if index < 2 else -1)  # the owner found only 2 of 6 right
    table = log.calibration()
    assert table["high"]["judged"] == 6 and table["high"]["right"] == 2
    fresh = log.calibrate(review(request="q", answer_source="research", research=GOOD_RESEARCH, now=NOW))
    assert fresh["calibrated_score"] < fresh["score"] and fresh["calibrated_on"] == 6
    assert "calibrated on 6 of your verdicts" in lines(fresh)[0]
    assert "2 of 6 right" in log.report()


def test_the_owners_verdict_reaches_the_review_of_that_reply(tmp_path):
    from app.owner_feedback import record_from_message

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    memory.record_message("s1", "user", "capital of france?")
    memory.record_message("s1", "assistant", "Paris.")
    SelfReviewLog.beside(memory).record(review(request="capital of france?", answer_source="brain"), "s1", "Paris.")
    record_from_message(memory, "s1", "that's right")
    assert SelfReviewLog.beside(memory).calibration()["medium"]["right"] == 1


def test_research_signal_reads_the_run_made_during_the_turn(tmp_path):
    path = tmp_path / "knowledge.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE research_runs (id TEXT, query TEXT, started_at TEXT, confidence REAL, complete INTEGER);
        CREATE TABLE run_sources (run_id TEXT, source_id INTEGER, rank INTEGER);
        CREATE TABLE sources (id INTEGER, domain TEXT, independence_group TEXT);
        CREATE TABLE claims (id TEXT, run_id TEXT, status TEXT);
        INSERT INTO research_runs VALUES ('old', 'q', '2026-11-01T00:00:00+00:00', .5, 1);
        INSERT INTO research_runs VALUES ('new', 'q', '2026-11-09T12:00:05+00:00', .8, 1);
        INSERT INTO sources VALUES (1, 'a.org', 'a'), (2, 'b.org', 'b'), (3, 'a.org', 'a');
        INSERT INTO run_sources VALUES ('new', 1, 0), ('new', 2, 1), ('new', 3, 2);
        INSERT INTO claims VALUES ('c1', 'new', 'SUPPORTED'), ('c2', 'new', 'DISPUTED');""")
    db.commit()
    db.close()
    signal = research_signal(path, "2026-11-09T12:00:00+00:00")
    assert signal["run_id"] == "new" and signal["sources"] == 3 and signal["independent"] == 2 and signal["disputed"] == 1
    assert research_signal(path, "2026-11-10T00:00:00+00:00") is None


def test_forgetting_removes_the_memory_and_everything_derived_from_it(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    keep = memory.store_memory("semantic", "The owner's favourite colour is green.", importance=.6)
    gone = memory.store_memory("semantic", "The owner's old address is 12 Elm Street.", importance=.6)
    with memory.connect() as db:
        db.execute("INSERT INTO memory_links (source_id, target_id, relation, weight, evidence_json, created_at, "
                   "last_reinforced_at, use_count) VALUES (?, ?, 'related', .5, '{}', '2026-11-09', '2026-11-09', 0)",
                   (keep, gone))
    assert any(item["id"] == gone for item in memory.retrieve("old address Elm Street", limit=5))
    result = memory.forget([gone, 999])
    assert result["forgotten"] == 1 and result["missing"] == [999] and result["links_removed"] == 1
    assert "Conversation history" in result["not_touched"]
    assert all(item["id"] != gone for item in memory.retrieve("old address Elm Street", limit=5))
    with memory.connect() as db:
        assert not db.execute("SELECT 1 FROM memory_fts WHERE memory_fts MATCH 'Elm'").fetchall()
        assert db.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


def test_the_forget_tools_show_first_and_delete_only_on_confirmation(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.memory import register_memory_tools
    from app.tools.registry import ToolRegistry

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    target = memory.store_memory("semantic", "The owner's old address is 12 Elm Street.", importance=.6)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_memory_tools(registry, memory)
    found = registry.execute("memory_forget_find", {"query": "old address"})
    assert found.success and found.output["matches"][0]["id"] == target and "nothing was deleted" in found.output["summary"]
    refused = registry.execute("memory_forget", {"ids": [target], "confirm": False})
    assert not refused.success and "permanent" in refused.error
    done = registry.execute("memory_forget", {"ids": [target], "confirm": True})
    assert done.success and done.output["forgotten"] == 1
    permission = {item["name"]: item.get("permission") for item in registry.describe()}
    assert permission["memory_forget"] == "filesystem_delete" and permission["memory_forget_find"] == "system_read"


def test_the_details_panel_shows_the_review():
    from app.answer_provenance import describe

    item = review(request="who wrote hamlet", intent="QUESTION", confidence=.9, answer_source="brain", now=NOW)
    runtime = SimpleNamespace(session=SimpleNamespace(last_answer_source="brain", last_self_review=item,
                                                      last_outbound={"nothing_sent": True, "model_runs_on": "this computer"}),
                              conversation=None, model=None)
    text = "\n".join(describe(runtime))
    assert "Self-review: reliability medium" in text and "who wrote hamlet" in text and "Why it arose" in text
    assert "No revision needed" in text
    assert level_of(.8) == "high" and level_of(.6) == "medium" and level_of(.2) == "low"


def test_a_real_turn_is_reviewed_recorded_and_judged(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    reply = rt.assistant.handle("what can you do")
    assert reply.response.metadata["self_review"]["level"] in {"high", "medium", "low"}
    assert rt.session.last_self_review["trigger"].startswith("Your message “what can you do”")
    with sqlite3.connect(tmp_path / "self-review.db") as db:
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE session_id = 'shared-session'").fetchone()[0] == 1
    rt.assistant.handle("that's right")
    assert SelfReviewLog.beside(rt.memory).calibration()[reply.response.metadata["self_review"]["level"]]["right"] == 1


@pytest.mark.parametrize("words", ["I am conscious", "I feel"])
def test_the_review_never_claims_an_experience(words):
    item = review(request="are you sure?", answer_source="brain", now=NOW)
    assert words not in " ".join(lines(item))
