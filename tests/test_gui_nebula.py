"""Appearance selection remains usable on short displays with enlarged text."""
from types import SimpleNamespace
import time

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from app.gui_theme import (Palette, appearance_dialog, apply_text_scale, apply_theme,
                           style_controls, style_hayat_buttons)  # noqa: E402
from tests.conftest import close_window, open_window  # noqa: E402


def settle(root):
    deadline = time.monotonic() + .1
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.005)


@pytest.mark.parametrize("scale", (1.0, 1.5))
def test_compact_appearance_keeps_footer_visible_and_reveals_focused_controls(shared_tk, scale):
    window = open_window(shared_tk, "800x560-4000-4000")

    class PreviewGUI(Palette):
        def __init__(self):
            self.root = window
            self.depth_nav = SimpleNamespace(motion=False)
            self._theme_name = "Nebula"
            self._text_scale = scale

        def _set_text_scale(self, value):
            self._text_scale = value
            apply_text_scale(self.root, value)

        def _set_workspace_motion(self, enabled):
            self.depth_nav.motion = enabled

        def _save_appearance(self, **_changes):
            return True

    gui = PreviewGUI()
    dialog = None
    try:
        ttk.Style(window).theme_use("clam")
        apply_theme(window, PreviewGUI, "Nebula")
        gui._set_text_scale(scale)
        dialog = appearance_dialog(gui)
        dialog.geometry("740x420-4000-4000")
        settle(dialog)
        column = dialog._appearance_column
        assert column.body.winfo_height() > column.canvas.winfo_height()
        footer = next(child for child in dialog.winfo_children()
                      if any(isinstance(button, ttk.Button) for button in child.winfo_children()))
        buttons = footer.winfo_children()
        assert {button.cget("text") for button in buttons} == {"Save appearance", "Cancel"}
        footer_y = footer.winfo_y()
        for button in buttons:
            assert button.winfo_viewable()
            assert button.winfo_rooty() >= dialog.winfo_rooty()
            assert button.winfo_rooty() + button.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        controls = list(descendants(column.body))
        glass = next(control for control in controls
                     if isinstance(control, ttk.Radiobutton) and control.cget("text") == "Glass")
        transparency = next(control for control in controls if isinstance(control, ttk.Scale))
        aurora = next(control for control in controls
                     if isinstance(control, ttk.Radiobutton) and control.cget("text") == "Aurora")
        positions = []
        for control in (glass, transparency, aurora):
            # FocusIn is the same reveal route used by keyboard traversal; no
            # focus_force is needed against another concurrently tested window.
            control.event_generate("<FocusIn>")
            settle(dialog)
            top = control.winfo_rooty() - column.body.winfo_rooty()
            visible = column.canvas.canvasy(0)
            assert top >= visible - 2
            assert top + control.winfo_height() <= visible + column.canvas.winfo_height() + 2
            assert footer.winfo_y() == footer_y
            positions.append(column.canvas.yview()[0])
        assert positions[1] > positions[2], "Returning to the first theme must scroll the form back upward."
    finally:
        if dialog is not None and dialog.winfo_exists():
            dialog._appearance_cancel()
        apply_text_scale(window, 1.0)
        close_window(window)
        style_controls(shared_tk, Palette)
        style_hayat_buttons(shared_tk, Palette)
