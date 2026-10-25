"""No model imports: dataset isolation, resumability and completion faults."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import train_hayat_adapter as runner


def approved_row(split, prompt, answer, *, chat=False, system="Answer accurately."):
    sample = ({"messages": [{"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": answer}]}
              if chat else {"input": prompt, "ideal_response": answer})
    return {"sample": sample, "category": "conversation", "privacy_scope": "STANDARD",
            "local_only": True, "split": split,
            "state": "HELD_OUT" if split == "test" else "TRAINING_READY",
            "content_hash": hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
            "provenance": {"source": "Synthetic regression fixture"},
            "validation": {"deterministic_correct": True}, "consent": {}}


def corpus(tmp_path, rows):
    files = {}
    for split, entries in rows.items():
        path = tmp_path / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in entries), encoding="utf-8")
        files[split] = str(path)
    return files


@pytest.mark.parametrize("split", ["validation", "test"])
@pytest.mark.parametrize("chat", [False, True])
def test_changed_answer_cannot_hide_cross_split_user_prompt(tmp_path, split, chat):
    rows = {name: [approved_row(name, f"Distinct {name} prompt", "A unique target.")]
            for name in ("train", "validation", "test")}
    rows["train"] = [approved_row("train", "What is ＦＯＵＲ plus four?", "8", chat=chat)]
    rows[split] = [approved_row(split, " WHAT  is four\nplus four? ", "eight", chat=not chat,
                                system="Changed instruction cannot conceal leakage.")]
    with pytest.raises(ValueError, match="user prompt leaks across splits"):
        runner._validate_corpus(corpus(tmp_path, rows))


def test_separate_user_turn_boundaries_remain_distinct(tmp_path):
    one = approved_row("train", "First question second question", "One answer.", chat=True)
    two = approved_row("validation", "Second question", "Two answers.", chat=True)
    two["sample"]["messages"][1:1] = [{"role": "user", "content": "First question"},
                                         {"role": "assistant", "content": "Context answer."}]
    two["content_hash"] = hashlib.sha256(json.dumps(two["sample"], sort_keys=True).encode()).hexdigest()
    rows = {"train": [one], "validation": [two],
            "test": [approved_row("test", "A held-out question", "A held-out answer.")]}
    assert runner._validate_corpus(corpus(tmp_path, rows)) == {"train": 1, "validation": 1, "test": 1}


@pytest.mark.parametrize("split", ["train", "validation", "test"])
@pytest.mark.parametrize("suite", ["fixed", "curveballs"])
def test_signed_export_cannot_include_reserved_evaluation_prompt(tmp_path, split, suite):
    if suite == "fixed":
        from app.evaluation.holdout import build_cases
        prompt = build_cases()[0]["prompt"]
    else:
        from app.evaluation.curveballs import build_cases
        prompt = build_cases()[4]["messages"][-1]["content"]
    rows = {name: [approved_row(name, f"Distinct {name} input", "Unique target.")]
            for name in ("train", "validation", "test")}
    rows[split] = [approved_row(split, prompt, "An independently approved target.", chat=True)]
    with pytest.raises(ValueError, match="reserved held-out evaluation question"):
        runner._validate_corpus(corpus(tmp_path, rows))


def test_reserved_prompt_cannot_hide_inside_nested_sample_text(tmp_path):
    from app.evaluation.curveballs import build_cases
    row = approved_row("train", "A separate training question.", "A verified answer.")
    prompt = build_cases()[0]["messages"][-1]["content"]
    row["sample"]["notes"] = {"quoted_material": ["  " + prompt.upper().replace(" ", "\n ") + "  "]}
    row["content_hash"] = hashlib.sha256(json.dumps(row["sample"], sort_keys=True).encode()).hexdigest()
    rows = {"train": [row], "validation": [approved_row("validation", "Another question.", "Answer.")],
            "test": [approved_row("test", "A test question.", "Test answer.")]}
    with pytest.raises(ValueError, match="reserved held-out evaluation question"):
        runner._validate_corpus(corpus(tmp_path, rows))


def foundation_and_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    base, adapter = tmp_path / "base", tmp_path / "adapter"
    base.mkdir(); adapter.mkdir()
    (base / "model.safetensors").write_bytes(b"frozen foundation fixture")
    (base / "config.json").write_text("{}", encoding="utf-8")
    (base / "tokenizer.json").write_text('{"version":"1"}', encoding="utf-8")
    (base / "chat_template.jinja").write_text("original template", encoding="utf-8")
    (base / "chat_templates").mkdir()
    (base / "chat_templates/tool_use.jinja").write_text("tool template", encoding="utf-8")
    system = tmp_path / "models/hayat-open/system.txt"
    system.parent.mkdir(parents=True)
    system.write_text("HAYAT system instruction", encoding="utf-8")
    config = {"peft_type": "LORA", "r": 16, "lora_alpha": 32, "target_modules": ["q_proj", "v_proj"]}
    original = {"foundation_sha256": {"model.safetensors": runner._sha256_file(base / "model.safetensors")},
                "model_config_sha256": runner._sha256_file(base / "config.json")}
    (adapter / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    (adapter / "run-config.json").write_text(json.dumps(original), encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter fixture")
    (adapter / "local-training-metrics.json").write_text("{}", encoding="utf-8")
    return base, adapter, original


def test_legacy_adapter_continuation_exposes_unrecorded_assets(tmp_path, monkeypatch):
    base, adapter, _ = foundation_and_adapter(tmp_path, monkeypatch)
    proof = runner._initial_adapter_signature(adapter, base, 16, 32, ["q_proj", "v_proj"])
    assert proof["input_assets_verification"] == "legacy_unrecorded"
    assert proof["input_assets_sha256"] == runner._input_assets_signature(base)
    assert "hayat-open/system.txt" in proof["input_assets_sha256"]


@pytest.mark.parametrize("asset", ["tokenizer.json", "chat_template.jinja",
                                    "chat_templates/tool_use.jinja", "hayat-open/system.txt"])
def test_recorded_adapter_refuses_changed_tokenizer_template_or_system(tmp_path, monkeypatch, asset):
    base, adapter, original = foundation_and_adapter(tmp_path, monkeypatch)
    original["input_assets_sha256"] = runner._input_assets_signature(base)
    (adapter / "run-config.json").write_text(json.dumps(original), encoding="utf-8")
    proof = runner._initial_adapter_signature(adapter, base, 16, 32, ["q_proj", "v_proj"])
    assert proof["input_assets_verification"] == "verified"
    path = tmp_path / "models" / asset if asset == "hayat-open/system.txt" else base / asset
    path.write_text("changed input assets", encoding="utf-8")
    with pytest.raises(ValueError, match="assets changed"):
        runner._initial_adapter_signature(adapter, base, 16, 32, ["q_proj", "v_proj"])


def test_new_resume_rejects_asset_additions_and_legacy_signature(tmp_path, monkeypatch):
    base, _, _ = foundation_and_adapter(tmp_path, monkeypatch)
    output = tmp_path / "new-candidate"
    original = {"input_assets_sha256": runner._input_assets_signature(base)}
    runner._prepare_output(output, original, None)
    checkpoint = output / "checkpoint-8"
    checkpoint.mkdir()
    for name in ("trainer_state.json", "adapter_config.json"):
        (checkpoint / name).write_text("{}", encoding="utf-8")
    runner._prepare_output(output, original, checkpoint)
    (base / "added_tokens.json").write_text("{}", encoding="utf-8")
    changed = {"input_assets_sha256": runner._input_assets_signature(base)}
    with pytest.raises(ValueError, match="hashes changed"):
        runner._prepare_output(output, changed, checkpoint)
    (output / "run-config.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hashes changed"):
        runner._prepare_output(output, original, checkpoint)


def test_atomic_completion_preserves_previous_marker_until_replacement(tmp_path, monkeypatch):
    destination = tmp_path / "local-training-metrics.json"
    runner._write_json(destination, {"previous": True})
    actual_replace = Path.replace
    attempts = []

    def fail_until_unlocked(source, target):
        assert json.loads(destination.read_text(encoding="utf-8")) == {"previous": True}
        assert json.loads(source.read_text(encoding="utf-8")) == {"completed": True}
        attempts.append(source)
        if len(attempts) < 3:
            raise PermissionError("Synthetic Windows file lock")
        return actual_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_until_unlocked)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    runner._write_json(destination, {"completed": True})
    assert len(attempts) == 3
    assert json.loads(destination.read_text(encoding="utf-8")) == {"completed": True}


def test_failed_completion_replacement_never_creates_completion_marker(tmp_path, monkeypatch):
    destination = tmp_path / "local-training-metrics.json"

    def locked(*args):
        raise PermissionError("Synthetic permanent file lock")

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        runner._write_json(destination, {"completed": True})
    assert not destination.exists()
