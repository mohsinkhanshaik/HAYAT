from types import SimpleNamespace

import pytest
from tkinter import ttk

from app.gui_chat_details import ChatDetails
from app.gui_theme import Palette, style_controls
from tests.conftest import open_window


@pytest.fixture
def details(shared_tk):
    root = open_window(shared_tk, '420x720-4000-4000')
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = root, SimpleNamespace(model=None)
    gui._video_cards, gui._video_link_cards, gui._inline_images = {}, {}, []
    gui.use_example = lambda _text: None
    gui.save_transcript = lambda: None
    gui._open_source = lambda _url: None
    gui._open_video_studio = lambda _path: None
    style = ttk.Style(root)
    previous = style.theme_use()
    style.theme_use('clam')
    style_controls(root, gui)
    panel = ChatDetails(root, gui)
    panel.pack(fill='x')
    root.update()
    try:
        yield panel
    finally:
        root.destroy()
        style.theme_use(previous)


def test_details_start_compact_and_do_not_expand_when_messages_arrive(details):
    assert not details.expanded and details.winfo_height() < 120
    details.observe('user', 'You\n' + 'A long conversation topic ' * 20)
    details.update()
    assert not details.tabs.winfo_ismapped() and details.winfo_height() < 120
    details.toggle()
    details.update()
    assert details.tabs.winfo_ismapped() and details.subject_label.winfo_ismapped()
    details.tabs.select(2)
    details.toggle()
    details.toggle()
    details.update()
    assert details.tabs.index(details.tabs.select()) == 2


def test_source_selection_and_reading_position_survive_unrelated_chat_updates(details):
    details.observe('user', 'You\n' + ' '.join(f'https://example.org/source/{i}' for i in range(18)))
    details.toggle()
    details.tabs.select(2)
    details.update()
    details.source_list.selection_set(10)
    details.source_list.yview(8)
    details.update()
    old = details.source_list.yview()
    details.observe('user', 'You\nContinue the explanation')
    assert details.source_list.curselection() == (10,)
    assert details.source_list.yview() == pytest.approx(old)
    details.observe('user', 'You\nhttps://example.org/source/new')
    assert details.source_list.curselection() == (10,)
    assert details.source_list.get(details.source_list.nearest(0)) == 'https://example.org/source/8'
    details.reset()
    details.observe('user', 'You\nhttps://example.org/again')
    assert details.source_list.get(0, 'end') == ('https://example.org/again',)


def test_long_provenance_can_scroll_without_making_the_sidebar_unbounded(details):
    details.toggle()
    details.provenance.set('\n'.join(f'Evidence source {i}' for i in range(100)))
    details.update()
    assert details.winfo_height() < 440
    canvas = details.detail_scroll.canvas
    assert canvas.yview()[1] < 1
    canvas.yview_moveto(1)
    assert canvas.yview()[0] > 0
