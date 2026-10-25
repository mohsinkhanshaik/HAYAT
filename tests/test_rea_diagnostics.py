import json
from types import SimpleNamespace

import pytest

from app import reverse_engineer
from app.rea_diagnostics import engine_report, format_engine_report
from scripts.setup_rea import bundle_toolchain, ensure_node_license, install_release, supported_node


@pytest.fixture
def engines(tmp_path):
    node, script = tmp_path / 'runtime/node/node.exe', tmp_path / 'runtime/rea/scripts/rea.mjs'
    for file in [node, script]:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    return dict(node=node, script=script, environment={}, root=tmp_path)


def test_native_readiness_uses_only_scoped_checks_and_never_opens_target(engines):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return {'exit_code': 0, 'stdout': json.dumps({'healthy': True, 'environment_healthy': False,
            'scope_checks': [{'name': 'ghidra:version', 'ok': True, 'detail': '12.1.4'}],
            'checks': [{'name': 'unrelated-client', 'ok': False}]})}
    report = engine_report(**engines, runner=run)
    assert report['ready'] and len(report['checks']) == 1
    assert calls[0][0][-4:] == ['doctor', '--provider', 'ghidra', '--json']
    assert calls[0][1]['timeout'] == 45
    assert '12.1.4' in format_engine_report(report)


@pytest.mark.parametrize('response,message', [
    ({'timed_out': True}, 'timed out'),
    ({'stdout_truncated': True}, 'incomplete'),
    ({'stdout': 'not json'}, 'Expecting value'),
    ({'stdout': '{"healthy":"yes"}'}, 'valid readiness'),
    ({'stdout': '{"healthy":true,"scope_checks":"invalid"}'}, 'complete scoped'),
])
def test_engine_check_failure_is_actionable_without_claiming_ready(engines, response, message):
    report = engine_report(**engines, runner=lambda *a, **k: response)
    assert not report['ready'] and message in report['error']
    assert 'setup_native_analysis.py' in format_engine_report(report)


def test_missing_node_does_not_run_a_probe(engines):
    engines['node'].unlink()
    def unexpected(*args, **kwargs):
        pytest.fail('A missing toolchain must not be launched')
    report = engine_report(**engines, runner=unexpected)
    assert not report['ready'] and 'setup_rea.py' in report['error']


def test_mismatched_jdk_preserves_upstream_remediation(engines):
    report = engine_report(**engines, runner=lambda *a, **k: {'exit_code': 1, 'stdout': json.dumps({
        'healthy': False, 'scope_checks': [{'name': 'ghidra:java', 'ok': False,
                                           'detail': 'Unsupported JRE', 'remediation': 'Install a full JDK 21.'}]})})
    assert not report['ready'] and 'Install a full JDK 21.' in format_engine_report(report)


def test_version_refresh_reads_changed_installation_without_restart(tmp_path, monkeypatch):
    script = tmp_path / 'scripts/rea.mjs'
    script.parent.mkdir()
    manifest = tmp_path / 'package.json'
    monkeypatch.setattr(reverse_engineer, 'REA_SCRIPT', script)
    manifest.write_text('{"version":"old"}')
    assert reverse_engineer.installed_version() == 'old'
    manifest.write_text('{"version":"new"}')
    assert reverse_engineer.installed_version() == 'new'


def test_status_check_does_not_start_or_change_the_shared_session(monkeypatch):
    def unexpected():
        pytest.fail('Status inspection must not open a REA session')
    monkeypatch.setattr('app.rea_diagnostics.engine_report', lambda **kwargs: {'ready': True})
    engine = reverse_engineer.ReverseEngineer(unexpected)
    report = engine.status(check_engine=True)
    assert report['engine_check']['ready'] and report['open_target'] is None and engine._session is None


@pytest.mark.parametrize('version,accepted', [
    ('v22.18.0', False), ('v22.19.0', True), ('v23.9.0', False), ('v24.10.0', False),
    ('v24.11.0', True), ('v24.18.0', True), ('v25.0.0', False), ('v26.0.0', True),
    ('not-a-version', False), ('v24.18.0-rc.1', False),
])
def test_node_versions_follow_the_pinned_rea_contract(version, accepted):
    assert supported_node(version) is accepted


