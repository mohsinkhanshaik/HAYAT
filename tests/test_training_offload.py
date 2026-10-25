"""Offloading must preserve the existing corpus and base-weight fit gates."""
import sys
import json
from types import SimpleNamespace

import pytest

from scripts import train_hayat_adapter as runner


def setup_hardware(monkeypatch, *, cuda=True, ram=24 * 1024**3, parameters=4_000_000_000):
    monkeypatch.setattr("app.training.readiness.checkpoint_report",
                        lambda _: {"ready": True, "weight_bytes": parameters * 2})
    monkeypatch.setattr(runner, "_checkpoint_parameter_count", lambda _: parameters)
    monkeypatch.setattr("psutil.virtual_memory", lambda: SimpleNamespace(available=ram))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: cuda, is_bf16_supported=lambda: True,
        mem_get_info=lambda device: (7 * 1024**3, 8 * 1024**3))))


COUNTS = {"train": 800, "validation": 100, "test": 100}


def test_activation_offload_reports_scope_without_claiming_memory_fit(tmp_path, monkeypatch):
    setup_hardware(monkeypatch)
    report = runner._preflight(tmp_path, COUNTS, "QLORA", activation_offloading=True)
    assert report["activation_offloading"] is True
    assert report["base_weight_offloading"] is False
    assert report["hardware_fit_verified"] is False


@pytest.mark.parametrize("cuda,ram,match", [
    (False, 24 * 1024**3, "requires a CUDA"),
    (True, 1024**3, "free host RAM"),
])
def test_offload_requires_cuda_and_host_headroom(tmp_path, monkeypatch, cuda, ram, match):
    setup_hardware(monkeypatch, cuda=cuda, ram=ram)
    with pytest.raises(RuntimeError, match=match):
        runner._preflight(tmp_path, COUNTS, "QLORA", activation_offloading=True)


def test_offload_does_not_bypass_35b_weight_storage_or_dataset_gate(tmp_path, monkeypatch):
    setup_hardware(monkeypatch, parameters=35_000_000_000)
    with pytest.raises(RuntimeError, match="Activation offloading does not reduce base-weight storage"):
        runner._preflight(tmp_path, COUNTS, "QLORA", activation_offloading=True)
    with pytest.raises(ValueError, match="approved examples"):
        runner._preflight(tmp_path, {"train": 42, "validation": 2, "test": 8}, "QLORA", True)


def test_resume_requires_own_checkpoint_and_unchanged_inputs(tmp_path):
    output = tmp_path / "candidate"
    signature = {"dataset_sha256": {"train": "original"}}
    runner._prepare_output(output, signature, None)
    with pytest.raises(FileExistsError):
        runner._prepare_output(output, signature, None)
    checkpoint = output / "checkpoint-50"
    checkpoint.mkdir()
    (checkpoint / "trainer_state.json").write_text("{}")
    (checkpoint / "adapter_config.json").write_text("{}")
    runner._prepare_output(output, signature, checkpoint)
    with pytest.raises(ValueError, match="hashes changed"):
        runner._prepare_output(output, {"dataset_sha256": {"train": "changed"}}, checkpoint)
    with pytest.raises(ValueError, match="inside"):
        runner._prepare_output(output, signature, tmp_path)
    (output / "local-training-metrics.json").write_text(json.dumps({"done": True}))
    with pytest.raises(ValueError, match="already finished"):
        runner._prepare_output(output, signature, checkpoint)


def test_continuation_refuses_a_different_foundation_or_adapter_shape(tmp_path):
    base, adapter = tmp_path / "base", tmp_path / "adapter"
    base.mkdir(); adapter.mkdir()
    (base / "model.safetensors").write_bytes(b"foundation")
    (base / "config.json").write_text("{}")
    config = {"peft_type": "LORA", "r": 16, "lora_alpha": 32, "target_modules": ["q_proj", "v_proj"]}
    original = {"foundation_sha256": {"model.safetensors": runner._sha256_file(base / "model.safetensors")},
                "model_config_sha256": runner._sha256_file(base / "config.json")}
    (adapter / "adapter_config.json").write_text(json.dumps(config))
    (adapter / "run-config.json").write_text(json.dumps(original))
    (adapter / "local-training-metrics.json").write_text("{}")
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    proof = runner._initial_adapter_signature(adapter, base, 16, 32, ["q_proj", "v_proj"])
    assert len(proof["weights_sha256"]) == 64
    with pytest.raises(ValueError, match="compatible"):
        runner._initial_adapter_signature(adapter, base, 8, 32, ["q_proj", "v_proj"])
    (base / "model.safetensors").write_bytes(b"different foundation")
    with pytest.raises(ValueError, match="exact foundation"):
        runner._initial_adapter_signature(adapter, base, 16, 32, ["q_proj", "v_proj"])


def test_public_corpus_selection_uses_actual_review_fields_and_tree_split():
    from scripts.prepare_hayat_open_corpus import eligible, split_for_tree
    row = {"lang": "en", "review_result": True, "review_count": 3, "deleted": False,
           "synthetic": False, "text": "Example", "labels": {"name": ["spam", "pii"], "value": [0, 0]}}
    assert eligible(row)
    assert not eligible({**row, "review_result": False})
    assert not eligible({**row, "review_count": 0})
    assert not eligible({**row, "labels": {"name": ["pii"], "value": [.5]}})
    assert split_for_tree("shared-root", "validation") == "test"
    assert split_for_tree("shared-root", "train") in {"train", "validation"}
    assert split_for_tree("shared-root", "train") == split_for_tree("shared-root", "train")
