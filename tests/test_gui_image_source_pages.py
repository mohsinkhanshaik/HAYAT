"""Source-page comparison is explicit, responsive and bound to its chosen file."""
import threading
from types import SimpleNamespace

import pytest

from app.gui_image_research import ImageResearchStudio
from tests.test_gui_image_research import image_panel as image_panel, pump_until
from tests.test_gui_research import studio as studio
from tests.test_image_source_pages import PageFetcher


def test_source_page_button_compares_real_pixels_in_worker_and_shows_citations(image_panel):
    panel, path, _ = image_panel
    fetcher = PageFetcher('<img src="/image.png">', {
        'https://publisher.example/image.png': path.read_bytes()})
    threads = []
    fetch = fetcher.fetch
    def record(*args, **kwargs):
        threads.append(threading.get_ident())
        return fetch(*args, **kwargs)
    fetcher.fetch = record
    panel.gui.runtime.research = SimpleNamespace(fetcher=fetcher)
    panel.source_url.insert(0, 'https://publisher.example/source')
    panel.page_button.invoke()
    assert panel._source_busy and panel.page_button.instate(['disabled'])
    pump_until(panel, lambda: not panel._source_busy)
    evidence = panel.evidence.get('1.0', 'end')
    assert 'MATCH · exact file bytes' in evidence and 'Page SHA-256:' in evidence
    assert 'https://publisher.example/image.png' in evidence
    assert '2026-10-24T12:00:00Z' in evidence
    assert 'verified' in panel.status.get()
    assert threads and all(value != threading.get_ident() for value in threads)
    assert not panel.page_button.instate(['disabled']) and not panel.source_button.instate(['disabled'])


def test_source_page_empty_or_privacy_block_keeps_url_and_never_requests(image_panel):
    panel, _, _ = image_panel
    panel.check_source_page()
    assert not panel._source_busy and 'Paste' in panel.status.get()
    panel.source_url.insert(0, 'https://publisher.example/source')
    panel.gui.runtime.authority = SimpleNamespace(privacy_mode=True)
    panel.check_source_page()
    assert not panel._source_busy and 'privacy' in panel.status.get().lower()
    assert panel.source_url.get() == 'https://publisher.example/source'


def test_unknown_and_failed_pages_are_explained_without_fabricated_match(image_panel):
    panel, _, _ = image_panel
    fetcher = PageFetcher('<script>loadImages()</script>')
    panel.gui.runtime.research = SimpleNamespace(fetcher=fetcher)
    panel.source_url.insert(0, 'https://publisher.example/source')
    panel.check_source_page()
    pump_until(panel, lambda: not panel._source_busy)
    assert 'UNKNOWN' in panel.evidence.get('1.0', 'end')
    assert 'JavaScript content is not rendered' in panel.evidence.get('1.0', 'end')
    fetcher.calls.clear()
    fetcher.html = TimeoutError('test provider failed')
    panel.check_source_page()
    pump_until(panel, lambda: not panel._source_busy)
    assert 'FAILED' in panel.evidence.get('1.0', 'end')
    assert 'could not be retrieved' in panel.status.get()


def test_old_source_response_cannot_replace_a_new_image_or_unlock_current_request(image_panel):
    panel, path, _ = image_panel
    started, release = threading.Event(), threading.Event()
    fetcher = PageFetcher('<img src="/image.png">', {
        'https://publisher.example/image.png': path.read_bytes()})
    original = fetcher.fetch
    def slow(*args, **kwargs):
        if not started.is_set():
            started.set()
            release.wait(3)
        return original(*args, **kwargs)
    fetcher.fetch = slow
    panel.gui.runtime.research = SimpleNamespace(fetcher=fetcher)
    panel.source_url.insert(0, 'https://publisher.example/source')
    try:
        panel.check_source_page()
        assert started.wait(1)
        panel.read_image(path)
        pump_until(panel, lambda: panel.attachment is not None)
        release.set()
        pump_until(panel, lambda: panel._source_results.qsize() > 0 or len(fetcher.calls) > 1)
        # Pump a polling interval so the old source result is actually consumed.
        panel.after(150, lambda: setattr(panel, '_test_pumped', True))
        pump_until(panel, lambda: getattr(panel, '_test_pumped', False))
        assert 'tower beside a river' in panel.evidence.get('1.0', 'end')
        assert 'SOURCE PAGE CHECK' not in panel.evidence.get('1.0', 'end')
        assert not panel._source_busy
    finally:
        release.set()


