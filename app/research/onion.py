"""Search public onion services through Tor and research the retrieved evidence."""
from __future__ import annotations

from tempfile import TemporaryDirectory
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from app.research.fetcher import PublicFetcher
from app.research.independence import SourceIndependenceAnalyzer
from app.research.models import ResearchBudget, SearchResult
from app.research.query_planner import PlannedQuery
from app.research.safety import validate_public_url
from app.research.search import BingRssSearch, DuckDuckGoHtmlSearch, SearchProviderChain, WebSearchProvider
from app.research.text import stable_id, token_set

INDEX = 'https://ahmia.fi/search/'
PUBLISHER_DIRECTORY = 'https://onion.torproject.org/'


def parse_tor_web_request(text):
    value = str(text or '').strip()
    if len(value) > 1200:
        return None
    match = re.fullmatch(r'(?i)(?:please )?(?:search|research) (?:the )?(?:public )?web '
                         r'(?:through|via|over) tor(?: for|:)\s+(.+)', value)
    return match[1].strip(' .!?') if match and 1 <= len(match[1]) <= 500 else None


#: The words that make a request a dark-web one. An explicit mention is always required.
_ONION_PLACE = r'(?:dark\s*web|darknet|deep\s*web|onion\s+(?:services|sites|network)|hidden\s+services|tor\s+(?:network|sites|hidden\s+services))'
#: Search-ish verbs. "make money on the dark web" is still a search of public onion sources, answered honestly
#: with what they actually say (which, for a vague query, is little); it is never a how-to for anything illicit.
_ONION_VERB = r'(?:search|research|look(?:\s+(?:up|for|into|through|around|at))?|find|check|scan|monitor|explore|browse|dig\s+up|see|investigate)'


def parse_onion_request(text: str) -> str | None:
    value = ' '.join((text or '').split()).strip()
    patterns = [
        # "<verb> ... <dark web> (for|about)? <query>"  — "search the dark web for leaked data"
        rf'^(?:please\s+)?{_ONION_VERB}\s+(?:deeply\s+)?(?:the\s+)?{_ONION_PLACE}\s+(?:for\s+|about\s+|on\s+)?(.+)$',
        # "<verb> <query> on (the) <dark web>"  — "find ransomware groups on the dark web",
        # "look all ways to make money on the dark web"
        rf'^(?:please\s+)?{_ONION_VERB}\s+(?:for\s+)?(.+?)\s+(?:on|in|across|through)\s+(?:the\s+)?{_ONION_PLACE}\b.*$',
        # "what is happening / suspicious / new on the dark web"  — the query is that adjective of activity
        rf'^(?:please\s+)?(?:what(?:\'s|\s+is|\s+are)?|anything|what\s+kind\s+of)\s+(.+?)\s+(?:on|in|going\s+on\s+(?:on|in)|happening\s+(?:on|in))\s+(?:the\s+)?{_ONION_PLACE}\b.*$',
    ]
    for pattern in patterns:
        found = re.match(pattern, value, re.I)
        if found:
            query = found[1].strip(' .!?,')
            # A bare place-name with no real topic ("search the dark web") still runs, as a general scan.
            if not query or len(query) > 500:
                return None
            return query
    # "search the dark web" / "look on the dark web" with no topic at all.
    if re.fullmatch(rf'(?:please\s+)?{_ONION_VERB}\s+(?:deeply\s+)?(?:on\s+|in\s+)?(?:the\s+)?{_ONION_PLACE}[.!?]*', value, re.I):
        return 'notable public onion services and recent activity'
    return None


