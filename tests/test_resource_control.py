from __future__ import annotations

from types import SimpleNamespace

from app.resource_control import ResourceController


def _runtime():
    provider = SimpleNamespace(name="ollama", base_url="http://localhost:11434",
                               keep_alive="30m", num_ctx=16384)
    router = SimpleNamespace(providers={"ollama": provider},
                             _availability={"ollama": ["model"]}, _availability_expires=10)
    return SimpleNamespace(router=router, model=provider, vision=None, agent=None,
                           conversation=None, model_resources=SimpleNamespace(_cache=(1, {})),
                           retrieval=None, embeddings=None, research=None), provider


def test_resource_profiles_update_every_live_ollama_reference_once():
    runtime, provider = _runtime()
    controller = ResourceController(runtime)
    result = controller.set_mode("eco")
    assert result == {"mode": "eco", "keep_alive": "2m", "num_ctx": 4096,
                      "providers_updated": 1}
    assert provider.keep_alive == "2m" and provider.num_ctx == 4096
    assert provider.context_length == 4096
    result = controller.set_mode("performance")
    assert result["keep_alive"] == "30m" and provider.num_ctx == 16384


def test_the_deep_mode_advisor_gets_the_same_window_as_hayat():
    """The advisor is a router copy sharing HAYAT's foundation; a different window reloads the model each turn."""
    runtime, provider = _runtime()
    advisor = SimpleNamespace(name="ollama", base_url="http://localhost:11435", keep_alive="30m", num_ctx=16384)
    runtime.escalation = SimpleNamespace(provider=advisor)
    result = ResourceController(runtime).set_mode("balanced")
    assert result["providers_updated"] == 2
    assert advisor.num_ctx == provider.num_ctx == advisor.context_length == 8192


def _brain_runtime(**admission):
    runtime, provider = _runtime()
    provider.model = "hayat:latest"
    runtime.model_resources = SimpleNamespace(can_load=lambda model: {"allowed": True, **admission})
    return runtime, provider


def test_the_brain_is_preloaded_with_the_window_the_mode_gave_it():
    runtime, _ = _brain_runtime()
    posted = []
    controller = ResourceController(runtime)  # balanced: 8K, 10 minutes
    result = controller.preload_brain(poster=lambda url, json, timeout: posted.append((url, json)), wait=True)
    assert result == {"preloading": True, "model": "hayat:latest", "num_ctx": 8192}
    assert posted == [("http://localhost:11434/api/generate",
                       {"model": "hayat:latest", "keep_alive": "10m", "options": {"num_ctx": 8192}})]


def test_the_brain_is_not_preloaded_when_warm_or_when_it_would_not_fit():
    posted = []

    def poster(url, json, timeout):
        posted.append(json)

    warm, _ = _brain_runtime(already_warm=True)
    assert ResourceController(warm).preload_brain(poster=poster, wait=True)["reason"] == "already loaded"
    full, _ = _brain_runtime(allowed=False)
    assert ResourceController(full).preload_brain(poster=poster, wait=True)["reason"] == "not enough free memory"
    cloud, provider = _brain_runtime()
    provider.name = "openai"
    assert ResourceController(cloud).preload_brain(poster=poster, wait=True)["preloading"] is False
    assert posted == []


def test_a_failed_preload_is_logged_not_raised():
    runtime, _ = _brain_runtime()

    def poster(url, json, timeout):
        raise ConnectionError("Ollama is not running")

    assert ResourceController(runtime).preload_brain(poster=poster, wait=True)["preloading"] is True


def test_adaptive_profile_uses_live_machine_ceiling_without_claiming_infinity():
    runtime, provider = _runtime()
    runtime.context_budget = SimpleNamespace(status=lambda: {"machine_ceiling": 32768})
    controller = ResourceController(runtime)
    result = controller.set_mode("adaptive")
    assert result["num_ctx"] == 32768
    assert result["keep_alive"] == "15m"
    assert provider.context_length == 32768
    assert controller.status()["mode"] == "adaptive"


def test_release_memory_only_unloads_hayat_models_and_caches(monkeypatch):
    runtime, _ = _runtime()
    controller = ResourceController(runtime)
    calls = []

    class Response:
        def __init__(self, body=None):
            self.body = body or {}
        def raise_for_status(self):
            return None
        def json(self):
            return self.body

    monkeypatch.setattr("app.resource_control.httpx.get",
                        lambda *args, **kwargs: Response({"models": [{"name": "local-model"}]}))
    monkeypatch.setattr("app.resource_control.httpx.post",
                        lambda url, **kwargs: calls.append((url, kwargs["json"])) or Response())
    monkeypatch.setattr(controller, "_trim_working_set", lambda: True)
    snapshots = iter([
        {"rss_bytes": 500, "private_bytes": 700, "system_available_bytes": 1000, "system_percent": 80.0},
        {"rss_bytes": 300, "private_bytes": 500, "system_available_bytes": 1200, "system_percent": 75.0},
    ])
    monkeypatch.setattr(controller, "_memory_snapshot", lambda: next(snapshots))

    report = controller.release_memory()
    assert calls[0][1]["keep_alive"] == 0
    assert report["released_process_bytes"] == 200
    assert report["models"]["unloaded"] == ["local-model"]
    assert report["drivers_removed"] is False
    assert report["unrelated_processes_terminated"] is False
    assert runtime.router._availability is None and runtime.model_resources._cache is None
