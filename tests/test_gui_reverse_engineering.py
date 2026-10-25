import json
import time
from types import SimpleNamespace

import pytest
from tkinter import ttk

from app.gui_reverse_engineering import ReverseEngineeringStudio
from app.gui_theme import Palette, style_controls
from app.tools.registry import ToolResult
from tests.conftest import open_window


TOOLS = [
    {'name': 'analyze_javascript_application', 'writes': False, 'summary': 'Static application graph',
     'description': 'Inspects source without running the target.', 'input_schema': {'type': 'object', 'properties': {
         'input_path': {'type': 'string', 'minLength': 1}, 'format': {'enum': ['directory', 'asar']}}, 'required': ['input_path']}},
    {'name': 'open_binary', 'writes': True, 'summary': 'Open binary', 'input_schema': {
        'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}]


class GUI(Palette):
    def __init__(self, root):
        self.root, self.calls = root, []
        def execute(name, arguments):
            self.calls.append((name, arguments))
            value = {'tools': TOOLS, 'version': 'test'} if name == 'reverse_engineer_tools' else (
                {'kind': 'reverse_engineer', 'how_to_confirm': 'Confirm action'} if name == 'reverse_engineer_act'
                else {'report': '\n'.join(f'Finding {i}' for i in range(200))})
            return ToolResult(name, True, value)
        self.runtime = SimpleNamespace(registry=SimpleNamespace(execute=execute))


def pump(panel, seconds=3):
    deadline = time.monotonic() + seconds
    while panel._busy and time.monotonic() < deadline:
        panel.update()
        time.sleep(.01)
    panel.update()
    assert not panel._busy


def test_link_button_routes_web_and_native_targets_and_restores_link_history(studio):
    studio.target.set('https://example.com/app')
    studio.link_button.invoke()
    pump(studio)
    assert studio.gui.calls[-1] == ('reverse_engineer_link', {'url': 'https://example.com/app'})
    studio.target.set('different')
    studio.restore_inputs()
    assert studio.target.get() == 'https://example.com/app'
    assert 'nothing has been submitted' in studio.status.get()
    studio.target.set('https://example.com/owned-app.apk')
    studio.link_button.invoke()
    pump(studio)
    assert studio.gui.calls[-1] == ('reverse_engineer_act', {'tool': 'open_binary',
                                                        'arguments': {'path': 'https://example.com/owned-app.apk'}})
    assert 'Confirmations' in studio.status.get()


def test_real_link_analysis_from_the_gui(studio, tmp_path, monkeypatch):
    from app import reverse_engineer
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from tests.test_rea_links import Fetcher
    from tests.test_gui_screenshots import _grab
    # The fixture reports the engine present for its fake registry; this test needs the real one.
    if not (reverse_engineer.NODE.is_file() and reverse_engineer.REA_SCRIPT.is_file()):
        pytest.skip('REA is not installed')
    monkeypatch.setattr(reverse_engineer, 'REA_HOME', tmp_path / 'rea-home')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    fetcher = Fetcher({'https://example.com/owned-app':
        (b'<html><title>Owned app</title><script>function ownedFeature(){return 42}</script></html>', 'text/html')})
    engine = reverse_engineer.register_reverse_engineer_tools(registry, fetcher=fetcher)
    try:
        studio.registry = registry
        studio.target.set('https://example.com/owned-app')
        studio.inspect_link()
        pump(studio, seconds=60)  # real Node/REA cold startup under full-suite load
        text = studio.result.get('1.0', 'end')
        assert 'JavaScriptApplicationGraph' in text and 'ownedFeature' in text
        assert 'code_executed' in text and 'false' in text
        assert 'Scripts collected: 1' in studio.summary.get('1.0', 'end')
        studio.geometry('800x650+20+20')
        studio.output_tabs.select(0)
        studio.update()
        _grab(studio.gui, '73-rea-app-link', studio)
    finally:
        engine.close()


@pytest.fixture
def studio(shared_tk, monkeypatch):
    # The registry here is a fake, so the window must not depend on the optional REA engine being
    # installed in runtime/rea: a fresh clone has no runtime folder.
    monkeypatch.setattr('app.reverse_engineer.available', lambda: True)
    root = open_window(shared_tk, '1100x800-4000-4000')
    gui = GUI(root)
    style = ttk.Style(root)
    theme = style.theme_use()
    style.theme_use('clam')
    style_controls(root, gui)
    panel = ReverseEngineeringStudio(gui)
    pump(panel)
    try:
        yield panel
    finally:
        panel.close()
        style.theme_use(theme)
        root.destroy()


def test_not_installed_shows_setup_instructions_without_dispatching(studio, monkeypatch):
    monkeypatch.setattr('app.reverse_engineer.available', lambda: False)
    before = len(studio.gui.calls)
    studio.load_tools()
    assert len(studio.gui.calls) == before, 'must not call REA when it is not installed'
    message = studio.status.get()
    assert 'not installed' in message.lower() and 'setup_rea.py' in message


def test_a_failed_operation_load_points_to_setup(studio):
    studio._busy = True
    studio._queue.put(('reverse_engineer_tools', False,
                       'ReaError: REA is not installed in runtime/rea; run scripts/setup_rea.py first.', None))
    studio._poll()
    message = studio.status.get()
    assert 'Could not load REA operations' in message and 'setup_rea.py' in message


def test_engine_check_keeps_analysis_history_inputs_and_reading_position(studio, tmp_path):
    studio.target.set(str(tmp_path))
    studio.fill_target()
    studio.run_operation()
    pump(studio)
    result = studio.result.get('1.0', 'end-1c')
    draft = studio.arguments.get('1.0', 'end-1c')
    records = studio.session.results
    studio.result.tag_add('sel', '10.0', '10.6')
    selection = studio.result.tag_ranges('sel')
    studio.registry.execute = lambda name, arguments: ToolResult(name, True, {'engine_check': {
        'ready': True, 'components': [], 'checks': [{'name': 'ghidra:version', 'ok': True, 'detail': '12.1.4'}]}})
    studio.check_engines()
    pump(studio)
    assert studio.notebook.index(studio.notebook.select()) == 2
    assert 'Ghidra is ready' in studio.engine_output.get('1.0', 'end')
    assert studio.session.results == records
    assert studio.result.get('1.0', 'end-1c') == result and studio.result.tag_ranges('sel') == selection
    assert studio.arguments.get('1.0', 'end-1c') == draft


def test_missing_engine_after_refresh_disables_stale_operations(studio, monkeypatch):
    assert studio._tools
    monkeypatch.setattr('app.reverse_engineer.available', lambda: False)
    studio.load_tools()
    assert not studio.operations.get_children() and not studio._tools
    assert 'disabled' in studio.run_button.state()


@pytest.mark.skipif(__import__('os').environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_real_engine_status_and_capture(studio):
    from app.reverse_engineer import ReverseEngineer
    from tests.test_gui_screenshots import _grab, _pump
    engine = ReverseEngineer()
    studio.registry.execute = lambda name, arguments: ToolResult(name, True, engine.status(**arguments))
    studio.check_engines()
    deadline = time.monotonic() + 50
    while studio._busy and time.monotonic() < deadline:
        studio.update()
        time.sleep(.01)
    assert not studio._busy and 'Ghidra is ready' in studio.engine_output.get('1.0', 'end')
    studio.attributes('-topmost', True)
    studio.geometry('1080x780+0+0')
    _pump(studio.gui, .5)
    _grab(studio.gui, '57-rea-engine-status', studio)


def test_static_target_runs_through_registry_and_preserves_scroll_during_idle_polling(studio, tmp_path):
    studio.target.set(str(tmp_path))
    studio.fill_target()
    studio.run_operation()
    pump(studio)
    name, arguments = studio.gui.calls[-1]
    assert name == 'reverse_engineer_read'
    assert arguments['arguments'] == {'input_path': str(tmp_path), 'format': 'directory'}
    studio.result.yview_moveto(.5)
    studio.result.tag_add('sel', '100.0', '100.7')
    studio.update()
    view, selection = studio.result.yview(), studio.result.tag_ranges('sel')
    studio.after(150, lambda: None)
    time.sleep(.2)
    studio.update()
    assert studio.result.yview() == pytest.approx(view) and studio.result.tag_ranges('sel') == selection


def test_schema_validation_prevents_dispatch_and_writes_only_prepare(studio):
    before = len(studio.gui.calls)
    studio.arguments.delete('1.0', 'end')
    studio.arguments.insert('1.0', '{}')
    studio.run_operation()
    assert 'Check inputs' in studio.status.get() and len(studio.gui.calls) == before
    studio.operations.selection_set('open_binary')
    studio.select_operation()
    studio.target.set('D:/example.exe')
    studio.fill_target()
    studio.run_operation()
    pump(studio)
    assert studio.gui.calls[-1][0] == 'reverse_engineer_act'
    assert 'prepared' in studio.status.get()


def test_filter_preserves_argument_draft_and_small_layout_keeps_result_visible(studio):
    studio.arguments.delete('1.0', 'end')
    studio.arguments.insert('1.0', json.dumps({'input_path': 'custom target'}))
    studio.filter.set('javascript')
    studio.update()
    assert 'custom target' in studio.arguments.get('1.0', 'end')
    studio.geometry('760x600')
    studio.update()
    assert not studio.inputs_visible.get()
    assert studio.result.winfo_viewable()
    assert studio.result.winfo_height() > 60
    assert studio.run_button.winfo_rooty() + studio.run_button.winfo_height() < studio.winfo_rooty() + studio.winfo_height()


def test_catalog_refresh_updates_guided_schema_without_erasing_json_draft(studio):
    studio.input_tabs.select(1)
    studio.arguments.delete('1.0', 'end')
    studio.arguments.insert('1.0', '{"input_path":"preserved"}')
    rows = json.loads(json.dumps(TOOLS))
    rows[0]['input_schema']['properties']['limit'] = {'type': 'integer', 'description': 'New catalog field'}
    studio.registry.execute = lambda name, _args: ToolResult(name, True, {'tools': rows, 'version': 'test'})
    studio.load_tools()
    pump(studio)
    assert studio.guided.fields['input_path'][0].get() == 'preserved'
    assert 'limit' in studio.guided.fields and 'New catalog field' in studio.reference.get('1.0', 'end')


def test_close_ignores_late_worker_results_and_cancels_polling(studio):
    studio.close()
    studio._queue.put(('reverse_engineer_read', True, {'report': 'late'}))
    studio._poll()
    assert studio._closed and studio._poll_id is None


def test_operation_switch_restores_json_draft_instead_of_overwriting_it_with_target(studio):
    studio.target.set('D:/different-target')
    studio.input_tabs.select(1)
    studio.arguments.delete('1.0', 'end')
    draft = '{"input_path": "my edited target", "format": "directory"}'
    studio.arguments.insert('1.0', draft)
    studio.operations.selection_set('open_binary')
    studio.select_operation()
    studio.operations.selection_set('analyze_javascript_application')
    studio.select_operation()
    assert studio.arguments.get('1.0', 'end-1c') == draft
    assert studio.guided.fields['input_path'][0].get() == 'my edited target'
    assert callable(studio.state), 'status text must not shadow Tk window state()'


def test_guided_numbers_booleans_and_nested_json_roundtrip_and_invalid_number_blocks_run(studio):
    studio._tools['typed'] = {'name': 'typed', 'writes': False, 'input_schema': {'type': 'object', 'properties': {
        'count': {'type': 'integer'}, 'ratio': {'type': 'number'}, 'enabled': {'type': 'boolean'},
        'details': {'type': 'object'}}, 'additionalProperties': False}}
    studio.filter_tools()
    studio.operations.selection_set('typed')
    studio.select_operation()
    studio.arguments.delete('1.0', 'end')
    studio.arguments.insert('1.0', '{"details": {"path": "preserved"}}')
    studio.guided.fields['count'][0].set('0')
    studio.guided.fields['enabled'][0].set('false')
    values = json.loads(studio.arguments.get('1.0', 'end'))
    assert values == {'details': {'path': 'preserved'}, 'count': 0, 'enabled': False}
    studio.guided.fields['ratio'][0].set('NaN')
    before = len(studio.gui.calls)
    studio.run_operation()
    assert len(studio.gui.calls) == before and 'valid number' in studio.status.get()
    studio.operations.selection_set('open_binary')
    studio.select_operation()
    studio.operations.selection_set('typed')
    studio.select_operation()
    assert studio.guided.fields['ratio'][0].get() == 'NaN'
    studio.guided.fields['ratio'][0].set('0.25')
    studio.run_operation()
    pump(studio)
    assert studio.gui.calls[-1][1]['arguments']['ratio'] == .25


def test_history_restore_never_submits_an_operation_and_find_wraps(studio, tmp_path):
    studio.target.set(str(tmp_path))
    studio.fill_target()
    studio.run_operation()
    pump(studio)
    before = len(studio.gui.calls)
    studio.restore_inputs()
    assert len(studio.gui.calls) == before and 'nothing has been submitted' in studio.status.get()
    studio.find_query.set('Finding 199')
    studio.find_next()
    assert studio.result.get(*studio.result.tag_ranges('rea_find')) == 'Finding 199'
    studio.find_next()
    assert studio.result.get(*studio.result.tag_ranges('rea_find')) == 'Finding 199'


def test_reading_old_history_during_a_run_is_not_replaced_on_completion(studio, tmp_path):
    import threading
    release, entered = threading.Event(), threading.Event()
    number = 0
    def execute(name, arguments):
        nonlocal number
        number += 1
        if number == 3:
            entered.set()
            release.wait(3)
        return ToolResult(name, True, {'report': f'Report {number}\n' + '\n'.join(f'Evidence {i}' for i in range(100))})
    studio.registry.execute = execute
    studio.target.set(str(tmp_path))
    studio.fill_target()
    for _ in range(2):
        studio.run_operation()
        pump(studio)
    studio.run_operation()
    try:
        assert entered.wait(1)
        studio.history.current(1)
        studio.show_history()
        studio.result.tag_add('sel', '5.0', '5.4')
        ranges = studio.result.tag_ranges('sel')
        release.set()
        pump(studio)
        assert studio.result.get('1.0', '1.end') == 'Report 1'
        assert studio.result.tag_ranges('sel') == ranges
        assert studio.session.results[0]['report'].startswith('Report 3')
    finally:
        release.set()


def test_completed_work_survives_closing_and_reopening_the_workbench(studio, tmp_path):
    import threading
    entered, release = threading.Event(), threading.Event()
    execute = studio.registry.execute
    def slow(name, arguments):
        if name == 'reverse_engineer_read':
            entered.set()
            release.wait(3)
        return execute(name, arguments)
    studio.registry.execute = slow
    studio.target.set(str(tmp_path))
    studio.fill_target()
    studio.run_operation()
    assert entered.wait(1)
    studio.close()
    release.set()
    deadline = time.monotonic() + 3
    while not studio.session.results and time.monotonic() < deadline:
        time.sleep(.01)
    assert studio.session.results
    reopened = ReverseEngineeringStudio(studio.gui)
    try:
        pump(reopened)
        assert 'Finding 199' in reopened.result.get('1.0', 'end')
        assert reopened.session is studio.session
    finally:
        reopened.close()


@pytest.mark.skipif(__import__('os').environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_capture_workbench_at_normal_and_compact_sizes(studio, tmp_path):
    from tests.test_gui_screenshots import _grab, _pump
    target = tmp_path / 'sample-app'
    target.mkdir()
    studio.target.set(str(target))
    studio.fill_target()
    studio._replace(studio.result, 'Static inspection fixture — target was not executed\n\n'
                    'Application graph\n  index.js → parser.js\n  index.js → node:fs\n\n'
                    'Evidence: module imports recovered from source.\n'
                    'Limitations: dynamic imports and native dependencies need further analysis.\n\n'
                    + '\n'.join(f'Source observation {i}' for i in range(40)))
    studio.attributes('-topmost', True)
    for size, name in [('1080x780', '49-rea-workbench'), ('760x600', '50-rea-workbench-compact')]:
        studio.geometry(size + '+0+0')
        _pump(studio.gui, .5)
        _grab(studio.gui, name, studio)


def test_switching_operations_releases_tk_variables_for_old_form_fields(studio):
    before = len(studio.tk.call('info', 'vars'))
    for _ in range(20):
        for name in ('open_binary', 'analyze_javascript_application'):
            studio.operations.selection_set(name)
            studio.select_operation()
    studio.update()
    assert len(studio.tk.call('info', 'vars')) <= before + 5


@pytest.mark.skipif(__import__('os').environ.get('HAYAT_SHOTS') != '1', reason='screenshots on request')
def test_real_guided_gui_analysis_and_summary(studio, tmp_path):
    from app.reverse_engineer import ReaSession, ReverseEngineer, available, register_reverse_engineer_tools
    from app.safety.permissions import PermissionManager
    from app.safety.sandbox import ProcessSandbox
    from app.tools.registry import ToolRegistry
    from tests.test_gui_screenshots import _grab, _pump
    if not available():
        pytest.skip('REA is not installed')
    target = tmp_path / 'example-app'
    target.mkdir()
    (target / 'package.json').write_text('{"name":"example-app","version":"1.0.0","main":"index.js"}')
    (target / 'index.js').write_text("const {format}=require('./helper'); module.exports={format};")
    (target / 'helper.js').write_text("exports.format=(value)=>String(value).trim();")
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    engine = register_reverse_engineer_tools(registry, ReverseEngineer(lambda: ReaSession(env=ProcessSandbox().environment())))
    try:
        studio.registry = registry
        studio.load_tools()
        pump(studio)
        studio.target.set(str(target))
        studio.fill_target()
        studio.run_operation()
        pump(studio)
        assert 'JavaScriptApplicationGraph' in studio.result.get('1.0', 'end')
        assert 'Completed' in studio.summary.get('1.0', 'end')
        studio.attributes('-topmost', True)
        studio.geometry('1080x780+0+0')
        studio.output_tabs.select(0)
        _pump(studio.gui, .5)
        _grab(studio.gui, '51-rea-guided-summary', studio)
    finally:
        engine.close()
