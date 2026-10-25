import json
import time
from types import SimpleNamespace

from app.gui_invention import InventionPanel
from app.gui_theme import Palette
from app.safety.permissions import PermissionManager
from app.tools.invention import register_invention_tools
from app.tools.registry import ToolRegistry
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate


def test_workbench_gui_creates_runs_and_reloads_a_measured_project(shared_tk, tmp_path):
    from tests.conftest import open_window, close_window
    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui-fixture'))
    root = open_window(shared_tk, '960x700-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)
    def finish():
        until = time.monotonic() + 5
        while panel._pending and time.monotonic() < until:
            root.update(); time.sleep(.01)
        assert not panel._pending
    try:
        panel.refresh(); finish()
        assert '0 saved' in panel.status.get()
        panel.goal.insert('1.0', 'Design a doubling formula')
        panel.tests.delete('1.0', 'end')
        panel.tests.insert('1.0', json.dumps([{'inputs': {'x': 2}, 'expected': 4}]))
        panel.create_button.invoke(); finish()
        assert panel.project_id
        panel.use_research.set(False)
        panel.run_button.invoke(); finish()
        assert 'TESTED' in panel.report.get('1.0', 'end')
        assert 'Passed: 1' in panel.report.get('1.0', 'end')
        assert panel.run_button.instate(['!disabled'])
        identifier = panel.project_id
        panel.refresh(); finish()
        panel.projects.selection_set(identifier)
        panel.open_project(); finish()
        assert panel.project_id == identifier and 'TESTED' in panel.status.get()
    finally:
        close_window(root)


def test_the_journal_tabs_chart_cases_trial_and_export(shared_tk, tmp_path):
    from tests.conftest import open_window, close_window
    model = ScriptedModel(candidate('x * 3'), accepted(), candidate('x * 2'), accepted())
    workbench = bench(tmp_path, model)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui-fixture'))
    root = open_window(shared_tk, '1100x760-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)

    def finish():
        until = time.monotonic() + 5
        while panel._pending and time.monotonic() < until:
            root.update(); time.sleep(.01)
        assert not panel._pending
    try:
        panel.goal.insert('1.0', 'Design a doubling formula')
        panel.tests.delete('1.0', 'end')
        panel.tests.insert('1.0', json.dumps([{'inputs': {'x': 2}, 'expected': 4}, {'inputs': {'x': 5}, 'expected': 10}]))
        assert panel.check_cases() and '2 cases over x · exact' in panel.case_note.cget('text')
        panel.create_button.invoke(); finish()
        panel.use_research.set(False)
        panel.cycles.set('2')
        panel.work_out_first.set(False)  # this test reads the model's two rounds
        panel.run_button.invoke(); finish()
        assert len(panel.data['rounds']) == 2 and panel.state_badge.cget('text') == 'TESTED'
        assert 'Numeric prototype' in panel.overview.get('1.0', 'end') and '2 of 2 cases passed' in panel.overview.get('1.0', 'end')
        root.update()
        panel.draw_chart()
        assert len(panel._chart_rounds) == 2
        # Clicking a round in the chart opens it, with its cases.
        left, right, number = panel._chart_rounds[0]
        panel.chart_click(SimpleNamespace(x=(left + right) / 2))
        assert panel.round_list.selection() == ('1',) and 'x * 3' in panel.round_text.get('1.0', 'end')
        assert [panel.case_table.item(row, 'values')[3] for row in panel.case_table.get_children()] == ['✗ failed', '✗ failed']
        assert '★ best' in panel.round_list.item('2', 'text')
        # Your own formula, graded on the same frozen cases and never saved.
        panel.formula.set('x + x')
        panel.try_button.invoke(); finish()
        assert 'your formula x + x (not saved)' in panel.cases_note.cget('text') and len(workbench.get(panel.project_id)['rounds']) == 2
        assert all(panel.case_table.item(row, 'values')[3] == '✓ passed' for row in panel.case_table.get_children())
        path = panel.export()
        assert path.exists() and path.read_text(encoding='utf-8').startswith('# Design a doubling formula')
    finally:
        close_window(root)
