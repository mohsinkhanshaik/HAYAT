"""The intelligence card beside chat moves and resizes without opening another window."""
import tkinter as tk
from types import SimpleNamespace
from tkinter import ttk

import pytest

from app.gui_intelligence_home import IntelligenceHomeCard
from app.gui_motion import MOTION, set_motion
from app.gui_panels import FoldMemory, ScrollableColumn
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from tests.conftest import open_window


@pytest.fixture
def card(shared_tk, tmp_path):
    root = open_window(shared_tk, '1100x800-4000-4000')
    root.configure(bg=Palette.BLACK)
    style = ttk.Style(root)
    previous = style.theme_use()
    style.theme_use('clam')
    style_controls(root, Palette)
    style_hayat_buttons(root, Palette)
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root = root
    gui._folds = FoldMemory(tmp_path / 'panels.json')
    gui.runtime = SimpleNamespace()
    gui._open_intelligence = lambda: None
    # Local snapshots are controlled here; no camera, network or model is started.
    center = tk.Frame(root, bg=Palette.PANEL)
    center.pack(side='left', fill='both', expand=True, padx=12, pady=12)
    tk.Label(center, text='Conversation', bg=Palette.PANEL, fg=Palette.WHITE).pack()
    gui.input = tk.Text(center, height=3)
    gui.input.pack(side='bottom', fill='x')
    rail = gui.right_rail = ScrollableColumn(root, Palette, width=400)
    rail.pack(side='right', fill='y')
    before = tk.Label(rail.body, text='Resources above', height=2)
    before.pack(fill='x')
    widget = IntelligenceHomeCard(rail.body, gui, pack={'fill': 'x', 'pady': (0, 8)}, show_map=True, movable=True)
    widget.close()  # deterministic display test: stop polling, then populate the real widgets
    after = tk.Label(rail.body, text='Other panels below', height=30)
    after.pack(fill='x')
    widget.headline.set('Global intelligence · World · 12 items')
    widget.summary.set('Local snapshots · 6 sources current\nCoverage is limited to connected sources.\n' * 8)
    root.update()
    try:
        yield widget, gui, before, after
    finally:
        style.theme_use(previous)
        root.destroy()


def test_inline_card_has_bounded_height_and_scrolls_over_its_map(card):
    widget, gui, _before, _after = card
    panel = widget.panel_layout
    assert widget.winfo_manager() == 'pack' and widget.dock_parent is gui.right_rail.body
    assert panel.viewport.winfo_height() == panel.DEFAULT_HEIGHT
    assert panel.canvas.yview()[1] < 1
    old_motion = MOTION['enabled']
    try:
        set_motion(False)
        outer = gui.right_rail.canvas.yview()
        widget.map.event_generate('<MouseWheel>', delta=-120)
        gui.root.update()
        assert panel.canvas.yview()[0] > 0
        assert gui.right_rail.canvas.yview() == outer
    finally:
        set_motion(old_motion)


def test_drag_floats_the_same_card_inside_main_window_and_dock_restores_its_slot(card):
    widget, gui, before, after = card
    panel = widget.panel_layout
    original_map = widget.map
    order = gui.right_rail.body.pack_slaves()
    title = widget.title_label
    x, y = title.winfo_rootx() + 10, title.winfo_rooty() + 10
    title.event_generate('<ButtonPress-1>', rootx=x, rooty=y)
    title.event_generate('<B1-Motion>', rootx=x - 420, rooty=y + 90)
    title.event_generate('<ButtonRelease-1>', rootx=x - 420, rooty=y + 90)
    gui.root.update()
    assert panel.floating and widget.winfo_manager() == 'place'
    assert widget.winfo_toplevel() is gui.root and widget.map is original_map
    assert panel.dock_button.winfo_ismapped()
    assert panel.dock_button.winfo_width() >= panel.dock_button.winfo_reqwidth()
    assert widget not in gui.right_rail.body.pack_slaves()
    panel.dock_button.invoke()
    gui.root.update()
    assert not panel.floating and gui.right_rail.body.pack_slaves() == order
    assert before.winfo_y() < widget.winfo_rooty() - gui.right_rail.body.winfo_rooty() < after.winfo_y()


