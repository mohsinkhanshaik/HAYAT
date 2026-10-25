"""Optional loopback-only connection to Colibri's existing HTTP gateway.

This adapter does not install engines, convert weights, or infer capabilities
from a model family name. The model id must be advertised by /v1/models.
"""
from __future__ import annotations

import math
from urllib.parse import urlsplit

from app.models.local import OpenAICompatibleProvider
from app.policy.cost import is_local_endpoint


class ColibriProvider(OpenAICompatibleProvider):
    def __init__(self, base_url: str = "http://127.0.0.1:8000/v1", model: str = "",
                 api_key: str = "", *, context_length: int = 4096,
                 timeout: float = 180.0) -> None:
        parsed = urlsplit(base_url)
        if (not is_local_endpoint(base_url) or parsed.scheme not in {"http", "https"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path.rstrip("/") != "/v1"):
            raise ValueError("Colibri requires a loopback HTTP(S) base URL ending in /v1.")
        if type(context_length) is not int or not 256 <= context_length <= 1_048_576:
            raise ValueError("Set Colibri's actual configured context limit (256..1048576 tokens).")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Colibri timeout must be finite and positive.")
        super().__init__("colibri", base_url, model, api_key, timeout)
        self.context_length = context_length

    def generate_vision(self, prompt, image_path, temperature=None):
        from app.models.base import ModelError
        raise ModelError("HAYAT's Colibri connection is text-only; use the configured vision provider.",
                         provider=self.name, model=self.model)
