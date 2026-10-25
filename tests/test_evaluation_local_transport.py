"""All transport tests use in-process HTTPX fixtures, never a listening service."""
import json

import httpx
import pytest

from app.evaluation import local_transport


@pytest.fixture
def client_factory(monkeypatch):
    original = httpx.Client
    settings = []

    def install(handler):
        def factory(**kwargs):
            settings.append(kwargs)
            return original(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr(local_transport.httpx, "Client", factory)
        return settings
    return install


def test_local_get_bypasses_proxy_settings_and_disables_redirects(client_factory, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://must-not-contact.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-contact.invalid:9999")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"models": []})
    settings = client_factory(handler)
    assert local_transport.request("http://127.0.0.1:11435/", "/api/tags", timeout=3) == {"models": []}
    assert seen[0].method == "GET" and str(seen[0].url) == "http://127.0.0.1:11435/api/tags"
    assert seen[0].headers["accept-encoding"] == "identity"
    assert settings == [{"timeout": 3, "trust_env": False, "follow_redirects": False}]


def test_local_post_preserves_payload_and_timeout(client_factory):
    seen = []
    payload = {"model": "hayat:latest", "messages": [{"role": "user", "content": "A local fixture"}]}

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"message": {"content": "fixture"}})
    settings = client_factory(handler)
    assert local_transport.request("http://localhost:11435", "/api/chat", payload, timeout=240)["message"]["content"] == "fixture"
    assert seen[0].method == "POST" and json.loads(seen[0].content) == payload
    assert settings[0]["timeout"] == 240


@pytest.mark.parametrize("endpoint", ["https://example.org", "http://127.0.0.1.evil:11435",
    "http://user:secret@127.0.0.1:11435", "ftp://127.0.0.1:11435", "http://127.0.0.1:11435/prefix",
    "http://127.0.0.1:11435?query=yes", "http://127.0.0.1:11435#fragment", "http://127.0.0.1:99999"])
def test_nonlocal_credentialed_or_nonroot_endpoints_never_create_client(monkeypatch, endpoint):
    monkeypatch.setattr(local_transport.httpx, "Client", lambda **kw: pytest.fail("No connection allowed"))
    with pytest.raises(PermissionError):
        local_transport.request(endpoint, "/api/tags")


@pytest.mark.parametrize("route", ["https://example.org/api/tags", "//example.org/api/tags", "api/tags",
    "/api/tags?redirect=elsewhere", "/api/tags#fragment", "/api/\\evil", "/api/ tags", "/api/tags\n", None])
def test_routes_cannot_replace_or_reinterpret_the_origin(monkeypatch, route):
    monkeypatch.setattr(local_transport.httpx, "Client", lambda **kw: pytest.fail("No connection allowed"))
    with pytest.raises(ValueError):
        local_transport.request("http://127.0.0.1:11435", route)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "240"])
def test_invalid_timeout_is_rejected_before_request(monkeypatch, timeout):
    monkeypatch.setattr(local_transport.httpx, "Client", lambda **kw: pytest.fail("No connection allowed"))
    with pytest.raises(ValueError, match="timeout"):
        local_transport.request("http://127.0.0.1:11435", "/api/tags", timeout=timeout)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_never_reach_an_external_service(client_factory, status):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://external.invalid/chargeable"})
    client_factory(handler)
    with pytest.raises(httpx.HTTPStatusError):
        local_transport.request("http://127.0.0.1:11435", "/api/tags")
    assert seen == ["http://127.0.0.1:11435/api/tags"]


@pytest.mark.parametrize("declared", ["33554433", "-1", "not-a-number"])
def test_oversize_or_invalid_declared_length_is_rejected_without_consuming_body(client_factory, declared):
    class Unreadable(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Declared oversize response must not be read")
    client_factory(lambda request: httpx.Response(200, headers={"Content-Length": declared}, stream=Unreadable()))
    with pytest.raises(ValueError, match="Content-Length|32 MiB"):
        local_transport.request("http://127.0.0.1:11435", "/api/show", {"model": "fixture"})


@pytest.mark.parametrize("declared", [None, "2"])
def test_stream_limit_does_not_trust_missing_or_false_content_length(client_factory, monkeypatch, declared):
    monkeypatch.setattr(local_transport, "MAX_RESPONSE_BYTES", 16)
    headers = {} if declared is None else {"Content-Length": declared}
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"value":"'
            yield b'x' * 20
            yield b'"}'
    client_factory(lambda request: httpx.Response(200, headers=headers, stream=Chunks()))
    with pytest.raises(ValueError, match="32 MiB"):
        local_transport.request("http://127.0.0.1:11435", "/api/show", {"model": "fixture"})


@pytest.mark.parametrize("content", [b"not json", b"[]", b"null", b'"text"'])
def test_malformed_or_nonobject_responses_are_not_service_results(client_factory, content):
    client_factory(lambda request: httpx.Response(200, content=content))
    with pytest.raises(ValueError):
        local_transport.request("http://127.0.0.1:11435", "/api/tags")


@pytest.mark.parametrize("tag", [None, {}, {"name": "qwen:cloud"}, {"name": "QWEN:CLOUD "},
    {"name": "innocent:latest", "remote_host": "https://provider.invalid"},
    {"name": "innocent:latest", "remote_model": "chargeable"},
    {"name": "innocent:latest", "details": {"remote_host": "https://provider.invalid"}},
    {"name": "innocent:latest", "details": {"remote_model": "chargeable"}},
    {"name": "innocent:latest", "details": ["invalid metadata"]}])
def test_cloud_aliases_and_remote_metadata_are_rejected(tag):
    with pytest.raises(PermissionError, match="ZERO-COST"):
        local_transport.validate_local_model(tag)


def test_local_metadata_returns_same_record_without_mutation():
    tag = {"name": "hayat:latest", "digest": "fixture", "details": {"format": "gguf", "family": "qwen3"}}
    assert local_transport.validate_local_model(tag) is tag


def test_legacy_request_wrapper_preserves_arguments_and_return(monkeypatch):
    from scripts.audit_local_models import request
    seen = []
    monkeypatch.setattr(local_transport, "request", lambda *a, **kw: seen.append((a, kw)) or {"fixture": True})
    assert request("http://127.0.0.1:11435", "/api/show", {"model": "fixture"}, 90) == {"fixture": True}
    assert seen == [(("http://127.0.0.1:11435", "/api/show"), {"payload": {"model": "fixture"}, "timeout": 90})]
