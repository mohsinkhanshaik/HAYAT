"""Small reproducibility records for local laboratories; never raw private data."""
from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str, allow_nan=False).encode("utf-8")).hexdigest()


def environment(*packages: str) -> dict[str, Any]:
    installed = {}
    for package in packages:
        try:
            installed[package] = version(package)
        except PackageNotFoundError:
            installed[package] = "not installed"
    return {"python": platform.python_version(), "platform": sys.platform, "packages": installed}


def compare_metric(current: dict[str, Any], previous: dict[str, Any] | None, *,
                   field: str = "metric", direction: str = "higher") -> dict[str, Any]:
    """Compare matching protocols/inputs only; repeated tests are not independent confirmation."""
    if previous is None:
        return {"comparable": False, "reason": "No earlier run for this input."}
    key = (current.get("evaluation") or {}).get("comparison_key")
    if not key or key != (previous.get("evaluation") or {}).get("comparison_key"):
        return {"comparable": False, "reason": "The input, validation split, environment, or evaluation protocol differs or was not recorded."}
    before, after = previous.get(field), current.get(field)
    if (not isinstance(before, (int, float)) or not isinstance(after, (int, float))
            or not math.isfinite(before) or not math.isfinite(after)):
        return {"comparable": False, "reason": "Both runs need a finite measured score."}
    delta = float(after) - float(before)
    improved = delta > 0 if direction == "higher" else delta < 0
    return {"comparable": True, "before": before, "after": after, "delta": round(delta, 8),
            "direction": direction, "change": "unchanged" if delta == 0 else "improved" if improved else "worse",
            "limits": "Descriptive comparison on the same recorded inputs; not statistical significance or independent confirmation."}
