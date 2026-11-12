"""Compatibility patch provenance must describe bytes, including Windows newlines."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app import research_lab
from scripts import rea_ghidra_compat as compatibility


def _package(root: Path) -> tuple[Path, dict[Path, bytes]]:
    package = root / 'rea-agents'
    package.mkdir()
    (package / 'package.json').write_text('{"version":"6.8.0"}', encoding='utf-8')
    originals = {}
    for name, newline in [('dist/cli/policy.js', '\n'), ('dist/ghidra/GhidraReleasePolicy.js', '\r\n'),
                          ('dist/mcp/policy.js', '\r\n')]:
        path = package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ('// UTF-8 provenance: π\nexport const ghidraReleaseCompatibility = () => {\n'
                + compatibility.POLICY + ';\n};\nconst limitation = `'
                + compatibility.LIMIT + '`;\n')
        content = text.replace('\n', newline).encode('utf-8')
        path.write_bytes(content)
        originals[path] = content
    return package, originals


def test_new_patch_retains_exact_original_bytes_and_hashes_actual_files(tmp_path):
    package, originals = _package(tmp_path)
    result = compatibility.apply_ghidra_compatibility(package)
    assert result['provenance_format'] == 2
    for entry in result['files']:
        path = package / entry['file']
        backup = package / entry['before_file']
        assert backup.read_bytes() == originals[path]
        assert entry['before_sha256'] == hashlib.sha256(originals[path]).hexdigest()
        assert entry['after_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry['before_digest_source'] == 'retained_backup_bytes'
        assert path.read_bytes().count(b'\r\n') == originals[path].count(b'\r\n')


def test_reapplication_is_idempotent_and_does_not_touch_js_or_marker(tmp_path):
    package, originals = _package(tmp_path)
    first = compatibility.apply_ghidra_compatibility(package)
    marker = package / 'hayat-ghidra-compatibility.json'
    snapshots = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in [*originals, marker]}
    assert compatibility.apply_ghidra_compatibility(package) == first
    for path, snapshot in snapshots.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == snapshot


def test_legacy_normalized_text_hashes_are_repaired_without_rewriting_patched_files(tmp_path):
    package, originals = _package(tmp_path)
    legacy_records = []
    patched_snapshots = {}
    for path, original in originals.items():
        text = original.decode('utf-8').replace('\r\n', '\n')
        patched = text.replace(compatibility.POLICY, compatibility.PATCHED_POLICY).replace(
            compatibility.LIMIT, compatibility.PATCHED_LIMIT)
        # Reproduce the old Windows write_text newline translation explicitly.
        path.write_bytes(patched.replace('\n', '\r\n').encode('utf-8'))
        path.with_suffix('.js.hayat-original').write_bytes(text.replace('\n', '\r\n').encode('utf-8'))
        legacy_records.append({'file': str(path.relative_to(package)),
                               'before_sha256': hashlib.sha256(text.encode()).hexdigest(),
                               'after_sha256': hashlib.sha256(patched.encode()).hexdigest()})
        patched_snapshots[path] = path.read_bytes()
    marker = package / 'hayat-ghidra-compatibility.json'
    marker.write_text(json.dumps({'patch': 'hayat-ghidra-12.2-v1', 'files': legacy_records}), encoding='utf-8')
    result = compatibility.apply_ghidra_compatibility(package)
    for entry in result['files']:
        path = package / entry['file']
        assert path.read_bytes() == patched_snapshots[path]
        assert entry['after_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry['before_sha256'] == hashlib.sha256((package / entry['before_file']).read_bytes()).hexdigest()
        assert entry['before_digest_source'] == 'retained_backup_bytes'
    assert json.loads(marker.read_text(encoding='utf-8')) == result
    assert compatibility.apply_ghidra_compatibility(package) == result


def test_an_already_patched_file_without_a_backup_does_not_invent_an_original_digest(tmp_path):
    package, originals = _package(tmp_path)
    for path, original in originals.items():
        path.write_bytes(original.replace(compatibility.POLICY.encode(), compatibility.PATCHED_POLICY.encode()).replace(
            compatibility.LIMIT.encode(), compatibility.PATCHED_LIMIT.encode()))
    result = compatibility.apply_ghidra_compatibility(package)
    assert all(entry['before_sha256'] is None and entry['before_digest_source'] == 'unavailable'
               for entry in result['files'])


def test_unexpected_policy_copy_preserves_every_input_without_creating_backups(tmp_path):
    package, originals = _package(tmp_path)
    last = sorted(originals)[-1]
    last.write_bytes(originals[last].replace(compatibility.POLICY.encode(), b'return true'))
    originals[last] = last.read_bytes()
    with pytest.raises(ValueError, match='Unexpected Ghidra policy'):
        compatibility.apply_ghidra_compatibility(package)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert not list(package.rglob('*.hayat-original'))
    assert not (package / 'hayat-ghidra-compatibility.json').exists()


def test_activation_failure_restores_previous_copies_and_preserves_backups(tmp_path, monkeypatch):
    package, originals = _package(tmp_path)
    failing_path = sorted(originals)[1]
    write = compatibility._write_bytes_atomically
    failed = False

    def fail_once(path, content):
        nonlocal failed
        if path == failing_path and not failed:
            failed = True
            raise PermissionError('fixture: locked policy copy')
        return write(path, content)

    monkeypatch.setattr(compatibility, '_write_bytes_atomically', fail_once)
    with pytest.raises(PermissionError, match='locked policy copy'):
        compatibility.apply_ghidra_compatibility(package)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert all(path.with_suffix('.js.hayat-original').read_bytes() == content for path, content in originals.items())
    assert not (package / 'hayat-ghidra-compatibility.json').exists()


def test_upgrade_guidance_includes_the_compatibility_bridge_before_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(research_lab, 'PROJECT_ROOT', tmp_path)
    ghidra = tmp_path / 'runtime/ghidra'
    (ghidra / 'support').mkdir(parents=True)
    (ghidra / 'support/analyzeHeadless.bat').write_bytes(b'fixture')
    (ghidra / 'Ghidra').mkdir()
    (ghidra / 'Ghidra/application.properties').write_text('application.version=12.1.4', encoding='utf-8')
    commands = research_lab.ghidra_status(None)['upgrade_next']
    assert commands.index('scripts.setup_native_analysis') < commands.index('scripts.setup_rea')
    assert commands.index('scripts.setup_rea') < commands.index('scripts.verify_native_analysis')
