"""Tk interactions use a registered public-read tool and never start live Tor."""
from __future__ import annotations

import threading
import time
import os
from types import SimpleNamespace

import pytest
from tkinter import ttk

from app.gui_onion_navigation import OnionNavigationWindow
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.conftest import open_window
from tests.test_tor_routing import onion


BASE = 'http://' + onion()


def report(url=BASE + '/', title='Public Widget docs'):
    return {'start_url': url, 'status': 'complete', 'usable_pages': 1, 'pages_retrieved': 1, 'errors': [],
            'stop_reason': 'selected_scope_finished',
            'pages': [{'citation': 'O1', 'url': url, 'final_url': url, 'title': title,
                       'retrieved_at': '2026-10-24T10:00:00+00:00', 'sha256': 'a' * 64, 'status_code': 200,
                       'content_type': 'text/html', 'bytes': 123, 'text': '\n'.join(f'Evidence line {n}' for n in range(180)),
                       'links': [{'url': BASE + '/next', 'text': 'Next', 'followable': True},
                                 {'url': 'https://example.org/source', 'text': 'Publisher', 'followable': False}]}],
            'citations': [{'id': 'O1', 'url': url, 'title': title, 'retrieved_at': '2026-10-24T10:00:00+00:00', 'sha256': 'a' * 64}]}


class GUI(Palette):
    pass


@pytest.fixture
def navigator(shared_tk):
    root = open_window(shared_tk, '1000x820-4000-4000')
    registry = ToolRegistry(PermissionManager({'default': 'DENY', 'permissions': {'public_web_read': 'ALLOW'}}))
    calls = []
    def handler(**arguments):
        calls.append((threading.get_ident(), arguments))
        return report(arguments['url'])
    registry.register('onion_navigate', 'Read selected public onion page', {'type': 'object'}, 'low', 'public_web_read', handler)
    gui = GUI()
    gui.root = root
    gui.runtime = SimpleNamespace(registry=registry, session=SimpleNamespace(effective_modes=[]),
                                  authority=SimpleNamespace(privacy_mode=False))
    style = ttk.Style(root)
    prior = style.theme_use()
    style.theme_use('clam')
    style_controls(root, gui)
    style_hayat_buttons(root, gui)
    panel = OnionNavigationWindow(gui)
    root.update()
    try:
        yield panel, calls
    finally:
        panel.close()
        style.theme_use(prior)
        root.destroy()


