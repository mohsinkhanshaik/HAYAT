"""The actual Operator mount keeps contract controls reachable at normal and larger text."""
import os
import time
from pathlib import Path
from tkinter import ttk

import pytest

from app.gui_theme import apply_text_scale
from tests.test_gui import _window, gui  # noqa: F401


def settle(root):
    deadline = time.monotonic() + .2
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.01)


def controls(widget):
    for child in widget.winfo_children():
        if isinstance(child, (ttk.Combobox, ttk.Button, ttk.Checkbutton)):
            yield child
        yield from controls(child)


@pytest.mark.parametrize('geometry,scale', [('1440x900+20+20', 1.0), ('1440x900+20+20', 1.5),
                                           ('1100x740+20+20', 1.5)])
def test_mounted_contract_explorer_controls_and_evidence_fit(gui, geometry, scale):  # noqa: F811
    gui._operator_suppress_auto_refresh = True
    gui.show_view('operator')
    previous_motion = gui.brain_graph.motion
    try:
        gui.root.geometry(geometry)
        gui.root.deiconify()
        apply_text_scale(gui.root, scale)
        gui.brain_graph.set_motion(False)
        gui.show_view('operator')
        settle(gui.root)
        if not gui._brain_inspector_open:
            gui._toggle_brain_inspector()
        gui.brain_inspector_tabs.select(gui._brain_contract_page)
        settle(gui.root)
        explorer = gui.brain_contract_explorer
        explorer.tabs.select(0)
        settle(gui.root)
        left, top = explorer.winfo_rootx(), explorer.winfo_rooty()
        right, bottom = left + explorer.winfo_width(), top + explorer.winfo_height()
        for control in controls(explorer):
            if not control.winfo_viewable():
                continue  # the separate View & trace tab is deliberately hidden
            assert control.winfo_rootx() >= left
            assert control.winfo_rootx() + control.winfo_width() <= right, control
            assert control.winfo_rooty() + control.winfo_height() <= bottom, control
        assert explorer.links.winfo_height() >= 60
        explorer.tabs.select(explorer.evidence_page)
        settle(gui.root)
        assert explorer.detail.winfo_height() >= 60
        if os.environ.get('HAYAT_SHOTS') == '1':
            from PIL import ImageGrab
            folder = Path('.test-runs/gui-shots')
            folder.mkdir(parents=True, exist_ok=True)
            name = 'brain-mounted-' + geometry.split('+')[0] + '-' + str(scale)
            ImageGrab.grab(window=int(gui.root.wm_frame(), 16)).save(folder / (name + '.png'))
        explorer.tabs.select(explorer.settings)
        settle(gui.root)
        settings = explorer.settings
        for control in controls(settings):
            y = control.winfo_rooty() - settings.body.winfo_rooty()
            settings.canvas.yview_moveto(max(0, y + control.winfo_height() - settings.canvas.winfo_height()) /
                                         max(1, settings.body.winfo_height()))
            settle(gui.root)
            assert control.winfo_viewable()
            assert control.winfo_rootx() >= settings.canvas.winfo_rootx()
            assert control.winfo_rootx() + control.winfo_width() <= settings.canvas.winfo_rootx() + settings.canvas.winfo_width()
            assert control.winfo_rooty() + control.winfo_height() <= settings.canvas.winfo_rooty() + settings.canvas.winfo_height() + 1
        if os.environ.get('HAYAT_SHOTS') == '1':
            ImageGrab.grab(window=int(gui.root.wm_frame(), 16)).save(folder / (name + '-trace-controls.png'))
        explorer.tabs.select(0)
    finally:
        apply_text_scale(gui.root, 1.0)
        gui.brain_graph.set_motion(previous_motion)
        gui._operator_suppress_auto_refresh = False
