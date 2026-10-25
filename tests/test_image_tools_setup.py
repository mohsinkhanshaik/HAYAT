"""Pinned project-local provisioning rejects global/different environments."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import setup_image_tools as setup


def test_version_check_has_no_install_or_network_side_effect(monkeypatch):
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('Status must not start pip'))
    report = setup.inventory()
    assert report['automatic_install'] is False
    assert {row['name'] for row in report['packages']} == {'ImageHash', 'ExifRead', 'PyWavelets'}


def test_global_python_is_rejected_before_any_package_mutation(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    monkeypatch.setattr(setup, 'sys', SimpleNamespace(platform='win32', version_info=(3, 14), prefix=str(tmp_path / 'global-python')))
    monkeypatch.setattr(setup.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('Global environment must stay untouched'))
    with pytest.raises(RuntimeError, match='global Python'):
        setup.install()
    assert not (tmp_path / 'runtime').exists()


def test_existing_different_helper_version_is_preserved(monkeypatch, tmp_path):
    monkeypatch.setattr(setup, 'ROOT', tmp_path)
    monkeypatch.setattr(setup, 'sys', SimpleNamespace(platform='win32', version_info=(3, 14), prefix=str(tmp_path / '.venv')))
    monkeypatch.setattr(setup.platform, 'machine', lambda: 'AMD64')
    monkeypatch.setattr(setup.importlib.metadata, 'version', lambda _: '1.0')
    monkeypatch.setattr(setup, 'inventory', lambda: {'packages': [{'name': 'ImageHash', 'version': '0.1',
        'expected_version': '4.3.2', 'inside_project': True}]})
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('Existing helper must not be downgraded/replaced'))
    with pytest.raises(RuntimeError, match='Preserving existing'):
        setup.install()


def test_unpinned_cached_wheel_is_rejected_before_use(tmp_path):
    (tmp_path / 'untrusted-1.0-py3-none-any.whl').write_bytes(b'not a published wheel')
    with pytest.raises(ValueError, match='pinned'):
        setup.verify_wheels(tmp_path)


def test_installed_wheel_cache_and_notices_are_reproducible():
    cache = setup.ROOT / 'runtime' / 'image-tools' / 'wheels'
    if not cache.is_dir():
        pytest.skip('Optional image helper provisioning has not been run')
    rows = setup.verify_wheels(cache)
    assert len(rows) >= 3 and all(row['notices'] for row in rows)
    for row in rows:
        assert hashlib.sha256((cache / row['wheel']).read_bytes()).hexdigest() == row['sha256']
        assert row['source'].startswith('https://pypi.org/project/')
    recorded = json.loads((cache.parent / 'provenance.json').read_text(encoding='utf-8'))
    assert {(row['name'].casefold(), row['sha256']) for row in recorded['packages']} == {
        (row['name'].casefold(), row['sha256']) for row in rows}
    assert all(Path(distribution.locate_file('')).resolve().is_relative_to(setup.ROOT)
               for distribution in (setup.importlib.metadata.distribution(name) for name in setup.PACKAGES))
