"""The local scientific instrument renders real measurements and owns its workers safely."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from tkinter import ttk

from app.gui_scientific_workbench import ScientificWorkbench
from app.gui_theme import Palette, apply_text_scale, apply_theme, style_controls, style_hayat_buttons
from tests.conftest import close_window, open_window


@pytest.fixture
def science(shared_tk):
    owner = open_window(shared_tk, "1000x720+20+20")

    class GUI(Palette):
        root = owner

    ttk.Style(owner).theme_use("clam")
    style_controls(owner, GUI)
    style_hayat_buttons(owner, GUI)
    window = ScientificWorkbench(GUI())

    def settle(done=None):
        deadline = time.monotonic() + 8
        quiet = time.monotonic() + .15
        while time.monotonic() < deadline:
            shared_tk.update()
            if done() if done else time.monotonic() >= quiet:
                return
            time.sleep(.005)
        pytest.fail("Scientific Workbench did not settle")

    settle()
    try:
        yield SimpleNamespace(window=window, settle=settle, tk=shared_tk, palette=GUI)
    finally:
        apply_text_scale(owner, 1.0)
        if not window._closed:
            window.close()
        close_window(owner)


def test_demo_renders_measured_profile_histogram_pairs_and_source(science):
    window = science.window
    assert window.result is None and not window.distribution.find_withtag("bar")
    assert str(window.export_button.cget("state")) == "disabled"
    assert window.load_demo()
    science.settle(lambda: window.result is not None)
    result = window.result
    assert result["demo"] is True and "DEMO DATA" in window.source.get()
    assert window.metric_values["rows"].get() == str(result["rows"])
    assert len(window.columns_tree.get_children()) == len(result["columns"])
    assert len(window.preview_tree.get_children()) == len(result["preview"])
    chosen = window._selected_column
    assert len(window.distribution.find_withtag("bar")) == len(chosen["histogram"])
    assert str(chosen["numeric_count"]) in window.column_detail.get("1.0", "end")
    assert str(window.export_button.cget("state")) == "normal"
    assert len(window.relationships_tree.get_children()) == len(result["correlations"])
    window.tabs.select(window.pages["Relationships"])
    window.relationships_tree.selection_set("pair-0")
    window._select_relationship()
    text = window.relationship_detail.get("1.0", "end")
    pair = result["correlations"][0]
    assert f"{pair['r']:+.5f}" in text and f"{pair['n']:,} paired rows" in text
    assert "does not measure causal effect" in text
    text_column = next(index for index, column in enumerate(result["columns"]) if column["kind"] == "text")
    window.columns_tree.selection_set(f"column-{text_column}")
    window._select_column()
    assert not window.distribution.find_withtag("bar")
    assert "Text column" in window.column_detail.get("1.0", "end")


def test_local_file_and_exports_share_the_measured_result(science, tmp_path):
    window = science.window
    path = tmp_path / "measurements.csv"
    path.write_text("sample,signal,response\na,1,3\nb,2,6\nc,,9\nd,4,12\n", encoding="utf-8")
    window.analyze_path(path)
    science.settle(lambda: window.result is not None)
    assert "LOCAL FILE" in window.source.get() and str(path) in window.source.get()
    assert window.metric_values["missing"].get() == "1"
    assert window.result["correlations"][0]["n"] == 3
    markdown = window.export_to(tmp_path / "report.md")
    exported = window.export_to(tmp_path / "measurements.json", format="json")
    assert "measurements.csv" in markdown.read_text(encoding="utf-8")
    assert json.loads(exported.read_text(encoding="utf-8")) == window.result
    assert not list(tmp_path.glob(".hayat-science-*.tmp"))
    original = path.read_bytes()
    with pytest.raises(ValueError, match="source CSV stays unchanged"):
        window.export_to(path)
    assert path.read_bytes() == original
    previous = window.result
    window.analyze_path(tmp_path / "missing.csv")
    science.settle(lambda: not window._busy)
    assert window.result is previous and "Could not inspect" in window.status.get()
    assert str(window.export_button.cget("state")) == "normal"


def test_cancel_and_destroy_do_not_publish_stale_worker_values(science, monkeypatch):
    import app.gui_scientific_workbench as module

    window = science.window
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    thread_ids = []

    def slow_profile(_path, *, cancelled):
        thread_ids.append(threading.get_ident())
        started.set()
        release.wait(4)
        finished.set()
        return module.demo_profile()  # deliberately ignores cancellation; UI must still discard it

    monkeypatch.setattr(module, "profile_csv", slow_profile)
    assert window.analyze_path("ignored.csv")
    science.settle(started.is_set)
    assert not window.load_demo()  # no overlapping job can be started from the API either
    window.cancel()
    assert window._busy and "Cancelling" in window.status.get()
    release.set()
    science.settle(lambda: not window._busy)
    assert window.result is None and "cancelled" in window.status.get()
    assert thread_ids == [thread_ids[0]] and thread_ids[0] != threading.get_ident()
    started.clear()
    release.clear()
    finished.clear()
    window.analyze_path("ignored-again.csv")
    science.settle(started.is_set)
    window.close()
    release.set()
    science.settle(finished.is_set)
    assert window._closed and window._poll_handle is None


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_small_window_keeps_actions_and_measurements_reachable(science, scale):
    window = science.window
    window.geometry("760x520+20+20")
    apply_text_scale(window, scale)
    window.load_demo()
    science.settle(lambda: window.result is not None)
    science.settle()
    right = window.winfo_rootx() + window.winfo_width()
    for control in (window.open_button, window.demo_button, window.cancel_button, window.export_button):
        assert control.winfo_viewable()
        assert window.winfo_rootx() <= control.winfo_rootx() < right
        assert control.winfo_rootx() + control.winfo_width() <= right
    assert window.tabs.winfo_height() > 140
    assert window.columns_tree.winfo_height() > 75
    assert window.distribution.winfo_width() > 190 and window.distribution.winfo_height() >= 110
    assert window.column_detail.winfo_height() >= 50
    assert not window.selection_label.winfo_ismapped()
    assert window.status_label.winfo_height() > 20
    assert window.column_detail.winfo_rooty() + window.column_detail.winfo_height() <= window.pages['Profile'].winfo_rooty() + window.pages['Profile'].winfo_height()
    for caption in window.metric_labels:
        required = int(caption.tk.call('font', 'measure', caption.cget('font'), caption.cget('text')))
        assert required <= caption.winfo_width(), f"Clipped metric caption: {caption.cget('text')}"
    heading_font = ttk.Style(window).lookup('Treeview.Heading', 'font')
    for key in window.columns_tree.cget('columns'):
        required = int(window.tk.call('font', 'measure', heading_font, window.columns_tree.heading(key, 'text')))
        assert int(window.columns_tree.column(key, 'width')) >= required + 16
    # A very long input header stays inspectable in the scrolling details, while its title fits one line.
    original = window._selected_column['name']
    window._selected_column['name'] = 'A very long measurement column ' * 20
    window._select_column()
    science.settle()
    title_width = int(window.tk.call('font', 'measure', window.selected_label.cget('font'), window.selected.get()))
    assert title_width <= window.selected_label.winfo_width() - 4 and window.selected.get().endswith('…')
    assert 'A very long measurement column' in window.column_detail.get('1.0', 'end')
    window._selected_column['name'] = original
    for page in window.pages.values():
        window.tabs.select(page)
        science.settle()
        assert page.winfo_viewable() and page.winfo_height() > 100


def test_custom_histogram_uses_current_appearance(science):
    window = science.window
    window.load_demo()
    science.settle(lambda: window.result is not None)
    for theme in ("Light", "High contrast", "Aurora"):
        apply_theme(window, science.palette, theme)
        science.settle()
        colours = {window.distribution.itemcget(item, "fill") for item in window.distribution.find_withtag("bar")}
        assert colours <= {science.palette.GOLD, science.palette.ROYAL_SOFT}
        assert window.distribution.cget("bg") == science.palette.PANEL


def test_direct_destroy_cancels_poll_without_tcl_callbacks(science):
    window = science.window
    errors = []
    previous = science.tk.report_callback_exception
    science.tk.report_callback_exception = lambda *args: errors.append(args)
    try:
        window.destroy()
        science.settle()
        assert window._closed and window._poll_handle is None
        assert not errors
    finally:
        science.tk.report_callback_exception = previous
