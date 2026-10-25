import json

import pytest

from app.training.pipeline import TrainingDataPipeline
from scripts.train_hayat_adapter import _dataset_files


def sample(pipeline):
    record = pipeline.ingest("conversation", {"input": "What is a transaction?", "ideal_response": "A unit of work."},
                             provenance={"source": "verified exercise"})
    pipeline.validate(record["id"], deterministic_correct=True)
    return record["id"]


def test_tampered_content_cannot_pass_dataset_checks(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    identifier = sample(pipeline)
    pipeline.promote(identifier)
    with pipeline._connect() as db:
        db.execute("UPDATE training_records SET sample_json=? WHERE id=?", ('{"input":"changed"}', identifier))
    quality = pipeline.validate_dataset()
    assert not quality["passed"] and quality["content_mismatches"] == [identifier]


@pytest.mark.parametrize('assignment', ['password = actual-password-123456', 'password = "actual-password-123456"'])
def test_redacted_password_assignments_are_not_reported_as_leaked_credentials(tmp_path, assignment):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    record = pipeline.ingest('conversation', {'input': assignment},
                             provenance={'source': 'redaction test'})
    assert record['redactions'] > 0
    assert pipeline.validate_dataset()['secret_leaks'] == []
    with pipeline._connect() as db:
        db.execute('UPDATE training_records SET sample_json=? WHERE id=?',
                   (json.dumps({'input': 'password = real-credential-123456'}), record['id']))
    assert pipeline.validate_dataset()['secret_leaks'] == [record['id']]


def test_training_validation_uses_the_shared_detector_for_other_credentials(tmp_path):
    # Intentionally fabricated credential fixture: exercises rejection, never authentication.
    pipeline = TrainingDataPipeline(tmp_path, config={})
    identifier = sample(pipeline)
    with pipeline._connect() as db:
        db.execute('UPDATE training_records SET sample_json=? WHERE id=?',
                   (json.dumps({'input': 'AKIA' + '1234567890123456'}), identifier))  # fake, split for secret scanners
    assert pipeline.validate_dataset()['secret_leaks'] == [identifier]


def test_malformed_records_are_reported_without_crashing_status(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    identifier = sample(pipeline)
    with pipeline._connect() as db:
        db.execute("UPDATE training_records SET consent_json='invalid' WHERE id=?", (identifier,))
    assert pipeline.status()["validation"]["malformed_records"] == [identifier]


def test_string_false_is_not_verification(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    identifier = sample(pipeline)
    with pytest.raises(ValueError, match="requires"):
        pipeline.validate(identifier, verification={"passed": "false"})


def test_raw_record_cannot_become_eligible_by_changing_only_its_state(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    record = pipeline.ingest("conversation", {"input": "A question"}, provenance={"source": "collected"})
    with pipeline._connect() as db:
        db.execute("UPDATE training_records SET state='TRAINING_READY',split='train'")
    assert pipeline.validate_dataset()["unverified_eligible_records"] == [record["id"]]


def test_repeated_review_does_not_duplicate_export_and_rejection_removes_it(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={})
    identifier = sample(pipeline)
    promoted = pipeline.promote(identifier)
    destination = tmp_path / "conversation" / f"{promoted['split']}.jsonl"
    pipeline.review(identifier, approved=True)
    assert len(destination.read_text().splitlines()) == 1
    assert json.loads(destination.read_text())["id"] == identifier
    pipeline.review(identifier, approved=False, reason="incorrect example")
    assert destination.read_text() == ""
    assert pipeline.validate_dataset()["passed"]


def test_readiness_uses_configured_minimum_and_requires_splits(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path, config={"minimum_validated_samples": 150})
    gate = pipeline.training_gate(150)
    assert gate["minimum_validated_samples"] == 150
    assert gate["checks"]["minimum_samples"]
    assert not gate["ready"] and "held_out_test" in gate["blockers"]


def test_training_runner_refuses_ambiguous_or_mixed_corpora(tmp_path):
    for category in ("conversation", "routing"):
        folder = tmp_path / category
        folder.mkdir()
        for split in ("train", "validation", "test"):
            (folder / f"{split}.jsonl").write_text('{}\n')
    with pytest.raises(ValueError, match="multiple dataset splits"):
        _dataset_files(tmp_path)
    assert set(_dataset_files(tmp_path / "conversation")) == {"train", "validation", "test"}
