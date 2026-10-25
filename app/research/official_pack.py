"""Refresh curated reference facts against their original public documentation.

This adds retrievable knowledge; it never trains weights or executes page text.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from app.research.extractor import ContentExtractor
from app.research.fetcher import PublicFetcher
from app.research.knowledge import KnowledgeStore
from app.research.models import Claim, ClaimStatus, Evidence, ResearchMetrics, ResearchResult
from app.research.verifier import EvidenceVerifier


OFFICIAL_HOSTS = frozenset({
    "docs.ollama.com", "docs.python.org", "www.python.org", "www.sqlite.org", "sqlite.org",
    "support.microsoft.com",
})


def _official_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https" and not parts.username and not parts.password
        and parts.port in {None, 443}
        and (
            parts.hostname in OFFICIAL_HOSTS
            or (parts.hostname == "huggingface.co" and parts.path.startswith("/docs/"))
            or (parts.hostname == "science.nasa.gov" and parts.path.rstrip("/") in
                {"/earth/facts", "/mars/facts", "/moon/facts"})
            or (parts.hostname == "github.com" and parts.path.rstrip("/") == "/SYSTRAN/faster-whisper")
        )
    )


def refresh_official_pack(
    entries: list[dict[str, Any]], store: KnowledgeStore, fetcher: PublicFetcher,
) -> dict[str, Any]:
    """Import only entries whose complete curated claim set still verifies.

    Failures are isolated per source and returned for review. A refresh cannot
    extend freshness for an entry that failed verification.
    """
    extractor, verifier = ContentExtractor(), EvidenceVerifier()
    report: dict[str, Any] = {"imported_sources": 0, "supported_claims": 0, "entries": []}
    for entry in entries:
        url = str(entry.get("url", ""))
        outcome: dict[str, Any] = {"url": url, "topic": str(entry.get("topic", ""))}
        try:
            if not _official_url(url):
                raise ValueError("Source is outside the curated official-source allowlist")
            topic = str(entry["topic"]).strip()
            facts = entry["facts"]
            days = int(entry.get("refresh_days", 7))
            if not topic or not facts or not 1 <= days <= 30:
                raise ValueError("A topic, facts, and a 1–30 day refresh interval are required")
            resource = fetcher.fetch(url, max_bytes=4 * 1024 * 1024)
            if not _official_url(resource.final_url) or urlsplit(url).hostname != urlsplit(resource.final_url).hostname:
                raise ValueError("Source redirected outside its official host")
            source, _ = extractor.extract(resource, query=topic)
            # Primary describes authorship. Confidence remains the verifier's
            # heuristic, not a claim of infallibility or an accuracy score.
            source.primary = True
            source.independence_group = source.domain
            source.metadata["curation"] = "official-reference-pack"
            retrieved = datetime.fromisoformat(source.retrieved_at)
            if retrieved.tzinfo is None:
                raise ValueError("Retrieval timestamp must include its timezone")
            deadline = (retrieved + timedelta(days=days)).isoformat()
            claims = []
            for fact in facts:
                statement, excerpt = str(fact["claim"]).strip(), str(fact["excerpt"]).strip()
                if not statement or not excerpt:
                    raise ValueError("Claim and source excerpt must be nonempty")
                claim_id = hashlib.sha256(f"{url}\n{statement}".encode()).hexdigest()[:24]
                evidence = Evidence(
                    evidence_id=f"{claim_id}-{source.content_hash[:12]}", claim_id=claim_id,
                    source_url=source.final_url, source_title=source.title, excerpt=excerpt,
                    quality=source.quality, retrieved_at=source.retrieved_at,
                    source_hash=source.content_hash, independence_group=source.domain,
                )
                claims.append(Claim(
                    claim_id=claim_id, text=statement, evidence=[evidence], scope=str(fact.get("scope") or topic),
                    freshness_class="fast_changing", validity_horizon=deadline,
                    created_at=source.retrieved_at, updated_at=source.retrieved_at,
                ))
            claims, confidence, validation = verifier.verify_claims(claims, [source])
            if any(claim.status != ClaimStatus.SUPPORTED for claim in claims):
                outcome["validation"] = validation
                outcome["unverified_claims"] = [claim.text for claim in claims if claim.status != ClaimStatus.SUPPORTED]
                raise ValueError("Citation checks did not confirm direct support for every fact; review required")
            answer = topic + "\n" + "\n".join(f"- {claim.text}" for claim in claims) + f"\nSource: {url}"
            result = ResearchResult(
                run_id=f"official-pack-{uuid4().hex}", query=topic, answer=answer,
                sources=[source], claims=claims, citations=[source.final_url],
                confidence=confidence, complete=True, stop_reason="curated_source_verified",
                metrics=ResearchMetrics(pages_attempted=1, pages_retrieved=1,
                                        bytes_downloaded=source.bytes_downloaded,
                                        claims_supported=len(claims)),
                trace={"pack": "official-reference", "retrieved_at": source.retrieved_at,
                       "valid_until": deadline, "validation": validation},
            )
            knowledge_id = store.store_result(result, level="NORMAL")
            if knowledge_id is None:
                raise RuntimeError("Knowledge store did not retain this reference")
            outcome.update(status="imported", knowledge_id=knowledge_id, claims=len(claims),
                           retrieved_at=source.retrieved_at, valid_until=deadline,
                           content_hash=source.content_hash, confidence=confidence)
            report["imported_sources"] += 1
            report["supported_claims"] += len(claims)
        except Exception as exc:
            outcome.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        report["entries"].append(outcome)
    report["failed_sources"] = sum(item["status"] == "failed" for item in report["entries"])
    return report
