import json
import hashlib

from app.training.public_status import public_training_summary


def test_public_status_requires_completed_evidence_and_reports_stale_progress_as_reported(tmp_path):
    assert "no completed" in public_training_summary(tmp_path)
    base = tmp_path / "data/models/hayat-open-4b"
    (base / "adapter").mkdir(parents=True)
    (base / "adapter/local-training-metrics.json").write_text(json.dumps({"train": {"epoch": 3}}))
    (base / "adapter/progress.json").write_text(json.dumps({"global_step": 585, "epoch": 3}))
    (base / "adapter-v2").mkdir()
    (base / "adapter-v2/progress.json").write_text(json.dumps({"global_step": 100, "max_steps": 600,
        "updated_at": "2026-10-09T10:00:00+00:00", "training_completed": False}))
    text = public_training_summary(tmp_path)
    assert "585 optimizer steps" in text and "reports step 100 / 600" in text
    assert "does not approve private" in text
    (base / "adapter/local-training-metrics.json").write_text('invalid')
    assert "evidence unavailable" in public_training_summary(tmp_path)


def test_public_status_binds_completed_evaluation_to_actual_weights(tmp_path):
    base = tmp_path / "data/models/hayat-open-4b"
    (base / "adapter").mkdir(parents=True)
    (base / "adapter/local-training-metrics.json").write_text(json.dumps({"train": {"epoch": 3}}))
    (base / "adapter-v2").mkdir()
    (base / "adapter-v2/progress.json").write_text(json.dumps({"training_completed": True, "global_step": 600}))
    weights = base / "adapter-v2/adapter_model.safetensors"
    weights.write_bytes(b"evaluated weights")
    evaluation = {"training_completed": True, "measured_nonregression_passed": True,
                  "candidate_adapter_sha256": hashlib.sha256(weights.read_bytes()).hexdigest()}
    (base / "evaluation-v2.json").write_text(json.dumps(evaluation))
    assert "gate passed" in public_training_summary(tmp_path)
    evaluation["measured_nonregression_passed"] = False
    (base / "evaluation-v2.json").write_text(json.dumps(evaluation))
    assert "gate failed; retain" in public_training_summary(tmp_path)
    weights.write_bytes(b"unevaluated replacements")
    assert "does not match" in public_training_summary(tmp_path)
