"""Explicit desktop model residency controls over the configured local Ollama."""
from __future__ import annotations

from urllib.parse import urlsplit
import httpx


class DesktopModels:
    def __init__(self, runtime):
        self.runtime = runtime

    def _provider(self):
        provider = self.runtime.router.providers.get("ollama")
        if provider is None:
            raise RuntimeError("Ollama is not configured.")
        host = (urlsplit(provider.base_url).hostname or "").casefold()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise RuntimeError("These controls manage the local Ollama service only.")
        return provider

    def inventory(self):
        provider = self._provider()
        base = provider.base_url.rstrip("/")
        tags = httpx.get(base + "/api/tags", timeout=5)
        tags.raise_for_status()
        resident = httpx.get(base + "/api/ps", timeout=5)
        resident.raise_for_status()
        loaded = {row.get("name") or row.get("model"): row for row in resident.json().get("models", [])}
        return [{"name": row["name"], "size": row.get("size", 0),
                 "loaded": row["name"] in loaded,
                 "vram": loaded.get(row["name"], {}).get("size_vram", 0),
                 "quantization": (row.get("details") or {}).get("quantization_level", "—")}
                for row in tags.json().get("models", [])]

    def change_residency(self, name: str, load: bool):
        from app.activity import ACTIVITY
        if self.runtime.agent.is_running or any(row.get("kind") in {"model", "image", "task"}
                                                for row in ACTIVITY.summary().get("running", [])):
            raise RuntimeError("Wait for the current task to finish before changing model memory.")
        provider = self._provider()
        rows = self.inventory()
        if name not in {row["name"] for row in rows}:
            raise ValueError("Select an installed model from the refreshed list.")
        if load:
            self.runtime.model_resources.status(refresh=True)
            decision = self.runtime.model_resources.can_load(name)
            if not decision.get("allowed"):
                raise RuntimeError(decision.get("reason") or "The model exceeds the current memory allowance.")
        with ACTIVITY.track("service", "Load model" if load else "Unload model", name):
            response = httpx.post(provider.base_url.rstrip("/") + "/api/generate",
                                  json={"model": name, "prompt": "", "stream": False,
                                        "keep_alive": "5m" if load else 0}, timeout=120)
            response.raise_for_status()
        after = self.inventory()
        actual = next((row["loaded"] for row in after if row["name"] == name), None)
        if actual is not load:
            raise RuntimeError("Ollama replied, but the requested memory state was not confirmed. Refresh to inspect it.")
        return after
