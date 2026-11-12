from __future__ import annotations

import json
import threading

import httpx
import pytest

from app.connected_ai import ConnectedAI, ConnectedAIError
from app.connected_ai_tools import ResearchTools, calculate
from app.integrations.secrets import EncryptedFileBackend, SecretBroker


@pytest.fixture
def factory(tmp_path):
    def make(handler, *, enabled=True, private=None):
        broker = SecretBroker(tmp_path / "vault" / "metadata.db", backend=EncryptedFileBackend(tmp_path / "vault"))
        manager = ConnectedAI(tmp_path / "settings", broker=broker, transport=httpx.MockTransport(handler), private=private)
        manager.save({"enabled": enabled, "provider": "openai", "models": {}, "max_tokens": 8192, "tools": True})
        for provider in ("openai", "anthropic", "gemini"):
            manager.store_key(provider, "test-secret-never-export")
        return manager
    return make


def final(provider, text="The measured answer is 4."):
    if provider == "openai":
        return {"status": "completed", "model": "selected-version", "usage": {"input_tokens": 20, "output_tokens": 5},
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}
    if provider == "anthropic":
        return {"model": "selected-version", "stop_reason": "end_turn", "usage": {"input_tokens": 20, "output_tokens": 5},
                "content": [{"type": "text", "text": text}]}
    return {"modelVersion": "selected-version", "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5},
            "candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [{"text": text}]}}]}


def tool_response(provider):
    if provider == "openai":
        return {"status": "completed", "usage": {"input_tokens": 10, "output_tokens": 4}, "output": [
            {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "opaque-reasoning"},
            {"type": "function_call", "call_id": "c1", "name": "calculate", "arguments": '{"expression":"2+2"}'}]}
    if provider == "anthropic":
        return {"stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 4}, "content": [
            {"type": "thinking", "thinking": "private-reasoning", "signature": "opaque-signature"},
            {"type": "tool_use", "id": "c1", "name": "calculate", "input": {"expression": "2+2"}}]}
    return {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}, "candidates": [{"finishReason": "STOP", "content": {
        "role": "model", "parts": [{"thoughtSignature": "opaque-signature", "functionCall": {
            "id": "c1", "name": "calculate", "args": {"expression": "2+2"}}}]}}]}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_native_tool_loop_preserves_metadata_and_exact_model(factory, provider):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.scheme == "https"
        assert "test-secret" not in request.content.decode()
        assert "test-secret" not in str(request.url)
        if provider != "gemini":
            assert payload["model"] == "selected-model"
        else:
            assert request.url.path.endswith("/selected-model:generateContent")
        return httpx.Response(200, json=tool_response(provider) if len(requests) == 1 else final(provider))

    manager = factory(handler)
    answer = manager.ask(provider, "selected-model", [{"role": "user", "content": "Calculate 2+2"}])
    assert answer.requested_model == "selected-model" and answer.returned_model == "selected-version"
    assert answer.requests == 2 and answer.tools[0]["result"] == {"value": 4}
    assert answer.usage["promptTokenCount" if provider == "gemini" else "input_tokens"] == 30
    assert not answer.incomplete
    second = requests[1]
    if provider == "openai":
        assert second["store"] is False
        assert second["input"][2]["encrypted_content"] == "opaque-reasoning"
        assert second["input"][-1]["call_id"] == "c1"
        assert second["include"] == ["reasoning.encrypted_content"]
    elif provider == "anthropic":
        assert second["messages"][-2]["content"][0]["signature"] == "opaque-signature"
        assert second["messages"][-1]["content"][0]["tool_use_id"] == "c1"
    else:
        declaration = second["tools"][0]["functionDeclarations"][0]
        assert declaration["parametersJsonSchema"]["additionalProperties"] is False
        assert "parameters" not in declaration
        assert second["contents"][-2]["parts"][0]["thoughtSignature"] == "opaque-signature"
        assert second["contents"][-1]["parts"][0]["functionResponse"]["id"] == "c1"


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 429, 500, 503])
def test_http_errors_do_not_retry_downgrade_or_leak_bodies(factory, status):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"error": "test-secret-never-export"}, headers={"Location": "https://evil.invalid"})

    manager = factory(handler)
    with pytest.raises(ConnectedAIError) as error:
        manager.ask("openai", "chosen-large-model", [{"role": "user", "content": "Hello"}])
    assert len(seen) == 1 and error.value.requests == 1
    assert str(status) in str(error.value) and "test-secret" not in str(error.value)
    assert "No fallback" in str(error.value)


