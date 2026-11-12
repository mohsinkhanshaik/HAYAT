"""Refresh curated reference facts against their original public documentation.

This adds retrievable knowledge; it never trains weights or executes page text.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
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

OFFICIAL_PATHS = {
    'www.investor.gov': {'/introduction-investing', '/introduction-investing/investing-basics/save-and-invest/figure-out-your-finances'},
    'www.eia.gov': {'/energyexplained/electricity/measuring-electricity.php'},
    'www.imf.org': {'/en/publications/fandd/issues/series/back-to-basics/inflation'},
    'main.un.org': {'/securitycouncil/en/content/current-members'},
    'www.un.org': {'/about-us/un-charter/chapter-5'},
    'www.stlouisfed.org': {'/publications/page-one-economics/2024/03/01/the-inflation-rate-is-falling-but-prices-are-not'},
}


#: Government and intergovernmental publishers: their own pages are the
#: official record for public reference facts (earth science, energy,
#: finance regulation, tax law, public health, archives). Only pages a
#: reviewed catalog names are accepted, not every page on such a host.
GOVERNMENT_SUFFIXES = (".gov", ".mil", ".gov.in", ".nic.in", ".gov.uk", ".gc.ca", ".gov.au", ".europa.eu")
INTERGOVERNMENTAL_HOSTS = frozenset({"www.who.int", "www.imf.org", "www.worldbank.org", "www.oecd.org", "wmo.int",
                                     "www.ipcc.ch", "www.iea.org", "www.un.org", "data.un.org"})
REVIEWED_CATALOGS = tuple(Path(__file__).resolve().parents[2] / "config" / name
                          for name in ("reference-knowledge.json",))


@lru_cache(maxsize=1)
def _reviewed_government_pages() -> frozenset[tuple[str, str]]:
    pages = set()
    for catalog in REVIEWED_CATALOGS:
        for entry in json.loads(catalog.read_text(encoding="utf-8")):
            parts = urlsplit(str(entry.get("url", "")))
            host = (parts.hostname or "").lower()
            if host.endswith(GOVERNMENT_SUFFIXES) or host in INTERGOVERNMENTAL_HOSTS:
                pages.add((host, parts.path.rstrip("/")))
    return frozenset(pages)


def _official_url(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https" and not parts.username and not parts.password
        and parts.port in {None, 443}
        and (
            parts.hostname in OFFICIAL_HOSTS
            or ((parts.hostname or "").lower(), parts.path.rstrip("/")) in _reviewed_government_pages()
            or parts.path.rstrip('/') in OFFICIAL_PATHS.get(parts.hostname, set())
            or (parts.hostname == "huggingface.co" and parts.path.startswith("/docs/"))
            or (parts.hostname == "science.nasa.gov" and parts.path.rstrip("/") in
                {"/earth/facts", "/mars/facts", "/moon/facts", "/uap/faqs"})
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
            outcome["response_status"] = resource.status_code
            if not _official_url(resource.final_url) or urlsplit(url).hostname != urlsplit(resource.final_url).hostname:
                raise ValueError("Source redirected outside its official host")
            source, _ = extractor.extract(resource, query=topic)
            if not source.text.strip():
                raise ValueError(f"Source returned no readable content (HTTP {resource.status_code}); "
                                 "its facts were not refreshed")
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


#: Curated catalogs and the report each refresh writes: (catalog, report).
PACKS = (
    ("config/official-knowledge.json", "data/knowledge/official-pack-report.json"),
    ("config/foundational-knowledge.json", "data/knowledge/foundational-upgrade-2026-10-31.json"),
    ("config/reference-knowledge.json", "data/knowledge/reference-pack-report.json"),
)


def due_entries(entries: list[dict[str, Any]], report: dict[str, Any] | None, now: datetime,
                margin: timedelta = timedelta(days=2)) -> list[dict[str, Any]]:
    """Entries never imported, last failed, or expiring within the margin."""
    last = {item.get("url"): item for item in (report or {}).get("entries", [])}
    due = []
    for entry in entries:
        outcome = last.get(entry.get("url"))
        try:
            fresh = (outcome is not None and outcome.get("status") == "imported"
                     and datetime.fromisoformat(str(outcome["valid_until"])) > now + margin)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if not fresh:
            due.append(entry)
    return due


def refresh_due_packs(store: KnowledgeStore, fetcher: PublicFetcher, root: Path,
                      now: datetime | None = None) -> dict[str, Any]:
    """Re-verify only the expiring or failed entries of each curated pack.

    The packs' facts expire after at most 30 days by design; without this they
    went stale until someone re-ran the script. Each report keeps the latest
    outcome per source, so a source that fails now is retried next time and a
    fresh one is left alone.
    """
    now = now or datetime.now().astimezone()
    summary: dict[str, Any] = {"checked": 0, "refreshed": 0, "failed": 0}
    for catalog_name, report_name in PACKS:
        catalog, report_path = root / catalog_name, root / report_name
        if not catalog.exists():
            continue
        entries = json.loads(catalog.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
        due = due_entries(entries, report, now)
        summary["checked"] += len(entries)
        if not due:
            continue
        fresh = refresh_official_pack(due, store, fetcher)
        summary["refreshed"] += fresh["imported_sources"]
        summary["failed"] += fresh["failed_sources"]
        merged = {item.get("url"): item for item in (report or {}).get("entries", [])}
        for item in fresh["entries"]:
            previous = merged.get(item["url"])
            # A failed retry must not erase the record of the last good import.
            if item["status"] == "imported" or previous is None or previous.get("status") != "imported":
                merged[item["url"]] = item
            else:
                previous["last_refresh_error"] = item.get("error", "")
        rows = [merged[entry["url"]] for entry in entries if entry.get("url") in merged]
        written = {"imported_sources": sum(row["status"] == "imported" for row in rows),
                   "supported_claims": sum(int(row.get("claims", 0)) for row in rows if row["status"] == "imported"),
                   "entries": rows, "failed_sources": sum(row["status"] == "failed" for row in rows)}
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(written, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
