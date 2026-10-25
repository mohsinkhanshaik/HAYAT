import time
import os

import pytest
from tkinter import ttk

from tests.test_gui_intelligence import explorer as explorer


@pytest.fixture(autouse=True)
def application_ttk_theme(shared_tk):
    """Mirror the production desktop's clam theme without changing other tests."""
    style = ttk.Style(shared_tk)
    previous = style.theme_use()
    style.theme_use('clam')
    try:
        yield
    finally:
        style.theme_use(previous)


def show_resources(window):
    window.notebook.select(window.tabs['resources'])
    window.update()


def test_resource_filters_are_local_and_keep_access_requirements_visible(explorer, monkeypatch):
    window, _gui = explorer
    show_resources(window)
    monkeypatch.setattr(window.manager.fetcher, 'fetch', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('Filter attempted network access')))
    window.resource_query.set('life science')
    window.resource_category.set('publications')
    window.resource_access.set('On demand')
    window.update()
    assert window.resource_tree.get_children() == ('europe_pmc',)
    window.resource_tree.selection_set('europe_pmc')
    window.resource_selected()
    assert 'Full text' in window.resource_text.get('1.0', 'end')
    window.clear_resource_filters()
    window.resource_query.set('scotland')
    window.resource_access.set('Manual / key')
    assert window.resource_tree.get_children() == ('scotland_cameras',)
    window.resource_tree.selection_set('scotland_cameras')
    window.resource_selected()
    assert 'Manual setup only' in window.resource_text.get('1.0', 'end')


def test_resource_refresh_preserves_selection_scroll_and_empty_states(explorer):
    window, _gui = explorer
    show_resources(window)
    identifiers = window.resource_tree.get_children()
    selected = identifiers[-4]
    window.resource_tree.selection_set(selected)
    window.resource_tree.see(selected)
    window.update()
    before = window.resource_tree.yview()
    window.filter_resources()
    window.update()
    assert window.resource_tree.selection() == (selected,)
    assert abs(window.resource_tree.yview()[0] - before[0]) < .02
    window.resource_query.set('no resource can possibly match this sentence 000')
    window.update()
    assert not window.resource_tree.selection() and not window.resource_tree.get_children()
    assert 'No resources match' in window.resource_text.get('1.0', 'end')
    window.clear_resource_filters()
    assert window.resource_tree.get_children() == identifiers


def test_resource_filters_survive_repeated_compact_resize_and_scroll(explorer):
    window, _gui = explorer
    window.show_filter_bar(False)
    show_resources(window)
    def widget_count(widget):
        return 1 + sum(widget_count(child) for child in widget.winfo_children())
    widgets = widget_count(window.tabs['resources'])
    tree = window.resource_tree
    pulses = []
    started = time.monotonic()
    for index in range(30):
        width, height = (620, 520) if index % 2 else (1000, 760)
        window.geometry(f'{width}x{height}')
        window.resource_query.set('camera' if index % 3 else '')
        window.resource_tree.yview_scroll(2 if index % 2 else -2, 'units')
        window.after(0, lambda: pulses.append(True))
        window.update()
        assert window.resource_tree is tree
        assert widget_count(window.tabs['resources']) == widgets
        assert window.resource_tree.winfo_height() >= 80
        assert window.resource_search.winfo_width() >= 120
        assert window.resource_count_label.winfo_rooty() < window.resource_tree.winfo_rooty()
    assert len(pulses) == 30
    assert time.monotonic() - started < 15


@pytest.mark.skipif(os.getenv('HAYAT_RESOURCE_SHOTS') != '1', reason='manual resource GUI visual verification')
def test_resource_gui_captures_real_window_at_normal_and_compact_sizes(explorer):
    from tests.test_gui_screenshots import _grab
    from app.gui_theme import apply_theme
    window, gui = explorer
    apply_theme(window, type(gui), 'Dark')
    window.show_filter_bar(False)
    show_resources(window)
    window.geometry('1000x760+30+30')
    window.update()
    _grab(gui, '83-resource-browser', widget=window)
    window.geometry('620x520+30+30')
    window.resource_query.set('life science')
    window.resource_tree.selection_set('europe_pmc')
    window.resource_selected()
    window.update()
    _grab(gui, '84-resource-browser-compact', widget=window)
