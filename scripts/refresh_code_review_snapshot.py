"""Record that the flagged lines of named files were read again, after those files changed.

A line review in app/code_review.py covers a finding only while the file's fingerprint matches
app/code_review_snapshot.json, so editing a reviewed file withdraws its reviews (tests/test_code_review.py fails).
After a person has re-read every medium/high finding in the file, this records the file's new fingerprint and
those findings. It cannot widen a review: a finding is recorded only when a line review already names its rule for
that path, so a new rule or a new file still needs an entry in app/code_review.py first.

    python scripts/refresh_code_review_snapshot.py app/memory/database.py [more files]

It prints every finding it records, so the reading can be checked against the list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.code_defense import bandit_findings  # noqa: E402
from app.code_review import REVIEWS, _source_digest  # noqa: E402

SNAPSHOT = ROOT / "app" / "code_review_snapshot.json"


def _key(rule: str, path: str, code: str) -> str:
    return hashlib.sha256(f"{rule}|{path}|{' '.join(code.split())}".encode()).hexdigest()


def _line_review(rule: str, path: str, code: str) -> bool:
    return any(review.rule == rule and review.depth == "line" and any(fnmatch(path, pattern) for pattern in review.paths)
               and (not review.contains or review.contains in code) for review in REVIEWS)


def refresh(files: list[str]) -> dict:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {"version": 1}
    snapshot.setdefault("version", 1)
    findings, digests = snapshot.setdefault("findings", {}), snapshot.setdefault("files", {})
    report: dict = {"recorded": [], "not_covered": []}
    for path in [item.replace("\\", "/") for item in files]:
        # The file's old entries go: only what is in it now, and was read, is recorded.
        for key in [key for key, value in findings.items() if value.get("path") == path]:
            del findings[key]
        for item in bandit_findings(ROOT, [path]):
            if item["severity"] not in ("MEDIUM", "HIGH"):
                continue
            target = report["recorded"] if _line_review(item["rule"], path, item["evidence"]) else report["not_covered"]
            target.append(f"{item['rule']} {path}:{item['line']}")
            if target is report["recorded"]:
                findings[_key(item["rule"], path, item["evidence"])] = {"rule": item["rule"], "path": path}
        digest = _source_digest(path)
        if digest:
            digests[path] = digest
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", help="project-relative paths whose flagged lines were re-read")
    report = refresh(parser.parse_args().files)
    for line in report["recorded"]:
        print("recorded  ", line)
    for line in report["not_covered"]:
        print("NOT COVERED (add a review to app/code_review.py first)", line)
    return 1 if report["not_covered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
