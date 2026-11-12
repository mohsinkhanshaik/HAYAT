"""Owner feedback must improve confidence bookkeeping without inventing accuracy."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.metacognition import AdaptiveControlPlane
from app.self_review import SelfReviewLog, level_of, lines, review


def example(score=.8, shown=None):
    item = review(request="A synthetic test question", answer_source="brain")
    item.update(score=score, level=level_of(score))
    if shown is not None:
        item.update(calibrated_score=shown, calibrated_level=level_of(shown), calibrated_on=10)
    return item


def test_unmatched_feedback_never_trains_on_another_recent_reply(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    log.record(example(), "session-a", "The reviewed answer.")
    assert not log.judge("session-a", "A different answer with no review.", -1)
    assert not log.judge("session-b", "The reviewed answer.", -1)
    assert log.assessment()["judged_reviews"] == 0
    assert log.judge("session-a", "The reviewed answer.", 1)
    # A corrected verdict replaces the old label, rather than training twice.
    assert log.judge("session-a", "The reviewed answer.", -1)
    assert log.calibration()["high"]["judged"] == 1
    assert log.calibration()["high"]["right"] == 0


@pytest.mark.parametrize("rating", [0, 2, -2, True, 1.0, "right", None])
def test_only_explicit_binary_verdicts_are_training_labels(tmp_path, rating):
    log = SelfReviewLog(tmp_path / "self-review.db")
    log.record(example(), "s", "answer")
    with pytest.raises(ValueError, match="verdict"):
        log.judge("s", "answer", rating)
    assert log.assessment()["judged_reviews"] == 0


def test_assessment_measures_saved_displayed_scores_and_reports_missing_feedback(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    log.record(example(.8, .4), "s", "first")
    log.record(example(.8, .7), "s", "second")
    log.record(example(.8), "s", "unjudged")
    assert log.assessment()["brier_score"] is None
    log.judge("s", "first", -1)
    log.judge("s", "second", 1)
    measured = log.assessment()
    assert measured["total_reviews"] == 3
    assert measured["judged_reviews"] == measured["scored_reviews"] == 2
    assert measured["unjudged_reviews"] == 1
    assert measured["verdict_coverage"] == pytest.approx(2 / 3)
    assert measured["brier_score"] == pytest.approx((.4 ** 2 + (.7 - 1) ** 2) / 2)
    assert measured["raw_brier_score"] == pytest.approx((.8 ** 2 + (.8 - 1) ** 2) / 2)
    assert measured["calibration_brier_gain"] == pytest.approx(.215)
    group = log.calibration()["high"]
    assert group["predicted"] == pytest.approx(.55)
    assert group["raw_predicted"] == pytest.approx(.8)
    assert "said 55%" in log.report()
    assert "Verdict coverage: 2/3" in log.report()
    assert "not an independent accuracy benchmark" in log.report()
    # Later feedback changes the fit for future replies, never past predictions.
    log.judge("s", "unjudged", -1)
    log.calibrate(example())
    with sqlite3.connect(log.path) as db:
        saved = json.loads(db.execute("SELECT review_json FROM reviews WHERE id=1").fetchone()[0])
    assert saved["calibrated_score"] == .4
    assert log.assessment()["calibrated_brier_score"] == measured["calibrated_brier_score"]


def test_prequential_calibration_improves_overconfident_synthetic_predictions(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    for index in range(20):
        # Predict and persist before seeing this example's outcome.
        item = log.calibrate(example())
        assert item.get("calibrated_on", 0) == index
        log.record(item, "s", f"answer {index}")
        log.judge("s", f"answer {index}", -1)
    metrics = log.assessment()
    assert metrics["calibrated_reviews"] == 19
    assert metrics["calibration_evidence"] == "lower_error_on_judged_replies"
    assert metrics["brier_score"] < metrics["raw_brier_score"] / 2
    fresh = log.calibrate(example())
    assert fresh["calibrated_level"] == "low"
    assert fresh["revise"]["needed"]
    assert "independent evidence" in " ".join(fresh["revise"]["how"])
    assert "No revision needed" not in " ".join(lines(fresh))


def test_adaptation_reports_when_calibration_worsens_instead_of_claiming_a_gain(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    for index in range(20):
        log.record(example(), "s", f"training {index}")
        log.judge("s", f"training {index}", -1)
    # The pattern changes. These historical wrong answers are not evidence that
    # every future answer is wrong, and the assessment must expose the mistake.
    candidate = log.calibrate(example())
    log.record(candidate, "s", "future correct answer")
    log.judge("s", "future correct answer", 1)
    metrics = log.assessment()
    assert metrics["calibrated_reviews"] == 1
    assert metrics["calibration_brier_gain"] < 0
    assert metrics["calibration_evidence"] == "higher_error_on_judged_replies"


def test_malformed_historical_records_are_visible_and_not_scored_as_zero(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    log.record(example(), "s", "valid")
    log.judge("s", "valid", 1)
    with sqlite3.connect(log.path) as db:
        for score, verdict, payload in ((.8, -1, "broken JSON"), (-.2, 1, "{}"), (.8, 0, "{}")):
            db.execute("INSERT INTO reviews (at, score, level, review_json, verdict, reply_hash) VALUES (?,?,?,?,?,?)",
                       ("2026-11-10", score, "high", payload, verdict, log.digest("historical reply")))
    result = log.assessment()
    assert result["total_reviews"] == 4 and result["judged_reviews"] == 3
    assert result["unscorable_reviews"] == 2 and result["invalid_verdicts"] == 1
    assert result["scored_reviews"] == 1
    assert result["brier_score"] == pytest.approx(.04)
    assert "shown scores incomplete" in log.report()


def test_no_feedback_means_unknown_error_without_creating_a_database(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    assert log.assessment()["brier_score"] is None
    assert log.assessment()["verdict_coverage"] is None
    assert log.assessment()["calibration_evidence"] == "not_measured"
    assert all(row["predicted"] is None and row["observed"] is None for row in log.calibration().values())
    assert not log.path.exists()


def test_historical_empty_reply_verdicts_remain_stored_but_do_not_train(tmp_path):
    log = SelfReviewLog(tmp_path / "self-review.db")
    log.record(example(), "s", "current unjudged answer")
    with sqlite3.connect(log.path) as db:
        db.execute("INSERT INTO reviews (at, score, level, review_json, verdict, reply_hash) VALUES (?,?,?,?,?,?)",
                   ("2026-11-09", .8, "high", json.dumps(example()), -1, log.digest("")))
    assert log.assessment()["unattributed_reviews"] == 1
    assert log.assessment()["scored_reviews"] == 0
    assert log.calibration()["high"]["judged"] == 0
    assert "calibrated_score" not in log.calibrate(example())
    assert "historical verdict" in log.report()
    with sqlite3.connect(log.path) as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2


def test_real_turn_hashes_the_delivered_text_for_exact_feedback(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    reply = rt.assistant.handle("what can you do")
    with sqlite3.connect(tmp_path / "self-review.db") as db:
        digest = db.execute("SELECT reply_hash FROM reviews").fetchone()[0]
    assert digest == SelfReviewLog.digest(reply.response.display_text)
    assert digest != SelfReviewLog.digest("")
    rt.assistant.handle("that's right")
    assert SelfReviewLog.beside(rt.memory).assessment()["scored_reviews"] == 1


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -1, 2, True])
def test_invalid_new_probabilities_are_rejected_before_recording(tmp_path, score):
    log = SelfReviewLog(tmp_path / "self-review.db")
    item = example()
    item["score"] = score
    with pytest.raises(ValueError, match="probability"):
        log.record(item, "s", "answer")
    assert not log.path.exists()


def test_metacognition_exposes_only_aggregate_prediction_evidence(tmp_path):
    memory = SimpleNamespace(path=tmp_path / "agent.db")
    log = SelfReviewLog.beside(memory)
    log.record(example(), "private-session", "Private reply text")
    log.judge("private-session", "Private reply text", 1)
    report = AdaptiveControlPlane(SimpleNamespace(memory=memory)).status()
    reliability = report["review_reliability"]
    assert reliability["available"] and reliability["judged_reviews"] == 1
    assert reliability["brier_score"] == pytest.approx(.04)
    assert "Private reply text" not in str(report)
    assert "private-session" not in str(report)


def test_metacognition_exposes_failed_review_measurement_as_unknown(tmp_path):
    (tmp_path / "self-review.db").write_bytes(b"not a database")
    report = AdaptiveControlPlane(SimpleNamespace(memory=SimpleNamespace(path=tmp_path / "agent.db"))).status()
    assert report["review_reliability"] == {"available": False}
    assert report["observation_errors"]["review_reliability"] == "DatabaseError"
