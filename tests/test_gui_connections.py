"""The linked inventory navigates actual tools without running their handlers."""
import os
import threading
import time
from pathlib import Path

from app.gui_features import FeaturesWindow
from app.gui_connections import check_label
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from tests.conftest import close_window, open_window
from tests.test_gui_discovery import build_gui


def settle(root, ready=lambda: True, timeout=4):
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        root.update()
        if ready() and time.monotonic() - started >= .15:
            return
        time.sleep(.01)
    assert ready(), 'The inventory or layout did not settle.'


def make_window(shared_tk, tmp_path):
    root = open_window(shared_tk, '1200x850+30+30')
    from tkinter import ttk
    ttk.Style(root).theme_use('clam')
    style_controls(root, Palette)
    style_hayat_buttons(root, Palette)
    gui = build_gui(root, tmp_path)
    called = []
    for name in ('model_discover', 'robustness_lab', 'code_defense'):
        gui.runtime.registry.register(name, 'Synthetic local research fixture.',
                                      {'type': 'object', 'properties': {}}, 'low', 'system_read',
                                      lambda: called.append('executed'))
    return root, gui, FeaturesWindow(gui), called


def test_catalog_relationships_open_exact_tools_and_keep_search_read_only(shared_tk, tmp_path):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        window.select_feature('area:research_lab')
        assert set(window.related.get_children()) == {'tool:model_discover', 'tool:robustness_lab', 'tool:code_defense'}
        window.related.selection_set('tool:model_discover')
        window.open_related()
        assert window.current.tool.name == 'model_discover'
        assert 'area:research_lab' in window.related.get_children()
        assert window.detail_tabs.tab(window.run_page, 'state') == 'normal'
        window.select_feature('area:research_lab')
        assert window.detail_tabs.tab(window.run_page, 'state') == 'disabled'
        window.query.set('injection guard')
        assert 'tool:robustness_lab' in window.shown
        assert not called and gui.activity_monitor.snapshot() == ([], 0)
    finally:
        window.destroy()
        gui.activity_monitor.close()
        close_window(root)


def test_snapshot_links_reach_the_same_catalog_and_show_unknown_checks(shared_tk, tmp_path):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        view = window.connections
        window.tabs.select(view)
        settle(root, lambda: view.snapshot is not None)
        assert view.snapshot['summary']['registered_tools'] == 4
        assert view.tree.item('research', 'values')[0] == 'Not checked'
        assert 'No system check recorded' in view.stamp.get()
        view.tree.selection_set('research')
        view.show()
        assert 'model_discover' in view.tool_tree.get_children()
        view.tool_tree.selection_set('model_discover')
        view.inspect_tool()
        assert window.tabs.select() == str(window.catalog_page)
        assert window.current.tool.name == 'model_discover'
        view.query.set('no matching subsystem')
        assert not view.tree.get_children()
        assert 'No matching' in view.title.get()
        assert not called and gui.activity_monitor.snapshot() == ([], 0)
    finally:
        window.destroy()
        gui.activity_monitor.close()
        close_window(root)


def test_recorded_checks_are_not_presented_as_current_readiness():
    assert check_label({}) == 'Not checked'
    assert check_label({'passed': True}) == 'Recorded pass'
    assert check_label({'passed': False}) == 'Needs attention'


def test_tool_evidence_and_run_controls_fit_with_large_text(shared_tk, tmp_path):
    from app.gui_theme import apply_text_scale
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        window.geometry('800x650+40+40')
        apply_text_scale(window, 1.5)
        window.select_tool('model_discover')
        window.detail_tabs.select(window.evidence_page)
        settle(root, lambda: window.connections.snapshot is not None)
        text = window.evidence_text.get('1.0', 'end')
        assert 'Recorded usage unavailable' in text and 'System check: not recorded' in text
        window.open_button.invoke()
        gui._open_research_lab.assert_called_once()
        window.detail_tabs.select(window.run_page)
        settle(root)
        assert window.arguments.winfo_viewable() and window.run_button.winfo_viewable()
        assert window.run_button.winfo_rooty() + window.run_button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
        if os.environ.get('HAYAT_SHOTS') == '1':
            from PIL import ImageGrab
            ImageGrab.grab(window=int(window.wm_frame(), 16)).save('.test-runs/gui-shots/connected-tools-large-text.png')
        assert not called
    finally:
        apply_text_scale(window, 1.0)
        window.destroy()
        gui.activity_monitor.close()
        close_window(root)


def test_closing_during_inventory_read_cancels_ui_delivery(shared_tk, tmp_path, monkeypatch):
    root, gui, window, _ = make_window(shared_tk, tmp_path)
    started, finish = threading.Event(), threading.Event()
    def blocked(_runtime):
        started.set()
        finish.wait(3)
        return {'nodes': [], 'tools': []}
    monkeypatch.setattr('app.system_insight.build_snapshot', blocked)
    try:
        view = window.connections
        view.refresh()
        assert started.wait(1)
        window.destroy()
        finish.set()
        root.update()
        assert view._closed and view._poll_id is None
    finally:
        finish.set()
        if window.winfo_exists():
            window.destroy()
        gui.activity_monitor.close()
        close_window(root)


def test_connected_catalog_layout_and_synthetic_captures(shared_tk, tmp_path):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        for width, height in ((1180, 800), (760, 600)):
            window.geometry(f'{width}x{height}+40+40')
            settle(root, lambda: window.winfo_width() == width)
            window.select_tool('model_discover')
            settle(root)
            assert window.tree.winfo_height() >= 90
            assert window.description.winfo_height() >= 40
            assert window.open_button.winfo_rooty() + window.open_button.winfo_height() <= window.winfo_rooty() + height
            if os.environ.get('HAYAT_SHOTS') == '1':
                from PIL import ImageGrab
                folder = Path('.test-runs/gui-shots')
                folder.mkdir(parents=True, exist_ok=True)
                ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / f'connected-tools-{width}.png')
            window.tabs.select(window.connections)
            settle(root, lambda: window.connections.snapshot is not None)
            window.connections.tree.selection_set('research')
            window.connections.show()
            settle(root)
            assert window.connections.tree.winfo_height() >= 90
            assert window.connections.canvas.winfo_height() >= 90
            button = window.connections.brain_button
            assert button.winfo_viewable() and button.winfo_rooty() + button.winfo_height() <= window.winfo_rooty() + height
            if os.environ.get('HAYAT_SHOTS') == '1':
                ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / f'system-connections-{width}.png')
        assert not called
    finally:
        window.destroy()
        gui.activity_monitor.close()
        close_window(root)
