from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.models.base import ModelError, ModelProvider, ModelResponse, normalize_messages
from app.models.local import OpenAICompatibleProvider
from app.policy.cost import require_local_endpoint


class OpenAIProvider(OpenAICompatibleProvider):
    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 120.0) -> None:
        super().__init__("openai", base_url, model, api_key, timeout)


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def list_models(self) -> list[str]:
        require_local_endpoint(self.base_url)
        response = httpx.get(f"{self.base_url}/v1/models", headers=self.headers, timeout=5.0, trust_env=False)
        response.raise_for_status()
        return [item["id"] for item in response.json().get("data", [])]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None) -> ModelResponse:
        require_local_endpoint(self.base_url)
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            raise ModelError("Anthropic has no configured or discoverable model.")
        normalized = normalize_messages(messages)
        system_parts = [item["content"] for item in normalized if item.get("role") == "system"]
        conversation = [item for item in normalized if item.get("role") in {"user", "assistant"}]
        payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens or 1024, "messages": conversation}
        if system_parts:
            payload["system"] = "\n\n".join(str(item) for item in system_parts)
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"]["description"],
                    "input_schema": item["function"]["parameters"],
                    "strict": True,
                }
                for item in tools
            ]
        if response_schema:
            payload["output_config"] = {"format": {"type": "json_schema", "schema": response_schema}}
        try:
            response = httpx.post(f"{self.base_url}/v1/messages", headers=self.headers, json=payload, timeout=self.timeout, trust_env=False)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelError(f"Anthropic request failed: {exc}") from exc
        data = response.json()
        blocks = data.get("content", [])
        content = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        tool_calls = [
            {"id": block.get("id"), "function": {"name": block.get("name"), "arguments": block.get("input", {})}}
            for block in blocks if block.get("type") == "tool_use"
        ]
        return ModelResponse(content, data.get("model", model), self.name, tool_calls, data.get("usage", {}), data)

    def generate_vision(self, prompt: str, image_path: str | Path, temperature: float | None = None) -> ModelResponse:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": encoded}},
            {"type": "text", "text": prompt},
        ]
        return self.generate([{"role": "user", "content": content}], temperature=temperature)


class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key, "content-type": "application/json"}

    def list_models(self) -> list[str]:
        require_local_endpoint(self.base_url)
        response = httpx.get(f"{self.base_url}/models", headers=self.headers, timeout=5.0, trust_env=False)
        response.raise_for_status()
        return [item["name"].removeprefix("models/") for item in response.json().get("models", []) if "generateContent" in item.get("supportedGenerationMethods", [])]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None) -> ModelResponse:
        require_local_endpoint(self.base_url)
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            raise ModelError("Gemini has no configured or discoverable model.")
        normalized = normalize_messages(messages)
        system_parts = [str(item["content"]) for item in normalized if item.get("role") == "system"]
        contents = [
            {"role": "model" if item.get("role") == "assistant" else "user", "parts": [{"text": str(item.get("content", ""))}]}
            for item in normalized if item.get("role") in {"user", "assistant"}
        ]
        payload: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens or 1024}}
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if temperature is not None:
            payload["generationConfig"]["temperature"] = temperature
        if response_schema:
            payload["generationConfig"].update({"responseMimeType": "application/json", "responseJsonSchema": response_schema})
        if tools:
            payload["tools"] = [{"functionDeclarations": [item["function"] for item in tools]}]
        try:
            response = httpx.post(f"{self.base_url}/models/{model}:generateContent", headers=self.headers, json=payload, timeout=self.timeout, trust_env=False)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelError(f"Gemini request failed: {exc}") from exc
        data = response.json()
        candidate = (data.get("candidates") or [{}])[0]
        parts = candidate.get("content", {}).get("parts", [])
        content = "\n".join(part.get("text", "") for part in parts if "text" in part)
        tool_calls = [
            {"function": {"name": part["functionCall"].get("name"), "arguments": part["functionCall"].get("args", {})}}
            for part in parts if "functionCall" in part
        ]
        return ModelResponse(content, model, self.name, tool_calls, data.get("usageMetadata", {}), data)

    def generate_vision(self, prompt: str, image_path: str | Path, temperature: float | None = None) -> ModelResponse:
        require_local_endpoint(self.base_url)
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        model = self.model or next(iter(self.list_models()), "")
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}, {"inlineData": {"mimeType": mime_type, "data": encoded}}]}],
            "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.1 if temperature is None else temperature},
        }
        try:
            response = httpx.post(f"{self.base_url}/models/{model}:generateContent", headers=self.headers, json=payload, timeout=self.timeout, trust_env=False)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelError(f"Gemini vision request failed: {exc}") from exc
        data = response.json()
        parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        content = "\n".join(part.get("text", "") for part in parts if "text" in part)
        return ModelResponse(content, model, self.name, usage=data.get("usageMetadata", {}), raw=data)
