"""Display separate public adapter evidence without altering private approval gates."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from app.config import PROJECT_ROOT


def public_training_summary(root: Path = PROJECT_ROOT) -> str:
    base = root / "data/models/hayat-open-4b"
    metrics = base / "adapter/local-training-metrics.json"
    if not metrics.is_file():
        return "Public HAYAT Open 4B: no completed local training evidence."
    try:
        previous = json.loads(metrics.read_text(encoding="utf-8"))
        train = previous["train"]
        steps = round(float(train.get("train_steps", 0)))
        epochs = float(train.get("epoch", 0))
        # Trainer records epoch in the saved progress even when the final
        # train metrics omit the optimizer step count.
        progress = base / "adapter/progress.json"
        if progress.is_file():
            completed = json.loads(progress.read_text(encoding="utf-8"))
            steps = completed.get("global_step", steps)
            epochs = completed.get("epoch", epochs)
        text = f"Public HAYAT Open 4B: completed {epochs:g} epochs / {steps} optimizer steps."
        candidate = base / "adapter-v2/progress.json"
        if candidate.is_file():
            status = json.loads(candidate.read_text(encoding="utf-8"))
            if status.get("training_completed"):
                text += f" Continuation trained {status['global_step']} steps."
                evaluation = base / "evaluation-v2.json"
                if evaluation.is_file():
                    result = json.loads(evaluation.read_text(encoding="utf-8"))
                    with (base / "adapter-v2/adapter_model.safetensors").open("rb") as weights:
                        digest = hashlib.file_digest(weights, "sha256").hexdigest()
                    if not result.get("training_completed") or result.get("candidate_adapter_sha256") != digest:
                        text += " Held-out evidence does not match these weights; evaluation required."
                    elif result.get("measured_nonregression_passed"):
                        text += " Limited held-out nonregression gate passed."
                    else:
                        text += " Held-out gate failed; retain the previous adapter."
                else:
                    text += " Inspect its held-out evaluation before use."
            else:
                text += f" Continuation reports step {status['global_step']} / {status['max_steps']} (updated {status['updated_at']})."
        return text + " Public training does not approve private records or train the 35B GGUF model."
    except (OSError, ValueError, TypeError, KeyError):
        return "Public HAYAT Open 4B: training evidence unavailable; inspect local reports."
