from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from app.jobs.career import canonical_skills
from app.jobs.outcomes import observations, outcome_summary, predictive_counts
from app.jobs.tools import job_request, report_lines
from tests.test_job_hunting_upgrade import hunting as hunting
from tests.test_jobs import posting


NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def application(index, *, days=30, status="applied", response_day=None):
    stamp = NOW - timedelta(days=days)
    events = [{"status": "applied", "at": stamp.isoformat()}]
    if response_day is not None:
        events.append({"status": "interview", "at": (stamp + timedelta(days=response_day)).isoformat()})
    return {"number": index, "status": status, "applied_at": stamp.isoformat(), "events": events}


def test_outcomes_retain_interviews_after_rejection_and_withdrawal():
    rows = [application(1, status="rejected", response_day=5), application(2, status="withdrawn", response_day=20)]
    result = outcome_summary(rows, NOW)
    assert result["submitted"] == 2 and result["interview_reached"] == 2
    assert result["matured"] == 2 and result["within_window"] == 1
    assert result["forecast"] is None
    assert observations([{"status": "withdrawn"}]) == []


def test_forecast_excludes_recent_and_unknown_timing_even_if_they_replied():
    rows = [application(i, status="interview" if i < 4 else "applied", response_day=3 if i < 4 else None) for i in range(10)]
    rows += [application(11, days=2, status="interview", response_day=1),
             {"status": "interview", "score": 90}, application(13, days=-2)]
    result = outcome_summary(rows, NOW)
    assert result["matured"] == 10 and result["within_window"] == 4
    assert result["recent_excluded"] == 1 and result["unknown_timing_excluded"] == 2
    assert result["forecast"]["model"] == "beta-binomial"
    assert result["forecast"]["expected_interview_reaching"] == pytest.approx(4.17)
    assert "not independently calibrated" in result["assumptions"]


def test_predictive_distribution_matches_uniform_prior_and_analytic_mean():
    prior = predictive_counts(0, 0)
    assert prior["probabilities"] == pytest.approx([1 / 11] * 11)
    result = predictive_counts(3, 10)
    probabilities = result["probabilities"]
    assert sum(probabilities) == pytest.approx(1)
    assert sum(k * probability for k, probability in enumerate(probabilities)) == pytest.approx(10 * 4 / 12)
    low, high = result["predictive_interval"]
    assert sum(probabilities[low:high + 1]) >= .9
    symmetric = predictive_counts(5, 10)["probabilities"]
    assert symmetric == pytest.approx(list(reversed(symmetric)))


def test_predictive_distribution_matches_independent_scipy_calculation():
    betabinom = pytest.importorskip("scipy.stats").betabinom
    for successes, trials, size in ((0, 10, 10), (3, 10, 10), (10, 10, 20), (70, 100, 50)):
        result = predictive_counts(successes, trials, size)
        expected = betabinom.pmf(list(range(size + 1)), size, successes + 1, trials - successes + 1)
        assert result["probabilities"] == pytest.approx(expected, abs=1e-11)


def test_original_application_table_migrates_without_losing_records(tmp_path):
    import sqlite3
    from app.jobs.store import JobStore
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE applications(number INTEGER PRIMARY KEY,status TEXT NOT NULL,folder TEXT,created_at TEXT NOT NULL,"
                   "updated_at TEXT NOT NULL,applied_at TEXT,channel TEXT,follow_up_at TEXT,followed_up INTEGER NOT NULL DEFAULT 0,notes TEXT NOT NULL DEFAULT '')")
        db.execute("INSERT INTO applications(number,status,created_at,updated_at,notes) VALUES(9,'applied','2026-10-01','2026-10-01','keep this note')")
    store = JobStore(path)
    row = store.application(9)
    assert row["notes"] == "keep this note" and row["status"] == "applied"
    assert row["score_at_application"] is None and row["verdict_at_application"] is None


@pytest.mark.parametrize("args", [(-1, 5), (6, 5), (1.5, 5), (True, 5), (1, 5, 51)])
def test_invalid_observations_cannot_generate_a_forecast(args):
    with pytest.raises(ValueError):
        predictive_counts(*args)


def test_migration_and_submission_snapshot_preserve_original_score(hunting):
    number = hunting.store.upsert([posting()])[0]
    hunting.rescore()
    original = hunting.store.job(number)["score"]
    hunting.store.set_status(number, "applied")
    hunting.store.set_status(number, "interview")
    hunting.store.set_status(number, "rejected")
    hunting.set_preferences({"target_titles": ["Gardener"]})
    assert hunting.store.application(number)["score_at_application"] == original
    events = hunting.store.applications()[0]["events"]
    assert [event["status"] for event in events] == ["applied", "interview", "rejected"]
    assert hunting.prospects()["replies"] == 1
    assert hunting.prospects()["by_strength"]["strong_fit"] is not None


def test_employer_rejection_does_not_erase_the_owners_preference_signal(hunting):
    numbers = hunting.store.upsert([posting(key=f"remoteok:pursued-{index}") for index in range(3)])
    hunting.rescore()
    for number in numbers:
        hunting.store.set_status(number, "applied")
    before = hunting.learned()
    for number in numbers:
        hunting.store.set_status(number, "rejected")
    assert hunting.learned() == before


def test_legacy_applied_rows_are_not_backfilled_with_current_scores(hunting):
    from app.jobs.store import JobStore
    number = hunting.store.upsert([posting()])[0]
    hunting.rescore()
    with hunting.store._db() as db:
        db.execute("INSERT INTO applications(number,status,created_at,updated_at,applied_at) VALUES(?, 'applied', ?, ?, ?)",
                   (number, NOW.isoformat(), NOW.isoformat(), NOW.isoformat()))
    reopened = JobStore(hunting.store.path)
    reopened.set_status(number, "applied")
    assert reopened.application(number)["score_at_application"] is None


