from app.models.base import Message, ModelProvider, ModelResponse, normalize_messages


class FakeProvider(ModelProvider):
    name = "fake"

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        return ModelResponse("ok", "fake-model", self.name)

    def list_models(self):
        return ["fake-model"]


def test_provider_interface():
    provider = FakeProvider()
    assert provider.is_available()
    assert provider.generate([Message("user", "hello")]).content == "ok"
    assert normalize_messages([Message("user", "hello")]) == [{"role": "user", "content": "hello"}]


def test_running_empty_server_is_not_a_ready_model(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(provider, 'list_models', lambda: [])
    assert not provider.is_available()


def test_model_router_checks_requested_model_and_preserves_inventory(monkeypatch):
    from app.models.router import ModelRouter
    router = ModelRouter({'default_model': {'provider': 'ollama', 'model': 'hayat:latest'}})
    monkeypatch.setattr(router, '_benchmark_selection', lambda _: None)
    monkeypatch.setattr(router.providers['ollama'], 'list_models', lambda: ['other:latest'])
    monkeypatch.setattr(router.providers['lm_studio'], 'list_models', lambda: [])
    assert router.get() is None
    inventory = router.available()
    inventory['ollama'].append('hayat:latest')
    assert router.get() is None
    monkeypatch.setattr(router.providers['ollama'], 'list_models', lambda: ['hayat:latest'])
    router.available(refresh=True)
    assert router.get().model == 'hayat:latest'
    router.config['default_model']['model'] = 'hayat'
    assert router.get().model == 'hayat'
