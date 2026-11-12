"""HAYAT once told its owner its main model was unavailable while that model was loaded and ready.

Two causes, both seen on 2026-10-04 with the 22 GB Qwen3.6 35B brain:
* the memory check counted the loaded brain's own memory as used, so the brain "did not fit";
* a saved benchmark pick named qwen:latest after it was removed, so intent classification 404'd.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.models.resources import ModelResourceManager
from app.models.router import ModelRouter

GB = 1_000_000_000


def _manager(models, ram_available, vram_free_mb=0):
    storage = SimpleNamespace(model_inventory=lambda: {"models": [dict(row) for row in models]})
    observability = SimpleNamespace(system=lambda: {
        "gpu": [{"vram_free_mb": vram_free_mb}], "ram": {"available_bytes": ram_available}})
    return ModelResourceManager(storage, observability)


def test_a_loaded_brain_is_never_refused_for_the_memory_it_is_using():
    brain = {"model": "hayat:latest", "size_bytes": 22 * GB, "loaded": True, "loaded_memory_bytes": 22 * GB}
    admission = _manager([brain], ram_available=10 * GB).can_load("hayat:latest")
    assert admission["allowed"] and admission["already_warm"]


def test_memory_held_by_another_loaded_model_counts_as_reclaimable():
    rows = [{"model": "hayat:latest", "size_bytes": 22 * GB, "loaded": False},
            {"model": "big:27b", "size_bytes": 18 * GB, "loaded": True, "loaded_memory_bytes": 18 * GB}]
    admission = _manager(rows, ram_available=12 * GB).can_load("hayat:latest")
    assert admission["allowed"] and admission["reclaimable_loaded_model_bytes"] == 18 * GB


def test_the_brain_is_warm_when_its_foundation_is_loaded_under_another_name():
    # hayat:latest is the Qwen foundation with HAYAT's template: one set of weights, one Ollama runner.
    rows = [{"model": "hayat:latest", "size_bytes": 22_066_048_308, "loaded": False},
            {"model": "qwen3.6-35b-a3b-uncensored:q4_k_m", "size_bytes": 22_066_041_317, "loaded": True,
             "loaded_memory_bytes": 22_029_627_551}]
    admission = _manager(rows, ram_available=1 * GB).can_load("hayat:latest")
    assert admission["allowed"] and admission["already_warm"]
    assert admission["shares_weights_with"] == "qwen3.6-35b-a3b-uncensored:q4_k_m"
    other = [{"model": "other:30b", "size_bytes": 19 * GB, "loaded": True, "loaded_memory_bytes": 19 * GB}, rows[0]]
    assert _manager(other, ram_available=1 * GB).can_load("hayat:latest")["shares_weights_with"] is None


def test_a_model_that_truly_does_not_fit_is_still_refused():
    rows = [{"model": "huge:70b", "size_bytes": 45 * GB, "loaded": False}]
    assert not _manager(rows, ram_available=20 * GB, vram_free_mb=8000).can_load("huge:70b")["allowed"]


def _router(installed, pick, monkeypatch):
    router = ModelRouter({"default_model": {"provider": "ollama", "model": "hayat:latest"},
                          "classification_model": {"provider": "auto", "model": None},
                          "providers": {"ollama": {"base_url": "http://127.0.0.1:11435"}}})
    monkeypatch.setattr(router, "available", lambda **kw: {"ollama": installed})
    monkeypatch.setattr(ModelRouter, "_benchmark_selection", staticmethod(lambda purpose: pick))
    monkeypatch.setattr(router.providers["ollama"], "is_available", lambda: True)
    return router


def test_a_saved_pick_for_a_removed_model_is_ignored(monkeypatch):
    """Classification then stays deterministic: handing it HAYAT's brain added 5-9 s to every turn."""
    router = _router(["hayat:latest"], {"provider": "ollama", "model": "qwen:latest"}, monkeypatch)
    assert router.get("classification_model") is None


def test_a_saved_pick_for_an_installed_model_is_still_used(monkeypatch):
    router = _router(["hayat:latest", "tiny:1b"], {"provider": "ollama", "model": "tiny:1b"}, monkeypatch)
    assert router.get("classification_model").model == "tiny:1b"


