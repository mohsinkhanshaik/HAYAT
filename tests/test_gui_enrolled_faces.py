"""Manual gallery controls use real registry permissions and isolated local storage."""
from pathlib import Path
import os
import threading
import time
from types import SimpleNamespace

from PIL import Image
import pytest

from app.gui_enrolled_faces import EnrolledFacesWindow
from app.gui_theme import Palette, style_controls, style_hayat_buttons
from app.perception.faces import DetectedFace, FaceGallery, FaceRecognizer
from app.safety.permissions import PermissionManager
from app.tools.faces import register_face_tools
from app.tools.registry import ToolRegistry


class LocalEngine:
    """Only native feature extraction is replaced; registry/gallery/matching are real."""
    available = True
    def __init__(self, directory):
        self.directory = directory
        self.calls = []

    def recognition_available(self):
        return self.available

    def status(self):
        return {'detection': True, 'recognition': self.available, 'models_dir': str(self.directory),
                'reason': '' if self.available else 'Optional local recognition models are not installed.'}

    def read_faces(self, path):
        self.calls.append((str(path), threading.get_ident()))
        vectors = {'sam.png': [[1., 0., 0.]], 'ada.png': [[0., 1., 0.]],
                   'scene.png': [[.98, .02, 0.], [0., 0., 1.], [.71, .70, 0.]],
                   'empty.png': []}
        return [DetectedFace((index * 20, 0, 10, 10), .99, vector if self.available else None)
                for index, vector in enumerate(vectors[Path(path).name])]


class GUI(Palette):
    def __init__(self, root, registry):
        self.root = root
        self.runtime = SimpleNamespace(registry=registry)


