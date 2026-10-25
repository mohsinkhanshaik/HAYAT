"""Incident bundles are redacted, traceable to their sources and cancellable."""
from datetime import UTC, datetime
import hashlib
import json
import threading
import time
import zipfile

import pytest

from app.gui_activity import ActivityWindow
from app.incident_bundle import ExportCancelled, describe, export_bundle, log_tail
from tests.conftest import close_window, open_window
from tests.test_gui_discovery import build_gui

NOW = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)


def test_bundle_holds_each_part_with_its_source_hash_and_redaction(tmp_path):
    def broken():
        raise OSError('disk unavailable')
    result = export_bundle(tmp_path, {
        'activity.json': ('Activity window', lambda: [{'tool': 'read_file', 'password': 'very-private-password'}]),
        'note.txt': ('A note', lambda: 'api_key=sk-test-1234567890abcdef1234567890'),
        'system.json': ('incident_snapshot', broken),
    }, query='failed', now=NOW)
    path = tmp_path / 'incident-20261014-093000.zip'
    assert result['path'] == str(path) and result['failed'] == ['system.json']
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        texts = {name: archive.read(name).decode() for name in archive.namelist()}
    assert manifest['search'] == 'failed' and manifest['changes_made'] is False
    assert 'very-private-password' not in str(texts) and 'sk-test-1234567890' not in str(texts)
    assert 'Not collected: OSError: disk unavailable' in texts['system.json']
    for part in manifest['parts']:
        assert part['sha256'] == hashlib.sha256(texts[part['file']].encode()).hexdigest()
    assert manifest['parts'][0] | {'sha256': None} == {'file': 'activity.json', 'source': 'Activity window',
                                                     'status': 'collected', 'bytes': len(texts['activity.json']),
                                                     'items': 1, 'sha256': None}
    text = describe(result)
    assert 'Some parts could not be collected: system.json' in text
    assert '- activity.json (collected, 1 items' in text and 'from Activity window' in text


def test_a_cancelled_export_leaves_no_file(tmp_path):
    calls = []
    with pytest.raises(ExportCancelled):
        export_bundle(tmp_path, {'a.txt': ('A', lambda: 'a'), 'b.txt': ('B', lambda: 'b')},
                      cancelled=lambda: calls.append(1) or len(calls) > 1, now=NOW)
    assert list(tmp_path.iterdir()) == []


def test_log_tail_starts_at_a_whole_line_and_reports_a_missing_log(tmp_path):
    log = tmp_path / 'hayat.log'
    log.write_bytes(''.join(f'line {index}\n' for index in range(1000)).encode())
    tail = log_tail(log, limit=100)
    assert tail.startswith('line ') and tail.endswith('line 999\n') and len(tail) <= 100
    assert log_tail(tmp_path / 'none.log').startswith('Log not readable: FileNotFoundError')


def _drain(gui, until, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while not gui.pending.empty():
            callback, args = gui.pending.get()
            callback(*args)
        if until():
            return
        time.sleep(0.02)
    raise AssertionError('export did not finish')


def test_activity_window_exports_the_filtered_activity_and_failures(shared_tk, tmp_path):
    root = open_window(shared_tk, '1100x850-4000-4000')
    gui = build_gui(root, tmp_path)
    gui.runtime.failures = type('Failures', (), {'recent': lambda self, limit: [{'stage': 'turn', 'error': 'boom'}]})()
    gui._stop = lambda: None
    gui._toggle_privacy = lambda: None
    log = tmp_path / 'hayat.log'
    log.write_text('2026-10-14 INFO started\n')
    ActivityWindow.incident_folder, ActivityWindow.log_path = tmp_path / 'incidents', log
    window = ActivityWindow(gui)
    try:
        gui.runtime.events.publish('TOOL_STARTED', 'tools', {'tool': 'test_add', 'arguments': {'number': 2}})
        gui.runtime.events.publish('TOOL_FAILED', 'tools', {'tool': 'read_file', 'error': 'Missing file'})
        window.query.set('failed')
        window.refresh()
        window.export_incident()
        assert window.export_button.cget('text') == 'Cancel export'
        _drain(gui, lambda: window.export_button.cget('text') == 'Export incident bundle')
        bundles = list((tmp_path / 'incidents').glob('incident-*.zip'))
        assert len(bundles) == 1
        with zipfile.ZipFile(bundles[0]) as archive:
            activity = json.loads(archive.read('activity.json'))
            assert [row['event_type'] for row in activity] == ['TOOL_FAILED']
            assert activity[0]['details']['payload']['error'] == 'Missing file'
            assert json.loads(archive.read('failures.json')) == [{'stage': 'turn', 'error': 'boom'}]
            assert 'started' in archive.read('hayat.log').decode()
            assert 'Privacy: off' in archive.read('state.txt').decode()
            assert json.loads(archive.read('manifest.json'))['search'] == 'failed'
        assert 'Incident bundle saved' in window.body.get('1.0', 'end')
    finally:
        ActivityWindow.incident_folder = ActivityWindow.log_path = None
        if window.winfo_exists():
            window.close()
        gui.activity_monitor.close()
        close_window(root)


def test_activity_export_can_be_cancelled_from_the_same_button(shared_tk, tmp_path):
    root = open_window(shared_tk, '1100x850-4000-4000')
    gui = build_gui(root, tmp_path)
    gui._stop = gui._toggle_privacy = lambda: None
    release = threading.Event()
    gui.runtime.incidents = type('Slow', (), {'snapshot': lambda self, scope: release.wait(10) or {}})()
    ActivityWindow.incident_folder, ActivityWindow.log_path = tmp_path / 'incidents', tmp_path / 'none.log'
    window = ActivityWindow(gui)
    try:
        window.export_incident()
        window.export_incident()
        assert window.export_button.cget('text') == 'Cancelling...'
        release.set()
        _drain(gui, lambda: window.export_button.cget('text') == 'Export incident bundle')
        assert 'Export cancelled. No bundle was saved.' in window.body.get('1.0', 'end')
        assert not list((tmp_path / 'incidents').glob('*'))
    finally:
        ActivityWindow.incident_folder = ActivityWindow.log_path = None
        if window.winfo_exists():
            window.close()
        gui.activity_monitor.close()
        close_window(root)
