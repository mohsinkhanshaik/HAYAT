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
