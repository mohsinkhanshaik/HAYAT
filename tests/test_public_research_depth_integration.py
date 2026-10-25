"""Public research options reach actual tools without face search or private records."""
from types import SimpleNamespace

import pytest

from app.research.contact_actions import request
from app.tools.image_sources import image_source_request
from tests.test_networking_actions import runtime as runtime


@pytest.mark.parametrize('phrase', [
    'Deep search professional contacts for Ada Example at Example Institute through Tor',
    'Find contacts for Ada Example at Example Institute in depth via Tor',
    'Please deeply search public work contact details for Ada Example at Example Institute over Tor thoroughly',
])
def test_deep_public_contact_requests_parse_explicit_budget_and_route(phrase):
    parsed = request(phrase)
    assert parsed == {'name': 'Ada Example', 'organization': 'Example Institute', 'official_url': '',
                      'max_pages': 12, 'depth': 'deep', 'tor_only': True}


@pytest.mark.parametrize('phrase', [
    'Find private contacts for Ada Example at Example Institute',
    'Find phone numbers for Ada Example at Example Institute',
    'Find leaked contacts for Ada Example at Example Institute',
])
def test_private_or_leaked_contact_requests_do_not_use_professional_adapter(phrase):
    assert request(phrase) is None


def test_actual_assistant_routes_deep_public_options_without_camera_or_identity(runtime, monkeypatch):
    calls = []
    def discover(_manager, name, organization, official_url='', **options):
        options['check']()
        calls.append((name, organization, options))
        return {'name': name, 'organization': organization, 'candidates': [], 'team_routes': [], 'sources': [],
                'errors': [], 'limits': ['Bounded public sources; absence elsewhere unknown.'], 'completed': True}
    monkeypatch.setattr('app.research.public_contacts.discover_contacts', discover)
    reply = runtime.assistant.handle('Deep search professional contacts for Ada Example at Example Institute through Tor')
    assert reply.complete and calls
    assert calls[0][0:2] == ('Ada Example', 'Example Institute')
    assert {key: calls[0][2][key] for key in ('max_pages', 'depth', 'tor_only')} == {
        'max_pages': 12, 'depth': 'deep', 'tor_only': True}
    assert [step['tool'] for step in reply.outcome.history] == ['public_contact_search']


@pytest.mark.parametrize('command,tool', [
    ('Find image copies on the web for "C:/photos/chosen.png" through Tor', 'image_source_search'),
    ('Check image copies "C:/photos/chosen.png" on https://publisher.example/page via Tor', 'image_source_check_page'),
])
def test_image_source_tor_requests_keep_file_local_and_select_required_route(command, tool):
    name, args = image_source_request(command)
    assert name == tool and args['tor_only'] is True
    assert args['path'] == 'C:/photos/chosen.png'


@pytest.mark.parametrize('proxy', [None, 'https://proxy.example:443', 'socks5://remote.example:9050'])
@pytest.mark.parametrize('tool', ['image_source_search', 'image_source_check_page'])
def test_required_tor_refuses_missing_or_nonlocal_route_before_image_or_network(runtime, monkeypatch, proxy, tool):
    from app.privacy_masks import ROUTE
    old = ROUTE.proxy
    ROUTE.set(proxy)
    try:
        monkeypatch.setattr('app.research.image_sources.exact_sources', lambda *a, **k: pytest.fail('source access without Tor'))
        monkeypatch.setattr('app.research.image_sources.check_source_page', lambda *a, **k: pytest.fail('source access without Tor'))
        args = {'path': 'missing-chosen.png', 'tor_only': True}
        if tool == 'image_source_check_page':
            args['url'] = 'https://publisher.example/page'
        result = runtime.registry.execute(tool, args)
        assert not result.success and 'Tor' in result.error and result.output is None
    finally:
        ROUTE.set(old)


def test_required_image_tor_fetcher_does_not_use_direct_fetcher(runtime, tmp_path, monkeypatch):
    from app.privacy_masks import ROUTE
    from app.tools.image_sources import register_image_source_tools
    from app.tools.registry import ToolRegistry
    from app.safety.permissions import PermissionManager
    from tests.test_image_source_pages import picture_bytes
    image = tmp_path / 'chosen.png'
    image.write_bytes(picture_bytes())
    calls = []
    direct = SimpleNamespace(get_json=lambda *a, **k: pytest.fail('direct fallback'))
    manager = SimpleNamespace(fetcher=direct, settings={})
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_image_source_tools(registry, manager)
    def fetch(self, url, **options):
        calls.append(url)
        assert options['require_tor'] is True
        return SimpleNamespace(content=b'{"query":{"allimages":[]}}', final_url=url, retrieved_at='2026-10-24T12:00:00Z')
    monkeypatch.setattr('app.research.onion.TorRequiredFetcher.fetch', fetch)
    old = ROUTE.proxy
    ROUTE.set('socks5://127.0.0.1:19999')
    try:
        result = registry.execute('image_source_search', {'path': str(image), 'tor_only': True})
        assert result.success, result.error
        assert calls and result.output['tor_required'] is True
        assert result.output['matches'] == [] and result.output['identity_inference'] is False
    finally:
        ROUTE.set(old)
