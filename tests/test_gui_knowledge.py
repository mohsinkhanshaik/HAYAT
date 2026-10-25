from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.gui_knowledge import KnowledgeStudio, evidence_text, queue_public_topic
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
