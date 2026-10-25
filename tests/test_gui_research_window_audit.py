"""Window-level behavior: manual handoffs, usable controls and stale-result rejection."""
import threading
import time
import os
from types import SimpleNamespace

import pytest

from app.gui_person_research import PersonResearchStudio, RecordLibrary
from app.research.investigations import build_report
from app.research.jobs import ResearchJob
from tests.test_gui_research import studio as studio
from tests import test_gui_enrolled_faces
from tests.test_gui_image_research import image_panel as image_panel
from tests.test_gui import own_gui as own_gui
from tests.test_public_people_research import research as research

gallery = test_gui_enrolled_faces.studio


def pump(panel, predicate, seconds=8):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        panel.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), getattr(panel, 'status', SimpleNamespace(get=lambda: 'pending')).get()


@pytest.fixture
def professional(studio, research, tmp_path):
    manager, _ = research
    runtime = studio.gui.runtime
    runtime.research, runtime.authority = manager, SimpleNamespace(privacy_mode=False)
    runtime.session = SimpleNamespace(effective_modes=[])
    runtime.memory = SimpleNamespace(path=tmp_path / 'memory' / 'isolated.db')
    runtime.research_jobs.research = lambda: manager
    panel = PersonResearchStudio(studio)
    panel.geometry('760x580-4000-4000')
    panel.update()
    yield panel
    if panel.winfo_exists():
        panel.close()


def paint_fixture(panel):
    result = panel.gui.runtime.research.research_person('Jane Example', 'public work')
    panel.paint(build_report(result, 'public work'))
    panel.update()


def test_professional_empty_actions_and_mode_guidance_are_real(professional):
    panel = professional
    for title in ('Research', 'Cancel', 'Compare selected', 'Open source'):
        assert panel.buttons[title].instate(['disabled']), title
    for title in ('Save investigation', 'Export PDF', 'Export JSON'):
        assert panel.report_menu.entrycget(panel.report_items[title], 'state') == 'disabled'
    assert not panel.contact_consent.winfo_manager()
    panel.query.insert(0, 'office@example.org')
    panel.input_mode.set('Public business contact')
    panel.update()
    assert panel.contact_consent.winfo_manager() == 'grid'
    assert panel.buttons['Research'].instate(['disabled'])
    panel.public_contact.set(True)
    assert not panel.buttons['Research'].instate(['disabled'])
    panel.input_mode.set('Name / professional profile')
    assert not panel.public_contact.get() and not panel.contact_consent.winfo_manager()


def test_professional_selection_enables_exactly_applicable_actions(professional):
    panel = professional
    paint_fixture(panel)
    assert panel.report_menu.entrycget(panel.report_items['Export JSON'], 'state') == 'normal'
    assert panel.buttons['Open source'].instate(['disabled'])
    rows = panel.sources.get_children()
    panel.sources.selection_set(rows[0])
    panel.update()
    assert not panel.buttons['Open source'].instate(['disabled'])
    assert panel.buttons['Compare selected'].instate(['disabled'])
    panel.sources.selection_set(rows[:2])
    panel.update()
    assert not panel.buttons['Compare selected'].instate(['disabled'])
    panel.buttons['Compare selected'].invoke()
    assert panel.tabs.select() == str(panel.panels['comparison'])


def test_company_mode_starts_organization_research_without_a_person_profile(professional, monkeypatch):
    panel = professional
    calls = []
    def start(query, **options):
        calls.append((query, options))
        return ResearchJob('company', query, query, 'deep', '', status='cancelled')
    monkeypatch.setattr(panel.gui.runtime.research_jobs, 'start', start)
    panel.input_mode.set('Company / organization')
    panel.query.insert(0, 'Example Research Foundation')
    panel.search()
    assert calls and 'Example Research Foundation' in calls[0][0]
    assert 'person' not in calls[0][1] and calls[0][1]['level'] == 'deep'


def test_cancelled_professional_worker_cannot_overlap_or_overwrite_a_new_operation(professional):
    panel = professional
    started, release = threading.Event(), threading.Event()
    values = []
    def slow():
        started.set()
        release.wait(5)
        return 'old'
    try:
        assert panel.work('Synthetic slow read', slow, values.append)
        assert started.wait(1)
        panel.cancel()
        assert not panel.work('Overlapping read', lambda: 'new', values.append)
        assert panel.buttons['Research'].instate(['disabled'])
        release.set()
        pump(panel, lambda: not panel._worker.is_alive())
        assert values == []
        assert panel.work('Explicit later read', lambda: 'new', values.append)
        pump(panel, lambda: not panel.busy)
        assert values == ['new']
    finally:
        release.set()


