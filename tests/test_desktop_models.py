from types import SimpleNamespace

import pytest

from app.models.desktop import DesktopModels


def runtime(host="http://localhost:11434"):
    return SimpleNamespace(router=SimpleNamespace(providers={"ollama": SimpleNamespace(base_url=host)}),
                           agent=SimpleNamespace(is_running=False),
                           model_resources=SimpleNamespace(status=lambda **kw: {}, can_load=lambda name: {"allowed": True}))


def test_remote_endpoint_and_uninstalled_model_cannot_be_controlled(monkeypatch):
    with pytest.raises(RuntimeError, match="local"):
        DesktopModels(runtime("https://example.com")).inventory()
    controller = DesktopModels(runtime())
    monkeypatch.setattr(controller, "inventory", lambda: [{"name": "installed", "loaded": False}])
    with pytest.raises(ValueError, match="installed"):
        controller.change_residency("not-installed", True)


def test_busy_task_and_memory_gate_block_load_before_post(monkeypatch):
    current = runtime()
    controller = DesktopModels(current)
    current.agent.is_running = True
    with pytest.raises(RuntimeError, match="current task"):
        controller.change_residency("small", True)
    current.agent.is_running = False
    current.model_resources.can_load = lambda name: {"allowed": False, "reason": "Not enough memory"}
    monkeypatch.setattr(controller, "inventory", lambda: [{"name": "small", "loaded": False}])
    with pytest.raises(RuntimeError, match="memory"):
        controller.change_residency("small", True)


@pytest.mark.parametrize("load", [True, False])
def test_load_and_unload_require_confirmed_residency(monkeypatch, load):
    import app.models.desktop as module
    controller = DesktopModels(runtime())
    calls = []
    states = iter([[{"name": "small", "loaded": not load}], [{"name": "small", "loaded": load}]])
    monkeypatch.setattr(controller, "inventory", lambda: next(states))
    def post(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(raise_for_status=lambda: None)
    monkeypatch.setattr(module.httpx, "post", post)
    assert controller.change_residency("small", load)[0]["loaded"] is load
    assert calls[0][1]["json"] == {"model": "small", "prompt": "", "stream": False, "keep_alive": "5m" if load else 0}
    monkeypatch.setattr(controller, "inventory", lambda: [{"name": "small", "loaded": not load}])
    with pytest.raises(RuntimeError, match="not confirmed"):
        controller.change_residency("small", load)
