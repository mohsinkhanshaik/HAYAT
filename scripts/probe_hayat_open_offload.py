"""Measure real-foundation backward memory; never take an optimizer step or save weights.

Uses a synthetic tensor fixture to check hardware, not an approved training
corpus. No inference of training quality is possible from this probe.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--length", type=int, default=512)
    args = parser.parse_args()
    if not 64 <= args.length <= 2048:
        parser.error("Probe length must be 64..2048.")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1")
    import torch
    from accelerate import PartialState
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl.models.activation_offloading import get_act_offloading_ctx_manager

    PartialState()
    torch.manual_seed(42)
    location = ROOT / "data/models/hayat-open-4b/foundation"
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(location, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(
        location, local_files_only=True, trust_remote_code=False, dtype=dtype, device_map={"": 0},
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype))
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0,
                                           target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM"))
    model.config.use_cache = False
    model.train()
    ids = tokenizer("Calculate 6 plus 7. The answer is 13. " * args.length, return_tensors="pt")["input_ids"][:, :args.length].to("cuda")
    labels = ids.clone()
    labels[:, :-16] = -100
    measurements = []
    for offload in (False, True):
        model.zero_grad(set_to_none=True)
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        manager = get_act_offloading_ctx_manager(model=model) if offload else contextlib.nullcontext()
        with manager:
            loss = model(input_ids=ids, attention_mask=torch.ones_like(ids), labels=labels).loss
            loss.backward()
        torch.cuda.synchronize()
        gradients = [p.grad for p in model.parameters() if p.requires_grad and p.grad is not None]
        entry = {"activation_offloading": offload, "loss": float(loss.detach()),
                 "finite_nonzero_gradients": bool(gradients) and all(torch.isfinite(g).all().item() for g in gradients)
                     and any(torch.count_nonzero(g).item() > 0 for g in gradients),
                 "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                 "peak_reserved_bytes": torch.cuda.max_memory_reserved()}
        measurements.append(entry)
        print(json.dumps(entry), flush=True)
        del loss
    report = {"scope": "Synthetic hardware/backward probe on real pretrained 4B foundation; no optimizer steps",
              "sequence_length": args.length, "adapter_rank": 16, "measurements": measurements,
              "training_completed": False, "optimizer_steps": 0,
              "weights_saved": False, "quality_established": False}
    destination = ROOT / "data/models/hayat-open-4b/offload-probe.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not all(row["finite_nonzero_gradients"] for row in measurements):
        raise RuntimeError("Probe produced missing, zero or nonfinite adapter gradients.")


if __name__ == "__main__":
    main()
