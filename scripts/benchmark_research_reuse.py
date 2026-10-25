from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings
from app.research.knowledge import KnowledgeStore
from app.research.manager import ResearchManager


def main() -> int:
    query = "How does HTTPX configure request timeouts in its current Python API?"
    with tempfile.TemporaryDirectory(prefix="ai-research-reuse-") as directory:
        settings = dict(load_settings()["research"])
        settings["database"] = str(Path(directory) / "knowledge.db")
        manager = ResearchManager(settings, model=None, knowledge=KnowledgeStore(settings["database"]))
        try:
            cold = manager.research(query, "quick", official_only=True, remember=True)
            warm = manager.research(query, "quick", official_only=True, remember=False)
            local = manager.research(query, "local_knowledge_only", official_only=True, remember=False)
        finally:
            manager.close()
    output = {
        "query": query,
        "cold": {"complete": cold.complete, "latency_ms": cold.metrics.total_ms, "searches": cold.metrics.searches, "pages": cold.metrics.pages_retrieved, "stop_reason": cold.stop_reason},
        "warm": {"complete": warm.complete, "latency_ms": warm.metrics.total_ms, "searches": warm.metrics.searches, "cache_hits": warm.metrics.cache_hits, "knowledge_hits": warm.metrics.knowledge_hits, "stop_reason": warm.stop_reason},
        "local_only": {"complete": local.complete, "latency_ms": local.metrics.total_ms, "searches": local.metrics.searches, "stop_reason": local.stop_reason},
        "latency_reduction_percent": round((1 - warm.metrics.total_ms / max(1, cold.metrics.total_ms)) * 100, 2),
    }
    print(json.dumps(output, indent=2))
    return 0 if cold.complete and warm.complete and local.complete and warm.metrics.searches == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