@pytest.mark.parametrize("block", ["disabled", "private", "cancel", "missing_key"])
def test_authorization_blocks_network(factory, block):
    requests = []
    manager = factory(lambda r: requests.append(r), enabled=block != "disabled", private=lambda: block == "private")
    cancel = threading.Event()
    if block == "cancel":
        cancel.set()
    if block == "missing_key":
        manager.forget_key("openai")
    with pytest.raises(ConnectedAIError):
        manager.ask("openai", "chosen", [{"role": "user", "content": "test"}], cancel=cancel)
    assert not requests


def test_revocation_between_tool_rounds_prevents_more_requests(factory):
    seen = []

    def handler(request):
        seen.append(request)
        manager.save({**manager.settings(), "enabled": False})
        return httpx.Response(200, json=tool_response("openai"))

    manager = factory(handler)
    with pytest.raises(ConnectedAIError, match="Enable external") as error:
        manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert len(seen) == 1 and error.value.usage["input_tokens"] == 10


def test_key_is_encrypted_and_absent_from_settings(factory, tmp_path):
    manager = factory(lambda _: None)
    assert manager.has_key("openai")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"test-secret-never-export" not in path.read_bytes()
    manager.forget_key("openai")
    assert not manager.has_key("openai")
    assert not manager.settings()["models"]


@pytest.mark.parametrize("field,value", [("enabled", "true"), ("enabled", 1), ("tools", "yes"),
                                         ("max_tokens", 0), ("max_tokens", True), ("provider", "evil"),
                                         ("models", {"openai": "../evil?key=oops"})])
def test_invalid_settings_fail_closed(factory, field, value):
    manager = factory(lambda _: None)
    invalid = {**manager.settings(), field: value}
    with pytest.raises(ValueError):
        manager.save(invalid)
    manager.path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ConnectedAIError, match="blocked"):
        manager.settings()


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_incomplete_answer_is_explicit(factory, provider):
    data = final(provider, "Partial evidence")
    if provider == "openai":
        data["status"] = "incomplete"
    elif provider == "anthropic":
        data["stop_reason"] = "max_tokens"
    else:
        data["candidates"][0]["finishReason"] = "MAX_TOKENS"
    manager = factory(lambda _: httpx.Response(200, json=data))
    answer = manager.ask(provider, "chosen", [{"role": "user", "content": "test"}])
    assert answer.incomplete and answer.text == "Partial evidence"


def test_tool_round_limit_raises_instead_of_fabricating_answer(factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=tool_response("anthropic"))

    manager = factory(handler)
    with pytest.raises(ConnectedAIError, match="Tool budget") as error:
        manager.ask("anthropic", "chosen", [{"role": "user", "content": "test"}])
    assert len(requests) == 6 and error.value.usage["input_tokens"] == 60


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_paginated_model_discovery_never_changes_selection(factory, provider):
    requests = []

    def handler(request):
        requests.append(request)
        if provider == "anthropic":
            data = {"data": [{"id": f"model-{len(requests)}"}], "has_more": len(requests) == 1, "last_id": "model-1"}
        else:
            data = {"models": [{"name": f"models/model-{len(requests)}", "supportedGenerationMethods": ["generateContent"]},
                               {"name": "models/embedding-only", "supportedGenerationMethods": ["embedContent"]}]}
            if len(requests) == 1:
                data["nextPageToken"] = "opaque/cursor+safe"
        return httpx.Response(200, json=data)

    manager = factory(handler)
    assert manager.discover(provider) == ["model-1", "model-2"]
    assert len(requests) == 2 and manager.settings()["models"] == {}
    assert requests[1].url.params["after_id" if provider == "anthropic" else "pageToken"] == (
        "model-1" if provider == "anthropic" else "opaque/cursor+safe")


def test_context_limit_and_empty_model_never_silently_trim_or_select(factory):
    requests = []
    manager = factory(lambda r: requests.append(r))
    with pytest.raises(ConnectedAIError, match="exact model"):
        manager.ask("openai", "", [{"role": "user", "content": "test"}])
    with pytest.raises(ConnectedAIError, match="silently removed"):
        manager.ask("openai", "chosen", [{"role": "user", "content": "x" * 200001}])
    assert not requests


