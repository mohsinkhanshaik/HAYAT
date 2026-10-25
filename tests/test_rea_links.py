import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rea_links import PublicAppLink


class Fetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
    def fetch(self, url, **options):
        self.calls.append((url, options))
        body, kind = self.pages[url]
        assert len(body) <= options['max_bytes']
        return SimpleNamespace(content=body, headers={'content-type': kind}, final_url=url,
                               sha256=hashlib.sha256(body).hexdigest())


def test_public_page_collects_bounded_same_origin_scripts_without_executing_them(tmp_path):
    page = b'''<html><title>Owned fixture</title><script>function inlineFeature(){return 42}</script>
    <script type="application/ld+json">{"not":"code"}</script>
    <script src="/main.js"></script><script src="https://elsewhere.example/skip.js"></script></html>'''
    fetcher = Fetcher({'https://example.com/app': (page, 'text/html'),
                       'https://example.com/main.js': (b'function appFeature(){return 7}', 'application/javascript')})
    called = []
    def analyze(path):
        called.append(path)
        assert not (Path(path) / 'node_modules').exists()
        assert not (Path(path) / 'hayat-link-source.json').exists()
        assert all(p.suffix in {'.js', '.json'} for p in Path(path).iterdir())
        return {'observed': 'static graph'}
    result = PublicAppLink(fetcher, analyze, tmp_path).inspect('https://example.com/app')
    assert len(called) == 1 and len(result['resources']) == 2 and not result['code_executed']
    # Without the optional parser (a fresh clone), an "import discovery not performed" note is listed too.
    assert len(fetcher.calls) == 2 and any('cross-origin' in item['reason'] for item in result['skipped_resources'])
    metadata = json.loads(Path(result['manifest']).read_text())
    assert metadata['analysis_status'] == 'REA static analysis completed'
    assert metadata['resources'][1]['sha256'] == hashlib.sha256(b'function appFeature(){return 7}').hexdigest()


def test_failing_resources_cannot_cause_unbounded_requests(tmp_path):
    page = ('<html>' + ''.join(f'<script src="/{i}.js"></script>' for i in range(100)) + '</html>').encode()
    fetcher = Fetcher({'https://example.com/': (page, 'text/html')})
    result = PublicAppLink(fetcher, lambda _: pytest.fail('no scripts were collected'), tmp_path).inspect('https://example.com/')
    assert len(fetcher.calls) == 9  # page plus eight failed script requests, not all hundred
    assert not result['resources'] and 'No JavaScript was exposed' in result['report']


def test_privacy_revocation_and_private_urls_stop_before_creating_analysis_files(tmp_path):
    allowed = [True]
    class Revoked(Fetcher):
        def fetch(self, url, **options):
            result = super().fetch(url, **options)
            allowed[0] = False
            return result
    fetcher = Revoked({'https://example.com/app.js': (b'const a=1;', 'application/javascript')})
    collector = PublicAppLink(fetcher, lambda _: pytest.fail('privacy was revoked'), tmp_path / 'output', lambda: allowed[0])
    with pytest.raises(PermissionError):
        collector.inspect('https://example.com/app.js')
    assert not (tmp_path / 'output').exists()
    with pytest.raises(ValueError):
        collector.inspect('http://127.0.0.1/private')
    assert len(fetcher.calls) == 1


def test_html_collection_runs_real_rea_static_analysis_when_installed(tmp_path):
    from app.reverse_engineer import ReverseEngineer, available
    if not available():
        pytest.skip('REA is not installed')
    fetcher = Fetcher({'https://example.com/app': (b'<html><title>Fixture</title><script>function calculateOwnedValue(){return 42}</script></html>', 'text/html')})
    engine = ReverseEngineer()
    try:
        collector = PublicAppLink(fetcher, lambda path: engine.read('analyze_javascript_application',
            {'input_path': path, 'format': 'directory'}), tmp_path)
        result = collector.inspect('https://example.com/app')
        assert 'JavaScriptApplicationGraph' in result['report']
        assert 'calculateOwnedValue' in result['report']
        assert result['code_executed'] is False
    finally:
        engine.close()


