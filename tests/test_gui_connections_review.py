"""Regressions from independent review: navigation stays read-only and runs retain runtime guards."""
import threading
from types import SimpleNamespace

import pytest

from app.gui_features import run_declared_tool
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.conftest import close_window
from tests.test_gui_connections import make_window, settle
from tests.test_location_privacy import shield_at_home


def dispose(root, gui, window):
    if window.winfo_exists():
        window.destroy()
    gui.activity_monitor.close()
    close_window(root)


def test_a_newly_registered_tool_can_be_inspected_from_a_refreshed_graph(shared_tk, tmp_path):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        gui.runtime.registry.register('late_registered_fixture', 'A synthetic late registration.',
                                      {'type': 'object', 'properties': {}}, 'low', 'system_read',
                                      lambda: called.append('late executed'))
        view = window.connections
        view.refresh()
        settle(root, lambda: view.snapshot is not None)
        view.tree.selection_set('tools')
        view.show()
        assert 'late_registered_fixture' in view.tool_tree.get_children()
        view.tool_tree.selection_set('late_registered_fixture')
        view.inspect_tool()
        assert window.current.tool.name == 'late_registered_fixture'
        assert window.tabs.select() == str(window.catalog_page)
        assert not called
    finally:
        dispose(root, gui, window)


def test_opening_brain_during_the_first_inventory_read_needs_no_selected_node(shared_tk, tmp_path, monkeypatch):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    started, release = threading.Event(), threading.Event()

    def blocked(_runtime):
        started.set()
        assert release.wait(3)
        return {'nodes': [], 'tools': []}

    monkeypatch.setattr('app.system_insight.build_snapshot', blocked)
    try:
        view = window.connections
        view.refresh()
        assert started.wait(1)
        assert view.snapshot is None
        view.open_brain()
        gui.show_view.assert_called_once_with('operator')
        assert view.selected is None and not called
        release.set()
        settle(root, lambda: not view._busy)
    finally:
        release.set()
        dispose(root, gui, window)


def test_manual_runs_respect_a_latched_emergency_stop(tmp_path):
    called = []
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    registry.register('local_fixture', 'In-memory fixture.', {'type': 'object', 'properties': {}},
                      'low', 'system_read', lambda: called.append(True))
    registry.interlock.halt()
    result = run_declared_tool(SimpleNamespace(registry=registry), 'local_fixture', {})
    assert not result.success and 'Emergency stop' in result.error
    assert not called


@pytest.mark.parametrize('resume_before_approval', [False, True])
def test_emergency_stop_revokes_a_manual_run_waiting_for_approval(tmp_path, resume_before_approval):
    started, release = threading.Event(), threading.Event()
    called, results = [], []

    def approve(*_args):
        started.set()
        assert release.wait(3)
        return True

    registry = ToolRegistry(PermissionManager({'default': 'ASK'}, tmp_path), approve)
    registry.register('local_fixture', 'In-memory fixture.', {'type': 'object', 'properties': {}},
                      'low', 'system_read', lambda: called.append(True))
    thread = threading.Thread(target=lambda: results.append(
        run_declared_tool(SimpleNamespace(registry=registry), 'local_fixture', {})))
    thread.start()
    try:
        assert started.wait(1)
        registry.interlock.halt()
        if resume_before_approval:
            registry.interlock.resume()
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and len(results) == 1
    assert not results[0].success and not called
    assert 'emergency stop' in results[0].error.lower()


def test_manual_tools_keep_actual_location_screening_without_network_io(tmp_path):
    sent, looked_up = [], []
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    registry.location_shield = shield_at_home()  # synthetic fixture location, not a device reading
    registry.register('fake_send', 'In-memory send stand-in.',
                      {'type': 'object', 'properties': {'body': {'type': 'string'}}, 'required': ['body']},
                      'low', 'send_message', lambda body: sent.append(body))
    registry.register('intelligence_weather', 'In-memory lookup stand-in.',
                      {'type': 'object', 'properties': {'lat': {'type': 'number'}, 'lon': {'type': 'number'}}},
                      'low', 'public_web_read', lambda **args: looked_up.append(args) or args)
    runtime = SimpleNamespace(registry=registry)
    result = run_declared_tool(runtime, 'fake_send', {'body': 'Meet at 42.47341, -83.22190'})
    assert not result.success and 'precise coordinates' in result.error and not sent
    position = {'lat': 42.47341, 'lon': -83.22190}
    lookup = run_declared_tool(runtime, 'intelligence_weather', position)
    assert lookup.success and looked_up == [{'lat': 42.5, 'lon': -83.2}]
    assert position == {'lat': 42.47341, 'lon': -83.22190}


def test_declared_graph_does_not_claim_measured_health_without_saved_checks(shared_tk, tmp_path):
    root, gui, window, called = make_window(shared_tk, tmp_path)
    try:
        view = window.connections
        view.refresh()
        settle(root, lambda: view.snapshot is not None)
        view.tree.selection_set('research')
        view.show()
        assert view.tree.item('research', 'values')[0] == 'Not checked'
        assert 'not a live engine test' in view.stamp.get()
        evidence = view.evidence.get('1.0', 'end')
        assert 'No observation has been recorded' in evidence
        assert 'not measured neural activity' in evidence
        assert not called and gui.activity_monitor.snapshot() == ([], 0)
    finally:
        dispose(root, gui, window)
