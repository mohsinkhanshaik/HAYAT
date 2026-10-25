"""Real Tk scrolling, paging and cancellable search controls."""
import threading
import os
from types import SimpleNamespace

import pytest

from tests.test_job_hunting_upgrade import hunting as hunting, jobs_window as jobs_window, pump
from tests.test_gui import own_gui as own_gui


def test_every_jobs_tab_scrolls_whole_page_without_global_wheel_binding(jobs_window):
    window = jobs_window
    window.geometry("680x480+0+0")
    window.update()
    for index, page in enumerate(window.pages):
        window.tabs.select(index)
        window.update()
        assert page.canvas.cget("yscrollcommand") and page.canvas.cget("xscrollcommand")
        assert page.body.winfo_width() >= 780
        page.canvas.xview_moveto(1)
        window.update()
        assert page.canvas.xview()[0] > 0
        # Saved matches load on a worker; when they land just after the jump the
        # page grows (a full run measured 0.981). Scroll again until the page
        # stops growing, then require the true bottom.
        for _ in range(5):
            height = page.body.winfo_reqheight()
            page.canvas.yview_moveto(1)
            window.update()
            if page.body.winfo_reqheight() == height:
                break
        assert page.canvas.yview()[1] >= .99
        if index in (0, 2, 5):
            assert page.canvas.yview()[0] > 0
        # A second visit retains an actual mapped body, not an empty canvas.
        window.tabs.select((index + 1) % 6)
        window.tabs.select(index)
        window.update()
        assert page.body.winfo_ismapped()


def test_list_and_detail_scrolling_are_independent_of_page_scroll(jobs_window):
    window = jobs_window
    rows = []
    template = next(iter(window.match_rows.values()))
    for n in range(250):
        rows.append({**template, "number": n + 1})
    window.show_matches({"matches": rows, "matching_total": 250, "counts": {"jobs": 250}, "profile_ready": True})
    window.update()
    page = window.pages[0]
    before = page.canvas.yview()
    window.match_tree.yview_moveto(1)
    window.say(window.detail, "\n".join(f"Job detail {n}" for n in range(500)))
    window.detail.yview_moveto(1)
    window.update()
    assert window.match_tree.yview()[0] > 0 and window.detail.yview()[0] > 0
    assert page.canvas.yview() == before


def test_previous_next_pages_keep_filters_and_reset_after_filter_change(jobs_window, monkeypatch):
    window = jobs_window
    calls = []
    original = window.center.matches
    monkeypatch.setattr(window.center, "matches", lambda **kw: calls.append(kw) or original(**kw))
    window.location.set("Worldwide")
    window.refresh_tab()
    pump(window, lambda: calls and calls[-1]["offset"] == 0)
    window.page_matches(1)
    pump(window, lambda: calls[-1]["offset"] == 200)
    assert calls[-1]["location"] == "Worldwide"
    window.query.set("designer")
    window.refresh_tab()
    pump(window, lambda: calls[-1]["query"] == "designer")
    assert calls[-1]["offset"] == 0


def test_stop_button_signals_worker_and_keeps_gui_responsive(jobs_window, monkeypatch):
    window = jobs_window
    entered = threading.Event()
    def search(**criteria):
        criteria["progress"]({"source": "fixture", "requests": 1, "request_limit": 100})
        entered.set()
        # A real cancellable worker, no Tk calls from its thread.
        assert window._search_stop.wait(5)
        assert criteria["check"]() is False
        return {"new": 0, "matching_total": 0, "read": [],
                "coverage": {"depth": criteria["depth"], "requests": 1, "stop_reason": "stopped by user"}}
    monkeypatch.setattr(window.center, "search", search)
    window.query.set("analyst")
    window.search()
    pump(window, entered.is_set)
    window.stop_search_button.invoke()
    pump(window, lambda: "stopped by user" in window.search_note.cget("text"))
    assert str(window.search_button.cget("state")) == "normal"
    assert str(window.stop_search_button.cget("state")) == "disabled"


@pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="opt-in native Jobs window capture")
def test_capture_scrollable_jobs_with_actual_main_window(own_gui, hunting, monkeypatch):
    from app.gui_jobs import JobsWindow
    from tests.test_jobs import posting
    from tests.test_gui_screenshots import _grab, _pump
    monkeypatch.setattr(own_gui.runtime, "jobs", hunting)
    hunting.store.upsert([posting()])
    hunting.rescore()
    own_gui.root.deiconify()
    window = JobsWindow(own_gui)
    try:
        pump(window, lambda: bool(window.match_tree.get_children()))
        for geometry, suffix in (("1180x760+20+20", "desktop"), ("680x480+20+20", "compact")):
            window.geometry(geometry)
            window.attributes("-topmost", True)
            window.lift()
            window.pages[0].canvas.yview_moveto(0)
            _pump(SimpleNamespace(root=window), .3)
            _grab(SimpleNamespace(root=window), "113-jobs-scroll-" + suffix)
            window.pages[0].canvas.yview_moveto(1)
            _pump(SimpleNamespace(root=window), .3)
            _grab(SimpleNamespace(root=window), "114-jobs-scroll-bottom-" + suffix)
        window.tabs.select(5)
        pump(window, lambda: bool(window.source_tree.get_children()))
        window.source_tree.selection_set("remoteok")
        pump(window, lambda: "Source access: remoteok" in window.source_access_text.get("1.0", "end"))
        window.pages[5].canvas.yview_moveto(1)
        _pump(SimpleNamespace(root=window), .3)
        _grab(SimpleNamespace(root=window), "115-jobs-source-access-compact")
    finally:
        window.close()
