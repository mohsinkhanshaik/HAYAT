import httpx
import pytest

from app.research import fetcher as module


def client(monkeypatch, handler, **kwargs):
    fetcher = module.PublicFetcher('test', delay=0, respect_robots=False, **kwargs)
    fetcher.client.close()
    fetcher.client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module, 'validate_public_url', lambda url: url)
    monkeypatch.setattr(module.time, 'sleep', lambda _seconds: None)
    return fetcher


def test_dropped_connection_retries_then_returns_verified_bytes(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError('Server disconnected without sending response')
        return httpx.Response(200, json={'connected': True})
    with_client = client(monkeypatch, handler)
    try:
        assert with_client.get_json('https://example.org/feed') == {'connected': True}
        assert len(calls) == 2
    finally:
        with_client.close()


@pytest.mark.parametrize('status,headers,attempts', [(502, {}, 3), (503, {}, 3), (504, {}, 3),
                                                   (503, {'Retry-After': '60'}, 1), (429, {}, 1), (403, {}, 1)])
def test_retry_only_transient_failures_without_publisher_cooldown(monkeypatch, status, headers, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers=headers)
    fetcher = client(monkeypatch, handler)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            fetcher.fetch('https://example.org/feed')
        assert len(calls) == attempts
    finally:
        fetcher.close()


def test_failure_after_deadline_does_not_start_another_request(monkeypatch):
    clock, calls = [0.0], []
    def handler(request):
        calls.append(request)
        clock[0] += 5
        raise httpx.ReadTimeout('Timed out')
    fetcher = client(monkeypatch, handler, timeout=4)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: clock[0])
    try:
        with pytest.raises(httpx.ReadTimeout):
            fetcher.fetch('https://example.org/feed')
        assert len(calls) == 1
    finally:
        fetcher.close()


def test_rejected_content_is_not_retried(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, text='not JSON')
    fetcher = client(monkeypatch, handler)
    try:
        with pytest.raises(ValueError, match='Unsupported content type'):
            fetcher.get_json('https://example.org/feed')
        assert len(calls) == 1
    finally:
        fetcher.close()


def test_partial_response_is_discarded_before_reconnecting(monkeypatch):
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"incomplete":'
            raise httpx.ReadError('Publisher dropped mid-response')
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, headers={'content-type': 'application/json'}, stream=BrokenBody())
        return httpx.Response(200, json={'reconnected': True})
    fetcher = client(monkeypatch, handler)
    try:
        assert fetcher.get_json('https://example.org/feed') == {'reconnected': True}
        assert len(calls) == 2
    finally:
        fetcher.close()
