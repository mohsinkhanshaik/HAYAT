from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Callable
from pathlib import Path

import httpx

from app.logging import log_swallowed
from app.policy.cost import is_local_endpoint, require_local_endpoint

_log = logging.getLogger(__name__)


def optional_embedding(operation: Callable[[], list[float]]) -> list[float] | None:
    """Keep lexical memory usable when its optional local service disappears."""
    try:
        return operation()
    except (httpx.HTTPError, OSError, RuntimeError) as error:
        # Never include request URLs, credentials, or private input in the log.
        _log.warning("Optional embeddings unavailable (%s); retaining local lexical memory.", type(error).__name__)
        return None


def normalized_content(text: str) -> str:
    return " ".join(str(text).replace("\x00", " ").split()).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalized_content(text).encode("utf-8")).hexdigest()


@dataclass(slots=True)
class EmbeddingCapability:
    provider: str
    model: str
    dimension: int | None
    max_input: int | None
    locality: str
    latency_ms: float | None = None
    ram_bytes: int | None = None
    vram_bytes: int | None = None
    benchmark_quality: float | None = None
    available: bool = False
    version: str = "1"


class EmbeddingProvider(ABC):
    name = "base"
    locality = "local"

    def __init__(self, model: str = "", *, query_cache_seconds: float = 30) -> None:
        self.model = model
        self.available = False
        self.dimension: int | None = None
        self.max_input: int | None = None
        self.version = "1"
        self.ram_bytes: int | None = None
        self.vram_bytes: int | None = None
        self.benchmark_quality: float | None = None
        self._query_cache_seconds = query_cache_seconds
        self._query_cache: dict[str, tuple[float, list[float]]] = {}
        self._lock = threading.RLock()
        self.last_latency_ms: float | None = None

    @property
    def identifier(self) -> str:
        return f"{self.name}/{self.model}"

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed([normalized_content(text) for text in texts])

    def embed_query(self, text: str) -> list[float]:
        key = content_hash(text)
        now = time.monotonic()
        with self._lock:
            cached = self._query_cache.get(key)
            if cached and now - cached[0] <= self._query_cache_seconds:
                return list(cached[1])
        vector = self.embed([normalized_content(text)])[0]
        with self._lock:
            self._query_cache = {key: (now, vector)}
        return vector

    def capability(self) -> EmbeddingCapability:
        return EmbeddingCapability(self.name, self.model, self.dimension, self.max_input, self.locality, self.last_latency_ms, self.ram_bytes, self.vram_bytes, self.benchmark_quality, self.available, self.version)

    def status(self) -> dict[str, Any]:
        capability = asdict(self.capability())
        capability.update({"privacy": "local_only" if self.locality == "local" else "external_opt_in", "fallback": "hashed semantic + lexical + entity + recency"})
        return capability


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"
    locality = "local"

    def __init__(self, base_url: str = "http://localhost:11434", model: str | None = None, **kwargs: Any) -> None:
        super().__init__(model or "", **kwargs)
        self.base_url = base_url.rstrip("/")
        self._inspect()

    def embed(self, texts: list[str]) -> list[list[float]]:
        require_local_endpoint(self.base_url)
        if self.model.lower().endswith("cloud"):
            raise PermissionError("ZERO-COST: cloud embedding models are blocked.")
        if not self.available:
            raise RuntimeError("No local embedding-capable Ollama model is configured and installed.")
        started = time.perf_counter()
        response = self._client().post(self.base_url + "/api/embed", json={"model": self.model, "input": texts}, timeout=60)
        response.raise_for_status()
        values = response.json().get("embeddings")
        if not isinstance(values, list) or len(values) != len(texts):
            raise RuntimeError("Ollama returned an invalid embedding response.")
        vectors = [[float(value) for value in vector] for vector in values]
        if vectors:
            self.dimension = len(vectors[0])
        self.last_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        self._resources()
        return vectors

    def _client(self) -> httpx.Client:
        """One kept connection to Ollama. A fresh httpx.post per call built a new TLS context each time (about 140 ms,
        twice a chat turn; hard-tested 2026-10-17), for a plain-HTTP call to this computer."""
        client = getattr(self, "_http", None)
        if client is None or client.is_closed:
            client = self._http = httpx.Client(timeout=60, trust_env=False)
        return client

    def _inspect(self) -> None:
        if not self.model or not is_local_endpoint(self.base_url) or self.model.lower().endswith("cloud"):
            return
        try:
            response = httpx.post(self.base_url + "/api/show", json={"model": self.model}, timeout=5, trust_env=False)
            response.raise_for_status()
            data = response.json()
            capabilities = set(data.get("capabilities") or [])
            self.available = (bool(capabilities & {"embedding", "embeddings"})
                              and not data.get("remote_host") and not data.get("remote_model"))
            info = data.get("model_info") or {}
            self.max_input = next((int(value) for key, value in info.items() if key.endswith(".context_length")), None)
            self.dimension = next((int(value) for key, value in info.items() if key.endswith(".embedding_length")), None)
            digest = str(data.get("digest") or "")
            self.version = digest[:12] or str(data.get("modified_at") or "1")
        except Exception:
            self.available = False

    def _resources(self) -> None:
        try:
            rows = self._client().get(self.base_url + "/api/ps", timeout=5).json().get("models", [])
            row = next((item for item in rows if item.get("name") == self.model), None)
            if row:
                total = int(row.get("size") or 0); self.vram_bytes = int(row.get("size_vram") or 0); self.ram_bytes = max(0, total - self.vram_bytes)
        except Exception:
            log_swallowed(_log, "reading embedding model residency from Ollama")


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Local OpenAI-compatible embeddings; legacy allow_cloud cannot enable billing."""

    name = "openai_compatible_embeddings"

    def __init__(self, base_url: str, model: str, api_key: str = "", *, allow_cloud: bool = False, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.locality = "local" if is_local_endpoint(self.base_url) else "cloud"
        self.available = bool(model) and self.locality == "local"

    def embed(self, texts: list[str]) -> list[list[float]]:
        require_local_endpoint(self.base_url)
        if not self.available:
            raise RuntimeError("Embedding endpoint is disabled; ZERO-COST permits only local embeddings.")
        started = time.perf_counter()
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = httpx.post(self.base_url + "/embeddings", headers=headers, json={"model": self.model, "input": texts}, timeout=60, trust_env=False)
        response.raise_for_status()
        rows = sorted(response.json().get("data", []), key=lambda row: int(row.get("index", 0)))
        vectors = [[float(value) for value in row["embedding"]] for row in rows]
        if len(vectors) != len(texts):
            raise RuntimeError("OpenAI-compatible endpoint returned an invalid embedding response.")
        if vectors:
            self.dimension = len(vectors[0])
        self.last_latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return vectors


class EmbeddingRegistry:
    def __init__(self, providers: list[EmbeddingProvider], selected: str | None = None) -> None:
        self.providers = providers
        available = [provider for provider in providers if provider.available]
        self.active = next((provider for provider in available if provider.identifier == selected or provider.model == selected), None) or next((provider for provider in available if provider.locality == "local"), None)

    @classmethod
    def from_config(cls, config: dict[str, Any], provider_configs: dict[str, Any]) -> "EmbeddingRegistry":
        provider_name = str(config.get("provider", "auto"))
        model = str(config.get("model") or "")
        ollama = provider_configs.get("ollama", {})
        compatible = provider_configs.get("embedding_compatible", {})
        providers: list[EmbeddingProvider] = [OllamaEmbeddingProvider(str(ollama.get("base_url", "http://localhost:11434")), model if provider_name in {"auto", "ollama"} else "")]
        if compatible.get("base_url"):
            providers.append(OpenAICompatibleEmbeddingProvider(str(compatible["base_url"]), str(compatible.get("model") or model), str(compatible.get("api_key") or os.getenv("EMBEDDING_API_KEY", "")), allow_cloud=bool(config.get("allow_cloud", False))))
        registry = cls(providers, model)
        if registry.active:
            try:
                root = Path(__file__).resolve().parents[2]
                report = __import__("json").loads((root / "data/benchmarks/retrieval/after.json").read_text(encoding="utf-8"))
                selected = report.get("selected_default"); registry.active.benchmark_quality = float(report["strategies"][selected]["mrr"])
            except Exception:
                log_swallowed(_log, "reading the retrieval benchmark")
        return registry

    def status(self) -> dict[str, Any]:
        return {"active": self.active.status() if self.active else None, "providers": [provider.status() for provider in self.providers], "cloud_private_memory_enabled": False}
