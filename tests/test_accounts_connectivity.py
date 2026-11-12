"""A failed account check says what it proves: a dropped connection is not a broken sign-in."""
import json
import threading
import time

import httpx
import pytest

from app.accounts import UNTESTED_KINDS, AccountManager, check_kind, connection_state
from app.accounts_chat import report
from tests.test_accounts import Broker, Provider, Response


def manager_with(tmp_path, request, *keys):
    """Saved token sign-ins for `keys`, written the way a real connect leaves them."""
    broker = Broker()
    manager = AccountManager(broker, tmp_path / 'accounts.json', request=request, open_browser=lambda _url: None)
    manager.retry_pause = 0
    tokens = {'github': 'gh-token', 'notion': 'secret_notion'}
    for key in keys:
        broker.store(f'account.{key}', json.dumps({'access_token': tokens[key], 'method': 'token'}), scopes=[f'account:{key}'])
    manager._save_records({key: {'connected': True, 'connected_as': 'owner', 'method': 'token', 'check_ok': True} for key in keys})
    return manager, broker


@pytest.mark.parametrize('problem,error,kind', [
    ('', None, 'ok'),
    ('the saved sign-in is missing from Windows Credential Manager; connect again', None, 'lost'),
    ('api.github.com could not be reached (ConnectError)', RuntimeError(), 'unreachable'),
    ('api.github.com answered 429: slow down', RuntimeError(), 'rate_limited'),
    ('api.github.com answered 503', RuntimeError(), 'provider_error'),
    ('api.github.com answered 401: Bad credentials', RuntimeError(), 'rejected'),
    ('api.github.com answered 403', RuntimeError(), 'rejected'),
    ('The GitHub sign-in has expired; sign in again', PermissionError(), 'rejected'),
    # A PermissionError is the provider's own refusal even when its wording mentions the network.
    ('Slack refused the token (could not be reached)', PermissionError(), 'rejected'),
    ('api.github.com answered with something that is not JSON (HTTP 200)', RuntimeError(), 'failed'),
    ('something nobody anticipated', ValueError(), 'failed'),
])
def test_a_failed_check_is_sorted_by_what_it_proves(problem, error, kind):
    assert check_kind(problem, error) == kind


def test_an_unreachable_provider_leaves_the_sign_in_untested_not_broken(tmp_path):
    fake, offline = Provider(), [True]

    def request(method, url, **kwargs):
        if offline[0]:
            raise httpx.ConnectError('no route to https://api.github.com/user?access_token=gh-token')
        return fake(method, url, **kwargs)

    manager, _broker = manager_with(tmp_path, request, 'github')
    row = manager.check('github')
    assert row['connected'] and row['check_ok'] is None and row['check_kind'] == 'unreachable'
    assert row['connection_state'] == 'unreachable' and 'no need to sign in again' in row['connection_note']
    assert 'gh-token' not in json.dumps(row)  # the failing address carried the token; only the host is kept
    assert row['connected_as'] == 'owner'      # what was known stays known
    # Chat says "not tested" and does not send the owner off to sign in again.
    said = report([{'tool': 'account_check', 'result': {'success': True, 'output': {'check_view': True, 'results': [row]}},
                    'verification': {'passed': True}}])
    assert 'GitHub: not tested' in said and 'Sign in again from' not in said
    # Back online: the same saved sign-in checks as working, with nothing re-entered.
    offline[0] = False
    again = manager.check('github')
    assert again['check_ok'] is True and again['check_kind'] == 'ok' and again['connection_state'] == 'checked'


@pytest.mark.parametrize('status,kind', [(429, 'rate_limited'), (500, 'provider_error'), (503, 'provider_error')])
def test_a_busy_or_faulty_provider_is_also_untested(tmp_path, status, kind):
    manager, _broker = manager_with(tmp_path, lambda method, url, **kwargs: Response(status, {'message': 'later'}), 'github')
    row = manager.check('github')
    assert row['check_ok'] is None and row['check_kind'] == kind and row['connection_state'] == 'unreachable'
    assert row['connection_note'] == UNTESTED_KINDS[kind]


def test_a_refused_sign_in_is_still_reported_as_not_working(tmp_path):
    manager, broker = manager_with(tmp_path, Provider(), 'github')
    broker.values['account.github'] = json.dumps({'access_token': 'revoked', 'method': 'token'})
    row = manager.check('github')
    assert row['check_ok'] is False and row['check_kind'] == 'rejected' and row['connection_state'] == 'check_failed'
    broker.delete('account.github')
    lost = manager.check('github')
    assert not lost['connected'] and lost['check_kind'] == 'lost'


def test_a_stale_untested_note_never_outlives_a_good_check_or_a_sign_out(tmp_path):
    record = {'connected': True, 'check_ok': True, 'check_kind': 'unreachable'}
    assert connection_state(record)['connection_state'] == 'checked'
    manager, _broker = manager_with(tmp_path, Provider(), 'github')
    records = manager._records()
    records['github'].update(check_ok=None, check_kind='unreachable', check_error='x could not be reached (ConnectError)')
    manager._save_records(records)
    assert manager.status('github')['connection_state'] == 'unreachable'
    manager.sign_out('github')
    row = manager.status('github')
    assert row['connection_state'] == 'not_connected' and not row['check_kind'] and 'check_kind' not in manager._records()['github']


