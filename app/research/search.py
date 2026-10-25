from __future__ import annotations

import abc
import os
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import replace
from app.security import xml as ET
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.research.models import SearchResult
from app.research.deduplication import canonicalize_url
from app.research.sources import score_source


class WebSearchProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def search(self, query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> list[SearchResult]:
        raise NotImplementedError


def _scoped_query(query: str, recency: str | None, domains: list[str] | None) -> str:
    parts = [query.strip()]
    if domains:
        parts.append("(" + " OR ".join(f"site:{domain}" for domain in domains[:8]) + ")")
    if recency:
        parts.append(recency.strip())
    return " ".join(item for item in parts if item)


def _search_get(url: str, *, require_tor: bool = False, **kwargs):
    from app.privacy_masks import ROUTE
    from app.research.network_diagnostics import connection_diagnostic

    # Remember the route used by this request. A later UI toggle must not make
    # a failed masked connection look like a direct-network failure.
    via_tor = require_tor or ROUTE.snapshot()[0] is not None
    try:
        return _routed_search_get(url, require_tor=via_tor, **kwargs)
    except (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as exc:
        try:
            request = exc.request
        except RuntimeError:
            request = None  # transports may fail before attaching a request
        raise type(exc)(connection_diagnostic(exc, via_tor=via_tor), request=request) from exc


def _routed_search_get(url: str, *, require_tor: bool = False, **kwargs):
    from app.privacy_masks import ROUTE

    route = ROUTE.snapshot()
    if route[0] is None and not require_tor:
        response = httpx.get(url, trust_env=False, **kwargs)
        if ROUTE.snapshot() != route:
            response.close()
            raise PermissionError('Privacy route changed during search; start a new request.')
        return response
    from app.research.fetcher import PublicFetcher

    headers = kwargs.get("headers", {})
    fetcher = PublicFetcher(headers.get("User-Agent", "HAYAT-Search/1.0"),
                            timeout=kwargs.get("timeout", 20), delay=0, respect_robots=False)
    try:
        target = str(httpx.URL(url, params=kwargs.get("params")))
        resource = fetcher.fetch(target, request_headers=headers, require_tor=True)
        # PublicFetcher returns decoded bytes. Reusing wire encoding headers would
        # make HTTPX decompress that body a second time (notably Brotli via Tor).
        headers = {name: value for name, value in resource.headers.items()
                   if name.lower() not in {'content-encoding', 'content-length', 'transfer-encoding'}}
        return httpx.Response(resource.status_code, headers=headers, content=resource.content,
                              request=httpx.Request("GET", resource.final_url))
    finally:
        fetcher.close()


class DuckDuckGoHtmlSearch(WebSearchProvider):
    name = "duckduckgo_html"

    def __init__(self, timeout: float = 20, user_agent: str = "AI-Research/1.0", *, require_tor: bool = False) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.require_tor = require_tor

    def search(self, query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> list[SearchResult]:
        scoped = _scoped_query(query, recency, domains)
        response = _search_get(
            "https://html.duckduckgo.com/html/",
            require_tor=self.require_tor,
            params={"q": scoped}, timeout=self.timeout, follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept": "text/html"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for node in soup.select(".result"):
            anchor = node.select_one(".result__a")
            if not anchor or not anchor.get("href"):
                continue
            url = str(anchor["href"])
            params = parse_qs(urlsplit(url).query)
            if "uddg" in params:
                url = unquote(params["uddg"][0])
            if urlsplit(url).scheme not in {"http", "https"}:
                continue
            title = anchor.get_text(" ", strip=True)
            snippet_node = node.select_one(".result__snippet")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            domain = (urlsplit(url).hostname or "").lower()
            quality, _ = score_source(url, title, query)
            results.append(SearchResult(title, url, snippet, domain, None, len(results) + 1, self.name, quality))
            if len(results) >= max(1, min(limit, 50)):
                break
        return results


class BraveSearch(WebSearchProvider):
    name = "brave"

    def __init__(self, api_key: str, timeout: float = 20, user_agent: str = "AI-Research/1.0") -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.user_agent = user_agent

    def search(self, query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> list[SearchResult]:
        raise PermissionError("ZERO-COST: subscription search APIs are blocked; use public search or browse manually.")

class BingRssSearch(WebSearchProvider):
    name = "bing_rss"

    def __init__(self, timeout: float = 20, user_agent: str = "AI-Research/1.0", *, require_tor: bool = False) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.require_tor = require_tor

    def search(self, query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> list[SearchResult]:
        response = _search_get(
            "https://www.bing.com/search", params={"q": _scoped_query(query, recency, domains), "format": "rss"},
            require_tor=self.require_tor,
            timeout=self.timeout, follow_redirects=True, headers={"User-Agent": self.user_agent, "Accept": "application/rss+xml,application/xml"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
        results: list[SearchResult] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            if urlsplit(url).scheme not in {"http", "https"}:
                continue
            quality, _ = score_source(url, title, query)
            results.append(SearchResult(title, url, (item.findtext("description") or "").strip(), (urlsplit(url).hostname or "").lower(), item.findtext("pubDate"), len(results) + 1, self.name, quality))
            if len(results) >= max(1, min(limit, 50)):
                break
        return results


class SearchProviderChain(WebSearchProvider):
    name = "chain"

    def __init__(self, providers: list[WebSearchProvider]) -> None:
        self.providers = providers
        self._errors = ContextVar('search_provider_errors', default=())
        self._coverage = ContextVar('search_provider_coverage', default=())

    @property
    def last_errors(self) -> list[str]:
        return list(self._errors.get())

    @property
    def last_coverage(self) -> list[dict]:
        return [dict(item) for item in self._coverage.get()]

    def search_wide(self, query: str, limit: int = 20, recency: str | None = None,
                    domains: list[str] | None = None) -> list[SearchResult]:
        """Combine configured indexes, retaining validated URLs and balancing domains."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Search limit must be an integer from 1 to 50.')
        self._errors.set(())
        self._coverage.set(())
        from app.privacy_masks import ROUTE
        initial_route = ROUTE.snapshot()
        # Each worker owns its validator and error context; no shared mutable provider state.
        def read(provider):
            if ROUTE.snapshot() != initial_route:
                return [], [f'{provider.name}: Privacy route changed during search; start a new request.']
            chain = SearchProviderChain([provider])
            rows = chain.search(query, min(50, max(10, limit)), recency, domains)
            return rows, chain.last_errors
        merged, scores, errors, coverage = {}, {}, [], []
        active = [provider for provider in self.providers if not hasattr(provider, 'supports') or provider.supports(query)]
        coverage.extend({'provider': provider.name, 'usable_results': 0, 'status': 'not_applicable', 'errors': []}
                        for provider in self.providers if provider not in active)
        with ThreadPoolExecutor(max_workers=min(3, max(1, len(active))), thread_name_prefix='search-index') as pool:
            futures = [pool.submit(read, provider) for provider in active]
            for provider, future in zip(active, futures):
                rows, failures = future.result()
                errors.extend(failures)
                coverage.append({'provider': provider.name, 'usable_results': len(rows),
                                 'status': 'results' if rows else 'no_usable_results', 'errors': failures})
                for item in rows:
                    key = canonicalize_url(item.url)
                    scores[key] = scores.get(key, 0.0) + 1 / (60 + item.rank)
                    if key not in merged:
                        merged[key] = item
        self._errors.set(tuple(errors))
        self._coverage.set(tuple(coverage))
        if ROUTE.snapshot() != initial_route:
            self._errors.set(tuple(errors + ['Privacy route changed during search; retrieved results were discarded.']))
            return []
        ranked = sorted(merged, key=lambda key: (-scores[key], -merged[key].quality, merged[key].rank, key))
        chosen, deferred, counts = [], [], {}
        for key in ranked:
            item = merged[key]
            if counts.get(item.domain, 0) >= 3 and not (domains and len(domains) == 1):
                deferred.append(item)
            else:
                chosen.append(item)
                counts[item.domain] = counts.get(item.domain, 0) + 1
        return [replace(item, rank=index + 1) for index, item in enumerate((chosen + deferred)[:limit])]

    def search(self, query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> list[SearchResult]:
        self._errors.set(())
        self._coverage.set(())
        if not isinstance(query, str) or not query.strip():
            return []
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('Search limit must be an integer from 1 to 50.')
        domains = [str(domain).casefold().strip().removeprefix('*.').lstrip('.') for domain in domains or []]
        if len(domains) > 8 or any(not domain or any(char in domain for char in '/:@?#')
                                   or any(char.isspace() for char in domain) for domain in domains):
            raise ValueError('Search domains must be host names, not URLs.')
        errors = []
        from app.privacy_masks import ROUTE
        initial_route = ROUTE.snapshot()
        try:
            for provider in self.providers:
                if ROUTE.snapshot() != initial_route:
                    errors.append('Privacy route changed during search; start a new request. No provider fallback was used after the change.')
                    break
                if hasattr(provider, 'supports') and not provider.supports(query):
                    continue
                try:
                    found, seen = [], set()
                    for item in provider.search(query.strip(), limit, recency, domains):
                        if not isinstance(item, SearchResult):
                            continue
                        if not isinstance(item.url, str):
                            continue
                        url = item.url.strip()
                        try:
                            parts = urlsplit(url)
                            host = (parts.hostname or '').casefold().rstrip('.')
                            parts.port
                        except ValueError:
                            continue
                        if (parts.scheme not in ('http', 'https') or not host or parts.username or parts.password
                                or host == 'localhost' or host.endswith(('.local', '.localhost'))):
                            continue
                        try:
                            address = ipaddress.ip_address(host)
                        except ValueError:
                            address = None
                        if address is not None and not address.is_global:
                            continue
                        if domains and not any(host == domain or host.endswith('.' + domain) for domain in domains):
                            continue
                        canonical = canonicalize_url(url)
                        if canonical in seen:
                            continue
                        seen.add(canonical)
                        found.append(replace(item, url=url, domain=host, title=str(item.title or '').strip() or host,
                                             provider=item.provider or provider.name, rank=len(found) + 1))
                        if len(found) >= limit:
                            break
                    if ROUTE.snapshot() != initial_route:
                        raise PermissionError('Privacy route changed during search; retrieved results were discarded.')
                    errors.extend(f'{provider.name}: {problem}' for problem in getattr(provider, 'last_errors', []))
                    if found:
                        return found
                    errors.append(f'{provider.name}: no usable results')
                except Exception as exc:  # one provider cannot suppress the remaining fallback providers
                    errors.append(f'{provider.name}: {type(exc).__name__}: {str(exc)[:240]}')
            return []
        finally:
            self._errors.set(tuple(errors))


class PublicRegistrySearch(WebSearchProvider):
    name = 'public_registries'

    def __init__(self, timeout=20, user_agent='HAYAT-PublicResearch/1.0'):
        self.timeout, self.user_agent = timeout, user_agent
        self._errors = ContextVar('registry_search_errors', default=())

    @property
    def last_errors(self):
        return list(self._errors.get())

    @staticmethod
    def supports(query):
        return isinstance(query, str) and bool(re.search(r'(?i)\b(?:papers?|datasets?|clinical|scientific|methodology|peer.reviewed|systematic review|meta.analysis)\b', query))

    def search(self, query, limit=10, recency=None, domains=None):
        from app.research.public_data import PublicData
        from app.research.fetcher import PublicFetcher
        from app.research.text import token_set
        self._errors.set(())
        if not self.supports(query):
            return []
        datasets = bool(re.search(r'(?i)\bdatasets?\b', query))
        papers = bool(re.search(r'(?i)\bpapers?|clinical|peer.reviewed|systematic review|meta.analysis', query))
        kind = 'all' if datasets and papers else 'datasets' if datasets else 'publications'
        topic = re.sub(r'(?i)\b(?:scientific|research|papers?|datasets?|methodology|results|limitations|official|primary|sources?|'
                       r'original|find|search|public|systematic review|meta.analysis)\b', ' ', query)
        topic = ' '.join(topic.split()).strip(' ?.')
        wanted = token_set(topic) - {'you', 'all', 'for', 'can', 'are'}
        if not wanted:
            return []
        fetcher = PublicFetcher(self.user_agent, timeout=self.timeout, respect_robots=True)
        try:
            value = self.lookup(PublicData(fetcher), topic, kind, min(10, max(5, limit)))
            self._errors.set(tuple(f"{row.get('provider', 'registry')}: {row.get('error') or row['status']}"
                                   for row in value['providers'] if row['status'] != 'ok'))
            weight = sum(len(term) for term in wanted)
            records = [row for row in value['records'] if sum(len(term) for term in wanted & token_set(row['title'])) / weight >= .45]
            if not records and any(row['status'] != 'ok' for row in value['providers']):
                raise RuntimeError('Public registry lookup failed: ' + '; '.join(str(row.get('error') or row['status']) for row in value['providers']))
            return [SearchResult(row['title'], row.get('doi_url') or row['landing_url'],
                                 f"Registry metadata only; {row.get('publisher') or 'publisher unknown'}; year {row.get('year') or 'unknown'}. Full content is not verified.",
                                 (urlsplit(row.get('doi_url') or row['landing_url']).hostname or ''), rank=index + 1, provider=self.name, quality=.5)
                    for index, row in enumerate(records[:limit])]
        finally:
            fetcher.close()

    def lookup(self, data, topic, kind, limit):
        return data.search(topic, kind, limit)


class EuropePmcSearch(PublicRegistrySearch):
    name = 'europe_pmc'

    @staticmethod
    def supports(query):
        return PublicRegistrySearch.supports(query) and (not re.search(r'(?i)\bdatasets?\b', query)
                or bool(re.search(r'(?i)\b(?:papers?|clinical|scientific)\b', query)))

    def lookup(self, data, topic, kind, limit):
        return data.search(topic, 'literature', limit)


def build_search_provider(settings: dict) -> WebSearchProvider:
    timeout = float(settings.get("request_timeout_seconds", 20))
    user_agent = str(settings.get("user_agent", "AI-Research/1.0"))
    requested = os.getenv("AI_SEARCH_PROVIDER", str(settings.get("search_provider", "auto"))).lower()
    providers: list[WebSearchProvider] = []
    # Credentials and stale research flags must never opt a paid provider in.
    if requested not in {'bing', 'bing_rss'}:
        providers.append(DuckDuckGoHtmlSearch(timeout, user_agent))
    if requested not in {'duckduckgo', 'duckduckgo_html'}:
        providers.append(BingRssSearch(timeout, user_agent))
    if requested == 'auto':
        providers.append(PublicRegistrySearch(timeout, user_agent))
        providers.append(EuropePmcSearch(timeout, user_agent))
    return SearchProviderChain(providers)
