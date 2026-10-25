"""A beginner can see every ability, try one, and learn what each button does."""
import tkinter as tk
from tkinter import ttk

from app.gui_help import BUTTON_HELP, Tooltip, attach_tooltips
from app.gui_theme import Palette


def test_tooltips_attach_to_known_buttons_once(shared_tk):
    frame = tk.Frame(shared_tk)
    known = ttk.Button(frame, text="Send")
    unknown = ttk.Button(frame, text="Something new")
    assert attach_tooltips(frame, Palette) == 1
    assert attach_tooltips(frame, Palette) == 0  # never doubled
    tip = known._hayat_tooltip
    assert tip.text == BUTTON_HELP["Send"] and getattr(unknown, "_hayat_tooltip", None) is None
    frame.destroy()


def test_a_tooltip_shows_and_hides(shared_tk):
    from tests.conftest import open_window, close_window
    window = open_window(shared_tk, "320x200+20+20")
    button = ttk.Button(window, text="Help")
    button.pack()
    window.update()
    tip = Tooltip(button, "Explains itself.", Palette)
    tip.show()
    assert tip.tip is not None and tip.tip.winfo_exists()
    tip.hide()
    assert tip.tip is None
    button.destroy()
    close_window(window)


def test_tooltip_does_not_open_for_a_hidden_control(shared_tk):
    button = ttk.Button(shared_tk, text="Help")
    tip = Tooltip(button, "Explains itself.", Palette)
    tip.show()
    assert tip.tip is None
    button.destroy()


def test_every_tooltip_is_a_plain_sentence():
    for text, explanation in BUTTON_HELP.items():
        assert explanation.endswith((".", ")")) and "_" not in explanation, text