def test_resize_works_docked_and_floating_and_clamps_to_main_window(card):
    widget, gui, _before, _after = card
    panel = widget.panel_layout
    panel.grip.event_generate('<ButtonPress-1>', rootx=500, rooty=400)
    panel.grip.event_generate('<B1-Motion>', rootx=500, rooty=340)
    panel.grip.event_generate('<ButtonRelease-1>', rootx=500, rooty=340)
    gui.root.update()
    assert panel.viewport.winfo_height() == panel.DEFAULT_HEIGHT - 60
    panel.float()
    gui.root.update()
    old_width, old_height = panel.box[2:]
    panel._start_resize(SimpleNamespace(x_root=500, y_root=400))
    panel._resize_to(SimpleNamespace(x_root=560, y_root=450))
    panel._stop_resize()
    gui.root.update()
    assert panel.box[2:] == (old_width + 60, old_height + 50)
    panel._place((-300, -400, 5000, 5000))
    gui.root.update()
    gui.root.geometry('700x500-4000-4000')
    gui.root.update()
    x, y, width, height = panel.box
    assert x >= 0 and y >= 0
    assert x + width <= gui.root.winfo_width() and y + height <= gui.root.winfo_height()
    assert panel.dock_button.winfo_ismapped()


def test_collapse_and_expand_keep_content_and_work_with_collapse_all(card):
    widget, gui, _before, _after = card
    panel = widget.panel_layout
    summary = widget.summary.get()
    gui._folds.fold_all()
    gui.root.update()
    assert panel.collapsed and not panel.viewport.winfo_ismapped()
    assert not widget.header_controls.winfo_ismapped()
    assert widget.winfo_height() < 140
    gui._folds.unfold_all()
    gui.root.update()
    assert not panel.collapsed and widget.summary.get() == summary
    assert widget.header_controls.winfo_ismapped()
    panel.float()
    gui.root.update()
    height = panel.box[3]
    panel.toggle_collapsed()
    gui.root.update()
    assert panel.box[3] < height and panel.dock_button.winfo_ismapped()
    panel.toggle_collapsed()
    gui.root.update()
    assert panel.box[3] == height and panel.viewport.winfo_ismapped()


def test_refresh_visibility_retains_docking_slot_or_floating_position(card):
    widget, gui, _before, _after = card
    panel = widget.panel_layout
    order = gui.right_rail.body.pack_slaves()
    panel.hide()
    panel.show()
    gui.root.update()
    assert gui.right_rail.body.pack_slaves() == order
    panel.float()
    gui.root.update()
    panel._place((90, 110, 420, 340))
    panel.hide()
    assert not widget.winfo_manager()
    panel.show()
    gui.root.update()
    assert panel.floating and panel.box == (90, 110, 420, 340)
    assert widget.winfo_manager() == 'place'


def test_sidebar_hiding_only_hides_the_docked_card(card):
    widget, gui, _before, _after = card
    gui.right_rail.pack_forget()
    gui.root.update()
    assert not widget.winfo_ismapped()
    gui.right_rail.pack(side='right', fill='y')
    gui.root.update()
    assert widget.winfo_ismapped()
    widget.panel_layout.float()
    gui.right_rail.pack_forget()
    gui.root.update()
    assert widget.winfo_ismapped()


def test_inline_panel_controls_remain_reachable(card):
    widget, gui, _before, _after = card
    assert widget.title_label.bind('<B1-Motion>')
    panel = widget.panel_layout
    panel.float()
    panel._place((220, 160, 360, 300))
    gui.root.update()
    for button in (panel.dock_button, panel.menu_button):
        assert button.winfo_ismapped()
        assert button.winfo_width() >= button.winfo_reqwidth()
        assert button.winfo_rootx() + button.winfo_width() <= widget.winfo_rootx() + widget.winfo_width()
