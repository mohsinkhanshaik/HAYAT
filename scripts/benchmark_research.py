from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings
from app.research.manager import ResearchManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure direct retrieval versus the bounded cited research pipeline.")
    parser.add_argument("--query", default="current Python 3 documentation supported versions")
    parser.add_argument("--reference-url", default="https://docs.python.org/3/")
    parser.add_argument("--iterations", type=int, default=3)
    args = parser.parse_args()
    direct_ms: list[int] = []
    research_ms: list[int] = []
    source_counts: list[int] = []
    failures = 0
    manager = ResearchManager(load_settings()["research"], model=None)
    try:
        for _ in range(max(1, min(args.iterations, 10))):
            started = time.perf_counter()
            response = httpx.get(args.reference_url, timeout=20, follow_redirects=True)
            response.raise_for_status()
            direct_ms.append(int((time.perf_counter() - started) * 1000))
            result = manager.research(args.query, "fast", official_only=True, remember=False)
            research_ms.append(result.metrics.total_ms)
            source_counts.append(len(result.sources))
            failures += result.metrics.failures + (0 if result.complete else 1)
    finally:
        manager.close()
    report = {
        "iterations": len(direct_ms),
        "direct_single_page_ms": {"median": int(statistics.median(direct_ms)), "min": min(direct_ms), "max": max(direct_ms)},
        "full_research_ms": {"median": int(statistics.median(research_ms)), "min": min(research_ms), "max": max(research_ms)},
        "sources_per_run": source_counts,
        "failures": failures,
        "note": "The full path does provider search, bounded multi-page retrieval, extraction, verification, and citation generation; it is not expected to beat one direct GET.",
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