def test_source_evidence_formatter_reports_partial_coverage_and_failed_candidates():
    value = {'outcome': 'unknown', 'scope': 'Exact checks only', 'metadata': {'sha256': 'local-hash'},
             'page': {'url': 'https://publisher.example/source', 'sha256': 'page-hash', 'retrieved_at': 'now'},
             'coverage': {'candidates_found': 20, 'candidates_attempted': 8, 'bytes_received': 1000, 'limited': True},
             'candidates': [{'requested_url': 'https://publisher.example/a.png', 'status': 'failed',
                             'error': 'TimeoutError: image could not be verified'}],
             'errors': ['TimeoutError: image could not be verified'], 'privacy': 'No upload'}
    text = ImageResearchStudio._source_page_text(value)
    assert 'Checked 8 of 20' in text and 'Coverage limited' in text
    assert 'UNVERIFIED' in text and 'TimeoutError' in text


def test_compact_source_page_controls_remain_reachable(image_panel):
    panel, _, _ = image_panel
    panel.geometry('560x460-4000-4000')
    panel.update()
    assert panel.evidence.winfo_height() >= 60
    for widget in (panel.source_url, panel.page_button, panel.source_button):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()


def test_image_pixel_limit_is_checked_before_ocr_or_vision(image_panel, monkeypatch):
    panel, path, _ = image_panel
    monkeypatch.setattr('app.research.image_sources.MAX_IMAGE_PIXELS', 100)
    panel.gui.runtime.attachments.read = lambda *_a: pytest.fail('Oversized image reached OCR/vision')
    panel.read_image(path)
    pump_until(panel, lambda: panel.status.get().startswith('Could not read image:'))
    assert '20 million pixels' in panel.status.get()
    assert panel.attachment is None and panel._photo is None
    assert panel.search_button.instate(['disabled'])


def test_emergency_stop_blocks_both_source_actions_before_work_starts(image_panel):
    panel, _, _ = image_panel
    panel.source_url.insert(0, 'https://publisher.example/source')
    panel.gui.runtime.registry.interlock.halt()
    for action in (panel.check_source_page, panel.image_sources):
        action()
        assert not panel._source_busy
        assert 'stop' in panel.status.get().lower()


@pytest.mark.parametrize('action', ['check_source_page', 'image_sources'])
def test_completed_source_evidence_is_discarded_if_privacy_changes_before_gui_delivery(image_panel, monkeypatch, action):
    panel, path, _ = image_panel
    done = threading.Event()
    fetcher = PageFetcher('<img src="/image.png">', {'https://publisher.example/image.png': path.read_bytes()})
    panel.gui.runtime.research = SimpleNamespace(fetcher=fetcher)
    panel.source_url.insert(0, 'https://publisher.example/source')
    original = panel._source_results.put
    def completed(value):
        original(value)
        done.set()
    monkeypatch.setattr(panel._source_results, 'put', completed)
    if action == 'image_sources':
        monkeypatch.setattr(fetcher, 'get_json', lambda _url: {'query': {'allimages': []}}, raising=False)
    getattr(panel, action)()
    assert done.wait(3), 'Synthetic source work should finish without pumping Tk'
    panel.gui.runtime.authority = SimpleNamespace(privacy_mode=True)
    pump_until(panel, lambda: not panel._source_busy)
    assert 'Source result discarded' in panel.evidence.get('1.0', 'end')
    assert 'privacy' in panel.evidence.get('1.0', 'end').lower()
