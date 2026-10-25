from __future__ import annotations

import time
import math
from collections import deque
from urllib.parse import urlsplit

from app.research.deduplication import canonicalize_url
from app.research.extractor import ContentExtractor
from app.research.fetcher import PublicFetcher
from app.research.models import SourceDocument


class BoundedCrawler:
    def __init__(self, fetcher: PublicFetcher, extractor: ContentExtractor) -> None:
        self.fetcher = fetcher
        self.extractor = extractor

    def crawl(self, start_url: str, query: str, max_pages: int = 10, max_depth: int = 2, max_seconds: float = 60, max_bytes: int = 10 * 1024 * 1024, same_domain: bool = True) -> tuple[list[SourceDocument], list[str]]:
        if type(max_pages) is not int or not 1 <= max_pages <= 50 or type(max_depth) is not int or not 0 <= max_depth <= 3:
            raise ValueError('Use 1–50 page attempts and a depth from 0 to 3.')
        if type(max_seconds) not in (float, int) or not math.isfinite(max_seconds) or max_seconds <= 0 or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('Crawl time and byte budgets must be positive.')
        queue = deque([(start_url, 0)])
        queued = {canonicalize_url(start_url)}
        seen: set[str] = set()
        sources: list[SourceDocument] = []
        errors: list[str] = []
        started = time.monotonic()
        total_bytes = 0
        origin = (urlsplit(start_url).hostname or "").lower()
        attempts = 0
        terms = {word for word in query.lower().split() if len(word) > 3}
        while queue and attempts < max_pages and time.monotonic() - started < max_seconds and total_bytes < max_bytes:
            url, depth = queue.popleft()
            canonical = canonicalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            attempts += 1
            try:
                resource = self.fetcher.fetch(url, min(4 * 1024 * 1024, max_bytes - total_bytes))
                total_bytes += len(resource.content)
                if total_bytes > max_bytes:
                    errors.append(f'{url}: response exceeded the remaining crawl byte budget; not retained')
                    continue
                final_host = (urlsplit(resource.final_url).hostname or '').lower()
                if attempts == 1:
                    origin = final_host  # follow the seed's canonical host, including www redirects
                elif same_domain and final_host != origin:
                    errors.append(f'{url}: redirected outside the seed domain; not retained or followed')
                    continue
                document, _ = self.extractor.extract(resource, query)
                sources.append(document)
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                continue
            if depth >= max_depth:
                continue
            ranked = sorted(document.links, key=lambda link: sum(term in (link["text"] + " " + link["url"]).lower() for term in terms), reverse=True)
            for link in ranked[:30]:
                host = (urlsplit(link["url"]).hostname or "").lower()
                if same_domain and host != origin:
                    continue
                canonical_link = canonicalize_url(link['url'])
                if canonical_link not in seen and canonical_link not in queued and urlsplit(link['url']).scheme in ('http', 'https'):
                    queued.add(canonical_link)
                    queue.append((link["url"], depth + 1))
        if queue:
            errors.append(f'Crawl stopped at its budget: {attempts} fetch attempts, {len(sources)} pages retained, {total_bytes} bytes received.')
        return sources, errors
