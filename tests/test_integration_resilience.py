"""Failure/recovery checks for shared open-source integration transports."""
from __future__ import annotations

import gzip
import json
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import anyio
import httpx
import pytest

from app.integrations.browser_bridge import BrowserExtensionBridge
from app.integrations.connectors import ConnectorGateway, ConnectorManifest, ConnectorRegistry
from app.integrations.mcp_catalog import MCPCatalogError, MCPToolError, checked_result, discover_tools, safe_catalog, search_tools
from app.integrations.mcp_gateway import MCPGateway, MCPServerManifest
from tests.test_integrations_foundation import broker


class Tool:
    def __init__(self, name, description=''):
        self.item = {'name': name, 'description': description, 'inputSchema': {'type': 'object'}}

    def model_dump(self, **_):
        return self.item


class Pages:
    def __init__(self, pages):
        self.pages, self.cursors = iter(pages), []

    async def list_tools(self, *, params=None):
        self.cursors.append(params.cursor if params else None)
        tools, cursor = next(self.pages)
        return SimpleNamespace(tools=tools, next_cursor=cursor)


def test_discovery_reads_all_pages_and_deduplicates_identical_tools():
    session = Pages([([Tool('first')], 'p2'), ([Tool('first'), Tool('second')], None)])
    assert [row['name'] for row in anyio.run(discover_tools, session)] == ['first', 'second']
    assert session.cursors == [None, 'p2']


@pytest.mark.parametrize('pages,limits,match', [
    ([([Tool('a')], 'repeat'), ([Tool('b')], 'repeat')], {}, 'cursor'),
    ([([Tool('a')], 'next'), ([Tool('a', 'changed')], None)], {}, 'conflicting'),
    ([([Tool('a')], 'next')], {'max_pages': 1}, 'page limit'),
    ([([Tool('a'), Tool('b')], None)], {'max_tools': 1}, 'limit'),
    ([([Tool('a')], None)], {'max_bytes': 1}, 'limit'),
])
def test_incomplete_or_unbounded_catalog_is_not_reported_as_complete(pages, limits, match):
    async def run():
        return await discover_tools(Pages(pages), **limits)
    with pytest.raises(MCPCatalogError, match=match):
        anyio.run(run)


def test_tool_search_prefers_names_and_keeps_input_schemas():
    rows = [Tool('misc', 'Search artifacts').item, Tool('artifact_search').item, Tool('unrelated').item]
    found = search_tools(rows, 'artifact', 1)
    assert found[0]['name'] == 'artifact_search' and found[0]['inputSchema'] == {'type': 'object'}
    assert search_tools(rows, 'absent') == []
    for limit in (0, 2001, True):
        with pytest.raises(ValueError):
            search_tools(rows, limit=limit)


def test_catalog_redaction_preserves_secret_named_argument_schema():
    secret = 'provider-scoped-credential'
    schema = {'type': 'object', 'properties': {'password': {'type': 'string'}, 'api_token': {'type': 'string'}},
              'required': ['password']}
    catalog = [{'name': 'login', 'inputSchema': schema, 'description': 'Provider echoed ' + secret}]
    cleaned = safe_catalog(catalog, [secret])
    assert cleaned[0]['inputSchema'] == schema and secret not in cleaned[0]['description']


@pytest.mark.parametrize('flag', ['isError', 'is_error'])
def test_server_tool_errors_are_failures_and_scoped_credentials_are_redacted(flag):
    secret = 'unusual-credential-value'
    with pytest.raises(MCPToolError) as error:
        checked_result({flag: True, 'content': [{'type': 'text', 'text': 'failed using ' + secret}]},
                       extra_secrets=[secret])
    assert secret not in str(error.value) and 'REDACTED' in str(error.value)
    assert checked_result({'content': [], 'structured_content': {'ok': 1}})['structured_content'] == {'ok': 1}


def test_real_stdio_catalog_paginates_searches_and_surfaces_tool_errors(tmp_path):
    script = tmp_path / 'server.py'
    script.write_text('''import json, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method, params = request['method'], request.get('params', {})
    if method == 'initialize':
        result = {'protocolVersion': params['protocolVersion'], 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'pagination-test', 'version': '1'}}
    elif method == 'tools/list':
        name = 'page_two' if params.get('cursor') else 'page_one'
        result = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}}]}
        if not params.get('cursor'):
            result['nextCursor'] = 'second'
    elif method == 'tools/call':
        result = {'isError': True, 'content': [{'type': 'text', 'text': 'analysis unavailable'}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
''', encoding='utf-8')
    gateway = MCPGateway(tmp_path / 'mcp.db')
    gateway.register(MCPServerManifest('paging-test', 'Paging test', sys.executable, [str(script)], ['failure']), enable=True)
    assert [row['name'] for row in gateway.tools('paging-test')] == ['page_one', 'page_two']
    assert [row['name'] for row in gateway.tools('paging-test', 'two', 1)] == ['page_two']
    with pytest.raises(MCPToolError, match='analysis unavailable'):
        gateway.call('paging-test', 'failure')
    with pytest.raises(PermissionError):
        gateway.call('paging-test', 'not_declared_read_only')


