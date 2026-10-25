"""Cheap checkpoint inspection; no imports of the optional training stack."""
from __future__ import annotations

import json
from pathlib import Path


def checkpoint_report(directory: str | Path | None) -> dict:
    root = Path(directory).expanduser().resolve() if directory else None
    problems, weights, config = [], [], {}
    if root is None or not root.is_dir():
        problems.append("Matching local Hugging Face checkpoint is missing.")
    else:
        try:
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            if not isinstance(config, dict) or not isinstance(config.get("model_type"), str):
                raise ValueError("missing model_type")
            index = root / "model.safetensors.index.json"
            if index.is_file():
                mapping = json.loads(index.read_text(encoding="utf-8")).get("weight_map")
                if not isinstance(mapping, dict) or not mapping:
                    raise ValueError("empty weight_map")
                names = set(mapping.values())
                if any(not isinstance(name, str) or Path(name).name != name
                       or not name.endswith(".safetensors") for name in names):
                    raise ValueError("invalid shard path")
                weights = [root / name for name in sorted(names)]
            elif (root / "model.safetensors").is_file():
                weights = [root / "model.safetensors"]
            if not weights or any(not path.is_file() or path.stat().st_size < 8 for path in weights):
                problems.append("Safetensors weights or declared shards are missing.")
            if not any((root / name).is_file() for name in ("tokenizer.json", "tokenizer.model")):
                problems.append("Local tokenizer files are missing.")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            problems.append(f"Checkpoint metadata is invalid ({type(exc).__name__}).")
    return {"ready": not problems, "path": str(root) if root else None,
            "model_type": config.get("model_type") if isinstance(config, dict) else None,
            "weight_bytes": sum(path.stat().st_size for path in weights if path.is_file()),
            "weight_files": len(weights), "problems": problems,
            "inspection_scope": "File and metadata preflight; loading and foundation identity remain to be checked."}


def explain_gate(gate: dict) -> str:
    """The exact conversation corpus, not all examples pooled together."""
    counts = gate.get("split_counts") or {}
    scope = "private local" if gate.get("privacy_scope") == "PRIVATE_LOCAL" else "standard"
    state = "Data and checkpoint preflight passed" if gate.get("ready") else "Fine-tuning is blocked"
    lines = [f"{state} · {gate.get('profile', 'conversation')} · {scope}",
             f"Approved: {gate.get('records', 0)} / {gate.get('minimum_validated_samples', 1000)} · "
             f"train {counts.get('train', 0)} · validation {counts.get('validation', 0)} · test {counts.get('test', 0)}"]
    labels = {"minimum_samples": "More verified examples needed", "training_split": "Training split missing",
              "validation_split": "Validation split missing", "held_out_test": "Held-out test missing",
              "local_trainable_checkpoint": "Matching trainable checkpoint missing or incomplete",
              "dependencies": "Training libraries missing", "dataset_validation": "Corpus validation failed",
              "private_local_scope": "This lifecycle requires a private local corpus"}
    if gate.get("blockers"):
        lines.append("; ".join(labels.get(key, key.replace("_", " ")) for key in gate["blockers"]))
    lines.append("Training changes weights. The 3D mesh and collected examples do not. Hardware is checked before training.")
    return "\n".join(lines)
