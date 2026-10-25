"""Feature discovery and unattended work remain explicit and observable."""
from datetime import UTC, datetime
import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

from app import capabilities
from app.gui import HayatGUI
from app.gui_activity import ActivityMonitor, ActivityWindow, live_state
from app.gui_features import FeaturesWindow, feature_inventory, inventory_commands, open_area, run_declared_tool
from app.gui_icons import icon_image, icon_kind
from app.gui_theme import Palette
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.conftest import close_window, open_window


class Bus:
    def __init__(self):
        self.listeners = {}
        self.sequence = 0

    def subscribe(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)
        return lambda: self.listeners[name].remove(callback)

    def publish(self, name, source, payload, **metadata):
        self.sequence += 1
        row = {'event_id': str(self.sequence), 'event_type': name, 'source': source,
               'timestamp': datetime.now(UTC).isoformat(), 'payload': payload, **metadata}
        for callback in self.listeners.get(name, []):
            callback(row)


def test_activity_tracks_concurrent_tools_redacts_credentials_and_releases_subscriptions():
    bus = Bus()
    monitor = ActivityMonitor(bus)
    def publish():
        bus.publish('TOOL_STARTED', 'tools', {'tool': 'read_file', 'arguments': {'password': 'very-private-password'}}, task_id='one')
        bus.publish('TOOL_STARTED', 'tools', {'tool': 'read_file'}, task_id='two')
        bus.publish('TOOL_FAILED', 'tools', {'tool': 'read_file', 'error': 'Missing file'}, task_id='one')
    thread = threading.Thread(target=publish)
    thread.start()
    thread.join()
    rows, active = monitor.snapshot()
    assert active == 1 and len(rows) == 3
    assert 'very-private-password' not in str(rows)
    assert 'Missing file' in rows[0]['details']
    bus.publish('TOOL_COMPLETED', 'tools', {'tool': 'read_file'}, task_id='two')
    assert monitor.snapshot()[1] == 0
    for _ in range(510):
        bus.publish('RESEARCH_STARTED', 'research', {})
    assert len(monitor.snapshot()[0]) == 500
    monitor.close()
    assert all(not callbacks for callbacks in bus.listeners.values())


def build_gui(root, tmp_path):
    bus = Bus()
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    registry.events = bus
    registry.register('test_add', 'Add two numbers locally.', {
        'type': 'object', 'properties': {'number': {'type': 'integer'}}, 'required': ['number'],
        'additionalProperties': False}, 'low', 'filesystem_read', lambda number: {'total': number + 2})
    class GUI(Palette):
        VIEWS, CONTROLS = HayatGUI.VIEWS, HayatGUI.CONTROLS
    gui = GUI()
    gui.root = root
    gui._camera_on = False
    gui.runtime = SimpleNamespace(registry=registry, events=bus, authority=SimpleNamespace(privacy_mode=False),
                                  voice=None, learner=SimpleNamespace(paused=False, running=True), intelligence=None)
    gui.activity_monitor = ActivityMonitor(bus)
    for method in {method for _, method, _ in gui.CONTROLS} | {area.opens for area in capabilities.CATALOG if area.opens and ':' not in area.opens} | {
            '_open_features', '_open_activity', '_open_control_studio', '_open_products', '_open_intelligence', '_open_knowledge',
            'show_view', 'use_example', '_report'}:
        setattr(gui, method, Mock())
    gui.pending = queue.Queue()
    gui._post = lambda callback, *args: gui.pending.put((callback, args))
    return gui


def test_inventory_includes_every_real_tool_workspace_control_and_capability(shared_tk, tmp_path):
    root = open_window(shared_tk, hidden=True)
    gui = build_gui(root, tmp_path)
    try:
        rows = feature_inventory(gui)
        assert len({row.key for row in rows}) == len(rows)
        assert {row.tool.name for row in rows if row.tool} == set(gui.runtime.registry.names())
        assert {row.capability.key for row in rows if row.capability} == set(capabilities.BY_KEY)
        assert {row.key[5:] for row in rows if row.key.startswith('view:')} == {view[0] for view in gui.VIEWS}
        commands = inventory_commands(gui)
        next(command for command in commands if command.label == 'Test add').callback()
        gui._open_features.assert_called_once_with('test_add')
        assert next(row for row in rows if row.key == 'area:public_intelligence').matches('earthquake')
        open_area(gui, capabilities.BY_KEY['public_intelligence'])
        gui._open_intelligence.assert_called_once_with()
        open_area(gui, capabilities.BY_KEY['chat'])
        gui.use_example.assert_called_once()
    finally:
        gui.activity_monitor.close()
        close_window(root)


