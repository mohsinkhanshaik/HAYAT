"""Explicit one-shot owner-camera person detection; retain diagnostics, no image."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    started = time.perf_counter()
    try:
        result = runtime.registry.execute("camera_people", {})
        report = {"passed": result.success, "result": result.to_dict(), "elapsed_seconds": round(time.perf_counter() - started, 3)}
        report["camera_active_after_inference"] = runtime.camera.capture_backend.active
        if result.success:
            report["passed"] = (result.output.get("raw_frames_retained") is False and result.output.get("path") is None
                                and result.output.get("width", 0) > 0 and result.output.get("height", 0) > 0)
    finally:
        runtime.close()
    report["shutdown_errors"] = runtime.shutdown_errors
    output = ROOT / ".test-runs/camera-person-verification.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] and not report["camera_active_after_inference"] and not runtime.shutdown_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
