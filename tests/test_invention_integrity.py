"""Numeric invention experiments preserve contracts, ownership and failed trials."""
from __future__ import annotations

import math
import threading

import pytest

from app import invention_sim as sim
from app.models.streaming import GenerationCancelled
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate


CASES = [{'inputs': {'x': value}, 'expected': value * 2} for value in (1, 2, 3, 4)]


@pytest.mark.parametrize('operation', ['try', 'work_out', 'simulate'])
@pytest.mark.parametrize('field,value', [('tests', [{'inputs': {'x': 2}, 'expected': 999}]), ('tolerance', '0.5')])
def test_every_numeric_entry_point_rejects_altered_acceptance_cases(tmp_path, operation, field, value):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    data[field] = value
    workbench._save(data)
    before = workbench.get(data['id'])
    calls = {'try': lambda: workbench.try_formula(data['id'], 'x * 2'),
             'work_out': lambda: workbench.work_out(data['id']),
             'simulate': lambda: workbench.simulate(data['id'], 'x * 2')}
    with pytest.raises(ValueError, match='Acceptance contract changed'):
        calls[operation]()
    assert workbench.get(data['id']) == before
    assert not workbench._run_lock.locked()


def test_working_out_obeys_a_live_cross_workbench_lease(tmp_path):
    first = bench(tmp_path, None)
    data = first.create('Double the input', tests=CASES)
    claimed = first._claim(data['id'])
    other = bench(tmp_path, None)
    with pytest.raises(RuntimeError, match='already running'):
        other.work_out(data['id'])
    assert other.get(data['id']) == claimed
    assert not other._run_lock.locked()


def test_stopping_work_out_interrupts_the_numeric_search_without_saving_an_unfinished_round(tmp_path, monkeypatch):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    started, released = threading.Event(), threading.Event()
    results = []

    def fitting(_data, cancel=None):
        started.set()
        assert released.wait(5)
        assert cancel.is_set()
        raise GenerationCancelled('Stopped numeric search')

    monkeypatch.setattr(workbench, '_fit_round', fitting)
    worker = threading.Thread(target=lambda: results.append(workbench.work_out(data['id'])))
    worker.start()
    assert started.wait(5)
    assert workbench.stop(data['id'])['stop_requested']
    released.set()
    worker.join(5)
    assert not worker.is_alive()
    assert results[0]['state'] == 'STOPPED' and results[0]['rounds'] == []
    assert workbench.get(data['id'])['state'] == 'STOPPED'
    assert not workbench.status()['active'] and not workbench._run_lock.locked()


def test_run_commits_cannot_overwrite_a_replaced_lease(tmp_path):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    claimed = workbench._claim(data['id'])
    owner = claimed['run_owner'].copy()
    newer = workbench.get(data['id'])
    newer.update(state='DRAFT', stop_reason='Newer journal retained')
    newer.pop('run_owner')
    workbench._save(newer)
    claimed['rounds'] = [{'uncommitted': True}]
    with pytest.raises(RuntimeError, match='no longer owns'):
        workbench._save_owned(claimed, owner)
    assert workbench.get(data['id']) == newer


def test_run_commit_rejects_contract_drift_without_replacing_newer_cases(tmp_path):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    claimed = workbench._claim(data['id'])
    newer = workbench.get(data['id'])
    newer['tests'][0]['expected'] = 999
    workbench._save(newer)
    with pytest.raises(ValueError, match='changed during the run'):
        workbench._save_owned(claimed, claimed['run_owner'])
    assert workbench.get(data['id']) == newer


def test_simulation_does_not_cache_output_for_an_obsolete_best_formula(tmp_path, monkeypatch):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    data['best'] = {'candidate': {'formula': 'x * 2'}}
    workbench._save(data)
    original = sim.simulate

    def advancing_simulation(*args, **kwargs):
        newer = workbench.get(data['id'])
        newer['best'] = {'candidate': {'formula': 'x * 3'}}
        newer['simulation'] = {'formula': 'x * 3', 'sentinel': 'newer result'}
        workbench._save(newer)
        return original(*args, **kwargs)

    monkeypatch.setattr(sim, 'simulate', advancing_simulation)
    result = workbench.simulate(data['id'])
    assert result['formula'] == 'x * 2'
    retained = workbench.get(data['id'])
    assert retained['simulation'] == {'formula': 'x * 3', 'sentinel': 'newer result'}


def test_simulation_does_not_write_to_a_running_project(tmp_path):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    data['best'] = {'candidate': {'formula': 'x * 2'}}
    workbench._save(data)
    claimed = workbench._claim(data['id'])
    result = workbench.simulate(data['id'])
    assert result['formula'] == 'x * 2'
    assert workbench.get(data['id']) == claimed


def test_baseline_fitting_reserves_its_journal_slot_before_model_calls(tmp_path):
    model = ScriptedModel(candidate(), accepted())
    workbench = bench(tmp_path, model)
    data = workbench.create('Double the input', tests=CASES)
    data['rounds'] = [{'method': 'model'}] * 31
    workbench._save(data)
    with pytest.raises(ValueError, match='journal limit'):
        workbench.run(data['id'], cycles=1, research=False)
    assert len(workbench.get(data['id'])['rounds']) == 31 and model.calls == []
    assert not workbench._run_lock.locked()


