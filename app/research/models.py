from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ClaimStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    DISPUTED = "DISPUTED"
    UNVERIFIED = "UNVERIFIED"
    SUPERSEDED = "SUPERSEDED"


class SupportType(StrEnum):
    DIRECT = "DIRECT"
    INFERRED = "INFERRED"
    WEAK = "WEAK"
    UNSUPPORTED = "UNSUPPORTED"


class ClaimType(StrEnum):
    FACT = "FACT"
    TEMPORAL = "TEMPORAL"
    COMPARISON = "COMPARISON"
    CAUSAL = "CAUSAL"
    INTERPRETATION = "INTERPRETATION"


@dataclass(slots=True)
class ResearchBudget:
    searches: int = 5
    pages: int = 15
    max_seconds: float = 90
    max_bytes: int = 20 * 1024 * 1024
    min_sources: int = 2
    max_workers: int = 3


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    domain: str = ""
    published_at: str | None = None
    rank: int = 0
    provider: str = ""
    quality: float = 0.0
    query_intent: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceDocument:
    url: str
    final_url: str
    title: str
    domain: str
    text: str
    retrieved_at: str
    status_code: int
    content_type: str
    content_hash: str
    bytes_downloaded: int
    published_at: str | None = None
    updated_at: str | None = None
    canonical_url: str | None = None
    description: str = ""
    headings: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    quality: float = 0.0
    quality_reasons: list[str] = field(default_factory=list)
    trust: str = "untrusted_external_data"
    source_type: str = "web_page"
    author: str | None = None
    primary: bool = False
    independence_group: str = ""
    lineage_urls: list[str] = field(default_factory=list)
    version: str | None = None
    cache_status: str = "MISS"

    def to_dict(self, include_text: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_text:
            data.pop("text", None)
        return data


@dataclass(slots=True)
class Evidence:
    evidence_id: str
    claim_id: str
    source_url: str
    source_title: str
    excerpt: str
    quality: float
    retrieved_at: str
    published_at: str | None = None
    location: str = "page"
    support_type: SupportType = SupportType.UNSUPPORTED
    strength: float = 0.0
    stance: str = "supporting"
    source_hash: str = ""
    independence_group: str = ""


@dataclass(slots=True)
class Claim:
    claim_id: str
    text: str
    claim_type: ClaimType = ClaimType.FACT
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    evidence: list[Evidence] = field(default_factory=list)
    contradictions: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    freshness_class: str = "slow_changing"
    validity_horizon: str | None = None
    created_at: str = ""
    updated_at: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    scope: str = ""


@dataclass(slots=True)
class ResearchMetrics:
    search_ms: int = 0
    fetch_ms: int = 0
    extraction_ms: int = 0
    total_ms: int = 0
    searches: int = 0
    pages_attempted: int = 0
    pages_retrieved: int = 0
    bytes_downloaded: int = 0
    duplicates: int = 0
    failures: int = 0
    validation_ms: int = 0
    synthesis_ms: int = 0
    knowledge_ms: int = 0
    cache_hits: int = 0
    knowledge_hits: int = 0
    model_calls: int = 0
    queries_generated: int = 0
    queries_reused: int = 0
    independent_sources: int = 0
    claims_supported: int = 0
    claims_disputed: int = 0
    claims_unsupported: int = 0


@dataclass(slots=True)
class ResearchResult:
    run_id: str
    query: str
    answer: str
    sources: list[SourceDocument]
    claims: list[Claim]
    citations: list[str]
    confidence: float
    complete: bool
    stop_reason: str
    metrics: ResearchMetrics
    knowledge_gaps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    mode: str = "NORMAL"
    trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_source_text: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["sources"] = [source.to_dict(include_source_text) for source in self.sources]
        return data
