import tkinter as tk

import pytest
from tkinter import ttk

from app.gui_theme import Palette, style_controls
from app.gui_window_switch import WindowSwitch, open_windows
from tests.conftest import open_window


@pytest.fixture
def gui(shared_tk):
    root = open_window(shared_tk, "1000x700-4000-4000")
    panel = Palette()
    panel.root = root
    style = ttk.Style(root)
    theme = style.theme_use()
    style.theme_use("clam")
    style_controls(root, panel)
    try:
        yield panel
    finally:
        style.theme_use(theme)
        root.destroy()


def test_open_windows_lists_visible_toplevels_sorted_and_skips_hidden(gui):
    a = tk.Toplevel(gui.root)
    a.title("Reverse engineering")
    b = tk.Toplevel(gui.root)
    b.title("Accounts")
    hidden = tk.Toplevel(gui.root)
    hidden.title("Hidden")
    hidden.withdraw()
    gui.root.update()
    try:
        titles = [title for _window, title in open_windows(gui.root)]
        assert "Reverse engineering" in titles and "Accounts" in titles
        assert "Hidden" not in titles  # withdrawn windows are skipped
        assert titles == sorted(titles, key=str.casefold)  # ordered by title
    finally:
        for window in (a, b, hidden):
            window.destroy()


def test_switch_filters_and_raises_the_chosen_window(gui):
    trade = tk.Toplevel(gui.root)
    trade.title("Trade Center")
    jobs = tk.Toplevel(gui.root)
    jobs.title("Jobs")
    gui.root.update()
    switch = WindowSwitch(gui)
    gui.root.update()
    try:
        # All open windows are listed (plus none hidden); typing narrows them.
        assert switch.listbox.size() >= 2
        switch.query.set("job")
        gui.root.update()
        shown = [switch.listbox.get(i) for i in range(switch.listbox.size())]
        assert shown == ["Jobs"]
        raised = []
        jobs.lift = lambda: raised.append("jobs")  # type: ignore[assignment]
        switch._choose()
        assert raised == ["jobs"] and not switch.winfo_exists()  # chosen window raised, switcher closed
    finally:
        for window in (trade, jobs):
            if window.winfo_exists():
                window.destroy()


def test_switch_reports_when_nothing_matches(gui):
    other = tk.Toplevel(gui.root)
    other.title("Trade Center")
    gui.root.update()
    switch = WindowSwitch(gui)
    try:
        switch.query.set("zzzz")
        gui.root.update()
        assert switch.listbox.size() == 0 and switch.empty.cget("text")
    finally:
        switch.close()
        other.destroy()
