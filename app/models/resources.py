from __future__ import annotations

import copy
import threading
import time
from typing import Any


class ModelResourceManager:
    """Resource gate for local model selection. Automatic eviction is opt-in and off by default."""

    def __init__(self, storage, observability, config: dict[str, Any] | None = None) -> None:
        self.storage = storage; self.observability = observability; self.config = config or {}
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = threading.RLock()

    def status(self, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            if not refresh and self._cache and time.monotonic() - self._cache[0] <= 60:
                return copy.deepcopy(self._cache[1]) | {"cache": "hit"}
            inventory = self.storage.model_inventory(); system = self.observability.system()
            for row in inventory["models"]:
                row["resource_state"] = "WARM" if row.get("loaded") else "COLD"
            result = {"models": inventory["models"], "system": system, "automatic_eviction": bool(self.config.get("automatic_model_eviction", False)), "states": ["COLD", "LOADING", "WARM", "BUSY", "UNAVAILABLE"], "cache": "miss"}
            self._cache = (time.monotonic(), copy.deepcopy(result))
            return result

    def quick_status(self) -> dict[str, Any]:
        """Return cached detail or an honest cheap summary; never trigger a model scan."""
        with self._lock:
            if self._cache:
                result = copy.deepcopy(self._cache[1])
                result["cache"] = "hit"
                return result
        return {
            "models": [], "inventory_state": "not_scanned_in_this_runtime",
            "automatic_eviction": bool(self.config.get("automatic_model_eviction", False)),
            "states": ["COLD", "LOADING", "WARM", "BUSY", "UNAVAILABLE"],
        }

    def can_load(self, model: str) -> dict[str, Any]:
        status = self.status(); row = next((item for item in status["models"] if item["model"] == model), None)
        if row is None: return {"allowed": False, "reason": "Model is not installed.", "model": model}
        gpu = next((item for item in status["system"]["gpu"] if item.get("vram_free_mb") is not None), {})
        needed = int(row.get("size_bytes") or 0); available = int(gpu.get("vram_free_mb") or 0) * 1024 * 1024 + int(status["system"]["ram"]["available_bytes"])
        # hayat:latest is built FROM the Qwen foundation that is also the DEEP and vision model: one set of weights
        # under two names, served by one Ollama runner. Loaded under the other name, it was still counted cold and
        # needed 22 GB of headroom; when free RAM dipped, HAYAT called its own loaded brain "unavailable"
        # (2026-10-13 hard-chat check). Same weights = same size to within 0.1 %.
        twin = next((item["model"] for item in status["models"] if item.get("loaded") and item["model"] != model and needed
                     and abs(int(item.get("size_bytes") or 0) - needed) <= needed * .001), None)
        warm = bool(row.get("loaded")) or twin is not None
        # Memory held by models Ollama already has loaded is not lost: Ollama
        # unloads an idle model to make room for the next one. Without this,
        # HAYAT's own 22 GB brain, once loaded, used up the very memory the
        # check counted, and HAYAT then refused to answer with the model that
        # was loaded and ready ("HAYAT's main model is currently unavailable").
        reclaimable = sum(int(item.get("loaded_memory_bytes") or 0) for item in status["models"] if item.get("loaded"))
        fits = needed <= (available + reclaimable) * .85
        return {"allowed": warm or fits, "model": model, "estimated_bytes": needed, "available_ram_plus_vram_bytes": available,
                "reclaimable_loaded_model_bytes": reclaimable, "already_warm": warm, "shares_weights_with": twin,
                "automatic_eviction_performed": False}
