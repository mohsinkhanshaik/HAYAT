"""Image reading stays responsive and never silently becomes a person lookup."""
from pathlib import Path
from types import SimpleNamespace
import os
import threading
import time

import pytest
from PIL import Image

from app.attachments import Attachment
from tests.test_gui_research import studio as studio


def pump_until(panel, predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        panel.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'Background image work did not complete'


def test_exact_source_button_reads_real_file_off_tk_and_preserves_metadata_on_timeout(image_panel):
    panel, path, _ = image_panel
    calls = []
    def get_json(_url):
        calls.append(threading.get_ident())
        raise TimeoutError('test source provider unavailable')
    panel.gui.runtime.research = SimpleNamespace(fetcher=SimpleNamespace(get_json=get_json))
    panel.image_sources()
    pump_until(panel, lambda: not panel._source_busy)
    result = panel.evidence.get('1.0', 'end')
    assert path.name in result and '240 × 160' in result
    assert 'No verified copy' in result and 'TimeoutError' in result
    assert 'incomplete' in panel.status.get()
    assert calls and calls[0] != threading.get_ident()


def test_image_source_lookup_explains_privacy_block_in_its_own_window(image_panel):
    panel, _, _ = image_panel
    panel.gui.runtime.authority = SimpleNamespace(privacy_mode=True)
    panel.image_sources()
    assert not panel._source_busy
    assert 'privacy' in panel.status.get().lower()


@pytest.fixture
def image_panel(studio, tmp_path):
    image = tmp_path / 'landmark.png'
    Image.new('RGB', (240, 160), '#387da6').save(image)
    calls = []

    def read(path):
        calls.append(threading.current_thread().name)
        return Attachment(Path(path), 'image', text='What it looks like:\nA tower beside a river.\nUnverified OCR: private note')

    studio.gui.runtime.attachments = SimpleNamespace(read=read)
    panel = studio.open_image_research()
    panel.read_image(str(image))
    pump_until(panel, lambda: panel.attachment is not None)
    yield panel, image, calls


def test_image_is_read_in_worker_and_preview_and_draft_survive_reopen(image_panel):
    panel, image, calls = image_panel
    assert calls == ['hayat-image-research']
    assert panel._photo is not None
    assert 'tower beside a river' in panel.evidence.get('1.0', 'end')
    assert panel.filename.get() == image.name
    assert panel.topic.get() == ''  # no inferred name or private OCR becomes a search
    assert panel.research.jobs.recent(1)[0].query == 'Topic 29'
    panel.topic.insert(0, 'Eiffel Tower construction history')
    panel.withdraw()
    assert panel.research.open_image_research() is panel
    assert panel.topic.get() == 'Eiffel Tower construction history'


def test_deep_search_receives_only_explicit_topic_and_shows_selected_job(image_panel, monkeypatch):
    panel, image, _ = image_panel
    calls = []

    def research(query, **options):
        calls.append((query, options))
        return SimpleNamespace(answer='Evidence from a public source', confidence=.8, sources=[{}], trace={})

    monkeypatch.setattr(panel.research.jobs, 'research', lambda: SimpleNamespace(research=research))
    panel.topic.insert(0, 'Eiffel Tower construction history')
    panel.search_button.invoke()
    job = panel.research._selected()
    pump_until(panel.research, lambda: job.status != 'running')
    assert job.level == 'deep' and job._released
    assert calls == [('Eiffel Tower construction history', {'level': 'deep', 'remember': True})]
    assert image.name not in job.asked and 'private note' not in job.asked
    panel.research.show_selected()
    assert 'Evidence from a public source' in panel.research.result.get('1.0', 'end')
    assert panel.state() == 'withdrawn' and panel.research.state() == 'normal'


@pytest.mark.parametrize('topic', ['Who is this person?', 'Find him all over the internet',
                                  'Search his private details', 'Identify this face', 'Her phone number'])
def test_person_identification_is_not_an_image_research_action(image_panel, monkeypatch, topic):
    panel, _, _ = image_panel
    monkeypatch.setattr(panel.research.jobs, 'start', lambda *a, **kw: pytest.fail('person lookup started'))
    panel.topic.insert(0, topic)
    panel.search()
    assert 'cannot be used to identify' in panel.status.get()
    assert panel.state() == 'normal'


@pytest.mark.parametrize('mode', ['PRIVATE', 'LOCAL', 'LOCAL_ONLY', 'privacy'])
def test_web_search_respects_privacy_mode_and_retains_topic(image_panel, monkeypatch, mode):
    panel, _, _ = image_panel
    panel.gui.runtime.session = SimpleNamespace(effective_modes=[] if mode == 'privacy' else [mode])
    panel.gui.runtime.authority = SimpleNamespace(privacy_mode=mode == 'privacy')
    monkeypatch.setattr(panel.research.jobs, 'start', lambda *a, **kw: pytest.fail('network research started'))
    panel.topic.insert(0, 'tower history')
    panel.search()
    assert panel.topic.get() == 'tower history'
    assert 'local-only mode is on' in panel.status.get()


def test_known_name_form_does_not_copy_ocr_or_infer_a_name(image_panel):
    panel, _, _ = image_panel
    panel.research.query.insert(0, 'Existing known-name draft')
    panel.public_work()
    panel.research.update()
    assert panel.research.search_kind.get() == 'Person (public work)'
    assert panel.research.query.get() == 'Existing known-name draft'
    assert panel.research.person_fields.winfo_ismapped()


def test_late_read_cannot_replace_a_newer_image_and_close_cleans_poll(image_panel):
    panel, image, _ = image_panel
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def slow_read(path):
        started.set()
        release.wait(3)
        finished.set()
        return Attachment(Path(path), 'image', text='Superseded image')

    panel.gui.runtime.attachments = SimpleNamespace(read=slow_read)
    try:
        panel.read_image(str(image))
        assert started.wait(1)
        panel.topic.insert(0, 'Draft while working')
        panel.update()  # Tk remains available while the reader is blocked
        panel.gui.runtime.attachments = SimpleNamespace(read=lambda path: Attachment(Path(path), 'image', text='Newer image'))
        panel.read_image(str(image))
        pump_until(panel, lambda: panel.attachment is not None)
        release.set()
        pump_until(panel, finished.is_set)
        time.sleep(.15)
        panel.update()
        assert panel.attachment.text == 'Newer image'
        assert panel.topic.get() == 'Draft while working'
        handle = panel._poll_job
        panel.close()
        assert panel._closed and handle not in panel.tk.call('after', 'info')
    finally:
        release.set()


def test_failed_read_and_cancelled_picker_keep_draft_and_do_not_enable_search(image_panel, monkeypatch):
    panel, image, _ = image_panel
    panel.topic.insert(0, 'Preserved topic')
    monkeypatch.setattr('app.gui_image_research.filedialog.askopenfilename', lambda **kw: '')
    panel.choose()
    assert panel.attachment.ok
    panel.gui.runtime.attachments = SimpleNamespace(read=lambda path: Attachment(Path(path), 'image', error='blocked by antimalware'))
    panel.read_image(str(image))
    pump_until(panel, lambda: panel.attachment is not None)
    assert 'antimalware' in panel.status.get()
    assert panel.search_button.instate(['disabled'])
    assert panel._photo is None and panel.topic.get() == 'Preserved topic'


def test_compact_layout_keeps_preview_evidence_and_actions_reachable(image_panel):
    panel, _, _ = image_panel
    panel.geometry('560x460-4000-4000')
    panel.update()
    assert panel.evidence.winfo_height() >= 60
    assert panel.search_button.winfo_ismapped()
    for widget in (panel.topic, panel.search_button, panel.person_button):
        assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()


def test_large_text_keeps_a_readable_evidence_area(image_panel):
    from app.gui_theme import apply_text_scale
    panel, _, _ = image_panel
    apply_text_scale(panel, 1.5)
    panel.geometry('720x680-4000-4000')
    panel.update()
    assert panel.evidence.winfo_height() >= 100
    assert panel.search_button.winfo_ismapped() and panel.topic.winfo_ismapped()


def test_native_image_read_and_visual_review(studio, tmp_path):
    if os.getenv('HAYAT_LIVE_IMAGE_RESEARCH') != '1':
        pytest.skip('opt-in installed OCR and window capture')
    from PIL import ImageDraw, ImageFont
    from app.attachments import AttachmentReader
    from tests.test_gui_screenshots import _grab, _pump
    image = Image.new('RGB', (1000, 360), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 48)
    draw.multiline_text((40, 35), 'SOLAR PANEL\nMODEL SP-240\n240 WATTS', fill='black', font=font, spacing=12)
    path = tmp_path / 'solar-panel.png'
    image.save(path)
    studio.gui.runtime.attachments = AttachmentReader()
    panel = studio.open_image_research()
    panel.read_image(str(path))
    pump_until(panel, lambda: panel.attachment is not None, seconds=30)
    assert panel.attachment.ok
    assert 'SOLAR PANEL' in panel.attachment.text.upper()
    assert panel._photo is not None and not panel.search_button.instate(['disabled'])
    panel.topic.insert(0, 'How do photovoltaic solar panels produce electricity?')
    for size, name in [('720x680+80+40', '85-image-research'), ('560x460+80+40', '86-image-research-compact')]:
        panel.geometry(size)
        panel.attributes('-topmost', True)
        _pump(SimpleNamespace(root=panel), .3)
        assert panel.evidence.winfo_height() >= 60
        _grab(SimpleNamespace(root=panel), name)
