"""Real chat/registry image workflows use synthetic files and offline transports."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.assistant import AssistantService
from app.tools.image_sources import image_source_request
from app.tools.image_tools import image_tool_controls, local_image_request
from tests.test_image_source_pages import PageFetcher, picture_bytes
from tests.test_networking_actions import runtime as runtime


SOURCE_PAGE = 'https://publisher.example/research-source-sentinel'
SOURCE_IMAGE = 'https://publisher.example/published.png'
COMMONS_PAGE = 'https://commons.wikimedia.org/wiki/File:Synthetic_example.png'
COMMONS_IMAGE = 'https://upload.wikimedia.org/synthetic-example.png'


@pytest.fixture
def image_runtime(runtime, tmp_path, monkeypatch):
    """No real sensor, face model, web provider, or owner image may be consulted."""
    runtime.authority.set_privacy_mode(False)
    runtime.session.last_camera_observation = {'at': 'synthetic-old-camera-observation'}
    first = tmp_path / 'private-image-sentinel.png'
    first.write_bytes(picture_bytes())
    second = tmp_path / 'private-second-sentinel.png'
    second.write_bytes(first.read_bytes())
    calls = []
    execute = runtime.registry.execute

    def tracked(name, arguments):
        calls.append((name, dict(arguments)))
        return execute(name, arguments)

    def forbidden(*_args, **_kwargs):
        pytest.fail('Image-file tooling must not use a sensor, identity model, or live web transport')

    monkeypatch.setattr(runtime.registry, 'execute', tracked)
    for name in runtime.registry.names():
        if any(token in name.lower() for token in ('camera', 'face', 'person', 'people')):
            monkeypatch.setattr(runtime.registry.get(name), 'handler', forbidden)
    for name in ('research', 'research_person', 'research_onion', 'research_tor_web'):
        monkeypatch.setattr(runtime.research, name, forbidden)
    monkeypatch.setattr(runtime.research.fetcher, 'fetch', forbidden)
    monkeypatch.setattr(runtime.research.fetcher, 'get_json', forbidden)
    return SimpleNamespace(runtime=runtime, first=first, second=second, calls=calls)


def source_transport(env, monkeypatch):
    fetcher = PageFetcher('<img src="/published.png">', {SOURCE_IMAGE: env.first.read_bytes()})
    monkeypatch.setattr(env.runtime.research.fetcher, 'fetch', fetcher.fetch)
    monkeypatch.setattr(env.runtime.research.fetcher, 'validate_url', fetcher.validate_url)
    return fetcher


def assert_single_tool(reply, env, tool):
    assert reply.complete, reply.display_response
    assert reply.outcome is not None
    assert [event['tool'] for event in reply.outcome.history] == [tool]
    assert [name for name, _args in env.calls] == [tool]


def test_inspect_chat_delivers_real_local_hash_and_dimensions_without_any_sensor(image_runtime):
    env = image_runtime
    reply = env.runtime.assistant.handle(f'Inspect local image "{env.first}"')
    assert_single_tool(reply, env, 'local_image_inspect')
    output = reply.outcome.history[0]['result']['output']
    assert output['local_image_inspection'] is True
    assert output['width'] == 24 and output['height'] == 16
    assert output['file_sha256'] == hashlib.sha256(env.first.read_bytes()).hexdigest()
    assert output['local_only'] is True and output['identity_inference'] is False
    assert output['file_sha256'] in reply.display_response
    assert 'SHA-256' in reply.display_response


def test_compare_chat_delivers_real_byte_pixel_and_hamming_results(image_runtime):
    env = image_runtime
    reply = env.runtime.assistant.handle(f'Compare local images "{env.first}" and "{env.second}"')
    assert_single_tool(reply, env, 'local_image_compare')
    output = reply.outcome.history[0]['result']['output']
    assert output['local_image_comparison'] is True
    assert output['byte_equal'] is True and output['pixel_equal'] is True
    assert output['phash_distance'] == 0 and output['dhash_distance'] == 0
    assert output['identity_inference'] is False
    assert 'Identical file bytes: True' in reply.display_response
    assert 'distance: 0/64' in reply.display_response


def test_toolkit_status_chat_uses_real_installed_package_inventory(image_runtime):
    env = image_runtime
    reply = env.runtime.assistant.handle('Check local image toolkit')
    assert_single_tool(reply, env, 'local_image_toolkit_status')
    output = reply.outcome.history[0]['result']['output']
    assert output['local_image_toolkit'] is True
    assert {'pillow', 'imagehash', 'exifread', 'pywavelets'} <= {row['name'].lower() for row in output['packages']}
    assert all(row['version'] for row in output['packages'] if row['available'])
    assert 'Local image tools:' in reply.display_response


def test_plain_quoted_image_web_search_uses_hash_lookup_not_a_literal_path_query(image_runtime, monkeypatch):
    env = image_runtime
    listing_calls = []
    fetcher = PageFetcher(env.first.read_bytes())

    def listing(url):
        listing_calls.append(url)
        parsed = urlsplit(url)
        assert parsed.hostname == 'commons.wikimedia.org'
        assert parse_qs(parsed.query)['aisha1'] == [hashlib.sha1(env.first.read_bytes()).hexdigest()]
        assert env.first.name not in url and str(env.first) not in url
        return {'query': {'allimages': [{'descriptionurl': COMMONS_PAGE, 'url': COMMONS_IMAGE}]}}

    def bounded_fetch(url, **options):
        if urlsplit(url).hostname == 'commons.wikimedia.org':
            assert options['max_bytes'] <= 1024 * 1024
            return SimpleNamespace(content=json.dumps(listing(url)).encode(), final_url=url, retrieved_at='2026-10-24T12:00:00Z')
        return fetcher.fetch(url, **options)
    monkeypatch.setattr(env.runtime.research.fetcher, 'fetch', bounded_fetch)
    reply = env.runtime.assistant.handle(f'search "{env.first}" on the web')
    assert_single_tool(reply, env, 'image_source_search')
    output = reply.outcome.history[0]['result']['output']
    assert output['image_source_result'] is True and output['identity_inference'] is False
    assert output['matches'][0]['match'] == 'verified_exact_bytes'
    assert len(listing_calls) == 1 and [url for url, _ in fetcher.calls] == [COMMONS_IMAGE]
    assert COMMONS_PAGE in reply.display_response and '2026-10-24T12:00:00Z' in reply.display_response
    assert 'no face identification' in reply.display_response.lower()


@pytest.mark.parametrize('quote', ['"', "'"])
def test_explicit_source_page_chat_checks_real_html_and_image_bytes_with_citations(image_runtime, monkeypatch, quote):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    text = f'Check image copies {quote}{env.first}{quote} on {quote}{SOURCE_PAGE}{quote}'
    reply = env.runtime.assistant.handle(text)
    assert_single_tool(reply, env, 'image_source_check_page')
    output = reply.outcome.history[0]['result']['output']
    assert output['page']['url'] == SOURCE_PAGE
    assert output['matches'][0]['match'] == 'verified_exact_bytes'
    assert [url for url, _ in fetcher.calls] == [SOURCE_PAGE, SOURCE_IMAGE]
    assert SOURCE_PAGE in reply.display_response and SOURCE_IMAGE in reply.display_response
    assert '2026-10-24T12:00:00Z' in reply.display_response


@pytest.mark.parametrize('tool,args', [
    ('image_source_search', {}),
    ('image_source_check_page', {'url': SOURCE_PAGE}),
])
def test_nested_filesystem_denial_precedes_provider_requests(image_runtime, monkeypatch, tool, args):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    monkeypatch.setitem(env.runtime.registry.permissions.policies, 'public_web_read', 'ALLOW')
    monkeypatch.setitem(env.runtime.registry.permissions.policies, 'filesystem_read', 'DENY')
    result = env.runtime.registry.execute(tool, {'path': str(env.first), **args})
    assert not result.success and 'not authorized' in result.error.lower()
    assert not fetcher.calls


@pytest.mark.parametrize('tool,args', [
    ('image_source_search', {}),
    ('image_source_check_page', {'url': SOURCE_PAGE}),
])
def test_privacy_denies_image_source_network_even_if_read_categories_are_allowed(image_runtime, monkeypatch, tool, args):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    monkeypatch.setitem(env.runtime.registry.permissions.policies, 'public_web_read', 'ALLOW')
    monkeypatch.setitem(env.runtime.registry.permissions.policies, 'filesystem_read', 'ALLOW')
    env.runtime.authority.set_privacy_mode(True)
    try:
        result = env.runtime.registry.execute(tool, {'path': str(env.first), **args})
        assert not result.success and 'privac' in result.error.lower()
        assert not fetcher.calls
    finally:
        env.runtime.authority.set_privacy_mode(False)


@pytest.mark.parametrize('command,tool', [
    ('Inspect local image "{first}"', 'local_image_inspect'),
    ('Compare local images "{first}" and "{second}"', 'local_image_compare'),
    ('Check image copies "{first}" on ' + SOURCE_PAGE, 'image_source_check_page'),
])
def test_private_image_task_diagnostics_omit_paths_hashes_and_source_contents(image_runtime, monkeypatch, command, tool):
    env = image_runtime
    source_transport(env, monkeypatch)
    reply = env.runtime.assistant.handle(command.format(first=env.first, second=env.second))
    assert_single_tool(reply, env, tool)
    env.runtime.events.flush()
    durable = json.dumps([row for row in env.runtime.events.recent(500) if row['event_type'] in {
        'TASK_STARTED', 'TASK_CREATED', 'TASK_PROGRESS', 'TASK_COMPLETED', 'TOOL_STARTED', 'TOOL_COMPLETED'}])
    assert env.first.name not in durable and env.second.name not in durable
    assert hashlib.sha256(env.first.read_bytes()).hexdigest() not in durable
    assert SOURCE_PAGE not in durable and SOURCE_IMAGE not in durable


@pytest.mark.parametrize('tool', ['local_image_inspect', 'local_image_compare', 'image_source_search', 'image_source_check_page'])
def test_private_image_outcomes_never_become_apprenticeship_correction_or_situation_training(tool):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Private image data must not enter learning')
    service = SimpleNamespace(runtime=SimpleNamespace(
        apprenticeship=SimpleNamespace(observe=forbidden), session=SimpleNamespace(last_correction='private image correction'),
        situation=SimpleNamespace(observe_turn=forbidden)))
    outcome = SimpleNamespace(plan=[{'tool': tool}], history=[{'tool': tool}])
    intent = SimpleNamespace(entities={'modality': 'image_sources' if tool.startswith('image_source') else 'local_image_tools'})
    AssistantService._observe_for_learning(service, outcome)
    AssistantService._learn_from_repair(service, intent, outcome)
    assert service.runtime.session.last_correction == ''
    AssistantService._observe_situation(service, 'private image request', SimpleNamespace(intent=intent, outcome=outcome))


def test_image_request_parsers_keep_explicit_file_references_and_never_claim_identity():
    path = 'D:/AI/synthetic.png'
    assert local_image_request(f'Inspect local image "{path}"') == ('local_image_inspect', {'path': path})
    assert image_source_request(f'search "{path}" on the web') == ('image_source_search', {'path': path})
    assert image_source_request(f"check image copies '{path}' on '{SOURCE_PAGE}'") == (
        'image_source_check_page', {'path': path, 'url': SOURCE_PAGE})
    assert image_source_request('Find image copies online') == ('needs_image', {})
    assert image_source_request(f'Identify who is in image "{path}" on the web') is None


def test_missing_selected_image_asks_for_file_without_opening_camera_or_searching(image_runtime):
    env = image_runtime
    reply = env.runtime.assistant.handle('Find image copies online')
    assert reply.intent.needs_clarification is True
    assert 'image' in reply.display_response.lower() and 'file' in reply.display_response.lower()
    assert not env.calls


@pytest.mark.parametrize('tool,args', [
    ('image_source_search', {}),
    ('image_source_check_page', {'url': SOURCE_PAGE}),
])
def test_cancelled_image_control_stops_before_source_network(image_runtime, monkeypatch, tool, args):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    with image_tool_controls(check=lambda: False):
        result = env.runtime.registry.execute(tool, {'path': str(env.first), **args})
    assert not result.success and 'cancel' in result.error.lower()
    assert not fetcher.calls


@pytest.mark.parametrize('stop_after', [1, 2])
def test_emergency_stop_during_retrieval_prevents_stale_image_success(image_runtime, monkeypatch, stop_after):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    fetch = fetcher.fetch

    def stop_after_page(*args, **kwargs):
        result = fetch(*args, **kwargs)
        if len(fetcher.calls) == stop_after:
            env.runtime.registry.interlock.halt()
        return result

    monkeypatch.setattr(env.runtime.research.fetcher, 'fetch', stop_after_page)
    try:
        result = env.runtime.registry.execute('image_source_check_page', {'path': str(env.first), 'url': SOURCE_PAGE})
        assert not result.success and 'stop' in result.error.lower()
        assert result.output is None
        assert [url for url, _ in fetcher.calls] == [SOURCE_PAGE, SOURCE_IMAGE][:stop_after]
    finally:
        env.runtime.registry.interlock.resume()


@pytest.mark.parametrize('tool,args', [
    ('image_source_search', {}),
    ('image_source_check_page', {'url': SOURCE_PAGE}),
])
def test_latched_emergency_stop_prevents_any_source_fetch(image_runtime, monkeypatch, tool, args):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    env.runtime.registry.interlock.halt()
    try:
        result = env.runtime.registry.execute(tool, {'path': str(env.first), **args})
        assert not result.success and 'stop' in result.error.lower()
        assert not fetcher.calls and result.output is None
    finally:
        env.runtime.registry.interlock.resume()


@pytest.mark.parametrize('enable_after', [1, 2])
def test_privacy_enabled_during_page_or_image_fetch_discards_stale_match(image_runtime, monkeypatch, enable_after):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    fetch = fetcher.fetch

    def enable_privacy_after_fetch(*args, **kwargs):
        result = fetch(*args, **kwargs)
        if len(fetcher.calls) == enable_after:
            env.runtime.authority.set_privacy_mode(True)
        return result

    monkeypatch.setattr(env.runtime.research.fetcher, 'fetch', enable_privacy_after_fetch)
    try:
        result = env.runtime.registry.execute('image_source_check_page', {'path': str(env.first), 'url': SOURCE_PAGE})
        assert not result.success and 'privacy' in result.error.lower()
        assert result.output is None
        assert [url for url, _ in fetcher.calls] == [SOURCE_PAGE, SOURCE_IMAGE][:enable_after]
    finally:
        env.runtime.authority.set_privacy_mode(False)


def test_source_page_failure_reports_unknown_without_fabricating_copy_or_exposing_exception(image_runtime, monkeypatch):
    env = image_runtime
    fetcher = source_transport(env, monkeypatch)
    fetcher.html = TimeoutError('private-provider-exception-sentinel')
    reply = env.runtime.assistant.handle(f'Check image copies "{env.first}" on {SOURCE_PAGE}')
    assert_single_tool(reply, env, 'image_source_check_page')
    output = reply.outcome.history[0]['result']['output']
    assert output['outcome'] == 'failed' and output['matches'] == [] and output['page'] is None
    assert 'No verified copy' in reply.display_response
    assert 'TimeoutError' in reply.display_response and 'private-provider-exception-sentinel' not in reply.display_response
    assert len(fetcher.calls) == 1
