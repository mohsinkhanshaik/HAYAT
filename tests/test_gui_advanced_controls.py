import math
import threading
import time

from tests.test_public_people_research import research as research


def pump(gui, predicate, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        gui.root.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'GUI operation did not finish'


def test_real_research_dashboard_stop_branch_colibri_and_3d(shared_tk, research, monkeypatch):
    from tests.test_gui import _build_window, _release
    from tests.test_gui_screenshots import _grab
    from app.gui_research import ResearchStudio
    gui = _build_window()
    panel = None
    try:
        gui.root.geometry('1280x900+20+20')
        gui.root.deiconify()
        manager, _ = research
        monkeypatch.setattr(gui.runtime, 'research', manager)
        gui.runtime.conversation.clear()
        gui.runtime.session.session_id = gui.runtime.conversation.session_id
        panel = ResearchStudio(gui)
        panel.open_person_studio()
        studio = panel._person_studio
        studio.geometry('1000x760+30+30')
        gui.root.update()
        assert studio.sources._hayat_empty.winfo_manager() == 'place', 'empty sources table should explain itself'
        studio.query.insert(0, 'Jane Example')
        studio.context.set('public work')
        studio.search()
        pump(gui, lambda: studio.report is not None)
        assert len(studio.sources.get_children()) == 2
        assert studio.sources._hayat_empty.winfo_manager() == ''
        studio.sources.selection_set(studio.sources.get_children())
        studio.compare()
        assert 'professor' in '\n'.join(p.get('1.0', 'end') for p in studio.comparison_sides)
        assert 'sculpture' in '\n'.join(p.get('1.0', 'end') for p in studio.comparison_sides)
        _grab(gui, '85-professional-research', studio)
        for _ in range(12):
            studio.geometry('760x580+30+30')
            gui.root.update()
            studio.sources.yview_moveto(1)
            studio.geometry('1000x760+30+30')
            gui.root.update()
        studio.tabs.select(studio.panels['evidence'])
        _grab(gui, '86-professional-evidence', studio)
        with monkeypatch.context() as browser_guard:
            browser_guard.setattr('app.gui_person_research.webbrowser.open', lambda _url: (_ for _ in ()).throw(AssertionError('privacy leak')))
            gui.runtime.authority.set_privacy_mode(True)
            studio.open_source()
            assert 'privacy' in studio.status.get().lower() and not studio.busy
            gui.runtime.authority.set_privacy_mode(False)
        from app.gui_person_research import RecordLibrary
        library = RecordLibrary(studio, studio.report)
        library.geometry('560x480+30+30')
        gui.root.update()
        def buttons(widget):
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton':
                    yield child
                yield from buttons(child)
        for button in buttons(library):
            assert button.winfo_ismapped(), button.cget('text')
            assert button.winfo_rootx() + button.winfo_width() <= library.winfo_rootx() + library.winfo_width(), button.cget('text')
        assert library.tree._hayat_empty.winfo_manager() == ('' if library.tree.get_children() else 'place')
        library.authorization.insert(0, 'I own this synthetic research report fixture')
        library.consent.set(True)
        before = set(library.tree.get_children())
        library.save()
        saved = set(library.tree.get_children()) - before
        assert len(saved) == 1
        rid = saved.pop()
        library.tree.selection_set(rid)
        library.open()
        assert studio.report['sources']
        library.revoke()
        assert not next(row for row in library.vault.list() if row['id'] == rid)['accessible']
        library.tree.selection_set(rid)
        library.open()
        assert 'consent' in library.status.cget('text').lower()
        library.tree.selection_set(rid)
        library.delete()
        assert not library.tree.exists(rid)
        library.destroy()
        studio.close()
        panel.close()
        panel = None

        # The same real camera API drives the main tools, brain and voice.
        scene = gui.depth_nav
        scene.set_motion(False)
        scene.camera_view('Right')
        assert math.isclose(scene.angle, math.pi / 2)
        scene.remember_camera()
        saved = scene.angle, scene.pitch, scene.zoom
        scene.camera_view('Above')
        assert scene.pitch < -.5
        scene.restore_camera()
        assert (scene.angle, scene.pitch, scene.zoom) == saved
        gui._operator_suppress_auto_refresh = True
        gui.show_view('operator')
        gui.brain_graph.set_motion(False)
        gui.brain_graph.camera_view('Front')
        assert gui.brain_graph.pitch == 0
        gui.show_view('chat')

        # Branching changes the real database context and stages the editable input.
        mid = gui.runtime.memory.record_message(gui.runtime.session.session_id, 'user', 'Plan trains to Boston')
        gui.runtime.memory.record_message(gui.runtime.session.session_id, 'assistant', 'Old discarded answer')
        gui._open_chat_rewind()
        rewind = gui._rewind_window
        rewind.tree.selection_set(str(mid))
        rewind.branch(True)
        assert gui.input.get('1.0', 'end').strip() == 'Plan trains to Boston'
        assert not gui.runtime.memory.conversation_history(gui.runtime.session.session_id)

        # Colibri button requests execute off Tk, without changing default models.
        gui._open_control_studio('models')
        controls = gui._control_studio
        pump(gui, lambda: not controls.busy)
        calls = []
        monkeypatch.setattr(controls.colibri, 'start', lambda backend: calls.append((backend, threading.get_ident())) or {'started': True})
        controls.start_colibri()
        pump(gui, lambda: (controls.update_live({}, {}, False), not controls.busy)[1])
        assert calls and calls[0][1] != threading.get_ident()
        controls.withdraw()

        gui.emergency_button.invoke()
        assert gui.runtime.registry.interlock.halted
        pump(gui, lambda: not getattr(gui, '_emergency_stopping', True))
        gui.input.delete('1.0', 'end')
        gui.input.insert('1.0', 'do not send while halted')
        gui.send()
        assert gui.input.get('1.0', 'end').strip() == 'do not send while halted'
        assert gui._working_since is None
        gui.root.update()
        assert gui.emergency_button.winfo_viewable()
        gui.root.geometry('900x700+20+20')
        gui.root.update()
        assert gui.emergency_button.winfo_viewable() and gui.resume_tools_button.winfo_viewable()
        assert gui.resume_tools_button.winfo_width() >= gui.resume_tools_button.winfo_reqwidth()
        assert gui.emergency_button.winfo_rootx() + gui.emergency_button.winfo_width() <= gui.root.winfo_rootx() + gui.root.winfo_width()
        _grab(gui, '87-emergency-stop')
        gui.resume_tools_button.invoke()
        assert not gui.runtime.registry.interlock.halted
        # Stop & correct must work even before the worker updates session state.
        monkeypatch.setattr(gui, '_work', lambda _text: None)
        gui.runtime.session.last_user_input = 'previous request'
        gui.input.delete('1.0', 'end')
        gui.input.insert('1.0', 'new request just sent')
        gui.send()
        gui._stop_and_correct()
        assert gui.input.get('1.0', 'end').strip() == 'new request just sent'
        gui._finish_reply()
    finally:
        if panel and panel.winfo_exists():
            panel.close()
        _release(gui)
