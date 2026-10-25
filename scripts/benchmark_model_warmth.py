from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

from app.models.base import Message
from app.models.local import OllamaProvider


def invoke(provider: OllamaProvider) -> dict:
    started = time.perf_counter(); response = provider.generate([Message("user", "Reply with exactly: ready")], temperature=0); elapsed = (time.perf_counter() - started) * 1000
    raw = response.raw; evaluation = float(raw.get("eval_duration") or 0) / 1e9; tokens = int(raw.get("eval_count") or 0)
    return {"latency_ms": round(elapsed, 3), "load_ms": round(float(raw.get("load_duration") or 0) / 1e6, 3), "tokens_per_second": round(tokens / evaluation, 2) if evaluation else None, "valid": "ready" in response.content.lower()}


def main() -> int:
    rows = []
    for model in ("qwen3.5:latest", "llama3:latest"):
        provider = OllamaProvider("http://localhost:11434", model, timeout=180)
        first = invoke(provider); second = invoke(provider)
        rows.append({"model": model, "first_request": first, "warm_request": second, "warm_speedup": round(first["latency_ms"] / max(1, second["latency_ms"]), 3)})
    report = {"models": rows, "note": "First-request load duration is measured from Ollama. Existing runtime state can make the first request partially warm."}
    destination = PROJECT_ROOT / "data/benchmarks/model_routing/warmth.json"; destination.parent.mkdir(parents=True, exist_ok=True); destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report | {"destination": str(destination)}, indent=2)); return 0 if all(row["first_request"]["valid"] and row["warm_request"]["valid"] for row in rows) else 1


if __name__ == "__main__": raise SystemExit(main())
