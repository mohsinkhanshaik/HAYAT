from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime import build_runtime


ROUTING_CASES = [
    ("Hi", "CONVERSATION", {"FAST"}),
    ("What is CUDA?", "QUESTION", {"FAST"}),
    ("What's new in CUDA?", "RESEARCH", {"RESEARCH", "DEEP"}),
    ("Open Notepad.", "COMPUTER_CONTROL", {"COMPUTER", "FAST"}),
    ("What's on my screen?", "COMPUTER_CONTROL", {"VISION", "COMPUTER", "FAST"}),
    ("Fix this Python code.", "CODING", {"CODER", "DEEP"}),
    ("Research this deeply.", "RESEARCH", {"RESEARCH", "DEEP"}),
]


def timed(callback):
    started = time.perf_counter()
    value = callback()
    return value, round((time.perf_counter() - started) * 1000, 3)


def main() -> int:
    runtime = build_runtime(use_model=True)
    rows = []
    try:
        runtime.modes.set("auto")
        for utterance, expected_intent, expected_modes in ROUTING_CASES:
            intent, latency = timed(lambda: runtime.intent_router.route(utterance, runtime.session))
            modes = {mode.value for mode in runtime.modes.resolve(intent)}
            rows.append({"input": utterance, "intent": intent.intent.value, "modes": sorted(modes), "latency_ms": latency, "passed": intent.intent.value == expected_intent and modes == expected_modes})

        router_latencies = []
        for _ in range(100):
            _, latency = timed(lambda: runtime.intent_router.route("Open Calculator", runtime.session))
            router_latencies.append(latency)
        _, memory_latency = timed(lambda: runtime.memory.retrieve("Kubernetes GPU scheduling", limit=5))
        runtime.modes.set("fast")
        fast_response, fast_latency = timed(lambda: runtime.assistant.handle("Explain Docker.").display_response)
        runtime.modes.set("deep")
        deep_response, deep_latency = timed(lambda: runtime.assistant.handle("Explain why GPU utilization can remain low during distributed inference.").display_response)
        research_result, research_latency = timed(lambda: runtime.research.research("current Python stable release", level="fast", official_only=True, remember=False))
        vision_result, vision_latency = timed(lambda: runtime.registry.execute("describe_screen", {"path": "data/artifacts/mode_benchmark_screen.png"})) if "describe_screen" in runtime.registry.names() else (None, None)
        report = {
            "auto_cases": len(rows), "auto_accuracy": sum(row["passed"] for row in rows) / len(rows), "rows": rows,
            "latency_ms": {
                "router_mean": round(statistics.mean(router_latencies), 3), "router_p95": round(sorted(router_latencies)[94], 3),
                "memory_retrieval": memory_latency, "fast_conversation": fast_latency, "deep_conversation": deep_latency,
                "fast_research": research_latency, "vision": vision_latency,
            },
            "evidence": {
                "fast_response_chars": len(fast_response), "fast_response_words": len(fast_response.split()), "deep_response_chars": len(deep_response),
                "research_complete": research_result.complete, "vision_verified": bool(vision_result and vision_result.success),
                "embedding": runtime.embeddings.status(),
            },
        }
        destination = PROJECT_ROOT / "data/benchmarks/modes/after.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key != "rows"}, indent=2))
        print(destination)
        return 0 if report["auto_accuracy"] == 1 and report["evidence"]["research_complete"] else 1
    finally:
        runtime.modes.set("auto")
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
