"""Fetch and verify the curated public reference pack into local knowledge."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.research.fetcher import PublicFetcher  # noqa: E402
from app.research.knowledge import KnowledgeStore  # noqa: E402
from app.research.official_pack import refresh_official_pack  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "config/official-knowledge.json")
    parser.add_argument("--database", type=Path, default=ROOT / "data/knowledge/knowledge.db")
    parser.add_argument("--report", type=Path, default=ROOT / "data/knowledge/official-pack-report.json")
    args = parser.parse_args()
    entries = json.loads(args.catalog.read_text(encoding="utf-8"))
    store = KnowledgeStore(args.database)
    store.initialize()
    fetcher = PublicFetcher("HAYAT-OfficialReference/1.0", timeout=25)
    # A small batch benefits from independent connections when an upstream
    # proxy leaves a reused documentation connection half open.
    fetcher.client.headers["Connection"] = "close"
    try:
        report = refresh_official_pack(entries, store, fetcher)
    finally:
        fetcher.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if report["failed_sources"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
