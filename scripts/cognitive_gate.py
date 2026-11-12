"""Keep an architectural change only when the measurements say it helped (app/cognitive_scorecard.py).

    python scripts/cognitive_gate.py            compare with the accepted baseline; exit 1 on a regression
    python scripts/cognitive_gate.py --accept   after a change that measured better, make it the new baseline
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cognitive_scorecard import BASELINE, accept, benchmarks, compare, load_baseline  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accept", action="store_true", help="write the current measurements as the baseline")
    parser.add_argument("--note", default="", help="why this baseline was accepted")
    args = parser.parse_args()
    current = benchmarks()
    result = compare(load_baseline(), current)
    for row in result["rows"]:
        print(f"{row['verdict']:>7}  {row['metric']:<36} {row.get('before')} -> {row.get('after')}")
    if args.accept:
        if result["regressed"]:
            print("Not accepted: these regressed: " + ", ".join(result["regressed"]))
            return 1
        accept(current, note=args.note)
        print(f"Accepted as the new baseline: {BASELINE}")
        return 0
    print(json.dumps({"passed": result["passed"], "regressed": result["regressed"], "improved": result["improved"]}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
