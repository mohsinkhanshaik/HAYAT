"""Retention practice, the scorecard and its keep-only-gains gate, the forecast ledger, and learning limits."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from app.cognitive_scorecard import BASELINE, Scorecard, benchmarks, compare, load_baseline, mind_request
from app.forecasts import ForecastLedger, check_date
from app.memory.database import MemoryDatabase
from app.memory.retention import RetentionPractice, cue_for

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _memory(tmp_path):
    memory = MemoryDatabase(tmp_path / "memory" / "agent.db")
    memory.initialize()
    return memory


# ---------------------------------------------------------------------------------------------- retention

def test_a_cue_is_the_rarest_words_in_their_own_order():
    from collections import Counter

    frequency = Counter({"birthday": 1, "sister": 2, "amina": 1, "on": 50, "may": 3})
    assert cue_for("My sister Amina's birthday is on 12 May.", frequency, 100, size=2) == "amina's birthday"


def test_recalled_memories_wait_longer_and_missed_ones_come_back_tomorrow(tmp_path):
    memory = _memory(tmp_path)
    kept = memory.store_memory("semantic", "Canberra is the capital city of Australia.", 0.7, "test")
    for index in range(15):
        memory.store_memory("episodic", f"Opened window number {index} and closed it again.", 0.4, "test")
    practice = RetentionPractice(memory, tmp_path / "memory" / "retention.db")
    first = practice.review(40, now=NOW)
    assert first["reviewed"] >= 1
    with practice._db() as db:
        row = db.execute("SELECT * FROM schedule WHERE memory_id=?", (kept,)).fetchone()
    assert row["recalled"] == 1 and row["due_at"].startswith((NOW + timedelta(days=1)).date().isoformat())
    practice.review(40, now=NOW + timedelta(days=2))  # due again: recalled, so the step grows to 3 days
    with practice._db() as db:
        row = db.execute("SELECT * FROM schedule WHERE memory_id=?", (kept,)).fetchone()
    assert row["step"] == 1 and row["reviews"] == 2
    report = practice.report()
    assert report["reviews"] >= 2 and 0 < report["retention"] <= 1 and report["mean_reciprocal_rank"] > 0


def test_forgetting_candidates_are_listed_never_deleted(tmp_path, monkeypatch):
    memory = _memory(tmp_path)
    lost = memory.store_memory("episodic", "Something minor happened with a gadget once.", 0.3, "test")
    practice = RetentionPractice(memory, tmp_path / "memory" / "retention.db")
    monkeypatch.setattr(memory, "retrieve", lambda *args, **kwargs: [])
    for day in range(3):
        practice.review(5, now=NOW + timedelta(days=day * 2))
    assert [row["memory_id"] for row in practice.forgetting_candidates()] == [lost]
    with memory.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM memories WHERE id=?", (lost,)).fetchone()[0] == 1


# ---------------------------------------------------------------------------------------------- scorecard + gate

def test_the_gate_holds_against_the_accepted_baseline():
    """A change that makes the measured benchmarks worse than the accepted baseline fails the suite."""
    assert BASELINE.is_file(), "accept a baseline with scripts/cognitive_gate.py --accept"
    result = compare(load_baseline(), benchmarks())
    assert result["passed"], result["rows"]


def test_compare_counts_only_drops_beyond_tolerance_as_worse():
    baseline = {"fixture_recall_at_5": 0.95, "context_contamination": 0.0, "topic_relation_accuracy": 1.0}
    same = compare(baseline, {**baseline, "fixture_recall_at_5": 0.94})
    assert "fixture_recall_at_5" not in same["regressed"]
    worse = compare(baseline, {**baseline, "context_contamination": 0.1})
    assert "context_contamination" in worse["regressed"] and not worse["passed"]
    dropped = compare({**baseline, "fixture_mean_reciprocal_rank": 0.9}, baseline)
    assert "fixture_mean_reciprocal_rank" in dropped["regressed"]  # gone from the run counts as a regression
    assert {row["metric"]: row["verdict"] for row in same["rows"]}["fixture_mean_reciprocal_rank"] == "new"


def test_the_live_scorecard_reads_records_and_keeps_history(tmp_path):
    memory = _memory(tmp_path)
    rt = SimpleNamespace(memory=memory, retention=RetentionPractice(memory, tmp_path / "memory" / "retention.db"))
    card = Scorecard(lambda: rt, tmp_path / "memory" / "scorecard.db")
    first = card.measure()
    assert first["previous"] is None and "memory_store_mb" in first["metrics"]
    assert "held_out_accuracy" not in first["metrics"]  # no held-out run on record: absent, not invented
    assert card.measure()["previous"]["at"] == first["measured_at"]
    assert len(card.history("memory_store_mb")) == 2


def test_questions_about_the_measured_mind_reach_their_tools():
    assert mind_request("how accurate are your forecasts?") == "forecast_ledger"
    assert mind_request("which predictions are due") == "forecast_ledger"
    assert mind_request("how well do you remember things") == "memory_retention"
    assert mind_request("show me your scorecard") == "cognitive_scorecard"
    assert mind_request("predict the weather tomorrow") is None


# ---------------------------------------------------------------------------------------------- forecasts

def test_check_dates_follow_the_horizon_named():
    asked = date(2026, 11, 9)
    assert check_date("predict GDP for next year", asked) == date(2028, 1, 31)
    assert check_date("forecast for 2027", asked) == date(2028, 1, 31)
    assert check_date("will it rain tomorrow", asked) == date(2026, 11, 11)
    assert check_date("outlook for next month", asked) == date(2027, 1, 7)
    assert check_date("will bitcoin rise", asked) == asked + timedelta(days=90)


def test_only_real_forecasts_are_kept_and_only_owner_verdicts_score_them(tmp_path):
    ledger = ForecastLedger(tmp_path / "forecasts.db")
    long_answer = "Growth is likely to stay near 6.5% next year, with risks from oil prices and weaker exports."
    assert ledger.record("what is the capital of france", long_answer) is None
    assert ledger.record("predict india's growth next year", "I'm searching for that now and will come back.") is None
    kept = ledger.record("predict india's growth next year", long_answer, now=NOW)
    assert kept and ledger.stats()["hit_rate"] is None
    assert ledger.due(date(2027, 6, 1)) == []
    due = ledger.due(date(2028, 2, 1))
    assert [item["id"] for item in due] == [kept]
    queued = []
    assert ledger.queue_lookups(queued.append, date(2028, 2, 1)) == 1
    assert ledger.queue_lookups(queued.append, date(2028, 2, 1)) == 0  # once
    assert queued[0].startswith("what actually happened: ")
    ledger.judge(kept, "partly")
    assert ledger.stats()["hit_rate"] == 0.5 and ledger.due(date(2028, 2, 1)) == []


def test_a_delivered_search_is_filed_under_its_topic_and_its_forecast_kept(tmp_path):
    from app.topics import ConversationTopics
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.topics = ConversationTopics(tmp_path / "topics.db")
    rt.forecasts = ForecastLedger(tmp_path / "forecasts.db")
    decision = rt.topics.decide("shared-session", "predict india's GDP growth for next year")
    rt.topics.observe_reply(decision, "I'm searching for that now.", open_task="job-1", session_id="shared-session")
    job = SimpleNamespace(job_id="job-1", status="done", asked="predict india's GDP growth for next year",
                          query="india GDP growth forecast 2027")
    answer = "Forecasts from the IMF and RBI put growth near 6.4% for next year, with downside risk from exports."
    rt.assistant._file_job_result(job, answer, "research")
    assert rt.topics.messages(decision.topic_id)[-1]["text"] == answer
    assert rt.topics.topics()[0]["open_task"] == ""
    assert rt.forecasts.recent()[0]["subject"] == "india GDP growth forecast 2027"


# ---------------------------------------------------------------------------------------------- learning limits

def test_learning_waits_for_ram_and_for_a_busy_graphics_card(monkeypatch):
    from app.hardware import GPUInfo, HardwareProfile
    from app.learning.continuous import ContinuousLearner, LearningBudget

    learner = ContinuousLearner.__new__(ContinuousLearner)
    learner.budget = LearningBudget(min_free_ram_gb=2.0, min_obtainable_vram_gb=1.0)
    monkeypatch.setattr("app.learning.continuous._windows_cpu_percent", lambda: 5.0)
    gpu = GPUInfo(name="RTX 5060", total_bytes=8 * 1024**3, used_bytes=int(7.6 * 1024**3))
    busy_card = HardwareProfile(available_ram_bytes=8 * 1024**3, gpu=gpu)
    monkeypatch.setattr("app.hardware.profile", lambda refresh=False: busy_card)
    assert learner.machine_is_busy()  # 0.4 GB obtainable: an image render holds the card
    gpu.reclaimable_bytes = 6 * 1024**3
    assert not learner.machine_is_busy()  # an idle model would unload: fine
    monkeypatch.setattr("app.hardware.profile", lambda refresh=False: HardwareProfile(available_ram_bytes=1024**3, gpu=gpu))
    assert learner.machine_is_busy()  # 1 GB RAM free, under the 2 GB floor
    learner.budget = LearningBudget(min_free_ram_gb=0.5, min_obtainable_vram_gb=0)
    assert not learner.machine_is_busy()
