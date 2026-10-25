"""The benchmark must preserve evidence types and other running models."""
from types import SimpleNamespace

import httpx
import pytest

from scripts.benchmark_colibri import CASES, check_resources, score_answer


def test_benchmark_targets_and_scoring_are_independent_of_model_output():
    expected = dict((name, target) for name, _, _, target in CASES)
    assert expected['arithmetic']['sum'] == sum(set(range(3, 200, 3)) | set(range(5, 200, 5)))
    assert expected['valuation']['equity_scenario'] == 100000 * 3 - 20000 + 5000
    assert score_answer('{"verified":false}', {'verified': False})[1]
    assert not score_answer('{"verified":0}', {'verified': False})[1]
    assert not score_answer('{"verified":"false"}', {'verified': False})[1]
    assert not score_answer('{"verified":false}', {'verified': False}, truncated=True)[1]
    assert not score_answer('I checked it.', {'verified': False})[1]


def test_preflight_leaves_loaded_ollama_models_alone(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request('GET', url), json={'models': [{'size': 20 * 1024**3}]})
    monkeypatch.setattr('scripts.benchmark_colibri.httpx.get', get)
    with pytest.raises(RuntimeError, match='large Ollama model'):
        check_resources('colibri', False)
    assert calls == ['http://127.0.0.1:11434/api/ps']


def test_preflight_refuses_to_borrow_existing_hayat_server(monkeypatch):
    monkeypatch.setattr('scripts.benchmark_colibri.httpx.get', lambda url, **_: httpx.Response(
        200, request=httpx.Request('GET', url), json={'models': []}))
    with pytest.raises(RuntimeError, match='already running'):
        check_resources('ollama', False)


def test_preflight_checks_available_memory_and_does_not_treat_timeouts_as_absence(monkeypatch):
    def absent(*args, **kwargs):
        raise httpx.ConnectError('No local server')
    monkeypatch.setattr('scripts.benchmark_colibri.httpx.get', absent)
    monkeypatch.setattr('scripts.benchmark_colibri.psutil.virtual_memory',
                        lambda: SimpleNamespace(available=20 * 1024**3))
    check_resources('colibri', False)
    with pytest.raises(RuntimeError, match='23 GiB'):
        check_resources('colibri', True)
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('Busy server')
    monkeypatch.setattr('scripts.benchmark_colibri.httpx.get', timeout)
    with pytest.raises(httpx.ReadTimeout):
        check_resources('colibri', False)
