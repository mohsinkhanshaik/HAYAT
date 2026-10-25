import hashlib
import json

import pytest

from app.rea_links import PublicAppLink, source_controls
from app.rea_source_discovery import discover_javascript, summarize_source_map
from app.rea_workbench import WorkbenchSession, verify_collected_file
from tests.test_rea_links import Fetcher


def test_actual_installed_parser_finds_literals_without_executing_source():
    source = b'''import './child.js'; export * from './exports.js';
    const decoy = "import('./never.js')"; // require('./also-never.js')
    import('./lazy.js'); throw new Error('THIS MUST NOT EXECUTE');
    //# sourceMappingURL=main.js.map
    '''
    result = discover_javascript(source)
    if result['parser'] == 'unavailable':
        pytest.skip('Bundled Node/REA Babel parser unavailable')
    values = {row['specifier'] for row in result['dependencies']}
    assert values == {'./child.js', './exports.js', './lazy.js'}
    assert result['source_map'] == 'main.js.map'


def test_modules_maps_and_evidence_are_bounded_and_retained_without_target_execution(tmp_path):
    # Following "./child.js" needs REA's JavaScript parser; a fresh clone without runtime/ has none.
    if discover_javascript(b'import "./x.js";')['parser'] == 'unavailable':
        pytest.skip('Bundled Node/REA Babel parser unavailable')
    mapping = json.dumps({'version': 3, 'sources': ['../../private-name.js'],
                          'sourcesContent': ['export const ownedValue = 42;'], 'mappings': ''}).encode()
    fetcher = Fetcher({'https://example.com/': (b'<html><script type="module" src="/main.js"></script></html>', 'text/html'),
                       'https://example.com/main.js': (b'import "./child.js";\n//# sourceMappingURL=main.js.map', 'application/javascript'),
                       'https://example.com/child.js': (b'export const ownedValue = 42;', 'application/javascript'),
                       'https://example.com/main.js.map': (mapping, 'application/json')})
    result = PublicAppLink(fetcher, lambda _path: {'static': True}, tmp_path).inspect('https://example.com/')
    assert len(result['resources']) == 2 and len(result['source_maps']) == 1
    assert any(row['status'] == 'collected' for row in result['dependencies'])
    assert not result['code_executed']
    assert not (tmp_path / 'private-name.js').exists()
    session = WorkbenchSession()
    row = session.remember('reverse_engineer_link', {'url': 'https://example.com/'}, 180, True, result, .1)
    assert row['source_evidence']['source_maps'][0]['sha256'] == hashlib.sha256(mapping).hexdigest()
    evidence = row['source_evidence']['resources'][0]
    assert verify_collected_file(evidence, row['evidence_root'])['verified']
    from pathlib import Path
    Path(evidence['path']).write_text('changed', encoding='utf-8')
    assert not verify_collected_file(evidence, row['evidence_root'])['verified']


def test_evidence_verification_rejects_paths_outside_retained_root(tmp_path):
    secret = tmp_path / 'outside.js'
    secret.write_bytes(b'outside')
    with pytest.raises(ValueError):
        verify_collected_file({'path': str(secret), 'bytes': 7, 'sha256': hashlib.sha256(b'outside').hexdigest()}, tmp_path / 'sources')


def test_source_map_metadata_is_not_used_as_local_or_remote_paths():
    details = summarize_source_map(b'{"version":3,"sources":["../../x.js","https://private.invalid/x"],"sourcesContent":["a",null],"mappings":""}')
    assert details['source_count'] == 2 and len(details['embedded_sources']) == 1
    assert details['embedded_sources'][0]['sha256'] == hashlib.sha256(b'a').hexdigest()
    with pytest.raises(ValueError):
        summarize_source_map(b'{"version":2}')


def test_module_fetch_returning_html_is_not_retained_or_analyzed_as_javascript(tmp_path):
    fetcher = Fetcher({'https://example.com/': (b'<html><script src="/challenge.js"></script></html>', 'text/html'),
                       'https://example.com/challenge.js': (b'<html><h1>Access denied</h1></html>', 'text/html')})
    result = PublicAppLink(fetcher, lambda _: pytest.fail('challenge is not JavaScript'), tmp_path).inspect('https://example.com/')
    assert not result['resources'] and not result['code_executed']
    assert any('returned HTML' in row['reason'] for row in result['skipped_resources'])


def test_cancelled_source_job_never_fetches_or_creates_artifacts(tmp_path):
    import threading
    cancel = threading.Event()
    cancel.set()
    fetcher = Fetcher({})
    with source_controls(cancel=cancel):
        with pytest.raises(PermissionError, match='cancelled'):
            PublicAppLink(fetcher, lambda _: None, tmp_path).inspect('https://example.com/')
    assert not fetcher.calls and not list(tmp_path.iterdir())
