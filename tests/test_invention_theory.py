"""Theory and proof for invention formulas (app/invention_theory.py) and its workbench, tool and chat wiring."""
import math
import threading

import pytest

from app import invention_theory as theory
from app.dual_brain import DualBrain
from app.executive import ExecutiveCortex
from app.invention import InventionWorkbench
from app.invention_commands import command
from app.models.streaming import GenerationCancelled
from app.observed_reply import _invention


def cases(function, points, digits=6):
    return [{'inputs': point, 'expected': f'{function(**point):.{digits}g}'} for point in points]


PENDULUM = cases(lambda L: 2 * math.pi * math.sqrt(L / 9.81), [{'L': v} for v in (0.25, 0.5, 1, 2, 4)])
GRAVITY = cases(lambda m1, m2, r: 6.674e-11 * m1 * m2 / r ** 2,
                [{'m1': a, 'm2': b, 'r': c} for a, b, c in ((5.97e24, 7.35e22, 3.84e8), (1e3, 2e3, 1.0), (5e10, 3e5, 20.0),
                                                            (7e3, 7e3, 2.5), (1e20, 4e2, 1e6), (2e6, 9e8, 300.0))])


def project(tests, formula, tolerance='0.001', constraints=()):
    return {'tests': tests, 'tolerance': tolerance, 'constraints': list(constraints), 'rounds': [],
            'best': {'candidate': {'formula': formula}}}


# ------------------------------------------------------------------ units

def test_a_fitted_coefficient_must_carry_units_and_is_named_when_it_matches_a_constant():
    report = theory.check_units('6.674e-11 * m1 * m2 / r ** 2', {'m1': 'kg', 'm2': 'kg', 'r': 'm'}, 'N')
    assert report['consistent'] and not report['issues']
    [coefficient] = report['coefficients']
    assert coefficient['units'] == 'm³·kg⁻¹·s⁻²'
    assert 'G (gravitational constant)' in coefficient['identified']


@pytest.mark.parametrize('formula,units,output,where', [
    ('x + t', {'x': 'm', 't': 's'}, 'm', 'x + t'),
    ('exp(t) * x', {'x': 'm', 't': 's'}, 'm', 'exp(t)'),
    ('x ** t', {'x': 'm', 't': '1'}, 'm', 'x ** t'),
    ('sin(x)', {'x': 'm'}, '1', 'sin(x)'),
])
def test_units_that_no_coefficient_can_fix_are_reported_where_they_break(formula, units, output, where):
    report = theory.check_units(formula, units, output)
    assert not report['consistent'] and where in report['issues'][0]


def test_coefficient_units_are_worked_out_inside_functions_and_sums():
    assert theory.check_units('exp(-0.3 * t) * x', {'x': 'm', 't': 's'}, 'm')['coefficients'][0]['units'] == 's⁻¹'
    internal = theory.check_units('V / (R + 0.5)', {'V': 'V', 'R': 'ohm'}, 'A')['coefficients'][0]
    assert internal['units'] == 'm²·kg·s⁻³·A⁻²'  # an ohm: the battery's internal resistance
    assert theory.check_units('x / 2', {'x': 'm'}, 'm')['coefficients'][0]['units'] == 'dimensionless'


def test_undeclared_inputs_are_refused_rather_than_guessed():
    with pytest.raises(ValueError, match='Declare units for y'):
        theory.check_units('x * y', {'x': 'm'}, 'm')


# ------------------------------------------------------------------ derivation

def test_buckingham_derives_the_pendulum_and_drops_the_mass():
    law = theory.derive({'L': 'm', 'g': 'm/s**2', 'm': 'kg'}, 's')
    assert law['possible'] and law['unique'] and law['drops_out'] == ['m']
    assert law['exponents'] == {'L': '1/2', 'g': '-1/2', 'm': '0'}
    assert law['shape'] == 'c1 * L ** (1/2) * g ** (-1/2)'


def test_derivation_names_the_missing_constant_case_and_dimensionless_groups():
    assert not theory.derive({'m1': 'kg', 'm2': 'kg', 'r': 'm'}, 'N')['possible']
    drag = theory.derive({'rho': 'kg/m**3', 'v': 'm/s', 'L': 'm', 'mu': 'Pa*s'}, 'N')
    assert drag['possible'] and len(drag['groups']) == 1  # the Reynolds number
    assert 'F(' in drag['law'] and drag['shape'].count('** c2') == 1


# ------------------------------------------------------------------ the whole argument