@pytest.mark.parametrize('cycles', [True, False, 1.5, '3', 0, 9])
def test_cycle_budget_requires_a_bounded_integer(tmp_path, cycles):
    workbench = bench(tmp_path, None)
    data = workbench.create('Double the input', tests=CASES)
    with pytest.raises(ValueError, match='integer from 1 to 8'):
        workbench.run(data['id'], cycles=cycles)
    assert workbench.get(data['id'])['state'] == 'DRAFT' and not workbench._run_lock.locked()


def test_domain_failures_count_against_robustness_instead_of_disappearing():
    data = [{'inputs': {'x': 0}, 'expected': 0}]
    result = sim.simulate('sqrt(x)', data, spread=0.1, samples=200, seed=7)
    monte = result['monte_carlo']
    assert monte['samples'] == 200 and 0 < monte['failed_samples'] < 200
    assert monte['completed_samples'] + monte['failed_samples'] == monte['samples']
    assert monte['evaluation_coverage'] < 1
    assert monte['share_within_band'] <= monte['evaluation_coverage']
    assert monte['cases'][0]['failed'] == monte['failed_samples']
    assert any('count against' in warning for warning in result['warnings'])


def test_all_failed_trials_still_have_a_sample_count_and_zero_success_rate():
    result = sim.simulate('sqrt(-1)', CASES, samples=23)
    monte = result['monte_carlo']
    assert monte['samples'] == monte['failed_samples'] == 23
    assert monte['completed_samples'] == 0 and monte['share_within_band'] == 0
    assert sum(row['attempted'] for row in monte['cases']) == 23
    assert all(row['ran'] == 0 and row['share_within_band'] == 0 for row in monte['cases'])


def test_zero_input_perturbation_uses_an_explicit_observed_scale():
    data = [{'inputs': {'x': value}, 'expected': value} for value in (0, 10)]
    result = sim.simulate('x', data, samples=40)
    monte = result['monte_carlo']
    zero = monte['cases'][0]
    assert monte['zero_input_scales']['x'] == 10
    assert zero['p5'] < 0 < zero['p95']
    assert 'zero inputs' in monte['perturbation_basis']


@pytest.mark.parametrize('kwargs', [{'samples': True}, {'samples': 19}, {'samples': 5001}, {'samples': 20.5},
                                    {'seed': True}, {'seed': -1}, {'seed': 2 ** 32}, {'spread': True},
                                    {'spread': float('nan')}, {'spread': float('inf')}])
def test_simulation_work_budget_is_bounded(kwargs):
    with pytest.raises(ValueError):
        sim.simulate('x * 2', CASES, **kwargs)


def test_repeated_input_points_do_not_leak_into_cross_validation(monkeypatch):
    data = [{'inputs': {'x': value}, 'expected': float(value) * 2} for value in (1, '1.0', 2, 3, 4)]
    original = sim.fit
    training_sets = []

    def recording_fit(shape, cases, *args, **kwargs):
        training_sets.append({float(case['inputs']['x']) for case in cases})
        return original(shape, cases, *args, **kwargs)

    monkeypatch.setattr(sim, 'fit', recording_fit)
    result = sim.leave_one_out('c1*x', data)
    assert result['independent_input_groups'] == 4 and result['duplicate_cases'] == 1
    assert result['predicted_within_tolerance'] == 5 and result['failed_predictions'] == 0
    assert training_sets == [{2, 3, 4}, {1, 3, 4}, {1, 2, 4}, {1, 2, 3}]
    assert 'not an independent external test' in result['basis']


def test_many_repeats_do_not_create_enough_independent_cases_to_validate():
    data = [{'inputs': {'x': value}, 'expected': value * 2} for value in (1, 1, 1, 2, 2, 2)]
    assert sim.leave_one_out('c1*x', data) is None


def test_periodic_fit_escapes_a_perfect_high_frequency_alias():
    data = [{'inputs': {'x': x}, 'expected': math.sin(x) / x} for x in (0.5, 1, 1.5, 2, 3, 4, 5, 6)]
    # At multiples of 0.5, -sin((4*pi-1)*x) and sin(x) agree exactly.
    result = sim.fit('c1*sin(c2*x)/x', data, tolerance=0.001, starts=[[-1, 4 * math.pi - 1]])
    assert result['formula'] == 'sin(x) / x'


def test_cancellation_reaches_scipy_residual_evaluation():
    class CancelAfterChecks:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls >= 6

    cancel = CancelAfterChecks()
    with pytest.raises(GenerationCancelled):
        sim.fit('c1*exp(c2*x)', CASES, cancel=cancel)
    assert cancel.calls == 6


def test_reported_trend_uses_the_stated_range_not_an_extrapolated_turning_point():
    data = [{'inputs': {'x': x, 'y': 6}, 'expected': math.hypot(x, 6)} for x in (1, 9)]
    result = sim.simulate('hypot(x,y)', data)
    sweep = next(row for row in result['sweeps'] if row['input'] == 'x')
    assert sweep['range'] == [1.0, 9.0] and sweep['trend'] == 'rises'
    assert sweep['extended_trend'] == 'falls then rises'
    assert 'not a proof' in sweep['trend_basis']
    assert 'from 1 to 9' in sim.describe_simulation(result)[0]
    assert 'the result rises' in sim.describe_simulation(result)[0]


def test_a_sweep_with_an_internal_domain_gap_does_not_claim_a_continuous_trend():
    result = sim.simulate('sqrt(x-3)', CASES)
    assert result['sweeps'][0]['trend'] == 'has domain gaps'
