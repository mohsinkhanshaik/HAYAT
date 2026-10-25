from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from app import reverse_engineer
from scripts import setup_native_analysis as setup


def _native_ready() -> bool:
    root = reverse_engineer.PROJECT_ROOT / 'runtime'
    return (reverse_engineer.available() and (root / 'ghidra' / 'support' / 'analyzeHeadless.bat').is_file()
            and (root / 'java' / 'bin' / 'javac.exe').is_file())


def archive(root, entries):
    cache = root / '.tmp' / 'native-analysis-setup'
    cache.mkdir(parents=True)
    path = cache / 'fixture.zip'
    with zipfile.ZipFile(path, 'w') as output:
        for name, content in entries.items():
            output.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_archive_installs_and_retains_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    digest = archive(tmp_path, {'jdk/bin/javac.exe': b'owned-fixture'})
    target = tmp_path / 'runtime' / 'java'
    setup.provision('https://example.org/fixture.zip', digest, 'fixture.zip', target, 'bin/javac.exe')
    assert (target / 'bin/javac.exe').read_bytes() == b'owned-fixture'
    assert digest in (target / 'hayat-provenance.json').read_text()
    # Existing valid installs are preserved on repeat runs.
    setup.provision('https://example.org/fixture.zip', digest, 'fixture.zip', target, 'bin/javac.exe')


@pytest.mark.parametrize('bad_name', ['../escape.txt', '/absolute.txt'])
def test_archive_cannot_escape_the_staging_directory(tmp_path, monkeypatch, bad_name):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    digest = archive(tmp_path, {bad_name: b'bad', 'jdk/bin/javac.exe': b'fixture'})
    with pytest.raises(ValueError):
        setup.provision('https://example.org/archive', digest, 'fixture.zip', tmp_path / 'runtime' / 'java', 'bin/javac.exe')
    assert not (tmp_path / 'runtime/java').exists()


def test_incomplete_existing_install_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    target = tmp_path / 'runtime/java'
    target.mkdir(parents=True)
    (target / 'owner-file').write_text('keep')
    with pytest.raises(RuntimeError, match='preserved'):
        setup.provision('https://example.org/archive', 'unused', 'fixture.zip', target, 'bin/javac.exe')
    assert (target / 'owner-file').read_text() == 'keep'


def test_native_engine_discovery_preserves_explicit_paths_and_parent_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(reverse_engineer, 'PROJECT_ROOT', tmp_path)
    for folder, marker in [('ghidra', 'support/analyzeHeadless.bat'), ('java', 'bin/javac.exe')]:
        path = tmp_path / 'runtime' / folder / marker
        path.parent.mkdir(parents=True)
        path.touch()
    original = {'JAVA_HOME': 'custom-java', 'PATH': 'unchanged'}
    environment = reverse_engineer.native_environment(original)
    assert environment['JAVA_HOME'] == 'custom-java'
    assert environment['GHIDRA_INSTALL_DIR'] == str((tmp_path / 'runtime/ghidra').resolve())
    assert original == {'JAVA_HOME': 'custom-java', 'PATH': 'unchanged'}


@pytest.mark.parametrize('tool', ['annotate_native_function', 'extract_firmware', 'recover_javascript_sources',
                                 'cutter_command', 'gdb_console', 'rizin_debug_command', 'pull_adb_file',
                                 'install_adb_package', 'unload_frida_script', 'collect_adb_bugreport'])
def test_new_rea_changes_cannot_run_through_the_read_path(tool):
    engine = reverse_engineer.ReverseEngineer()
    with pytest.raises(PermissionError):
        engine.read(tool)
    assert engine._session is None


def test_node_is_only_the_bundled_copy_never_the_system_path():
    # Self-contained: a missing runtime/node is a setup error, not a silent fall-through to PATH.
    assert reverse_engineer.NODE == reverse_engineer.PROJECT_ROOT / 'runtime' / 'node' / 'node.exe'


def test_contained_folders_keep_rea_inside_the_project(tmp_path):
    home = tmp_path / 'rea-home'
    env = reverse_engineer.contained_folders(home)
    resolved = str(home.resolve())
    assert set(env) >= {'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
                        'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'}
    assert all(value.startswith(resolved) for value in env.values()), env
    assert env['TEMP'] == env['TMP']
    for value in env.values():  # created on demand so the JVM/REA never fail to write
        assert Path(value).is_dir()


def test_native_environment_redirects_home_and_temp_into_the_project():
    env = reverse_engineer.native_environment({'USERPROFILE': 'C:/Users/someone', 'TEMP': 'C:/Windows/Temp'})
    inside = str(reverse_engineer.REA_HOME.resolve())
    assert env['USERPROFILE'].startswith(inside) and env['TEMP'].startswith(inside)


@pytest.mark.skipif(not _native_ready(), reason='Ghidra/JDK not installed; run scripts/setup_native_analysis.py')
def test_real_ghidra_run_writes_nothing_outside_the_project(tmp_path):
    """Everything HAYAT loads or runs stays in its folder: a real JVM analysis adds no hsperfdata to %TEMP% and
    no .ghidra/.rea to the user profile."""
    import os

    from scripts.verify_native_analysis import fixture

    profile = Path(os.environ['USERPROFILE'])
    temp = Path(os.environ.get('TEMP', tmp_path))

    def shot():
        found = set()
        for root, pattern in ((temp, 'hsperfdata_*/*'), (profile, '.ghidra*'), (profile, '.rea*')):
            if root.exists():
                found |= {p for p in root.rglob(pattern) if p.is_file()}
        return found
    before = shot()
    target = tmp_path / 'owned.exe'
    fixture(target)
    engine = reverse_engineer.ReverseEngineer(lambda: reverse_engineer.ReaSession())
    try:
        engine.act('open_binary', {'path': str(target), 'provider_id': 'ghidra'}, timeout=360)
        pseudo = engine.read('procedure_pseudo_code', {'procedure': 'entry'}, timeout=180)['report']
        assert '42' in pseudo or '0x2a' in pseudo.lower()
    finally:
        engine.close()
    assert shot() - before == set(), 'REA/Ghidra wrote outside D:\\AI (profile or %TEMP%)'


@pytest.mark.skipif(not _native_ready(), reason='Ghidra/JDK not installed; run scripts/setup_native_analysis.py')
def test_real_ghidra_decompiles_an_owned_pe_without_running_it(tmp_path):
    """The whole native path end to end: open a generated x64 PE with Ghidra and read back its decompiled 42."""
    from scripts.verify_native_analysis import fixture

    target = tmp_path / 'owned-fixture.exe'
    fixture(target)
    before = hashlib.sha256(target.read_bytes()).hexdigest()
    engine = reverse_engineer.ReverseEngineer(lambda: reverse_engineer.ReaSession(start_timeout=60))
    try:
        assert engine.act('open_binary', {'path': str(target), 'provider_id': 'ghidra'}, timeout=360)['ok']
        assert engine.read('binary_overview', {}, timeout=180)['ok']
        decompiled = engine.read('procedure_pseudo_code', {'procedure': 'entry'}, timeout=180)['report']
        assert '42' in decompiled or '0x2a' in decompiled.lower()
        # A changing Ghidra note still cannot slip through the read path.
        with pytest.raises(PermissionError):
            engine.read('set_comment', {'address': '0x140001000', 'comment': 'x'})
    finally:
        engine.close()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == before, 'the target binary must never be modified or run'