def test_skill_scenario_recomputes_scores_without_mutating_profile_or_matches(hunting):
    number = hunting.store.upsert([posting(description="Requirements\nSQL and Docker required.\nRemote position.")])[0]
    hunting.rescore()
    before_profile = hunting.profile.path.read_bytes()
    before_match = json.dumps(hunting.store.job(number)["match"], sort_keys=True)
    result = hunting.career_radar(["docker"], 5)
    row = next(row for row in result["opportunities"] if row["number"] == number)
    assert result["scenario"]["skills"] == ["Docker"]
    assert row["scenario_score"] > row["score"]
    assert "Docker" not in row["remaining_required"]
    assert "Docker" in row["requirements"][0]["posting_excerpt"]
    assert hunting.profile.path.read_bytes() == before_profile
    assert json.dumps(hunting.store.job(number)["match"], sort_keys=True) == before_match
    assert sum(item["minutes_allocated"] for item in result["practice_sprint"]) <= 5 * 60
    assert "mastery" in result["practice_sprint"][0]["completion"]
    assert result["outcomes"]["forecast"] is None
    assert "Career radar" in "\n".join(report_lines(result))


def test_requirement_excerpt_comes_from_requirements_instead_of_first_mention():
    from app.jobs.career import requirement_evidence
    row = {"description": "What you'll do\nOwn Docker infrastructure.\nRequirements\nHands-on Docker experience required.\nNice to have\nDocker certifications."}
    evidence = requirement_evidence(row, ["Docker"])[0]
    assert evidence["posting_excerpt"] == "Hands-on Docker experience required."
    assert evidence["section"] == "required"


def test_learning_a_tool_does_not_remove_country_restrictions(hunting):
    number = hunting.store.upsert([posting(location="Germany", description="Germany residents only.\nRequirements\nDocker required.")])[0]
    hunting.rescore()
    row = hunting.career_radar(["Docker"])["opportunities"][0]
    assert row["number"] == number and row["blocked"]
    assert row["scenario_score"] <= 25
    assert row["action"] == "resolve eligibility"


@pytest.mark.parametrize("hours", [0, 41, math.nan, math.inf, True])
def test_invalid_practice_budget_is_rejected_without_profile_change(hunting, hours):
    before = hunting.profile.path.read_bytes()
    with pytest.raises(ValueError, match="budget"):
        hunting.career_radar(weekly_hours=hours)
    assert hunting.profile.path.read_bytes() == before


def test_empty_and_cancelled_radar_are_explicit(hunting):
    result = hunting.career_radar()
    assert result["coverage"]["jobs_considered"] == 0
    assert any("Search" in action for action in result["next_actions"])
    hunting.store.upsert([posting()])
    hunting.rescore()
    with pytest.raises(RuntimeError, match="cancelled"):
        hunting.career_radar(check=lambda: False)


def test_canonical_skills_and_chat_routes():
    assert canonical_skills(["docker", "postgres", "Docker"]) == ["Docker", "PostgreSQL"]
    with pytest.raises(ValueError, match="Unknown"):
        canonical_skills(["instant superintelligence"])
    assert job_request("show my career radar")["tool"] == "job_career_radar"
    assert job_request("what if I learn Docker and Kubernetes for jobs?")["arguments"]["proposed_skills"] == ["Docker", "Kubernetes"]


def test_weak_role_with_no_recognised_skill_gap_is_not_called_strong(hunting):
    hunting.store.upsert([posting(title="Principal Quantum Architect", description="Requirements\n40 years of experience required.")])
    hunting.rescore()
    plan = hunting.learning_plan()
    assert plan["readiness"]["strong_now"] == 0


def test_future_and_naive_posting_timestamps_are_readable(hunting):
    number = hunting.store.upsert([posting(posted_at="2099-01-01T00:00:00")])[0]
    hunting.rescore()
    reason = hunting.store.job(number)["match"]["parts"]["freshness"]["reason"]
    assert "future posting date" in reason


def test_cooldowns_compare_real_instants(hunting, monkeypatch):
    import app.jobs.store as module
    monkeypatch.setattr(module, "now_iso", lambda: "2026-10-30T12:00:00+00:00")
    hunting.store.defer_source("himalayas", "2026-10-30T10:00:00-03:00", "rate_limited")
    hunting.store.defer_source("himalayas", "2026-10-30T13:00:00+02:00", "rate_limited")
    assert hunting.store.source_cooldowns()["himalayas"]["retry_at"] == "2026-10-30T13:00:00+00:00"


def test_publisher_expiry_is_retained_and_cannot_be_undone_by_a_skill_scenario(hunting):
    from app.jobs.sources import himalayas_rows
    rows = himalayas_rows({"jobs": [{"guid": "expiry-test", "applicationLink": "https://himalayas.app/jobs/test",
        "title": "Data Analyst", "companyName": "Sample", "description": "Requirements\nDocker required.",
        "expiryDate": 946684800}]})
    assert rows[0]["expires_at"].startswith("2000-01-01")
    number = hunting.store.upsert(rows)[0]
    hunting.rescore()
    assert hunting.store.job(number)["verdict"] == "not eligible"
    row = hunting.career_radar(["Docker"])["opportunities"][0]
    assert row["scenario_score"] <= 25 and any("expiry" in reason for reason in row["blocked"])
