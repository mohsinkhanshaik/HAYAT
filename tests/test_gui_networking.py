"""Private networking controls exercise the real local store and draft builder."""
from __future__ import annotations

import json
import os
import time
import tkinter as tk
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.emergency import ExecutionInterlock
from app.gui_networking import NetworkingWindow, evidence_text
from app.gui_theme import Palette
from app.networking import NetworkingBook


def codec(raw, *, decrypt=False):
    """Isolated storage codec; DPAPI itself is covered by the backend tests."""
    return raw[5:] if decrypt else b'TEST:' + raw


class GUI(Palette):
    def __init__(self, root, book):
        self.root = root
        self.runtime = SimpleNamespace(networking=book, registry=SimpleNamespace(interlock=ExecutionInterlock()))


def finish(window, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        window.update()
        if not window._pending and not window._refresh_after:
            return
        time.sleep(.01)
    assert not window._pending, window.status.get()


@pytest.fixture
def studio(shared_tk, tmp_path):
    from tests.conftest import close_window, open_window
    parent = open_window(shared_tk, '1120x800-4000-4000')
    book = NetworkingBook(tmp_path / 'contacts', codec=codec)
    window = NetworkingWindow(GUI(parent, book))
    window.geometry('1120x800-4000-4000')
    finish(window)
    yield window, book
    close_window(parent)


def save(window, name='Ada Example', expertise='Python, Open science'):
    window.fields['name'].set(name)
    window.fields['role'].set('Research engineer')
    window.fields['organization'].set('Example Research')
    window.fields['expertise'].set(expertise)
    window.fields['source_url'].set('https://example.org/team/ada')
    window.channel_kind.set('email')
    window.channel_value.set('ada@example.org')
    window.channel_status.set('Public professional contact')
    assert window.add_channel()
    assert window.save_contact()
    finish(window)
    assert window.current_id
    return window.current_id


def test_save_edit_draft_and_copy_are_real_local_operations(studio):
    window, book = studio
    contact_id = save(window)
    assert book.get(contact_id)['name'] == 'Ada Example'
    window.fields['goals'].set('Mentor Python contributors')
    assert window.save_contact()
    finish(window)
    assert book.get(contact_id)['goals'] == ['Mentor Python contributors']
    window.purpose.set('Discuss contributing a small Python documentation fix')
    window.sender.set('Sam Example')
    assert window.prepare_draft()
    finish(window)
    assert window.draft['sent'] is False
    assert 'Python documentation fix' in window.draft['body']
    assert 'Sam Example' in window.draft['body']
    assert window.copy_draft()
    finish(window)
    assert 'Subject:' in window.clipboard_get()
    assert window.copy_route()
    finish(window)
    assert window.clipboard_get() == 'ada@example.org'


def test_topic_fit_and_search_use_saved_evidence_and_honest_empty_state(studio):
    window, book = studio
    contact_id = save(window)
    book.save('Lee Example', expertise=['mechanical design'])
    window.goal.set('Learn Python and robotics')
    window.refresh()
    finish(window)
    text = window.evidence.get('1.0', 'end')
    assert 'Recorded topic overlap: Python' in text
    assert 'robotics' in text
    assert 'Willingness, availability' in text
    assert window.current_id == contact_id
    window.query.set('no such saved contact')
    window.refresh()
    finish(window)
    assert not window.contacts.get_children()
    assert '0 shown' in window.list_note.cget('text')


def test_refresh_preserves_selection_scroll_draft_and_unsaved_edits(studio):
    window, _book = studio
    contact_id = save(window)
    window.purpose.set('Discuss a documentation contribution')
    assert window.prepare_draft()
    finish(window)
    draft = dict(window.draft)
    window.editor.canvas.yview_moveto(1)
    window.update()
    position = window.editor.canvas.yview()[0]
    window.refresh()
    finish(window)
    assert window.contacts.selection() == (contact_id,)
    assert window.draft == draft
    assert window.editor.canvas.yview()[0] == pytest.approx(position, abs=.02)
    window.fields['role'].set('Unsaved role change')
    window.refresh()
    finish(window)
    assert window.fields['role'].get() == 'Unsaved role change'


def test_discard_confirmation_includes_unadded_route_and_keeps_selected_contact(studio, monkeypatch):
    window, book = studio
    contact_id = save(window)
    other = book.save('Lee Example')
    window.refresh()
    finish(window)
    window.channel_value.set('new@example.org')
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: False)
    window.contacts.selection_set(other['id'])
    window.select_contact()
    window.update()
    assert window.current_id == contact_id
    assert window.contacts.selection() == (contact_id,)
    assert not window.new_contact()
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: True)
    assert window.new_contact()
    assert window.channel_value.get() == ''


