"""Opt-in live checks of free public job search; isolated storage, no CV or application."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Fetch public APIs without keys or paid services")
    parser.add_argument("--query", default="analyst")
    parser.add_argument("--depth", choices=("normal", "deep", "maximum"), default="deep")
    parser.add_argument("--output", default=".test-runs/job-search-live-verification.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to authorize public network checks. No paid API or application is used.")
    from app.jobs.center import JobCenter
    from app.research.fetcher import PublicFetcher
    fetcher = PublicFetcher("HAYAT public job API verification", timeout=15, delay=.5)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    isolated = path.parent / ("jobs-live-" + uuid.uuid4().hex[:12])
    center = JobCenter(isolated, fetcher)
    try:
        found = center.search(query=args.query, location="Worldwide", force=True, depth=args.depth, limit=500,
                              progress=lambda value: print(json.dumps(value), flush=True))
    finally:
        fetcher.close()
    jobs = found.pop("matches")
    japan = center.matches(limit=500, location="Japan", include_blocked=True)
    report = {"retrieved_at": datetime.now(UTC).isoformat(), "query": args.query, **found,
              "matching_jobs": found["matching_total"], "sample_postings": jobs[:15],
              "japan_matches": japan["matching_total"], "isolated_store": str(isolated),
              "paid_api_used": False, "application_submitted": False,
              "owner_profile_accessed": False}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "matching_jobs": report["matching_jobs"], "sources": report["read"]}), flush=True)
    return 0 if jobs else 1


if __name__ == "__main__":
    raise SystemExit(main())
