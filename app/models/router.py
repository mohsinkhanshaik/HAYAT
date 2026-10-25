from __future__ import annotations

import os
import json
from copy import copy
from pathlib import Path
from typing import Any

from app.config import load_settings, load_yaml
from app.models.base import ModelProvider
from app.models.cloud import AnthropicProvider, GeminiProvider, OpenAIProvider
from app.models.local import OllamaProvider, OpenAICompatibleProvider
from app.models.colibri import ColibriProvider
from app.policy.cost import CostPolicy


class ModelRouter:
    def __init__(self, config: dict[str, Any] | None = None, cost_policy: CostPolicy | None = None) -> None:
        self.config = config or load_yaml("config/models.yaml")
        self.cost_policy = cost_policy or CostPolicy.from_settings(load_settings().get("cost_policy", {}))
        self.cost_policy.assert_zero_cost()
        provider_configs = self.config.get("providers", {})
        ollama = provider_configs.get("ollama", {})
        lm_studio = provider_configs.get("lm_studio", {})
        openai = provider_configs.get("openai", {})
        anthropic = provider_configs.get("anthropic", {})
        gemini = provider_configs.get("gemini", {})
        self.providers: dict[str, ModelProvider] = {
            "ollama": OllamaProvider(ollama.get("base_url", "http://localhost:11434"), ollama.get("model", "")),
            "lm_studio": OpenAICompatibleProvider(
                "lm_studio", lm_studio.get("base_url", "http://localhost:1234/v1"),
                lm_studio.get("model", ""), lm_studio.get("api_key", "lm-studio")
            ),
            "openai": OpenAIProvider(
                openai.get("base_url", "https://api.openai.com/v1"), openai.get("model", ""), openai.get("api_key", "")
            ),
            "anthropic": AnthropicProvider(
                anthropic.get("base_url", "https://api.anthropic.com"), anthropic.get("model", ""), anthropic.get("api_key", "")
            ),
            "gemini": GeminiProvider(
                gemini.get("base_url", "https://generativelanguage.googleapis.com/v1beta"), gemini.get("model", ""), gemini.get("api_key", "") or os.getenv("GOOGLE_API_KEY", "")
            ),
        }
        self._availability: dict[str, list[str]] | None = None
        colibri = self.config.get("providers", {}).get("colibri", {})
        if colibri.get("enabled") is True:
            self.providers["colibri"] = ColibriProvider(
                colibri.get("base_url", "http://127.0.0.1:8000/v1"),
                colibri.get("model", ""), colibri.get("api_key", ""),
                context_length=colibri.get("context_length", 4096),
                timeout=float(colibri.get("timeout", 180)),
            )
        self._availability_expires: float = 0.0

    #: How long a discovery result stays usable. Provider availability changes
    #: on the timescale of a service starting or stopping, not of a function
    #: call -- and `doctor` alone asked for it twice per run, paying the full
    #: probe cost both times.
    AVAILABILITY_TTL_SECONDS = 5.0

    def available(self, *, refresh: bool = False) -> dict[str, list[str]]:
        """Which providers answer, probed concurrently and briefly cached.

        This was the slowest thing in the system by a wide margin. Probing ran
        serially, so every configured-but-absent local provider added its full
        connect cost to the total: on this machine LM Studio is not running and
        its refused probe took 4.17s, against 135ms for the Ollama that is.
        `doctor` then paid that twice, which was 8.6 of its 11 seconds.

        Probing in parallel makes the cost the slowest single provider rather
        than the sum, and the short cache makes the second call in a run free.
        """
        import time

        now = time.monotonic()
        if not refresh and self._availability is not None and now < self._availability_expires:
            return {name: list(models) for name, models in self._availability.items()}

        candidates = [
            (name, provider) for name, provider in self.providers.items()
            if self.cost_policy.provider_allowed(name, getattr(provider, "base_url", None))
            and not (name in {"openai", "anthropic", "gemini"} and not getattr(provider, "api_key", ""))
        ]
        result: dict[str, list[str]] = {}
        if candidates:
            from concurrent.futures import ThreadPoolExecutor

            def probe(item: tuple[str, ModelProvider]) -> tuple[str, list[str]]:
                name, provider = item
                try:
                    return name, provider.list_models()
                except Exception:
                    # An unreachable provider is a real answer -- "nothing here"
                    # -- not an error worth failing discovery over.
                    return name, []

            with ThreadPoolExecutor(max_workers=len(candidates), thread_name_prefix="provider-probe") as pool:
                result = dict(pool.map(probe, candidates))

        self._availability = {name: list(models) for name, models in result.items()}
        self._availability_expires = now + self.AVAILABILITY_TTL_SECONDS
        return {name: list(models) for name, models in result.items()}

    def get(self, purpose: str = "default_model") -> ModelProvider | None:
        selection = self.config.get(purpose, self.config.get("default_model", {}))
        benchmark = self._benchmark_selection(purpose)
        if benchmark and not self._installed(benchmark):
            # A saved benchmark pick outlives the model it picked: it named
            # qwen:latest after that model was removed, and every intent
            # classification then failed with a 404 against HAYAT's Ollama.
            benchmark = None
        if selection.get("provider", "auto") == "auto" and benchmark:
            selection = {**selection, **benchmark}
        if purpose == "classification_model" and not selection.get("model"):
            # Configured as automatic with no installed pick: classification is
            # deterministic. Handing it HAYAT's brain added a 5-9 s model call
            # to every message the rules did not recognise.
            return None
        if not selection.get("model") and purpose != "vision_model":
            # An automatic role with no usable pick answers with HAYAT's own
            # model rather than whichever model Ollama happens to list first.
            selection = {**selection, "model": (self.config.get("default_model") or {}).get("model")}
        if purpose == "vision_model" and not selection.get("provider"):
            return None
        env_name = {
            "vision_model": "AI_VISION_PROVIDER", "classification_model": "AI_CLASSIFICATION_PROVIDER",
            "conversation_model": "AI_CONVERSATION_PROVIDER", "reasoning_model": "AI_REASONING_PROVIDER",
        }.get(purpose, "AI_MODEL_PROVIDER")
        requested = os.getenv(env_name, selection.get("provider", "auto"))
        if requested and requested != "auto":
            provider = self.providers.get(requested)
            if provider and not self.cost_policy.provider_allowed(requested, getattr(provider, "base_url", None)):
                return None
            selected = copy(provider) if provider else None
            if selected and selection.get("model"):
                selected.model = selection["model"]
            names = self.available().get(requested, [])
            return selected if selected and self._model_present(requested, getattr(selected, 'model', ''), names) else None
        for name in ("ollama", "lm_studio", "colibri", "openai", "anthropic", "gemini"):
            if name not in self.providers:
                continue
            provider = self.providers[name]
            if not self.cost_policy.provider_allowed(name, getattr(provider, "base_url", None)):
                continue
            if name in {"openai", "anthropic", "gemini"} and not getattr(provider, "api_key", ""):
                continue
            names = self.available().get(name, [])
            desired = selection.get('model') or getattr(provider, 'model', '')
            if self._model_present(name, desired, names):
                selected = copy(provider)
                if selection.get("model"):
                    selected.model = selection["model"]
                return selected
        return None

    @staticmethod
    def _model_present(provider: str, model: str, names: list[str]) -> bool:
        if not names:
            return False
        if not model:
            return True
        if model in names:
            return True
        return provider == 'ollama' and ':' not in model.rsplit('/', 1)[-1] and model + ':latest' in names

    def _installed(self, pick: dict[str, str]) -> bool:
        """Whether the advertised inventory contains the saved pick's model."""
        available = self.available()
        names = available.get(str(pick.get("provider") or ""))
        return self._model_present(str(pick.get('provider') or ''), str(pick.get('model') or ''), names or [])

    @staticmethod
    def _benchmark_selection(purpose: str) -> dict[str, str] | None:
        path = Path(__file__).resolve().parents[2] / "data/benchmarks/models/selection.json"
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(purpose)
            return value if isinstance(value, dict) and value.get("provider") and value.get("model") else None
        except (OSError, json.JSONDecodeError):
            return None
