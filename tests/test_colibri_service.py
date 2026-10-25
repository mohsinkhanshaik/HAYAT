from types import SimpleNamespace

import pytest

from app.models.colibri_service import ColibriService


def test_manual_colibri_control_rejects_missing_installation_without_launch(tmp_path, monkeypatch):
    service = ColibriService(tmp_path)
    monkeypatch.setattr('app.models.colibri_service.subprocess.Popen', lambda *_a, **_k: pytest.fail('must not launch'))
    with pytest.raises(FileNotFoundError, match='incomplete'):
        service.start()
    with pytest.raises(ValueError):
        service.start('remote')
    assert 'No GUI-owned' in service.stop()['detail']


def test_colibri_status_requires_advertised_model_and_bypasses_environment_proxy(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'data': [{'id': ColibriService.MODEL}]})
    monkeypatch.setattr('app.models.colibri_service.httpx.get', get)
    status = ColibriService().status()
    assert status['ready'] and not status['owned']
    assert calls[0][0] == 'http://127.0.0.1:8000/v1/models'
    assert calls[0][1]['trust_env'] is False


def test_colibri_test_requires_ready_local_model(monkeypatch):
    service = ColibriService()
    monkeypatch.setattr(service, 'status', lambda: {'ready': False})
    with pytest.raises(RuntimeError, match='not ready'):
        service.test('a real prompt')
    with pytest.raises(ValueError):
        service.test('')
