"""Bounded HTTP transport for explicit, zero-cost local model diagnostics."""
from __future__ import annotations

import json
import math
from urllib.parse import urlsplit

import httpx

from app.policy.cost import require_local_endpoint


MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def _request_url(endpoint: str, route: str) -> str:
    require_local_endpoint(endpoint)
    if urlsplit(endpoint).path not in {"", "/"}:
        raise PermissionError("Local model diagnostics require a root HTTP endpoint, without a path prefix.")
    if not isinstance(route, str):
        raise ValueError("Use an absolute local API path.")
    parsed = urlsplit(route)
    if (not route.startswith("/") or route.startswith("//") or parsed.scheme or parsed.netloc
            or parsed.query or parsed.fragment or "\\" in route
            or any(char.isspace() or ord(char) < 32 for char in route)):
        raise ValueError("Use an absolute local API path without an origin, query or fragment.")
    return endpoint.rstrip("/") + route


def validate_local_model(tag: dict) -> dict:
    """A loopback Ollama can expose cloud aliases; require local model metadata too."""
    if not isinstance(tag, dict):
        raise PermissionError("ZERO-COST: an installed local model record is required.")
    name = tag.get("name") or tag.get("model")
    details = tag.get("details") or {}
    if (not isinstance(name, str) or not name.strip() or name.strip().lower().endswith("cloud")
            or tag.get("remote_host") or tag.get("remote_model")
            or not isinstance(details, dict) or details.get("remote_host") or details.get("remote_model")):
        raise PermissionError("ZERO-COST: remote Ollama models and cloud aliases are blocked.")
    return tag


def request(endpoint: str, route: str, payload=None, timeout=180):
    """Return one local JSON object; never use environment proxies or follow redirects."""
    url = _request_url(endpoint, route)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("The local request timeout must be a positive finite number of seconds.")
    method = "GET" if payload is None else "POST"
    with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
        with client.stream(method, url, json=payload,
                           headers={"Accept": "application/json", "Content-Type": "application/json",
                                    "Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    size = int(declared)
                except ValueError as error:
                    raise ValueError("The local model response has an invalid Content-Length.") from error
                if not 0 <= size <= MAX_RESPONSE_BYTES:
                    raise ValueError("The local model response exceeds the 32 MiB JSON limit.")
            body = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("The local model response exceeds the 32 MiB JSON limit.")
                body.extend(chunk)
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("The local model service must return a JSON object.")
    return result
