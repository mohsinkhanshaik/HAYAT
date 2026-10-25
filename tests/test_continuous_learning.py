"""The one subsystem that acts with nobody watching.

Every other path in this project ends at a person who sees the result and can
say no. A loop running for days does not, so these tests assume any single
cycle might be wrong and check that the containment holds anyway: the
authority is refused at the kernel, the budget stops rather than throttles,
the kill switch works from outside the process, and the whole thing can be
reviewed afterwards.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.learning.continuous import (
    PERMITTED_ACTIONS,
    ContinuousLearner,
    CycleResult,
    LearningBudget,
    anticipated_questions,
    reflection_topics,
)
from app.safety.authority import (
    BACKGROUND_LEARNER_CATEGORIES,
    ActionRequest,
    AuthorityManager,
    Decision,
    Principal,
    RiskTier,
)


def _request(category: str, **kwargs) -> ActionRequest:
    return ActionRequest(tool=kwargs.pop("tool", category), category=category,
                         principal=Principal.BACKGROUND_LEARNER, **kwargs)


def _learner(tmp_path, **kwargs) -> ContinuousLearner:
    return ContinuousLearner(root=tmp_path, **kwargs)


# ------------------------------------------------- authority, not good intent


@pytest.mark.parametrize("category", sorted(BACKGROUND_LEARNER_CATEGORIES))
def test_the_learner_may_read(category):
    assert AuthorityManager(owner_mode=True).evaluate(_request(category)).allowed


@pytest.mark.parametrize(
    "category",
    ["filesystem_write_project", "filesystem_delete", "computer_input", "camera_use",
     "clipboard_read", "browser_click", "browser_type", "public_download",
     "application_launch", "powershell_safe", "powershell_admin", "install_system_software",
     "external_post", "send_message", "login_account", "credential_access"],
)
def test_the_learner_may_not_act(category):
    """It reads; it does not act. Enforced by the kernel, not by this module."""
    decision = AuthorityManager(owner_mode=True).evaluate(_request(category))
    assert decision.decision is Decision.DENY, f"background learning reached {category}"


def test_owner_mode_does_not_widen_the_learner():
    """An unattended process must not inherit authority granted for watched work."""
    authority = AuthorityManager(owner_mode=True)
    authority.grant("filesystem_write_project", scope="*")
    assert not authority.evaluate(_request("filesystem_write_project")).allowed


def test_an_envelope_does_not_widen_the_learner():
    authority = AuthorityManager(owner_mode=True)
    authority.open_envelope({"public_download"}, turn_id="turn-1")
    assert not authority.evaluate(_request("public_download", turn_id="turn-1")).allowed


def test_the_learner_can_never_reach_another_machine():
    """The boundary the owner asked for in words, expressed as a test."""
    authority = AuthorityManager(owner_mode=True)
    for category in ("external_post", "send_message", "submit_form", "login_account",
                     "bypass_access_control", "powershell_admin"):
        assert authority.evaluate(_request(category)).decision is Decision.DENY


def test_every_permitted_category_is_read_only_except_verified_knowledge_storage():
    """The only background write is the constrained research knowledge path."""
    from app.safety.authority import CATEGORY_TIERS, BACKGROUND_LEARNER_WRITE_CATEGORIES

    for category in BACKGROUND_LEARNER_CATEGORIES:
        assert CATEGORY_TIERS[category] <= RiskTier.PASSIVE or category in BACKGROUND_LEARNER_WRITE_CATEGORIES
    assert BACKGROUND_LEARNER_WRITE_CATEGORIES == {"knowledge_research"}


# ----------------------------------------------------------------- kill switch


def test_a_file_on_disk_stops_it_without_this_process_cooperating(tmp_path):
    learner = _learner(tmp_path)
    assert learner.may_run()[0]

    learner.write_stop_file("owner halted it")

    allowed, reason = learner.may_run()
    assert not allowed
    assert "STOP" in reason


def test_the_halt_can_be_lifted(tmp_path):
    learner = _learner(tmp_path)
    learner.write_stop_file()
    learner.clear_stop_file()
    assert learner.may_run()[0]


def test_a_halted_learner_runs_no_cycle(tmp_path):
    ran: list[str] = []
    learner = _learner(tmp_path, cycle=lambda topic: ran.append(topic) or CycleResult(action="search", ok=True))
    learner.write_stop_file()

    result = learner.run_cycle("anything")

    assert ran == [], "a halted learner must not do work"
    assert result.action == "skipped"


def test_request_stop_records_why(tmp_path):
    learner = _learner(tmp_path)
    learner.request_stop("disk filling up")
    assert not learner.may_run()[0]
    assert learner.stopped_because == "disk filling up"


# --------------------------------------------------------------- the budget


def test_the_cycle_ceiling_stops_rather_than_throttles(tmp_path):
    learner = _learner(tmp_path, budget=LearningBudget(max_cycles_per_hour=2))
    learner.ledger.cycles_this_hour = 2

    allowed, reason = learner.may_run()

    assert not allowed
    assert "cycle ceiling" in reason


def test_the_request_ceiling_is_enforced(tmp_path):
    learner = _learner(tmp_path, budget=LearningBudget(max_requests_per_hour=5))
    learner.ledger.requests_this_hour = 5
    assert "request ceiling" in learner.may_run()[1]


def test_the_daily_write_ceiling_is_enforced(tmp_path):
    learner = _learner(tmp_path, budget=LearningBudget(max_bytes_per_day=1000))
    learner.ledger.bytes_written = 1000
    assert "daily write ceiling" in learner.may_run()[1]


def test_spending_is_counted_from_real_cycles(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="search", topic=topic, ok=True, requests=2, bytes_written=500))

    learner.run_cycle("one")
    learner.run_cycle("two")

    assert learner.ledger.cycles == 2
    assert learner.ledger.requests == 4
    assert learner.ledger.bytes_written == 1000


def test_fresh_verified_knowledge_skips_duplicate_web_work(tmp_path):
    from types import SimpleNamespace

    class Registry:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("fresh learned knowledge should prevent a web tool call")

    recalled = SimpleNamespace(
        complete=True, stop_reason="fresh_local_knowledge",
        confidence=.91, sources=[object(), object()],
    )
    runtime = SimpleNamespace(
        registry=Registry(), research=SimpleNamespace(recall_verified=lambda _topic: recalled),
    )
    result = ContinuousLearner(runtime=runtime, root=tmp_path)._default_cycle(
        "prompt injection defenses for tool using agents"
    )
    assert result.action == "reuse"
    assert result.ok and result.requests == 0 and result.bytes_written == 0
    assert result.confidence == .91 and result.sources == 2


def test_the_daily_ledger_is_keyed_by_date_not_by_uptime(tmp_path):
    """A crash loop must not spend the daily budget many times over."""
    learner = _learner(tmp_path)
    learner.ledger.bytes_written = 999
    learner.ledger.day = "1999-01-01"
    learner.ledger.roll()
    assert learner.ledger.day == date.today().isoformat()
    assert learner.ledger.bytes_written == 0


def test_spending_survives_a_process_restart(tmp_path):
    first = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="index", topic=topic, ok=True, requests=3, bytes_written=700))
    first.run_cycle("a topic")
    second = _learner(tmp_path)
    assert second.ledger.requests == 3
    assert second.ledger.bytes_written == 700


def test_default_cycle_uses_verified_research_and_memory_not_search_only(tmp_path):
    calls = []

    class Registry:
        def execute(self, name, arguments):
            calls.append((name, arguments))
            return type("Result", (), {"success": True, "error": "", "output": {
                "complete": True, "answer": "Stored evidence", "sources": [{"url": "https://example.com"}],
                "citations": ["https://example.com"],
                "metrics": {"searches": 1, "pages_retrieved": 1, "claims_supported": 1},
                "stop_reason": "sufficient_evidence",
            }})()

    learner = ContinuousLearner(
        runtime=type("Runtime", (), {"registry": Registry()})(), root=tmp_path,
        research_level="deep",
    )
    result = learner._default_cycle("GPU scheduling")
    assert calls[0][0] == "research_topic"
    assert calls[0][1]["remember"] is True
    assert calls[0][1]["level"] == "deep"
    assert result.action == "index" and result.ok


def test_meaningful_questions_enter_a_durable_learning_queue(tmp_path):
    first = _learner(tmp_path)
    assert first.observe_request("Why does NCCL performance depend on GPU topology?", "QUESTION")
    assert not first.observe_request("hello", "CONVERSATION")
    assert not first.observe_request(r"Why does my project at D:\Private\Atlas fail?", "QUESTION")
    assert not first.observe_request("Why is password=correct-horse-battery-staple rejected?", "QUESTION")
    assert not first.observe_request("Diagnose this internal application traceback", "DIAGNOSTIC")
    second = _learner(tmp_path)
    assert second.next_topic() == "Why does NCCL performance depend on GPU topology?"
    assert second.queue_status()["counts"]["learning"] == 1


def test_one_safe_question_queues_deeper_expected_followups(tmp_path):
    learner = _learner(tmp_path, anticipate_followups=True, anticipated_per_request=2)
    assert learner.observe_request(
        "Why does NCCL performance depend on GPU topology?", "QUESTION"
    )
    status = learner.queue_status(10)
    assert status["counts"]["pending"] == 3
    predicted = [row for row in status["topics"] if row["source"] == "anticipated_follow_up"]
    assert len(predicted) == 2
    assert all("NCCL" in row["topic"] and "GPU" in row["topic"] for row in predicted)


def test_prediction_facets_are_ranked_by_verified_match_feedback(tmp_path):
    learner = _learner(tmp_path, anticipate_followups=True, anticipated_per_request=2)
    topic = "How does PostgreSQL B-tree index performance work?"
    for question in anticipated_questions(topic, limit=3):
        learner.enqueue(question, source="anticipated_follow_up", priority=40)
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='learned',learned_at='2026-08-20T00:00:00+00:00'")
        db.execute("UPDATE learning_queue SET match_count=8 WHERE topic LIKE '%work internally%'")
        db.execute("UPDATE learning_queue SET mismatch_count=8 WHERE topic LIKE '%failure modes%'")
    ranked = learner._ranked_anticipated_questions(topic, limit=2)
    assert "work internally" in ranked[0]
    metrics = learner.queue_status()["predictions"]
    assert metrics["matched"] == 8 and metrics["different_question"] == 8
    assert metrics["ranking"] == "verified_match_feedback"


def test_prediction_feedback_is_scoped_to_the_subject(tmp_path):
    learner = _learner(tmp_path, anticipate_followups=True)
    postgres = "How does PostgreSQL B-tree index performance work?"
    unrelated = "How does Rust borrowing prevent data races?"
    for question in anticipated_questions(unrelated, limit=3):
        learner.enqueue(question, source="anticipated_follow_up", priority=40)
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='learned',learned_at='2026-08-20T00:00:00+00:00'")
        db.execute("UPDATE learning_queue SET match_count=100 WHERE topic LIKE '%work internally%'")
    # Rust usage should not make HAYAT favor the mechanism facet for Postgres.
    assert learner._ranked_anticipated_questions(postgres, limit=1) == anticipated_questions(postgres, limit=1)


def test_private_or_personal_questions_never_generate_predictions():
    assert anticipated_questions("How does my private project at D:\\Atlas work?") == []


def test_learned_prediction_is_reused_only_for_the_same_question_facet(tmp_path):
    learner = _learner(tmp_path, anticipate_followups=True)
    learner.observe_request("Explain how SQLite WAL durability works", "QUESTION")
    with learner._queue() as db:
        db.execute(
            "UPDATE learning_queue SET status='learned',learned_at='2026-08-20T00:00:00+00:00' "
            "WHERE source='anticipated_follow_up'"
        )
    same = learner.anticipated_match(
        "Which failures happen in SQLite WAL durability and how are they diagnosed?"
    )
    different = learner.anticipated_match(
        "What security attacks affect SQLite WAL durability?"
    )
    assert same and same["matched"] is True
    assert different and different["matched"] is False
    assert different["related_but_different"] is True


def test_enqueue_wakes_the_sleeping_learning_loop(tmp_path):
    learner = _learner(tmp_path)
    assert not learner._wake.is_set()
    learner.enqueue("SQLite WAL checkpoint behavior under sustained writes")
    assert learner._wake.is_set()


def test_idle_reflection_uses_real_conflicts_and_stale_entries_without_a_model():
    from types import SimpleNamespace

    class Knowledge:
        def conflicts(self, **kwargs):
            return [{"claim_text": "Widget X supports mode A",
                     "conflicting_text": "Widget X does not support mode A"}]

        def stale_entries(self, limit):
            return [{"query": "Widget X release support"}]

    runtime = SimpleNamespace(research=SimpleNamespace(knowledge=Knowledge()))
    topics = reflection_topics(runtime, limit=2)
    assert topics[0][1:] == ("reflection_conflict", 65)
    assert "versus" in topics[0][0]
    assert topics[1][1:] == ("reflection_stale", 30)


def test_successful_learning_marks_the_queued_topic_learned(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="index", topic=topic, ok=True, requests=2, bytes_written=100,
    ))
    learner.enqueue("How SQLite WAL provides durable local learning", priority=90)
    topic = learner.next_topic()
    learner.run_cycle(topic)
    assert learner.queue_status()["counts"]["learned"] == 1


def test_failed_learning_retries_but_does_not_loop_forever(tmp_path):
    learner = _learner(tmp_path, retry_delay_seconds=0, cycle=lambda topic: CycleResult(
        action="index", topic=topic, ok=False, detail="network unavailable",
    ))
    learner.enqueue("A difficult research topic")
    for _ in range(3):
        learner.run_cycle(learner.next_topic())
    status = learner.queue_status()
    assert status["counts"]["failed"] == 1
    assert learner.next_topic() == ""
    assert learner.retry("A difficult research topic")
    assert learner.next_topic() == "A difficult research topic"


# ------------------------------------------------------- what a cycle may do


def test_an_action_outside_the_permitted_verbs_is_refused(tmp_path):
    """Even inside its read-only authority, it does one kind of thing."""
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="delete_everything", topic=topic, ok=True))

    result = learner.run_cycle("x")

    assert not result.ok
    assert result.action == "refused"
    assert "not a permitted background action" in result.detail


@pytest.mark.parametrize("action", sorted(PERMITTED_ACTIONS))
def test_permitted_verbs_pass_through(action, tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(action=action, ok=True))
    assert learner.run_cycle("x").ok


def test_a_cycle_that_raises_does_not_kill_the_loop(tmp_path):
    def explode(topic):
        raise RuntimeError("network died")

    learner = _learner(tmp_path, cycle=explode)
    result = learner.run_cycle("x")

    assert not result.ok
    assert "RuntimeError" in result.detail
    assert learner.ledger.cycles == 1, "a failed cycle still counts against the budget"


# ------------------------------------------------------------- reviewability


def test_every_cycle_is_journalled_for_review(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="search", topic=topic, ok=True, requests=1))
    learner.run_cycle("quantum computing")
    learner.run_cycle("rust ownership")

    path = tmp_path / f"journal-{date.today().isoformat()}.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(lines) == 2
    assert {item["topic"] for item in lines} == {"quantum computing", "rust ownership"}


def test_the_daily_report_says_what_it_actually_did(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="search", topic=topic, ok=True, requests=1, bytes_written=100))
    for topic in ("alpha", "beta", "gamma"):
        learner.run_cycle(topic)

    report = learner.daily_report()

    assert report["cycles"] == 3
    assert report["succeeded"] == 3 and report["failed"] == 0
    assert report["requests"] == 3
    assert report["bytes_written"] == 300
    assert set(report["topics"]) == {"alpha", "beta", "gamma"}
    assert report["actions"]["search"] == 3


def test_the_report_is_honest_about_failures(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(
        action="search", topic=topic, ok=False, detail="refused"))
    learner.run_cycle("x")
    report = learner.daily_report()
    assert report["failed"] == 1 and report["succeeded"] == 0


def test_status_states_the_containment_plainly(tmp_path):
    status = _learner(tmp_path).status()
    assert status["principal"] == "BACKGROUND_LEARNER"
    assert set(status["permitted_actions"]) == set(PERMITTED_ACTIONS)
    assert "budget" in status and "spent" in status


# ----------------------------------------------------------------- the loop


def test_the_loop_starts_once_and_stops_cleanly(tmp_path):
    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(action="search", ok=True))

    assert learner.start(topics=lambda: "topic", interval=0.05)
    assert not learner.start(topics=lambda: "topic"), "must not start twice"
    assert learner.running

    assert learner.stop(timeout=10)
    assert not learner.running


def test_the_loop_exits_when_the_halt_file_appears(tmp_path):
    import time

    learner = _learner(tmp_path, cycle=lambda topic: CycleResult(action="search", ok=True))
    learner.start(topics=lambda: "topic", interval=0.05)
    try:
        learner.write_stop_file("halt now")
        deadline = time.monotonic() + 10
        while learner.running and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not learner.running, "the halt file must end the loop, not pause it"
    finally:
        learner.stop(timeout=10)


def test_the_loop_exits_when_the_budget_is_spent(tmp_path):
    import time

    learner = _learner(tmp_path, budget=LearningBudget(max_cycles_per_hour=2),
                       cycle=lambda topic: CycleResult(action="search", ok=True))
    learner.start(topics=lambda: "topic", interval=0.02)
    try:
        deadline = time.monotonic() + 10
        while learner.running and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not learner.running, "a spent budget must stop the loop, not slow it"
        assert "ceiling" in (learner.stopped_because or "")
    finally:
        learner.stop(timeout=10)


# ------------------------------------------------- surviving a broken queue


def test_a_corrupt_queue_is_quarantined_and_rebuilt(tmp_path):
    """Found in the wild: the queue reported "database disk image is
    malformed" and the learner could not start at all. The topics in it are
    things HAYAT meant to read about later -- losing them costs nothing that
    cannot be relearned, and keeping the broken file costs the learner
    entirely. It is renamed rather than deleted so it can still be examined.
    """
    from app.learning.continuous import ContinuousLearner

    broken = tmp_path / "learning.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4096)

    learner = ContinuousLearner(root=tmp_path)

    assert learner.queue_status(5)["counts"] == {}
    quarantined = list(tmp_path.glob("learning.corrupt.*.db"))
    assert quarantined, "the broken file should have been kept for inspection"


def test_the_queue_uses_wal_so_two_writers_do_not_corrupt_it(tmp_path):
    """The learner writes from its background thread while a turn enqueues
    from the main one. Default journalling plus no busy timeout is how the
    file became malformed in the first place."""
    import sqlite3

    from app.learning.continuous import ContinuousLearner

    learner = ContinuousLearner(root=tmp_path)
    connection = sqlite3.connect(learner.queue_path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        connection.close()


def test_queue_connections_are_actually_closed(tmp_path):
    """`with sqlite3.connect(...)` commits but does not close, so every call
    leaked a handle -- and on Windows an open handle is also why the corrupt
    file could not be renamed out of the way."""
    from app.learning.continuous import ContinuousLearner

    learner = ContinuousLearner(root=tmp_path)
    for index in range(30):
        learner.enqueue(f"a topic worth reading about number {index}")

    # If handles leaked, replacing the file would fail on Windows.
    replacement = learner.queue_path.with_suffix(".moved")
    learner.queue_path.rename(replacement)
    replacement.rename(learner.queue_path)


def test_a_healthy_queue_is_left_alone(tmp_path):
    from app.learning.continuous import ContinuousLearner

    first = ContinuousLearner(root=tmp_path)
    first.enqueue("something worth remembering here")
    before = first.queue_status(5)["counts"]

    second = ContinuousLearner(root=tmp_path)

    assert second.queue_status(5)["counts"] == before
    assert not list(tmp_path.glob("learning.corrupt.*.db"))


# ------------------------------------- what may be typed into a search engine


@pytest.mark.parametrize(
    "private",
    [
        # All of these were genuinely sitting in the queue, bound for a public
        # search engine, when this was found.
        "how do my face look rate me score me",
        "ok how can style myself to groom and look better",
        "ok so are u hayat or qwen?",
        "great but why wont u?",
        "how is everything going",
        "work on your toon and make more nature and u learn humaized language",
        "ok understand all the chat that we had cause u misunderstand me",
    ],
)
def test_private_and_conversational_text_never_becomes_a_search(private):
    """The learner types its topics into a public search engine, so the only
    things that belong in the queue are things Mohsin would not mind a
    stranger reading. Remarks addressed to HAYAT, questions about his own
    body and life, and fragments carrying no question are none of them."""
    from app.learning.continuous import _is_about_the_world

    assert not _is_about_the_world(private)


@pytest.mark.parametrize(
    "topic",
    [
        "prompt-injection defenses for tool-using local agents",
        "how does rust ownership and borrowing work",
        "what is the difference between WAL and rollback journal in sqlite",
        "explain transformer attention mechanisms",
    ],
)
def test_genuine_questions_about_the_world_are_still_queued(topic):
    """Blocking private text must not block the learner from learning."""
    from app.learning.continuous import _is_about_the_world

    assert _is_about_the_world(topic)


def test_a_topic_needs_more_than_filler_words():
    from app.learning.continuous import _is_about_the_world

    assert not _is_about_the_world("so how is it going")
    assert not _is_about_the_world("ok thanks")


# ------------------------------------------- learning is not the same as research


@pytest.mark.parametrize("trivial", [
    # Really researched, on the web, at 0.58 confidence -- having known the
    # answer the whole time.
    "what is 2 plus 2",
    "2+2",
    "who is the CEO of nvidia",
    "how many bytes in a gigabyte",
    "when was python released",
])
def test_a_question_it_can_already_answer_is_not_a_research_topic(trivial):
    """Research goes to the web because the answer is not already known.
    Learning is for subjects worth getting deeper on. Nothing enforced the
    difference, so a network round trip and a knowledge entry were spent on
    arithmetic."""
    from app.learning.continuous import _is_about_the_world

    assert not _is_about_the_world(trivial)


@pytest.mark.parametrize("topic", [
    "prompt-injection defenses for tool-using local agents",
    "explain how rust ownership and borrowing work together",
    "local LLM routing load shedding and VRAM management",
])
def test_subjects_worth_studying_still_queue(topic):
    from app.learning.continuous import _is_about_the_world

    assert _is_about_the_world(topic)


# ---------------------------------- studying what he actually keeps asking about


def _conversation(memory, messages):
    for index, text in enumerate(messages):
        memory.record_message("session-test", "user", text)


def test_it_notices_what_he_keeps_coming_back_to(tmp_path):
    """The learner only ever pursued his literal questions, so it answered
    what was asked and then had nothing of its own to study."""
    from app.learning.continuous import interests
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    _conversation(memory, [
        "explain how rust ownership and borrowing work together",
        "what happens to rust ownership when a value moves",
        "show me rust ownership with an example",
        "how do I file a tax return in the UK",
    ])

    found = dict(interests(memory))
    assert "rust ownership" in found
    assert found["rust ownership"] >= 2


def test_the_follow_ups_are_about_his_subjects_not_random_ones(tmp_path):
    from app.learning.continuous import follow_up_topics
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    _conversation(memory, [
        "explain how rust ownership and borrowing work together",
        "what happens to rust ownership when a value moves",
        "give me a rust ownership example please",
    ])

    topics = follow_up_topics(memory, limit=2)
    assert topics, "nothing to study despite a clear repeated subject"
    assert all("rust ownership" in topic for topic in topics)
    # Depth, not breadth: how it works and where it goes wrong.
    assert any(word in " ".join(topics)
               for word in ("mechanism", "mistakes", "trade-offs", "practices"))


def test_subjects_are_phrases_because_single_words_are_not_subjects(tmp_path):
    """Single words produced follow-ups like "how people actually works",
    which is neither a subject nor a sentence."""
    from app.learning.continuous import interests
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    _conversation(memory, ["explain how tax returns are filed and assessed"] * 3)

    for subject, _count in interests(memory):
        assert " " in subject, f"{subject!r} is one word"


def test_an_empty_history_asks_for_nothing(tmp_path):
    from app.learning.continuous import follow_up_topics
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    assert follow_up_topics(memory) == []


# ------------------------------------------------------------ pause vs. stop


def test_pausing_is_not_stopping_and_the_learner_comes_back(tmp_path):
    """A learner that steps aside for one answer has to return on its own.

    `request_stop` was the only lever, and nothing clears it -- so standing
    aside for a question ended background learning for the rest of the run.
    """
    learner = _learner(tmp_path)
    assert learner.may_run()[0] is True

    learner.pause("answering your question")
    allowed, reason = learner.may_run()
    assert allowed is False
    assert reason == "answering your question"
    assert learner.paused is True
    assert learner.status()["paused"] is True

    learner.resume()
    assert learner.paused is False
    assert learner.may_run()[0] is True


def test_a_stop_still_survives_a_resume(tmp_path):
    """Resuming must not quietly undo the kill switch."""
    learner = _learner(tmp_path)
    learner.request_stop("stopped by the owner")
    learner.resume()
    allowed, reason = learner.may_run()
    assert allowed is False
    assert reason == "stopped by the owner"


def test_the_stop_file_still_wins_over_a_resume(tmp_path):
    learner = _learner(tmp_path)
    learner.write_stop_file("halted")
    learner.resume()
    assert learner.may_run()[0] is False
    learner.clear_stop_file()
    assert learner.may_run()[0] is True


def test_typing_alone_no_longer_counts_as_the_machine_being_busy(tmp_path, monkeypatch):
    """Keyboard activity is not resource contention.

    The idle rule was the first and strongest test in machine_is_busy, so a
    two-minute stillness requirement kept the learner from running during
    every session Mohsin was actually present for -- reported to him as "the
    machine is busy with your work" while the CPU sat idle.
    """
    import app.learning.continuous as continuous

    learner = _learner(tmp_path)
    monkeypatch.setattr(continuous, "_windows_idle_seconds", lambda: 0.0)
    monkeypatch.setattr(continuous, "_windows_cpu_percent", lambda: 5.0)
    # The machine's real free RAM is not part of this question: with the brain
    # loaded it dropped under 2 GB during a full run and the test failed.
    import app.hardware as hardware
    from types import SimpleNamespace
    monkeypatch.setattr(hardware, "profile", lambda refresh=False: SimpleNamespace(available_ram_bytes=16 * 1024**3))
    assert learner.machine_is_busy() is False

    # Real contention still yields: for CPU, and for memory.
    monkeypatch.setattr(continuous, "_windows_cpu_percent", lambda: 99.0)
    assert learner.machine_is_busy() is True
    monkeypatch.setattr(continuous, "_windows_cpu_percent", lambda: 5.0)
    monkeypatch.setattr(hardware, "profile", lambda refresh=False: SimpleNamespace(available_ram_bytes=1024**3))
    assert learner.machine_is_busy() is True


def test_his_own_self_improvement_is_not_a_public_search_topic():
    """"What would make me better" went to DuckDuckGo.

    It came back with life-coaching articles -- "striving for something can
    make you a happier and better person" -- which entered the knowledge store
    as claims. His own life is the category this filter exists to exclude, and
    it only knew about faces, salaries and relatives.
    """
    from app.learning.continuous import _is_about_the_world, anticipated_questions

    for personal in ("what would make me better",
                     "how can i be a better person",
                     "how do i improve myself",
                     "what should i change about myself"):
        assert not _is_about_the_world(personal), personal
        assert anticipated_questions(personal) == [], personal


def test_a_technical_question_phrased_with_i_is_still_researched():
    """The object decides, not the pronoun.

    "how can i improve query performance in postgres" is a question about
    postgres. Excluding it because it starts "how can i improve" would be the
    same error in the other direction.
    """
    from app.learning.continuous import _is_about_the_world, anticipated_questions

    for technical in ("how can i improve query performance in postgres",
                      "how does rust borrowing prevent data races",
                      "what is the difference between a tax deduction and a tax credit"):
        assert _is_about_the_world(technical), technical
        assert anticipated_questions(technical), technical


def test_a_derived_topic_is_never_built_out_of_pronouns():
    """The queue held "the best practices ... for me better?".

    Pronouns survived into the derived subject, so the template produced a
    question that is not about anything and queued it for research.
    """
    from app.learning.continuous import anticipated_questions

    for derived in anticipated_questions("how do b-tree indexes work in postgres"):
        subject = derived.lower()
        for pronoun in (" for me ", " for my ", " for you ", " for it ", " for them "):
            assert pronoun not in subject, derived


def test_a_stale_topic_already_learned_can_be_queued_again_for_refresh(tmp_path):
    """Every stale topic had been learned once, and enqueue refused learned
    topics, so the stale refresh reported 'queued 0' and refreshed nothing."""
    learner = ContinuousLearner(root=tmp_path)
    assert learner.enqueue("python 3.13 release date")
    with learner._queue() as db:
        db.execute("UPDATE learning_queue SET status='learned'")
    assert not learner.enqueue("python 3.13 release date"), "an ordinary request does not relearn"
    assert learner.enqueue("python 3.13 release date", source="stale_refresh", relearn=True)
    with learner._queue() as db:
        assert db.execute("SELECT status FROM learning_queue").fetchone()[0] == "pending"


def test_a_failed_local_command_is_not_researched_on_the_web():
    """A failed "read config/settings.yaml" was queued as an unfinished task,
    searched on the web, and filed nineteen claims about Kubernetes config
    under that "topic". Commands to this machine are not topics."""
    from app.learning.continuous import _is_about_the_world, unresearchable_reason

    for command in ("read config/settings.yaml", "list the files in nowhere/at/all",
                    "open notepad and type hello", "create a folder called research",
                    "can you list the files in tests", "run scripts/doctor.py now please"):
        assert not _is_about_the_world(command), command
        assert unresearchable_reason(command) is not None, command
    for topic in ("how does kubernetes schedule gpu workloads",
                  "what causes inflation in developing economies",
                  "explain how tls handshakes work in detail"):
        assert _is_about_the_world(topic), topic


def test_follow_up_templates_are_not_wrapped_around_lookups_requests_or_life_goals():
    """The queue held "What are the common failure modes and diagnostic
    signals for latest stable version python?", "... for find tutorials rust
    async?" and "... for MONEY REACH BILLIONS DOLLAR STARTING?"."""
    from app.learning.continuous import anticipated_questions, unresearchable_reason

    for request in ("what is the latest stable version of python", "find me tutorials on rust async",
                    "HOW CAN I MAKE MONEY AND REACH TO BILLIONS OF DOLLAR STARTING FROM 0",
                    "show me the weather in fremont", "what is the price of an rtx 5090"):
        assert anticipated_questions(request) == [], request
    # Concepts still get their deeper follow-ups.
    assert anticipated_questions("how does rust borrowing prevent data races")
    # Junk already stored is retired at refresh rather than researched again.
    for stored in ("What are the common failure modes and diagnostic signals for latest stable version python?",
                   "What are the best practices, limitations, and trade-offs for find tutorials rust async?"):
        assert unresearchable_reason(stored) == "a follow-up template wrapped around a lookup, not a topic"
    # A real subject is not caught by this rule (other gates may still judge it).
    assert unresearchable_reason(
        "What are the best practices, limitations, and trade-offs for postgres btree indexes?"
    ) != "a follow-up template wrapped around a lookup, not a topic"
def test_referential_chat_followup_is_not_queued_as_public_research():
    from app.learning.continuous import _is_about_the_world

    for text in ("explain me about that", "tell me more about it", "so what about this?"):
        assert not _is_about_the_world(text), text