def test_pendulum_theory_completes_the_law_with_g_and_finds_two_pi():
    result = theory.theorise(project(PENDULUM, '2.006 * L ** 0.5'), units={'L': 'm'}, output='s')
    coefficient = result['units']['coefficients'][0]
    assert 'π' in coefficient['identified'] and '√g' in coefficient['identified']
    derivation = result['derivation']
    assert derivation['completed_with']['symbol'] == 'g'
    assert derivation['tested']['passed'] == 5 and '2π' in derivation['tested']['c1_identified']
    claims = {row['claim']: row for row in result['proofs']['properties']}
    assert claims['rises with L']['status'] == 'proved'
    assert claims['proportional to L^1/2']['status'] == 'proved'
    ladder = {rung['rung']: rung['met'] for rung in result['ladder']}
    assert ladder['Units are consistent'] and ladder['Agrees with the law derived from units alone']
    assert ladder['Measured on a real device'] is None  # software never claims this rung
    assert 'not a physical experiment' in result['summary'][-1]


def test_owner_claims_are_proved_or_disproved_with_a_counterexample():
    result = theory.theorise(project(GRAVITY, '6.674e-11 * m1 * m2 / r ** 2'), claims=[
        'symmetric in m1 and m2', 'inverse square in r', 'increases with r', 'is positive', 'tastes nice'])
    status = {row['claim']: row for row in result['claims']}
    assert status['symmetric in m1 and m2']['status'] == 'proved'
    assert status['inverse square in r']['status'] == 'proved'
    assert status['increases with r']['status'] == 'disproved' and 'r' in status['increases with r']['counterexample']
    assert status['is positive']['status'] == 'proved'
    assert status['tastes nice']['status'] == 'not understood'
    assert result['cases']['exact'] == 6


def test_a_bound_claim_is_disproved_by_a_real_counterexample():
    [row] = theory.check_claims('2.006 * L ** 0.5', PENDULUM, ['at most 3'])
    assert row['status'] == 'disproved'
    point = row['counterexample']['L']
    assert 2.006 * point ** 0.5 > 3


def test_constraints_that_read_as_claims_are_checked_too():
    result = theory.theorise(project(PENDULUM, '2.006 * L ** 0.5', constraints=['decreases with L', 'Costs under $5']))
    assert [row['claim'] for row in result['claims']] == ['decreases with L']
    assert result['claims'][0]['status'] == 'disproved'


def test_cases_are_proved_exactly_where_arithmetic_allows():
    exact = theory.prove_cases('2 * x + 1', [{'inputs': {'x': '0.5'}, 'expected': '2'}, {'inputs': {'x': '3'}, 'expected': '8'}])
    assert exact['exact'] == 1 and exact['failed'] == 1  # 2*3+1 is 7, not 8
    roots = theory.prove_cases('sqrt(x)', [{'inputs': {'x': '2'}, 'expected': '1.41421356'}], '0.000001')
    assert roots['fifty_digit'] == 1 and roots['exact'] == 0


def test_the_decisive_test_is_where_rival_formulas_disagree_most():
    line = [{'inputs': {'x': v}, 'expected': str(2 * v)} for v in (1, 2, 3)]
    found = theory.decisive_test('2 * x', [{'formula': '2 * x + 0.01 * (x - 1) * (x - 2) * (x - 3)', 'description': 'a cubic'}],
                                 line, 0)
    assert found['decisive'] and found['gap'] > 0.02 and 'a cubic' in found['text']


def test_inconsistent_units_fail_the_rung_instead_of_being_hidden():
    tests = [{'inputs': {'x': v, 't': v}, 'expected': str(2 * v)} for v in (1, 2, 3)]
    result = theory.theorise(project(tests, 'x + t', '0'), units={'x': 'm', 't': 's'}, output='m')
    ladder = {rung['rung']: rung['met'] for rung in result['ladder']}
    assert ladder['Units are consistent'] is False
    assert any(line.startswith('Units: NOT consistent') for line in result['summary'])


def test_theory_stops_when_cancelled():
    event = threading.Event()
    event.set()
    with pytest.raises(GenerationCancelled):
        theory.theorise(project(PENDULUM, '2.006 * L ** 0.5'), cancel=event)


# ------------------------------------------------------------------ workbench, tools, chat

def bench(tmp_path):
    cortex = ExecutiveCortex()
    return InventionWorkbench(tmp_path, cortex, DualBrain(cortex), lambda: None)