def finish(panel, predicate=None, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel.update()
        if predicate() if predicate is not None else not panel.busy:
            return
        time.sleep(.01)
    assert predicate() if predicate is not None else not panel.busy, panel.status.get()


@pytest.fixture
def studio(shared_tk, tmp_path):
    from tkinter import ttk
    from tests.conftest import close_window, open_window
    parent = open_window(shared_tk, '1000x800-4000-4000')
    for name in ('sam', 'ada', 'scene', 'empty'):
        Image.new('RGB', (20, 20), 'blue').save(tmp_path / (name + '.png'))
    registry = ToolRegistry(PermissionManager({'default': 'DENY', 'permissions': {
        'filesystem_read': 'ALLOW', 'filesystem_write_project': 'ALLOW', 'system_read': 'ALLOW'}}, tmp_path))
    engine = LocalEngine(tmp_path / 'models')
    recognizer = FaceRecognizer(engine=engine, gallery=FaceGallery(tmp_path / 'test-gallery.json'))
    register_face_tools(registry, recognizer)
    calls = []
    execute = registry.execute
    def tracked(name, arguments):
        calls.append((name, arguments, threading.get_ident()))
        return execute(name, arguments)
    registry.execute = tracked
    style = ttk.Style(parent)
    previous_theme = style.theme_use()
    style.theme_use('clam')
    style_controls(parent, Palette)
    style_hayat_buttons(parent, Palette)
    panel = EnrolledFacesWindow(GUI(parent, registry))
    panel.geometry('920x760-4000-4000')
    panel.update()
    yield panel, recognizer, engine, calls, tmp_path
    if panel.winfo_exists():
        panel.close()
    style.theme_use(previous_theme)
    close_window(parent)


def enroll(panel, path, name='Sam Example'):
    panel.image_path.set(str(path))
    panel.name.set(name)
    panel.consent.set(True)
    assert panel.enroll()
    finish(panel)


def test_open_is_manual_consent_unchecked_and_no_tools_or_downloads_run(studio):
    panel, recognizer, _engine, calls, _folder = studio
    assert not panel.consent.get() and not calls
    assert panel.buttons['enroll'].instate(['disabled'])
    assert panel.buttons['recognize'].instate(['disabled'])
    assert not recognizer.gallery.people()
    assert 'Unknown faces stay unknown' in panel.intro.cget('text')
    assert 'not a probability' in panel.report.get('1.0', 'end')


def test_choose_image_does_not_analyze_and_cancelling_picker_preserves_input(studio, monkeypatch):
    panel, _recognizer, _engine, calls, folder = studio
    monkeypatch.setattr('app.gui_enrolled_faces.filedialog.askopenfilename', lambda **_kw: str(folder / 'sam.png'))
    panel.choose_image()
    assert panel.image_path.get() == str(folder / 'sam.png') and not calls
    panel.name.set('Sam Example')
    panel.consent.set(True)
    monkeypatch.setattr('app.gui_enrolled_faces.filedialog.askopenfilename', lambda **_kw: '')
    panel.choose_image()
    assert panel.image_path.get() == str(folder / 'sam.png') and panel.consent.get()
    panel.image_path.set(str(folder / 'ada.png'))
    assert not panel.consent.get(), 'Changing the enrollment image requires fresh explicit permission'


def test_enrollment_requires_explicit_consent_and_passes_it_through_real_tool(studio):
    panel, recognizer, engine, calls, folder = studio
    panel.image_path.set(str(folder / 'sam.png'))
    panel.name.set('Sam Example')
    assert not panel.enroll() and not calls
    assert 'explicitly confirm' in panel.status.get()
    panel.consent.set(True)
    assert panel.enroll()
    finish(panel)
    assert recognizer.gallery.people()[0]['name'] == 'Sam Example'
    assert [call[0] for call in calls] == ['face_enroll', 'face_list_enrolled']
    assert calls[0][1] == {'name': 'Sam Example', 'path': str(folder / 'sam.png'), 'consent': True}
    assert all(call[2] != threading.get_ident() for call in calls)
    assert engine.calls[0][1] != threading.get_ident()
    assert not panel.consent.get() and panel.people.get_children()


def test_recognition_shows_enrolled_unknown_ambiguous_and_raw_similarity(studio):
    panel, recognizer, _engine, calls, folder = studio
    recognizer.gallery.enroll('Sam Example', [1., 0., 0.])
    recognizer.gallery.enroll('Ada Example', [0., 1., 0.])
    panel.image_path.set(str(folder / 'scene.png'))
    assert panel.recognize()
    finish(panel)
    report = panel.report.get('1.0', 'end').lower()
    assert 'sam example' in report and 'unknown' in report and 'ambiguous' in report
    assert 'cosine' in report and 'probability' in report
    assert '%' not in report
    assert [call[0] for call in calls] == ['face_recognize']
    assert 'identity is not verified' in panel.status.get()


def test_refresh_and_forget_remove_only_explicitly_selected_person(studio):
    panel, recognizer, _engine, calls, _folder = studio
    recognizer.gallery.enroll('Sam Example', [1., 0., 0.])
    recognizer.gallery.enroll('Ada Example', [0., 1., 0.])
    assert panel.refresh()
    finish(panel)
    assert len(panel.people.get_children()) == 2
    sam = next(key for key, row in panel._people.items() if row['name'] == 'Sam Example')
    panel.people.selection_set(sam)
    assert panel.forget()
    finish(panel)
    assert [row['name'] for row in recognizer.gallery.people()] == ['Ada Example']
    assert [row['name'] for row in panel._people.values()] == ['Ada Example']
    assert calls[1][0:2] == ('face_forget', {'name': 'Sam Example'})
    assert not any(call[0] in {'face_clear_all', 'face_models_setup'} for call in calls)


def test_missing_models_are_reported_without_automatic_install(studio):
    panel, _recognizer, engine, calls, folder = studio
    engine.available = False
    assert panel.check_status()
    finish(panel)
    assert 'recognition available: no' in panel.report.get('1.0', 'end')
    panel.image_path.set(str(folder / 'scene.png'))
    assert panel.recognize()
    finish(panel)
    assert panel.last_result['available'] is False
    assert 'unavailable' in panel.status.get().lower()
    assert 'not installed' in panel.report.get('1.0', 'end')
    assert not any(call[0] == 'face_models_setup' for call in calls)


@pytest.mark.parametrize('path', ['https://example.org/image.jpg', r'\\server\share\image.jpg', '//server/share/image.jpg'])
def test_window_rejects_network_images_before_dispatch(studio, path):
    panel, _recognizer, _engine, calls, _folder = studio
    panel.image_path.set(path)
    assert not panel.recognize() and not calls
    assert 'URLs and network shares' in panel.status.get()


def test_emergency_stop_and_denied_tool_permissions_do_not_touch_gallery(studio):
    panel, recognizer, _engine, calls, folder = studio
    registry = panel.gui.runtime.registry
    registry.interlock.halt()
    assert not panel.refresh() and not calls
    registry.interlock.resume()
    registry.permissions.policies['filesystem_write_project'] = 'DENY'
    enroll(panel, folder / 'sam.png')
    assert not recognizer.gallery.people()
    assert 'DENY' in panel.status.get()


def test_cancel_discards_late_native_result_and_keeps_interface_usable(studio):
    panel, _recognizer, _engine, _calls, folder = studio
    panel.image_path.set(str(folder / 'scene.png'))
    started, release = threading.Event(), threading.Event()
    spec = panel.gui.runtime.registry._tools['face_recognize']
    original = spec.handler
    def slow(**arguments):
        started.set()
        release.wait(3)
        return original(**arguments)
    spec.handler = slow
    original_report = panel.report.get('1.0', 'end')
    try:
        assert panel.recognize() and started.wait(1)
        panel.update()
        panel.cancel()
        assert not panel.busy and panel.buttons['recognize'].instate(['disabled'])
        assert not panel.recognize(), 'Native inference must not overlap the cancelled worker'
        release.set()
        panel.after(200, lambda: setattr(panel, '_test_pumped', True))
        finish(panel, lambda: getattr(panel, '_test_pumped', False))
        assert panel.last_result is None and panel.report.get('1.0', 'end') == original_report
        assert not panel.buttons['recognize'].instate(['disabled'])
    finally:
        release.set()


def test_close_stops_polling_and_late_results_never_touch_destroyed_widgets(studio):
    panel, _recognizer, _engine, _calls, _folder = studio
    handle = panel._poll_job
    panel.close()
    assert panel._closed and panel._cancel.is_set()
    assert handle not in panel.tk.call('after', 'info')


def test_successful_enrollment_is_not_hidden_by_gallery_refresh_failure(studio):
    panel, recognizer, _engine, _calls, folder = studio
    panel.gui.runtime.registry.permissions.policies['system_read'] = 'DENY'
    enroll(panel, folder / 'sam.png')
    assert len(recognizer.gallery.people()) == 1
    assert 'Enrolled Sam Example locally' in panel.status.get()
    assert 'Gallery refresh incomplete' in panel.status.get()


def test_compact_layout_keeps_gallery_report_consent_and_actions_accessible(studio):
    panel, _recognizer, _engine, _calls, _folder = studio
    panel.geometry('700x650-4000-4000')
    panel.update()
    assert panel.people.winfo_height() >= 70
    assert panel.report.winfo_height() >= 100
    assert panel.people.cget('xscrollcommand') and panel.report.cget('yscrollcommand')
    for widget in (panel.choose_button, panel.buttons['recognize'], panel.buttons['forget'], panel.consent_control):
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() + widget.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert widget.winfo_rooty() + widget.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()
    if os.getenv('HAYAT_SHOTS') == '1':
        from tests.test_gui_screenshots import _grab, _pump
        recognizer, folder = studio[1], studio[4]
        recognizer.gallery.enroll('Sam Example', [1., 0., 0.], consent=True)
        recognizer.gallery.enroll('Ada Example', [0., 1., 0.], consent=True)
        panel.image_path.set(str(folder / 'scene.png'))
        panel.refresh()
        finish(panel)
        panel.recognize()
        finish(panel)
        panel.geometry('920x760+25+25')
        panel.deiconify()
        panel.lift()
        panel.attributes('-topmost', True)
        _pump(SimpleNamespace(root=panel), .6)
        for size, label in [('920x760+25+25', '105-enrolled-faces'), ('700x650+25+25', '106-enrolled-faces-compact')]:
            panel.geometry(size)
            panel.attributes('-topmost', True)
            _pump(SimpleNamespace(root=panel), .25)
            _grab(SimpleNamespace(root=panel), label)


def test_cancel_button_cannot_interrupt_an_explicit_local_enrollment_commit(studio):
    panel, recognizer, _engine, _calls, folder = studio
    started, release = threading.Event(), threading.Event()
    spec = panel.gui.runtime.registry._tools['face_enroll']
    original = spec.handler
    def slow(**arguments):
        started.set()
        release.wait(3)
        return original(**arguments)
    spec.handler = slow
    panel.image_path.set(str(folder / 'sam.png'))
    panel.name.set('Sam Example')
    panel.consent.set(True)
    try:
        assert panel.enroll() and started.wait(1)
        assert panel.buttons['cancel'].instate(['disabled'])
        panel.cancel()
        assert panel.busy and 'save/removal is completing' in panel.status.get()
        release.set()
        finish(panel)
        assert recognizer.gallery.people()[0]['name'] == 'Sam Example'
    finally:
        release.set()


def test_unreadable_gallery_is_reported_as_failure_not_an_empty_verified_list(studio):
    panel, recognizer, _engine, _calls, _folder = studio
    recognizer.gallery.path.write_bytes(b'{malformed local fixture')
    assert panel.refresh()
    finish(panel)
    assert 'gallery unavailable' in panel.status.get().lower()
    assert 'preserved' in panel.report.get('1.0', 'end').lower()


def test_minimum_window_keeps_explicit_consent_and_reading_pane_visible(studio):
    panel = studio[0]
    panel.geometry('640x580-4000-4000')
    panel.update()
    assert panel.consent_control.winfo_ismapped()
    assert panel.people.winfo_height() >= 70
    assert panel.report.winfo_height() >= 90
    assert panel.buttons['recognize'].winfo_ismapped()


def test_duplicate_enrollment_explains_retention_without_claiming_another_sample(studio):
    panel, recognizer, _engine, _calls, folder = studio
    enroll(panel, folder / 'sam.png')
    enroll(panel, folder / 'sam.png')
    assert recognizer.gallery.people()[0]['signatures'] == 1
    assert 'Existing template retained' in panel.status.get()
    report = panel.report.get('1.0', 'end')
    assert 'No sample slot was consumed' in report
    assert 'different clear, consented still image' in report
    assert not panel.consent.get()
