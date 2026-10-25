"""Durable local training followed by held-out evaluation, with append-only logs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/models/hayat-open-4b"


def status(stage, **extra):
    value = {"stage": stage, "updated_at": datetime.now(UTC).isoformat(), "supervisor_pid": os.getpid(),
             "local_only": True, "active_hayat_changed": False, **extra}
    temporary = BASE / "run-status.tmp"
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(BASE / "run-status.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-from-checkpoint", type=Path)
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "scripts/train_hayat_adapter.py", "--model-dir", str(BASE / "foundation"),
               "--dataset-dir", str(BASE / "corpus"), "--output-dir", str(BASE / "adapter"),
               "--method", "QLORA", "--epochs", "3", "--learning-rate", "0.00002", "--rank", "16",
               "--alpha", "32", "--targets", "q_proj,v_proj", "--max-length", "1024",
               "--checkpoint-steps", "50", "--activation-offloading"]
    if args.resume_from_checkpoint:
        command += ["--resume-from-checkpoint", str(args.resume_from_checkpoint.resolve())]
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_DATASETS_OFFLINE="1",
                       HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", PYTHONUNBUFFERED="1")
    try:
        with (BASE / "training.log").open("a", encoding="utf-8") as log:
            status("training", command=command, training_completed=False)
            subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
        status("evaluating", training_completed=True)
        with (BASE / "evaluation.log").open("a", encoding="utf-8") as log:
            subprocess.run([sys.executable, "scripts/evaluate_hayat_open.py"], cwd=ROOT, env=environment,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        evaluation = json.loads((BASE / "evaluation.json").read_text(encoding="utf-8"))
        status("completed", training_completed=True, evaluation_completed=True,
               measured_nonregression_passed=evaluation["measured_nonregression_passed"],
               adapter_directory=str(BASE / "adapter"), automatic_promotion=False)
    except Exception as exc:
        status("failed", error=f"{type(exc).__name__}: {exc}",
               training_completed=(BASE / "adapter/local-training-metrics.json").is_file())
        raise


if __name__ == "__main__":
    main()
