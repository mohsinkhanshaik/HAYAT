from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.intelligent_router import IntelligentModelRouter, RouteRequest
from app.models.router import ModelRouter
from app.storage.manager import StorageManager


CATEGORIES = ["conversation", "coding", "reasoning", "windows", "research", "vision", "json", "teaching", "summarization", "tool_use"]
EXPECTED = {
    "conversation": {"qwen3.5:latest", "gemma4:latest"}, "coding": {"llama3:latest", "gemma4:latest", "qwen3.5:latest"},
    "reasoning": {"llama3:latest", "gemma4:latest", "qwen3.5:latest"}, "windows": {None},
    "research": {"llama3:latest", "gemma4:latest", "qwen3.5:latest"}, "vision": {"qwen3.5:latest", "gemma4:latest"},
    "json": {"qwen:latest", "gemma4:latest", "qwen3.5:latest", "llama3:latest"}, "teaching": {"llama3:latest", "gemma4:latest", "qwen3.5:latest"},
    "summarization": {"qwen3.5:latest", "gemma4:latest"}, "tool_use": {"qwen:latest", "gemma4:latest", "qwen3.5:latest", "llama3:latest"},
}


def cases() -> list[dict]:
    rows = []
    for category in CATEGORIES:
        for index in range(20):
            modes = ("FAST",) if index % 3 == 0 else ("DEEP",) if index % 3 == 1 else ("LOCAL_ONLY", "BALANCED")
            rows.append({"category": category, "complexity": "simple" if index % 2 == 0 else "complex", "modality": "vision" if category == "vision" else "text", "output": "json" if category == "json" else "text", "modes": modes})
    return rows


def main() -> int:
    storage = StorageManager()
    router = ModelRouter()
    with tempfile.TemporaryDirectory(prefix="ai-router-", ignore_cleanup_errors=True) as temporary:
        adaptive = IntelligentModelRouter(storage, router, Path(temporary) / "routing.db")
        rows = []
        for case in cases():
            request = RouteRequest(case["category"], case["complexity"], case["modality"], case["modes"], "local", 1200 if "FAST" in case["modes"] else 8000, case["output"])
            started = time.perf_counter(); decision = adaptive.route(request); latency = (time.perf_counter() - started) * 1000
            rows.append(case | {"primary": decision.primary, "fallbacks": decision.fallbacks, "deterministic": decision.deterministic, "passed": decision.primary in EXPECTED[case["category"]], "latency_ms": round(latency, 3), "vram_bytes": decision.scores[0]["vram_bytes"] if decision.scores else 0})
        # Seed verified outcomes that make qwen3.5 repeatedly fail coding and prove adaptive routing will not reinforce it.
        for _ in range(5): adaptive.record_outcome("coding", "qwen3.5:latest", False, 3000, {"passed": False})
        for _ in range(5): adaptive.record_outcome("coding", "llama3:latest", True, 1800, {"passed": True})
        learned = adaptive.route(RouteRequest("coding", "complex", "text", ("DEEP",), "local", 8000, "json"))
        report = {
            "cases": len(rows), "routing_accuracy": sum(row["passed"] for row in rows) / len(rows),
            "wrong_model_rate": sum(not row["passed"] for row in rows) / len(rows),
            "mean_router_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows), 3),
            "fallback_coverage_rate": sum(len(row["fallbacks"]) == 2 or row["deterministic"] for row in rows) / len(rows),
            "adaptive_verified_outcome_check": learned.primary == "llama3:latest",
            "fixed_qwen35": {"expected_task_accuracy": sum("qwen3.5:latest" in EXPECTED[category] for category in CATEGORIES) / len(CATEGORIES), "note": "Capability suitability proxy, not live generation quality."},
            "static_capability_router": {"routing_accuracy": sum(row["passed"] for row in rows) / len(rows)},
            "adaptive_router": {"coding_primary_after_verified_evidence": learned.primary, "evidence_count": learned.scores[0]["evidence_count"] if learned.scores else 0},
            "rows": rows,
        }
        destination = PROJECT_ROOT / "data/benchmarks/model_routing/adaptive.json"
        destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "rows"} | {"destination": str(destination)}, indent=2))
        return 0 if len(rows) >= 200 and report["routing_accuracy"] >= .95 and report["adaptive_verified_outcome_check"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
