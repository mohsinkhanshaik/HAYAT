from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from app.training.pipeline import TrainingDataPipeline


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-training-", ignore_cleanup_errors=True) as temporary:
        pipeline = TrainingDataPipeline(Path(temporary) / "training")
        first = pipeline.ingest("routing", {"input": "Hi", "ideal": "CONVERSATION", "api_key": "sk-example-secret-value"}, provenance={"source": "deterministic_golden"})
        validated = pipeline.validate(first["id"], deterministic_correct=True); promoted = pipeline.promote(first["id"])
        duplicate = pipeline.ingest("routing", {"input": "Hi", "ideal": "CONVERSATION", "api_key": "different-secret"}, provenance={"source": "duplicate_probe"})
        rejected = pipeline.ingest("conversation", {"input": "What model?", "bad": "I invent one"}, provenance={"source": "failure"})
        pipeline.validate(rejected["id"], reject_reason="Unsupported operational claim")
        for index in range(60):
            record = pipeline.ingest("planning", {"input": f"Verified plan case {index}", "ideal_plan": ["observe", "act", "verify"], "token": f"ghp_{'x' * 24}"}, provenance={"source": "deterministic_benchmark", "case": index})
            pipeline.validate(record["id"], verification={"passed": True}); pipeline.promote(record["id"])
        status = pipeline.status(); validation = pipeline.validate_dataset()
        result = {"raw_created": first["state"] == "RAW", "validated_state": validated["state"], "promoted_state": promoted["state"], "dedupe": duplicate["duplicate"], "redactions_first_record": first["redactions"], "rejected": status["states"]["REJECTED"], "splits": status["dataset"]["records"], "validation": validation, "training_execution_started": False, "passed": validated["state"] == "VALIDATED" and first["redactions"] == 1 and duplicate["duplicate"] and validation["passed"] and status["states"]["REJECTED"] == 1 and status["states"]["HELD_OUT"] > 0}
        destination = PROJECT_ROOT / "data/benchmarks/training/pipeline.json"; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result | {"destination": str(destination)}, indent=2)); return 0 if result["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
