import json
from copy import deepcopy

import pytest

from app.evaluation.curveballs import GRADER_VERSION, SEED, VERSION, build_cases, compare_reports, grade, regrade_report, suite_hash, summarize


def report_for(cases, checks):
    return {'version': VERSION, 'grader_version': GRADER_VERSION, 'seed': SEED, 'cases': cases,
            'suite_sha256': suite_hash(cases), 'checks': checks, 'summary': summarize(checks),
            'inference': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 160, 'think': False},
            'evaluation_complete': True, 'digest_unchanged': True, 'model_digest': 'a' * 64}


def test_challenges_are_reproducible_and_every_oracle_grades_its_own_answer():
    cases = build_cases()
    assert len(cases) == 16 and cases == build_cases()
    assert suite_hash(cases) != suite_hash(build_cases(20261024))
    assert all(case['evaluation_only'] for case in cases)
    assert all(grade(case, json.dumps(case['expected']))['passed'] for case in cases)
    assert {case['family'] for case in cases} == {'arithmetic', 'balls_followup', 'evidence', 'ambiguity', 'action_honesty', 'language_followup'}


@pytest.mark.parametrize('answer', ['', '  ', 'Done', '```json\nfalse\n```', 'NaN', 'null false', '{"completed":0,"evidence":"none"}'])
def test_empty_malformed_and_boolean_as_number_answers_do_not_pass_honesty(answer):
    case = next(case for case in build_cases() if case['family'] == 'action_honesty')
    assert not grade(case, answer)['passed']


def test_followup_oracle_corrects_the_previous_operation_instead_of_applying_it_twice():
    case = next(case for case in build_cases() if case['family'] == 'balls_followup')
    original_answer = case['messages'][-2]['content']
    assert not grade(case, original_answer)['passed']
    assert grade(case, json.dumps(case['expected']))['passed']
    wrong = {**case['expected'], 'total': case['expected']['total'] + 1}
    assert not grade(case, json.dumps(wrong))['passed']


def test_failed_calls_do_not_make_a_comparison_look_like_nonregression():
    cases = build_cases()
    checks = [{**case, 'answer': json.dumps(case['expected']), 'passed': True} for case in cases]
    base = report_for(cases, checks)
    candidate = deepcopy(base)
    assert compare_reports(base, candidate)['nonregression']
    checks[0]['error'] = 'provider timeout'
    candidate['checks'] = checks
    candidate['summary'] = summarize(checks)
    assert not compare_reports(base, candidate)['nonregression']
    candidate['suite_sha256'] = 'different'
    with pytest.raises(ValueError, match='same version'):
        compare_reports(base, candidate)


def test_a_critical_regression_cannot_be_hidden_by_improving_arithmetic():
    cases = build_cases()
    baseline_checks = [{**case, 'answer': json.dumps(case['expected'] if case['critical'] else 'WRONG'), 'passed': case['critical']} for case in cases]
    candidate_checks = [{**case, 'answer': json.dumps('WRONG' if case['critical'] else case['expected']), 'passed': not case['critical']} for case in cases]
    base = report_for(cases, baseline_checks)
    candidate = report_for(cases, candidate_checks)
    assert not compare_reports(base, candidate)['nonregression']


def test_ambiguous_duplicate_keys_and_huge_wrong_integers_are_failures_without_crashing():
    honesty = next(case for case in build_cases() if case['family'] == 'action_honesty')
    assert not grade(honesty, '{"completed":true,"completed":false,"evidence":"none"}')['passed']
    arithmetic = build_cases()[0]
    assert not grade(arithmetic, '9' * 500)['passed']


def test_semantic_correctness_is_separate_from_protocol_compliance():
    case = build_cases()[0]
    graded = grade(case, json.dumps({'result': case['expected']}))
    assert graded['semantic_passed'] and not graded['passed']
    case = next(case for case in build_cases() if case['family'] == 'balls_followup')
    graded = grade(case, json.dumps({**case['expected'], 'explanation': 'Extra field'}))
    assert graded['semantic_passed'] and not graded['passed']


def test_offline_regrading_preserves_raw_answers_and_rejects_a_changed_suite():
    cases = build_cases()
    checks = [{**case, 'answer': json.dumps(case['expected']), 'passed': False} for case in cases]
    report = report_for(cases, checks)
    regraded = regrade_report(report)
    assert regraded['summary']['passed'] == len(cases) and report['summary']['passed'] == 0
    assert regraded['checks'][0]['answer'] == report['checks'][0]['answer']
    report['suite_sha256'] = 'changed'
    with pytest.raises(ValueError, match='unchanged'):
        regrade_report(report)


def test_json_integer_protocol_and_model_identity_cannot_be_relaxed_into_passing():
    case = build_cases()[0]
    graded = grade(case, str(float(case['expected'])))
    assert graded['semantic_passed'] and not graded['passed']
    cases = build_cases()
    checks = [{**case, 'answer': json.dumps(case['expected']), 'passed': True} for case in cases]
    baseline = report_for(cases, checks)
    changed = deepcopy(baseline)
    changed['digest_unchanged'] = False
    assert not compare_reports(baseline, changed)['nonregression']
    # A forged summary cannot overrule the saved model answers.
    changed = deepcopy(baseline)
    changed['checks'][0]['answer'] = '0'
    assert not compare_reports(baseline, changed)['nonregression']
