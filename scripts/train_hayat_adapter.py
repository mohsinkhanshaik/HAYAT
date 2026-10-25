"""Explicit local-only LoRA/QLoRA training entry point for a gate-passing candidate.

This script runs only through an explicit local command or launched workflow.
The application does not start it automatically. It accepts only existing
local paths, forces Hugging Face offline mode, never pushes to a Hub, and saves
an adapter candidate rather than replacing the active HAYAT model.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import os
import sys
import math
import time
import unicodedata
from uuid import uuid4
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _existing_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"Not an existing directory: {path}")
    return path


def _dataset_files(root: Path) -> dict[str, str]:
    files = {}
    for split in ("train", "validation", "test"):
        matches = sorted(root.rglob(f"{split}.jsonl"))
        if len(matches) > 1:
            raise ValueError("Select one category and privacy scope; multiple dataset splits were found.")
        if matches:
            files[split] = str(matches[0])
    if "train" not in files or "validation" not in files or "test" not in files:
        raise ValueError("Train, validation, and held-out test JSONL files are required.")
    if len({Path(path).parent for path in files.values()}) != 1:
        raise ValueError("All dataset splits must belong to the same category and privacy scope.")
    return files


def _conversation(row: dict) -> dict:
    sample = row.get("sample") or row
    if not isinstance(sample, dict):
        raise ValueError("Training samples must be objects.")
    if isinstance(sample.get("messages"), list):
        messages = sample["messages"]
        if (not messages or any(not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}
                                or not isinstance(item.get("content"), str) or not item["content"].strip() for item in messages)
                or not any(item["role"] == "user" for item in messages) or messages[-1]["role"] != "assistant"):
            raise ValueError("Message examples require nonempty user input and a final assistant target.")
        return {"messages": messages}
    prompt = sample.get("prompt") or sample.get("input") or sample.get("instruction")
    completion = sample.get("completion") or sample.get("ideal_response") or sample.get("response")
    if not isinstance(prompt, str) or not prompt.strip() or not isinstance(completion, str) or not completion.strip():
        raise ValueError("Every SFT record needs messages or a prompt/input plus completion/ideal_response.")
    return {"prompt": str(prompt), "completion": str(completion)}


def _sample_texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _sample_texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _sample_texts(item)


def _evaluation_normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", text).casefold()).split())


def _validate_corpus(files: dict[str, str]) -> dict[str, int]:
    """Recheck the actual exported bytes before allocating model memory."""
    from app.safety.sensitive import contains_secret
    from app.evaluation.holdout import is_holdout
    from app.evaluation.curveballs import build_cases
    reserved_prompts = {_evaluation_normal(message["content"])
                        for case in build_cases() for message in case["messages"] if message["role"] == "user"}
    seen, prompts, counts, scopes, categories = {}, {}, {}, set(), set()
    for split, filename in files.items():
        counts[split] = 0
        with Path(filename).open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                location = f"{split}:{number}"
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("sample"), dict):
                    raise ValueError(f"{location}: expected an approved HAYAT training export.")
                expected_state = "HELD_OUT" if split == "test" else "TRAINING_READY"
                validation = row.get("validation") or {}
                if not isinstance(validation, dict) or not isinstance(row.get("provenance"), dict) or not isinstance(row.get("consent", {}), dict):
                    raise ValueError(f"{location}: invalid approval or provenance metadata.")
                proof = validation.get("verification") or {}
                if (row.get("state") != expected_state or row.get("split") != split
                        or not (validation.get("human_approved") is True or validation.get("deterministic_correct") is True
                                or isinstance(proof, dict) and proof.get("passed") is True)
                        or validation.get("reject_reason") or not (row.get("provenance") or {}).get("source")):
                    raise ValueError(f"{location}: missing approval, provenance or correct split.")
                canonical = json.dumps(row["sample"], sort_keys=True, default=str)
                if hashlib.sha256(canonical.encode()).hexdigest() != row.get("content_hash") or contains_secret(row):
                    raise ValueError(f"{location}: content changed or contains credentials.")
                # A signed export is not permission to optimize an evaluation
                # question. Recheck actual text at allocation time, including
                # nested fields, system instructions and assistant targets.
                for content in _sample_texts(row["sample"]):
                    normalized_content = _evaluation_normal(content)
                    if is_holdout(content) or any(prompt in normalized_content for prompt in reserved_prompts):
                        raise ValueError(f"{location}: reserved held-out evaluation question is not training data.")
                if row["provenance"].get("source") == "HAYAT deterministic exercises v2":
                    from scripts.prepare_hayat_reasoning_corpus import exercise
                    provenance = row["provenance"]
                    if type(provenance.get("seed")) is not int:
                        raise ValueError(f"{location}: deterministic exercise needs an integer seed.")
                    question, expected = exercise(provenance.get("family"), provenance["seed"])
                    messages = row["sample"].get("messages", [])
                    if (len(messages) != 3 or messages[-2].get("content") != question
                            or messages[-1].get("content") != expected):
                        raise ValueError(f"{location}: deterministic exercise target did not match its oracle.")
                scope = row.get("privacy_scope")
                if scope not in {"STANDARD", "PRIVATE_LOCAL"} or row.get("local_only") is not True:
                    raise ValueError(f"{location}: invalid local privacy scope.")
                if scope == "PRIVATE_LOCAL" and (row.get("consent") or {}).get("owner_consented") is not True:
                    raise ValueError(f"{location}: private sample lacks speaker/owner consent.")
                scopes.add(scope); categories.add(row.get("category"))
                example = _conversation(row)
                normalized = re.sub(r"\s+", " ", json.dumps(example, sort_keys=True).casefold()).strip()
                if normalized in seen:
                    raise ValueError(f"{location}: duplicate training target also found in {seen[normalized]}.")
                seen[normalized] = location
                # Different answers, system messages or formatting must not
                # conceal the same user input in a held-out split. Keep a
                # dialogue's user turns separate so their boundaries matter.
                user_turns = ([item["content"] for item in example["messages"] if item["role"] == "user"]
                              if "messages" in example else [example["prompt"]])
                prompt_key = json.dumps([
                    re.sub(r"\s+", " ", unicodedata.normalize("NFKC", turn).casefold()).strip()
                    for turn in user_turns], ensure_ascii=False)
                previous = prompts.get(prompt_key)
                if previous and previous[0] != split:
                    raise ValueError(f"{location}: user prompt leaks across splits; also found in {previous[1]}.")
                prompts[prompt_key] = (split, location)
                counts[split] += 1
        if not counts[split]:
            raise ValueError(f"{split} has no approved examples.")
    if len(scopes) != 1 or len(categories) != 1:
        raise ValueError("Select one category and privacy scope for training.")
    return counts


def _training_pair(row: dict) -> dict:
    """Keep user/system text out of completion loss, including chat examples."""
    example = _conversation(row)
    if "messages" in example:
        return {"prompt": example["messages"][:-1], "completion": example["messages"][-1:]}
    return example


def _format_pair(row: dict, tokenizer) -> dict:
    pair = _training_pair(row)
    if isinstance(pair["prompt"], list):
        prompt = tokenizer.apply_chat_template(pair["prompt"], tokenize=False, add_generation_prompt=True)
        full = tokenizer.apply_chat_template(pair["prompt"] + pair["completion"], tokenize=False,
                                             add_generation_prompt=False)
        if not full.startswith(prompt) or not full[len(prompt):].strip():
            raise ValueError("Chat template does not preserve the prompt/completion boundary; curate the template first.")
        return {"prompt": prompt, "completion": full[len(prompt):]}
    return pair


def _checkpoint_parameter_count(model_dir: Path) -> int:
    """Inspect tensor shapes without materializing checkpoint weights."""
    from safetensors import safe_open

    index = model_dir / "model.safetensors.index.json"
    if index.is_file():
        mapping = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        names = sorted(set(mapping.values()))
    else:
        names = ["model.safetensors"]
    count = 0
    for name in names:
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("Invalid checkpoint shard path.")
        with safe_open(str(model_dir / name), framework="pt") as shard:
            for key in shard.keys():
                tensor = shard.get_slice(key)
                if tensor.get_dtype() not in {"F64", "F32", "F16", "BF16"}:
                    raise ValueError("This runner requires floating-point foundation weights, not an inference container.")
                count += math.prod(tensor.get_shape())
    if count <= 0:
        raise ValueError("The checkpoint has no model parameters.")
    return count


def _cuda_memory_floor(parameter_count: int, method: str, bf16: bool) -> int:
    # This runner places the entire base on CUDA device 0; it has no CPU/disk
    # offload training path. Even ideal 4-bit weights alone must fit there.
    # Real allocations also include quantization metadata, unquantized modules,
    # adapters, activations and optimizer state, so passing is not a fit proof.
    weights = (parameter_count + 1) // 2 if method == "QLORA" else parameter_count * (2 if bf16 else 4)
    return weights + 1024 ** 3


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex[:8] + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05 * (attempt + 1))


def _prepare_output(output: Path, signature: dict, resume: Path | None) -> None:
    """Only resume our own checkpoint with identical data and training settings."""
    if resume is not None:
        resume = resume.resolve()
        if (resume.parent != output or not (resume / "trainer_state.json").is_file()
                or not (resume / "adapter_config.json").is_file()):
            raise ValueError("Resume requires an adapter checkpoint immediately inside the candidate output.")
        if json.loads((output / "run-config.json").read_text(encoding="utf-8")) != signature:
            raise ValueError("Resume configuration or input hashes changed; use a new candidate output.")
        if (output / "local-training-metrics.json").exists():
            raise ValueError("This candidate already finished training.")
    elif output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty candidate output: {output}")
    output.mkdir(parents=True, exist_ok=True)
    if resume is None:
        _write_json(output / "run-config.json", signature)


def _input_assets_signature(model_dir: Path) -> dict[str, str]:
    """Bind tokenization and prompt assets without importing model libraries.

    A new run records this exact file set, so additions and removals matter as
    well as changed bytes. System messages embedded in examples are separately
    bound by the dataset hashes.
    """
    assets = {}
    patterns = ("tokenizer*", "special_tokens_map.json", "added_tokens.json", "vocab.*", "merges.txt",
                "*.model", "chat_template*", "chat_templates/*.jinja", "system.txt", "system_prompt.txt",
                "generation_config.json")
    for pattern in patterns:
        for path in sorted(model_dir.glob(pattern)):
            if path.is_file():
                assets[path.relative_to(model_dir).as_posix()] = _sha256_file(path)
    system = PROJECT_ROOT / "models/hayat-open/system.txt"
    if system.is_file():
        assets["hayat-open/system.txt"] = _sha256_file(system)
    return dict(sorted(assets.items()))


def _initial_adapter_signature(adapter: Path, model_dir: Path, rank: int, alpha: int,
                               targets: list[str]) -> dict:
    """Continuation must use the exact foundation and adapter structure."""
    config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    original = json.loads((adapter / "run-config.json").read_text(encoding="utf-8"))
    weights = adapter / "adapter_model.safetensors"
    foundation = {p.name: _sha256_file(p) for p in sorted(model_dir.glob("*.safetensors"))}
    assets = _input_assets_signature(model_dir)
    if (config.get("peft_type") != "LORA" or config.get("r") != rank
            or config.get("lora_alpha") != alpha or set(config.get("target_modules", [])) != set(targets)
            or original.get("foundation_sha256") != foundation
            or original.get("model_config_sha256") != _sha256_file(model_dir / "config.json")
            or not (adapter / "local-training-metrics.json").is_file() or not weights.is_file()):
        raise ValueError("Initial adapter must be a completed compatible run on this exact foundation.")
    recorded_assets = original.get("input_assets_sha256")
    if "input_assets_sha256" in original and (not isinstance(recorded_assets, dict) or recorded_assets != assets):
        raise ValueError("Initial adapter tokenizer, chat template or system assets changed since training.")
    return {"directory": str(adapter), "weights_sha256": _sha256_file(weights),
            "config_sha256": _sha256_file(adapter / "adapter_config.json"),
            "input_assets_sha256": assets,
            "input_assets_verification": "verified" if recorded_assets is not None else "legacy_unrecorded"}


def _preflight(model_dir: Path, counts: dict[str, int], method: str,
               activation_offloading: bool = False) -> dict:
    from app.config import load_settings
    from app.training.readiness import checkpoint_report
    report = checkpoint_report(model_dir)
    if not report["ready"]:
        raise ValueError(" ".join(report["problems"]))
    minimum = max(100, int(load_settings().get("training", {}).get("minimum_validated_samples", 1000)))
    if sum(counts.values()) < minimum:
        raise ValueError(f"Need {minimum} approved examples in one category and privacy scope, found {sum(counts.values())}.")
    import torch
    import psutil
    cuda = torch.cuda.is_available()
    if activation_offloading and not cuda:
        raise RuntimeError("CPU activation offloading requires a CUDA training device.")
    if method == "QLORA" and not cuda:
        raise RuntimeError("This QLoRA runner requires CUDA-enabled PyTorch. Installed PyTorch cannot use CUDA.")
    # An intentionally conservative refusal before a frozen float32 CPU base
    # can exhaust RAM. Quantized CUDA allocation still needs a real fit test.
    available = psutil.virtual_memory().available
    if activation_offloading and available < 2 * 1024**3:
        raise RuntimeError("CPU activation offloading needs at least 2 GiB of free host RAM; actual usage depends on context length.")
    if not cuda and report["weight_bytes"] * 3 > available:
        raise RuntimeError("Insufficient free RAM for this runner's float32 base plus training headroom.")
    parameters = _checkpoint_parameter_count(model_dir)
    memory = {"parameter_count": parameters}
    if cuda:
        free_vram, total_vram = torch.cuda.mem_get_info(0)
        minimum_vram = _cuda_memory_floor(parameters, method, torch.cuda.is_bf16_supported())
        memory.update(available_vram_bytes=free_vram, total_vram_bytes=total_vram,
                      minimum_vram_bytes=minimum_vram)
        if minimum_vram > free_vram:
            raise RuntimeError(
                f"Insufficient free VRAM: this runner needs at least {minimum_vram / 1024**3:.1f} GiB "
                f"for {parameters:,} parameters and minimal headroom, but device 0 has "
                f"{free_vram / 1024**3:.1f} GiB free. This runner does not implement CPU/disk-offloaded training "
                "of base weights. Activation offloading does not reduce base-weight storage."
            )
    return {**report, "cuda_available": cuda, "device": "cuda" if cuda else "cpu",
            "minimum_validated_samples": minimum, "split_counts": counts,
            "available_ram_bytes": available, **memory, "hardware_fit_verified": False,
            "activation_offloading": activation_offloading, "base_weight_offloading": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True, type=_existing_directory)
    parser.add_argument("--dataset-dir", required=True, type=_existing_directory)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--method", choices=("LORA", "QLORA"), default="QLORA")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=32)
    parser.add_argument("--targets", default="q_proj,v_proj",
                        help="Explicit module suffixes, e.g. q_proj,v_proj; confirm these in the matching foundation.")
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--max-steps", type=int, default=-1, help="Optional bounded training budget; -1 uses epochs.")
    parser.add_argument("--activation-offloading", action="store_true",
                        help="Move saved training activations to CPU RAM using TRL; base weights stay on CUDA.")
    parser.add_argument("--checkpoint-steps", type=int, default=0,
                        help="0 evaluates/saves each epoch; positive values evaluate/save every N steps.")
    parser.add_argument("--resume-from-checkpoint", type=_existing_directory)
    parser.add_argument("--initial-adapter", type=_existing_directory,
                        help="Continue a completed compatible adapter into a separate candidate directory.")
    parser.add_argument("--early-stopping-patience", type=int, default=0,
                        help="Stop after this many validation checks without improvement; 0 disables.")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if (not math.isfinite(args.epochs) or not 0 < args.epochs <= 20
            or not math.isfinite(args.learning_rate) or not 0 < args.learning_rate <= 1e-3
            or not 1 <= args.rank <= 128 or not 1 <= args.alpha <= 512
            or not 64 <= args.max_length <= 8192 or args.max_steps == 0 or args.max_steps < -1
            or args.checkpoint_steps < 0 or not 0 <= args.early_stopping_patience <= 20):
        parser.error("Invalid finite epoch, adapter capacity, context or step budget.")
    targets = [target.strip() for target in args.targets.split(",") if target.strip()]
    if not targets or any(not re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", target) for target in targets):
        parser.error("Provide explicit module suffixes for adapter targets.")
    initial = (_initial_adapter_signature(args.initial_adapter, args.model_dir, args.rank, args.alpha, targets)
               if args.initial_adapter else None)
    files = _dataset_files(args.dataset_dir)
    counts = _validate_corpus(files)

    os.environ.update({
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1", "WANDB_DISABLED": "true",
        "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1",
    })
    readiness = _preflight(args.model_dir, counts, args.method, args.activation_offloading)
    input_assets = _input_assets_signature(args.model_dir)
    readiness["input_assets_sha256"] = input_assets
    if initial:
        readiness["initial_adapter"] = initial
        if initial["input_assets_verification"] == "legacy_unrecorded":
            warning = ("The explicitly selected legacy adapter did not record tokenizer/template/system hashes. "
                       "Its historical assets cannot be verified; this new candidate pins the current assets.")
            readiness["warnings"] = [warning]
            print(warning, file=sys.stderr)
    if args.check_only:
        print(json.dumps(readiness, indent=2))
        return 0
    output = Path(args.output_dir).expanduser().resolve()
    signature = {"model_directory": str(args.model_dir), "method": args.method, "epochs": args.epochs,
                 "rank": args.rank, "alpha": args.alpha, "targets": targets, "max_length": args.max_length,
                 "max_steps": args.max_steps, "learning_rate": args.learning_rate,
                 "activation_offloading": args.activation_offloading, "checkpoint_steps": args.checkpoint_steps,
                 "dataset_sha256": {s: hashlib.sha256(Path(p).read_bytes()).hexdigest() for s, p in files.items()},
                 "foundation_sha256": {p.name: _sha256_file(p) for p in sorted(args.model_dir.glob("*.safetensors"))},
                 "model_config_sha256": hashlib.sha256((args.model_dir / "config.json").read_bytes()).hexdigest(),
                 "input_assets_sha256": input_assets}
    if initial is not None or args.early_stopping_patience:
        signature.update(initial_adapter=initial, early_stopping_patience=args.early_stopping_patience)
    _prepare_output(output, signature, args.resume_from_checkpoint)

    import torch
    from datasets import load_dataset
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                              EarlyStoppingCallback, TrainerCallback, set_seed)
    from trl import SFTConfig, SFTTrainer

    set_seed(42)
    corpus = load_dataset("json", data_files=files)
    quantization = None
    cuda = readiness["cuda_available"]
    compute_dtype = torch.bfloat16 if cuda and torch.cuda.is_bf16_supported() else torch.float32
    if args.method == "QLORA":
        quantization = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype, bnb_4bit_use_double_quant=True,
        )
    tokenizer = AutoTokenizer.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("The local tokenizer needs an EOS or padding token.")
        tokenizer.pad_token = tokenizer.eos_token
    corpus = corpus.map(lambda row: _format_pair(row, tokenizer), remove_columns=corpus["train"].column_names)
    # Do not silently discard a correct answer beyond the context boundary.
    # The held-out test is validated for provenance above, never optimized here.
    for split in ("train", "validation"):
        for example in corpus[split]:
            ids = tokenizer(example["prompt"] + example["completion"])["input_ids"]
            if len(ids) + 1 > args.max_length:
                raise ValueError(f"{split} example exceeds the context budget; curate it before training.")
    model = AutoModelForCausalLM.from_pretrained(
        str(args.model_dir), local_files_only=True, trust_remote_code=False,
        dtype=compute_dtype,
        quantization_config=quantization, device_map={"": 0} if cuda else None,
    )
    if quantization is not None:
        model = prepare_model_for_kbit_training(model)
    if initial:
        model = PeftModel.from_pretrained(model, args.initial_adapter, is_trainable=True)
    else:
        model = get_peft_model(model, LoraConfig(
            r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
            target_modules=targets, task_type="CAUSAL_LM", bias="none",
        ))
    model.config.use_cache = False
    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    total = sum(parameter.numel() for parameter in model.parameters())
    if not 0 < trainable < total:
        raise RuntimeError("Expected a frozen foundation and nonempty trainable adapter.")
    config = SFTConfig(
        output_dir=str(output), num_train_epochs=args.epochs, max_steps=args.max_steps,
        per_device_train_batch_size=1, per_device_eval_batch_size=1,
        gradient_accumulation_steps=8, gradient_checkpointing=True,
        eval_strategy="steps" if args.checkpoint_steps else "epoch",
        save_strategy="steps" if args.checkpoint_steps else "epoch",
        eval_steps=args.checkpoint_steps or None, save_steps=args.checkpoint_steps or 500, save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss",
        learning_rate=args.learning_rate, max_length=args.max_length, report_to="none",
        activation_offloading=args.activation_offloading,
        completion_only_loss=True, optim="adamw_torch", use_cpu=not cuda,
        bf16=cuda and torch.cuda.is_bf16_supported(), fp16=False,
        push_to_hub=False,
        logging_steps=1,
    )
    class Progress(TrainerCallback):
        def on_log(self, args_, state, control, logs=None, **kwargs):
            _write_json(output / "progress.json", {
                "updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid(),
                "stage": "training_or_validation", "global_step": state.global_step,
                "max_steps": state.max_steps, "epoch": state.epoch, "metrics": logs or {},
                "training_completed": False})

    callbacks = [Progress()]
    if args.early_stopping_patience:
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=args.early_stopping_patience))
    trainer = SFTTrainer(
        model=model, args=config, processing_class=tokenizer,
        train_dataset=corpus["train"], eval_dataset=corpus["validation"],
        callbacks=callbacks,
    )
    baseline_file = output / "baseline-validation.json"
    if args.resume_from_checkpoint:
        baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    else:
        baseline = trainer.evaluate()
        _write_json(baseline_file, baseline)
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    result = trainer.train(resume_from_checkpoint=str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None)
    training_memory = ({"peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                        "peak_reserved_bytes": torch.cuda.max_memory_reserved()} if cuda else {})
    validation = trainer.evaluate()
    trainer.save_model(str(output))
    _write_json(output / "local-training-metrics.json", {
        "train": result.metrics, "baseline_validation": baseline, "validation": validation,
        "preflight": readiness, "trainable_parameters": trainable, "total_parameters": total,
        "adapter_rank": args.rank, "target_modules": targets,
        "initial_adapter": initial, "early_stopping_patience": args.early_stopping_patience,
        "best_checkpoint": trainer.state.best_model_checkpoint,
        "activation_offloading": args.activation_offloading,
        "base_weight_offloading": False, "training_cuda_memory": training_memory,
        "dataset_sha256": {split: hashlib.sha256(Path(path).read_bytes()).hexdigest() for split, path in files.items()},
        "input_assets_sha256": input_assets,
        "evaluation_scope": "Validation token loss only; held-out task, safety and latency checks still required.",
        "held_out_test_reserved": True, "automatic_promotion": False,
    })
    _write_json(output / "progress.json", {"updated_at": datetime.now(UTC).isoformat(),
                "stage": "training_complete_evaluation_pending", "global_step": trainer.state.global_step,
                "max_steps": trainer.state.max_steps, "epoch": trainer.state.epoch, "training_completed": True})
    return 0


def _sha256_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
