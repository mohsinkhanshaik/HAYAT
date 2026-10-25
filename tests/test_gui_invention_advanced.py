"""Exercise the workbench controls against real saved projects and simulations."""
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

from app.gui_invention import InventionPanel
from app.gui_theme import Palette
from app.safety.permissions import PermissionManager
from app.tools.invention import register_invention_tools
from app.tools.registry import ToolRegistry
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate


@pytest.fixture
def studio(shared_tk, tmp_path):
    from tests.conftest import close_window, open_window
    workbench = bench(tmp_path, ScriptedModel(candidate('x * 3'), accepted(), candidate('x * 2'), accepted()))
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench,
                              authority=SimpleNamespace(privacy_mode=False),
                              session=SimpleNamespace(session_id='invention-gui-test', effective_modes=set()))
    root = open_window(shared_tk, '1120x780-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)
    root.update()
    try:
        yield panel, root, workbench
    finally:
        close_window(root)


def finish(panel, root):
    deadline = time.monotonic() + 6
    while panel._pending and time.monotonic() < deadline:
        root.update()
        time.sleep(.01)
    assert not panel._pending, 'The GUI operation did not finish.'


def create(panel, root):
    panel.goal.insert('1.0', 'Find a formula that doubles x')
    panel.tests.delete('1.0', 'end')
    panel.tests.insert('1.0', json.dumps([{'inputs': {'x': 2}, 'expected': 4},
                                        {'inputs': {'x': 5}, 'expected': 10}]))
    panel.create_project()
    finish(panel, root)
    assert panel.project_id


def test_examples_create_real_frozen_project_without_running_model(studio, request):
    panel, root, workbench = studio
    panel.example.set('Sensor calibration')
    assert panel.load_example()
    assert panel.project_id is None
    assert len(panel._cases_from_box()) == 4
    if os.getenv('HAYAT_SHOTS') == '1':
        from tkinter import ttk
        from app.gui_theme import style_controls, style_hayat_buttons
        from tests.test_gui_screenshots import _grab
        style = ttk.Style(root)
        previous = style.theme_use()
        style.theme_use('clam')
        style_controls(root, Palette)
        style_hayat_buttons(root, Palette)
        request.addfinalizer(lambda: style.theme_use(previous))
        root.geometry('1000x760+20+20')
        root.update()
        _grab(SimpleNamespace(root=root), '94-invention-starter-example')
    panel.create_project()
    finish(panel, root)
    project = workbench.get(panel.project_id)
    assert len(project['tests']) == 4
    assert not project['rounds']
    assert 'Illustrative synthetic' in project['constraints'][0]
    assert panel.work_button.instate(['!disabled'])


def test_loading_example_preserves_saved_project_and_can_cancel_unsaved_changes(studio, monkeypatch):
    panel, root, workbench = studio
    create(panel, root)
    saved_id = panel.project_id
    original = workbench.get(saved_id)
    monkeypatch.setattr('tkinter.messagebox.askyesno', lambda *args, **kwargs: False)
    panel.example.set('Delivery cost')
    assert not panel.load_example()
    assert panel.project_id == saved_id
    monkeypatch.setattr('tkinter.messagebox.askyesno', lambda *args, **kwargs: True)
    assert panel.load_example()
    assert panel.project_id is None and panel.data is None
    assert workbench.get(saved_id) == original
    assert not panel.round_list.get_children()
    assert not panel.source_list.get_children()
    assert panel.source_button.instate(['disabled'])


def test_compact_workbench_reflows_and_retains_scroll_access(studio):
    panel, root, _ = studio
    assert str(panel.body.cget('orient')) == 'horizontal'
    count = len(root.winfo_children())
    for _ in range(3):
        root.geometry('760x640-4000-4000')
        root.update()
        assert str(panel.body.cget('orient')) == 'vertical'
        assert panel.workspace.winfo_height() >= 150
        canvas = panel.editor_column.canvas
        canvas.yview_moveto(1)
        root.update()
        assert canvas.yview()[1] == pytest.approx(1)
        assert canvas.yview()[0] > 0, 'The tall editor must scroll rather than cut off its saved projects.'
        assert panel.projects.cget('yscrollcommand') and panel.projects.cget('xscrollcommand')
        assert panel.case_table.cget('xscrollcommand')
        root.geometry('1120x780-4000-4000')
        root.update()
        assert str(panel.body.cget('orient')) == 'horizontal'
    assert len(root.winfo_children()) == count


@pytest.mark.parametrize(('inputs', 'expected'), [
    ('x=2 garbage', '4'), ('x=2, x=3', '4'), ('x=2, y=NaN', '4'), ('x=2,', '4'),
    ('x=1e31', '4'), ('x=2', 'NaN'), ('x=2', '1e-101'),
])
def test_case_builder_never_silently_drops_invalid_input(studio, inputs, expected):
    panel, _, _ = studio
    before = panel.tests.get('1.0', 'end')
    panel.case_inputs.set(inputs)
    panel.case_expected.set(expected)
    assert not panel.add_case()
    assert panel.tests.get('1.0', 'end') == before
    assert not panel._pending


def test_scientific_notation_is_preserved_and_json_numeric_checks_match_backend(studio):
    panel, root, _ = studio
    panel.case_inputs.set('x=2e-2, y=+.5')
    panel.case_expected.set('-1.2e3')
    assert panel.add_case()
    data = json.loads(panel.tests.get('1.0', 'end'))
    assert data == [{'inputs': {'x': '2e-2', 'y': '+.5'}, 'expected': '-1.2e3'}]
    for bad in ('true', 'NaN', '"not numeric"', '1e100'):
        panel.tests.delete('1.0', 'end')
        panel.tests.insert('1.0', '[{"inputs":{"x":2},"expected":' + bad + '}]')
        assert not panel.check_cases()
    panel.tests.delete('1.0', 'end')
    panel.tests.insert('1.0', '[{"inputs":{"x":true},"expected":4}]')
    assert not panel.check_cases()
    panel.create_project()
    root.update()
    assert panel.project_id is None and not panel._pending


def test_round_reuse_comparison_and_curve_probe_use_actual_results(studio):
    panel, root, workbench = studio
    create(panel, root)
    panel.cycles.set('2')
    panel.use_research.set(False)
    panel.work_out_first.set(False)
    panel.run_project()
    finish(panel, root)
    assert len(panel.data['rounds']) == 2
    evidence = panel.evidence_text.get('1.0', 'end')
    assert 'NUMERIC_CASES_PASSED' in evidence and 'physical' in evidence.lower()
    panel.round_list.selection_set('1')
    panel.round_selected()
    panel.compare_round()
    comparison = panel.round_text.get('1.0', 'end')
    assert 'x * 3' in comparison and 'x * 2' in comparison
    assert '0/2 frozen cases passed' in comparison and '2/2 frozen cases passed' in comparison
    panel.use_round_button.invoke()
    assert panel.formula.get() == 'x * 3'
    panel.try_button.invoke()
    finish(panel, root)
    assert panel.trial['measurement']['passed'] == 0
    assert len(workbench.get(panel.project_id)['rounds']) == 2
    panel.simulate_mine_button.invoke()
    finish(panel, root)
    root.update()
    assert panel.simulation['formula'] == 'x * 3'
    assert 'x' in panel.sweep_picker.cget('values')
    panel.simulation_input.set('x')
    panel.draw_simulation()
    region = panel._sim_regions[0]
    px, py, x, y = region['points'][len(region['points']) // 2]
    panel.probe_simulation(SimpleNamespace(x=px, y=py))
    assert 'computed sample' in panel.simulation_probe.get()
    assert f'{y:.6g}' in panel.simulation_probe.get()
    assert y == pytest.approx(x * 3)
    assert panel.sim_chart.find_withtag('probe')


def test_invalid_cycle_and_uncertainty_fields_do_not_launch(studio):
    panel, root, _ = studio
    create(panel, root)
    panel.cycles.set('broken')
    panel.run_project()
    assert not panel._pending and '1 to 8' in panel.status.get()
    for value in ('NaN', '0', '51', 'broken'):
        panel.spread.set(value)
        panel.simulate_best()
        assert not panel._pending and '1 to 50' in panel.status.get()


def test_experiment_draft_uses_recorded_gaps_without_executing_tools(studio):
    panel, root, workbench = studio
    create(panel, root)
    drafts = []
    panel.compose = drafts.append
    before = workbench.get(panel.project_id)
    panel.draft_experiment()
    assert len(drafts) == 1 and panel.project_id in drafts[0]
    assert 'uncertainty' in drafts[0] and 'plan for my review' in drafts[0]
    assert not panel._pending
    assert workbench.get(panel.project_id)['rounds'] == before['rounds']


def source_fixture(panel):
    panel.data = {'research': {'sources': [{'title': 'A documented source', 'url': 'https://example.org/paper',
                                           'snippet': 'Evidence excerpt', 'retrieved_at': '2026-10-23T12:00:00+00:00'}]}}
    panel.paint_research()
    panel.source_list.selection_set('0')
    panel.source_selected()


def test_source_explorer_checks_url_in_worker_and_opens_selected_publisher(studio, monkeypatch):
    panel, root, _ = studio
    source_fixture(panel)
    calls = []
    main_thread = threading.get_ident()

    def validate(url):
        assert threading.get_ident() != main_thread
        calls.append(('checked', url))
        return url

    monkeypatch.setattr('app.research.safety.validate_public_url', validate)
    monkeypatch.setattr('webbrowser.open', lambda url: calls.append(('opened', url)) or True)
    panel.source_button.invoke()
    finish(panel, root)
    assert calls == [('checked', 'https://example.org/paper'), ('opened', 'https://example.org/paper')]
    assert '2026-10-23' in panel.research_text.get('1.0', 'end')
    panel.runtime.authority.privacy_mode = True
    panel.open_source()
    assert len(calls) == 2 and not panel._pending


def test_emergency_stop_cancels_source_even_if_resumed_before_dns_returns(studio, monkeypatch):
    panel, root, _ = studio
    source_fixture(panel)
    entered, release = threading.Event(), threading.Event()
    opened = []

    def validate(url):
        entered.set()
        assert release.wait(3)
        return url

    monkeypatch.setattr('app.research.safety.validate_public_url', validate)
    monkeypatch.setattr('webbrowser.open', lambda url: opened.append(url))
    panel.open_source()
    try:
        assert entered.wait(2)
        panel.runtime.registry.interlock.halt()
        panel.runtime.registry.interlock.resume()
    finally:
        release.set()
    finish(panel, root)
    assert not opened and 'cancelled by the emergency stop' in panel.status.get()


def test_local_source_url_and_halted_work_are_rejected(studio, monkeypatch):
    panel, root, workbench = studio
    create(panel, root)
    source_fixture(panel)
    panel._source_rows['0']['url'] = 'http://127.0.0.1/private'
    monkeypatch.setattr('webbrowser.open', lambda _url: pytest.fail('Unsafe address must not open.'))
    panel.open_source()
    finish(panel, root)
    assert 'not permitted' in panel.status.get()
    panel.runtime.registry.interlock.halt()
    before = len(workbench.list())
    panel.create_project()
    assert not panel._pending and len(workbench.list()) == before
    assert panel.export() is None
    assert 'Emergency stop' in panel.status.get()


def test_required_tor_route_never_uses_direct_dns_or_an_ordinary_browser(studio, monkeypatch):
    from app.privacy_masks import ROUTE
    panel, root, _ = studio
    source_fixture(panel)
    monkeypatch.setattr('app.research.safety.validate_public_url', lambda _url: pytest.fail('No direct DNS under Tor.'))
    monkeypatch.setattr('webbrowser.open', lambda _url: pytest.fail('No ordinary browser under Tor.'))
    ROUTE.set('socks5://127.0.0.1:9052')
    panel.open_source()
    assert not panel._pending and 'masked browser' in panel.status.get()
    ROUTE.set(None)


@pytest.mark.skipif(os.getenv('HAYAT_INVENTION_SHOTS') != '1', reason='manual invention GUI visual verification')
def test_capture_invention_experiments_and_compact_evidence(studio):
    from tkinter import ttk
    from app.gui_theme import apply_theme
    from tests.test_gui_screenshots import _grab
    panel, root, _ = studio
    style = ttk.Style(root)
    previous = style.theme_use()
    try:
        style.theme_use('clam')
        apply_theme(root, Palette, 'Dark')
        create(panel, root)
        panel.work_out()
        finish(panel, root)
        panel.simulate_best()
        finish(panel, root)
        root.geometry('1120x780+20+20')
        root.update()
        _grab(None, '89-invention-experiments', widget=root)
        root.geometry('760x640+20+20')
        panel.tabs.select(5)
        root.update()
        _grab(None, '90-invention-compact-evidence', widget=root)
    finally:
        style.theme_use(previous)
