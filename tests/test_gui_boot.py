"""Startup stays responsive, uses real stage callbacks and hands off one root."""
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from app.gui_boot import BootScreen
from app.startup import BOOT_STAGES, readiness, readiness_lines


def pump(root, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Startup did not reach the expected state')


@pytest.fixture
def boot(shared_tk):
    top = tk.Toplevel(shared_tk)
    panel = BootScreen(top)
    try:
        yield panel
    finally:
        panel.detach()
        top.destroy()


def test_startup_build_runs_off_tk_and_does_not_advance_by_fake_time(boot):
    gate, entered = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    builder_threads = []
    runtime = SimpleNamespace()
    def build(progress):
        builder_threads.append(threading.get_ident())
        progress('stores')
        entered.set()
        gate.wait(5)
        progress('ready')
        return runtime
    boot.start(build)
    try:
        pump(boot.root, lambda: entered.is_set() and boot.stage == 2)
        assert builder_threads[0] != main_thread
        assert not boot.finished
        boot.publish('settings')  # A delayed earlier event cannot rewind it.
        boot.publish('unknown arbitrary label')
        pump(boot.root, lambda: boot.updates.empty())
        assert boot.stage == 2
        assert 'Optional engines' in boot.status.cget('text')
    finally:
        gate.set()
    pump(boot.root, lambda: boot.finished)
    assert boot.result is runtime and boot.stage == 7


def test_startup_error_is_masked_and_can_be_closed(boot):
    secret = 'sk-proj-' + 'X5qL9nR8zV' * 4
    def fail(progress):
        raise ValueError('Connection failed: ' + secret)
    boot.start(fail)
    pump(boot.root, lambda: boot.finished)
    assert secret not in boot.status.cget('text')
    assert '[hidden]' in boot.status.cget('text')
    assert boot.result is None
    boot.request_close()
    assert boot.cancelled.is_set()


def test_close_request_waits_for_builder_and_preserves_result_for_cleanup(boot):
    gate = threading.Event()
    runtime = SimpleNamespace()
    boot.start(lambda progress: (gate.wait(3), runtime)[1])
    boot.request_close()
    assert boot.cancelled.is_set() and not boot.finished
    assert boot.root.winfo_exists()
    gate.set()
    pump(boot.root, lambda: boot.finished)
    assert boot.result is runtime


@pytest.fixture(scope='module')
def runtime():
    from app.runtime import build_runtime
    phases = []
    runtime = build_runtime(use_model=False, start_background_services=False, on_progress=phases.append)
    runtime.test_phases = phases
    try:
        yield runtime
    finally:
        runtime.close()


def test_real_runtime_emits_ordered_stage_evidence_and_loads_late_registered_skills(runtime):
    assert runtime.test_phases == [key for key, label in BOOT_STAGES[1:-1]]
    assert {'career_scenario_lab', 'startup_and_mesh_check'} <= set(runtime.skills.skills)
    report = readiness(runtime)
    assert report['ready']
    model = next(row for row in report['checks'] if row['name'] == 'Reply model')
    assert model['state'] == 'deferred'
    assert 'not tested' not in model['detail'] or 'disabled' in model['detail']
    assert 'no claim all models are loaded' in '\n'.join(readiness_lines(report))


def test_readonly_startup_skill_executes_both_checks_without_enabling_services(runtime):
    before = runtime.learner.running
    results = runtime.skills.execute('startup_and_mesh_check', {})
    assert len(results) == 2 and all(result.success for result in results)
    assert results[1].output['passed']
    assert runtime.learner.running == before
    learning = results[1].output['independent']['nodes']['learning']['evidence']
    assert learning['trusted_skills'] >= 5 and not learning['skill_issues']


def test_missing_requested_model_is_attention_not_a_successful_disabled_launch(runtime, monkeypatch):
    monkeypatch.setattr(runtime, 'model_generation_requested', True)
    report = readiness(runtime)
    assert not report['ready']
    assert next(row for row in report['checks'] if row['name'] == 'Reply model')['state'] == 'attention'


def test_startup_request_uses_real_readiness_tool_and_never_shell(runtime):
    steps = runtime.agent.planner.create_plan('Check startup readiness')
    assert [step.tool for step in steps] == ['startup_readiness']
    reply = runtime.assistant.handle('Check startup readiness')
    assert reply.intent.classification_source == 'startup_readiness'
    assert reply.outcome is not None and [row['tool'] for row in reply.outcome.history] == ['startup_readiness']
    assert 'Startup readiness' in reply.display_response and 'Model generation was disabled' in reply.display_response
    assert 'Partial' not in reply.display_response


def test_career_skill_passes_typed_inputs_to_real_local_analysis_without_profile_edits(runtime):
    path = runtime.jobs.profile.path
    before = path.read_bytes() if path.exists() else None
    results = runtime.skills.execute('career_scenario_lab', {'proposed_skills': ['Docker'], 'weekly_hours': 2})
    assert len(results) == 1 and results[0].success
    assert results[0].output['scenario']['skills'] == ['Docker']
    assert (path.read_bytes() if path.exists() else None) == before


def test_declared_skill_issues_are_reported_and_inventory_cannot_mutate_registry(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.skills.loader import SkillLoader
    from app.skills.registry import SkillRegistry
    from app.tools.registry import ToolRegistry
    directory = tmp_path / 'skills'
    directory.mkdir()
    (directory / 'broken.yaml').write_text('bad: schema', encoding='utf-8')
    tools = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    skills = SkillRegistry(tools, SkillLoader(directory))
    skills.reload()
    inventory = skills.inventory()
    assert any(issue['name'] == 'broken' for issue in inventory['issues'])
    assert any('Missing registered tools' in issue['reason'] for issue in inventory['issues'])
    inventory['issues'].clear()
    assert skills.inventory()['issues']


def test_cli_gui_entry_uses_bootstrap_without_changing_mode_flags(monkeypatch):
    import main
    from app import gui_boot
    calls = []
    monkeypatch.setattr(main.sys, 'argv', ['main.py', '--gui', '--no-model', '--safe-mode'])
    monkeypatch.setattr(gui_boot, 'run_desktop', lambda **kw: calls.append(kw) or 0)
    assert main.main() == 0
    assert calls == [{'use_model': False, 'safe_mode': True}]


def test_actual_worker_runtime_and_single_root_handoff(shared_tk):
    from app.gui import HayatGUI, startup_window
    from app.runtime import build_runtime
    root = startup_window()
    panel = BootScreen(root)
    def build(progress):
        return build_runtime(use_model=False, start_background_services=False, on_progress=progress)
    panel.start(build)
    window = None
    try:
        pump(root, lambda: panel.finished, seconds=35)
        assert panel.error is None and panel.result is not None
        panel.detach()
        window = HayatGUI(panel.result)
        assert window.root is root
        assert window.runtime.assistant is not None
        if os.environ.get('HAYAT_SHOTS') == '1':
            from tests.test_gui_screenshots import _grab
            window.root.geometry('1180x760+0+0')
            window.root.update()
            _grab(window, '134-startup-real-handoff')
    finally:
        if window is not None:
            window.close()
        else:
            panel.detach()
            if panel.result is not None:
                panel.result.close()
            root.destroy()


def test_native_boot_capture(boot):
    if os.environ.get('HAYAT_SHOTS') != '1':
        pytest.skip('native captures are opt-in')
    from tests.test_gui_screenshots import _grab
    boot.publish('models')
    pump(boot.root, lambda: boot.stage == 4)
    _grab(SimpleNamespace(root=boot.root), '133-boot-stage-fixture')


def test_desktop_entry_owns_service_until_real_workspace_closes(shared_tk, monkeypatch):
    from app import gui, gui_boot, launcher, portable
    from app import runtime as runtime_module
    events = []
    class Service:
        def __init__(self, **kwargs):
            events.append('server constructed')
        def start(self):
            events.append('server started')
            return True
        def stop(self):
            events.append('server stopped')
    class Job:
        def close(self):
            events.append('job closed')
    monkeypatch.setattr(launcher, 'OwnOllama', Service)
    monkeypatch.setattr(launcher, 'Job', Job)
    monkeypatch.setattr(portable, 'active', lambda: True)
    real_builder = runtime_module.build_runtime
    def build(**kwargs):
        assert threading.current_thread() is not threading.main_thread()
        return real_builder(use_model=False, start_background_services=False, on_progress=kwargs['on_progress'])
    monkeypatch.setattr(runtime_module, 'build_runtime', build)
    original_run = gui.HayatGUI.run
    def finish(window):
        events.append('workspace shown')
        def close():
            # The test session owns another hidden Tk root. Unlike the real
            # single-window process, destroying this root alone does not end
            # Tk's process-wide mainloop while that fixture remains alive.
            window.root.quit()
            window.close()
        window.root.after(200, close)
        return original_run(window)
    monkeypatch.setattr(gui.HayatGUI, 'run', finish)
    assert gui_boot.run_desktop(use_model=True) == 0
    assert events == ['server constructed', 'server started', 'workspace shown', 'server stopped', 'job closed']


@pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='actual local model startup is opt-in')
def test_actual_local_model_desktop_boot_and_readiness(shared_tk, monkeypatch):
    import json
    from pathlib import Path
    from app import gui, gui_boot
    observed = []
    original_run = gui.HayatGUI.run
    def finish(window):
        report = window.runtime.startup_report
        assert window.runtime.model is not None
        observed.append({'model': window.runtime.model.model, 'readiness': report,
                         'scope': 'Actual GUI/runtime assembly with a local model client; no generation or sensor capture in this check.'})
        def close():
            window.root.quit()
            window.close()
        window.root.after(300, close)
        return original_run(window)
    monkeypatch.setattr(gui.HayatGUI, 'run', finish)
    assert gui_boot.run_desktop(use_model=True) == 0
    assert observed
    target = Path(__file__).resolve().parents[1] / '.test-runs' / 'startup-live-local.json'
    target.write_text(json.dumps(observed[0], indent=2), encoding='utf-8')
