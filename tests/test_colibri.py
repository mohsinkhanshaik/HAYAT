from types import SimpleNamespace

import httpx
import pytest

from app.models.base import ModelError
from app.models.colibri import ColibriProvider
from app.models.router import ModelRouter
from app.policy.cost import CostPolicy


@pytest.mark.parametrize("url", ["https://remote.example/v1", "http://127.0.0.1:8000/",
    "http://user:pass@localhost:8000/v1", "http://localhost:8000/v1?key=x", "file:///v1"])
def test_colibri_connection_requires_loopback_and_an_api_root(url):
    with pytest.raises(ValueError):
        ColibriProvider(url)
    assert not CostPolicy(paid_api_allowed=True, cloud_inference_allowed=True).provider_allowed(
        "colibri", "https://remote.example/v1")


def test_disabled_colibri_is_not_constructed_or_probed(monkeypatch):
    router = ModelRouter({"providers": {"colibri": {"enabled": False, "base_url": "invalid"}}})
    assert "colibri" not in router.providers


def test_colibri_routes_only_an_advertised_exact_model(monkeypatch):
    router = ModelRouter({"default_model": {"provider": "colibri", "model": "qwen-local"},
                          "providers": {"colibri": {"enabled": True}}})
    monkeypatch.setattr(router, "available", lambda: {"colibri": ["other"]})
    monkeypatch.setattr(router, "_benchmark_selection", lambda _: None)
    assert router.get() is None
    monkeypatch.setattr(router, "available", lambda: {"colibri": ["qwen-local"]})
    provider = router.get()
    assert provider.name == "colibri" and provider.model == "qwen-local"
    assert provider.context_length == 4096


def test_colibri_uses_gateway_protocol_and_preserves_tool_and_length_results(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "model": "qwen-local", "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "search", "arguments": '{"q":"deal"}'}}
            ]}, "finish_reason": "length"}], "usage": {"completion_tokens": 12}})
    monkeypatch.setattr("app.models.local.httpx.post", post)
    provider = ColibriProvider(model="qwen-local", context_length=2048)
    result = provider.generate([{"role": "user", "content": "research"}],
                              tools=[{"type": "function", "function": {"name": "search"}}], max_tokens=12)
    assert calls[0][0] == "http://127.0.0.1:8000/v1/chat/completions"
    assert calls[0][1]["tools"] and calls[0][1]["max_tokens"] == 12
    assert result.provider == "colibri" and result.truncated and result.tool_calls[0]["id"] == "call_1"
    with pytest.raises(ModelError, match="text-only"):
        provider.generate_vision("look", "unused.png")


def test_colibri_model_page_paginates_with_existing_models_and_labels_provenance():
    from app.gui_explorer import workspace_page
    runtime = SimpleNamespace(storage=SimpleNamespace(model_inventory=lambda: {"models": []}),
        router=SimpleNamespace(providers={"colibri": SimpleNamespace(
            list_models=lambda: [f"local-{i}" for i in range(31)],
            context_length=2048, base_url="http://127.0.0.1:8000/v1")}))
    first, last = workspace_page(runtime, "models"), workspace_page(runtime, "models", page=2)
    assert len(first["rows"]) == 30 and first["has_more"]
    assert len(last["rows"]) == 1 and not last["has_more"]
    assert first["rows"][0]["state"] == "Advertised"
    assert "not detected" in first["rows"][0]["detail"]
    assert "not been established" in first["summary"]
