from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.gui_knowledge import KnowledgeStudio, evidence_text, queue_public_topic, self_check_text
from app.gui_theme import Palette


def claim(identifier="one", **fields):
    return {"id": identifier, "claim_text": "A retained fact", "status": "SUPPORTED", "stale": False,
            "valid_until": "2099-01-01", "updated_at": "2026-10-01", "evidence": [
                {"title": "Official source", "final_url": "https://docs.python.org/3/", "support_type": "DIRECT", "excerpt": "Original evidence."}], **fields}


class FakeGUI(Palette):
    def __init__(self, root, store):
        self.root = root
        self.runtime = SimpleNamespace(research=SimpleNamespace(knowledge=store), authority=SimpleNamespace(privacy_mode=False))

    def show_view(self, _view):
        pass


def pump(window, predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        window.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def studio(shared_tk):
    store = SimpleNamespace(search_claims=lambda *a, **k: [claim()], stats=lambda: {"claims": 1, "sources": 1})
    window = KnowledgeStudio(FakeGUI(shared_tk, store))
    window.withdraw()
    pump(window, lambda: bool(window.rows))
    yield window
    window.destroy()
    shared_tk.withdraw()


@pytest.fixture
def visible_studio(shared_tk):
    shared_tk.deiconify()
    pump(shared_tk, lambda: bool(shared_tk.winfo_viewable()))
    store = SimpleNamespace(search_claims=lambda *a, **k: [claim()], stats=lambda: {"claims": 1, "sources": 1})
    window = KnowledgeStudio(FakeGUI(shared_tk, store))
    window.geometry("920x720+50+50")
    pump(window, lambda: bool(window.rows) and window.winfo_width() > 100)
    try:
        yield window
    finally:
        window.destroy()
        shared_tk.withdraw()


def test_evidence_detail_and_clipboard_keep_sources_and_freshness(studio):
    studio.copy_selected()
    copied = studio.clipboard_get()
    assert "https://docs.python.org/3/" in copied
    assert "2099-01-01" in copied and "Original evidence." in copied
    assert "accuracy measurements" in copied


def test_fresh_filter_excludes_stale_undated_and_disputed_claims(studio):
    studio.store.search_claims = lambda *a, **k: [claim("fresh"), claim("old", stale=True),
        claim("undated", valid_until=None), claim("conflict", has_unresolved_conflict=True)]
    studio.fresh_only.set(True)
    studio.refresh()
    pump(studio, lambda: set(studio.rows) == {"fresh"})


def test_old_search_response_cannot_replace_newer_results(studio):
    generation = studio.generation
    studio.query.set("new topic")
    studio.changes.put(("search", generation, ([claim("wrong")], {}), None))
    pump(studio, lambda: studio.changes.empty())
    assert "wrong" not in studio.rows


def test_search_is_off_tk_thread_and_close_during_search_is_safe(studio):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    worker_ids = []
    def blocked(*args, **kwargs):
        worker_ids.append(threading.get_ident())
        started.set()
        release.wait(3)
        finished.set()
        return [claim("late")]
    studio.store.search_claims = blocked
    studio.refresh()
    assert started.wait(1)
    studio.close()
    release.set()
    assert finished.wait(1)
    assert worker_ids != [main_thread]
    assert studio.closed


def test_unknown_search_has_a_clear_empty_state(studio):
    studio.store.search_claims = lambda *a, **k: []
    studio.refresh()
    pump(studio, lambda: not studio.rows)
    assert "No matching evidence" in studio.status.get()
    assert str(studio.copy_button.cget("state")) == "disabled"


def test_privacy_blocks_queueing_before_touching_the_learner():
    def unexpected(*a, **k):
        raise AssertionError("privacy must prevent enqueue")
    runtime = SimpleNamespace(authority=SimpleNamespace(privacy_mode=True), learner=SimpleNamespace(enqueue=unexpected))
    assert "Privacy mode" in queue_public_topic(runtime, "Python concurrency")


def test_conflicts_and_missing_dates_are_not_presented_as_settled():
    text = evidence_text(claim(valid_until=None, has_unresolved_conflict=True))
    assert "No refresh deadline" in text and "Do not treat this claim as settled" in text


def test_quality_selection_reaches_store_before_result_limit(studio):
    calls = []
    def search(*args, **kwargs):
        calls.append(kwargs)
        return [claim()]
    studio.store.search_claims = search
    studio.fresh_only.set(True)
    studio.quality.set("Unresolved conflicts")
    studio._quality_changed()
    pump(studio, lambda: bool(calls))
    assert calls[-1]["quality"] == "conflicts"
    assert not studio.fresh_only.get()


def test_refresh_preserves_selected_claim(studio):
    rows = [claim("first"), claim("chosen")]
    studio._paint(rows, {})
    studio.tree.selection_set("chosen")
    studio._paint(rows, {})
    assert studio.tree.selection() == ("chosen",)


def test_new_results_start_at_the_new_selection_instead_of_old_scroll_offset(visible_studio):
    studio = visible_studio
    studio._paint([claim(str(i)) for i in range(40)], {})
    studio.tree.selection_set("39")
    studio.tree.yview_moveto(1.0)
    studio.update()
    studio._paint([claim("new-" + str(i)) for i in range(40)], {})
    studio.update()
    assert studio.tree.selection() == ("new-0",)
    assert studio.tree.yview()[0] == 0.0


def test_read_failure_clears_previous_evidence(studio):
    def fail(*args, **kwargs):
        raise OSError("database unavailable")
    studio.store.search_claims = fail
    studio.refresh()
    pump(studio, lambda: "Could not read evidence" in studio.status.get())
    assert not studio.rows and not studio.tree.get_children()
    assert "unavailable" in studio.summary.get()
    assert str(studio.copy_button.cget("state")) == "disabled"


def test_self_check_distinguishes_unknown_measurements_from_zero():
    text = self_check_text({}, {"observation_errors": {"knowledge": "OperationalError"}})
    assert "Verified and current: Unknown" in text
    assert "knowledge: OperationalError" in text
    assert "does not establish subjective consciousness" in text
    assert "no gaps" in text


def test_library_layout_keeps_controls_visible_at_normal_and_compact_sizes(visible_studio):
    studio = visible_studio
    for width, height in ((920, 720), (620, 560)):
        studio.geometry(f"{width}x{height}")
        studio.update()
        for widget in (studio.quality_selector, studio.learn_button, studio.copy_button, studio.details_tabs):
            assert widget.winfo_width() > 30 and widget.winfo_height() > 10
            assert widget.winfo_rootx() >= studio.winfo_rootx()
            assert widget.winfo_rootx() + widget.winfo_width() <= studio.winfo_rootx() + studio.winfo_width()
            assert widget.winfo_rooty() + widget.winfo_height() <= studio.winfo_rooty() + studio.winfo_height()
        assert studio.details_tabs.winfo_height() >= 75
        assert studio.tree.winfo_height() >= 65
    studio.details_tabs.select(studio.self_check)
    studio.update()
    assert "HAYAT self-check" in studio.self_check.get("1.0", "end")


def test_capture_knowledge_upgrade(visible_studio):
    studio = visible_studio
    import os
    if os.environ.get("HAYAT_SHOTS") != "1":
        pytest.skip("screenshots on request")
    from app.gui_theme import apply_theme
    from tests.test_gui_screenshots import _grab
    studio.tk.call("ttk::style", "theme", "use", "clam")
    apply_theme(studio, type(studio.gui), "Midnight")
    for width, height in ((920, 720), (620, 560)):
        studio.geometry(f"{width}x{height}+50+50")
        studio.update()
        _grab(SimpleNamespace(root=studio), f"90-knowledge-{width}")
    studio.geometry("920x720+50+50")
    studio.details_tabs.select(studio.self_check)
    studio.update()
    _grab(SimpleNamespace(root=studio), "91-knowledge-self-check")