def pump(panel, predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'Navigator background action did not complete.'


def test_window_opens_without_network_and_reads_via_registry_off_tk_thread(navigator):
    panel, calls = navigator
    assert not calls and not panel._pending
    panel.update()
    assert panel.source_list._hayat_empty.winfo_manager() == 'place'
    assert 'numbered for citation' in panel.source_list._hayat_empty.cget('text')
    # The cell between the two scrollbars shows the holder frame: it must be
    # the panel's colour, not a bright system-grey square.
    for table in (panel.source_list, panel.link_list):
        assert table.master.cget('bg') == table.master.master.cget('bg')
    panel.url.set(BASE + '/')
    panel.read_button.invoke()
    pump(panel, lambda: not panel._pending)
    assert panel.source_list._hayat_empty.winfo_manager() == ''
    assert len(calls) == 1 and calls[0][0] != threading.get_ident()
    assert calls[0][1] == {'url': BASE + '/', 'query': '', 'max_pages': 1, 'max_depth': 0}
    assert 'Evidence line 0' in panel.text.get('1.0', 'end')
    assert 'SHA-256: ' + 'a' * 64 in panel.text.get('1.0', 'end')
    assert '1 usable / 1 retrieved' in panel.status.get()


def test_follow_options_are_explicit_and_selected_external_link_is_not_opened(navigator):
    panel, calls = navigator
    panel.url.set(BASE + '/')
    panel.follow.set(True)
    panel.pages.set('4')
    panel.depth.set('2')
    panel.query.set('Widget API')
    panel.read()
    pump(panel, lambda: not panel._pending)
    assert calls[0][1]['max_pages'] == 4 and calls[0][1]['max_depth'] == 2
    panel.link_list.selection_set('link-1')
    panel.show_link()
    assert 'disabled' in panel.link_button.state()
    panel.read_link()
    assert len(calls) == 1 and 'same-site' in panel.status.get()
    panel.link_list.selection_set('link-0')
    panel.show_link()
    panel.read_link()
    pump(panel, lambda: not panel._pending)
    assert calls[-1][1]['url'] == BASE + '/next'


def test_history_back_forward_is_local_and_clear_removes_evidence(navigator):
    panel, calls = navigator
    for url in (BASE + '/', BASE + '/next'):
        panel.url.set(url)
        panel.read()
        pump(panel, lambda: not panel._pending)
    assert len(calls) == 2
    panel.back_button.invoke()
    assert panel.url.get() == BASE + '/' and len(calls) == 2
    panel.forward_button.invoke()
    assert panel.url.get() == BASE + '/next' and len(calls) == 2
    panel.clear_history()
    assert not panel._history and not panel.report and not panel.source_list.get_children()
    assert 'history cleared' in panel.status.get()


@pytest.mark.parametrize('mode', ['PRIVATE', 'LOCAL', 'LOCAL_ONLY', 'privacy', 'stopped', 'denied'])
def test_privacy_stop_and_permission_denial_prevent_network_tool_handler(navigator, mode):
    panel, calls = navigator
    runtime = panel.gui.runtime
    if mode in {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
        runtime.session.effective_modes = [mode]
    elif mode == 'privacy':
        runtime.authority.privacy_mode = True
    elif mode == 'stopped':
        runtime.registry.interlock.halt()
    else:
        runtime.registry.permissions.policies['public_web_read'] = 'DENY'
    panel.url.set(BASE + '/')
    panel.read()
    pump(panel, lambda: not panel._pending)
    assert not calls
    assert any(word in panel.status.get().lower() for word in ('privacy', 'local-only', 'stop', 'deny'))


def test_cancel_discards_inflight_result_and_preserves_previous_history(navigator):
    panel, calls = navigator
    panel.url.set(BASE + '/')
    panel.read()
    pump(panel, lambda: not panel._pending)
    started, release = threading.Event(), threading.Event()
    def blocked(**arguments):
        started.set()
        assert release.wait(3)
        return report(arguments['url'], 'Discard this late result')
    panel.gui.runtime.registry.get('onion_navigate').handler = blocked
    panel.url.set(BASE + '/late')
    panel.read()
    pump(panel, started.is_set)
    panel.cancel_button.invoke()
    assert 'Cancelling' in panel.status.get()
    release.set()
    pump(panel, lambda: not panel._pending)
    assert len(panel._history) == 1 and 'Discard this late result' not in panel.text.get('1.0', 'end')
    assert 'cancelled' in panel.status.get().lower()


def test_live_progress_context_is_connected_through_registered_handler(navigator):
    from app.research.onion_navigation import _CONTROLS
    panel, _ = navigator
    started, release = threading.Event(), threading.Event()
    def slow(**arguments):
        controls = _CONTROLS.get()
        controls['progress']({'message': 'Reading actual source 1/3'})
        started.set()
        assert release.wait(3)
        return report(arguments['url'])
    panel.gui.runtime.registry.get('onion_navigate').handler = slow
    panel.url.set(BASE + '/')
    panel.read()
    pump(panel, lambda: started.is_set() and 'actual source' in panel.status.get())
    assert panel._pending and panel.cancel_button.instate(['!disabled'])
    release.set()
    pump(panel, lambda: not panel._pending)


def test_repeated_same_page_render_preserves_scroll_and_pending_motion(navigator):
    panel, _ = navigator
    value = report()
    panel.show_report(value)
    panel.update()
    # Tk computes wrapped-line heights asynchronously; update() can return
    # before yview's denominator is current. Wait for real text metrics rather
    # than allowing suite load to change the apparent fraction mid-assertion.
    panel.text.tk.call(panel.text._w, 'sync')
    panel.text.yview_moveto(.5)
    panel.update()
    panel.text.tk.call(panel.text._w, 'sync')
    before = panel.text.yview()[0]
    top = panel.text.index('@0,0')
    top_y = panel.text.dlineinfo(top)[1]
    panel.show_page()
    panel.update()
    panel.text.tk.call(panel.text._w, 'sync')
    assert panel.text.index('@0,0') == top
    assert abs(panel.text.dlineinfo(top)[1] - top_y) <= 1
    assert abs(panel.text.yview()[0] - before) < .01
    panel.text._smooth_scroller.target = .8
    pending = panel.text._smooth_scroller.job = panel.text.after(1000, lambda: None)
    panel._set_text(panel.text, 'Fresh source')
    assert panel.text._smooth_scroller.target is None
    assert panel.text._smooth_scroller.job is None
    assert pending not in panel.tk.call('after', 'info')


def test_compact_layout_keeps_controls_and_source_view_inside_window(navigator):
    panel, _ = navigator
    panel.geometry('650x640+30+30')
    panel.update()
    assert panel.body.cget('orient') == 'vertical'
    for widget in (panel.read_button, panel.cancel_button, panel.address, panel.status_label, panel.body):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() >= panel.winfo_rootx()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height() + 2


def test_unavailable_sources_show_actual_error_report(navigator):
    panel, _ = navigator
    value = report()
    value.update(status='unavailable', usable_pages=0, pages_retrieved=0, pages=[], citations=[],
                 errors=[{'url': BASE + '/', 'error_type': 'PermissionError', 'message': 'robots.txt disallows retrieval'}])
    panel.show_report(value)
    assert 'No public page was retrieved' in panel.text.get('1.0', 'end')
    assert 'robots.txt disallows' in panel.details.get('1.0', 'end')
    assert 'Unavailable' in panel.status.get()


def test_close_cancels_worker_controls_and_poll(navigator):
    panel, _ = navigator
    poll = panel._poll_job
    panel.close()
    assert panel._closed and panel._cancel.is_set() and panel._poll_job is None
    assert poll not in panel.gui.root.tk.call('after', 'info')


def test_navigator_visual_review(navigator):
    panel, _ = navigator
    panel.show_report(report(title='Public source — display verification fixture'))
    panel.update()
    if os.environ.get('HAYAT_SHOTS') != '1':
        return
    from tests.test_gui_screenshots import _grab, _pump
    panel.gui.root.geometry('1100x860+0+0')
    panel.geometry('980x760+40+30')
    panel.attributes('-topmost', True)
    _pump(SimpleNamespace(root=panel), .4)
    _grab(SimpleNamespace(root=panel), '97-onion-navigator-sources')
    panel.geometry('650x640+40+30')
    _pump(SimpleNamespace(root=panel), .4)
    _grab(SimpleNamespace(root=panel), '98-onion-navigator-compact')
