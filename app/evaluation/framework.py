from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class EvaluationCase:
    category: str
    input: str
    expected: Any


class EvaluationRunner:
    def run(self, cases: list[EvaluationCase], evaluator: Callable[[EvaluationCase], tuple[bool, Any]]) -> dict[str, Any]:
        rows = []
        for case in cases:
            started = time.perf_counter()
            try:
                passed, actual = evaluator(case)
                error = None
            except Exception as exc:
                passed, actual, error = False, None, f"{type(exc).__name__}: {exc}"
            rows.append({**asdict(case), "actual": actual, "passed": passed, "error": error, "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
        categories = sorted({case.category for case in cases})
        metrics = {category: sum(row["passed"] for row in rows if row["category"] == category) / max(1, sum(row["category"] == category for row in rows)) for category in categories}
        return {"cases": len(rows), "accuracy": sum(row["passed"] for row in rows) / max(1, len(rows)), "metrics": metrics, "rows": rows}

    @staticmethod
    def save(report: dict[str, Any], destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return destination
