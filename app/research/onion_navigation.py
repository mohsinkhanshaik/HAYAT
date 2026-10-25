"""Bounded, read-only navigation of a user-selected public v3 onion service.

No scripts, credentials, cookies, forms, browser fallback or persistent page cache.
The fetcher deliberately has stricter redirect and robots rules than web research:
every transfer must remain on the selected onion origin, through the same Tor route.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import hashlib
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.research.extractor import ContentExtractor
from app.research.fetcher import FetchedResource
from app.research.injection import assess_prompt_injection, sanitize_for_synthesis
from app.research.onion import TorRequiredFetcher, onion_url
from app.research.safety import validate_public_url
from app.research.text import token_set


_ACCEPT = {'text/html', 'application/xhtml+xml', 'text/plain'}
_SECRET_KEY = re.compile(r'(?i)(?:^|[_-])(?:password|passwd|secret|token|api[_-]?key|authorization|session|auth)(?:$|[_-])')
_CHALLENGE = re.compile(r'(?i)\b(?:verify (?:you are|that you are) human|checking your browser|captcha|access denied|authentication required)\b')
_CONTROLS: ContextVar[dict[str, Any]] = ContextVar('onion_navigation_controls', default={})


@contextmanager
def navigation_controls(*, cancel: Any = None, check: Callable[[], None] | None = None,
                        progress: Callable[[dict[str, Any]], None] | None = None):
    """Attach GUI job controls without bypassing normal registry authorization."""
    token = _CONTROLS.set({'cancel': cancel, 'check': check, 'progress': progress})
    try:
        yield
    finally:
        _CONTROLS.reset(token)


class OnionNavigationCancelled(PermissionError):
    pass


def _safe_reference(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('Use a public URL of at most 4096 characters.')
    result = validate_public_url(url, via_tor=True)
    if any(_SECRET_KEY.search(key) for key, _ in parse_qsl(urlsplit(result).query, keep_blank_values=True)):
        raise PermissionError('Public navigation does not accept credential or session query parameters.')
    return result


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    return parts.scheme.lower(), (parts.hostname or '').lower(), parts.port or (443 if parts.scheme == 'https' else 80)


class OnionNavigationFetcher(TorRequiredFetcher):
    """Use existing public retry/transfer machinery, with cooperative whole-run limits."""

    def __init__(self, url: str, deadline: float, guard: Callable[[], None], timeout: float = 25) -> None:
        super().__init__('HAYAT-OnionNavigation/1.0', timeout=timeout, delay=.5, respect_robots=True)
        self.origin = _origin(url)
        self.deadline, self.guard = deadline, guard

    def _check(self) -> float:
        self.guard()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Onion navigation reached its whole-run time budget.')
        return min(self.timeout, remaining)

    def validate_url(self, url: str) -> str:
        self._check()
        value = super().validate_url(_safe_reference(url))
        if not onion_url(value) or _origin(value) != self.origin:
            raise PermissionError('Onion navigation stays on the selected onion origin; cross-origin redirects are refused.')
        return value

    def _rate_limit(self, host: str) -> None:
        # This instance has one sequential owner, avoiding a long blocking wait
        # while still enforcing the public per-origin delay.
        wait_until = self._last_request.get(host, 0) + self.delay
        while wait_until > time.monotonic():
            self._check()
            time.sleep(min(.1, wait_until - time.monotonic()))
        self._check()
        self._last_request[host] = time.monotonic()

    def _read(self, response: httpx.Response, max_bytes: int) -> bytes:
        declared = response.headers.get('content-length')
        if declared is not None and int(declared) > max_bytes:
            raise ValueError(f'Response exceeds the {max_bytes} byte limit.')
        chunks, total = [], 0
        for chunk in response.iter_bytes():
            self._check()
            self._check_route()
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f'Response exceeds the {max_bytes} byte limit.')
            chunks.append(chunk)
        self._check()
        return b''.join(chunks)

    def _allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f'{parts.scheme}://{parts.netloc}'
        parser = self._robots.get(origin)
        if parser is None:
            robots_url = self.validate_url(origin + '/robots.txt')
            self._rate_limit(parts.hostname or '')
            with self.client.stream('GET', robots_url, timeout=self._check(), follow_redirects=False) as response:
                # A robots redirect or transient failure is not permission to
                # crawl. A missing robots file is the publisher's normal opt-out.
                if response.status_code in {401, 403}:
                    return False
                if response.status_code in {404, 410}:
                    lines = []
                else:
                    if response.is_redirect:
                        raise PermissionError('robots.txt redirected; automatic onion navigation is paused.')
                    response.raise_for_status()
                    lines = self._read(response, 128 * 1024).decode('utf-8', errors='replace').splitlines()
            parser = RobotFileParser()
            parser.parse(lines)
            self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    def _fetch_once(self, url: str, max_bytes: int, accept: set[str] | None,
                    request_headers: dict[str, str] | None, timeout: float) -> FetchedResource:
        current = requested = self.validate_url(url)
        started = time.perf_counter()
        # Each native socket operation has a shortened timeout as the total
        # budget shrinks; an in-flight operation may finish before cancellation.
        attempt_deadline = min(self.deadline, time.monotonic() + timeout)
        for redirects in range(6):
            if not self._allowed_by_robots(current):
                raise PermissionError('robots.txt disallows this public onion page.')
            self._rate_limit(urlsplit(current).hostname or '')
            remaining = min(self._check(), attempt_deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError('Onion page request time budget exhausted.')
            with self.client.stream('GET', current, timeout=remaining, follow_redirects=False) as response:
                self._check()
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location or redirects == 5:
                        raise httpx.TooManyRedirects('Onion navigation redirect limit reached.')
                    from urllib.parse import urljoin
                    current = self.validate_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').split(';', 1)[0].lower()
                if content_type not in (accept or _ACCEPT):
                    raise ValueError('Only public HTML and plain-text onion pages can be read.')
                content = self._read(response, max_bytes)
                self._check_route()
                return FetchedResource(requested, str(response.url), response.status_code, dict(response.headers), content,
                                       int((time.perf_counter() - started) * 1000), datetime.now(UTC).isoformat(),
                                       hashlib.sha256(content).hexdigest())
        raise httpx.TooManyRedirects('Onion navigation redirect limit reached.')


def navigate_onion(parent: Any, url: str, max_pages: int = 1, max_depth: int = 0, query: str = '', *,
                   cancel: Any = None, check: Callable[[], None] | None = None,
                   progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Read one selected onion page, optionally following bounded same-origin links.

    ``check`` should validate a captured execution-interlock ticket. Cancellation
    discards results; ordinary page errors retain an honest partial source report.
    The output records retrieved evidence, never verifies the publisher's claims.
    """
    from app.privacy_masks import ROUTE

    start = time.monotonic()
    controls = _CONTROLS.get()
    cancel = cancel if cancel is not None else controls.get('cancel')
    progress = progress if progress is not None else controls.get('progress')
    if type(max_pages) is not int or not 1 <= max_pages <= 8:
        raise ValueError('Choose 1–8 onion pages.')
    if type(max_depth) is not int or not 0 <= max_depth <= 2:
        raise ValueError('Choose link depth 0–2; depth 0 reads only the selected page.')
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError('Use an optional navigation topic of at most 500 characters.')
    url = _safe_reference(url)
    if not onion_url(url):
        raise ValueError('Start navigation with a valid public v3 .onion URL.')

    def cancelled() -> None:
        context_check = controls.get('check')
        if context_check is not None and context_check is not check:
            context_check()
        if check is not None:
            check()
        if cancel is not None and (cancel.is_set() if hasattr(cancel, 'is_set') else cancel()):
            raise OnionNavigationCancelled('Onion navigation cancelled; results were discarded.')

    cancelled()
    if progress is not None:
        progress({'stage': 'tor', 'url': url, 'message': 'Checking Tor before reading the selected onion page.'})
    ensure = getattr(parent, 'ensure_tor', None)
    if ensure is not None:
        status = ensure()
        if not status.get('masked'):
            raise PermissionError('Tor is not ready; public onion navigation cannot start.')
    route = ROUTE.snapshot()
    if route[0] is None:
        raise PermissionError('Onion navigation requires Tor; direct access is refused.')

    def guard() -> None:
        cancelled()
        if ROUTE.snapshot() != route:
            raise OnionNavigationCancelled('Tor route changed during navigation; results were discarded.')

    settings = getattr(parent, 'settings', {})
    seconds = max(10, min(180, float(settings.get('onion_navigation_seconds', 90))))
    request_seconds = max(5, min(30, float(settings.get('onion_request_timeout_seconds', 25))))
    deadline = time.monotonic() + seconds
    fetcher = OnionNavigationFetcher(url, deadline, guard, request_seconds)
    extractor = ContentExtractor()
    queue = deque([(url, 0, '')])
    scheduled, pages, errors = {url}, [], []
    accepted_bytes, attempted, text_remaining = 0, 0, 32_000
    stop_reason = 'selected_scope_finished'
    budget_bytes = 4 * 1024 * 1024
    try:
        while queue and attempted < max_pages:
            guard()
            if time.monotonic() >= deadline:
                stop_reason = 'time_budget'
                break
            if accepted_bytes >= budget_bytes:
                stop_reason = 'byte_budget'
                break
            target, depth, via = queue.popleft()
            attempted += 1
            if progress is not None:
                progress({'stage': 'fetch', 'url': target, 'pages_attempted': attempted,
                          'message': f'Reading public page {attempted}/{max_pages}; robots and Tor are required.'})
            try:
                resource = fetcher.fetch(target, min(1024 * 1024, budget_bytes - accepted_bytes), _ACCEPT,
                                         timeout=min(request_seconds, deadline - time.monotonic()))
                guard()
                # Validate again before accepting even injected/test transports.
                final_url = fetcher.validate_url(resource.final_url)
                accepted_bytes += len(resource.content)
                document, _ = extractor.extract(resource, query)
                injection = assess_prompt_injection(document.text)
                quarantined = bool(injection['quarantine'])
                text = '' if quarantined else sanitize_for_synthesis(document.text)
                limit = min(12_000, text_remaining)
                excerpt = text[:limit]
                text_remaining -= len(excerpt)
                challenge = bool(_CHALLENGE.search(document.title + '\n' + document.text[:1500]))
                links, seen_links = [], set()
                for link in document.links:
                    try:
                        reference = _safe_reference(link['url'])
                    except (ValueError, PermissionError):
                        continue
                    if reference in seen_links:
                        continue
                    seen_links.add(reference)
                    same_origin = _origin(reference) == _origin(url)
                    links.append({'url': reference, 'text': link['text'][:200], 'same_origin': same_origin,
                                  'followable': same_origin and not quarantined and not challenge})
                    if len(links) >= 100:
                        break
                citation = 'O' + str(len(pages) + 1)
                pages.append({'citation': citation, 'url': target, 'final_url': final_url, 'title': document.title[:500],
                              'retrieved_at': resource.retrieved_at, 'sha256': resource.sha256,
                              'status_code': resource.status_code, 'content_type': document.content_type,
                              'bytes': len(resource.content), 'depth': depth, 'followed_from': via,
                              'text': excerpt, 'text_truncated': len(excerpt) < len(text),
                              'description': sanitize_for_synthesis(document.description)[:1000] if not quarantined else '',
                              'headings': document.headings[:20] if not quarantined else [], 'links': links,
                              'published_at': document.published_at, 'updated_at': document.updated_at,
                              'quarantined': quarantined, 'injection_assessment': injection,
                              'interaction_required': challenge, 'trust': 'untrusted_external_data'})
                if progress is not None:
                    progress({'stage': 'page', 'url': final_url, 'pages_retrieved': len(pages),
                              'message': f'Page retrieved: {document.title[:150]}'})
                if depth < max_depth and not quarantined and not challenge:
                    ranked = sorted((item for item in links if item['followable']),
                                    key=lambda item: -len(token_set(query) & token_set(item['text'] + ' ' + urlsplit(item['url']).path)))
                    for item in ranked:
                        if len(scheduled) >= max_pages:
                            break
                        if item['url'] not in scheduled:
                            scheduled.add(item['url'])
                            queue.append((item['url'], depth + 1, citation))
            except OnionNavigationCancelled:
                raise
            except Exception as exc:
                # Recheck after failures so stop/resume cannot revive stale work.
                guard()
                if isinstance(exc, TimeoutError):
                    stop_reason = 'time_budget'
                errors.append({'url': target, 'error_type': type(exc).__name__, 'message': str(exc)[:500]})
                if progress is not None:
                    progress({'stage': 'error', 'url': target, 'message': type(exc).__name__ + ': ' + str(exc)[:250]})
        guard()
        if queue and stop_reason == 'selected_scope_finished':
            stop_reason = 'page_budget'
    finally:
        fetcher.close()
    usable = sum(not page['quarantined'] and not page['interaction_required'] and bool(page['text']) for page in pages)
    return {'start_url': url, 'query': query, 'status': 'complete' if usable == len(pages) and usable and not errors else 'partial' if usable else 'unavailable',
            'scope': 'Public same-origin v3 onion HTML/text through Tor; citations record retrieval, not independently verified facts.',
            'tor_required': True, 'route_unchanged': True, 'pages': pages, 'errors': errors,
            'citations': [{'id': page['citation'], 'url': page['final_url'], 'title': page['title'],
                           'retrieved_at': page['retrieved_at'], 'sha256': page['sha256']} for page in pages],
            'stop_reason': stop_reason, 'pages_attempted': attempted, 'pages_retrieved': len(pages), 'usable_pages': usable,
            'bytes_accepted': accepted_bytes, 'elapsed_ms': int((time.monotonic() - start) * 1000),
            'budget': {'max_pages': max_pages, 'max_depth': max_depth, 'max_seconds': seconds,
                       'max_bytes_per_page': 1024 * 1024, 'max_accepted_bytes': budget_bytes, 'max_report_text_chars': 32_000},
            'privacy': {'persistent_page_cache': False, 'cookies': False, 'scripts_executed': False,
                        'forms_submitted': False, 'request_urls_may_remain_in_transfer_logs': True},
            'limitations': ['Public pages only; unavailable, robots-blocked and interactive pages stay unresolved.',
                            'Same service is one publisher, not independent corroboration.',
                            'In-flight socket operations can finish before cancellation takes effect.',
                            'Tor routing is not a guarantee of anonymity or complete dark-web coverage.']}
