from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import PROJECT_ROOT
from app.runtime import build_runtime


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark HAYAT's unified learning audit.")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "data/benchmarks/learning/architecture-benchmark.json",
    )
    args = parser.parse_args()
    iterations = max(2, min(int(args.iterations), 100))
    os.environ["HAYAT_DISABLE_BACKGROUND"] = "1"
    runtime = build_runtime(start_background_services=False)
    try:
        cold: list[float] = []
        warm: list[float] = []
        alignment: list[float] = []
        latest = None
        for _ in range(iterations):
            started = time.perf_counter()
            latest = runtime.learning_architecture.inventory(refresh=True)
            cold.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            runtime.learning_architecture.inventory()
            warm.append((time.perf_counter() - started) * 1000)
            started = time.perf_counter()
            runtime.learning_architecture.alignment()
            alignment.append((time.perf_counter() - started) * 1000)
        aligned = runtime.learning_architecture.alignment()
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "iterations": iterations,
            "cold_inventory_ms": {
                "mean": round(statistics.mean(cold), 3), "p95": round(percentile(cold, .95), 3),
            },
            "cached_inventory_ms": {
                "mean": round(statistics.mean(warm), 4), "p95": round(percentile(warm, .95), 4),
            },
            "cached_alignment_ms": {
                "mean": round(statistics.mean(alignment), 4), "p95": round(percentile(alignment, .95), 4),
            },
            "aligned": aligned["aligned"], "blockers": aligned["blockers"],
            "warnings": aligned["warnings"],
            "records": {
                "durable_memory": latest["layers"]["durable_memory"]["records"],
                "researched_knowledge": latest["layers"]["researched_knowledge"]["knowledge"],
                "teaching_topics": latest["layers"]["teaching_progress"]["topics"],
                "background_learned": latest["layers"]["background_learning"]["queue"].get("learned", 0),
                "trusted_procedures": latest["layers"]["procedural_learning"]["trusted_procedures"],
                "registered_skills": latest["layers"]["procedural_learning"]["registered_skills"],
                "training": latest["layers"]["model_training"]["records"],
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["aligned"] else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