@pytest.fixture
def connector(tmp_path, monkeypatch):
    monkeypatch.setattr('app.integrations.connectors.validate_public_url', lambda url: url)
    registry = ConnectorRegistry(tmp_path / 'connectors.db')
    registry.install(ConnectorManifest('sample', 'Sample', 'http', 'https://example.org', read_actions=['items']), enable=True)
    return registry


def response(status=200, **kwargs):
    return httpx.Response(status, request=httpx.Request('GET', 'https://example.org/items'), **kwargs)


@pytest.mark.parametrize('first', [429, 503, 'timeout', 'network'])
def test_reads_retry_transient_failure_and_return_attempt_count(connector, first):
    calls, waits = [], []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            if first == 'timeout':
                raise httpx.ReadTimeout('busy')
            if first == 'network':
                raise httpx.ConnectError('offline')
            return response(first, headers={'retry-after': '2'})
        return response(json={'items': [1]})
    result = ConnectorGateway(connector, request=request, sleep=waits.append).call('sample', 'items')
    assert result['output'] == {'items': [1]} and result['attempts'] == 2
    assert len(calls) == 2 and waits == ([2.0] if isinstance(first, int) else [1.0])
    assert not calls[0][1]['follow_redirects']


@pytest.mark.parametrize('write,status', [(True, 503), (False, 401), (False, 429)])
def test_writes_auth_failures_and_long_publisher_cooldowns_are_not_replayed(connector, write, status):
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return response(status, headers={'retry-after': '3600'})
    with pytest.raises(httpx.HTTPStatusError):
        ConnectorGateway(connector, request=request, sleep=lambda _: pytest.fail('must not retry')).call(
            'sample', 'items', write=write)
    assert len(calls) == 1


def test_retries_are_bounded_and_streams_are_closed(connector):
    responses = []
    def request(*args, **kwargs):
        item = response(503)
        responses.append(item)
        return item
    with pytest.raises(httpx.HTTPStatusError):
        ConnectorGateway(connector, request=request, sleep=lambda _: None).call('sample', 'items')
    assert len(responses) == 3 and all(item.is_closed for item in responses)


def test_default_transport_decompresses_once_and_enforces_decoded_size(connector, monkeypatch):
    real_stream = httpx.stream
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-encoding': 'gzip'},
                                                                  content=gzip.compress(b'{"items": [2]}')))
    client = httpx.Client(transport=transport)
    monkeypatch.setattr('app.integrations.connectors.httpx.stream', client.stream)
    try:
        gateway = ConnectorGateway(connector)
        assert gateway.call('sample', 'items')['output'] == {'items': [2]}
        gateway.MAX_RESPONSE_BYTES = 2
        with pytest.raises(ValueError, match='pagination'):
            gateway.call('sample', 'items')
    finally:
        client.close()
        monkeypatch.setattr('app.integrations.connectors.httpx.stream', real_stream)


def test_connector_redacts_the_scoped_token_if_a_provider_echoes_it(connector, tmp_path):
    secret = 'nonstandard-provider-credential'
    secrets = broker(tmp_path)
    secrets.store('sample-key', secret, scopes=['connector:sample'])
    manifest = connector.get('sample')
    manifest.secret_name = 'sample-key'
    connector.install(manifest, enable=True)
    result = ConnectorGateway(connector, secrets, request=lambda *a, **k: response(json={'message': secret})).call('sample', 'items')
    assert secret not in json.dumps(result)


def test_trickling_download_obeys_total_deadline_without_waiting_for_a_full_chunk(connector, monkeypatch):
    now = [0.0]
    closed = threading.Event()
    class Trickling(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                now[0] += 20
                yield b'x'

        def close(self):
            closed.set()
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Trickling())))
    monkeypatch.setattr('app.integrations.connectors.httpx.stream', client.stream)
    try:
        with pytest.raises(httpx.ReadTimeout, match='deadline'):
            ConnectorGateway(connector, clock=lambda: now[0], sleep=lambda _: None).call('sample', 'items')
        assert closed.is_set() and now[0] == 60  # it never waited to buffer 64 KiB
    finally:
        client.close()


def test_write_timeouts_are_never_automatically_retried(connector):
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        raise httpx.ReadTimeout('write may already have completed')
    with pytest.raises(httpx.ReadTimeout):
        ConnectorGateway(connector, request=request).call('sample', 'items', write=True)
    assert len(calls) == 1


