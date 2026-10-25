from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_settings
from app.research.manager import ResearchManager


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def mean(values: list[float]) -> float:
    return round(statistics.mean(values), 4) if values else 0.0


def citation_numbers(answer: str) -> list[int]:
    body = answer.split("\n\nSources:\n", 1)[0]
    return [int(value) for value in re.findall(r"\[(\d+)\]", body)]


def score_case(case: dict, result) -> dict:
    evidence = " ".join([result.answer, *[source.text[:10_000] for source in result.sources]]).lower()
    expected = [value.lower() for value in case.get("expected_any", [])]
    correctness = 1.0 if any(value in evidence for value in expected) else 0.0
    numbers = citation_numbers(result.answer)
    valid_numbers = [number for number in numbers if 1 <= number <= len(result.sources)]
    source_by_url = {source.final_url: source for source in result.sources}
    presented_claims = [claim for claim in result.claims if claim.text in result.answer]
    evidence = [item for claim in presented_claims for item in claim.evidence]
    validated = [item for item in evidence if getattr(item.support_type, "value", item.support_type) != "UNSUPPORTED" and item.source_url in source_by_url and (not item.source_hash or item.source_hash == source_by_url[item.source_url].content_hash)]
    index_integrity = len(valid_numbers) / max(1, len(numbers)) if numbers else 0.0
    evidence_integrity = len(validated) / max(1, len(evidence)) if evidence else (1.0 if result.stop_reason == "fresh_local_knowledge" and numbers else 0.0)
    citation_correctness = index_integrity * evidence_integrity
    supported_claims = [claim for claim in presented_claims if getattr(claim.status, "value", claim.status) in {"SUPPORTED", "PARTIALLY_SUPPORTED", "DISPUTED"} and claim.evidence]
    answer_bullets = [line for line in result.answer.splitlines() if line.startswith("- ")]
    cited_bullets = [line for line in answer_bullets if re.search(r"\[\d+\]", line)]
    citation_completeness = len(cited_bullets) / max(1, len(answer_bullets)) if answer_bullets else (1.0 if result.stop_reason == "fresh_local_knowledge" and numbers else 0.0)
    preferred = set(case.get("preferred_domains", []))
    source_quality = mean([source.quality for source in result.sources])
    preferred_ratio = mean([1.0 if any(source.domain == domain or source.domain.endswith("." + domain) for domain in preferred) else 0.0 for source in result.sources])
    relevance_flags = [1.0 if any(source.domain == domain or source.domain.endswith("." + domain) for domain in preferred) else 0.0 for source in result.sources]
    precision_at_3 = sum(relevance_flags[:3]) / max(1, min(3, len(relevance_flags)))
    recall_proxy = 1.0 if any(relevance_flags) else 0.0
    preferred_rank = next((index for index, value in enumerate(relevance_flags, 1) if value), 0)
    reciprocal_rank = 1 / preferred_rank if preferred_rank else 0.0
    domains = {source.domain for source in result.sources}
    source_diversity = min(1.0, len(domains) / max(1, min(3, len(result.sources))))
    dated = [source for source in result.sources if source.published_at or source.updated_at]
    freshness_coverage = len(dated) / max(1, len(result.sources))
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "complete": result.complete,
        "correctness": correctness,
        "citation_correctness": round(citation_correctness, 4),
        "citation_completeness": round(citation_completeness, 4),
        "source_quality": source_quality,
        "preferred_source_ratio": preferred_ratio,
        "retrieval_precision_at_3": round(precision_at_3, 4),
        "retrieval_recall_proxy": recall_proxy,
        "retrieval_mrr": round(reciprocal_rank, 4),
        "source_diversity": round(source_diversity, 4),
        "freshness_coverage": round(freshness_coverage, 4),
        "confidence": result.confidence,
        "latency_ms": result.metrics.total_ms,
        "searches": result.metrics.searches,
        "pages_opened": result.metrics.pages_retrieved,
        "model_calls": getattr(result.metrics, "model_calls", 0),
        "duplicates": result.metrics.duplicates,
        "cache_hits": getattr(result.metrics, "cache_hits", 0),
        "knowledge_hits": getattr(result.metrics, "knowledge_hits", 0),
        "independent_sources": getattr(result.metrics, "independent_sources", 0),
        "validated_claims": len(supported_claims),
        "sources": [{"url": source.final_url, "quality": source.quality, "published_at": source.published_at, "updated_at": source.updated_at} for source in result.sources],
        "stop_reason": result.stop_reason,
        "errors": result.errors,
    }


def summarize(rows: list[dict]) -> dict:
    return {
        "cases": len(rows),
        "completion_rate": mean([float(row["complete"]) for row in rows]),
        "answer_correctness": mean([row["correctness"] for row in rows]),
        "citation_correctness": mean([row["citation_correctness"] for row in rows]),
        "citation_completeness": mean([row["citation_completeness"] for row in rows]),
        "source_quality": mean([row["source_quality"] for row in rows]),
        "preferred_source_ratio": mean([row["preferred_source_ratio"] for row in rows]),
        "source_diversity": mean([row["source_diversity"] for row in rows]),
        "freshness_coverage": mean([row["freshness_coverage"] for row in rows]),
        "retrieval_precision_at_3": mean([row["retrieval_precision_at_3"] for row in rows]),
        "retrieval_recall_proxy": mean([row["retrieval_recall_proxy"] for row in rows]),
        "retrieval_mrr": mean([row["retrieval_mrr"] for row in rows]),
        "median_latency_ms": int(statistics.median([row["latency_ms"] for row in rows])) if rows else 0,
        "mean_searches": mean([row["searches"] for row in rows]),
        "mean_pages_opened": mean([row["pages_opened"] for row in rows]),
        "model_calls": sum(row["model_calls"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=["before", "after"], required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--cases", type=Path, default=PROJECT_ROOT / "data/benchmarks/research_quality/cases.json")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]
    manager = ResearchManager(load_settings()["research"], model=None)
    rows: list[dict] = []
    try:
        for case in cases:
            if case["mode"] == "local_knowledge_only":
                if args.label == "after":
                    manager.research(case["query"], "quick", case.get("primary_only", False), remember=True)
                    result = manager.research(case["query"], "local_knowledge_only", case.get("primary_only", False), remember=False)
                else:
                    result = manager.research(case["query"], "fast", case.get("primary_only", False), remember=False)
            else:
                level = "fast" if case["mode"] == "quick" else "normal"
                result = manager.research(case["query"], level, case.get("primary_only", False), remember=False)
            row = score_case(case, result)
            rows.append(row)
            print(f"{case['id']}: complete={row['complete']} correctness={row['correctness']:.2f} citations={row['citation_correctness']:.2f} {row['latency_ms']}ms")
    finally:
        manager.close()
    report = {"label": args.label, "created_at": datetime.now(UTC).isoformat(), "summary": summarize(rows), "cases": rows}
    output = PROJECT_ROOT / f"data/benchmarks/research_quality/{args.label}.json"
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
