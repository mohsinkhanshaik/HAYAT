"""The widgets the window is assembled from, tested with numbers not runtimes."""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from app.gui_panels import (
    Gauge, InfoCard, LiveConsole, ModelChips, NavItem, ScrollableColumn, StatusPill,
    human_bytes, human_duration,
)


class Palette:
    BLACK = "#07080A"
    PANEL = "#101216"
    PANEL_2 = "#191C22"
    LINE = "#23272F"
    WHITE = "#F4F6F8"
    MUTED = "#8A93A0"
    GOLD = "#D4A82C"
    GOLD_SOFT = "#E8C765"
    ROYAL = "#2F5BD9"
    ROYAL_SOFT = "#7FA0F0"
    OLIVE = "#7E9B3F"
    OLIVE_SOFT = "#A8C368"
    ALERT = "#E2574C"


@pytest.fixture(scope="module")
def _interpreter(shared_tk):
    """One Tk interpreter for the module.

    Creating and destroying a root per test exhausts Tk part-way through --
    it starts raising `invalid command name "tcl_findLibrary"`, which the
    fixture then reported as "no display available". Tests were being skipped
    for a reason that had nothing to do with displays.
    """
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, hidden=True)
    yield window
    close_window(window)


@pytest.fixture
def root(_interpreter):
    """A clean frame inside the shared interpreter, discarded after the test."""
    holder = tk.Frame(_interpreter)
    yield holder
    try:
        holder.destroy()
    except tk.TclError:
        pass


# ------------------------------------------------------------------- gauges


def test_a_gauge_that_cannot_be_read_says_so_rather_than_showing_zero(root):
    """A dial resting at 0% and a dial that could not be read look identical,
    and only one of them is good news."""
    gauge = Gauge(root, Palette, "CPU")
    assert gauge.value is None
    assert gauge.itemcget(gauge._reading, "text") == "--"

    gauge.set(0.0)
    assert gauge.itemcget(gauge._reading, "text") == "0%"  # a real zero reads as one
    gauge.set(None)
    assert gauge.itemcget(gauge._reading, "text") == "--"


def test_a_gauge_changes_colour_as_it_gets_alarming(root):
    gauge = Gauge(root, Palette, "RAM")
    assert gauge.colour_for(20) == Palette.OLIVE
    assert gauge.colour_for(75) == Palette.GOLD
    assert gauge.colour_for(95) == Palette.ALERT


def test_a_gauge_never_sweeps_past_the_full_circle(root):
    gauge = Gauge(root, Palette, "GPU")
    for value in (-40, 0, 50, 100, 250):
        gauge.set(value)
        assert 0 <= abs(float(gauge.itemcget(gauge._arc, "extent"))) <= 360


# ------------------------------------------------------------------ console


def test_the_console_keeps_the_newest_lines_and_drops_the_oldest(root):
    console = LiveConsole(root, Palette)
    for index in range(console.MAX_LINES + 120):
        console.write("tools", f"line {index}")
    lines = int(console.text.index("end-1c").split(".")[0])
    assert lines <= console.MAX_LINES + 2
    assert f"line {console.MAX_LINES + 119}" in console.text.get("end-3l", "end")


def test_console_errors_are_coloured_apart_from_ordinary_lines(root):
    console = LiveConsole(root, Palette)
    console.write("tools", "ordinary")
    console.write("tools", "something broke", level="error")
    assert console.text.tag_cget("alert", "foreground") == Palette.ALERT
    assert console.text.tag_cget("body", "foreground") != Palette.ALERT


def test_the_console_can_be_cleared(root):
    console = LiveConsole(root, Palette)
    console.write("x", "y")
    console.clear()
    assert console.text.get("1.0", "end").strip() == ""


# ------------------------------------------------------------------- models


def test_models_shows_what_is_reachable_and_marks_the_one_in_use(root):
    chips = ModelChips(root, Palette)
    chips.set_models({"ollama": ["qwen3.5:latest", "llama3:latest"]}, active="qwen3.5:latest")
    assert chips.shown == ("ollama/qwen3.5:latest", "ollama/llama3:latest")

    labels = [child for frame in chips.winfo_children()
              for chip in frame.winfo_children()
              for child in chip.winfo_children()]
    assert any(label.cget("text") == "in use" for label in labels)


def test_no_reachable_model_is_stated_plainly(root):
    """A model listed in config that no service is serving is exactly the kind
    of thing this window exists to stop HAYAT claiming."""
    chips = ModelChips(root, Palette)
    chips.set_models({})
    assert chips.shown == ()
    texts = [child.cget("text") for child in chips.winfo_children()
             if isinstance(child, tk.Label)]
    assert any("No model service" in text for text in texts)


def test_an_unchanged_model_list_is_not_rebuilt(root):
    """Rebuilding every poll would make the panel flicker twice a minute."""
    chips = ModelChips(root, Palette)
    chips.set_models({"ollama": ["a", "b"]})
    before = [str(child) for child in chips.winfo_children()]
    chips.set_models({"ollama": ["a", "b"]})
    assert [str(child) for child in chips.winfo_children()] == before


