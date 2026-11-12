"""Optional loopback-only connection to Colibri's existing HTTP gateway.

This adapter does not install engines, convert weights, or infer capabilities
from a model family name. The model id must be advertised by /v1/models.
"""
from __future__ import annotations

import math
from urllib.parse import urlsplit

from app.models.local import OpenAICompatibleProvider
from app.policy.cost import is_local_endpoint


def parse_metrics(text: str) -> dict[str, float]:
    """Prometheus text exposition into {name{labels}: value}; comment and malformed lines are skipped."""
    values: dict[str, float] = {}
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # name{labels} value [timestamp]; a label value may itself hold spaces.
        end = line.rfind("}") + 1 if "{" in line else line.find(" ")
        if end <= 0:
            continue
        name, fields = line[:end], line[end:].split()
        try:
            number = float(fields[0])
        except (IndexError, ValueError):
            continue
        if math.isfinite(number):
            values[name.strip()] = number
    return values


def summarize_metrics(values: dict[str, float]) -> dict[str, float | int | None]:
    """The engine scheduler's own counters in plain terms (colibri_scheduler_* from GET /metrics)."""
    prefix = "colibri_scheduler_"

    def value(name: str) -> float | None:
        return values.get(prefix + name)

    def mean(name: str) -> float | None:
        total, count = value(name + "_sum"), value(name + "_count")
        return round(total / count, 3) if total is not None and count else None

    summary: dict[str, float | int | None] = {
        key: (int(found) if (found := value(key)) is not None else None)
        for key in ("active", "queued", "capacity", "completed_total", "failed_total", "rejected_total",
                    "timed_out_total", "cancelled_total")}
    summary.update(mean_first_output_seconds=mean("first_output_seconds"),
                   mean_request_seconds=mean("slot_duration_seconds"),
                   mean_queue_wait_seconds=mean("queue_wait_seconds"))
    return summary


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