def test_setup_bundles_node_and_npm_and_then_runs_without_system_tools(tmp_path):
    source = tmp_path / 'system-node'
    source.mkdir()
    (source / 'node.exe').write_bytes(b'node-fixture')
    (source / 'LICENSE').write_text('Node license fixture')
    npm = source / 'node_modules/npm/bin/npm-cli.js'
    npm.parent.mkdir(parents=True)
    npm.write_text('npm fixture')
    run = lambda *a, **k: SimpleNamespace(stdout='v24.18.0\n')
    node, cli = bundle_toolchain(tmp_path, find=lambda name: str(source / 'node.exe') if name == 'node' else None, run=run)
    assert node.read_bytes() == b'node-fixture' and cli.read_text() == 'npm fixture'
    assert (node.parent / 'LICENSE').read_text() == 'Node license fixture'
    assert bundle_toolchain(tmp_path, find=lambda _: None, run=run) == (node, cli)


def test_setup_preserves_an_incomplete_npm_install(tmp_path):
    node = tmp_path / 'runtime/node/node.exe'
    node.parent.mkdir(parents=True)
    node.touch()
    incomplete = node.parent / 'node_modules/npm'
    incomplete.mkdir(parents=True)
    (incomplete / 'owner-file').write_text('preserve')
    source = tmp_path / 'system'
    npm = source / 'node_modules/npm/bin/npm-cli.js'
    npm.parent.mkdir(parents=True)
    npm.write_text('fixture')
    with pytest.raises(SystemExit, match='preserved'):
        bundle_toolchain(tmp_path, find=lambda name: str(source / 'node.exe') if name == 'node' else None,
                         run=lambda *a, **k: SimpleNamespace(stdout='v24.18.0'))
    assert (incomplete / 'owner-file').read_text() == 'preserve'


def test_bundled_node_keeps_its_license_even_when_the_system_install_lacks_one(tmp_path):
    node = tmp_path / 'node.exe'
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(content=b'Node.js license fixture', raise_for_status=lambda: None)
    ensure_node_license(node, 'v24.18.0', fetch=fetch)
    ensure_node_license(node, 'v24.18.0', fetch=lambda *a, **k: pytest.fail('an existing license is retained'))
    assert calls == ['https://raw.githubusercontent.com/nodejs/node/v24.18.0/LICENSE']
    assert (tmp_path / 'LICENSE').read_bytes() == b'Node.js license fixture'


@pytest.mark.parametrize('failure', ['download', 'version', 'activation', None])
def test_rea_upgrade_retains_the_working_release_until_a_verified_replacement(tmp_path, monkeypatch, failure):
    """A failed download, bad launcher or locked directory cannot remove the old release."""
    from pathlib import Path
    import subprocess

    source, current = tmp_path / 'integrations/rea', tmp_path / 'runtime/rea'
    source.mkdir(parents=True)
    current.mkdir(parents=True)
    (current / 'owner-file').write_text('old release')
    (source / 'package.json').write_text('{"dependencies":{"rea-agents":"6.7.0"}}')
    (source / 'package-lock.json').write_text('{}')
    node, npm = tmp_path / 'node.exe', tmp_path / 'npm-cli.js'
    def run(command, **kwargs):
        assert (current / 'owner-file').read_text() == 'old release'
        if 'ci' in command:
            assert '--ignore-scripts' in command and kwargs['cwd'] != current
            if failure == 'download':
                raise subprocess.CalledProcessError(1, command)
            package = kwargs['cwd'] / 'node_modules/rea-agents'
            package.mkdir(parents=True)
            (package / 'package.json').write_text('{"version":"6.7.0"}')
        return SimpleNamespace(stdout='wrong version' if failure == 'version' else '6.7.0\n')
    rename = Path.rename
    def locked_activation(path, destination):
        if path.name.startswith('rea-stage-') and destination == current:
            raise PermissionError('fixture: locked directory')
        return rename(path, destination)
    if failure == 'activation':
        monkeypatch.setattr(Path, 'rename', locked_activation)
    if failure:
        with pytest.raises((subprocess.CalledProcessError, RuntimeError, PermissionError)):
            install_release(tmp_path, node, npm, run=run)
        assert (current / 'owner-file').read_text() == 'old release'
    else:
        report = install_release(tmp_path, node, npm, run=run)
        assert (Path(report['backup']) / 'owner-file').read_text() == 'old release'
        assert json.loads((current / 'hayat-install.json').read_text())['version'] == '6.7.0'
    assert not (tmp_path / 'runtime/.rea-install.lock').exists()


def test_rea_setup_refuses_to_overlap_another_install(tmp_path):
    lock = tmp_path / 'runtime/.rea-install.lock'
    lock.parent.mkdir()
    lock.write_text('existing install')
    with pytest.raises(RuntimeError, match='Another REA setup'):
        install_release(tmp_path, tmp_path / 'node.exe', tmp_path / 'npm.js')
    assert lock.read_text() == 'existing install'
