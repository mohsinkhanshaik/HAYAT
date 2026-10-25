r"""Layout, keyboard and long-content baselines at each Windows display scaling.

HAYAT runs DPI-unaware, so Windows hands it a smaller logical screen as the
scaling goes up: a 1920x1080 panel is 1536x864 at 125 %, 1280x720 at 150 % and
960x540 at 200 %. Each case lays the real main window out at the size HAYAT
would open on that screen and checks that the controls needed to use it are
fully inside the window, reachable with Tab, and still there after a long
report, a failure and a batch of attachments.

    $env:HAYAT_SHOTS = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_gui_baselines.py

also captures each layout to .test-runs/gui-baselines/ for a visual review.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

tkinter = pytest.importorskip("tkinter")

# Display scaling -> (logical screen of a 1920x1080 panel, HAYAT text size).
SCALINGS = {
    "100": ((1920, 1080), 1.0),
    "125": ((1536, 864), 1.0),
    "125-large-text": ((1536, 864), 1.3),
    "150": ((1280, 720), 1.0),
    "200": ((960, 540), 1.0),
}
SHOTS = Path(__file__).resolve().parents[1] / ".test-runs" / "gui-baselines"


@pytest.fixture(scope="module")
def window():
    from tests.test_gui import _build_window, _release
    built = _build_window()
    try:
        yield built
    finally:
        _release(built)


def _pump(gui, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        gui.root.update()
        time.sleep(0.02)


def _essentials(gui):
    controls = {"message box": gui.input, "Send": gui.send_button, "conversation": gui.chat,
                "Attach": gui.composer_buttons["Attach"], "Mic": gui.composer_buttons["Mic"]}
    for index, item in enumerate(gui._header_actions.items[:gui._header_actions.max_items or None]):
        controls[f"header action {index + 1}"] = item
    return controls


def _outside(gui, controls):
    """Controls that are hidden or not fully inside the window."""
    root = gui.root
    left, top = root.winfo_rootx(), root.winfo_rooty()
    right, bottom = left + root.winfo_width(), top + root.winfo_height()
    problems = []
    for name, widget in controls.items():
        if not widget.winfo_ismapped():
            problems.append(f"{name} is not shown")
            continue
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        if x < left or y < top or x + widget.winfo_width() > right or y + widget.winfo_height() > bottom:
            problems.append(f"{name} is cut off at {widget.winfo_width()}x{widget.winfo_height()}+{x - left}+{y - top}")
    return problems


def _tab_order(gui, start, limit=600):
    seen, widget = [], start
    for _ in range(limit):
        widget = widget.tk_focusNext()
        if widget is None or widget is start or widget in seen:
            break
        seen.append(widget)
    return seen


def _long_content(gui):
    from app.gui_attachments import AttachmentBatchCard
    report = "\n".join(f"{index}. Finding {index}: " + "evidence and its source " * 6 for index in range(1, 160))
    gui._append("assistant", "HAYAT\n" + report + "\nhttps://example.org/" + "a-very-long-unbroken-path-" * 20)
    gui._append("error", "The research provider failed: ConnectError after 3 retries; nothing was inferred.")
    paths = [f"C:/Users/owner/Documents/Quarterly report draft number {index} with a long name.pdf"
             for index in range(12)]
    card = AttachmentBatchCard(gui.chat, gui, paths)
    gui._evidence_cards.append(card)
    gui.chat.configure(state="normal")
    gui.chat.window_create("end", window=card, padx=8, pady=8)
    gui.chat.insert("end", "\n")
    gui.chat.configure(state="disabled")
    gui.chat.see("end")


@pytest.mark.parametrize("case", list(SCALINGS))
def test_main_window_fits_is_keyboard_reachable_and_survives_long_content(window, case):
    from app.gui import minimum_size, window_placement
    from app.gui_theme import apply_text_scale
    gui = window
    screen, text_scale = SCALINGS[case]
    geometry, _ = window_placement(None, screen)
    size = geometry.split("+", 1)[0]
    width, height = (int(value) for value in size.split("x"))
    assert width <= screen[0] and height <= screen[1] - 40, f"{geometry} does not fit {screen}"
    previous_scale = getattr(gui.root, "_hayat_text_scale", 1.0)
    gui.root.minsize(*minimum_size(screen))
    apply_text_scale(gui.root, text_scale)
    shots = os.environ.get("HAYAT_SHOTS") == "1"
    gui.root.geometry(size + ("+0+0" if shots else "-4000-4000"))
    gui.root.deiconify()
    try:
        gui.show_view("chat")
        _pump(gui, 0.6)
        assert (gui.root.winfo_width(), gui.root.winfo_height()) == (width, height)
        assert not _outside(gui, _essentials(gui)), _outside(gui, _essentials(gui))
        assert gui.chat.winfo_height() >= 110 and gui.chat.winfo_width() >= 400

        order = _tab_order(gui, gui.input)
        assert gui.send_button in order, "Send cannot be reached with Tab from the message box"
        unreachable = [name for name, widget in _essentials(gui).items()
                       if widget is not gui.input and widget.winfo_ismapped() and widget not in order
                       and str(widget.cget("takefocus")) != "0" and widget is not gui.chat]
        assert not unreachable, f"Not reachable with Tab: {unreachable}"

        _long_content(gui)
        _pump(gui, 0.6)
        assert not _outside(gui, _essentials(gui)), _outside(gui, _essentials(gui))
        assert gui.chat.xview() == (0.0, 1.0), "A long line made the conversation scroll sideways"
        assert gui.chat.winfo_height() >= 110
        if shots:
            from tests.test_gui_screenshots import _grab
            SHOTS.mkdir(parents=True, exist_ok=True)
            _grab(gui, f"../gui-baselines/{case}")
            assert (SHOTS / f"{case}.png").is_file()
    finally:
        apply_text_scale(gui.root, previous_scale)
        gui.root.withdraw()
        gui.chat.configure(state="normal")
        gui.chat.delete("1.0", "end")
        gui.chat.configure(state="disabled")
        gui.root.update()


def test_a_very_short_window_slims_its_rows_and_restores_them_when_taller(window):
    gui = window
    gui.root.minsize(*__import__("app.gui", fromlist=["minimum_size"]).minimum_size((960, 540)))
    gui.root.geometry("940x480-4000-4000")
    gui.root.deiconify()
    try:
        _pump(gui, 0.5)
        assert gui._very_short and not gui._status_bar.winfo_manager()
        assert int(gui.input.cget("height")) == 1 and not gui._header_clock_label.winfo_ismapped()
        gui.root.geometry("1352x760-4000-4000")
        _pump(gui, 0.5)
        assert not gui._very_short and gui._status_bar.winfo_ismapped()
        assert int(gui.input.cget("height")) == 2 and gui._header_clock_label.winfo_ismapped()
        # The status bar is back along the bottom edge, under the workspace.
        assert gui._status_bar.winfo_rooty() + gui._status_bar.winfo_height() >=             gui.root.winfo_rooty() + gui.root.winfo_height() - 2
    finally:
        gui.root.withdraw()
        gui.root.update()


def test_attachment_card_rows_share_the_card_colour_and_keep_the_extension(shared_tk):
    import tkinter as tk
    from app.gui_attachments import AttachmentBatchCard, short_name
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    assert short_name("notes.txt") == "notes.txt"
    long = short_name("Quarterly report draft number 0 with a long name.pdf")
    assert len(long) == 38 and long.endswith("name.pdf") and "\u2026" in long
    root = open_window(shared_tk, hidden=True)
    try:
        card = AttachmentBatchCard(root, Palette, [f"C:/files/Quarterly report draft {index} with a long name.pdf"
                                                   for index in range(12)])
        labels = [widget for row in card.winfo_children() for widget in ([row] + row.winfo_children())
                  if isinstance(widget, tk.Label)]
        assert labels and all(str(label.cget("bg")) == str(card.cget("bg")) for label in labels)
        assert any(label.cget("text") == "And 2 more files" for label in labels)
    finally:
        close_window(root)
