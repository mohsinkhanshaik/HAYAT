"""Opt-in rendered checks with fictional people and offline public/own camera fixtures."""
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')


def test_capture_public_research_and_camera_browsers(tmp_path):
    from app.gui_intelligence import IntelligenceWindow
    from app.gui_my_cameras import MyCamerasWindow
    from app.gui_research import ResearchStudio
    from app.intelligence.manager import IntelligenceManager
    from app.intelligence.my_cameras import MyCameras
    from app.intelligence.tools import register_intelligence_tools
    from app.research.jobs import ResearchJob, ResearchJobs
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_gui import _build_window
    from tests.test_gui_screenshots import _grab, _Overlay, _pump
    from tests.test_intelligence_resources import ExtendedFetcher
    from tests.test_my_cameras import Secrets

    window = _build_window()
    manager = None
    opened = []
    try:
        root = window.root
        root.deiconify()
        root.geometry('1280x820+0+0')
        registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
        fetcher = ExtendedFetcher()
        manager = IntelligenceManager(tmp_path / 'intelligence.db', fetcher, registry)
        register_intelligence_tools(registry, manager)
        manager.refresh(list(fetcher.payloads))
        manager.my_cameras = MyCameras(tmp_path / 'own-cameras.json', Secrets())
        for index in range(12):
            manager.my_cameras.add(f'Demo camera {index + 1}', f'rtsp://192.168.1.{index + 20}/stream')
        jobs = ResearchJobs(lambda: None)
        jobs._jobs.append(ResearchJob('demo-person', 'Public professional background of Jane Example', 'research person Jane Example',
                                     'deep', '', status='done', sources=2, confidence=.6,
                                     person={'name': 'Jane Example', 'context': 'Example Institute', 'profile_url': ''},
                                     answer='Public professional sources for Jane Example\n'
                                     'Name matches are unverified candidates. Sources may describe different people.\n\n'
                                     '[1] Example Institute — public biography\nhttps://institute.example/jane\n'
                                     'Published: not reported · Retrieved: 2026-10-17\n'
                                     '• Jane Example is a professor of physics at Example Institute.\n\n'
                                     '[2] Example Gallery — artist profile\nhttps://gallery.example/jane\n'
                                     '• Jane Example is an artist with an exhibition of sculpture.\n\n'
                                     'Not established: whether these sources describe the same person.'))
        window.runtime = _Overlay(window.runtime, intelligence=manager, registry=registry, research_jobs=jobs)
        studio = ResearchStudio(window)
        opened.append(studio)
        studio.search_kind.set('Person (public work)')
        studio._show_person_fields()
        studio.query.insert(0, 'Jane Example')
        studio.person_context.set('Example Institute')
        studio.tree.selection_set('demo-person')
        studio.show_selected()
        studio.attributes('-topmost', True)
        studio.geometry('860x700+0+0')
        _pump(window, .8)
        _grab(window, '45-public-person-research', studio)
        studio.geometry('720x560+0+0')
        studio.person_details_visible.set(False)
        studio._show_person_fields()
        _pump(window, .6)
        _grab(window, '46-public-person-compact', studio)
        studio.close()
        opened.remove(studio)

        explorer = IntelligenceWindow(window)
        opened.append(explorer)
        explorer.attributes('-topmost', True)
        explorer.geometry('1200x820+0+0')
        explorer.notebook.select(explorer.tabs['cameras'])
        _pump(window, 1.5)
        _grab(window, '47-public-camera-browser', explorer)
        explorer.close()
        opened.remove(explorer)

        own = MyCamerasWindow(window)
        opened.append(own)
        own.attributes('-topmost', True)
        own.geometry('820x620+0+0')
        own.tree.selection_set('mine:demo-camera-3')
        _pump(window, .7)
        _grab(window, '48-own-camera-browser', own)
    finally:
        for panel in opened:
            if panel.winfo_exists():
                panel.close() if hasattr(panel, 'close') else panel.destroy()
        if manager:
            manager.close()
        window.root.destroy()
