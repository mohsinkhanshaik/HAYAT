from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.robotparser
from http.cookiejar import CookieJar, DefaultCookiePolicy
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit

import httpx

from app.research.safety import validate_public_url


class _NoTrackingCookies(DefaultCookiePolicy):
    """Public Tor reads do not establish ambient cross-request cookie identity."""
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


@dataclass(slots=True)
class FetchedResource:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    content: bytes
    elapsed_ms: int
    retrieved_at: str
    sha256: str


class PublicFetcher:
    """Small public-only HTTP client with SSRF, robots, size and rate limits."""

    #: The shortest ceiling for one whole download, however small the per-read timeout.
    MIN_DOWNLOAD_SECONDS = 60.0

    def __init__(self, user_agent: str, timeout: float = 20, delay: float = 0.35, respect_robots: bool = True) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.respect_robots = respect_robots
        self._http: httpx.Client | None = None
        self._route_version = -1
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._request_route = threading.local()

    def _checked_route_snapshot(self) -> tuple[str | None, int]:
        from app.privacy_masks import ROUTE

        current = ROUTE.snapshot()
        expected = getattr(self._request_route, "snapshot", current)
        if current != expected:
            raise PermissionError("Privacy route changed during retrieval; start a new request.")
        return expected

    def _check_route(self) -> str | None:
        return self._checked_route_snapshot()[0]

    def validate_url(self, url: str) -> str:
        proxy = self._check_route()
        if proxy is None:
            return validate_public_url(url)
        parts = urlsplit(proxy)
        if parts.scheme not in {"socks5", "socks5h"} or parts.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise PermissionError("Public masked retrieval requires a local Tor SOCKS route.")
        return validate_public_url(url, via_tor=True)

    @property
    def client(self) -> httpx.Client:
        """The connection, rebuilt when HAYAT's Tor mask is switched on or off (app/privacy_masks.py).

        Everything this fetcher reads is public (private addresses are refused), so the whole client goes
        through the mask when it is on; local services never use this class.
        """
        from app.privacy_masks import ROUTE

        if self._checked_route_snapshot()[0] is not None and self._route_version is not None:
            ROUTE.ensure_ready()  # outside the lock: a cold Tor start takes a while
        with self._client_lock:  # its own lock: self._lock is held around rate limits and robots
            proxy, version = self._checked_route_snapshot()
            if self._http is not None and self._route_version is None:
                return self._http  # a client handed in (tests, a mock transport) is used as given
            if self._http is None or self._route_version != version:
                from app.research.tor_transport import tor_transport
                # The old connection is left to finish whatever another thread is still downloading on it.
                self._http = httpx.Client(
                    timeout=httpx.Timeout(self.timeout),
                    follow_redirects=False,
                    transport=tor_transport(proxy, self.timeout) if proxy else None,
                    trust_env=False,
                    cookies=CookieJar(policy=_NoTrackingCookies()) if proxy else None,
                    headers={"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml,application/json,application/pdf,text/plain;q=0.9,*/*;q=0.2"},
                )
                self._route_version = version
            if ROUTE.snapshot() != (proxy, version):
                raise PermissionError("Privacy route changed while creating the connection; start a new request.")
            return self._http

    @client.setter
    def client(self, value: httpx.Client) -> None:
        with self._client_lock:
            self._http, self._route_version = value, None

    def close(self) -> None:
        if self._http is not None:
            self._http.close()

    def _rate_limit(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            scheduled = max(now, self._last_request.get(host, 0.0) + self.delay)
            self._last_request[host] = scheduled
        if scheduled > now:
            time.sleep(scheduled - now)

    def _allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            parser = self._robots.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(origin, "/robots.txt"))
            try:
                response = self.client.get(parser.url, follow_redirects=False)
                parser.parse(response.text.splitlines() if response.status_code < 400 else [])
            except httpx.HTTPError:
                parser.parse([])
            with self._lock:
                self._robots[origin] = parser
        return parser.can_fetch(self.user_agent, url)

    def fetch(self, url: str, max_bytes: int = 4 * 1024 * 1024, accept: set[str] | None = None, request_headers: dict[str, str] | None = None,
              timeout: float | None = None, *, require_tor: bool = False) -> FetchedResource:
        """Recover transient GET failures within the original request time budget; every request joins the transfer log."""
        from app.transfers import transfers
        from app.privacy_masks import ROUTE

        started = time.perf_counter()
        previous = getattr(self._request_route, "snapshot", None)
        self._request_route.snapshot = ROUTE.snapshot()
        try:
            if require_tor and self._request_route.snapshot[0] is None:
                raise PermissionError("This retrieval requires Tor; direct access is refused.")
            resource = self._fetch_with_retries(url, max_bytes, accept, request_headers, timeout)
        except Exception as exc:
            transfers().web(url, purpose=self.purpose, size=0, ms=int((time.perf_counter() - started) * 1000), status="failed",
                            error=type(exc).__name__)
            raise
        finally:
            if previous is None:
                del self._request_route.snapshot
            else:
                self._request_route.snapshot = previous
        transfers().web(resource.final_url or url, purpose=self.purpose, size=len(resource.content or b""), ms=resource.elapsed_ms)
        return resource

    @property
    def purpose(self) -> str:
        """"HAYAT-Markets/1.0" → "Markets": what this fetcher is for, shown in the Downloads window."""
        label = self.user_agent.split("/", 1)[0]
        return label[6:] if label.startswith("HAYAT-") else label

    def _fetch_with_retries(self, url: str, max_bytes: int, accept: set[str] | None, request_headers: dict[str, str] | None,
                            timeout: float | None) -> FetchedResource:
        started = time.perf_counter()
        deadline = started + (timeout if timeout is not None else self.timeout)
        for attempt in range(3):
            remaining = max(.001, deadline - time.perf_counter())
            try:
                resource = self._fetch_once(url, max_bytes, accept, request_headers, remaining)
                resource.elapsed_ms = int((time.perf_counter() - started) * 1000)
                return resource
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                    httpx.ReadError, httpx.RemoteProtocolError):
                if attempt == 2 or deadline - time.perf_counter() <= .5 * (attempt + 1):
                    raise
            except httpx.HTTPStatusError as exc:
                # Respect publisher cooldowns (429/Retry-After) and do not retry
                # access denials or invalid responses.
                if (exc.response.status_code not in {502, 503, 504}
                        or 'retry-after' in exc.response.headers or attempt == 2
                        or deadline - time.perf_counter() <= .5 * (attempt + 1)):
                    raise
            time.sleep(.5 * (attempt + 1))
        raise RuntimeError('Public retrieval exhausted its attempts')

    def _fetch_once(self, url: str, max_bytes: int, accept: set[str] | None,
                    request_headers: dict[str, str] | None, timeout: float) -> FetchedResource:
        """`timeout` shortens one request, e.g. a liveness check that must not stall a reply."""
        current = self.validate_url(url)
        requested = current
        started = time.perf_counter()
        # httpx's timeouts are per read: a server trickling a few bytes at a time could hold a request for ever (a feed
        # refresh stalled 17 minutes on 2026-10-07, holding up every refresh behind it). The whole download gets a
        # ceiling too: several read timeouts, never under a minute.
        ceiling = started + max(self.MIN_DOWNLOAD_SECONDS, 6 * (timeout or self.timeout))
        redirects = 0
        while True:
            if not self._allowed_by_robots(current):
                raise PermissionError(f"robots.txt disallows retrieval: {current}")
            host = urlsplit(current).hostname or ""
            self._rate_limit(host)
            with self.client.stream("GET", current, headers=request_headers,
                                    timeout=httpx.Timeout(timeout) if timeout else httpx.USE_CLIENT_DEFAULT) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirects >= 5:
                        raise httpx.TooManyRedirects("Redirect limit reached")
                    redirected = self.validate_url(urljoin(current, location))
                    old_origin, new_origin = urlsplit(current), urlsplit(redirected)
                    if (old_origin.scheme, old_origin.hostname, old_origin.port) != (new_origin.scheme, new_origin.hostname, new_origin.port):
                        # Caller-supplied credentials and custom identifying
                        # headers belong to the original origin, not a redirect.
                        request_headers = None
                    current = redirected
                    redirects += 1
                    continue
                if response.status_code == 304:
                    return FetchedResource(requested, str(response.url), 304, {key.lower(): value for key, value in response.headers.items()}, b"", int((time.perf_counter() - started) * 1000), datetime.now(UTC).isoformat(), "")
                response.raise_for_status()
                content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
                def accepted(pattern: str) -> bool:
                    if pattern.endswith("/*"):
                        return content_type.startswith(pattern[:-1])
                    if "*+" in pattern:
                        prefix, suffix = pattern.split("*", 1)
                        return content_type.startswith(prefix) and content_type.endswith(suffix)
                    return content_type == pattern
                if accept and not any(accepted(item) for item in accept):
                    raise ValueError(f"Unsupported content type: {content_type}")
                declared = int(response.headers.get("content-length", "0") or 0)
                if declared > max_bytes:
                    raise ValueError(f"Response exceeds {max_bytes} byte limit.")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"Response exceeds {max_bytes} byte limit.")
                    if time.perf_counter() > ceiling:
                        raise httpx.ReadTimeout(f"Download did not finish within {ceiling - started:.0f} s; stopped.")
                    chunks.append(chunk)
                content = b"".join(chunks)
                headers = {key.lower(): value for key, value in response.headers.items()}
                final = str(response.url)
                status = response.status_code
                break
        return FetchedResource(
            requested, final, status, headers, content,
            int((time.perf_counter() - started) * 1000), datetime.now(UTC).isoformat(), hashlib.sha256(content).hexdigest(),
        )

    def get_json(self, url: str, max_bytes: int = 2 * 1024 * 1024) -> dict | list:
        resource = self.fetch(url, max_bytes, {"application/json", "application/*+json", "text/json"})
        return json.loads(resource.content)
