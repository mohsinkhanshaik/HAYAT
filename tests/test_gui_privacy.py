"""Real display-boundary, copy-buffer, screen-cover and adaptive layout checks."""
from __future__ import annotations

import os
import tkinter as tk
from types import SimpleNamespace

import pytest

from app.gui_presentation import diagnostic_text, safe_text
from app.gui_theme import Palette
from tests.test_gui import _window as _window, gui as gui, own_gui as own_gui


SECRET = 'sk-proj-' + 'Z9nX4bR7qA' * 5


@pytest.mark.parametrize('original,private', [
    ('Answer: ' + SECRET, SECRET),
    ('https://example.org/callback?code=xy&state=public', 'xy'),
    ('https://example.org/?access%255Ftoken=private123&role=analyst', 'private123'),
    ('https://alice:abc@example.org/jobs', 'alice:abc'),
    ('https://example.org/report#token=tiny', 'tiny'),
    ('Cookie: session=private-cookie; other=also-private', 'also-private'),
    ('Authorization: Bearer short', 'short'),
    ('{"password":"a\\\"b-secret"}', 'b-secret'),
    ('{"token":"tiny"}', 'tiny'),
    ('[REDACTED:api_key len=32 sha256=abcabcabcabc]', 'abcabcabcabc'),
])
def test_visible_outputs_remove_credentials_and_derived_fingerprints(original, private):
    rendered = safe_text(original)
    assert private not in rendered
    assert '[hidden]' in rendered
    assert safe_text(rendered) == rendered


def test_environment_secret_is_masked_even_when_not_shaped_like_a_key(monkeypatch):
    monkeypatch.setenv('HAYAT_PRESENTATION_TEST_TOKEN', 'ordinary-looking-private-value')
    assert safe_text('Error: ordinary-looking-private-value') == 'Error: [hidden]'


def test_public_evidence_and_normal_links_are_preserved():
    text = 'Python required, 3 years. https://example.org/jobs?q=data+analyst&location=London'
    assert safe_text(text) == text


def test_diagnostics_mask_before_truncating_and_abbreviate_home_path():
    from pathlib import Path
    value = 'Failed at ' + str(Path.home() / 'project' / 'config.json') + ' ' + SECRET
    assert str(Path.home()) not in diagnostic_text(value)
    assert 'sha256' not in diagnostic_text(value)
    assert len(diagnostic_text(value, 45)) <= 45
    assert SECRET not in diagnostic_text(value, 45)


@pytest.fixture
def top(shared_tk):
    window = tk.Toplevel(shared_tk)
    window.withdraw()
    yield window
    window.destroy()


def test_readonly_report_masks_split_secret_with_emoji_and_preserves_tags(top):
    from app.gui_theme import DarkScrolledText
    text = DarkScrolledText(top)
    text.tag_configure('heading', foreground='blue')
    text.insert('end', '🚀 Heading\n', 'heading')
    # Separate pieces evade insert-time detection; the read-only transition
    # must inspect the complete buffer before selection/copy is enabled.
    text.insert('end', SECRET[:8])
    text.insert('end', SECRET[8:])
    text.configure(state='disabled')
    assert text.get('1.0', 'end-1c') == '🚀 Heading\n[hidden]'
    assert 'heading' in text.tag_names('1.0')
    text.tag_add('sel', '1.0', 'end-1c')
    assert SECRET not in text.get('sel.first', 'sel.last')


def test_explicit_editor_keeps_exact_contents_when_temporarily_disabled(top):
    from app.gui_theme import DarkScrolledText
    editor = DarkScrolledText(top, protect_output=False)
    editor.insert('1.0', SECRET)
    editor.configure(state='disabled')
    assert editor.get('1.0', 'end-1c') == SECRET


def test_report_masks_multiple_insert_arguments_and_replace(top):
    from app.gui_theme import DarkScrolledText
    text = DarkScrolledText(top, protect_output=True)
    text.insert('end', 'Title\n', ('heading',), SECRET, ('body',))
    text.configure(state='disabled')
    assert SECRET not in text.get('1.0', 'end')
    text.configure(state='normal')
    text.replace('1.0', 'end', SECRET)
    text.configure(state='disabled')
    assert SECRET not in text.get('1.0', 'end')


