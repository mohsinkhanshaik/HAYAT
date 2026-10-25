"""No billable endpoint is used: negative canaries plus a real loopback server."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from app.config import load_settings
from app.integrations.connectors import ConnectorGateway
from app.integrations.mcp_gateway import MCPGateway, MCPServerManifest, register_builtin_web_research
from app.memory.embeddings import OllamaEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from app.models.cloud import AnthropicProvider, GeminiProvider, OpenAIProvider
from app.models.local import OllamaProvider, OpenAICompatibleProvider
from app.models.router import ModelRouter
from app.policy.cost import CostPolicy, background_autostart_allowed, is_local_endpoint
from app.research.search import build_search_provider


@pytest.fixture
def no_http(monkeypatch):
    attempts = []
    def forbidden(*args, **kwargs):
        attempts.append(args)
        raise AssertionError('Unexpected network request')
    for method in ('get', 'post', 'stream'):
        monkeypatch.setattr(httpx, method, forbidden)
    yield
    assert attempts == [], 'Policy must reject before any HTTP call, even if a caller swallows the error'


@pytest.mark.parametrize('url', [
    None, '', 'https://billable.example/v1', 'http://localhost.billable.example',
    'http://localhost@billable.example', 'http://user:pass@localhost/v1',
    'file://localhost/v1', '//localhost/v1', 'ftp://127.0.0.1',
    'http://localhost:99999', 'http://localhost:bad', 'http://[broken',
    'http://localhost/v1?api_key=secret', 'http://localhost/#remote',
    'http://localhost/\nremote', 'http://localhost\\@billable.example',
])
def test_endpoint_is_not_local_merely_because_it_mentions_localhost(url):
    assert not is_local_endpoint(url)
    assert not CostPolicy().provider_allowed('ollama', url)
    assert not CostPolicy().provider_allowed('lm_studio', url)


@pytest.mark.parametrize('url', ['http://localhost:11435', 'https://127.0.0.1:1234/v1', 'http://[::1]:8000/v1'])
def test_valid_loopback_endpoints_remain_available(url):
    assert is_local_endpoint(url)
    assert CostPolicy().provider_allowed('ollama', url)


@pytest.mark.parametrize('value', ['false', 'true', 0, 1, None, [], {}])
@pytest.mark.parametrize('flag', ['paid_api_allowed', 'cloud_inference_allowed', 'paid_search_allowed',
                                 'cloud_embeddings_allowed', 'cloud_tts_allowed', 'cloud_stt_allowed'])
def test_cost_flags_reject_ambiguous_types(flag, value):
    with pytest.raises(ValueError, match='boolean'):
        CostPolicy.from_settings({flag: value})


@pytest.mark.parametrize('mode', ['PAID', '', 'ZERO_COST', 'AUTO'])
def test_unknown_cost_modes_do_not_bypass_validation(mode):
    policy = CostPolicy.from_settings({'mode': mode})
    with pytest.raises(RuntimeError, match='ZERO-COST'):
        policy.assert_zero_cost()
    assert not policy.provider_allowed('ollama', 'http://localhost:11435')


@pytest.mark.parametrize('flag', ['paid_api_allowed', 'cloud_inference_allowed', 'paid_search_allowed',
                                 'cloud_embeddings_allowed', 'cloud_tts_allowed', 'cloud_stt_allowed'])
def test_paid_flags_cannot_be_enabled_by_configuration(flag):
    policy = CostPolicy.from_settings({flag: True})
    with pytest.raises(RuntimeError, match=flag):
        ModelRouter({'providers': {}}, policy)


@pytest.mark.parametrize('factory', [
    lambda: OllamaProvider('https://billable.example', 'm'),
    lambda: OpenAICompatibleProvider('lm_studio', 'https://billable.example/v1', 'm', 'secret'),
    lambda: OpenAIProvider('https://billable.example/v1', 'm', 'secret'),
    lambda: AnthropicProvider('https://billable.example', 'm', 'secret'),
    lambda: GeminiProvider('https://billable.example', 'm', 'secret'),
])
def test_direct_adapters_cannot_bypass_cost_policy(factory, no_http, tmp_path):
    provider = factory()
    with pytest.raises(PermissionError, match='ZERO-COST'):
        provider.list_models()
    with pytest.raises(PermissionError, match='ZERO-COST'):
        provider.generate([{'role': 'user', 'content': 'hello'}])
    image = tmp_path / 'example.png'
    image.write_bytes(b'local fixture')
    with pytest.raises(PermissionError, match='ZERO-COST'):
        provider.generate_vision('describe', image)
    if hasattr(provider, 'generate_stream'):
        with pytest.raises(PermissionError, match='ZERO-COST'):
            provider.generate_stream([], on_chunk=lambda _: None)


@pytest.mark.parametrize('model', ['gpt-oss:120b-cloud', 'some-model:cloud'])
def test_local_ollama_does_not_mean_cloud_models_are_free(model, no_http):
    provider = OllamaProvider('http://127.0.0.1:11435', model)
    with pytest.raises(PermissionError, match='cloud'):
        provider.generate([])
    with pytest.raises(PermissionError, match='cloud'):
        provider.generate_stream([], on_chunk=lambda _: None)


def test_remote_embeddings_never_probe_or_generate_even_with_legacy_cloud_opt_in(no_http):
    ollama = OllamaEmbeddingProvider('https://billable.example', 'm')
    compatible = OpenAICompatibleEmbeddingProvider('https://billable.example/v1', 'm', 'secret', allow_cloud=True)
    for provider in (ollama, compatible):
        assert not provider.available
        with pytest.raises(PermissionError, match='ZERO-COST'):
            provider.embed(['private text'])


@pytest.mark.parametrize('requested', ['auto', 'brave', 'bing', 'duckduckgo'])
def test_search_credentials_and_stale_paid_flags_cannot_enable_subscription_search(monkeypatch, requested, no_http):
    monkeypatch.setenv('BRAVE_SEARCH_API_KEY', 'configured')
    monkeypatch.setenv('AI_SEARCH_PROVIDER', requested)
    chain = build_search_provider({'paid_search_allowed': True})
    assert chain.providers
    assert 'brave' not in [provider.name for provider in chain.providers]


def test_runtime_connector_profile_rejects_before_credentials_or_requests(no_http):
    def forbidden(*args):
        pytest.fail('Connector or credential lookup must not run')
    gateway = ConnectorGateway(SimpleNamespace(get=forbidden), zero_cost_only=True)
    for write in (False, True):
        with pytest.raises(PermissionError, match='manually'):
            gateway.call('service', 'action', write=write)


def test_only_exact_builtin_mcp_manifest_is_admitted(tmp_path):
    gateway = MCPGateway(tmp_path / 'mcp.db', zero_cost_only=True)
    register_builtin_web_research(gateway)
    assert gateway._prepared('zero-cost-web-research')[0].enabled
    gateway.register(MCPServerManifest('external', 'External', sys.executable, ['-c', 'print(1)']), enable=True)
    with pytest.raises(PermissionError, match='ZERO-COST'):
        gateway.tools('external')
    # Keeping the trusted ID cannot smuggle a different process or credential.
    gateway.register(MCPServerManifest('zero-cost-web-research', 'Fake', sys.executable, ['-c', 'print(1)']), enable=True)
    with pytest.raises(PermissionError, match='ZERO-COST'):
        gateway._prepared('zero-cost-web-research')


@pytest.mark.parametrize('settings', [{}, {'background_services': None},
    {'background_services': {'autostart': 'true'}}, {'background_services': {'autostart': 1}}])
def test_optional_background_services_default_to_manual(settings):
    assert not background_autostart_allowed(settings)


def test_current_profile_is_zero_cost_and_manual():
    settings = load_settings()
    CostPolicy.from_settings(settings['cost_policy']).assert_zero_cost()
    assert not background_autostart_allowed(settings)
    assert settings['training']['automatic_training'] is False


def test_runtime_manual_profile_does_not_start_optional_workers(monkeypatch):
    from app.runtime import build_runtime
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)
    monkeypatch.delenv('HAYAT_DISABLE_BACKGROUND', raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Manual profile started an optional worker')
    for target in (
        'app.automation.scheduler.AgentScheduler.start',
        'app.automation.event_engine.EventTriggerEngine.start',
        'app.learning.continuous.ContinuousLearner.start',
        'app.development.code_monitor.CodeMonitor.start',
        'app.market_alerts.AlertWatcher.start',
        'app.intelligence.discovery.SourceScout.start',
    ):
        monkeypatch.setattr(target, forbidden)
    runtime = build_runtime(use_model=False, start_background_services=True)
    try:
        assert not runtime.intelligence.status()['running']
        assert runtime.connector_gateway.zero_cost_only
        assert runtime.mcp_gateway.zero_cost_only
        runtime.cost_policy.assert_zero_cost()
    finally:
        runtime.close()


def test_real_local_model_and_embedding_calls_ignore_proxy_environment(monkeypatch):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            calls.append(self.path)
            self.reply({'models': [{'name': 'local'}, {'name': 'alias', 'remote_host': 'https://cloud.example'},
                                   {'name': 'model:cloud'}]})
        def do_POST(self):
            calls.append(self.path)
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/api/show':
                self.reply({'capabilities': ['embedding']})
            elif self.path == '/api/embed':
                self.reply({'embeddings': [[1.0, 2.0] for _ in request['input']]})
            elif self.path == '/v1/embeddings':
                self.reply({'data': [{'index': i, 'embedding': [1.0, 2.0]} for i, _ in enumerate(request['input'])]})
            else:
                self.reply({'model': 'local', 'message': {'content': 'local answer'}, 'done': True})
        def reply(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    url = f'http://127.0.0.1:{server.server_port}'
    embeddings = None
    try:
        provider = OllamaProvider(url, 'local')
        assert provider.list_models() == ['local']
        assert provider.generate([{'role': 'user', 'content': 'hello'}]).content == 'local answer'
        embeddings = OllamaEmbeddingProvider(url, 'local')
        assert embeddings.embed(['text']) == [[1.0, 2.0]]
        assert OpenAICompatibleEmbeddingProvider(url + '/v1', 'local').embed(['text']) == [[1.0, 2.0]]
        assert {'/api/tags', '/api/chat', '/api/show', '/api/embed', '/v1/embeddings'} <= set(calls)
    finally:
        if embeddings and getattr(embeddings, '_http', None):
            embeddings._http.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
