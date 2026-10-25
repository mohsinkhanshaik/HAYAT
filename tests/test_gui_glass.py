"""The Glass appearance (app/gui_glass.py): frosted panels, an aurora behind them, matching title bars."""
from __future__ import annotations

import re
from types import SimpleNamespace

from app.gui_glass import GLASS, AuroraBackdrop, aurora_image, draw_glass_surface, is_glass, style_title_bar
from app.gui_theme import DARK, THEMES, tokens
from tests.conftest import close_window, open_window


def _palette(name: str) -> SimpleNamespace:
    return SimpleNamespace(**tokens(name))


def test_glass_is_an_appearance_with_distinct_valid_colours():
    assert "Glass" in THEMES
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", value) for value in GLASS.values())
    # Switching appearances maps each old colour to a new one, so no two may share a value.
    assert len({value.lower() for value in GLASS.values()}) == len(GLASS)
    assert set(GLASS) <= set(DARK)


def test_only_the_glass_palette_counts_as_glass():
    assert is_glass(_palette("Glass"))
    assert not is_glass(_palette("Dark")) and not is_glass(_palette("Light"))


def test_a_glass_panel_gets_a_sheen_frost_and_aurora_and_other_appearances_are_untouched(shared_tk):
    import tkinter as tk

    window = open_window(shared_tk, "400x200+0+0")
    try:
        canvas = tk.Canvas(window, width=320, height=120)
        canvas.pack()
        window.update()
        assert not draw_glass_surface(canvas, 320, 120, _palette("Dark"))
        assert canvas.find_withtag("surface") == ()
        assert draw_glass_surface(canvas, 320, 120, _palette("Glass"))
        for tag in ("glass-sheen", "glass-frost", "glass-aurora"):
            assert canvas.find_withtag(tag), tag
    finally:
        close_window(window)


def test_the_aurora_glows_rather_than_being_flat(monkeypatch):
    from app import gui_glass

    monkeypatch.setattr(gui_glass, "wallpaper_path", lambda: None)  # the plain aurora, not this desktop's picture
    gui_glass._BACKDROPS.clear()
    image = aurora_image((800, 450), (800, 450), (0, 0), _palette("Glass")).convert("RGB")
    assert image.size == (800, 450)
    cyan_corner, violet_corner = image.getpixel((90, 80)), image.getpixel((700, 360))
    assert cyan_corner[1] > cyan_corner[0]          # the cyan light, top left
    assert violet_corner[2] > violet_corner[1]      # the violet light, bottom right
    assert sum(abs(a - b) for a, b in zip(cyan_corner, violet_corner)) > 20


def test_the_backdrop_shows_only_in_glass(shared_tk):
    import tkinter as tk

    window = open_window(shared_tk, "300x200+0+0")
    try:
        frame = tk.Frame(window, width=300, height=200)
        frame.pack(fill="both", expand=True)
        palette = SimpleNamespace(**tokens("Glass"))
        backdrop = AuroraBackdrop(window, frame, palette)
        window.update()
        backdrop.paint()
        assert backdrop.label.winfo_manager() == "place" and backdrop.photo is not None
        for key, value in tokens("Dark").items():
            setattr(palette, key, value)
        backdrop.paint()
        assert backdrop.label.winfo_manager() == ""
    finally:
        close_window(window)


def test_title_bars_are_styled_on_windows_and_skipped_elsewhere(shared_tk, monkeypatch):
    import sys

    window = open_window(shared_tk, "200x100+0+0")
    try:
        if sys.platform == "win32":
            assert style_title_bar(window, _palette("Glass")) is True
        monkeypatch.setattr(sys, "platform", "linux")
        assert style_title_bar(window, _palette("Glass")) is False
    finally:
        close_window(window)


def test_only_the_glass_window_is_a_little_see_through(shared_tk, monkeypatch, tmp_path):
    from app import gui_glass

    monkeypatch.setattr(gui_glass, "glass_opacity", lambda root=None: 0.97)
    window = open_window(shared_tk, "200x100+0+0")
    try:
        assert gui_glass.apply_window_glass(window, _palette("Glass"))["alpha"] == 0.97
        assert abs(float(window.attributes("-alpha")) - 0.97) < 0.01
        assert gui_glass.apply_window_glass(window, _palette("Dark"))["alpha"] == 1.0
        assert abs(float(window.attributes("-alpha")) - 1.0) < 0.01
    finally:
        close_window(window)


def test_the_saved_opacity_is_clamped_to_stay_readable(monkeypatch, tmp_path):
    import json

    from app import config, gui_glass

    memory = tmp_path / "data" / "memory"
    memory.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (memory / "appearance.json").write_text(json.dumps({"glass_opacity": 0.1}), encoding="utf-8")
    assert gui_glass.glass_opacity() == gui_glass.MIN_GLASS_OPACITY
    (memory / "appearance.json").write_text(json.dumps({"glass_opacity": 0.5}), encoding="utf-8")
    assert gui_glass.glass_opacity() == 0.5
    (memory / "appearance.json").write_text(json.dumps({"glass_opacity": 0.92}), encoding="utf-8")
    assert gui_glass.glass_opacity() == 0.92
    (memory / "appearance.json").write_text("{}", encoding="utf-8")
    assert gui_glass.glass_opacity() == gui_glass.GLASS_ALPHA


def test_glass_turns_backgrounds_into_live_frosted_glass_and_back(shared_tk, monkeypatch):
    """Plain transparency showed every window behind HAYAT sharply; a colour key plus acrylic frosts them."""
    import sys
    import tkinter as tk

    from app import gui_glass

    if sys.platform != "win32":
        return
    monkeypatch.setattr(gui_glass, "glass_opacity", lambda root=None: 1.0)
    window = open_window(shared_tk, "300x200+0+0")
    try:
        glass = _palette("Glass")
        backdrop = tk.Frame(window, bg=glass.BLACK, width=300, height=200)
        backdrop.pack()
        window.update()
        result = gui_glass.apply_window_glass(window, glass)
        if not result["acrylic"]:
            return  # this Windows build has no acrylic; plain solid glass is the fallback
        assert gui_glass.live_glass_active()
        assert str(window.attributes("-transparentcolor")).lower() == gui_glass.GLASS_KEY.lower()
        assert backdrop.cget("bg").lower() == gui_glass.GLASS_KEY.lower()
        gui_glass.apply_window_glass(window, _palette("Dark"))
        assert not gui_glass.live_glass_active()
        assert str(window.attributes("-transparentcolor")) == ""
        assert backdrop.cget("bg").lower() == _palette("Dark").BLACK.lower()  # the current appearance's backdrop
    finally:
        gui_glass._LIVE["active"] = False
        close_window(window)


def test_frosted_glass_is_made_from_the_wallpaper_when_there_is_one(monkeypatch, tmp_path):
    from PIL import Image

    from app import gui_glass

    picture = tmp_path / "wall.png"
    Image.new("RGB", (400, 240), (200, 40, 60)).save(picture)
    monkeypatch.setattr(gui_glass, "wallpaper_path", lambda: picture)
    gui_glass._BACKDROPS.clear()
    frosted = gui_glass.window_backdrop((320, 200), _palette("Glass")).convert("RGB")
    red, green, blue = frosted.getpixel((160, 100))
    assert red > green and red > 40          # the wallpaper's colour comes through the frost
    assert gui_glass.aurora_image((50, 40), (320, 200), (10, 10), _palette("Glass")).size == (50, 40)
