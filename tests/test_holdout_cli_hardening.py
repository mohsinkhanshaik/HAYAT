import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scripts import evaluate_holdout


@pytest.fixture
def local_eval(monkeypatch):
    endpoint = 'http://127.0.0.1:11435'
    state = {'responses': ['4', '7'], 'digests': ['before', 'before']}
    cases = [{'id': 'math-a', 'family': 'arithmetic', 'prompt': 'two plus two', 'grader': {'kind': 'number', 'expected': 4}},
             {'id': 'math-b', 'family': 'arithmetic', 'prompt': 'three plus four', 'grader': {'kind': 'number', 'expected': 7}}]
    # Use the actual existing fixed-case schema, replacing only its prompt and oracle.
    originals = evaluate_holdout.build_cases()[:2]
    for case, original in zip(cases, originals):
        original.update(id=case['id'], family=case['family'], prompt=case['prompt'])
        original['grader'] = {**original['grader'], 'answer': case['grader']['expected']}
    monkeypatch.setattr(evaluate_holdout, 'build_cases', lambda: originals)
    monkeypatch.setattr('app.portable.base_url', lambda: endpoint)
    monkeypatch.setattr('app.launcher.folder_runtime', lambda *_: nullcontext({}))
    def request(*_args, **_kwargs):
        digest = state['digests'].pop(0)
        return {'models': [{'name': 'test:latest', 'digest': digest}]}
    monkeypatch.setattr('scripts.audit_local_models.request', request)
    class Provider:
        def __init__(self, *_args, **_kwargs):
            pass
        def generate(self, *_args, **_kwargs):
            value = state['responses'].pop(0)
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(content=value)
    monkeypatch.setattr('app.models.local.OllamaProvider', Provider)
    return state


def test_complete_measurements_are_separate_from_a_requested_quality_gate(tmp_path, local_eval):
    local_eval['responses'] = ['4', 'wrong']
    result = evaluate_holdout.main(['--model', 'test:latest', '--output', str(tmp_path), '--minimum-accuracy', '.9'])
    report = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert result == 2 and report['evaluation_complete'] and not report['score_gate_passed']
    assert report['model_digest'] == 'before' and report['digest_unchanged']


def test_one_success_does_not_hide_an_incomplete_evaluation(tmp_path, local_eval):
    local_eval['responses'] = ['4', TimeoutError('fixture timeout')]
    assert evaluate_holdout.main(['--model', 'test:latest', '--output', str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert not report['evaluation_complete'] and report['summary']['errors'] == 1


def test_changed_model_digest_invalidates_the_measured_run(tmp_path, local_eval):
    local_eval['digests'] = ['before', 'after']
    assert evaluate_holdout.main(['--model', 'test:latest', '--output', str(tmp_path)]) == 1


@pytest.mark.parametrize('arguments', [['--per-family', '-1'], ['--per-family', '21'],
                                      ['--minimum-accuracy', 'nan'], ['--minimum-accuracy', '1.5']])
def test_invalid_work_and_score_budgets_fail_before_a_model_call(tmp_path, arguments):
    with pytest.raises(SystemExit):
        evaluate_holdout.main([*arguments, '--output', str(tmp_path)])


def test_partial_report_regrading_recomputes_the_score_gate_and_preserves_prior_exports(tmp_path):
    from app.evaluation.holdout import build_cases, regrade, run
    report = run(lambda _prompt: '0', build_cases()[:3], model='test')
    report.update(evaluation_complete=True, minimum_accuracy_requested=1.0, score_gate_passed=True)
    regraded = regrade(report)
    assert regraded['summary']['cases'] == 3 and regraded['score_gate_passed'] is False
    source = tmp_path / 'original.json'
    source.write_text(json.dumps(report))
    target = tmp_path / 'regraded'
    assert evaluate_holdout.main(['--regrade', str(source), '--regrade-output', str(target)]) == 2
    before = target.with_suffix('.json').read_bytes()
    with pytest.raises(FileExistsError, match='Preserve'):
        evaluate_holdout.main(['--regrade', str(source), '--regrade-output', str(target)])
    assert target.with_suffix('.json').read_bytes() == before


@pytest.mark.parametrize('metadata', [{'remote_host': 'https://billing.invalid'}, {'remote_model': 'remote'},
                                     {'details': {'remote_host': 'https://billing.invalid'}}])
def test_remote_model_metadata_is_rejected_before_any_generation(tmp_path, local_eval, monkeypatch, metadata):
    monkeypatch.setattr('scripts.audit_local_models.request', lambda *_args, **_kwargs: {
        'models': [{'name': 'test:latest', 'digest': 'before', **metadata}]})
    with pytest.raises(PermissionError, match='ZERO-COST'):
        evaluate_holdout.main(['--model', 'test:latest', '--output', str(tmp_path)])
    assert local_eval['responses'] == ['4', '7']
