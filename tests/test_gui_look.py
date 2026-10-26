"""The 2026-10-25 look pass: colour roles, colour emoji in chat, window entrance, grouped live state."""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from app import gui_emoji, gui_motion  # noqa: E402

windows_emoji = pytest.mark.skipif(gui_emoji._font_path() is None, reason="needs Segoe UI Emoji (Windows)")


def test_colour_roles_keep_gold_for_the_main_action_and_blue_for_the_chosen_option(shared_tk):
    from app.gui_theme import Palette, style_controls, style_hayat_buttons

    style_controls(shared_tk, Palette)
    style_hayat_buttons(shared_tk, Palette)
    style = ttk.Style(shared_tk)
    # The chosen tab opens into its page: not a solid gold block beside a gold Save button.
    assert style.lookup("TNotebook.Tab", "background", ("selected",)) == Palette.BLACK
    assert style.lookup("TNotebook.Tab", "foreground", ("selected",)) == Palette.GOLD_SOFT
    assert style.lookup("Segment.Toolbutton", "background", ("selected",)) == Palette.ROYAL
    assert style.lookup("HayatAccent.TButton", "background") == Palette.GOLD
    chosen = style.lookup("HayatChosen.TButton", "background")
    assert chosen not in {Palette.GOLD, Palette.PANEL_2} and style.lookup("HayatChosen.TButton", "foreground") == Palette.WHITE
    # Fields are recessed with a visible edge; buttons are raised. They shared one fill before.
    assert style.lookup("TEntry", "fieldbackground") == Palette.PANEL != style.lookup("TButton", "background")
    assert style.lookup("TEntry", "bordercolor", ("focus",)) == Palette.GOLD


def test_headings_use_the_display_face_when_windows_has_it(monkeypatch):
    import importlib

    from app import gui_fonts

    monkeypatch.setattr(sys, "platform", "linux")
    assert importlib.reload(gui_fonts).DISPLAY == "Segoe UI Semibold"
    monkeypatch.undo()
    importlib.reload(gui_fonts)
    if gui_fonts._installed("SegUIVar.ttf"):
        assert gui_fonts.DISPLAY == "Segoe UI Variable Display Semib"


def test_emoji_detection_leaves_interface_symbols_as_text():
    assert gui_emoji.has_emoji("Launch day 🚀") and gui_emoji.has_emoji("Done ✅") and gui_emoji.has_emoji("⭐")
    for symbol in "✓★☑▸⚙≡◇⌕↻":
        assert not gui_emoji.has_emoji(symbol), symbol


@windows_emoji
def test_chat_text_shows_emoji_in_colour_and_keeps_the_characters(shared_tk):
    text = tk.Text(shared_tk, font=("Segoe UI", 11))
    try:
        gui_emoji.insert(text, "end", "Great work 🎉 keep going 🚀\n", ("assistant",))
        assert len(text.image_names()) == 2
        # Copying, searching and saving still see the emoji: the characters stay, hidden behind the pictures.
        assert text.get("1.0", "end-1c") == "Great work 🎉 keep going 🚀\n"
        assert text.tag_cget("emoji_text", "elide") in {"1", 1, True}
        picture = gui_emoji.emoji_image("🎉", 20)
        pixels = [picture.getpixel((x, y)) for x in range(20) for y in range(20)]
        colours = {pixel[:3] for pixel in pixels if pixel[3] > 200}
        assert len(colours) > 8  # a colour glyph, not a one-colour outline
        # A fixed index keeps the pieces in order instead of reversing them.
        text.insert("end", "[]")
        gui_emoji.insert(text, "2.1", "a⭐b", ())
        assert text.get("2.0", "end-1c") == "[a⭐b]"
        # Skin tones and joiners need a shaper Pillow lacks here: the base emoji shows alone.
        text.delete("1.0", "end")
        gui_emoji.insert(text, "end", "hi 👋🏽", ())
        assert text.get("1.0", "end-1c") == "hi 👋" and len(text.image_names()) >= 1
    finally:
        text.destroy()


def test_text_without_emoji_goes_in_unchanged(shared_tk, monkeypatch):
    text = tk.Text(shared_tk)
    calls = []
    monkeypatch.setattr(gui_emoji, "_photo", lambda *args: calls.append(args))
    try:
        gui_emoji.insert(text, "end", "plain ✓ text", "body")
        assert text.get("1.0", "end-1c") == "plain ✓ text" and calls == [] and text.tag_ranges("body")
    finally:
        text.destroy()


def test_new_windows_ease_in_and_end_fully_opaque(shared_tk, monkeypatch):
    monkeypatch.setitem(gui_motion.MOTION, "enabled", True)
    window = tk.Toplevel(shared_tk)
    try:
        if not gui_motion.fade_in(window, duration_ms=60):
            pytest.skip("this window system has no window transparency")
        assert float(window.wm_attributes("-alpha")) < 1.0
        deadline = time.monotonic() + 3
        while float(window.wm_attributes("-alpha")) < 1.0 and time.monotonic() < deadline:
            window.update()
        assert float(window.wm_attributes("-alpha")) == 1.0
        assert gui_motion.fade_in(window) is False  # once per window
    finally:
        window.destroy()


def test_motion_off_means_windows_appear_at_once(shared_tk, monkeypatch):
    monkeypatch.setitem(gui_motion.MOTION, "enabled", False)
    window = tk.Toplevel(shared_tk)
    try:
        assert gui_motion.fade_in(window) is False
        assert float(window.wm_attributes("-alpha")) == 1.0
    finally:
        window.destroy()


def test_live_state_strip_groups_by_state_so_it_fits_one_line(monkeypatch):
    from app import gui_activity

    parts = {"camera": "off", "mic": "on", "wifi": "off", "monitor": "stopped", "learning": "running", "privacy": "off"}
    monkeypatch.setattr(gui_activity, "_live_parts", lambda gui: parts)
    assert gui_activity.live_state_brief(SimpleNamespace()) == "On: mic, learning · Off: camera, Wi-Fi, feeds, privacy"
    parts.update(mic="paused", learning="stopped")
    assert gui_activity.live_state_brief(SimpleNamespace()) == "Paused: mic · Off: camera, Wi-Fi, feeds, learning, privacy"


def test_buttons_without_a_matching_icon_get_none(shared_tk):
    from app.gui_icons import apply_icons, icon_kind
    from app.gui_theme import Palette

    assert icon_kind("Previous") == "tool" and icon_kind("Previous", fallback="") == ""
    window = tk.Toplevel(shared_tk)
    try:
        plain, matched = ttk.Button(window, text="Previous"), ttk.Button(window, text="Find jobs")
        apply_icons(window, Palette)
        assert not plain.cget("image") and matched.cget("image")
    finally:
        window.destroy()


def test_opening_global_intelligence_does_not_turn_every_segment_gold(shared_tk):
    from app.gui_theme import Palette, style_controls

    style_controls(shared_tk, Palette)
    source = __import__("inspect").getsource(__import__("app.gui_intelligence", fromlist=["x"]))
    assert "('Segment.Toolbutton', (12, 5))" not in source
    assert ttk.Style(shared_tk).lookup("Chip.Toolbutton", "background", ("selected",)) == Palette.ROYAL
