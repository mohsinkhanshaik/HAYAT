from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
from urllib.parse import urlsplit


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_local_endpoint(url: str | None) -> bool:
    if not url:
        return False
    try:
        value = str(url)
        parsed = urlsplit(value)
        return (not any(char.isspace() or ord(char) < 32 for char in value)
                and parsed.scheme in {"http", "https"}
                and parsed.hostname in LOCAL_HOSTS
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment
                and (parsed.port is None or 0 < parsed.port <= 65535)
                and "\\" not in value)
    except ValueError:
        return False


def require_local_endpoint(url: str | None) -> None:
    """Check at the request boundary, including callers outside ModelRouter."""
    if not is_local_endpoint(url):
        raise PermissionError("ZERO-COST: model services must use a local loopback HTTP endpoint; cloud calls are blocked.")


def background_autostart_allowed(settings: dict[str, Any]) -> bool:
    """Optional workers require an actual boolean opt-in; missing means manual."""
    values = settings.get("background_services", {})
    return isinstance(values, dict) and values.get("autostart") is True


@dataclass(slots=True)
class CostPolicy:
    """Fail-closed policy for services that can create usage charges."""

    mode: str = "ZERO-COST"
    paid_api_allowed: bool = False
    cloud_inference_allowed: bool = False
    paid_search_allowed: bool = False
    cloud_embeddings_allowed: bool = False
    cloud_tts_allowed: bool = False
    cloud_stt_allowed: bool = False

    @classmethod
    def from_settings(cls, settings: dict[str, Any] | None) -> "CostPolicy":
        values = settings or {}
        return cls(
            mode=str(values.get("mode", "ZERO-COST")).strip().upper(),
            **{name: cls._flag(values.get(name, False), name) for name in (
                "paid_api_allowed", "cloud_inference_allowed", "paid_search_allowed",
                "cloud_embeddings_allowed", "cloud_tts_allowed", "cloud_stt_allowed")},
        )

    @staticmethod
    def _flag(value: Any, name: str) -> bool:
        if type(value) is not bool:
            raise ValueError(f"Cost policy {name} must be a YAML boolean, not text or a number.")
        return value

    def provider_allowed(self, provider: str, base_url: str | None = None) -> bool:
        try:
            self.assert_zero_cost()
        except RuntimeError:
            return False
        return is_local_endpoint(base_url)

    def status(self) -> dict[str, Any]:
        values = asdict(self)
        values.update(
            {
                "estimated_api_cost_usd": 0.0 if self.mode == "ZERO-COST" else None,
                "paid_provider_fallback": "disabled" if not self.cloud_inference_allowed else "explicit_only",
                "billing_verified": False,
                "cost_basis": "local model execution and uncredentialed public search; no provider billing audit",
                "note": "API cost excludes electricity, hardware, internet service, and disk usage.",
            }
        )
        return values

    def assert_zero_cost(self) -> None:
        if self.mode != "ZERO-COST":
            raise RuntimeError("Only ZERO-COST mode is supported; paid services must remain outside HAYAT.")
        enabled = [
            name
            for name in (
                "paid_api_allowed",
                "cloud_inference_allowed",
                "paid_search_allowed",
                "cloud_embeddings_allowed",
                "cloud_tts_allowed",
                "cloud_stt_allowed",
            )
            if getattr(self, name) is not False
        ]
        if enabled:
            raise RuntimeError(f"ZERO-COST mode cannot enable: {', '.join(enabled)}")