def test_safe_table_masks_text_values_and_cell_updates_without_changing_identity(top):
    from app.gui_output import SafeTreeview
    table = SafeTreeview(top, columns=('value',))
    identity = 'https://example.org/?code=original'
    table.insert('', 'end', iid=identity, text=SECRET, values=(SECRET,))
    assert table.exists(identity)
    assert SECRET not in str(table.item(identity))
    table.item(identity, values=('https://example.org/?code=short',))
    assert 'short' not in table.set(identity, 'value')
    table.set(identity, 'value', SECRET)
    assert SECRET not in str(table.set(identity))


def test_partial_stream_never_displays_or_queues_raw_draft(top):
    from app.gui_workspace import StreamingPreview
    preview = StreamingPreview(top, Palette)
    preview.publish('start', 'local-model')
    for piece in (SECRET[:3], SECRET[3:16], SECRET[16:], '\n'):
        preview.publish('delta', piece)
        preview.drain()
        assert SECRET[:3] not in preview.text.get('1.0', 'end')
        assert SECRET[:3] not in preview._content
    assert 'Draft hidden' in preview.text.get('1.0', 'end')
    preview.publish('clear')
    preview.drain()
    assert not preview.winfo_manager()


def test_main_chat_transcript_console_and_trace_share_masking(gui):
    gui._append('assistant', 'HAYAT\n' + SECRET)
    gui._append_trace('Failed with ' + SECRET)
    gui.console.write('tools', 'Failed with ' + SECRET)
    assert SECRET not in gui.chat.get('1.0', 'end')
    assert SECRET not in gui.trace.get('1.0', 'end')
    assert SECRET not in gui.console.text.get('1.0', 'end')
    assert SECRET not in gui.console.last_line
    gui._transcript.seek(0)
    assert SECRET not in gui._transcript.read()
    from app.identity import IDENTITY
    # Passive chrome must not duplicate the owner's identity while a private
    # profile/workspace has not deliberately been opened.
    def labels(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                yield str(child.cget('text'))
            yield from labels(child)
    assert not any('Local workspace · ' + IDENTITY.owner_short in label for label in labels(gui._sidebar))


def test_status_polls_are_coalesced_and_rescheduled_after_paint_failure(gui, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, '_paint_tick', lambda: calls.append('paint'))
    gui._tick()
    first = gui._tick_pending
    gui._tick()
    second = gui._tick_pending
    assert first != second and first not in gui._after_handles and second in gui._after_handles
    assert calls == ['paint', 'paint']
    def fail():
        raise RuntimeError('Temporary paint failure')
    monkeypatch.setattr(gui, '_paint_tick', fail)
    with pytest.raises(RuntimeError, match='Temporary paint failure'):
        gui._tick()
    assert not gui._tick_running
    assert second not in gui._after_handles and gui._tick_pending in gui._after_handles


def test_screen_shield_covers_existing_and_new_windows_and_restores_data(top):
    from app.gui_screen_shield import ScreenShield
    gui = type('GUI', (Palette,), {} )()
    gui.root = top
    top.title('Private document')
    original = tk.Text(top)
    original.pack()
    original.insert('1.0', 'Personal CV evidence')
    child = tk.Toplevel(top)
    child.title('Finance')
    shield = ScreenShield(gui)
    try:
        shield.show()
        assert set(shield.covers) == {top, child}
        assert top.title() == child.title() == 'HAYAT · Screen shield'
        assert all(cover.winfo_manager() == 'place' for cover, title in shield.covers.values())
        added = tk.Toplevel(top)
        added.deiconify()
        top.update()
        assert added in shield.covers
        assert shield.covers[child][0].grab_current() in {pair[0] for pair in shield.covers.values()}
        added.destroy()
        shield.hide()
        assert top.title() == 'Private document' and child.title() == 'Finance'
        assert original.get('1.0', 'end-1c') == 'Personal CV evidence'
        assert not shield.covers and not shield.active
    finally:
        child.destroy()


@pytest.mark.parametrize('theme', ['Dark', 'Glass', 'Light', 'Aurora'])
def test_screen_cover_never_uses_a_theme_transparency_key(top, theme):
    from app.gui_screen_shield import ScreenShield
    from app.gui_theme import tokens
    from app.gui_glass import GLASS_KEY
    gui = type('GUI', (Palette,), tokens(theme))()
    gui.root = top
    shield = ScreenShield(gui)
    shield.show()
    try:
        cover = shield.covers[top][0]
        assert cover.cget('background').lower() not in {gui.BLACK.lower(), GLASS_KEY.lower()}
    finally:
        shield.hide()


def test_responsive_details_reflow_without_losing_content(top):
    from app.gui_layout import ResponsiveSplit
    split = ResponsiveSplit(top, Palette)
    first, second = tk.Frame(split), tk.Frame(split)
    split.add(first)
    split.add(second)
    split._fit(SimpleNamespace(width=720))
    assert str(split.cget('orient')) == 'vertical'
    split._fit(SimpleNamespace(width=1180))
    assert str(split.cget('orient')) == 'horizontal'
    assert len(split.panes()) == 2


def test_display_variables_mask_updates_and_skip_identical_masked_polls(top):
    from app.gui_output import DisplayVar
    variable = DisplayVar(top, SECRET)
    assert variable.get() == '[hidden]'
    writes = []
    variable.trace_add('write', lambda *_: writes.append(variable.get()))
    variable.set_if_changed(SECRET)
    assert not writes
    variable.set('https://example.org/?token=short')
    assert 'short' not in variable.get() and len(writes) == 1


def test_flow_row_contains_oversized_controls_and_recovers_label_width(top):
    from app.gui_intelligence_parts import FlowRow
    row = FlowRow(top, Palette.BLACK)
    row.pack(fill='x')
    button = row.add(tk.Button(row, text='Long action ' * 15))
    from tkinter import ttk
    row.add(ttk.Label(row, text='Label with an empty themed wraplength'))
    label = row.add(tk.Label(row, text='A description that should reflow ' * 10))
    top.geometry('480x500')
    top.deiconify()
    top.update()
    assert button.winfo_x() + button.winfo_width() <= row.winfo_width()
    small = label.cget('wraplength')
    top.geometry('1000x500')
    top.update()
    assert int(label.cget('wraplength')) > int(small)


def test_flowing_group_expands_when_accessibility_fonts_grow(top):
    from app.gui_intelligence_parts import FlowRow
    row = FlowRow(top, Palette.BLACK)
    row.pack(fill='x')
    group = row.add(tk.Frame(row))
    label = tk.Label(group, text='Open wallet', font=('Segoe UI', 9), padx=10)
    label.pack(side='right')
    top.geometry('1000x500')
    top.deiconify()
    top.update()
    before = group.winfo_width()
    label.configure(font=('Segoe UI', 18))
    top.update()
    assert group.winfo_width() > before
    assert label.winfo_width() == label.winfo_reqwidth()


def test_stale_resize_events_cannot_expand_embedded_content_past_current_viewport(top):
    from app.gui_depth import DepthSurface
    from app.gui_panels import ScrollableColumn
    surface = DepthSurface(top, Palette)
    surface.pack(fill='x')
    column = ScrollableColumn(top, Palette)
    column.pack(fill='both', expand=True)
    top.geometry('480x500')
    top.deiconify()
    top.update()
    stale = SimpleNamespace(width=1900)
    surface._resize(stale)
    column._canvas_resized(stale)
    assert int(float(surface.itemcget(surface._body_window, 'width'))) <= surface.winfo_width()
    assert int(float(column.canvas.itemcget(column._window, 'width'))) <= column.canvas.winfo_width()


def test_native_privacy_layout_captures(own_gui):
    gui = own_gui
    if os.environ.get('HAYAT_SHOTS') != '1':
        pytest.skip('native captures are opt-in')
    from app.gui_theme import apply_theme
    from tests.test_gui_screenshots import _grab, _pump
    previous = gui._theme_name
    previous_scale = gui._text_scale
    previous_motion = gui.depth_nav.motion
    try:
        gui._set_workspace_motion(False)
        apply_theme(gui.root, type(gui), 'Aurora')
        gui.root.deiconify()
        gui.root.geometry('1280x800+0+0')
        gui._show_home()
        _pump(gui, .3)
        _grab(gui, '130-aurora-home')
        gui._append('assistant', 'HAYAT\nA completed report keeps credential material hidden: ' + SECRET)
        _pump(gui, .2)
        _grab(gui, '131-private-output')
        gui._set_text_scale(1.3)
        gui._show_home()
        _pump(gui, .2)
        assert gui._accessible_compact_controls, (gui.root.geometry(), gui.root.winfo_height(), gui._text_scale)
        _grab(gui, '135-aurora-large-text')
        gui._set_text_scale(previous_scale)
        gui.screen_shield.show()
        _pump(gui, .2)
        _grab(gui, '132-screen-shield')
    finally:
        gui.screen_shield.hide()
        gui._set_text_scale(previous_scale)
        apply_theme(gui.root, type(gui), previous)
        gui._set_workspace_motion(previous_motion)
        gui.root.withdraw()