class TorRequiredFetcher(PublicFetcher):
    def __init__(self, *args, check=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._execution_guard = check or (lambda: None)

    def fetch(self, url, *args, **kwargs):
        self._execution_guard()
        kwargs['require_tor'] = True
        result = super().fetch(url, *args, **kwargs)
        self._execution_guard()
        return result


def _execution_check(parent, explicit=None):
    interlock = getattr(parent, 'interlock', None)
    ticket = interlock.ticket() if interlock is not None else None
    def check():
        if interlock is not None:
            interlock.check(ticket)
        if getattr(getattr(parent, 'authority', None), 'privacy_mode', False):
            raise PermissionError('Privacy mode blocks Tor research.')
        if explicit is not None:
            explicit()
    check()
    return check


def onion_url(url: str) -> str | None:
    try:
        if (urlsplit(url).hostname or '').endswith('.onion'):
            return validate_public_url(url, via_tor=True)
    except (ValueError, PermissionError):
        pass
    return None


def parse_index(html: str, query: str, limit: int) -> list[SearchResult]:
    soup = BeautifulSoup(html, 'html.parser')
    found, seen = [], set()
    for node in soup.select('li.result')[:100]:
        anchor = node.select_one('h4 a[href]')
        if anchor is None:
            continue
        href = str(anchor['href'])
        target = parse_qs(urlsplit(href).query).get('redirect_url', [href])[0]
        url = onion_url(target)
        if not url or url in seen:
            continue
        seen.add(url)
        snippet = node.select_one('p')
        found.append(SearchResult(anchor.get_text(' ', strip=True)[:300], url,
                                  snippet.get_text(' ', strip=True)[:600] if snippet else '',
                                  urlsplit(url).hostname or '', rank=len(found) + 1, provider='ahmia_onion', quality=.4))
        if len(found) >= limit:
            break
    if not found and not soup.select_one('#noResults'):
        raise ValueError('Onion index returned no usable v3 results or an unrecognized/challenge page.')
    return found


class AhmiaSearch(WebSearchProvider):
    name = 'ahmia_onion'

    def __init__(self, fetcher: PublicFetcher) -> None:
        self.fetcher = fetcher

    def search(self, query, limit=10, recency=None, domains=None):
        resource = self.fetcher.fetch(INDEX + '?' + urlencode({'q': query}), 2 * 1024 * 1024,
                                      {'text/html', 'application/xhtml+xml'}, require_tor=True)
        return parse_index(resource.content.decode('utf-8', errors='replace'), query, limit)


class OnionPublisherWebSearch(WebSearchProvider):
    """Discover public onion references through ordinary indexes, with every request Tor-bound."""
    name = 'public_onion_references'

    def __init__(self, fetcher: PublicFetcher) -> None:
        self.fetcher = fetcher
        self.searcher = SearchProviderChain([DuckDuckGoHtmlSearch(timeout=12, require_tor=True),
                                             BingRssSearch(timeout=12, require_tor=True)])

    def search(self, query, limit=10, recency=None, domains=None):
        # Two phrasings surface more real references: the strict "official website" wording misses pages
        # that simply publish a service's .onion. Results are merged and still deduplicated and onion-validated.
        hits, seen_hits = [], set()
        for phrase in (query + ' ".onion" official website', query + ' ".onion" address'):
            for hit in self.searcher.search_wide(phrase, limit=6):
                if hit.url not in seen_hits:
                    seen_hits.add(hit.url)
                    hits.append(hit)
        found, seen = [], set()
        def admit(url, title, origin):
            target = onion_url(url)
            if target and target not in seen:
                seen.add(target)
                found.append(SearchResult(title, target, 'Public onion reference from ' + origin,
                                          urlsplit(target).hostname or '', rank=len(found) + 1,
                                          provider=self.name, quality=.45))
        pages = 0
        failures = []
        for hit in hits:
            if onion_url(hit.url):
                admit(hit.url, hit.title, hit.url)
            else:
                # A .onion address printed in the result snippet is a public reference too.
                for token in re.findall(r'https?://[a-z2-7]{56}\.onion\b[^\s"\'<>]*', str(hit.snippet or ''), re.I):
                    admit(token, hit.title + ': published in a public listing', hit.url)
            if not onion_url(hit.url) and pages < 3:
                pages += 1
                try:
                    resource = self.fetcher.fetch(hit.url, 1024 * 1024, {'text/html', 'application/xhtml+xml'},
                                                  require_tor=True, timeout=10)
                    soup = BeautifulSoup(resource.content, 'html.parser')
                    advertiser = urlsplit(resource.final_url)
                    if advertiser.scheme == 'https' and not (advertiser.hostname or '').endswith('.onion'):
                        location = next((value for name, value in resource.headers.items() if name.lower() == 'onion-location'), '')
                        if location:
                            admit(location, hit.title + ': publisher Onion-Location', resource.final_url)
                        for meta in soup.select('meta[http-equiv][content]'):
                            if str(meta['http-equiv']).lower() == 'onion-location':
                                admit(str(meta['content']), hit.title + ': publisher Onion-Location', resource.final_url)
                    for anchor in soup.select('a[href]')[:400]:
                        target = urljoin(resource.final_url, str(anchor['href']))
                        admit(target, hit.title + ': ' + anchor.get_text(' ', strip=True)[:120], resource.final_url)
                except Exception as error:
                    failures.append(type(error).__name__)
            if len(found) >= limit:
                break
        if not found and (failures or self.searcher.last_errors):
            raise RuntimeError('Public onion references unavailable: ' + '; '.join(failures + self.searcher.last_errors))
        return found[:limit]


class PublisherOnionSearch(WebSearchProvider):
    """A publisher-maintained directory is useful when an onion index is unavailable."""
    name = 'tor_project_directory'

    def __init__(self, fetcher: PublicFetcher) -> None:
        self.fetcher, self._rows = fetcher, None
        self.publisher_hosts: dict[str, str] = {}

    def search(self, query, limit=10, recency=None, domains=None):
        if self._rows is None:
            resource = self.fetcher.fetch(PUBLISHER_DIRECTORY, 2 * 1024 * 1024, {'text/html'}, require_tor=True)
            soup = BeautifulSoup(resource.content, 'html.parser')
            self._rows = []
            for anchor in soup.select('a[href]')[:1000]:
                url = onion_url(urljoin(PUBLISHER_DIRECTORY, str(anchor['href'])))
                if url:
                    label = anchor.get_text(' ', strip=True) or url
                    # The onion address is nested below its clearnet publisher name.
                    for container in anchor.find_parents('li'):
                        clear = next((item for item in container.select('a[href]')
                                      if (urlsplit(str(item['href'])).hostname or '') == 'torproject.org'
                                      or (urlsplit(str(item['href'])).hostname or '').endswith('.torproject.org')), None)
                        if clear is not None:
                            label = urlsplit(str(clear['href'])).hostname or label
                            self.publisher_hosts[urlsplit(url).hostname or ''] = 'torproject.org'
                            break
                    self._rows.append((label, url))
        terms = token_set(query) - {'search', 'onion', 'dark', 'web', 'deep', 'research', 'official', 'documentation'}
        if not terms & {'tor', 'torproject', 'privacy', 'censorship'}:
            return []
        public = {'www.torproject.org': 100, 'support.torproject.org': 95, 'community.torproject.org': 90,
                  'blog.torproject.org': 85, 'onionservices.torproject.org': 80, 'metrics.torproject.org': 70}
        subject = terms - {'tor', 'torproject', 'project', 'privacy', 'censorship'}
        rows = sorted(self._rows, key=lambda row: (-len(subject & token_set(row[0])) * 1000 - public.get(row[0], 0), row[0]))
        rows = [SearchResult('Tor Project: ' + label, url, 'Onion link published at ' + PUBLISHER_DIRECTORY,
                             urlsplit(url).hostname or '', rank=index + 1, provider=self.name, quality=.8)
                for index, (label, url) in enumerate(rows)]
        return rows[:limit]


class _OnionPlan:
    @staticmethod
    def plan(query, mode, budget, official_only=False):
        # Onion indexes are small: precise queries work better than generic suffix expansion.
        return [PlannedQuery(query.strip(), 'onion_discovery', 1, 'Public onion index and publisher directory')]


class _DirectoryIndependence(SourceIndependenceAnalyzer):
    def __init__(self, directory: PublisherOnionSearch) -> None:
        self.directory = directory

    def annotate(self, sources):
        sources = super().annotate(sources)
        for source in sources:
            publisher = self.directory.publisher_hosts.get(source.domain)
            if publisher:
                source.independence_group = stable_id('lineage', publisher)
                source.metadata['onion_publisher_directory'] = {'publisher': publisher, 'source': PUBLISHER_DIRECTORY}
        return sources


def research_onion(parent, query: str, level: str = 'deep'):
    from app.privacy_masks import ROUTE
    from app.research.manager import ResearchManager

    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('Use a public onion research query of 1–500 characters.')
    if level not in {'quick', 'normal', 'deep'}:
        raise ValueError('Choose quick, normal or deep onion research.')
    check = _execution_check(parent)
    ensure = getattr(parent, 'ensure_tor', None)
    if ensure is not None:
        status = ensure()
        if not status.get('masked'):
            raise PermissionError('Tor is required and is not ready: ' + str(status.get('reason') or status.get('state') or 'bootstrap failed'))
    if ROUTE.proxy is None:
        raise PermissionError('Onion search requires Tor. Enable HAYAT Tor with hayat_mask_on first.')
    route = ROUTE.snapshot()
    timeout = max(5, min(60, float(parent.settings.get('onion_request_timeout_seconds', 30))))
    fetcher = TorRequiredFetcher('HAYAT-OnionResearch/1.0', timeout=timeout, delay=.5, respect_robots=True, check=check)
    directory = PublisherOnionSearch(fetcher)
    providers = [AhmiaSearch(fetcher), directory, OnionPublisherWebSearch(fetcher)]
    search = SearchProviderChain(providers)
    pages = {'quick': 3, 'normal': 6, 'deep': 10}[level]
    budget = ResearchBudget(searches=1, pages=pages, max_seconds={'quick': 120, 'normal': 180, 'deep': 240}[level],
                            max_bytes=12 * 1024 * 1024, min_sources=2, max_workers=2)
    try:
        # Keep evidence caches separate from normal web research and remove them after this run.
        with TemporaryDirectory(prefix='hayat-onion-research-') as scratch:
            child = ResearchManager({**parent.settings, 'database': str(Path(scratch) / 'evidence.db'), 'onion_research': True}, parent.model, search)
            child.fetcher.close()
            child.fetcher = fetcher
            child.query_planner = _OnionPlan()
            child.independence = _DirectoryIndependence(directory)
            try:
                result = child.research(query.strip(), 'deep', remember=False, _budget=budget)
                check()
                if ROUTE.snapshot() != route:
                    raise PermissionError('Tor route changed during onion research; results were not accepted. Retry with Tor ready.')
                result.trace['onion_scope'] = {'tor_required': True, 'indexes': [INDEX, PUBLISHER_DIRECTORY, 'Tor-bound public web onion references'],
                                              'scope': 'Public v3 onion discovery and linked evidence; index entries are unverified leads',
                                              'publisher_scope': 'Directory-declared Tor services are one publisher; other onion ownership is unverified',
                                              'temporary_evidence_cache': True, 'remembered': False}
                if search.last_errors:
                    result.errors.extend(error for error in search.last_errors if error not in result.errors)
                result.answer = ('Tor onion research — public sources; index listings do not verify the publisher or its claims.\n\n'
                                 + result.answer)
                if not result.complete:
                    result.answer += ('\n\nThe checked providers did not establish an answer. This does not show that no matching '
                                      'onion service exists. In Searches, choose "Tor web (public internet)" to research '
                                      'ordinary public sources through Tor, use Browse search manually, or read a known '
                                      'public v3 URL in Onion pages. No robots or access restrictions were bypassed.')
                return result
            finally:
                child.close()
    finally:
        fetcher.close()


def research_tor_web(parent, query: str, level: str = 'deep', *, check=None):
    """Ordinary public-web research through Tor; never mislabeled as onion-only."""
    from app.privacy_masks import ROUTE
    from app.research.manager import ResearchManager
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('Use a Tor web query of 1–500 characters.')
    if level not in {'quick', 'normal', 'deep'}:
        raise ValueError('Choose quick, normal or deep Tor web research.')
    check = _execution_check(parent, check)
    ensure = getattr(parent, 'ensure_tor', None)
    if ensure is not None and not ensure().get('masked'):
        raise PermissionError('Tor is required and is not ready. No direct fallback was used.')
    if ROUTE.proxy is None:
        raise PermissionError('Tor web research requires Tor; no direct fallback was used.')
    route = ROUTE.snapshot()
    timeout = max(5, min(30, float(parent.settings.get('onion_request_timeout_seconds', 20))))
    fetcher = TorRequiredFetcher('HAYAT-TorWebResearch/1.0', timeout=timeout, delay=.5, respect_robots=True, check=check)
    search = SearchProviderChain([DuckDuckGoHtmlSearch(timeout=timeout, require_tor=True),
                                  BingRssSearch(timeout=timeout, require_tor=True)])
    budget = ResearchBudget(searches=2, pages={'quick': 3, 'normal': 6, 'deep': 8}[level],
                            max_seconds=150, max_bytes=8 * 1024 * 1024, min_sources=2, max_workers=2)
    try:
        with TemporaryDirectory(prefix='hayat-tor-web-') as scratch:
            child = ResearchManager({**parent.settings, 'database': str(Path(scratch) / 'evidence.db')}, parent.model, search)
            child.fetcher.close()
            child.fetcher = fetcher
            try:
                result = child.research(query.strip(), level, remember=False, _budget=budget)
                check()
                if ROUTE.snapshot() != route:
                    raise PermissionError('Tor route changed during public-web research; results discarded.')
                result.trace['tor_web_scope'] = {'tor_required': True, 'onion_only': False,
                                                 'temporary_evidence_cache': True, 'remembered': False}
                result.errors.extend(error for error in search.last_errors if error not in result.errors)
                result.answer = ('Public-web research through Tor — these sources can be ordinary websites, not onion services.\n\n'
                                 + result.answer)
                return result
            finally:
                child.close()
    finally:
        fetcher.close()
