r"""Live invention trial: hard tasks no standard shape solves, run through the real model.

HAYAT's own Ollama (port 11435) is started for the run and stopped afterwards if this started it.
The workbench lives under .test-runs, never the owner's data\invention. Each task is checked first
without a model (work-out only) to show it needs one, then run for real: research off, up to four
cycles, the proposer naming the shape and HAYAT fitting its constants. The best formula is then
graded on held-out cases it never saw.

    .\.venv\Scripts\python.exe -m scripts.live_invention_trial [--cycles 4]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import invention_sim  # noqa: E402

TASKS = [
    {"goal": "Distance of a point from the origin, from its x and y coordinates.",
     "law": lambda x, y: math.hypot(x, y),
     "train": [{"x": 3, "y": 4}, {"x": 5, "y": 12}, {"x": 1, "y": 1}, {"x": 8, "y": 15}, {"x": 2, "y": 7},
               {"x": 9, "y": 2}, {"x": 6, "y": 6}],
     "test": [{"x": 20, "y": 21}, {"x": 0.5, "y": 1.2}], "tolerance": 0.001},
    {"goal": "Shipping price for a parcel of weight w kg: 0.5 per kg, but never less than a minimum charge of 5.",
     "law": lambda w: max(5.0, 0.5 * w),
     "train": [{"w": v} for v in (1, 2, 5, 8, 10, 14, 20, 30)],
     "test": [{"w": 3}, {"w": 50}], "tolerance": 0.001},
    {"goal": "Find the formula behind these measurements of a diffraction pattern's brightness against angle x.",
     "law": lambda x: math.sin(x) / x,
     "train": [{"x": v} for v in (0.5, 1, 1.5, 2, 3, 4, 5, 6)],
     "test": [{"x": 2.5}, {"x": 7}], "tolerance": 0.001},
    {"goal": "Income tax owed on income I: nothing on the first 10,000, then 20 % of everything above it.",
     "law": lambda I: max(0.0, 0.2 * (I - 10000)),
     "train": [{"I": v} for v in (2000, 8000, 10000, 12000, 20000, 35000, 60000)],
     "test": [{"I": 5000}, {"I": 100000}], "tolerance": 0.001},
    {"goal": "Where a thin lens forms the image: image distance from the focal length f and the object distance d "
             "(both in cm, object beyond the focal point).",
     "law": lambda f, d: 1 / (1 / f - 1 / d),
     "train": [{"f": 10, "d": 15}, {"f": 10, "d": 30}, {"f": 5, "d": 20}, {"f": 20, "d": 25}, {"f": 15, "d": 60},
               {"f": 8, "d": 12}, {"f": 25, "d": 100}],
     "test": [{"f": 12, "d": 18}, {"f": 30, "d": 300}], "tolerance": 0.001},
]


def cases(law, points):
    return [{"inputs": point, "expected": format(float(f"{law(**point):.10g}"), ".10g")} for point in points]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cycles", type=int, default=4)
    args = parser.parse_args()
    from app.config import load_yaml
    from app.dual_brain import DualBrain
    from app.executive import ExecutiveCortex
    from app.invention import InventionWorkbench
    from app.launcher import folder_runtime
    from app.models.local import OllamaProvider
    from app.portable import base_url

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    folder = ROOT / ".test-runs" / f"live-invention-{stamp}"
    model_name = load_yaml("config/models.yaml")["conversation_model"]["model"]
    results = []
    with folder_runtime(lambda message: print(message, flush=True)):
        provider = OllamaProvider(base_url(), model_name, timeout=600)
        cortex = ExecutiveCortex()
        bench = InventionWorkbench(folder, cortex, DualBrain(cortex), lambda: provider, None)
        for task in TASKS:
            train, test = cases(task["law"], task["train"]), cases(task["law"], task["test"])
            baseline = invention_sim.work_out(train, task["tolerance"])["best"]
            project = bench.create(task["goal"], tests=train, tolerance=task["tolerance"])
            started = time.perf_counter()
            data = bench.run(project["id"], cycles=args.cycles, research=False)
            seconds = time.perf_counter() - started
            best = data.get("best") or {}
            formula = (best.get("candidate") or {}).get("formula") or ""
            held = invention_sim.grade(formula, test, task["tolerance"]) if formula else {"passed": 0, "total": len(test)}
            rounds = [{"round": r["round"], "method": r.get("method"), "formula": r["candidate"]["formula"],
                       "passed": f"{r['measurement']['passed']}/{r['measurement']['total']}",
                       "fitted": r.get("fitted"), "critic": (r.get("review") or {}).get("verdict")} for r in data["rounds"]]
            row = {"goal": task["goal"], "without_model": {"formula": baseline["formula"],
                                                          "passed": f"{baseline['measurement']['passed']}/{baseline['measurement']['total']}"},
                   "state": data["state"], "stop_reason": data.get("stop_reason"), "best_formula": formula,
                   "best_round": best.get("round"), "best_method": best.get("method"),
                   "held_out": f"{held['passed']}/{held['total']}", "seconds": round(seconds, 1), "rounds": rounds}
            results.append(row)
            print(json.dumps(row, indent=1), flush=True)
    report = {"model": model_name, "endpoint": base_url(), "cycles": args.cycles, "tasks": results,
              "solved": sum(1 for row in results if row["state"] == "TESTED" and row["held_out"].split("/")[0] == row["held_out"].split("/")[1])}
    out = ROOT / ".test-runs" / f"live-invention-{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSolved {report['solved']}/{len(results)} with the model. Report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
