from types import SimpleNamespace

import pytest

from app.learning.continuous import ContinuousLearner, CycleResult


def test_failed_topic_waits_and_does_not_starve_other_work(tmp_path):
    learner = ContinuousLearner(root=tmp_path, cycle=lambda topic: CycleResult(action="index", ok=False))
    learner.enqueue("SQLite durability and recovery", priority=95)
    learner.enqueue("Python thread scheduling behavior", priority=50)
    first = learner.next_topic()
    learner.run_cycle(first)
    assert learner.next_topic() == "Python thread scheduling behavior"
    assert learner.next_topic() == ""
    restarted = ContinuousLearner(root=tmp_path)
    assert restarted.next_topic() == "Python thread scheduling behavior"
    with restarted._queue() as db:
        db.execute("UPDATE learning_queue SET ready_after=0 WHERE topic=?", (first,))
    assert restarted.next_topic() == first


def test_already_learned_failure_is_not_returned_outside_queue(tmp_path):
    runtime = SimpleNamespace(failures=SimpleNamespace(recent=lambda limit: [
        {"dependency": "SQLite", "failure_class": "timeout"}]))
    learner = ContinuousLearner(runtime, root=tmp_path)
    learner.enqueue("SQLite timeout troubleshooting")
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='learned'")
    assert learner.next_topic() == ""


@pytest.mark.parametrize("topic", [
    "Read my file at D:\\private\\plan.txt", "How does my bank account work?",
    "Debug this internal service at 172.16.2.4", "password=very-secret-value troubleshooting",
])
def test_all_queue_producers_reject_private_topics(tmp_path, topic):
    learner = ContinuousLearner(root=tmp_path)
    assert not learner.enqueue(topic, source="unfinished_task")
    assert learner._default_cycle(topic).action == "refused"
    assert not learner.queue_status()["topics"]


def test_retry_wakes_standby_and_resets_deadline(tmp_path):
    learner = ContinuousLearner(root=tmp_path)
    topic = "SQLite transaction recovery mechanisms"
    learner.enqueue(topic)
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='failed',attempts=3,ready_after=9999999999")
    learner._wake.clear()
    assert learner.retry(topic) and learner._wake.is_set()
    assert learner.next_topic() == topic


def test_maintenance_is_bounded_and_reports_real_outcomes(tmp_path):
    calls = []
    control = SimpleNamespace(review_upgrades=lambda **kw: calls.append(kw) or {
        "proposals": [1, 2], "applied": [{"result": {"applied": True}}, {"result": {"applied": False}}],
        "needs_owner_review": [1]})
    learner = ContinuousLearner(SimpleNamespace(metacognition=control), root=tmp_path)
    learner._maintain_if_due()
    learner._maintain_if_due()
    assert calls == [{"apply_small": True}]
    assert learner.last_maintenance["applied"] == 1
    assert learner.last_maintenance["needs_review"] == 1


def test_privacy_pauses_without_spending_retries(tmp_path):
    learner = ContinuousLearner(SimpleNamespace(authority=SimpleNamespace(privacy_mode=True)), root=tmp_path)
    learner.enqueue("SQLite recovery and transactions")
    topic = learner.next_topic()
    result = learner.run_cycle(topic)
    assert result.action == "skipped" and "privacy" in result.detail
    assert learner.queue_status()["topics"][0]["attempts"] == 0


def test_budget_exhaustion_never_uses_up_a_topic(tmp_path):
    exhausted = CycleResult(action="index", ok=False,
                            detail="Repeated-call budget exhausted for research_topic (60 calls).")
    learner = ContinuousLearner(root=tmp_path, cycle=lambda topic: exhausted)
    learner.enqueue("SQLite durability and recovery", priority=95)
    for _ in range(4):
        with learner._queue() as db:
            db.execute("UPDATE learning_queue SET ready_after=0")
        learner.run_cycle(learner.next_topic())
    with learner._queue() as db:
        assert db.execute("SELECT status,attempts FROM learning_queue").fetchone() == ("pending", 0)


def test_topics_failed_by_the_old_shared_budget_get_their_attempts_back(tmp_path):
    learner = ContinuousLearner(root=tmp_path)
    learner.enqueue("SQLite durability and recovery")
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='failed',attempts=3,"
                   "last_error='Repeated-call budget exhausted for research_topic (60 calls).'")
    restarted = ContinuousLearner(root=tmp_path)
    assert restarted.next_topic() == "SQLite durability and recovery"


def test_a_just_learned_topic_is_not_refreshed_in_a_loop(tmp_path):
    learner = ContinuousLearner(root=tmp_path, cycle=lambda topic: CycleResult(action="index", ok=True))
    learner.enqueue("SQLite durability and recovery")
    learner.run_cycle(learner.next_topic())
    assert not learner.enqueue("SQLite durability and recovery", source="stale_refresh", relearn=True)
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET learned_at='2026-01-01T00:00:00+00:00'")
    assert learner.enqueue("SQLite durability and recovery", source="stale_refresh", relearn=True)


@pytest.mark.parametrize("topic", [
    "find current location of someone@example.com", "where does elon musk live",
    "track this person across social media", "who answers +1 313 555 0199 for support",
])
def test_finding_a_person_is_never_background_research(tmp_path, topic):
    from app.learning.continuous import unresearchable_reason

    learner = ContinuousLearner(root=tmp_path)
    assert not learner.enqueue(topic, source="stale_refresh", relearn=True)
    assert unresearchable_reason(topic)


def test_follow_up_angles_on_commands_and_changing_facts_are_retired():
    from app.learning.continuous import unresearchable_reason

    assert unresearchable_reason("common mistakes and failure modes with brief today")
    assert unresearchable_reason("how brief today actually works, mechanism and internals")
    assert unresearchable_reason("how rust ownership actually works, mechanism and internals") is None


def test_revalidated_page_counts_as_verified_now(tmp_path):
    """A 304 confirms the page is current; claims from it must not be born expired."""
    from datetime import UTC, datetime, timedelta

    from app.research.knowledge import KnowledgeStore
    from app.research.manager import ResearchManager
    from app.research.models import SearchResult, SourceDocument

    old = (datetime.now(UTC) - timedelta(hours=9)).isoformat()
    knowledge = KnowledgeStore(tmp_path / "knowledge.db")
    page = SourceDocument("https://news.example/today", "https://news.example/today", "Today", "news.example",
                          "body", old, 200, "text/html", "h1", 4, quality=0.8)
    manager = ResearchManager({"database": str(tmp_path / "knowledge.db"), "respect_robots_txt": False},
                              search_provider=object(), knowledge=knowledge)
    knowledge.cache_source(page, (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), etag='"v1"')
    manager.fetcher.fetch = lambda url, max_bytes, request_headers=None: SimpleNamespace(
        status_code=304, elapsed_ms=5, headers={})
    document, _ms, cached = manager._fetch_extract_cached(
        SearchResult("Today", "https://news.example/today"), "what happened today", 100_000)
    assert cached and document.cache_status == "REVALIDATED"
    assert datetime.fromisoformat(document.retrieved_at) > datetime.now(UTC) - timedelta(minutes=1)
