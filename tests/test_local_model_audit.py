"""Inventory is read-only by default, isolates failures and preserves reports."""
import json

import pytest

from scripts import audit_local_models as module


def tag(name='hayat:latest', digest='a' * 64, **extra):
    return {'name': name, 'digest': digest, 'size': 1234, **extra}


def test_inventory_does_not_generate_and_survives_unavailable_other_service(monkeypatch):
    calls = []

    def request(endpoint, route, payload=None, **kwargs):
        calls.append((endpoint, route))
        if endpoint.endswith(':11434'):
            raise ConnectionError('service is off')
        if route == '/api/tags':
            return {'models': [tag()]}
        assert route == '/api/show'
        return {'details': {'quantization_level': 'Q4_K_M'}, 'capabilities': ['completion']}

    monkeypatch.setattr(module, 'request', request)
    result = module.audit(['http://127.0.0.1:11434', 'http://127.0.0.1:11435'])
    assert result['services'][0]['status'] == 'unavailable'
    assert result['services'][1]['models'][0]['status'] == 'inventoried_only'
    assert result['generation_requested'] is False
    assert all(route in ('/api/tags', '/api/show') for _, route in calls)


def test_cloud_alias_is_rejected_without_show_or_generation(monkeypatch):
    calls = []

    def request(endpoint, route, payload=None, **kwargs):
        calls.append(route)
        return {'models': [tag('hayat:cloud', remote_host='https://cloud.example')]}

    monkeypatch.setattr(module, 'request', request)
    result = module.audit(['http://127.0.0.1:11435'], generate=True)
    assert result['services'][0]['models'][0]['status'] == 'failed'
    assert calls == ['/api/tags']


def test_generation_is_opt_in_and_changed_digest_is_not_success(monkeypatch):
    tags = 0

    def request(endpoint, route, payload=None, **kwargs):
        nonlocal tags
        if route == '/api/tags':
            tags += 1
            return {'models': [tag(digest=('a' if tags == 1 else 'b') * 64)]}
        if route == '/api/show':
            return {'details': {}, 'capabilities': ['completion']}
        assert route == '/api/chat'
        prompt = payload['messages'][0]['content']
        answer = '399' if '19 * 21' in prompt else 'Theo' if 'Theo lives' in prompt else 'UNKNOWN'
        return {'message': {'content': answer}}

    monkeypatch.setattr(module, 'request', request)
    item = module.audit(['http://127.0.0.1:11435'], generate=True)['services'][0]['models'][0]
    assert item['passed'] == 3 and item['digest_unchanged'] is False
    assert item['status'] == 'model_changed_during_check'


def test_output_cannot_overwrite_a_report_or_contact_services(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    path.write_text('original', encoding='utf-8')
    monkeypatch.setattr(module, 'request', lambda *_a, **_k: pytest.fail('Output conflict contacted a service'))
    with pytest.raises(SystemExit) as error:
        module.main(['--output', str(path)])
    assert error.value.code == 2 and path.read_text(encoding='utf-8') == 'original'


def test_cli_writes_explicit_metadata_only_result(tmp_path, monkeypatch):
    path = tmp_path / 'report.json'
    monkeypatch.setattr(module, 'request', lambda endpoint, route, payload=None, **kwargs:
                        {'models': [tag()]} if route == '/api/tags' else {'details': {}})
    assert module.main(['--output', str(path), '--endpoint', 'http://127.0.0.1:11435']) == 0
    result = json.loads(path.read_text(encoding='utf-8'))
    assert result['complete'] and result['generation_requested'] is False
    assert 'no model loaded' in result['scope'].lower()


def test_remote_endpoint_is_not_contacted_even_for_inventory():
    result = module.audit(['https://cloud.example'])
    assert result['services'][0]['status'] == 'unavailable'
    assert 'ZERO-COST' in result['services'][0]['error']


@pytest.mark.parametrize('vector,expected', [([.1, -.2], 'passed'), ([], 'failed'), ([float('nan')], 'failed')])
def test_embedding_capability_uses_embedding_route_and_validates_vector(monkeypatch, vector, expected):
    calls = []

    def request(endpoint, route, payload=None, **kwargs):
        calls.append(route)
        if route == '/api/tags':
            return {'models': [tag('embedding-specialist:latest')]}
        if route == '/api/show':
            return {'details': {}, 'capabilities': ['embedding']}
        assert route == '/api/embed'
        assert payload['keep_alive'] != 0
        return {'embeddings': [vector]}

    monkeypatch.setattr(module, 'request', request)
    item = module.audit(['http://127.0.0.1:11435'], generate=True)['services'][0]['models'][0]
    assert item['status'] == expected
    assert '/api/chat' not in calls