@pytest.mark.parametrize('matching_parent', [True, False])
def test_transport_limit_uses_only_the_exact_completed_evidence(tmp_path, monkeypatch, matching_parent):
    from app import reverse_engineer
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_reverse_engineer import FakeSession
    evidence_id = 'ev_' + 'a' * 64
    class RetainedSession(FakeSession):
        def call(self, tool, arguments=None, timeout=180):
            if tool == 'analyze_javascript_application':
                payload = {'error': {'code': 'resource_constraint', 'details': {'resource': 'transport',
                    'reported_limits': {'evidence_reference': {'kind': 'retained-evidence', 'evidence_id': evidence_id}}}}}
                return {'isError': True, 'content': [{'type': 'text', 'text': json.dumps(payload)}]}
            if tool == 'inspect_analysis_view':
                assert arguments['source']['evidence_id'] == evidence_id
                payload = {'normalized_result': {'parent_evidence_id': evidence_id if matching_parent else 'ev_' + 'b' * 64,
                                                'parent_operation': 'analyze_javascript_application'}}
                return {'content': [{'type': 'text', 'text': json.dumps(payload)}]}
            return super().call(tool, arguments, timeout)
    monkeypatch.setattr(reverse_engineer, 'REA_HOME', tmp_path / 'rea')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    engine = reverse_engineer.register_reverse_engineer_tools(registry, reverse_engineer.ReverseEngineer(RetainedSession()),
        fetcher=Fetcher({'https://example.com/app.js': (b'const x = 1;', 'application/javascript')}))
    try:
        result = registry.execute('reverse_engineer_link', {'url': 'https://example.com/app.js'})
        assert result.success is matching_parent
        if matching_parent:
            assert 'full_result_transport_limited' in result.output['report']
        else:
            assert 'resource_constraint' in result.error
    finally:
        engine.close()


def test_native_download_link_checks_privacy_again_when_the_prepared_action_runs(tmp_path):
    from app.reverse_engineer import ReverseEngineer, register_reverse_engineer_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_reverse_engineer import FakeSession
    fetched = []
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    engine = ReverseEngineer(FakeSession(), fetch=lambda *args: fetched.append(args) or b'MZ')
    register_reverse_engineer_tools(registry, engine)
    registry.authority = SimpleNamespace(privacy_mode=True)
    with pytest.raises(PermissionError, match='privacy'):
        engine.act('open_binary', {'path': 'https://example.com/owned.exe'})
    assert not fetched and engine.status()['open_target'] is None
    engine.close()


@pytest.mark.skipif(os.environ.get('HAYAT_REA_WEB') != '1', reason='public link check: set HAYAT_REA_WEB=1')
def test_public_http_links_run_actual_fetch_and_rea_analysis():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    rows = []
    try:
        for url in ('https://code.jquery.com/jquery-3.7.1.js',
                    'https://jqueryui.com/resources/demos/accordion/default.html'):
            result = runtime.registry.execute('reverse_engineer_link', {'url': url})
            assert result.success, result.error
            data = result.output
            analysis_path = json.loads(Path(data['manifest']).read_text())['analysis_path']
            analysis = json.loads(Path(analysis_path).read_text())
            projected = None
            if analysis.get('full_result_transport_limited'):
                projected = json.loads(analysis['summary']['report'])['normalized_result']
                assert projected['parent_evidence_id'] == analysis['retained_evidence']['evidence_id']
                assert projected['parent_operation'] == 'analyze_javascript_application'
                assert projected['summary']['statistics']['parsed_javascript_files'] > 0
            row = {'url': url, 'resources': data['resources'], 'skipped': data['skipped_resources'],
                   'manifest': data['manifest'], 'code_executed': data['code_executed'],
                   'actual_rea_graph': bool(projected) or 'JavaScriptApplicationGraph' in data['report'],
                   'retained_summary': bool(projected), 'coverage': projected.get('summary', {}).get('coverage') if projected else None}
            rows.append(row)
            assert row['actual_rea_graph'] and data['resources'] and not row['code_executed']
        (Path(__file__).resolve().parents[1] / '.test-runs/rea-public-links.json').write_text(json.dumps(
            {'passed': True, 'cases': rows, 'scope': 'Public frontend/JavaScript static analysis, no execution.'}, indent=2), encoding='utf-8')
    finally:
        runtime.close()