def test_a_read_whose_connection_drops_is_tried_once_more_but_a_send_never_is(tmp_path):
    fake, attempts = Provider(), []

    def request(method, url, **kwargs):
        attempts.append((method, url))
        if len([item for item in attempts if item == (method, url)]) == 1:
            raise httpx.ConnectError('connection reset')
        return fake(method, url, **kwargs)

    manager, _broker = manager_with(tmp_path, request, 'github')
    assert manager.check('github')['check_ok'] is True
    assert attempts == [('GET', 'https://api.github.com/user')] * 2
    attempts.clear()
    with pytest.raises(RuntimeError, match='could not be reached'):
        manager._call('POST', 'https://api.github.com/repos/owner/hayat/issues', json={'title': 'once'})
    assert attempts == [('POST', 'https://api.github.com/repos/owner/hayat/issues')]
    # A slow answer is not repeated either: the first request may still be running at the provider.
    attempts.clear()

    def slow(method, url, **kwargs):
        attempts.append((method, url))
        raise httpx.ReadTimeout('slow')
    manager._request = slow
    with pytest.raises(RuntimeError, match='ReadTimeout'):
        manager._call('GET', 'https://api.github.com/user')
    assert len(attempts) == 1


def test_accounts_are_checked_side_by_side_and_reported_in_order(tmp_path):
    fake, entered, together = Provider(), [], threading.Event()

    def request(method, url, **kwargs):
        entered.append(url)
        if len(entered) == 2:
            together.set()
        # Each check waits for the other to have started: one after another this would time out.
        assert together.wait(3), 'the second account was not checked while the first was waiting'
        return fake(method, url, **kwargs)

    manager, _broker = manager_with(tmp_path, request, 'github', 'notion')
    started = time.monotonic()
    result = manager.check_all()
    assert time.monotonic() - started < 3
    assert [row['key'] for row in result['results']] == [row['key'] for row in manager.status()['accounts'] if row['connected']]
    assert all(row['check_ok'] is True for row in result['results'])
    # Both results were saved: side-by-side saves must not overwrite one another.
    saved = manager._records()
    assert saved['github']['checked_at'] and saved['notion']['checked_at']


def test_reads_and_saves_of_the_record_file_do_not_collide(tmp_path):
    manager, _broker = manager_with(tmp_path, Provider(), 'github', 'notion')
    failures, stop = [], threading.Event()

    def read():
        while not stop.is_set():
            if set(manager._records()) != {'github', 'notion'}:
                failures.append('a read saw a missing or half-written file')
                return

    readers = [threading.Thread(target=read) for _ in range(4)]
    for thread in readers:
        thread.start()
    try:
        for index in range(150):
            records = manager._records()
            records['github']['note'] = index
            manager._save_records(records)
    except OSError as error:
        failures.append(f'a save failed: {error}')
    finally:
        stop.set()
        for thread in readers:
            thread.join(3)
    assert not failures
    assert manager._records()['github']['note'] == 149


def test_the_window_shows_an_untested_account_in_amber_without_asking_to_sign_in_again(shared_tk, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from tkinter import ttk

    from app.gui_accounts import DOWN, AccountsCenter
    from app.gui_theme import OBSIDIAN, Palette, style_controls, style_hayat_buttons
    from tests.conftest import close_window, open_window

    class GUI(Palette):
        pass

    for name, value in OBSIDIAN.items():
        setattr(GUI, name, value)
    root = open_window(shared_tk, '1380x900+20+20')
    manager, _broker = manager_with(tmp_path, lambda *args, **kwargs: pytest.fail('No live provider checks'), 'github')
    records = manager._records()
    records['github'].update(check_ok=None, check_kind='unreachable', checked_at='2026-11-10T00:00:00+00:00',
                             check_error='api.github.com could not be reached (ConnectError)')
    manager._save_records(records)
    monkeypatch.setattr(manager, 'check_all', lambda: {'results': []})
    gui = GUI()
    gui.root, gui.runtime = root, SimpleNamespace(accounts=manager, email=None, secret_broker=manager.broker)
    ttk.Style(root).theme_use('clam')
    style_controls(root, GUI)
    style_hayat_buttons(root, GUI)
    window = AccountsCenter(gui)
    try:
        window.select('github')
        root.update()
        card = window.card_widgets['github']['state']
        assert 'not reached' in card.cget('text') and 'untested' in card.cget('text')
        assert str(card.cget('fg')).lower() != str(DOWN).lower()
        assert 'no need to sign in again' in window.state_label.cget('text')
        assert 'GitHub not reached (untested)' in window.status.get() and 'not working' not in window.status.get()
        shown = ' '.join(str(widget.cget('text')) for widget in _labels(window.connect_tab))
        assert 'not tested' in shown and 'Sign in again below' not in shown
        window.done_test(manager.status('github'), None)
        assert window.connect_note.cget('text').startswith('Not tested:')
    finally:
        if window.winfo_exists():
            window.close()
        close_window(root)


def _labels(widget):
    import tkinter as tk
    for child in widget.winfo_children():
        if isinstance(child, tk.Label):
            yield child
        yield from _labels(child)