def test_feature_search_is_read_only_and_explicit_run_validates_and_reports(shared_tk, tmp_path):
    root = open_window(shared_tk, '1100x850-4000-4000')
    gui = build_gui(root, tmp_path)
    window = FeaturesWindow(gui)
    try:
        window.query.set('test_add')
        root.update()
        assert list(window.shown) == ['tool:test_add']
        assert gui.activity_monitor.snapshot() == ([], 0)
        window.run_tool()
        assert 'Check arguments' in window.feedback.get()
        assert gui.activity_monitor.snapshot() == ([], 0)
        window.arguments.delete('1.0', 'end')
        window.arguments.insert('1.0', '{"number": 5}')
        window.run_tool()
        deadline = time.monotonic() + 4
        while gui.pending.empty() and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        callback, args = gui.pending.get_nowait()
        callback(*args)
        assert not window._busy
        gui._report.assert_called_once()
        assert '7' in gui._report.call_args.args[1]
        assert [row['event_type'] for row in gui.activity_monitor.snapshot()[0]] == ['TOOL_COMPLETED', 'TOOL_STARTED', 'GUI_TOOL_REQUESTED']
        window.query.set('no such feature anywhere')
        root.update()
        assert not window.shown and window.current is None
        assert window.open_button.instate(['disabled'])
        assert 'No matching' in window.title_var.get()
    finally:
        window.destroy()
        gui.activity_monitor.close()
        close_window(root)


def test_live_state_keeps_sensor_state_visible_even_with_privacy_enabled(shared_tk, tmp_path):
    root = open_window(shared_tk, hidden=True)
    gui = build_gui(root, tmp_path)
    try:
        gui._camera_on = True
        gui.runtime.authority.privacy_mode = True
        gui.runtime.voice = SimpleNamespace(status=lambda: {'enabled': True, 'state': 'listening', 'muted': True})
        state = live_state(gui)
        assert 'Camera: on' in state and 'Mic: paused' in state and 'Privacy: on' in state
        assert 'Public feeds: stopped' in state and 'Learning: running' in state
    finally:
        gui.activity_monitor.close()
        close_window(root)


def test_manual_tools_keep_existing_permissions_and_do_not_reset_chat_budgets(shared_tk, tmp_path):
    root = open_window(shared_tk, hidden=True)
    gui = build_gui(root, tmp_path)
    try:
        registry = gui.runtime.registry
        registry.budget.total = registry.budget.max_total_calls
        result = run_declared_tool(gui.runtime, 'test_add', {'number': 3})
        assert result.success and result.output == {'total': 5}
        assert registry.budget.total == registry.budget.max_total_calls
        registry.permissions = PermissionManager({'default': 'DENY'}, tmp_path)
        result = run_declared_tool(gui.runtime, 'test_add', {'number': 3})
        assert not result.success
        rows, active = gui.activity_monitor.snapshot()
        assert active == 0 and rows[0]['event_type'] == 'GUI_TOOL_REFUSED'
        assert 'LOCAL_OWNER' in rows[0]['details'] and 'filesystem_read' in rows[0]['details']
    finally:
        gui.activity_monitor.close()
        close_window(root)


def test_activity_window_filters_keeps_selection_and_does_not_pause_on_close(shared_tk, tmp_path):
    root = open_window(shared_tk, '1100x850-4000-4000')
    gui = build_gui(root, tmp_path)
    window = ActivityWindow(gui)
    try:
        gui.runtime.events.publish('TOOL_STARTED', 'tools', {'tool': 'test_add', 'arguments': {'number': 2}})
        window.refresh()
        window.tree.selection_set('row0')
        window.details()
        selected_id = window._selected_id
        gui.runtime.events.publish('TOOL_COMPLETED', 'tools', {'tool': 'test_add'})
        window.refresh()
        root.update()
        assert window.shown[window.tree.selection()[0]]['id'] == selected_id
        assert 'Number: 2' in window.body.get('1.0', 'end')
        window.query.set('completed')
        assert len(window.shown) == 1
        window.close()
        assert gui.activity_monitor.unsubscribers
        gui._stop.assert_not_called()
    finally:
        if window.winfo_exists():
            window.close()
        gui.activity_monitor.close()
        close_window(root)


def test_icons_are_distinct_readable_and_scaled_on_the_same_tk_interpreter(shared_tk):
    root = open_window(shared_tk, hidden=True)
    try:
        kinds = [icon_kind(label) for label in ('Activity', 'Global intelligence', 'Revenue', 'All features', 'Learn Arabic', 'Tasks')]
        assert len(set(kinds)) == 6
        assert icon_kind('Stop current task') == 'stop'
        assert icon_kind('Pause public feeds') == 'stop'
        images = [icon_image(root, kind, Palette.GOLD, 20) for kind in kinds]
        assert all(image.width() >= 20 for image in images)
        root._root()._hayat_text_scale = 1.5
        assert icon_image(root, 'globe', Palette.GOLD, 20).width() == 30
    finally:
        root._root()._hayat_text_scale = 1.0
        close_window(root)