@pytest.mark.parametrize('change', ['stop_resume', 'privacy', 'route'])
def test_professional_completed_worker_is_rechecked_before_display(professional, change):
    panel = professional
    values = []
    assert panel.work('Synthetic public result', lambda: 'evidence', values.append, network=True)
    panel._worker.join(2)
    assert not panel._worker.is_alive()
    from app.privacy_masks import ROUTE
    old = ROUTE.proxy
    try:
        if change == 'stop_resume':
            panel.gui.runtime.registry.interlock.halt()
            panel.gui.runtime.registry.interlock.resume()
        elif change == 'privacy':
            panel.gui.runtime.authority.privacy_mode = True
        else:
            ROUTE.set('socks5://127.0.0.1:19999')
        pump(panel, lambda: not panel.busy)
        assert values == []
    finally:
        ROUTE.set(old)


def test_professional_source_opening_validates_through_tor_without_local_dns(professional, monkeypatch):
    panel = professional
    paint_fixture(panel)
    panel.sources.selection_set(panel.sources.get_children()[0])
    validated, launched = [], []
    monkeypatch.setattr('app.research.safety.validate_public_url', lambda url, **options: validated.append(options) or url)
    monkeypatch.setattr(panel.gui.runtime.research, 'search_browser', lambda url, required: launched.append(required) or {'opened': True}, raising=False)
    from app.privacy_masks import ROUTE
    old = ROUTE.proxy
    ROUTE.set('socks5://127.0.0.1:19999')
    try:
        panel.open_source()
        pump(panel, lambda: not panel.busy)
        assert validated == [{'via_tor': True}] and launched == [True]
    finally:
        ROUTE.set(old)


def test_professional_report_keeps_tor_requirement_when_route_is_later_disabled(professional, monkeypatch):
    from app.privacy_masks import ROUTE
    panel = professional
    old = ROUTE.proxy
    ROUTE.set('socks5://127.0.0.1:19999')
    try:
        paint_fixture(panel)
        assert panel.report['tor_required'] is True
        panel.sources.selection_set(panel.sources.get_children()[0])
        ROUTE.set(None)
        monkeypatch.setattr('app.research.safety.validate_public_url', lambda *a, **k: pytest.fail('direct URL lookup'))
        panel.open_source()
        pump(panel, lambda: not panel.busy)
        assert 'Start Tor' in panel.status.get()
    finally:
        ROUTE.set(old)


def test_queued_professional_operation_checks_old_stop_ticket_before_reading(professional, monkeypatch):
    panel, calls, workers = professional, [], []
    class Deferred:
        def __init__(self, target, **_options):
            self.target = target
            workers.append(self)
        def start(self):
            pass
        def is_alive(self):
            return False
    monkeypatch.setattr('app.gui_person_research.threading.Thread', Deferred)
    assert panel.work('Queued public operation', lambda: calls.append('read'), lambda value: None, network=True)
    panel.gui.runtime.registry.interlock.halt()
    panel.gui.runtime.registry.interlock.resume()
    workers[0].target()
    pump(panel, lambda: not panel.busy)
    assert calls == []


@pytest.mark.parametrize('finish', ['cancel', 'close'])
def test_cancel_or_close_before_worker_start_prevents_the_operation(professional, monkeypatch, finish):
    panel, calls, workers = professional, [], []
    class Deferred:
        def __init__(self, target, **_options):
            self.target = target
            workers.append(self)
        def start(self):
            pass
        def is_alive(self):
            return False
    monkeypatch.setattr('app.gui_person_research.threading.Thread', Deferred)
    assert panel.work('Queued operation', lambda: calls.append('read'), lambda value: None)
    getattr(panel, finish)()
    workers[0].target()
    assert calls == []


@pytest.mark.parametrize('size', ['720x550', '760x580', '1000x760'])
def test_professional_controls_and_report_stay_reachable(professional, size):
    panel = professional
    panel.geometry(size + '-4000-4000')
    panel.update()
    assert panel.tabs.winfo_height() >= 130
    for button in panel.buttons.values():
        assert button.winfo_ismapped()
        assert button.winfo_rootx() + button.winfo_width() <= panel.winfo_rootx() + panel.winfo_width()
        assert button.winfo_rooty() + button.winfo_height() <= panel.winfo_rooty() + panel.winfo_height()


def test_saved_record_library_has_bidirectional_scrolling_at_compact_size(professional):
    library = RecordLibrary(professional)
    try:
        library.geometry('560x480-4000-4000')
        library.update()
        assert library.tree.cget('yscrollcommand') and library.tree.cget('xscrollcommand')
        assert library.preview.winfo_height() >= 70
    finally:
        library.destroy()


