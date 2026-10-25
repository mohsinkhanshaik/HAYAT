"""Actual desktop controls for model quality and evidence-based self-view."""
import json
from pathlib import Path
import statistics
import time


def test_brain_quality_controls_render_and_self_view(shared_tk):
    from tests.test_gui import _build_window, _release
    from tests.test_gui_screenshots import _grab
    from tests.test_gui_workspace_tools import pump

    gui = _build_window()
    try:
        gui.root.geometry('1280x900+20+20')
        gui.root.deiconify()
        gui._operator_suppress_auto_refresh = True
        gui.show_view('operator')
        gui._operator_suppress_auto_refresh = False
        gui.root.update()
        graph = gui.brain_graph
        graph.set_motion(False)
        gui._toggle_brain_appearance()
        assert 'Ultra' in gui.brain_detail_picker.cget('values') and 'Auto' in gui.brain_detail_picker.cget('values')
        gui.brain_mesh_detail.set('Ultra')
        gui.brain_detail_picker.event_generate('<<ComboboxSelected>>')
        gui.root.update()
        assert graph.geometry_status()['triangles'] == 4850
        assert '4,850 triangles' in gui.brain_mesh_note.get()
        _grab(gui, '70-brain-ultra-main')
        timing = {}
        for detail in ('Low', 'Normal', 'Ultra'):
            graph.set_surface('Folded', detail=detail)
            durations = []
            for _ in range(8):
                started = time.perf_counter()
                graph.angle += .025
                graph.redraw()
                durations.append((time.perf_counter() - started) * 1000)
            timing[detail] = {'triangles': len(graph._shell), 'median_frame_ms': round(statistics.median(durations), 3),
                              'visible_mesh_faces': len(graph.find_withtag('mesh'))}
        graph.mesh_detail = 'Auto'
        graph._inside, graph._idle_after = False, 0
        graph.set_motion(True)
        durations = []
        for _ in range(8):
            started = time.perf_counter()
            graph.angle += .025
            graph.redraw()
            durations.append((time.perf_counter() - started) * 1000)
        assert graph.geometry_status()['render_detail'] == 'Low' and len(graph._shell) == 384
        timing['Auto idle spin'] = {'triangles': len(graph._shell),
                                   'median_frame_ms': round(statistics.median(durations), 3)}
        graph.set_motion(False)
        gui.root.geometry('900x700+20+20')
        gui.brain_mesh_detail.set('Auto')
        gui.brain_detail_picker.event_generate('<<ComboboxSelected>>')
        gui.root.update()
        assert graph.mesh_detail == 'Auto' and graph.asset_loaded
        assert graph.winfo_ismapped() and graph.winfo_height() >= 100
        assert not gui.brain_timeline.winfo_ismapped()
        _grab(gui, '71-brain-auto-compact')
        gui.brain_history_button.invoke()
        gui.root.update()
        # More room can restore the full view; otherwise history has its own view.
        assert gui.brain_timeline.winfo_ismapped()
        if not graph.winfo_ismapped():
            gui.brain_history_button.invoke()
            gui.root.update()
        assert graph.winfo_ismapped()
        report = {'rendering': timing, 'scope': 'Measured Tk redraws on this host, not GPU/model inference speed.',
                  'auto_compact': graph.geometry_status()}
        (Path(__file__).resolve().parents[1] / '.test-runs/brain-render-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        gui._open_control_studio('self')
        studio = gui._control_studio
        studio.geometry('850x650+40+40')
        studio.inspect_self('state')
        pump(gui, lambda: not studio.busy, seconds=10)
        assert 'Task evidence in this session' in studio.self_result.get('1.0', 'end')
        _grab(gui, '72-operational-self-view', studio)
    finally:
        _release(gui)
