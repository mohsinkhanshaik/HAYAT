"""The 2026-11-08 GUI audit: measured contrast in every appearance, keyboard reach, one gold action per area."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk  # noqa: E402

from app.gui_theme import THEMES, Palette, style_controls, style_hayat_buttons, tokens  # noqa: E402

SURFACES = ("BLACK", "PANEL", "PANEL_2")


def _luminance(colour: str) -> float:
    red, green, blue = (int(colour.lstrip("#")[index:index + 2], 16) / 255 for index in (0, 2, 4))

    def linear(channel: float) -> float:
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4

    return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue)


def contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("theme", list(THEMES))
def test_every_appearance_meets_wcag_aa_for_text_and_control_edges(theme):
    palette = tokens(theme)
    for surface in SURFACES:
        # Body text and secondary text (WCAG 1.4.3, 4.5:1).
        assert contrast(palette["WHITE"], palette[surface]) >= 4.5, (theme, "WHITE", surface)
        assert contrast(palette["MUTED"], palette[surface]) >= 4.5, (theme, "MUTED", surface)
        # The outline of a field, tick box or slider (WCAG 1.4.11, 3:1). LINE stays a quieter divider.
        assert contrast(palette["EDGE"], palette[surface]) >= 3.0, (theme, "EDGE", surface)
    # White text on a selected row/segment and on the stop button.
    assert contrast("#FFFFFF", palette["ROYAL"]) >= 4.5, theme
    assert contrast("#FFFFFF", palette["DANGER"]) >= 4.5, theme
    # Dark text on the gold main action.
    assert contrast(palette["BLACK"], palette["GOLD"]) >= 4.5, theme


@pytest.mark.parametrize("theme", list(THEMES))
def test_theme_colours_are_distinct_so_switching_appearance_maps_each_role_back(theme):
    # apply_theme recolours live widgets by value; two roles sharing one value would swap roles on the next switch.
    values = [value.lower() for key, value in tokens(theme).items() if key not in {"BG", "TEXT", "ACCENT", "SUCCESS", "WARNING"}]
    assert len(values) == len(set(values)), theme


def test_fields_use_the_control_edge_and_the_danger_style_comes_from_the_theme(shared_tk):
    style_controls(shared_tk, Palette)
    style_hayat_buttons(shared_tk, Palette)
    style = ttk.Style(shared_tk)
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        assert style.lookup(name, "bordercolor") == Palette.EDGE
    assert style.lookup("TCheckbutton", "upperbordercolor") == Palette.EDGE
    assert style.lookup("Hayat.Horizontal.TProgressbar", "bordercolor") == Palette.LINE
    for name in ("HayatDanger.TButton", "Emergency.TButton"):
        assert style.lookup(name, "background") == Palette.DANGER
    assert style.lookup("HayatCard.TButton", "anchor") == "w"


def test_an_older_palette_without_the_new_roles_still_styles(shared_tk):
    old = SimpleNamespace(**{key: value for key, value in vars(Palette).items() if key.isupper() and key not in {"EDGE", "DANGER"}})
    style_controls(shared_tk, old)
    style_hayat_buttons(shared_tk, old)
    assert ttk.Style(shared_tk).lookup("TEntry", "bordercolor") == old.LINE
    style_controls(shared_tk, Palette)
    style_hayat_buttons(shared_tk, Palette)


def test_a_clickable_label_is_reachable_and_runs_from_the_keyboard(shared_tk):
    from app.gui_a11y import activatable

    runs = []
    frame = tk.Frame(shared_tk, bg=Palette.PANEL)
    label = tk.Label(frame, text="Open wallet", bg=Palette.PANEL)
    try:
        activatable(label, lambda: runs.append("ran"), Palette)
        assert int(label.cget("takefocus")) == 1 and label.cget("cursor") == "hand2"
        # The ring is invisible until focus arrives, then gold; the layout does not move.
        assert label.cget("highlightbackground") == Palette.PANEL and label.cget("highlightcolor") == Palette.GOLD
        for sequence in ("<Return>", "<KP_Enter>", "<space>", "<Button-1>"):
            assert label.bind(sequence), sequence
        label._hayat_activate()
        label.event_generate("<Return>")  # bound handlers run without focus when called directly
        assert runs
    finally:
        frame.destroy()


def test_keys_only_leaves_an_existing_click_handler(shared_tk):
    from app.gui_a11y import activatable

    clicks = []
    tile = tk.Frame(shared_tk, bg=Palette.PANEL_2)
    try:
        tile.bind("<Button-1>", lambda _event: clicks.append("own"))
        before = tile.bind("<Button-1>")
        activatable(tile, lambda: clicks.append("key"), Palette, keys_only=True)
        assert tile.bind("<Button-1>") == before and tile.bind("<Return>")
    finally:
        tile.destroy()


def test_the_ability_guide_keeps_gold_for_opening_the_area_not_every_example(shared_tk):
    from app.gui_help import CapabilityGuide

    class GUI(Palette):
        root = shared_tk
        runtime = SimpleNamespace(registry=SimpleNamespace(names=lambda: []), model=None, voice=None,
                                  authority=SimpleNamespace(privacy_mode=False))

    guide = CapabilityGuide(GUI())
    try:
        guide.withdraw()
        tries = [child for row in guide.examples.winfo_children() for child in row.winfo_children()
                 if isinstance(child, ttk.Button)]
        assert tries and all(str(button.cget("style")) == "Hayat.TButton" for button in tries)
        assert str(guide.open_button.cget("style")) == "HayatAccent.TButton"
    finally:
        guide.destroy()


def test_home_task_cards_title_on_the_left_edge_like_their_text(shared_tk):
    from app.gui_guidance import TaskHome

    home = TaskHome(shared_tk, Palette, lambda _key: None)
    try:
        assert home.buttons and all(str(button.cget("style")) == "HayatCard.TButton" for button in home.buttons.values())
    finally:
        home.destroy()


def test_classic_entry_fields_get_the_same_visible_edge(shared_tk):
    from app.gui_theme import field_outline

    entry = tk.Entry(shared_tk, relief="flat", bg=Palette.PANEL)
    try:
        field_outline(entry, Palette)
        assert int(entry.cget("highlightthickness")) == 1
        assert entry.cget("highlightbackground") == Palette.EDGE and entry.cget("highlightcolor") == Palette.GOLD
        field_outline(entry, SimpleNamespace())  # a test palette without colours still works
    finally:
        entry.destroy()
