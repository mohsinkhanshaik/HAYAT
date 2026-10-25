from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.memory.database import MemoryDatabase


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hayat-dna-benchmark-") as temporary:
        memory = MemoryDatabase(Path(temporary) / "dna.db")
        memory.initialize()
        ids = [
            memory.store_memory(
                "episodic", f"Routine event {index} for subsystem {index % 50}.", 0.4, "benchmark"
            )
            for index in range(2000)
        ]
        seed = memory.store_memory(
            "semantic", "Project Atlas uses the Zephyr deployment gate.", 0.95, "benchmark"
        )
        context = memory.store_memory(
            "semantic", "Atlas rollback needs a signed recovery manifest.", 0.95, "benchmark"
        )
        memory.link_memories(seed, context, "depends_on", 0.95, {"benchmark": True})
        # Build a bounded graph away from the target to measure indexed
        # traversal without allowing an unbounded synthetic chain.
        for offset in range(0, 300, 3):
            memory.link_memories(ids[offset], ids[offset + 1], "related", 0.7)
            memory.link_memories(ids[offset + 1], ids[offset + 2], "related", 0.7)

        latencies = []
        correct = 0
        for _ in range(100):
            started = time.perf_counter()
            rows = memory.retrieve("Which deployment gate does Project Atlas use?", limit=8)
            latencies.append((time.perf_counter() - started) * 1000)
            returned = {row["id"] for row in rows}
            correct += int(seed in returned and context in returned)
        report = {
            "memories": 2002,
            "associations": memory.health()["dna_associations"],
            "queries": 100,
            "complete_linked_recall_rate": correct / 100,
            "mean_latency_ms": round(statistics.mean(latencies), 3),
            "p95_latency_ms": round(sorted(latencies)[94], 3),
            "maximum_latency_ms": round(max(latencies), 3),
            "bounded_depth": 2,
            "passed": correct == 100 and sorted(latencies)[94] < 100,
        }
        destination = PROJECT_ROOT / "data" / "benchmarks" / "retrieval" / "dna.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report | {"destination": str(destination)}, indent=2))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
