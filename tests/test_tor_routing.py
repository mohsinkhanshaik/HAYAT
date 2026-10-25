"""Tor-bound retrieval: DNS boundaries, onion checks, proxy loss and redirects."""
import base64
import hashlib
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from app.privacy_masks import ROUTE, PrivacyMasks, Route, TorMask
from app.research.fetcher import PublicFetcher
from app.research.safety import UnsafeUrlError, validate_public_url


def onion():
    key = bytes(range(32))
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + key + version).digest()[:2]
    return base64.b32encode(key + checksum + version).decode().lower() + ".onion"


@pytest.mark.parametrize("suffix", ["", "."])
def test_valid_onion_is_tor_only_and_never_uses_local_dns(monkeypatch, suffix):
    def no_dns(*args, **kwargs):
        pytest.fail("onion URL validation leaked a local DNS query")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    url = "http://" + onion() + suffix + "/docs#section"
    with pytest.raises(UnsafeUrlError, match="require"):
        validate_public_url(url)
    assert validate_public_url(url, via_tor=True).endswith("/docs")


@pytest.mark.parametrize("host", ["localhost", "a.local", "a.localhost", "a.internal", "a.lan", "a.home",
                                    "127.0.0.1", "127.1", "2130706433", "0x7f000001", "10.0.0.1",
                                    "[::1]", "[::ffff:127.0.0.1]", "invalid.onion", "a" * 56 + ".onion"])
def test_tor_preflight_rejects_local_or_invalid_targets_without_dns(monkeypatch, host):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("unexpected local DNS"))
    with pytest.raises(UnsafeUrlError):
        validate_public_url("http://" + host + "/", via_tor=True)


def test_proxy_change_during_redirect_cannot_fall_back_to_direct(monkeypatch):
    requests = []
    ROUTE.set("socks5://127.0.0.1:9052")
    fetcher = PublicFetcher("HAYAT-test", delay=0, respect_robots=False)

    def handler(request):
        requests.append(request)
        ROUTE.set(None)
        return httpx.Response(302, headers={"Location": "https://second.example.org/"})

    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("route change must not resolve directly"))
    try:
        with pytest.raises(PermissionError, match="route changed"):
            fetcher.fetch("https://example.org/")
        assert len(requests) == 1
    finally:
        fetcher.close()


def test_route_toggle_during_client_creation_never_labels_direct_pool_as_masked(monkeypatch):
    original = httpx.Client
    first = True

    def build(*args, **kwargs):
        nonlocal first
        client = original(*args, **kwargs)
        if first:
            first = False
            ROUTE.set("socks5://127.0.0.1:9052")
        return client

    ROUTE.set(None)
    monkeypatch.setattr(httpx, "Client", build)
    fetcher = PublicFetcher("HAYAT-test")
    try:
        with pytest.raises(PermissionError, match="route changed"):
            fetcher.client
        masked = fetcher.client
        assert type(masked._transport_for_url(httpx.URL("https://example.org"))._pool).__name__ == "SOCKSProxy"
    finally:
        fetcher.close()


def test_proxy_failure_never_retries_as_a_direct_request(monkeypatch):
    requests = []
    ROUTE.set("socks5://127.0.0.1:9052")
    fetcher = PublicFetcher("HAYAT-test", delay=0, respect_robots=False)

    def handler(request):
        requests.append(request)
        ROUTE.set(None)
        raise httpx.ConnectError("Tor stopped")

    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("app.research.fetcher.time.sleep", lambda seconds: None)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("proxy failure must not leak DNS"))
    try:
        with pytest.raises(PermissionError, match="route changed"):
            fetcher.fetch("https://example.org/")
        assert len(requests) == 1
    finally:
        fetcher.close()


def test_cross_origin_redirect_drops_caller_headers(monkeypatch):
    ROUTE.set("socks5://127.0.0.1:9052")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.example.org/"}) if len(seen) == 1 else httpx.Response(200, text="Public result")

    fetcher = PublicFetcher("HAYAT-test", delay=0, respect_robots=False)
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        fetcher.fetch("https://example.org/", request_headers={"Authorization": "Bearer synthetic-secret",
                      "Cookie": "private=synthetic", "X-Private-Context": "owner-test"})
        assert "authorization" in seen[0].headers
        for header in ("authorization", "cookie", "x-private-context"):
            assert header not in seen[1].headers
    finally:
        fetcher.close()


