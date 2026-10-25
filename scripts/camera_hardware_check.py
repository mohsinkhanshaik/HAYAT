from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2

from app.perception.camera.manager import CameraManager


def main() -> int:
    output = PROJECT_ROOT / "data/benchmarks/camera/hardware.json"; output.parent.mkdir(parents=True, exist_ok=True)
    manager = CameraManager({"device": 0, "width": 1280, "height": 720, "retain_frames": False})
    report = {"started_at": datetime.now(UTC).isoformat(), "devices": manager.devices(), "privacy": {"retention": False, "background_recording": False}}
    path = PROJECT_ROOT / "data/camera/hardware-check.jpg"
    started = time.perf_counter()
    try:
        opened = manager.on(0)
        frame = manager.capture(str(path.relative_to(PROJECT_ROOT)), retain=True)
        image = cv2.imread(str(path))
        if image is None:
            raise RuntimeError("The captured frame could not be decoded.")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        released = manager.off()
        report.update({
            "passed": bool(frame["width"] > 0 and frame["height"] > 0 and gray.size > 0 and released["device_released"]),
            "opened": opened, "frame": {"width": frame["width"], "height": frame["height"], "bytes": frame["bytes"], "mean_luma": round(float(gray.mean()), 3), "stddev_luma": round(float(gray.std()), 3)},
            "released": released, "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        })
    except Exception as exc:
        report.update({"passed": False, "error": f"{type(exc).__name__}: {exc}", "latency_ms": round((time.perf_counter() - started) * 1000, 3)})
    finally:
        manager.close(); path.unlink(missing_ok=True)
        report["raw_frame_exists_after_test"] = path.exists(); report["finished_at"] = datetime.now(UTC).isoformat()
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report.get("passed") and not report["raw_frame_exists_after_test"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
