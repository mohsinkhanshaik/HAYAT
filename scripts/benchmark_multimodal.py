from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime import build_runtime


def timed(handler):
    started = time.perf_counter()
    result = handler()
    return result, round((time.perf_counter() - started) * 1000, 2)


def main() -> int:
    runtime = build_runtime(use_model=False)
    try:
        cost, cost_ms = timed(runtime.cost_policy.status)
        displays, displays_ms = timed(runtime.displays.status)
        camera, camera_ms = timed(runtime.camera.status)
        clipboard, clipboard_ms = timed(runtime.clipboard.status)
        system, system_ms = timed(runtime.observability.system)
        incident, incident_ms = timed(lambda: runtime.incidents.snapshot("system"))
        routes = {}
        for phrase in (
            "What cameras do I have?", "Screenshot the second monitor.",
            "Explain what I copied.", "My Ollama service is down. Diagnose it.",
        ):
            intent = runtime.intent_router.route(phrase, runtime.session)
            routes[phrase] = {"intent": intent.intent.value, "entities": intent.entities, "requires_tools": intent.requires_tools}
        event = runtime.events.publish("TASK_COMPLETED", "benchmark_multimodal", {"secret": "must-redact", "result": "ok", "kind": "multimodal-benchmark"})
        payload = {
            "captured_at": datetime.now(UTC).isoformat(),
            "git_baseline": "c472e42",
            "tools": len(runtime.registry.names()),
            "tool_registration": "model-independent (use_model=False)",
            "cost": cost,
            "camera": camera,
            "displays": displays,
            "clipboard": clipboard,
            "system": system,
            "incident_scope": incident.get("scope"),
            "routes": routes,
            "durable_event": event,
            "latency_ms": {
                "cost": cost_ms, "displays": displays_ms, "camera_status": camera_ms,
                "clipboard_status": clipboard_ms, "system": system_ms, "incident_system": incident_ms,
            },
            "privacy_assertions": {
                "camera_background_recording": camera["background_recording"],
                "camera_raw_retention": camera["raw_retention"],
                "clipboard_content_persisted": clipboard["content_persisted"],
                "paid_api_cost_usd": cost["estimated_api_cost_usd"],
            },
        }
        output = PROJECT_ROOT / "data/benchmarks/multimodal/after.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(json.dumps({"output": str(output), "tools": payload["tools"], "latency_ms": payload["latency_ms"]}, indent=2))
        safe = (
            cost["estimated_api_cost_usd"] == 0
            and not camera["background_recording"]
            and not camera["raw_retention"]
            and not clipboard["content_persisted"]
            and len(displays["displays"]) >= 1
        )
        return 0 if safe else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