def test_channel_evidence_and_explicit_permission_are_required(studio):
    window, _book = studio
    window.channel_kind.set('email')
    window.channel_value.set('hello@example.org')
    window.channel_status.set('Public professional contact')
    assert not window.add_channel()
    assert 'source URL' in window.status.get()
    window.channel_status.set('Explicit permission to contact')
    assert not window.add_channel()
    window.channel_consent.set(True)
    assert window.add_channel()
    assert window.channels[0]['consent'] is True
    window.channel_value.set('hello@example.org')
    window.channel_status.set('Owner provided / unverified')
    assert not window.add_channel()
    assert len(window.channels) == 1


def test_external_revocation_prevents_copying_stale_drafts_or_routes(studio):
    window, book = studio
    contact_id = save(window)
    window.purpose.set('Discuss an open project')
    assert window.prepare_draft()
    finish(window)
    book.revoke(contact_id)
    window.clipboard_clear()
    window.clipboard_append('unchanged clipboard')
    assert window.copy_draft()
    finish(window)
    assert 'revoked' in window.status.get().lower()
    assert window.clipboard_get() == 'unchanged clipboard'
    assert window.copy_route()
    finish(window)
    assert window.clipboard_get() == 'unchanged clipboard'


def test_revocation_and_deletion_controls_update_the_real_book(studio, monkeypatch):
    window, book = studio
    contact_id = save(window)
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: True)
    assert window.revoke_contact()
    finish(window)
    with pytest.raises(PermissionError, match='revoked'):
        book.get(contact_id)
    assert not window.draft and not window.channels
    window.include_revoked.set(True)
    window.refresh()
    finish(window)
    window.contacts.selection_set(contact_id)
    window.select_contact()
    assert 'Permission revoked' in window.evidence.get('1.0', 'end')
    assert not window.save_contact()
    assert window.delete_contact()
    finish(window)
    assert book.all(include_revoked=True) == []


def test_followup_filters_and_sorting_use_recorded_dates(studio):
    window, book = studio
    today = date.today()
    due = book.save('Due Example', follow_up=(today - timedelta(days=1)).isoformat())
    book.save('Later Example', follow_up=(today + timedelta(days=3)).isoformat())
    book.save('No date Example')
    window.refresh()
    finish(window)
    window.sort.set('Next follow-up')
    window._paint_rows()
    assert window.contacts.get_children()[0] == due['id']
    window.due_only.set(True)
    window._paint_rows()
    assert window.contacts.get_children() == (due['id'],)


def test_export_defaults_to_account_encrypted_json_and_plaintext_is_explicit(studio, monkeypatch, tmp_path):
    window, _book = studio
    save(window)
    target = tmp_path / 'encrypted.json'
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: False)
    monkeypatch.setattr('app.gui_networking.filedialog.asksaveasfilename', lambda **kw: str(target))
    assert window.export()
    finish(window)
    result = json.loads(target.read_text('utf-8'))
    assert result['encrypted'] is True
    assert 'ada@example.org' not in target.read_text('utf-8')
    target = tmp_path / 'readable.json'
    answers = iter([True, False])
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: next(answers))
    assert not window.export()
    assert not target.exists()
    monkeypatch.setattr('app.gui_networking.messagebox.askyesno', lambda *a, **kw: True)
    assert window.export()
    finish(window)
    assert 'ada@example.org' in target.read_text('utf-8')


def test_emergency_stop_blocks_gui_operations(studio):
    window, book = studio
    window.fields['name'].set('Blocked Example')
    window.gui.runtime.registry.interlock.halt()
    assert not window.save_contact()
    assert not window.image_research()
    assert book.all() == []
    assert 'stop' in window.status.get().lower()