def test_workbench_keeps_units_and_theorises_after_working_out(tmp_path):
    workbench = bench(tmp_path)
    created = workbench.create('Pendulum clock timing', tests=PENDULUM, tolerance='0.001', units={'L': 'm'}, output_unit='s')
    assert created['units'] == {'inputs': {'L': 'm'}, 'output': 's'}
    data = workbench.work_out(created['id'])
    assert data['theory']['units']['consistent']
    assert workbench.get(created['id'])['theory']['formula'] == data['best']['candidate']['formula']
    report = workbench.report_markdown(created['id'])
    assert '## Theory and proof' in report and '| Units are consistent | yes |' in report
    result = workbench.theorise(created['id'], claims=['increases with L'])
    assert result['saved'] and result['claims'][0]['status'] == 'proved'


def test_workbench_refuses_units_for_names_that_are_not_inputs(tmp_path):
    workbench = bench(tmp_path)
    with pytest.raises(ValueError, match='not an input'):
        workbench.create('x', tests=PENDULUM, units={'Q': 'm'})
    with pytest.raises(ValueError, match='Unknown unit'):
        workbench.create('x', tests=PENDULUM, units={'L': 'furlongs'})


def test_a_trial_formula_is_theorised_but_not_saved_as_the_projects_theory(tmp_path):
    workbench = bench(tmp_path)
    created = workbench.create('Pendulum', tests=PENDULUM, tolerance='0.001')
    workbench.work_out(created['id'])
    before = workbench.get(created['id']).get('theory')
    result = workbench.theorise(created['id'], formula='2 * L ** 0.5')
    assert result['formula'] == '2 * L ** 0.5' and workbench.get(created['id']).get('theory') == before


def test_chat_commands_and_replies_reach_the_theory():
    identifier = 'invent-' + 'a' * 32
    assert command(f'prove invention {identifier}') == ('invention_theory', {'project_id': identifier})
    assert command(f'explain why invention {identifier} works') == ('invention_theory', {'project_id': identifier})
    result = theory.theorise(project(PENDULUM, '2.006 * L ** 0.5'), units={'L': 'm'}, output='s')
    reply = _invention(result)
    assert reply.startswith('Theory and proof:') and '2π' in reply
    derived = _invention(theory.derive({'L': 'm', 'g': 'm/s**2', 'm': 'kg'}, 's'))
    assert 'From units alone' in derived and 'Cannot affect the result: m' in derived


def test_the_tools_are_registered_with_their_schemas():
    from app.tools.invention import register_invention_tools

    class Registry:
        def __init__(self):
            self.tools = {}

        def register(self, name, description, schema, risk, category, handler):
            self.tools[name] = (schema, risk, category)

    registry = Registry()
    simple = type('S', (), {'status': None, 'list': None, 'get': None, 'assess': None, 'create': None, 'run': None,
                            'work_out': None, 'simulate': None, 'try_formula': None, 'holdout': None, 'stop': None,
                            'theorise': None, 'preview_research': None, 'create_from_research': None})()
    register_invention_tools(registry, simple, simple, simple)
    schema, risk, category = registry.tools['invention_theory']
    assert risk == 'low' and category == 'filesystem_write_project' and 'claims' in schema['properties']
    assert registry.tools['invention_derive_law'][2] == 'system_read'
    assert 'units' in registry.tools['invention_create'][0]['properties']