def test_disabled_tools_are_neither_advertised_nor_run(factory):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=tool_response("openai"))

    manager = factory(handler)
    manager.save({**manager.settings(), "tools": False})
    with pytest.raises(ConnectedAIError, match="disabled"):
        manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert "tools" not in requests[0] and len(requests) == 1


def test_unknown_tool_becomes_error_result_never_executes_code(factory):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        data = tool_response("openai") if len(seen) == 1 else final("openai")
        if len(seen) == 1:
            data["output"][1]["name"] = "shell"
        return httpx.Response(200, json=data)

    manager = factory(handler)
    result = manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert "error" in result.tools[0]["result"]
    assert "error" in json.loads(seen[1]["input"][-1]["output"])


@pytest.mark.parametrize("expression", ["__import__('os')", "(1).__class__", "2**1000000", "1/0", "sqrt(-1)",
                                         "[x for x in range(10)]", "True", "1e1000", "abs(x=1)"])
def test_calculator_rejects_code_and_unbounded_arithmetic(expression):
    assert "error" in ResearchTools().execute("calculate", {"expression": expression})


def test_calculator_and_sample_statistics():
    assert calculate("sqrt(16) + sin(pi/2) + log(e)") == pytest.approx(6)
    tools = ResearchTools()
    result = tools.execute("describe_numbers", {"values": [1, 2, 3, 4]})
    assert result["mean"] == result["median"] == 2.5
    assert result["sample_stdev"] == pytest.approx(1.2909944487)
    assert tools.execute("describe_numbers", {"values": [5]})["sample_stdev"] is None
    for values in ([], [True], [float("inf")], [1e101]):
        assert "error" in tools.execute("describe_numbers", {"values": values})


def test_document_retrieval_returns_verifiable_bounded_sources(tmp_path):
    path = tmp_path / "lab.md"
    path.write_text("# Experiment\nDose: 2 mg\nMeasured signal: 4\nLimitations: only one sample.\n", encoding="utf-8")
    tools = ResearchTools()
    doc = tools.attach(path)
    assert doc.id == "D1" and len(doc.sha256) == 64
    result = tools.execute("search_documents", {"query": "signal"})
    assert result["matches"][0]["source"] == "D1" and "L3: Measured signal" in result["matches"][0]["text"]
    assert str(tmp_path) not in json.dumps(result)
    assert tools.execute("read_document", {"document_id": "D1", "start_line": 4})["text"].startswith("L4:")
    assert "error" in tools.execute("read_document", {"document_id": str(path), "start_line": 1})
    assert "error" in tools.execute("read_document", {"document_id": "D1", "start_line": 0})
    tools.documents.clear()
    assert tools.attach(path).id == "D2"  # never reuse citation IDs from an earlier answer
    with pytest.raises(ValueError):
        tools.attach(tmp_path / ".env")
    path.write_bytes(b"x" * 512001)
    with pytest.raises(ValueError, match="512 KB"):
        tools.attach(path)


def test_emergency_stop_blocks_requests_and_tools(factory):
    requests = []
    manager = factory(lambda r: requests.append(r))
    manager.halted = lambda: True
    with pytest.raises(ConnectedAIError, match="Emergency stop"):
        manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert not requests


@pytest.mark.parametrize("data", [{"data": None}, {"data": [None]}, {"data": [], "has_more": True},
                                  {"data": [{"id": ["not a string"]}]}])
def test_invalid_discovery_is_a_visible_error(factory, data):
    manager = factory(lambda _: httpx.Response(200, json=data))
    with pytest.raises(ConnectedAIError):
        manager.discover("anthropic")


def test_timeout_is_not_retried_and_does_not_expose_request_secrets(factory):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("contains test-secret-never-export", request=request)

    manager = factory(handler)
    with pytest.raises(ConnectedAIError, match="timed out") as error:
        manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert len(requests) == 1 and "test-secret" not in str(error.value)


def test_openai_incomplete_reason_is_preserved(factory):
    data = {**final("openai"), "status": "incomplete", "incomplete_details": {"reason": "content_filter"}}
    manager = factory(lambda _: httpx.Response(200, json=data))
    answer = manager.ask("openai", "chosen", [{"role": "user", "content": "test"}])
    assert answer.incomplete and answer.finish_reason == "content_filter"