def test_model_selection_refreshes_without_marking_other_sizes_selected(root):
    chips = ModelChips(root, Palette, rows=True)
    models = {"ollama": ["qwen:8b", "qwen:35b"]}
    chips.set_models(models, active="qwen:8b")
    chips.set_models(models, active="qwen:35b")
    rows = [chip for frame in chips.winfo_children() for chip in frame.winfo_children()]
    assert rows[0].winfo_children()[1].cget("text") == "ollama"
    assert rows[1].winfo_children()[1].cget("text") == "Selected · ollama"
    chips.destroy()


# ------------------------------------------------------------ nav and status


def test_selecting_a_nav_item_marks_it_in_gold(root):
    chosen: list[str] = []
    item = NavItem(root, Palette, "chat", "\U0001F4AC", "Chat", "New conversation", chosen.append)
    assert not item.selected

    item.select(True)
    assert item.selected
    assert item.marker.cget("bg") == Palette.GOLD
    assert item.title.cget("fg") == Palette.GOLD

    item.select(False)
    assert item.marker.cget("bg") == Palette.BLACK


def test_a_status_pill_shows_dead_as_muted_and_held_as_gold(root):
    pill = StatusPill(root, Palette, "Voice")
    pill.set("Ready", True)
    assert pill.dot.cget("fg") == Palette.OLIVE
    pill.set("Off", False)
    assert pill.dot.cget("fg") == Palette.MUTED
    pill.set("Unknown", None)
    assert pill.dot.cget("fg") == Palette.GOLD
    assert pill.value.get() == "Voice: Unknown"


# ------------------------------------------------------------------ scrolling


def test_the_right_column_scrolls_when_its_contents_do_not_fit(root):
    """Which panels got cut used to depend on pack order rather than on
    anything meaningful, and two of them simply never appeared."""
    column = ScrollableColumn(root, Palette, width=400)
    column.pack(fill="y")
    for index in range(12):
        tk.Label(column.body, text=f"panel {index}", height=6).pack(fill="x")
    column.adopt(column.body)
    root.update()

    top, bottom = column.canvas.yview()
    assert bottom - top < 1.0, "everything fits, so this test proves nothing"
    column.canvas.yview_moveto(1.0)
    assert column.canvas.yview()[1] == pytest.approx(1.0)


# -------------------------------------------------------------- plain values


@pytest.mark.parametrize("value,expected", [
    (None, "--"), (512, "512 B"), (2048, "2 KB"),
    (5 * 1024**2, "5 MB"), (42603331584, "39.7 GB"), (4000768323584, "3.6 TB"),
])
def test_bytes_are_shown_the_way_a_person_reads_them(value, expected):
    assert human_bytes(value) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0m"), (90, "1m"), (3600, "1h 0m"), (8130, "2h 15m"), (200000, "2d 7h"),
])
def test_uptime_is_shown_at_the_precision_anyone_cares_about(seconds, expected):
    assert human_duration(seconds) == expected


def test_an_info_card_falls_back_to_a_dash_rather_than_an_empty_box(root):
    card = InfoCard(root, Palette, "GPU")
    assert card.primary.get() == "--"
    card.set("", "nothing reported")
    assert card.primary.get() == "--"
    card.set("RTX 5060", "8 GB VRAM")
    assert card.primary.get() == "RTX 5060" and card.secondary.get() == "8 GB VRAM"


def test_column_preserves_nested_text_and_custom_wheel_handlers(root):
    column = ScrollableColumn(root, Palette)
    column.pack(fill='both', expand=True)
    text = tk.Text(column.body, height=3)
    text.pack()
    canvas = tk.Canvas(column.body, height=20)
    canvas.pack()
    handler = canvas.bind('<MouseWheel>', lambda _event: 'break')
    column.adopt(column.body)
    assert not text.bind('<MouseWheel>')
    assert handler in canvas.bind('<MouseWheel>')
    column.destroy()


def test_new_labels_in_a_column_scroll_without_another_adopt_pass(root):
    from app.gui_motion import MOTION, set_motion
    column = ScrollableColumn(root, Palette)
    column.pack(fill='both', expand=True)
    old_motion = MOTION['enabled']
    try:
        column.adopt(column.body)
        label = tk.Label(column.body, text='New findings', height=120)
        label.pack()
        root.update()
        set_motion(False)
        label.event_generate('<MouseWheel>', delta=-60)
        root.update()
        assert column.canvas.yview()[0] > 0
    finally:
        set_motion(old_motion)
        column.destroy()


def test_rollback_snapshots_follow_real_models_and_are_counted(root):
    chips = ModelChips(root, Palette, limit=3)
    chips.set_models({"ollama": ["hayat:rollback-ff12d890ba32", "hayat:latest", "hayat:rollback-b37be07bf345",
                                 "qwen3.5:latest", "llama3:latest"]})
    assert chips.shown[:3] == ("ollama/hayat:latest", "ollama/qwen3.5:latest", "ollama/llama3:latest")
    texts = [child.cget("text") for child in chips.winfo_children() if isinstance(child, tk.Label)]
    assert any("+ 2 more (2 rollback snapshots)" in text for text in texts)
    chips.destroy()