# 2026-11-07: "HAYAT's main model is currently unavailable" again, with the brain cold and free RAM briefly short.
def test_the_check_says_whether_a_model_fits_the_machine_at_all():
    rows = [{"model": "hayat:latest", "size_bytes": 22 * GB, "loaded": False}]
    storage = SimpleNamespace(model_inventory=lambda: {"models": [dict(row) for row in rows]})
    tight = SimpleNamespace(system=lambda: {"gpu": [{"vram_free_mb": 300, "vram_total_mb": 8151}],
                                            "ram": {"available_bytes": 12 * GB, "total_bytes": 40 * GB}})
    decision = ModelResourceManager(storage, tight).can_load("hayat:latest")
    assert not decision["allowed"] and decision["fits_machine"]  # not free now, but the machine can hold it
    small = SimpleNamespace(system=lambda: {"gpu": [{"vram_free_mb": 300, "vram_total_mb": 4000}],
                                            "ram": {"available_bytes": 4 * GB, "total_bytes": 16 * GB}})
    assert not ModelResourceManager(storage, small).can_load("hayat:latest")["fits_machine"]


def test_the_brain_tries_a_load_that_fits_the_machine_but_other_models_wait(tmp_path):
    from app.models.intelligent_router import IntelligentModelRouter
    rows = [{"model": name, "provider": "ollama", "capabilities": ["completion"], "size_bytes": 1,
             "benchmark_score": {"coding_accuracy": score, "mean_latency_seconds": 1}}
            for name, score in (("hayat:latest", .95), ("helper:7b", .7))]
    brain = IntelligentModelRouter(SimpleNamespace(model_inventory=lambda: {"models": rows}),
                                   SimpleNamespace(providers={"ollama": SimpleNamespace(model="hayat:latest")}), tmp_path / "r.db")
    brain.resource_manager = SimpleNamespace(can_load=lambda model: {"allowed": False, "fits_machine": True})
    assert brain._resources_allow("hayat:latest") and not brain._resources_allow("helper:7b")
    brain.resource_manager = SimpleNamespace(can_load=lambda model: {"allowed": False, "fits_machine": False})
    assert not brain._resources_allow("hayat:latest")  # larger than the whole machine: still refused


def test_a_refused_request_brings_the_service_back_and_tries_once_more(monkeypatch):
    import httpx
    from app.models import local
    from app.models.local import OllamaProvider
    calls, recovered = [], []

    def post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectError("[WinError 10061] refused", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(local, "SERVICE_RECOVERY", lambda: recovered.append(1) or (True, "restarted"))
    provider = OllamaProvider("http://127.0.0.1:11435", "hayat:latest")
    assert provider.generate([{"role": "user", "content": "hi"}]).content == "ok"
    assert len(calls) == 2 and recovered == [1]


def test_a_service_that_cannot_be_brought_back_says_why_and_a_slow_one_is_not_restarted(monkeypatch):
    import httpx
    import pytest
    from app.models import local
    from app.models.base import ModelError
    from app.models.local import OllamaProvider
    recovered = []
    monkeypatch.setattr(local, "SERVICE_RECOVERY", lambda: recovered.append(1) or (False, "ollama exited during startup"))
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (_ for _ in ()).throw(
        httpx.ConnectError("refused", request=httpx.Request("POST", url))))
    with pytest.raises(ModelError, match="could not be brought back: ollama exited during startup"):
        OllamaProvider("http://127.0.0.1:11435", "hayat:latest").generate([{"role": "user", "content": "hi"}])
    recovered.clear()
    monkeypatch.setattr(httpx, "post", lambda url, **kw: (_ for _ in ()).throw(
        httpx.ReadTimeout("slow", request=httpx.Request("POST", url))))
    with pytest.raises(ModelError):
        OllamaProvider("http://127.0.0.1:11435", "hayat:latest").generate([{"role": "user", "content": "hi"}])
    assert recovered == []  # slow is not down: nothing restarted


def test_an_inventory_read_that_finds_the_service_down_brings_it_back_first(monkeypatch, tmp_path):
    # With the service down the inventory was empty, the brain looked "not installed", and no request ever
    # reached the provider's recovery: every reply was "main model unavailable".
    import httpx
    from app.models import local
    from app.storage.manager import StorageManager
    up, recovered = [False], []
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")

    def get(url, **kwargs):
        if not up[0]:
            raise httpx.ConnectError("[WinError 10061] refused", request=httpx.Request("GET", url))
        body = {"models": [{"name": "hayat:latest", "size": 22 * GB}]} if url.endswith("/api/tags") else {"models": []}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    def recover():
        recovered.append(1)
        up[0] = True
        return True, "restarted"

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(200, json={}, request=httpx.Request("POST", url)))
    monkeypatch.setattr(local, "SERVICE_RECOVERY", recover)
    storage = StorageManager.__new__(StorageManager)
    import threading
    storage._cache_lock, storage._model_metadata_cache = threading.Lock(), {}
    storage._model_score = lambda name: None
    names = [row["model"] for row in storage.model_inventory()["models"]]
    assert names == ["hayat:latest"] and recovered == [1]
