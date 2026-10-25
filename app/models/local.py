from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.models.base import ModelError, ModelProvider, ModelResponse, normalize_messages
from app.policy.cost import require_local_endpoint


#: Hosts where "is anything listening?" is answered by the kernel immediately.
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")


def discovery_timeout(base_url: str) -> httpx.Timeout:
    """Probe budget for asking a provider what models it has.

    A local port either has something listening or it does not, and the OS
    knows which without waiting. Measured here: a refused probe to a stopped
    LM Studio cost 4.17 seconds against 135ms for a running Ollama, and that
    single number was most of `doctor`'s runtime. The connect phase is what
    needs bounding -- a provider that answers is still given room to send a
    large model list.
    """
    if any(host in base_url.lower() for host in _LOCAL_HOSTS):
        return httpx.Timeout(5.0, connect=0.4)
    return httpx.Timeout(10.0, connect=3.0)


class OllamaProvider(ModelProvider):
    name = "ollama"

    #: How long Ollama keeps the model resident in VRAM after a call. Ollama's
    #: default is 5 minutes, after which the next call cold-loads the whole
    #: model again -- which on this machine is ~90 seconds of dead wait before
    #: a single token. Keeping it loaded is the single biggest latency win
    #: there is: the first call of a session pays the load once, and every call
    #: after it starts generating immediately. "30m" survives normal gaps
    #: between turns without pinning VRAM forever when HAYAT is idle overnight.
    KEEP_ALIVE = "30m"

    def __init__(self, base_url: str, model: str = "", timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Mutable per-provider hints let the desktop resource control change
        # residency without editing model selection or restarting HAYAT.
        self.keep_alive = self.KEEP_ALIVE
        self.num_ctx = 16384
        # ContextBudget reads this public model ceiling. Keeping it aligned
        # with num_ctx prevents the prompt planner from budgeting 16k into an
        # explicitly selected 8k/Eco provider window.
        self.context_length = self.num_ctx
        # Whether the model reasons privately before answering. Off for
        # HAYAT's replies, which must start quickly; the DEEP-mode advisor
        # turns it on (escalation_model.think), where measured accuracy on
        # hard questions rose from 20 to 22 of 22 at about three times the
        # time. Only the answer text is returned; the reasoning is not.
        self.think = False

    def _error(self, message: str, exc: BaseException | None = None, *, endpoint: str, status: int | None = None) -> ModelError:
        """Build a ModelError that already knows what kind of failure this was."""
        from app.failures import FailureClass, classify_exception, classify_http_status

        if status is not None:
            failure_class = classify_http_status(status)
        elif exc is not None:
            failure_class = classify_exception(exc)
        else:
            failure_class = FailureClass.UNKNOWN
        return ModelError(
            message, provider=self.name, endpoint=endpoint, dependency="Ollama",
            failure_class=failure_class, http_status=status, model=self.model,
        )

    def list_models(self) -> list[str]:
        require_local_endpoint(self.base_url)
        endpoint = f"{self.base_url}/api/tags"
        try:
            response = httpx.get(endpoint, timeout=discovery_timeout(self.base_url), trust_env=False)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._error(
                f"Ollama answered {exc.response.status_code} when asked for its models.",
                exc, endpoint=endpoint, status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error(
                f"Could not reach Ollama at {self.base_url}: {exc}",
                exc.__cause__ or exc, endpoint=endpoint,
            ) from exc
        return [item["name"] for item in response.json().get("models", [])
                if not item.get("remote_host") and not item.get("remote_model")
                and not item["name"].lower().endswith("cloud")]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None) -> ModelResponse:
        require_local_endpoint(self.base_url)
        if self.model.lower().endswith("cloud"):
            raise PermissionError("ZERO-COST: Ollama cloud models are blocked.")
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            from app.failures import FailureClass

            raise ModelError(
                "Ollama is reachable but has no installed models.",
                provider=self.name, endpoint=self.base_url, dependency="Ollama",
                failure_class=FailureClass.NOT_FOUND,
            )
        payload: dict[str, Any] = {
            "model": model,
            "messages": normalize_messages(messages),
            "stream": False,
            "think": bool(getattr(self, "think", False)),
            # Keep the model in VRAM between calls so only the first turn of a
            # session pays the load cost, not every turn after a short pause.
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": 0.2 if temperature is None else temperature,
                # 16384, held constant across every call so Ollama never has to
                # reload the model to resize its cache (a resize costs a ~90s
                # reload on this hardware). This is the same ceiling the context
                # budget hands out under low free VRAM, so the window the budget
                # plans against and the window Ollama actually allocates are the
                # same number -- which is what stops a long follow-up from
                # filling the window and truncating the reply mid-word. The
                # overflow past what fits in VRAM spills to the machine's 48 GiB
                # of RAM: slower per token, but the answer finishes.
                "num_ctx": self.num_ctx,
                # A fixed 256 previously applied to every call, including
                # schema-constrained multi-step JSON plans: Ollama's grammar-
                # constrained decoding still hard-stops at num_predict mid
                # structure, returning syntactically incomplete JSON (observed
                # as JSONDecodeError from the planner) or a truncated
                # mid-sentence conversational reply. 512 is a safer default;
                # callers that know they need more (the planner) pass it.
                # 512 was roughly 380 words, which silently truncated any
                # answer that needed a real explanation -- and the truncation
                # landed mid-sentence, so it read as the model losing its
                # thread rather than running out of room. A ceiling still
                # exists because an unbounded generation can run for minutes
                # on this hardware, but it now sits above the length of a
                # complete answer rather than in the middle of one.
                "num_predict": max_tokens if max_tokens is not None else 4096,
            },
        }
        if tools:
            payload["tools"] = tools
        if response_schema:
            payload["format"] = response_schema
        endpoint = f"{self.base_url}/api/chat"
        from app.activity import ACTIVITY

        try:
            with ACTIVITY.track("model", "thinking", f"{self.name}/{model}"):
                response = httpx.post(endpoint, json=payload, timeout=self.timeout, trust_env=False)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._error(
                f"Ollama answered {exc.response.status_code} for a generation request.",
                exc, endpoint=endpoint, status=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            # httpx wraps the OS error; the cause carries the actual evidence
            # (ConnectionRefusedError / WinError 10061) the classifier needs.
            raise self._error(
                f"Ollama request failed: {exc}", exc.__cause__ or exc, endpoint=endpoint,
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            from app.failures import FailureClass

            raise ModelError(
                "Ollama returned a response that was not valid JSON.",
                provider=self.name, endpoint=endpoint, dependency="Ollama",
                failure_class=FailureClass.DATA_INTEGRITY,
            ) from exc
        message = data.get("message", {})
        return ModelResponse(
            content=message.get("content", ""),
            model=data.get("model", model),
            provider=self.name,
            tool_calls=message.get("tool_calls", []),
            usage={"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")},
            raw=data,
            # Ollama reports "stop" when the model ended the reply and
            # "length" when num_predict ran out. Reading it is the difference
            # between knowing an answer finished and assuming it did.
            finish_reason=str(data.get("done_reason") or ("stop" if data.get("done") else "")),
        )

    def generate_stream(self, messages, *, on_chunk, cancel=None, temperature=None, max_tokens=None):
        require_local_endpoint(self.base_url)
        if self.model.lower().endswith("cloud"):
            raise PermissionError("ZERO-COST: Ollama cloud models are blocked.")
        from app.models.streaming import stream_local
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            raise ModelError("No local model is available for streaming")
        payload = {"model": model, "messages": normalize_messages(messages), "think": bool(getattr(self, "think", False)),
                   "keep_alive": self.keep_alive, "options": {
                       "temperature": .2 if temperature is None else temperature,
                       "num_ctx": self.num_ctx, "num_predict": max_tokens if max_tokens is not None else 4096}}
        return stream_local(self, payload, on_chunk=on_chunk, cancel=cancel)

    def generate_vision(self, prompt: str, image_path: str | Path, temperature: float | None = None) -> ModelResponse:
        path = Path(image_path)
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return self.generate([{"role": "user", "content": prompt, "images": [encoded]}], temperature=temperature)


class OpenAICompatibleProvider(ModelProvider):
    def __init__(self, name: str, base_url: str, model: str = "", api_key: str = "", timeout: float = 120.0) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def list_models(self) -> list[str]:
        require_local_endpoint(self.base_url)
        response = httpx.get(
            f"{self.base_url}/models", headers=self.headers, timeout=discovery_timeout(self.base_url), trust_env=False,
        )
        response.raise_for_status()
        return [item["id"] for item in response.json().get("data", [])]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None) -> ModelResponse:
        require_local_endpoint(self.base_url)
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            raise ModelError(f"{self.name} has no configured or discoverable model.")
        payload: dict[str, Any] = {
            "model": model,
            "messages": normalize_messages(messages),
            "temperature": 0.2 if temperature is None else temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max(1, int(max_tokens))
        if tools:
            payload["tools"] = tools
        if response_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agent_response", "strict": True, "schema": response_schema},
            }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions", headers=self.headers, json=payload, timeout=self.timeout, trust_env=False
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelError(f"{self.name} request failed: {exc}") from exc
        data = response.json()
        message = data["choices"][0]["message"]
        return ModelResponse(
            content=message.get("content") or "",
            model=data.get("model", model),
            provider=self.name,
            tool_calls=message.get("tool_calls", []),
            usage=data.get("usage", {}),
            raw=data,
            finish_reason=str(data["choices"][0].get("finish_reason") or ""),
        )

    def generate_vision(self, prompt: str, image_path: str | Path, temperature: float | None = None) -> ModelResponse:
        path = Path(image_path)
        mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ],
        }
        return self.generate([message], temperature=temperature)

    def generate_stream(self, messages, *, on_chunk, cancel=None, temperature=None, max_tokens=None):
        from app.models.streaming import stream_local
        model = self.model or next(iter(self.list_models()), "")
        if not model:
            raise ModelError("No compatible model is available for streaming")
        payload = {"model": model, "messages": normalize_messages(messages),
                   "temperature": .2 if temperature is None else temperature}
        if max_tokens is not None:
            payload["max_tokens"] = max(1, int(max_tokens))
        return stream_local(self, payload, compatible=True, on_chunk=on_chunk, cancel=cancel)
