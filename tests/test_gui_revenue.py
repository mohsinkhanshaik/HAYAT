from types import SimpleNamespace

from app.gui_revenue import RevenueWindow
from app.gui_theme import Palette
from app.revenue import RevenueWorkspace
from app.revenue_catalog import CATALOG
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.tools.registry import ToolRegistry
from tests.conftest import open_window, close_window


def test_manual_earning_form_runs_and_refreshes_real_action_records(shared_tk, tmp_path):
    import time
    from app.revenue import register_revenue_tools
    parent = open_window(shared_tk, '1000x820-4000-4000')
    permission = PermissionManager({'default': 'ALLOW'}, tmp_path)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permission)
    workspace = RevenueWorkspace(ledger, ToolRegistry(permission))
    register_revenue_tools(workspace.registry, workspace)
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(revenue=workspace)
    window = RevenueWindow(gui)
    intake = window.open_intake('income_execution_plan')
    try:
        for key, value in {'skills': 'python data', 'buyer': 'Test buyer', 'offer': 'Test CSV service',
                           'deliverable': 'Clean CSV and report'}.items():
            intake.fields[key].set(value)
        intake.run()
        deadline = time.monotonic() + 5
        while intake.busy and time.monotonic() < deadline:
            parent.update()
            time.sleep(.02)
        assert not intake.busy
        assert intake.status.get().startswith('Completed')
        assert window.stat_values['actions'].get() == '7'
        assert len(window.records['Artifacts'].get_children()) == 1
        assert intake.fields['offer'].get() == 'Test CSV service'
    finally:
        intake.destroy()
        window.destroy()
        close_window(parent)


def test_revenue_workspace_filters_workflows_and_prepares_chat_without_running(shared_tk, tmp_path):
    parent = open_window(shared_tk, '1000x820-4000-4000')
    permission = PermissionManager({'default': 'ALLOW'}, tmp_path)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permission)
    workspace = RevenueWorkspace(ledger, ToolRegistry(permission))
    used = []
    class GUI(Palette):
        def use_example(self, text):
            used.append(text)
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(revenue=workspace)
    window = RevenueWindow(gui)
    try:
        window.update()
        assert len(window.tools.get_children()) == len(CATALOG)
        assert window.use_button.winfo_height() >= window.use_button.winfo_reqheight()
        assert (window.use_button.winfo_rooty() + window.use_button.winfo_height()
                <= window.winfo_rooty() + window.winfo_height())
        window.query.set('pricing engine')
        assert window.tools.get_children() == ('pricing_engine',)
        assert 'hours, hourly_cost' in window.details.get('1.0', 'end')
        window.use_selected()
        assert used[0].startswith('pricing_engine ')
        assert workspace.artifacts() == []
    finally:
        if window.winfo_exists():
            window.destroy()
        close_window(parent)


def test_income_actions_and_osiris_button_use_current_records(shared_tk, tmp_path):
    from app.revenue import register_revenue_tools
    parent = open_window(shared_tk, '1000x820-4000-4000')
    permission = PermissionManager({'default': 'ALLOW'}, tmp_path)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permission)
    workspace = RevenueWorkspace(ledger, ToolRegistry(permission))
    register_revenue_tools(workspace.registry, workspace)
    workspace.execute('income_action_queue', action='create', key='pilot', data={
        'title': 'Prepare sample', 'tool': 'data_cleanup_service', 'priority': 1})
    used = []
    class GUI(Palette):
        def use_example(self, text):
            used.append(text)
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(revenue=workspace)
    window = RevenueWindow(gui)
    try:
        assert window.records['Income actions'].get_children() == ('income-pilot',)
        assert window.records['Income actions'].item('income-pilot')['text'] == 'Prepare sample'
        window.group.set('Osiris')
        assert set(window.tools.get_children()) == {'osiris_status', 'osiris_playbook'}
        window.use_osiris()
        assert used == ['Show Osiris playbook']
        assert workspace.records('income_action_queue')[0]['data']['status'] == 'planned'
    finally:
        if window.winfo_exists():
            window.destroy()
        close_window(parent)


def test_revenue_workspace_displays_existing_pipeline_and_experiment_evidence(shared_tk, tmp_path):
    parent = open_window(shared_tk, '1000x820-4000-4000')
    permission = PermissionManager({'default': 'ALLOW'}, tmp_path)
    ledger = BusinessLedger(tmp_path / 'business.db', tmp_path / 'artifacts/business', permission)
    workspace = RevenueWorkspace(ledger, ToolRegistry(permission))
    ledger.add_lead('Actual tracked opportunity', 'Owner')
    workspace.execute('revenue_experiment_manager', action='record', key='trial-1',
                      data={'revenue': 100, 'costs': 20, 'hours': 2, 'currency': 'USD'})
    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(revenue=workspace)
    window = RevenueWindow(gui)
    try:
        assert len(window.records['Pipeline'].get_children()) == 1
        assert window.records['Experiments'].item('experiment-trial-1', 'values')[0] == '80 USD'
        assert 'no revenue recorded' in window.summary.get()
        window.records['Pipeline'].selection_set(window.records['Pipeline'].get_children()[0])
        details = window.show_record('Pipeline')
        assert details.winfo_exists()
        details.destroy()
    finally:
        window.destroy()
        close_window(parent)