def test_research_buttons_handoff_known_inputs_without_starting_search(studio):
    window, _book = studio
    window.fields['name'].set('Ada Example')
    window.fields['role'].set('Engineer')
    window.fields['organization'].set('Example Research')
    calls = []
    query = tk.Entry(window)
    panel = SimpleNamespace(query=query, context=tk.StringVar(window), profile=tk.StringVar(window),
                            lift=lambda: calls.append('lift'))
    research = SimpleNamespace(open_person_studio=lambda: calls.append('person'), _person_studio=panel)
    window.gui._open_searches = lambda: calls.append('research')
    window.gui._research_studio = research
    window.gui._research_image = lambda: calls.append('image')
    assert window.research_contact()
    assert query.get() == 'Ada Example'
    assert panel.context.get() == 'Engineer Example Research'
    assert calls == ['research', 'person', 'lift']
    assert window.image_research()
    assert calls[-1] == 'image'


def test_compact_workspace_keeps_editor_and_tables_scrollable(studio, request):
    window, _book = studio
    count = len(window.winfo_children())
    for _ in range(2):
        window.geometry('760x720-4000-4000')
        window.update()
        assert str(window.body.cget('orient')) == 'horizontal'
        assert window.workspace.winfo_height() >= 200
        assert window.contacts.winfo_height() >= 100, 'Compact search must leave visible contact rows.'
        window.editor.canvas.yview_moveto(1)
        window.update()
        assert window.editor.canvas.yview()[0] > 0
        assert window.route_table.cget('xscrollcommand')
        assert window.contacts.cget('yscrollcommand')
        window.geometry('760x960-4000-4000')
        window.update()
        assert str(window.body.cget('orient')) == 'vertical'
        assert window.contacts.winfo_height() >= 70
        window.geometry('1120x800-4000-4000')
        window.update()
        assert str(window.body.cget('orient')) == 'horizontal'
    assert len(window.winfo_children()) == count
    if os.getenv('HAYAT_SHOTS') == '1':
        from tkinter import ttk
        from app.gui_theme import style_controls, style_hayat_buttons
        from tests.test_gui_screenshots import _grab
        style = ttk.Style(window)
        previous = style.theme_use()
        style.theme_use('clam')
        style_controls(window, Palette)
        style_hayat_buttons(window, Palette)
        request.addfinalizer(lambda: style.theme_use(previous))
        save(window)
        window.editor.canvas.yview_moveto(0)
        window.geometry('1120x800+30+30')
        window.update()
        _grab(SimpleNamespace(root=window), '95-private-networking')
        window.geometry('760x720+30+30')
        window.update()
        _grab(SimpleNamespace(root=window), '96-private-networking-compact')


def test_evidence_without_sources_does_not_claim_verification():
    result = evidence_text({'name': 'Example', 'expertise': [], 'goals': [], 'channels': []})
    assert 'not independently verified' in result
    assert 'friendship outcome are unknown' in result


def test_empty_book_and_routes_explain_how_to_start(studio):
    window, _book = studio
    window.update()
    contacts, routes = window.contacts._hayat_empty, window.route_table._hayat_empty
    assert contacts.winfo_manager() == 'place' and 'contact book is empty' in contacts.cget('text')
    assert routes.winfo_manager() == 'place' and 'Add route' in routes.cget('text')
    save(window)
    finish(window)
    assert window.contacts.get_children() and contacts.winfo_manager() == ''
    window.query.set('nobody-matches-this')
    window.refresh()
    finish(window)
    assert 'matches these filters' in contacts.cget('text')


def test_restore_button_adds_contacts_from_an_export_and_leaves_saved_ones(studio, tmp_path, monkeypatch):
    window, book = studio
    source = NetworkingBook(tmp_path / "other-book", codec=codec)
    source.save("Lin Example", organization="Docs Guild", expertise=["Documentation"])
    path = tmp_path / "export.json"
    path.write_text(json.dumps(source.export()), encoding="utf-8")
    save(window)
    finish(window)
    monkeypatch.setattr("app.gui_networking.filedialog.askopenfilename", lambda **_options: str(path))
    assert window.restore_export()
    finish(window)
    assert {row["name"] for row in book.all()} == {"Ada Example", "Lin Example"}
    assert "Restored 1 contact" in window.status.get() and "Nothing was sent" in window.status.get()
    assert "restore" in window.buttons