def test_the_theory_tab_proves_the_best_formula_from_the_window(shared_tk, tmp_path):
    import time
    from types import SimpleNamespace

    from app.gui_invention import InventionPanel
    from app.gui_theme import Palette
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry
    from tests.conftest import close_window, open_window

    workbench = bench(tmp_path)
    created = workbench.create('Pendulum clock timing', tests=PENDULUM, tolerance='0.001')
    workbench.work_out(created['id'])
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui-fixture'))
    root = open_window(shared_tk, '1100x760-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)

    def finish():
        until = time.monotonic() + 30
        while panel._pending and time.monotonic() < until:
            root.update()
            time.sleep(.01)
        assert not panel._pending
    try:
        panel._call('invention_get', {'project_id': created['id']})
        finish()
        assert 'Theory' in [panel.tabs.tab(tab, 'text') for tab in panel.tabs.tabs()]
        panel.theory_units.set('L=bad unit here')
        panel.theorise_best()
        finish()
        assert 'Could not complete' in panel.status.get() or 'Unknown unit' in panel.status.get()
        panel.theory_units.set('L=m')
        panel.theory_output.set('s')
        panel.theory_claims.set('increases with L; at most 3')
        panel.theory_button.invoke()
        finish()
        text = panel.theory_text.get('1.0', 'end')
        assert '2π' in text and 'Evidence ladder' in text and 'disproved' in text
        assert '✓ met — Units are consistent' in text and '· open — Measured on a real device' in text
        assert panel.tabs.tab(panel.tabs.select(), 'text') == 'Theory'
        assert 'evidence rungs met' in panel.status.get()
        assert workbench.get(created['id'])['units'] == {'inputs': {'L': 'm'}, 'output': 's'}
    finally:
        close_window(root)


def test_the_first_line_is_plain_words_without_symbols_for_everyone():
    result = theory.theorise(project(PENDULUM, '2.006 * L ** 0.5'), units={'L': 'm'}, output='s', claims=['at most 3'])
    first = result['summary'][0]
    assert first.startswith('In plain words: The formula matches 5 of your 5 measurements')
    assert 'units make sense' in first and '“at most 3”' in first and 'not been tested on a real device' in first
    assert not any(symbol in first for symbol in ('∂', '^', '·', '√', '**'))
    broken = theory.theorise(project([{'inputs': {'x': v, 't': v}, 'expected': str(2 * v)} for v in (1, 2, 3)], 'x + t', '0'),
                             units={'x': 'm', 't': 's'}, output='m')
    assert 'units do not make sense' in broken['summary'][0]


# ------------------------------------------------------------------ deep check on benchmark laws (2026-11-07)

def test_device_constants_leave_agreement_open_instead_of_failing_it():
    rc = cases(lambda t: 9 * (1 - math.exp(-t / 2.2)), [{'t': v} for v in (0.5, 1, 2, 4, 8)])
    result = theory.theorise(project(rc, '9 - 9 * exp(-(5 / 11) * t)', '0.002'), units={'t': 's'}, output='V')
    agreement = result['agreement']
    assert agreement['agrees'] is None and 'device constants' in agreement['detail']
    assert {c['units'] for c in agreement['device_constants']} == {'m²·kg·s⁻³·A⁻¹', 's⁻¹'}  # volts, and 1/(time constant)
    rung = {r['rung']: r['met'] for r in result['ladder']}['Agrees with the law derived from units alone']
    assert rung is None  # open, never "no": units alone can never supply a capacitor's time constant
    assert 'belonging to this particular device' in result['summary'][0]


def test_a_fraction_is_one_number_with_units():
    [coefficient] = theory.check_units('V / (R + 1 / 2)', {'V': 'V', 'R': 'ohm'}, 'A')['coefficients']
    assert coefficient['text'] == '1 / 2' and coefficient['value'] == 0.5 and coefficient['units'] == 'm²·kg·s⁻³·A⁻²'


def test_a_function_of_dimensionless_groups_is_not_held_against_the_formula():
    lens = cases(lambda u, f: u * f / (u - f), [{'u': u, 'f': f} for u, f in ((0.3, 0.1), (0.5, 0.2), (1, 0.25), (2, 0.5), (0.8, 0.3))])
    result = theory.theorise(project(lens, 'u * f / (u - f)'), units={'u': 'm', 'f': 'm'}, output='m')
    assert result['agreement']['agrees'] is True and 'F(' in result['agreement']['detail']
    assert {r['rung']: r['met'] for r in result['ladder']}['Agrees with the law derived from units alone'] is True


def test_doppler_is_completed_with_the_speed_of_sound_even_with_a_minus_sign():
    doppler = cases(lambda f, v: f * 343 / (343 - v), [{'f': f, 'v': v} for f, v in ((440, 10), (1000, 30), (250, 60), (800, 100), (500, 5))])
    result = theory.theorise(project(doppler, '-343 * f / (v - 343)'), units={'f': 'Hz', 'v': 'm/s'}, output='Hz')
    assert result['derivation']['completed_with']['symbol'] == 'v_sound'
    assert result['agreement']['agrees'] is True and 'v_sound' in result['agreement']['detail']


def test_a_single_dimensionless_input_does_not_crash_the_derivation():
    law = theory.derive({'b': '1'}, '1')  # relativistic gamma: sympy's ilcm needed two numbers
    assert law['possible'] and law['groups'] == ['b']


def test_wrong_but_consistent_formula_is_not_called_agreeing():
    result = theory.theorise(project(PENDULUM, '0.64 * L + 0.9'), units={'L': 'm'}, output='s')
    assert result['agreement']['agrees'] is None  # 0.64 s/m and 0.9 s match no constant: open, not confirmed
