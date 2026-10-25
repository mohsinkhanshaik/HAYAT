import pytest

from app.training.readiness import checkpoint_report, explain_gate
from scripts.train_hayat_adapter import _format_pair, _training_pair


def test_inference_weights_or_empty_directory_are_not_a_training_checkpoint(tmp_path):
    assert not checkpoint_report(None)["ready"]
    assert not checkpoint_report(tmp_path)["ready"]
    (tmp_path / "model.gguf").write_bytes(b"GGUF")
    assert not checkpoint_report(tmp_path)["ready"]
    (tmp_path / "config.json").write_text('{"model_type":"qwen3_5_moe"}')
    (tmp_path / "tokenizer.json").write_text("{}")
    (tmp_path / "model.safetensors.index.json").write_text('{"weight_map":{"x":"missing.safetensors"}}')
    assert not checkpoint_report(tmp_path)["ready"]
    (tmp_path / "missing.safetensors").write_bytes(b"0123456789")
    report = checkpoint_report(tmp_path)
    assert report["ready"] and report["weight_bytes"] == 10
    assert "loading" in report["inspection_scope"]
    (tmp_path / "model.safetensors.index.json").write_text('{"weight_map":{"x":"../outside.safetensors"}}')
    assert not checkpoint_report(tmp_path)["ready"]


def test_gate_explanation_uses_exact_scope_and_does_not_claim_training():
    text = explain_gate({"ready": False, "profile": "conversation", "privacy_scope": "PRIVATE_LOCAL",
        "records": 0, "minimum_validated_samples": 1000, "blockers": ["local_trainable_checkpoint", "held_out_test"],
        "split_counts": {"train": 0, "validation": 0, "test": 0}})
    assert "blocked" in text and "private local" in text and "0 / 1000" in text
    assert "checkpoint missing" in text and "Hardware is checked" in text


def test_chat_targets_exclude_system_and_user_from_completion_loss():
    row = {"sample": {"messages": [{"role": "system", "content": "Be accurate"},
           {"role": "user", "content": "What did you check?"},
           {"role": "assistant", "content": "Only the recorded sources."}]}}
    pair = _training_pair(row)
    assert len(pair["prompt"]) == 2 and pair["completion"] == row["sample"]["messages"][-1:]
    class Tokenizer:
        def apply_chat_template(self, messages, tokenize, add_generation_prompt):
            return "".join(f"{m['role']}: {m['content']}\n" for m in messages) + ("assistant: " if add_generation_prompt else "")
    formatted = _format_pair(row, Tokenizer())
    assert formatted["completion"] == "Only the recorded sources.\n"
    class Broken(Tokenizer):
        def apply_chat_template(self, *args, **kwargs):
            return "prompt" if kwargs["add_generation_prompt"] else "changed completion"
    with pytest.raises(ValueError, match="boundary"):
        _format_pair(row, Broken())


def test_preflight_refuses_small_dataset_before_loading_training_stack(tmp_path):
    from scripts.train_hayat_adapter import _preflight
    (tmp_path / "config.json").write_text('{"model_type":"gpt2"}')
    (tmp_path / "tokenizer.json").write_text("{}")
    (tmp_path / "model.safetensors").write_bytes(b"0123456789")
    with pytest.raises(ValueError, match="approved examples"):
        _preflight(tmp_path, {"train": 1, "validation": 1, "test": 1}, "LORA")


def test_cuda_floor_rejects_35b_on_an_8gb_device_even_with_four_bit_weights():
    from scripts.train_hayat_adapter import _cuda_memory_floor
    assert _cuda_memory_floor(35_000_000_000, "QLORA", True) > 8 * 1024**3
    assert _cuda_memory_floor(50_000, "LORA", False) == 200_000 + 1024**3


def test_checkpoint_count_reads_shapes_and_rejects_quantized_inference_weights(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    import safetensors
    from scripts.train_hayat_adapter import _checkpoint_parameter_count

    (tmp_path / "model.safetensors.index.json").write_text(json.dumps({
        "weight_map": {"a": "first.safetensors", "b": "first.safetensors", "c": "second.safetensors"}}))
    opened = []
    dtype = ["BF16"]
    class Shard:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def keys(self):
            return ["weight"]
        def get_slice(self, key):
            return SimpleNamespace(get_shape=lambda: [5, 7], get_dtype=lambda: dtype[0])
    def open_shard(path, framework):
        opened.append(path)
        return Shard()
    monkeypatch.setattr(safetensors, "safe_open", open_shard)
    assert _checkpoint_parameter_count(tmp_path) == 70
    assert len(opened) == 2
    dtype[0] = "U8"
    with pytest.raises(ValueError, match="inference container"):
        _checkpoint_parameter_count(tmp_path)


def test_preflight_stops_before_model_loading_when_cuda_memory_is_insufficient(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import train_hayat_adapter as runner
    monkeypatch.setattr("app.training.readiness.checkpoint_report", lambda _: {"ready": True, "weight_bytes": 70_000_000_000})
    monkeypatch.setattr(runner, "_checkpoint_parameter_count", lambda _: 35_000_000_000)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, is_bf16_supported=lambda: True,
        mem_get_info=lambda device: (7 * 1024**3, 8 * 1024**3))))
    with pytest.raises(RuntimeError, match="does not implement CPU/disk-offloaded training"):
        runner._preflight(tmp_path, {"train": 800, "validation": 100, "test": 100}, "QLORA")
