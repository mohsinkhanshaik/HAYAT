"""Navigation filtering and bounded draft sizing on real Tk widgets."""
from __future__ import annotations

import tkinter as tk

import pytest

from app.gui_composer import ComposerSizing
from app.gui_navigation import WorkspaceNavigation
from app.gui_theme import Palette


@pytest.fixture
def window(shared_tk):
    root = tk.Toplevel(shared_tk)
    root.geometry("420x850-4000-4000")
    try:
        yield root
    finally:
        root.destroy()


def test_filter_matches_terms_and_aliases_without_opening_workspaces(window):
    opened = []
    views = [("chat", "", "Chat", "New conversation"), ("training", "", "Training", "Review examples"),
             ("models", "", "Models", "Local services"), ("health", "", "Health", "System checks")]
    nav = WorkspaceNavigation(window, Palette, views, opened.append)
    nav.pack(fill="both", expand=True)
    nav.items["training"].select(True)
    nav.query.set(" LORA train ")
    window.update_idletasks()
    assert nav.visible_keys == ["training"]
    assert opened == [] and nav.items["training"].selected
    nav.open_first()
    assert opened == ["training"]
    nav.query.set("no-such-workspace")
    window.update_idletasks()
    assert nav.empty.winfo_manager() == "pack"
    nav.open_first()
    assert opened == ["training"]
    nav.clear()
    window.update_idletasks()
    assert set(nav.visible_keys) == {"chat", "training", "models", "health"}
    assert nav.items["training"].selected
    assert not nav.empty.winfo_manager()
    assert nav.clear_button.instate(["disabled"])


def test_keyboard_focus_keeps_lower_navigation_entries_in_view(window):
    from app.gui import HayatGUI

    window.geometry("260x420-4000-4000")
    nav = WorkspaceNavigation(window, Palette, HayatGUI.VIEWS, lambda _: None)
    nav.pack(fill="both", expand=True)
    window.update_idletasks()
    nav.focus_result("settings")
    window.update_idletasks()
    assert nav.column.canvas.yview()[0] > 0
    nav.focus_result("chat")
    window.update_idletasks()
    assert nav.column.canvas.yview()[0] < .1


def test_long_and_wrapped_drafts_expand_then_shrink_without_changing_text(window):
    text = tk.Text(window, height=2, wrap="word", font=("Segoe UI", 12))
    text.pack(fill="x")
    sizing = ComposerSizing(text)
    window.update_idletasks()
    draft = "\n".join(f"Draft line {n}" for n in range(12))
    text.insert("1.0", draft)
    text.event_generate("<<Modified>>")
    window.update_idletasks()
    assert int(text.cget("height")) == 6
    assert text.get("1.0", "end-1c") == draft
    window.geometry("420x700-4000-4000")
    window.update()
    assert int(text.cget("height")) == 4
    text.delete("1.0", "end")
    text.insert("1.0", "short")
    text.event_generate("<<Modified>>")
    window.update_idletasks()
    assert int(text.cget("height")) == 2
    text.delete("1.0", "end")
    wrapped = "A longer draft wraps across several visual lines. " * 15
    text.insert("1.0", wrapped)
    text.event_generate("<<Modified>>")
    window.update_idletasks()
    assert int(text.cget("height")) == 4
    assert text.get("1.0", "end-1c") == wrapped
    text.destroy()
    assert sizing.closed and sizing.pending is None


def test_destroying_pending_composer_removes_only_its_own_root_callback(window):
    seen = []
    previous = window.bind("<Configure>", lambda event: seen.append(event), add="+")
    text = tk.Text(window)
    text.pack()
    sizing = ComposerSizing(text)
    assert sizing.pending is not None
    text.destroy()
    assert previous in window.bind("<Configure>")
    assert sizing.root_binding not in window.bind("<Configure>")
    assert sizing.closed and sizing.pending is None
