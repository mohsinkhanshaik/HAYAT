from types import SimpleNamespace
import time

from PIL import Image

from app.tools.registry import ToolResult
from tests.test_gui import own_gui as own_gui
from tests.test_gui_reverse_engineering import studio as studio, pump as rea_pump
from tests import test_gui_research

research_studio = test_gui_research.studio


def pump(gui, condition):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        gui.root.update()
        if condition():
            return
        time.sleep(.01)
    assert condition()


def test_detector_explains_supplied_score_and_connects_known_name_research(own_gui, tmp_path, monkeypatch):
    gui = own_gui
    gui._open_workspace_tools('people')
    window = gui._workspace_tools
    path = tmp_path / 'synthetic-detection.png'
    Image.new('RGB', (1254, 1254), '#235ACA').save(path)
    output = {'person_detection': True, 'people': [{'box': [11, 79, 1247, 1247], 'score': .9973}],
              'count': 1, 'width': 1254, 'height': 1254, 'threshold': .5, 'device': 'CPU', 'cloud': False,
              'model': 'SSDLite320 MobileNetV3 COCO', 'path': str(path)}
    calls = []
    monkeypatch.setattr(window.registry, 'execute', lambda name, args: calls.append((name, args)) or ToolResult(name, True, output))
    window.image_path.set(str(path))
    window.detect_people()
    pump(gui, lambda: not window.busy)
    assert '99.73%' in window.people_text.get('1.0', 'end')
    assert 'not who someone is' in window.people_text.get('1.0', 'end')
    assert '"score": 0.9973' in window.people_raw.get('1.0', 'end')
    assert len(window.people_canvas.find_all()) == 3  # image, box, detector-confidence label
    window.people_confidence.set('nan')
    window.detect_people()
    assert len(calls) == 1 and '0.05 and 0.95' in window.status.cget('text')
    window.known_person_name.set('Ada Example')
    window.known_person_org.set('Example Institute')
    window.research_known_person()
    panel = gui._research_studio._person_studio
    assert panel.query.get() == 'Ada Example' and panel.context.get() == 'Example Institute'
    assert not panel.busy and panel.job is None
    panel.close()
    gui._research_studio.withdraw()
    window.geometry('800x600+20+20')
    window.update()
    assert window.people_canvas.winfo_height() >= 60 and window.people_text.winfo_height() >= 60
    bounds = window.people_canvas.bbox(window.people_canvas.find_all()[0])
    assert bounds[0] >= -1 and bounds[1] >= -1
    assert bounds[2] <= window.people_canvas.winfo_width() + 1
    assert bounds[3] <= window.people_canvas.winfo_height() + 1
    window.people_confidence.set('0.50')
    from tests.test_gui_screenshots import _grab
    _grab(gui, '102-people-explained', window)


def test_person_discovery_handoff_carries_only_supplied_name_and_context(research_studio):
    parent = research_studio
    parent.open_person_studio()
    panel = parent._person_studio
    received = []
    parent.gui._open_networking = lambda: SimpleNamespace(find_professional_contacts=lambda **kw: received.append(kw) or True)
    panel.query.insert(0, 'Ada Example')
    panel.context.set('Example Institute')
    panel.profile.set('https://institute.example/ada')
    panel.find_contacts()
    assert received == [{'name': 'Ada Example', 'organization': 'Example Institute', 'official_url': 'https://institute.example/ada'}]
    assert panel.job is None and not panel.busy
    panel.close()


def test_main_enrolled_face_launcher_reuses_window_without_starting_tools(own_gui, monkeypatch):
    gui = own_gui
    calls = []
    monkeypatch.setattr(gui.runtime.registry, 'execute', lambda *a, **k: calls.append((a, k)))
    gui._open_enrolled_faces()
    window = gui._enrolled_faces_window
    gui._open_enrolled_faces()
    assert gui._enrolled_faces_window is window
    assert not calls and not window.consent.get() and not window.busy
    window.close()


def test_main_image_tool_launcher_prefills_reuses_and_never_starts_processing(own_gui, monkeypatch):
    gui = own_gui
    monkeypatch.setattr(gui.runtime.registry, 'execute', lambda *a, **k: (_ for _ in ()).throw(AssertionError('automatic processing')))
    gui._open_local_image_tools('first.png', 'other.png')
    window = gui._local_image_tools_window
    try:
        assert window.path.get() == 'first.png' and window.other.get() == 'other.png'
        assert not window.busy
        gui._open_local_image_tools('changed.png')
        assert gui._local_image_tools_window is window and window.path.get() == 'changed.png'
        assert not window.busy and window.last_result is None
    finally:
        window.close()


def test_tor_web_form_and_retry_preserve_explicit_mode(research_studio, monkeypatch):
    from app.research.jobs import ResearchJob
    panel = research_studio
    calls = []
    def start(query, *, asked, level, **options):
        calls.append((query, options))
        job = ResearchJob('tor-web', query, asked, level, '', status='done', tor_web=True)
        panel.jobs._jobs.append(job)
        return job
    monkeypatch.setattr(panel.jobs, 'start', start)
    panel.search_kind.set('Tor web (public internet)')
    panel._show_person_fields()
    panel.query.insert(0, 'artificial intelligence')
    panel.search()
    panel.again()
    assert calls == [('artificial intelligence', {'tor_web': True})] * 2
    assert 'Public internet through Tor' in panel.detail_status.get()


def test_rea_evidence_tab_verifies_saved_copy_and_detects_tampering(studio, tmp_path):
    import hashlib
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    path = tmp_path / 'source-00.js'
    path.write_bytes(b'const owned = 42;')
    item = {'path': str(path), 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'url': 'https://example.org/main.js', 'retrieved_at': '2026-10-24T12:00:00Z'}
    value = {'link_analysis': True, 'workspace': str(tmp_path), 'source_evidence': {'resources': [item]}, 'report': '{}'}
    studio.session.remember('reverse_engineer_link', {}, 180, True, value, .1)
    studio._refresh_history()
    studio.history.current(0)
    studio.show_history()
    studio.registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    studio.evidence_table.selection_set('0')
    studio.show_evidence()
    studio.verify_evidence_button.invoke()
    rea_pump(studio)
    assert 'Verified: yes' in studio.evidence_text.get('1.0', 'end')
    path.write_text('tampered')
    studio.show_evidence()
    studio.verify_evidence_button.invoke()
    rea_pump(studio)
    assert 'Verified: no' in studio.evidence_text.get('1.0', 'end')
    studio.output_tabs.select(2)
    studio.geometry('800x650+30+30')
    studio.update()
    assert studio.evidence_text.winfo_height() >= 75
    assert studio.evidence_table.winfo_height() >= 75
    assert studio.evidence_text.winfo_rooty() + studio.evidence_text.winfo_height() <= studio.winfo_rooty() + studio.winfo_height()
    from tests.test_gui_screenshots import _grab
    _grab(studio.gui, '103-rea-source-evidence', studio)
