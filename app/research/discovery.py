"""Bounded source diversity and evidence-link discovery for deep research."""
from __future__ import annotations

from urllib.parse import urlsplit

from app.research.deduplication import canonicalize_url
from app.research.models import SearchResult, SourceDocument
from app.research.ranking import score_for_query
from app.research.sources import is_probably_official
from app.research.text import token_set


def diverse_sources(ranked: list[SearchResult], limit: int) -> list[SearchResult]:
    selected, deferred, counts = [], [], {}
    for item in ranked:
        host = (urlsplit(item.url).hostname or '').lower()
        if counts.get(host, 0) >= 2:
            deferred.append(item)
        else:
            selected.append(item)
            counts[host] = counts.get(host, 0) + 1
    return (selected + deferred)[:limit]


def followup_sources(documents: list[SourceDocument], query: str, seen: set[str], limit: int,
                     official_only: bool = False) -> list[SearchResult]:
    terms = token_set(query) - {'search', 'research', 'deep', 'find', 'sources', 'please', 'about', 'what', 'with', 'does', 'this', 'that'}
    found = {}
    for document in documents:
        for link in document.links[:300]:
            url, title = str(link.get('url') or ''), str(link.get('text') or '')
            try:
                parts = urlsplit(url)
                parts.port
            except ValueError:
                continue
            if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
                continue
            key = canonicalize_url(url)
            overlap = len(terms & token_set(title + ' ' + parts.path.replace('/', ' ')))
            if key in seen or key in found or not overlap:
                continue
            if official_only and not is_probably_official(url, query, title):
                continue
            quality, _, _, _ = score_for_query(url, title, query, None)
            found[key] = (overlap, SearchResult(title or url, url, domain=parts.hostname, provider='source_link',
                                              quality=quality, query_intent='follow_reference'))
    ranked = [item for _, item in sorted(found.values(), key=lambda value: (-value[0], -value[1].quality, value[1].url))]
    return diverse_sources(ranked, limit)
