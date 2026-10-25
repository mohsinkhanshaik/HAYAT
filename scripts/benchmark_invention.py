"""Hard invention benchmark: can HAYAT work out real laws from measured cases, without a model?

Each task is a known law from physics, engineering or finance, sampled at a handful of inputs (some
with measurement noise), with a tolerance like a lab would use. HAYAT's work-out (app/invention_sim.py)
must find a formula that passes every case AND predicts cases it never saw: each task also has
held-out cases, graded after the fit, so memorising the supplied cases does not count.

    .\\.venv\\Scripts\\python.exe -m scripts.benchmark_invention [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import invention_sim  # noqa: E402


def _cases(law: Callable[..., float], inputs: list[dict[str, float]], noise: float = 0.0, seed: int = 1) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    for values in inputs:
        value = law(**values)
        if noise:
            value *= 1 + rng.uniform(-noise, noise)
        rows.append({"inputs": {k: _tidy(v) for k, v in values.items()}, "expected": _tidy(value)})
    return rows


def _tidy(value: float) -> str:
    return format(float(f"{value:.10g}"), ".10g")


def _grid(**ranges: list[float]) -> list[dict[str, float]]:
    """Points spread over the ranges without being a full grid (keeps cases few, like real measurements)."""
    names = list(ranges)
    count = max(len(values) for values in ranges.values())
    return [{name: ranges[name][(index * (position + 1) + position) % len(ranges[name])] for position, name in enumerate(names)}
            for index in range(count)]


G = 6.674e-11
TASKS: list[dict[str, Any]] = [
    {"name": "pendulum period", "law": lambda L: 2 * math.pi * math.sqrt(L / 9.81),
     "train": [{"L": v} for v in (0.25, 0.5, 1, 2, 4)], "test": [{"L": 0.1}, {"L": 8}], "tolerance": 0.001},
    {"name": "kinetic energy", "law": lambda m, v: 0.5 * m * v ** 2,
     "train": _grid(m=[1, 2, 5, 10, 20, 50], v=[1, 3, 5, 10, 20, 30]), "test": [{"m": 7, "v": 12}, {"m": 80, "v": 2}],
     "tolerance": 0.001},
    {"name": "electrical power", "law": lambda V, R: V ** 2 / R,
     "train": _grid(V=[1, 5, 12, 24, 120, 230], R=[1, 2, 10, 47, 100, 1000]), "test": [{"V": 9, "R": 3}, {"V": 400, "R": 50}],
     "tolerance": 0.001},
    {"name": "gravitation (tiny constant)", "law": lambda m1, m2, r: G * m1 * m2 / r ** 2,
     "train": _grid(m1=[5.97e24, 7.35e22, 1000, 70, 1.99e30, 6.4e23, 1.9e27],
                    m2=[70, 1000, 7.35e22, 5.97e24, 1, 500, 3e3],
                    r=[6.37e6, 3.84e8, 1.5e11, 1, 2, 7e5, 1e7]),
     "test": [{"m1": 5.97e24, "m2": 1, "r": 4.2e7}], "tolerance": 0.001},
    {"name": "ideal gas (3 inputs)", "law": lambda n, T, V: n * 8.314 * T / V,
     "train": _grid(n=[0.5, 1, 2, 3, 5, 8, 10], T=[250, 273, 300, 350, 400, 500, 600], V=[0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1]),
     "test": [{"n": 4, "T": 310, "V": 0.03}], "tolerance": 0.001},
    {"name": "drag force (4 inputs)", "law": lambda rho, v, Cd, A: 0.5 * rho * v ** 2 * Cd * A,
     "train": _grid(rho=[1.2, 1.0, 0.9, 1.1, 1.25, 1000, 1.3, 0.8], v=[5, 10, 20, 30, 40, 2, 25, 15],
                    Cd=[0.3, 0.5, 0.82, 1.0, 1.2, 0.47, 0.25, 0.6], A=[0.5, 1, 2, 2.5, 0.1, 0.01, 3, 1.5]),
     "test": [{"rho": 1.2, "v": 33, "Cd": 0.29, "A": 2.2}], "tolerance": 0.001},
    {"name": "Kepler's third law", "law": lambda a: a ** 1.5,
     "train": [{"a": v} for v in (0.39, 0.72, 1.0, 1.52, 5.2)], "test": [{"a": 9.58}, {"a": 30.1}], "tolerance": 0.001},
    {"name": "radioactive decay", "law": lambda N0, t: N0 * math.exp(-0.0866 * t),
     "train": _grid(N0=[100, 500, 1000, 50, 2000, 300], t=[0, 2, 5, 8, 12, 20]), "test": [{"N0": 800, "t": 30}],
     "tolerance": 0.001},
    {"name": "compound interest", "law": lambda P, r, n: P * (1 + r) ** n,
     "train": _grid(P=[1000, 500, 2500, 10000, 750, 5000, 200], r=[0.03, 0.05, 0.07, 0.1, 0.04, 0.12, 0.02],
                    n=[1, 5, 10, 3, 20, 7, 30]),
     "test": [{"P": 1200, "r": 0.06, "n": 15}], "tolerance": 0.0001},
    {"name": "battery with internal resistance", "law": lambda V, R: V / (R + 0.5),
     "train": _grid(V=[1.5, 3, 9, 12, 24, 6], R=[0.5, 1, 2, 5, 10, 20]), "test": [{"V": 12, "R": 0.1}, {"V": 5, "R": 100}],
     "tolerance": 0.001},
    {"name": "projectile height", "law": lambda v, t: v * t - 4.905 * t ** 2,
     "train": _grid(v=[10, 15, 20, 25, 30, 12, 18, 22], t=[0.5, 1, 1.5, 2, 2.5, 0.8, 1.2, 3]),
     "test": [{"v": 40, "t": 4}], "tolerance": 0.001},
    {"name": "logistic growth", "law": lambda t: 1000 / (1 + 9 * math.exp(-0.5 * t)),
     "train": [{"t": v} for v in (0, 2, 4, 6, 8, 10, 14)], "test": [{"t": 5}, {"t": 20}], "tolerance": 0.005},
    {"name": "noisy Ohm's law (2 % noise)", "law": lambda V: V / 47,
     "train": [{"V": v} for v in (1, 2, 4, 6, 8, 10, 12)], "test": [{"V": 20}], "tolerance": 0.05, "noise": 0.02},
    {"name": "noisy cooling (1 % noise)", "law": lambda t: 20 + 70 * math.exp(-0.05 * t),
     "train": [{"t": v} for v in (0, 5, 10, 20, 30, 45, 60)], "test": [{"t": 90}], "tolerance": 0.03, "noise": 0.01},
    {"name": "beam deflection (4 inputs)", "law": lambda F, L, E, I: F * L ** 3 / (3 * E * I),
     "train": _grid(F=[100, 500, 1000, 2000, 50, 800, 300, 1500], L=[1, 2, 3, 4, 0.5, 2.5, 1.5, 5],
                    E=[2e11, 7e10, 1e10, 2e11, 1.1e11, 7e10, 2e11, 1e10], I=[1e-6, 2e-6, 5e-6, 1e-5, 3e-6, 8e-6, 4e-6, 2e-5]),
     "test": [{"F": 1200, "L": 3.5, "E": 2e11, "I": 6e-6}], "tolerance": 0.001},
    {"name": "damped oscillation", "law": lambda t: math.exp(-0.3 * t) * math.cos(2 * t),
     "train": [{"t": v} for v in (0, 0.5, 1, 1.5, 2, 2.5, 3, 4, 5)], "test": [{"t": 3.5}], "tolerance": 0.01},
]


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    train = _cases(task["law"], task["train"], task.get("noise", 0.0))
    test = _cases(task["law"], task["test"])
    started = time.perf_counter()
    try:
        found = invention_sim.work_out(train, task["tolerance"])
    except ValueError as exc:
        return {"task": task["name"], "solved": False, "error": str(exc), "seconds": round(time.perf_counter() - started, 2)}
    seconds = time.perf_counter() - started
    best = found["best"]
    held = invention_sim.grade(best["formula"], test, task["tolerance"])
    # With measurement noise, held-out cases are judged against the true law within the tolerance.
    passed_train = best["measurement"]["passed"] == best["measurement"]["total"]
    passed_test = held["passed"] == held["total"]
    return {"task": task["name"], "solved": passed_train and passed_test and not best["too_flexible"],
            "shape": best["description"], "formula": best["formula"], "train": f"{best['measurement']['passed']}/{best['measurement']['total']}",
            "held_out": f"{held['passed']}/{held['total']}", "held_out_error": held["mean_relative_error"],
            "shapes_tried": found["shapes_tried"], "seconds": round(seconds, 2)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    rows = [run_task(task) for task in TASKS]
    for row in rows:
        mark = "SOLVED" if row["solved"] else "missed"
        print(f"{mark:7} {row['task']:34} {row.get('train', ''):6} held-out {row.get('held_out', ''):5} "
              f"{row.get('seconds', 0):6.2f}s  {row.get('formula') or row.get('error')}")
    solved = sum(row["solved"] for row in rows)
    print(f"\n{solved}/{len(rows)} solved without a model")
    if args.out:
        args.out.write_text(json.dumps({"solved": solved, "tasks": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