def test_mcp_oversized_output_is_reported_as_a_failed_event(tmp_path, monkeypatch):
    events = []
    gateway = MCPGateway(tmp_path / 'mcp.db', event_bus=SimpleNamespace(publish=lambda *args: events.append(args)))
    gateway.register(MCPServerManifest('large-test', 'Large test', sys.executable, read_only_tools=['read']), enable=True)
    async def oversized(*args):
        return {'content': [{'type': 'text', 'text': 'x' * (8 * 1024 * 1024)}]}
    monkeypatch.setattr(gateway, '_call_async', oversized)
    with pytest.raises(MCPToolError, match='limit'):
        gateway.call('large-test', 'read')
    assert events[-1][2]['success'] is False


def test_mcp_reported_failure_reaches_the_tool_registry_as_a_failed_call(tmp_path, monkeypatch):
    from app.integrations.mcp_gateway import register_mcp_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    gateway = MCPGateway(tmp_path / 'mcp.db')
    gateway.register(MCPServerManifest('error-test', 'Error test', sys.executable, read_only_tools=['read']), enable=True)
    async def failure(*args):
        return {'is_error': True, 'content': [{'type': 'text', 'text': 'requested analysis failed'}]}
    monkeypatch.setattr(gateway, '_call_async', failure)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    register_mcp_tools(registry, gateway)
    result = registry.execute('mcp_read_tool', {'server_id': 'error-test', 'tool': 'read'})
    assert not result.success and 'requested analysis failed' in result.error


def test_bridge_duplicate_and_old_connection_responses_never_block_or_replace_results(tmp_path):
    bridge = BrowserExtensionBridge(broker(tmp_path))
    owner, other, waiting = object(), object(), queue.Queue(maxsize=1)
    bridge._pending['command'] = owner, waiting
    bridge._deliver_response(other, {'id': 'command', 'result': {'wrong': True}})
    assert waiting.empty()
    bridge._deliver_response(owner, {'id': 'command', 'result': {'first': True}})
    bridge._deliver_response(owner, {'id': 'command', 'result': {'duplicate': True}})
    assert waiting.get_nowait()['result'] == {'first': True}


def test_real_bridge_disconnect_wakes_waiting_request_without_command_timeout(tmp_path):
    from websockets.sync.client import connect
    bridge = BrowserExtensionBridge(broker(tmp_path), port=0)
    bridge.start()
    port = bridge._server.socket.getsockname()[1]
    try:
        with connect(f'ws://127.0.0.1:{port}') as socket, ThreadPoolExecutor(1) as pool:
            socket.send(json.dumps({'type': 'authenticate', 'token': bridge.pairing_token()}))
            assert json.loads(socket.recv())['type'] == 'authenticated'
            pending = pool.submit(bridge.command, 'snapshot', timeout=20)
            assert json.loads(socket.recv())['command'] == 'snapshot'
            socket.close()
            with pytest.raises(RuntimeError, match='disconnected'):
                pending.result(timeout=2)
            assert not bridge._pending
    finally:
        bridge.stop()


def test_stop_wakes_in_flight_request_and_releases_pending_state(tmp_path):
    bridge = BrowserExtensionBridge(broker(tmp_path))
    sent = threading.Event()
    socket = SimpleNamespace(send=lambda _: sent.set(), close=lambda **_: None)
    bridge._socket = socket
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(bridge.command, 'scroll', timeout=20)
        assert sent.wait(1)
        bridge.stop()
        with pytest.raises(RuntimeError, match='stopped'):
            pending.result(timeout=1)
    assert not bridge._pending and bridge._socket is None


def test_replacing_real_extension_connection_fails_old_request_and_new_connection_works(tmp_path):
    from websockets.sync.client import connect
    bridge = BrowserExtensionBridge(broker(tmp_path), port=0)
    bridge.start()
    port = bridge._server.socket.getsockname()[1]
    url = f'ws://127.0.0.1:{port}'
    def authenticate(socket):
        socket.send(json.dumps({'type': 'authenticate', 'token': bridge.pairing_token()}))
        assert json.loads(socket.recv())['type'] == 'authenticated'
    try:
        with connect(url) as previous, ThreadPoolExecutor(1) as pool:
            authenticate(previous)
            old_request = pool.submit(bridge.command, 'snapshot')
            assert json.loads(previous.recv())['command'] == 'snapshot'
            with connect(url) as current:
                authenticate(current)
                with pytest.raises(RuntimeError, match='replaced'):
                    old_request.result(timeout=2)
                new_request = pool.submit(bridge.command, 'scroll')
                command = json.loads(current.recv())
                current.send(json.dumps({'type': 'response', 'id': command['id'], 'ok': True, 'result': {'scrollY': 700}}))
                assert new_request.result(timeout=2) == {'scrollY': 700}
    finally:
        bridge.stop()