def test_image_handoff_prepares_only_a_path_without_reading_or_network(image_panel):
    panel, image, calls = image_panel
    baseline = list(calls)
    assert panel.prepare_image(str(image))
    assert calls == baseline and panel.attachment is None and panel._photo is None
    assert panel.topic.get() == '' and panel._image_path == str(image)
    assert panel.search_button.instate(['disabled'])
    assert not panel.source_button.instate(['disabled'])
    assert 'No file was read' in panel.evidence.get('1.0', 'end')


def test_research_menu_passes_images_only_to_image_windows_and_names_only_to_name_windows(shared_tk):
    import tkinter as tk
    from app.gui_theme import Palette
    from app.gui_research_links import research_links
    class Bridge(Palette):
        def __init__(self):
            self.calls = []
        def _open_research_workspace(self, kind, **arguments):
            self.calls.append((kind, arguments))
    gui = Bridge()
    header = tk.Frame(shared_tk)
    try:
        button = research_links(header, gui, '', image=lambda: 'selected-local.png',
                                name=lambda: 'Explicitly Typed', context=lambda: 'Typed Organization')
        for index in range(button.research_menu.index('end') + 1):
            button.research_menu.invoke(index)
        assert len(gui.calls) == 5
        for kind, arguments in gui.calls:
            assert arguments == ({'image_path': 'selected-local.png'} if kind in {'faces', 'images', 'sources'} else
                                 {'name': 'Explicitly Typed', 'context': 'Typed Organization'})
    finally:
        header.destroy()


def test_local_gallery_filter_is_local_and_cannot_leave_hidden_person_selected(gallery):
    panel, _recognizer, engine, calls, _folder = gallery
    panel._paint_people([{'name': 'Sam Example', 'signatures': 2}, {'name': 'Ada Example', 'signatures': 1}])
    panel.people.selection_set('0')
    panel.gallery_filter.set('ada')
    panel.update()
    assert [panel.people.item(key, 'text') for key in panel.people.get_children()] == ['Ada Example']
    assert not panel.people.selection() and panel.buttons['forget'].instate(['disabled'])
    assert not calls and not engine.calls
    panel.gallery_filter.set('')
    assert len(panel.people.get_children()) == 2


def test_main_research_links_reuse_windows_and_never_derive_a_name_from_an_image(own_gui, tmp_path, monkeypatch):
    gui = own_gui
    calls = []
    monkeypatch.setattr(gui.runtime.registry, 'execute', lambda *a, **k: calls.append((a, k)))
    path = str(tmp_path / 'not-read.png')
    sources = gui._open_research_workspace('sources', image_path=path)
    try:
        assert sources._image_path == path and sources.attachment is None
        assert gui._open_research_workspace('sources') is sources
        person = gui._open_research_workspace('professional', image_path=path)
        assert person.query.get() == '' and person.context.get() == ''
        assert gui._open_research_workspace('professional', name='Typed Example', context='Typed Organization') is person
        assert person.query.get() == 'Typed Example' and person.context.get() == 'Typed Organization'
        assert not calls
    finally:
        if gui._research_studio._person_studio.winfo_exists():
            gui._research_studio._person_studio.close()
        gui._research_studio.close()


@pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='opt-in native window review')
def test_capture_professional_and_record_library_windows(own_gui, research, monkeypatch):
    from tests.test_gui_screenshots import _grab, _pump
    monkeypatch.setattr(own_gui.runtime, 'research', research[0])
    panel = own_gui._open_research_workspace('professional')
    paint_fixture(panel)
    panel.gui.root.geometry('1040x820+10+10')
    panel.gui.root.deiconify()
    panel.research.deiconify()
    panel.deiconify()
    panel.lift()
    panel.sources.selection_set(panel.sources.get_children()[:2])
    panel.compare()
    for geometry, title in [('1000x760+25+25', '110-professional-workspace'),
                             ('720x550+25+25', '111-professional-workspace-compact')]:
        panel.geometry(geometry)
        panel.attributes('-topmost', True)
        _pump(SimpleNamespace(root=panel), .3)
        _grab(SimpleNamespace(root=panel), title)
    library = RecordLibrary(panel)
    try:
        library.geometry('560x480+25+25')
        library.deiconify()
        library.lift()
        library.attributes('-topmost', True)
        _pump(SimpleNamespace(root=library), .3)
        _grab(SimpleNamespace(root=library), '112-record-library-compact')
    finally:
        library.destroy()
