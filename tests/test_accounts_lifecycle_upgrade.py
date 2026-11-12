"""Deterministic account lifecycle checks with in-memory credentials and synthetic providers."""
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.accounts import AccountManager
from tests.test_accounts import Broker, Provider


@pytest.mark.parametrize('stage', ['device', 'grant', 'profile'])
@pytest.mark.parametrize('action', ['cancel', 'replace', 'sign_out'])
def test_late_device_responses_cannot_reconnect_or_remove_a_replacement(tmp_path, stage, action):
    started, release = threading.Event(), threading.Event()
    fake, broker = Provider(), Broker()
    endpoints = {'device': 'https://github.com/login/device/code',
                 'grant': 'https://github.com/login/oauth/access_token', 'profile': 'https://api.github.com/user'}
    blocked = []

    def request(method, url, **kwargs):
        if url == endpoints[stage] and not blocked:
            blocked.append(True)
            started.set()
            assert release.wait(3)
        return fake(method, url, **kwargs)

    manager = AccountManager(broker, tmp_path / 'accounts.json', request=request, open_browser=lambda _url: None)
    manager.set_app('github', 'synthetic-client-id')
    fake.device_answers.append({'access_token': 'gh-token', 'expires_in': 3600})
    if stage != 'device':
        manager.start_device('github')
    errors = []

    def work():
        try:
            manager.start_device('github') if stage == 'device' else manager.poll_device('github')
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert started.wait(1)
        if action == 'sign_out':
            manager.sign_out('github')
        elif action == 'replace':
            manager.start_device('github')
        else:
            manager.cancel_device('github')
        replacement = manager._devices.get('github')
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], PermissionError)
    assert 'account.github' not in broker.values
    assert not manager.status('github')['connected']
    if action == 'replace':
        assert replacement is not None and manager._devices.get('github') is replacement


def test_saved_expiry_and_check_status_do_not_probe_or_read_credentials(tmp_path, monkeypatch):
    broker, fake = Broker(), Provider()
    manager = AccountManager(broker, tmp_path / 'accounts.json', request=fake)
    manager._save_records({'github': {'connected': True, 'connected_as': 'synthetic user', 'method': 'token',
                                      'check_ok': True, 'credential_expires_at': time.time() - 1,
                                      'refresh_available': False}})
    monkeypatch.setattr(broker, 'issue_lease', lambda *args, **kwargs: pytest.fail('status must not read credentials'))
    state = manager.status('github')
    assert state['connection_state'] == 'reconnect' and 'expired' in state['connection_note']
    assert state['connected']  # saved identity is retained until explicit sign-out
    assert not fake.calls
    records = manager._records()
    records['github']['refresh_available'] = True
    manager._save_records(records)
    assert manager.status('github')['connection_state'] == 'refresh_due'
    records['github']['check_ok'] = False
    manager._save_records(records)
    assert manager.status('github')['connection_state'] == 'check_failed'


def test_synthetic_reconnect_state_and_window_close_cancel_pending_sign_ins(shared_tk, tmp_path, monkeypatch):
    from tkinter import ttk
    from app.gui_accounts import AccountsCenter
    from app.gui_theme import OBSIDIAN, Palette, style_controls, style_hayat_buttons
    from tests.conftest import close_window, open_window

    class GUI(Palette):
        pass

    for name, value in OBSIDIAN.items():
        setattr(GUI, name, value)
    root = open_window(shared_tk, '1380x900+20+20')
    manager = AccountManager(Broker(), tmp_path / 'accounts.json', request=lambda *args, **kwargs: pytest.fail('No live provider checks'))
    manager._save_records({'github': {'connected': True, 'connected_as': 'Synthetic account', 'method': 'token',
                                      'connected_at': '2026-11-10T00:00:00+00:00', 'check_ok': True,
                                      'credential_expires_at': time.time() - 1, 'refresh_available': False}})
    monkeypatch.setattr(manager, 'check_all', lambda: {'results': []})
    cancelled = []
    monkeypatch.setattr(manager, 'cancel_browser', lambda key: cancelled.append(('browser', key)))
    monkeypatch.setattr(manager, 'cancel_device', lambda key: cancelled.append(('device', key)))
    gui = GUI()
    gui.root, gui.runtime = root, SimpleNamespace(accounts=manager, email=None, secret_broker=manager.broker)
    ttk.Style(root).theme_use('clam')
    style_controls(root, GUI)
    style_hayat_buttons(root, GUI)
    window = AccountsCenter(gui)
    try:
        window.select('github')
        root.update()
        assert 'expired' in window.card_widgets['github']['state'].cget('text')
        assert 'expired' in window.state_label.cget('text')
        if os.environ.get('HAYAT_SHOTS') == '1':
            from PIL import ImageGrab
            output = Path('.test-runs/gui-shots')
            output.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(window=int(window.wm_frame(), 16)).save(output / 'accounts-reconnect-synthetic.png')
        window._device_request_key, window.browser_key = 'github', 'google'
        window.close()
        assert ('device', 'github') in cancelled and ('browser', 'google') in cancelled
    finally:
        if window.winfo_exists():
            window.close()
        close_window(root)
