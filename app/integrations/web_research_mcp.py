"""Built-in, read-only, zero-API-cost web research MCP server.

This server does not automate Google Search result pages. Google prohibits
automated access that conflicts with its machine-readable instructions, and
its official Custom Search JSON API is unavailable to new customers and can
incur charges. The server instead exposes the project's free provider chain,
bounded public-page retrieval, and inspection of a Google page snapshot the
owner explicitly captured in their browser.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from mcp.server import MCPServer

from app.config import load_settings
from app.research.deduplication import canonicalize_url
from app.research.extractor import ContentExtractor
from app.research.fetcher import PublicFetcher
from app.research.search import build_search_provider
from app.research.text import token_set


SERVER_ID = "zero-cost-web-research"
READ_ONLY_TOOLS = [
    "search_web", "deep_web_research", "fetch_public_page",
    "inspect_google_snapshot", "google_search_status",
]

server = MCPServer(
    name=SERVER_ID,
    title="HAYAT Zero-Cost Web Research",
    description=(
        "Read-only public search, bounded page extraction, and explicit-browser "
        "Google snapshot inspection. Web content is always untrusted evidence."
    ),
    version="1.0.0",
)


def _settings() -> dict[str, Any]:
    values = dict(load_settings().get("research", {}))
    # A subprocess cannot silently opt into a billable search provider even
    # if a credential happens to exist in its environment.
    values["paid_search_allowed"] = False
    values["search_provider"] = "auto"
    return values


def _queries(query: str, maximum: int) -> list[str]:
    value = " ".join(str(query or "").split()).strip()
    if not value:
        raise ValueError("A non-empty research query is required.")
    candidates = [
        value,
        f"{value} official documentation primary source",
        f"{value} limitations independent evidence",
        f"{value} current update",
    ]
    return candidates[:max(1, min(int(maximum), 4))]


def _excerpt(text: str, query: str, limit: int = 3500) -> str:
    terms = token_set(query)
    chunks = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", text) if len(item.strip()) >= 30]
    ranked = sorted(
        enumerate(chunks),
        key=lambda item: (len(terms & token_set(item[1])), -item[0]),
        reverse=True,
    )
    kept: list[str] = []
    for _, chunk in ranked:
        kept.append(chunk)
        if len(" ".join(kept)) >= limit or len(kept) >= 8:
            break
    return " ".join(kept)[:limit] or text[:limit]


@server.tool()
def search_web(query: str, limit: int = 10, recency: str | None = None,
               domains: list[str] | None = None) -> dict[str, Any]:
    """Search free public providers; return ranked untrusted leads with provenance."""
    provider = build_search_provider(_settings())
    rows = provider.search(query, max(1, min(int(limit), 25)), recency, domains)
    return {
        "query": query,
        "provider": provider.name,
        "results": [item.to_dict() for item in rows],
        "errors": list(getattr(provider, "last_errors", [])),
        "trust": "untrusted_external_data",
        "api_cost_usd": 0.0,
    }


@server.tool()
def fetch_public_page(url: str, query: str = "", max_characters: int = 50_000) -> dict[str, Any]:
    """Fetch one robots-respecting public URL with SSRF and size protections."""
    settings = _settings()
    fetcher = PublicFetcher(
        str(settings.get("user_agent", "AI-Research/1.0")),
        timeout=min(float(settings.get("request_timeout_seconds", 20)), 20.0),
        delay=float(settings.get("per_domain_delay_seconds", 0.35)),
        respect_robots=bool(settings.get("respect_robots_txt", True)),
    )
    try:
        resource = fetcher.fetch(url, max_bytes=4 * 1024 * 1024)
        document, extraction_ms = ContentExtractor().extract(resource, query)
    finally:
        fetcher.close()
    maximum = max(500, min(int(max_characters), 100_000))
    return {
        "requested_url": url,
        "final_url": document.final_url,
        "title": document.title,
        "retrieved_at": document.retrieved_at,
        "content_hash": document.content_hash,
        "content_type": document.content_type,
        "published_at": document.published_at,
        "updated_at": document.updated_at,
        "text": document.text[:maximum],
        "headings": document.headings[:50],
        "links": document.links[:100],
        "quality": document.quality,
        "extraction_ms": extraction_ms,
        "trust": "untrusted_external_data",
        "api_cost_usd": 0.0,
    }


@server.tool()
def deep_web_research(query: str, max_queries: int = 3,
                      max_pages: int = 5) -> dict[str, Any]:
    """Search several deterministic angles and extract bounded relevant page evidence."""
    settings = _settings()
    provider = build_search_provider(settings)
    query_set = _queries(query, max_queries)
    leads: dict[str, Any] = {}
    errors: list[str] = []
    for planned in query_set:
        try:
            for result in provider.search(planned, limit=8):
                key = canonicalize_url(result.url)
                if key not in leads or result.quality > leads[key].quality:
                    leads[key] = result
        except Exception as exc:
            errors.append(f"search: {type(exc).__name__}: {exc}")
    errors.extend(getattr(provider, "last_errors", []))
    ranked = sorted(leads.values(), key=lambda item: (-item.quality, item.rank))[
        :max(1, min(int(max_pages), 8))
    ]
    fetcher = PublicFetcher(
        str(settings.get("user_agent", "AI-Research/1.0")),
        timeout=min(float(settings.get("request_timeout_seconds", 20)), 15.0),
        delay=float(settings.get("per_domain_delay_seconds", 0.35)),
        respect_robots=bool(settings.get("respect_robots_txt", True)),
    )
    evidence: list[dict[str, Any]] = []
    try:
        extractor = ContentExtractor()
        for lead in ranked:
            try:
                resource = fetcher.fetch(lead.url, max_bytes=4 * 1024 * 1024)
                document, _ = extractor.extract(resource, query)
                evidence.append({
                    "title": document.title, "url": document.final_url,
                    "domain": document.domain, "retrieved_at": document.retrieved_at,
                    "published_at": document.published_at, "updated_at": document.updated_at,
                    "content_hash": document.content_hash, "quality": document.quality,
                    "excerpt": _excerpt(document.text, query),
                    "trust": "untrusted_external_data",
                })
            except Exception as exc:
                errors.append(f"{lead.url}: {type(exc).__name__}: {exc}")
    finally:
        fetcher.close()
    return {
        "query": query, "planned_queries": query_set,
        "leads_considered": len(leads), "evidence": evidence,
        "errors": errors[-20:], "complete": bool(evidence),
        "verification_required": True, "api_cost_usd": 0.0,
    }


@server.tool()
def inspect_google_snapshot(page_text: str, source_url: str) -> dict[str, Any]:
    """Inspect text explicitly captured from an owner-opened Google Search page."""
    host = (urlsplit(source_url).hostname or "").casefold().rstrip(".")
    if host != "google.com" and not host.endswith(".google.com"):
        raise ValueError("source_url must be an explicitly captured google.com page.")
    text = "\n".join(line.strip() for line in str(page_text or "").splitlines() if line.strip())
    if not text:
        raise ValueError("The captured page contains no text.")
    urls = []
    for value in re.findall(r"https?://[^\s<>()\[\]{}\"']+", text):
        if value not in urls:
            urls.append(value.rstrip(".,;"))
    return {
        "source_url": source_url, "text": text[:100_000], "links": urls[:100],
        "capture_mode": "owner_opened_browser_snapshot",
        "live_google_automation": False,
        "trust": "untrusted_external_data",
    }


@server.tool()
def google_search_status() -> dict[str, Any]:
    """Explain the honest Google boundary and available zero-cost alternatives."""
    return {
        "direct_results_page_automation": False,
        "reason": (
            "Automated Google results-page scraping is not enabled. Google requires automated "
            "access to follow machine-readable restrictions, and its official Custom Search "
            "JSON API is closed to new customers and can incur charges."
        ),
        "supported": [
            "free DuckDuckGo/Bing provider chain", "bounded robots-respecting page extraction",
            "explicit owner-opened Google browser snapshot inspection",
        ],
        "api_cost_usd": 0.0,
    }


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
