"""Owner-facing memory profiles and safe HAYAT-only memory release.

No process, driver, Windows service, or unrelated application is terminated.
The controller changes only HAYAT's Ollama request hints, unloads models from
the local Ollama API on explicit request, clears HAYAT caches, and asks Windows
to trim this process's working set.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
from enum import StrEnum
from typing import Any, Iterable

import httpx

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class ResourceMode(StrEnum):
    ECO = "eco"
    BALANCED = "balanced"
    PERFORMANCE = "performance"
    ADAPTIVE = "adaptive"


PROFILES: dict[ResourceMode, dict[str, Any]] = {
    ResourceMode.ECO: {"keep_alive": "2m", "num_ctx": 4096},
    ResourceMode.BALANCED: {"keep_alive": "10m", "num_ctx": 8192},
    ResourceMode.PERFORMANCE: {"keep_alive": "30m", "num_ctx": 16384},
}


class ResourceController:
    def __init__(self, runtime: Any, mode: ResourceMode | str = ResourceMode.BALANCED) -> None:
        self.runtime = runtime
        self._mode = ResourceMode(str(mode))
        self._lock = threading.RLock()
        self._last_release: dict[str, Any] | None = None
        self.set_mode(self._mode)

    @property
    def mode(self) -> ResourceMode:
        return self._mode

    def set_mode(self, mode: ResourceMode | str) -> dict[str, Any]:
        selected = ResourceMode(str(mode))
        profile = self._profile(selected)
        changed = 0
        with self._lock:
            for provider in self._ollama_providers():
                provider.keep_alive = profile["keep_alive"]
                provider.num_ctx = profile["num_ctx"]
                provider.context_length = profile["num_ctx"]
                changed += 1
            self._mode = selected
        return {"mode": selected.value, **profile, "providers_updated": changed}

    def unload_models(self) -> dict[str, Any]:
        unloaded: list[str] = []
        errors: list[str] = []
        endpoints = {str(provider.base_url).rstrip("/") for provider in self._ollama_providers()}
        for base_url in sorted(endpoints):
            try:
                response = httpx.get(f"{base_url}/api/ps", timeout=3.0)
                response.raise_for_status()
                names = [str(row.get("name") or row.get("model") or "").strip()
                         for row in response.json().get("models", [])]
                for name in filter(None, names):
                    result = httpx.post(
                        f"{base_url}/api/generate",
                        json={"model": name, "prompt": "", "stream": False, "keep_alive": 0},
                        timeout=15.0,
                    )
                    result.raise_for_status()
                    unloaded.append(name)
            except Exception as exc:
                errors.append(f"{base_url}: {type(exc).__name__}")
        return {"unloaded": unloaded, "count": len(unloaded), "errors": errors,
                "external_processes_terminated": False}

    def release_memory(self, *, unload_models: bool = True) -> dict[str, Any]:
        with self._lock:
            before = self._memory_snapshot()
            model_result = self.unload_models() if unload_models else {
                "unloaded": [], "count": 0, "errors": [],
                "external_processes_terminated": False,
            }
            cleared = self._clear_runtime_caches()
            collected = gc.collect()
            trimmed = self._trim_working_set()
            after = self._memory_snapshot()
            report = {
                "mode": self._mode.value, "before": before, "after": after,
                "released_process_bytes": max(0, before["rss_bytes"] - after["rss_bytes"]),
                "gc_objects": collected, "caches_cleared": cleared,
                "working_set_trimmed": trimmed, "models": model_result,
                "drivers_removed": False, "system_services_stopped": False,
                "unrelated_processes_terminated": False,
            }
            self._last_release = report
            return report

    def preload_brain(self, *, poster: Any = None, wait: bool = False) -> dict[str, Any]:
        """Load HAYAT's answer model now, in the background, so the first reply does not pay the load.

        HAYAT's 35B foundation takes about 23 s to load from cold. Loading it
        while the window opens moves that wait off the owner's first question.
        The request asks for the same window and residency the resource mode
        gave the provider, so Ollama does not reload it on the first turn; vision
        and DEEP advice share the same loaded weights. Nothing is loaded when the
        memory check says the model would not fit, or when it is already warm.
        """
        provider = getattr(self.runtime, "model", None)
        model = str(getattr(provider, "model", "") or "")
        base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
        if getattr(provider, "name", "") != "ollama" or not model or not base_url:
            return {"preloading": False, "reason": "HAYAT's answer model is not a local Ollama model."}
        resources = getattr(self.runtime, "model_resources", None)
        if resources is not None and hasattr(resources, "can_load"):
            try:
                admission = resources.can_load(model)
            except Exception:
                log_swallowed(_log, "checking memory before preloading HAYAT's model")
                admission = {}
            if admission.get("already_warm"):
                return {"preloading": False, "model": model, "reason": "already loaded"}
            if admission and not admission.get("allowed", True):
                return {"preloading": False, "model": model, "reason": "not enough free memory"}
        payload = {"model": model, "keep_alive": getattr(provider, "keep_alive", "10m"),
                   "options": {"num_ctx": int(getattr(provider, "num_ctx", 0) or 8192)}}
        post = poster or httpx.post

        def load() -> None:
            try:
                post(f"{base_url}/api/generate", json=payload, timeout=180)
            except Exception:
                log_swallowed(_log, "preloading HAYAT's answer model")

        worker = threading.Thread(target=load, name="brain-preload", daemon=True)
        worker.start()
        if wait:
            worker.join(timeout=200)
        return {"preloading": True, "model": model, "num_ctx": payload["options"]["num_ctx"]}

    def status(self) -> dict[str, Any]:
        profile = self._profile(self._mode)
        return {"mode": self._mode.value, **profile, "memory": self._memory_snapshot(),
                "last_release": self._last_release}

    def _profile(self, mode: ResourceMode) -> dict[str, Any]:
        """Return a real model window, never the fiction of an infinite one.

        Adaptive mode follows the context budget's live machine ceiling.  It
        can grow after a resident model is unloaded and shrink when memory is
        tight, while paged recall remains available beyond the model window.
        """
        if mode is not ResourceMode.ADAPTIVE:
            return dict(PROFILES[mode])
        budget = getattr(self.runtime, "context_budget", None)
        try:
            ceiling = int((budget.status() if budget is not None else {}).get("machine_ceiling") or 16384)
        except Exception:
            ceiling = 16384
        ceiling = max(4096, min(65536, ceiling))
        return {"keep_alive": "15m", "num_ctx": ceiling}

    def _ollama_providers(self) -> Iterable[Any]:
        candidates: list[Any] = []
        router = getattr(self.runtime, "router", None)
        if router is not None:
            candidates.extend(getattr(router, "providers", {}).values())
        for path in (
            ("model",), ("vision", "provider"), ("agent", "planner", "model"),
            ("conversation", "model"), ("conversation", "classification_model"),
            ("conversation", "reasoning_model"),
            # The DEEP-mode advisor is a router copy, not one of router.providers.
            # It shares HAYAT's foundation, so a different window would make
            # Ollama reload the model between every chat turn and its advice.
            ("escalation", "provider"),
        ):
            value = self.runtime
            for part in path:
                value = getattr(value, part, None)
                if value is None:
                    break
            if value is not None:
                candidates.append(value)
        seen: set[int] = set()
        for provider in candidates:
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            if getattr(provider, "name", "") == "ollama" and hasattr(provider, "base_url"):
                yield provider

    def _clear_runtime_caches(self) -> list[str]:
        cleared: list[str] = []
        for name in ("router", "model_resources", "retrieval", "embeddings", "research"):
            component = getattr(self.runtime, name, None)
            if component is None:
                continue
            if name == "router":
                if hasattr(component, "_availability"):
                    component._availability = None
                    component._availability_expires = 0.0
                    cleared.append("provider availability")
                continue
            clear = getattr(component, "clear_cache", None)
            if callable(clear):
                try:
                    clear()
                    cleared.append(name)
                except Exception:
                    log_swallowed(_log, "clearing a component cache")
            elif hasattr(component, "_cache"):
                try:
                    component._cache = None
                    cleared.append(name)
                except Exception:
                    log_swallowed(_log, "dropping a component cache")
        return cleared

    @staticmethod
    def _memory_snapshot() -> dict[str, int | float]:
        try:
            import psutil

            process = psutil.Process(os.getpid())
            memory = process.memory_info()
            system = psutil.virtual_memory()
            return {
                "rss_bytes": int(memory.rss), "private_bytes": int(getattr(memory, "private", 0)),
                "system_available_bytes": int(system.available),
                "system_percent": float(system.percent),
            }
        except Exception:
            return {"rss_bytes": 0, "private_bytes": 0,
                    "system_available_bytes": 0, "system_percent": 0.0}

    @staticmethod
    def _trim_working_set() -> bool:
        if os.name != "nt":
            return False
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            process = kernel32.GetCurrentProcess()
            return bool(kernel32.SetProcessWorkingSetSize(process, -1, -1))
        except Exception:
            return False
