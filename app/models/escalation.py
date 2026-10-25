"""A larger local model HAYAT consults in the DEEP mode. HAYAT still answers.

``escalation_model`` in config/models.yaml names it. When the DEEP mode is
selected and a turn is worth it, this model writes a second opinion first, and
HAYAT reads it and writes the reply (app/models/council.py). It never writes
the answer itself, and every other mode does not consult it at all.

Since 2026-10-04 the advisor is HAYAT's own foundation, Qwen3.6-35B-A3B
Uncensored, without HAYAT's identity prompt. It shares the weights hayat:latest
already has loaded, so advice costs no reload. Measured on this laptop it
answered 8 of 8 check questions at about 31 tokens/s, where the previous
advisor, a 27B model running mostly from RAM, answered 7 of 8 at 3.2 tokens/s.
The gains are still in using it well:

* it is loaded in the background the moment DEEP is chosen, so the first
  answer does not pay the load;
* short social turns ("thanks", "ok") stay on HAYAT -- a second opinion on
  "you're welcome" is not depth;
* its advice is capped so it finishes inside the timeout instead of running
  into it and being thrown away;
* the router's health breaker is shared, so after repeated failures it stops
  being tried for a while and HAYAT answers straight away.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.logging import log_swallowed
from app.models.base import ModelProvider

_log = logging.getLogger(__name__)

USES = frozenset({"deep_mode"})
DEFAULT_TIMEOUT_SECONDS = 600.0
#: 8K reads as fast as 4K here and leaves more RAM than the 16K default.
DEFAULT_NUM_CTX = 8192
#: Enough for a real second opinion (about 25 s at the 35B foundation's ~31
#: tokens/s; it was ~3 minutes on the old 27B), leaving HAYAT time to write the
#: reply well inside the timeout.
DEFAULT_MAX_ADVICE_TOKENS = 700
KEEP_ALIVE = "30m"

_SOCIAL = re.compile(
    r"^(?:hi|hello|hey|yo|thanks?|thank you|thx|ty|ok(?:ay)?|k|cool|nice|great|perfect|"
    r"yes|yeah|yep|no|nope|sure|bye|good (?:morning|afternoon|evening|night))"
    r"(?:\s+(?:hayat|man|bro|so much|a lot))?[\s!.,]*$",
    re.IGNORECASE,
)


def worth_escalating(text: str) -> bool:
    """Whether a turn deserves the slow model: a question or a real request, not a pleasantry."""
    value = " ".join(str(text or "").split())
    if not value or _SOCIAL.match(value):
        return False
    from app.answers import is_small_talk

    if is_small_talk(value):
        # "tell me a joke" in God mode waited 14 s for a thinking advisor.
        return False
    return "?" in value or len(value.split()) >= 4


class CappedProvider(ModelProvider):
    """Pass-through that never asks for more than ``cap`` answer tokens."""

    def __init__(self, inner: ModelProvider, cap: int) -> None:
        self.inner, self.cap = inner, max(64, int(cap))
        self.name = getattr(inner, "name", "ollama")
        self.model = getattr(inner, "model", "")

    @property
    def context_length(self):
        from app.models.base import model_context_limit
        return model_context_limit(self.inner)

    def _limit(self, max_tokens: int | None) -> int:
        return self.cap if max_tokens is None else min(int(max_tokens), self.cap)

    def list_models(self) -> list[str]:
        return self.inner.list_models()

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        return self.inner.generate(messages, tools, temperature, response_schema, max_tokens=self._limit(max_tokens))

    def generate_stream(self, messages, *, on_chunk, cancel=None, temperature=None, max_tokens=None):
        return self.inner.generate_stream(messages, on_chunk=on_chunk, cancel=cancel,
                                          temperature=temperature, max_tokens=self._limit(max_tokens))


@dataclass
class Escalation:
    provider: ModelProvider | None = None
    uses: frozenset[str] = field(default_factory=frozenset)
    num_ctx: int = DEFAULT_NUM_CTX
    max_advice_tokens: int = DEFAULT_MAX_ADVICE_TOKENS
    #: The intelligent router's admission and outcome callbacks, so its health
    #: breaker covers this model like any other.
    admission: Callable[[str], bool] | None = None
    on_failure: Callable[..., None] | None = None
    on_success: Callable[[str], None] | None = None
    poster: Callable[..., Any] | None = field(default=None, repr=False)

    @property
    def model(self) -> str:
        return str(getattr(self.provider, "model", "") or "")

    def bind(self, router: Any) -> None:
        """Share the router's circuit breaker and outcome records."""
        self.admission = getattr(router, "_admit", None)
        self.on_failure = getattr(router, "_record_failure", None)
        self.on_success = getattr(router, "_record_success", None)

    def for_modes(self, modes: Iterable[str]) -> ModelProvider | None:
        return self.provider if "deep_mode" in self.uses and "DEEP" in set(modes) else None

    def advisor(self, text: str, modes: Iterable[str]) -> ModelProvider | None:
        """The capped advisor for this turn, or None: not DEEP, not worth it, or its breaker is open."""
        larger = self.for_modes(modes)
        if larger is None or not worth_escalating(text):
            return None
        if self.admission is not None and not self.admission(self.model):
            return None
        return CappedProvider(larger, self.max_advice_tokens)

    def warm(self, modes: Iterable[str]) -> bool:
        """Load the model in the background when DEEP is chosen; False when there is nothing to do."""
        if self.for_modes(modes) is None:
            return False
        base_url = str(getattr(self.provider, "base_url", "") or "").rstrip("/")
        if not base_url:
            return False
        # The window the advice call will ask for, read now: the desktop's
        # resource mode (Eco/Balanced/Performance) resizes every HAYAT model,
        # this one included, after startup. Preloading at the configured size
        # instead made Ollama reload the shared 35B foundation twice per DEEP
        # choice, once here and again on the next chat turn.
        window = int(getattr(self.provider, "num_ctx", None) or self.num_ctx)
        payload = {"model": self.model, "keep_alive": KEEP_ALIVE, "options": {"num_ctx": window}}

        def load() -> None:
            try:
                post = self.poster
                if post is None:
                    import httpx

                    post = httpx.post
                post(f"{base_url}/api/generate", json=payload, timeout=180)
            except Exception:
                log_swallowed(_log, "preloading the DEEP-mode escalation model")

        threading.Thread(target=load, name="escalation-warm", daemon=True).start()
        return True

    def status(self) -> dict[str, Any]:
        return {"model": self.model or None, "uses": sorted(self.uses), "available": self.provider is not None,
                "num_ctx": self.num_ctx, "max_advice_tokens": self.max_advice_tokens,
                "thinks": bool(getattr(self.provider, "think", False))}


def load_escalation(router: Any) -> Escalation:
    """Build the escalation from the router's config; empty when none is configured."""
    config = (getattr(router, "config", None) or {}).get("escalation_model") or {}
    if not config.get("model"):
        return Escalation()
    uses = frozenset(str(use) for use in config.get("use_for") or ()) & USES
    num_ctx = int(config.get("num_ctx") or DEFAULT_NUM_CTX)
    provider = router.get("escalation_model")
    if provider is not None:
        if hasattr(provider, "timeout"):
            provider.timeout = float(config.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
        # Ollama reloads a model to resize its cache, so every call -- and the
        # warm-up -- must ask for the same window.
        provider.num_ctx = num_ctx
        provider.context_length = num_ctx
        provider.keep_alive = KEEP_ALIVE
        # Think before advising: DEEP is where depth is worth the wait.
        provider.think = bool(config.get("think", False))
    return Escalation(provider, uses, num_ctx, int(config.get("max_advice_tokens") or DEFAULT_MAX_ADVICE_TOKENS))
