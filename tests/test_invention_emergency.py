import threading
from types import SimpleNamespace

import pytest

from app.emergency import stop_runtime
from app.safety.permissions import PermissionManager
from app.tools.invention import register_invention_tools
from app.tools.registry import ToolRegistry
from tests.test_executive_invention import ScriptedModel, bench, candidate


def test_global_stop_cancels_active_invention_even_if_resumed_before_model_returns(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class BlockingModel(ScriptedModel):
        def generate(self, messages, **kwargs):
            entered.set()
            assert release.wait(10)
            return super().generate(messages, **kwargs)

    model = BlockingModel(candidate(''))
    workbench = bench(tmp_path, model)
    project = workbench.create('Propose a prototype')
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench,
                              agent=SimpleNamespace(cancel_current=lambda: None),
                              authority=SimpleNamespace(approvals_lifecycle=None),
                              session=SimpleNamespace(active_turn_id=None))
    results = []
    thread = threading.Thread(target=lambda: results.append(registry.execute(
        'invention_run', {'project_id': project['id'], 'cycles': 2, 'research': False})))
    thread.start()
    try:
        assert entered.wait(5)
        assert stop_runtime(runtime) == {'halted': True, 'errors': [],
                                        'detail': 'Tools blocked. Running calls may finish; completed actions are not undone. Resume does not restart background services.'}
        registry.interlock.resume()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and results[0].success
    result = workbench.get(project['id'])
    assert result['state'] == 'STOPPED' and result['rounds'] == []
    assert len(model.calls) == 1, 'Stopping must prevent the next critic or proposer call.'


def test_cancellation_failure_is_reported_without_unlatching_other_tools():
    registry = ToolRegistry()
    def broken_close():
        raise OSError('fixture failure')
    runtime = SimpleNamespace(registry=registry, invention=SimpleNamespace(close=broken_close),
                              agent=SimpleNamespace(cancel_current=lambda: None),
                              authority=SimpleNamespace(approvals_lifecycle=None), session=SimpleNamespace(active_turn_id=None))
    result = stop_runtime(runtime)
    assert result['halted'] and result['errors'] == ['invention: OSError']
    assert registry.interlock.halted


@pytest.mark.parametrize('operation', ['try', 'simulate'])
@pytest.mark.parametrize('stop_kind', ['project', 'global'])
def test_direct_numeric_operations_stop_while_fitting_and_never_cache_cancelled_output(tmp_path, monkeypatch, operation, stop_kind):
    from app import invention_sim
    workbench = bench(tmp_path, None)
    project = workbench.create('Double the input', tests=[{'inputs': {'x': 2}, 'expected': 4},
                                                         {'inputs': {'x': 3}, 'expected': 6}])
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench,
                              agent=SimpleNamespace(cancel_current=lambda: None),
                              authority=SimpleNamespace(approvals_lifecycle=None), session=SimpleNamespace(active_turn_id=None))
    entered, release = threading.Event(), threading.Event()
    original_fit = invention_sim.fit

    def delayed_fit(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_fit(*args, **kwargs)

    monkeypatch.setattr(invention_sim, 'fit', delayed_fit)
    results = []
    name = 'invention_try' if operation == 'try' else 'invention_simulate'
    worker = threading.Thread(target=lambda: results.append(registry.execute(
        name, {'project_id': project['id'], 'formula': 'c1*x'})))
    worker.start()
    try:
        assert entered.wait(5)
        assert workbench.status()['active'] == [project['id']]
        if stop_kind == 'project':
            assert workbench.stop(project['id'])['stop_requested']
        else:
            assert stop_runtime(runtime)['halted']
            registry.interlock.resume()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and not results[0].success
    assert 'cancel' in results[0].error.casefold()
    assert workbench.get(project['id']) == project
    assert not workbench.status()['active']


def test_a_trial_does_not_replace_the_model_runs_cancellation_event(tmp_path):
    from app.models.streaming import GenerationCancelled
    model_entered, model_release = threading.Event(), threading.Event()

    class BlockingModel(ScriptedModel):
        def generate(self, messages, **kwargs):
            model_entered.set()
            assert model_release.wait(10)
            return super().generate(messages, **kwargs)

    workbench = bench(tmp_path, BlockingModel(candidate('')))
    project = workbench.create('A design')
    results = []
    worker = threading.Thread(target=lambda: results.append(workbench.run(project['id'], research=False)))
    worker.start()
    try:
        assert model_entered.wait(5)
        with pytest.raises(GenerationCancelled):
            with workbench._numeric_scope(project['id']) as trial_cancel:
                assert workbench.stop(project['id'])['stop_requested']
                assert trial_cancel.is_set()
        assert workbench.status()['active'] == [project['id']]
    finally:
        model_release.set()
        worker.join(10)
    assert not worker.is_alive() and results[0]['state'] == 'STOPPED'
    assert not workbench.status()['active']


def test_stop_during_model_selection_is_stopped_rather_than_blocked(tmp_path):
    entered, release = threading.Event(), threading.Event()
    workbench = bench(tmp_path, None)
    def selecting_model():
        entered.set()
        assert release.wait(10)
        return None
    workbench.model_supplier = selecting_model
    project = workbench.create('A proposal')
    results = []
    worker = threading.Thread(target=lambda: results.append(workbench.run(project['id'], research=False)))
    worker.start()
    try:
        assert entered.wait(5)
        workbench.close()
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and results[0]['state'] == 'STOPPED'
