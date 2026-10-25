from __future__ import annotations

import base64

from app.models.base import Message
from app.models.cloud import AnthropicProvider, GeminiProvider


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


def test_anthropic_adapter_normalizes_system_tools_and_response(monkeypatch):
    captured = {}

    def post(url, headers, json, timeout, trust_env):
        assert trust_env is False
        captured.update({"url": url, "headers": headers, "json": json})
        return Response({"model": "configured", "content": [{"type": "text", "text": "done"}, {"type": "tool_use", "id": "u1", "name": "read_file", "input": {"path": "README.md"}}], "usage": {"input_tokens": 4}})

    monkeypatch.setattr("app.models.cloud.httpx.post", post)
    provider = AnthropicProvider("http://127.0.0.1:8080", "configured", "secret")
    tools = [{"type": "function", "function": {"name": "read_file", "description": "Read", "parameters": {"type": "object"}}}]
    result = provider.generate([Message("system", "safe"), Message("user", "read")], tools=tools)
    assert captured["json"]["system"] == "safe"
    assert captured["json"]["tools"][0]["input_schema"] == {"type": "object"}
    assert result.content == "done"
    assert result.tool_calls[0]["function"]["name"] == "read_file"


def test_gemini_adapter_normalizes_function_calls(monkeypatch):
    captured = {}

    def post(url, headers, json, timeout, trust_env):
        assert trust_env is False
        captured.update({"url": url, "json": json})
        return Response({"candidates": [{"content": {"parts": [{"text": "ok"}, {"functionCall": {"name": "read_file", "args": {"path": "README.md"}}}]}}], "usageMetadata": {"promptTokenCount": 3}})

    monkeypatch.setattr("app.models.cloud.httpx.post", post)
    provider = GeminiProvider("http://127.0.0.1:8080/v1beta", "gemini-configured", "secret")
    result = provider.generate([Message("system", "safe"), Message("user", "read")])
    assert captured["json"]["systemInstruction"]["parts"][0]["text"] == "safe"
    assert result.content == "ok"
    assert result.tool_calls[0]["function"]["arguments"]["path"] == "README.md"


def test_gemini_vision_sends_inline_base64(monkeypatch, tmp_path):
    captured = {}
    image = tmp_path / "image.png"
    image.write_bytes(b"pngdata")

    def post(url, headers, json, timeout, trust_env):
        assert trust_env is False
        captured["json"] = json
        return Response({"candidates": [{"content": {"parts": [{"text": "visible UI"}]}}]})

    monkeypatch.setattr("app.models.cloud.httpx.post", post)
    result = GeminiProvider("http://127.0.0.1:8080/v1beta", "vision", "secret").generate_vision("describe", image)
    inline = captured["json"]["contents"][0]["parts"][1]["inlineData"]
    assert base64.b64decode(inline["data"]) == b"pngdata"
    assert result.content == "visible UI"