def test_real_socks_transport_sends_onion_hostname_to_proxy_not_local_dns(monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    captured, errors = [], []

    def exact(connection, length):
        data = b""
        while len(data) < length:
            chunk = connection.recv(length - len(data))
            if not chunk:
                raise RuntimeError("SOCKS peer closed early")
            data += chunk
        return data

    def serve():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(10)
                version, methods = exact(connection, 2)
                assert version == 5
                exact(connection, methods)
                connection.sendall(b"\x05\x00")
                header = exact(connection, 4)
                assert header == b"\x05\x01\x00\x03"  # domain name, not locally resolved IP
                host = exact(connection, exact(connection, 1)[0]).decode("ascii")
                port = int.from_bytes(exact(connection, 2), "big")
                captured.append((host, port))
                connection.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
                request = b""
                while b"\r\n\r\n" not in request:
                    request += exact(connection, 1)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 14\r\nConnection: close\r\n\r\nonion fixture\n")
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    original = socket.getaddrinfo

    def dns(host, *args, **kwargs):
        assert host == "127.0.0.1", f"destination DNS leaked: {host}"
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    ROUTE.set(f"socks5://127.0.0.1:{listener.getsockname()[1]}")
    fetcher = PublicFetcher("HAYAT-test", timeout=5, delay=0, respect_robots=False)
    try:
        resource = fetcher.fetch("http://" + onion() + "/fixture")
        assert resource.content == b"onion fixture\n"
        worker.join(10)
        assert not worker.is_alive() and not errors
        assert captured == [(onion(), 80)]
    finally:
        fetcher.close()
        listener.close()


def test_required_but_dead_tor_is_blocked_and_status_never_probes_direct(tmp_path):
    asked = []
    route = Route()
    route.set("socks5://127.0.0.1:9052")
    tor = TorMask(tmp_path / "tor", tmp_path / "state", route=route, settings=tmp_path / "settings.json")
    masks = PrivacyMasks(tor, network=lambda: {"available": False}, direct_json=lambda url: asked.append(url),
                         masked_json=lambda url: asked.append(url))
    result = masks.status()
    assert result["tor"]["state"] == "blocked" and not result["tor"]["masked"]
    assert result["tor"]["routing_required"] and not asked
    assert result["coverage"]["guaranteed_untrackable"] is False


def test_masked_public_client_does_not_retain_tracking_cookies():
    ROUTE.set("socks5://127.0.0.1:9052")
    fetcher = PublicFetcher("HAYAT-test")
    try:
        # Exercise the actual HTTPX cookie extraction against its real masked
        # client, without sending anything over the proxy.
        client = fetcher.client
        request = httpx.Request("GET", "https://example.org/")
        response = httpx.Response(200, request=request, headers={"Set-Cookie": "tracking=synthetic; Path=/; Secure"})
        client.cookies.extract_cookies(response)
        assert not list(client.cookies.jar)
        assert "cookie" not in client.build_request("GET", "https://example.org/next").headers
    finally:
        fetcher.close()


def test_socks_proxy_that_never_replies_cannot_hold_request_indefinitely():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.listen(1)
    listener.settimeout(5)
    release = threading.Event()

    def stalled_proxy():
        try:
            with listener.accept()[0] as connection:
                connection.recv(100)
                release.wait(5)  # no SOCKS reply at all
        finally:
            listener.close()

    worker = threading.Thread(target=stalled_proxy, daemon=True)
    worker.start()
    ROUTE.set(f"socks5://127.0.0.1:{port}")
    fetcher = PublicFetcher("HAYAT-test", timeout=.15, delay=0, respect_robots=False)
    started = time.monotonic()
    try:
        with pytest.raises(httpx.ReadTimeout):
            fetcher.fetch("http://" + onion() + "/")
        assert time.monotonic() - started < 2
        assert ROUTE.proxy is not None
    finally:
        fetcher.close()
        release.set()
        worker.join(5)
        assert not worker.is_alive()


def test_search_queries_use_the_same_required_tor_transport(monkeypatch):
    from app.research.search import DuckDuckGoHtmlSearch

    calls = []
    ROUTE.set("socks5://127.0.0.1:9052")

    def fetch(self, url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>", final_url=url)

    monkeypatch.setattr(PublicFetcher, "fetch", fetch)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("query leaked via direct HTTP"))
    assert DuckDuckGoHtmlSearch().search("synthetic research query") == []
    assert len(calls) == 1 and calls[0][1]["require_tor"]
    assert "synthetic" in calls[0][0]


def test_remembered_tor_intent_is_preserved_without_background_services(monkeypatch, tmp_path):
    import app.privacy_masks as masks
    from app.runtime import build_runtime

    settings = tmp_path / "privacy-masks.json"
    settings.write_text('{"tor_for_hayat":true}')
    monkeypatch.setattr(masks, "SETTINGS_FILE", settings)
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        report = runtime.registry.privacy_masks.tor.status()
        assert report["routing_required"] and not report["masked"]
        assert ROUTE.proxy == "socks5://127.0.0.1:9052"
        assert runtime.registry.privacy_masks.tor.scanner is not None
    finally:
        runtime.close()


def test_browser_fallback_cannot_use_direct_network_while_tor_required(tmp_path, monkeypatch):
    from app.research.manager import ResearchManager
    from app.research.models import SearchResult
    from app.research.knowledge import KnowledgeStore
    from tests.test_research import settings

    manager = ResearchManager(settings(tmp_path), model=None, knowledge=KnowledgeStore(tmp_path / "knowledge.db"))
    navigations = []
    manager.attach_browser(SimpleNamespace(navigate=lambda *a: navigations.append(a)))
    ROUTE.set("socks5://127.0.0.1:9052")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("browser preflight leaked DNS"))
    try:
        with pytest.raises(PermissionError, match="Tor"):
            manager.capture_page("https://example.org/", "topic")
        with pytest.raises(PermissionError, match="Tor"):
            manager._browser_fetch(SearchResult("Example", "https://example.org/", "", "example.org"), "topic")
        assert not navigations
    finally:
        manager.close()


def test_app_link_preflight_uses_the_same_tor_aware_validator(tmp_path, monkeypatch):
    from app.rea_links import PublicAppLink

    ROUTE.set("socks5://127.0.0.1:9052")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: pytest.fail("app-link preflight leaked DNS"))
    validated = []

    class Fetcher:
        def validate_url(self, url):
            validated.append(url)
            return validate_public_url(url, via_tor=True)

        def fetch(self, url, **kwargs):
            raise RuntimeError("end of isolated preflight check")

    with pytest.raises(RuntimeError, match="isolated preflight"):
        PublicAppLink(Fetcher(), lambda path: {}, tmp_path).inspect("http://" + onion() + "/")
    assert validated == ["http://" + onion() + "/"]
