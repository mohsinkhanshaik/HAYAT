"""Working inventions out and simulating them (app/invention_sim.py and the workbench around it).

The numbers are real: least-squares fits, exact grading and a seeded Monte Carlo. Only the
language model is scripted (tests/test_executive_invention.py).
"""
import json
import math
import time
from types import SimpleNamespace

import pytest

from app import invention_sim as sim
from tests.test_executive_invention import ScriptedModel, accepted, bench, candidate

LINE = [{'inputs': {'x': x}, 'expected': 2 * x + 3} for x in (1, 2, 5, 7)]
PENDULUM = [{'inputs': {'L': length}, 'expected': round(2 * math.pi * math.sqrt(length / 9.81), 6)}
            for length in (0.25, 0.5, 1, 2, 4)]
AREA = [{'inputs': {'w': w, 'h': h}, 'expected': w * h} for w, h in ((2, 3), (4, 5), (1, 7), (3, 3))]


# -- formulas: what they may say, and what they may not ------------------------------------

@pytest.mark.parametrize('formula', ['__import__("os")', 'x.__class__', '(lambda: 1)()', '[x][0]', 'open("f")',
                                     'sqrt(x, key=1)', '__builtins__'])
def test_nothing_but_arithmetic_names_and_listed_functions_can_run(formula):
    case = sim.grade(formula, LINE)['cases'][0]
    assert case['passed'] is False and case.get('error')


def test_a_tower_of_powers_fails_at_once_instead_of_hanging():
    started = time.monotonic()
    case = sim.grade('10**10**10', LINE)['cases'][0]
    assert case.get('error') and time.monotonic() - started < 2


def test_exact_arithmetic_is_used_when_possible_and_floats_only_when_needed():
    exact = sim.grade('2*x + 3', LINE)
    assert exact['passed'] == 4 and exact['floating_point_cases'] == 0
    physical = sim.grade('2*pi*sqrt(L/9.81)', PENDULUM, tolerance=0.001)
    assert physical['passed'] == 5 and physical['floating_point_cases'] == 5
    assert all(case['exact'] is False for case in physical['cases'])


# -- fitting constants ---------------------------------------------------------------------

def test_unknown_constants_are_fitted_and_written_as_short_numbers():
    fitted = sim.fit('c1*L**c2', PENDULUM, tolerance=0.001)
    assert fitted['formula'] == '2.006 * sqrt(L)'
    assert fitted['parameters']['c1'] == pytest.approx(2 * math.pi / math.sqrt(9.81), rel=1e-4)
    assert sim.fit('c1 + c2*x', LINE)['formula'] == '3 + 2 * x'


def test_a_constant_that_comes_out_as_one_disappears():
    assert sim.fit('c1*w*h', AREA)['formula'] == 'w * h'


def test_more_than_six_unknown_constants_are_refused():
    with pytest.raises(ValueError, match='At most 6'):
        sim.fit('a1+a2*x+a3*x**2+a4*x**3+a5*x**4+a6*x**5+a7*x**6', LINE)


# -- working a formula out with no model -----------------------------------------------------

def test_standard_shapes_find_a_line_a_power_law_and_a_product():
    assert sim.work_out(LINE)['best']['formula'] == '3 + 2 * x'
    pendulum = sim.work_out(PENDULUM, tolerance=0.001)['best']
    assert pendulum['description'] == 'a power law in L' and pendulum['measurement']['passed'] == 5
    assert sim.work_out(AREA)['best']['formula'] == 'h * w'


def test_leave_one_out_predicts_each_case_from_a_fit_that_never_saw_it():
    best = sim.work_out(LINE)['best']
    assert best['leave_one_out']['predicted_within_tolerance'] == 4
    noisy = [{'inputs': {'x': x}, 'expected': value} for x, value in ((1, 3), (2, 9), (3, 4), (4, 12), (5, 2))]
    assert sim.work_out(noisy)['best']['leave_one_out']['mean_relative_error'] > 0.3  # nothing explains noise


def test_a_shape_with_as_many_constants_as_cases_ranks_last():
    two = [{'inputs': {'x': 1}, 'expected': 5}, {'inputs': {'x': 2}, 'expected': 9}]
    result = sim.work_out(two)
    # A straight line (two constants) passes both cases but would pass any two: a one-constant shape ranks first.
    assert result['best']['too_flexible'] is False and result['best']['constants'] == 1
    line = sim.fit('c1 + c2*x', two)
    assert sim.grade(line['formula'], two)['passed'] == 2


# -- simulating a formula --------------------------------------------------------------------

def test_sweeps_say_which_way_the_result_goes_and_where_it_breaks():
    result = sim.simulate('2.006*L**0.5', PENDULUM, tolerance=0.001)
    sweep = result['sweeps'][0]
    assert sweep['trend'] == 'rises' and sweep['breaks_at'] and all(x < 0.25 for x in sweep['breaks_at'])
    assert not result['warnings']  # it breaks only outside the cases' range
    assert 'beyond the cases' in sim.describe_simulation(result)[0]


def test_a_pole_or_a_gap_inside_the_cases_range_is_a_warning():
    assert 'infinity near x' in sim.simulate('1/(x-3)', LINE)['warnings'][0]
    assert 'no value for x' in sim.simulate('sqrt(x-3)', LINE)['warnings'][0]


def test_sensitivity_ranks_the_input_that_matters_most_first():
    cases = [{'inputs': {'x': x, 'z': z}, 'expected': x ** 3 * z} for x, z in ((1, 2), (2, 3), (3, 1))]
    effects = sim.simulate('x**3*z', cases)['sensitivity']
    assert effects[0]['input'] == 'x' and effects[0]['effect_of_10_percent'] == pytest.approx(0.331)
    assert effects[1]['effect_of_10_percent'] == pytest.approx(0.1)


def test_the_monte_carlo_is_seeded_and_tighter_spread_passes_more_often():
    loose = sim.simulate('2*x + 3', LINE, tolerance=0.05, spread=0.3)
    again = sim.simulate('2*x + 3', LINE, tolerance=0.05, spread=0.3)
    tight = sim.simulate('2*x + 3', LINE, tolerance=0.05, spread=0.02)
    assert loose['monte_carlo'] == again['monte_carlo']
    assert tight['monte_carlo']['share_within_band'] > loose['monte_carlo']['share_within_band']
    assert tight['monte_carlo']['band_from'] == 'tolerance'


def test_a_simulation_needs_every_constant_known():
    with pytest.raises(ValueError, match='Fit the constants first'):
        sim.simulate('c1*x', LINE)


# -- the workbench ---------------------------------------------------------------------------

def test_work_it_out_saves_a_fitted_round_and_its_simulation(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Pendulum period from length', tests=PENDULUM, tolerance=0.001)
    data = workbench.work_out(project['id'])
    record = data['rounds'][0]
    assert data['state'] == 'TESTED' and record['method'] == 'fit' and record['candidate']['formula'] == '2.006 * sqrt(L)'
    assert record['leave_one_out']['cases'] == 5 and record['alternatives']
    assert data['simulation']['summary'] and data['simulation']['formula'] == '2.006 * sqrt(L)'
    report = workbench.report_markdown(project['id'])
    assert '## Simulation of the best formula' in report and '| 1 | fitted, no model |' in report


def test_without_a_model_a_run_still_works_the_formula_out(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Box area', tests=AREA)
    result = workbench.run(project['id'], research=False)
    assert result['state'] == 'TESTED' and result['best']['candidate']['formula'] == 'h * w'
    assert 'model is unavailable' in result['stop_reason'] and result['simulation']['sweeps']


def test_without_a_model_or_cases_the_run_is_still_blocked(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Design a monitor')
    assert workbench.run(project['id'], research=False)['state'] == 'BLOCKED'


def test_the_model_chooses_the_shape_and_hayat_fits_its_constants(tmp_path):
    model = ScriptedModel(candidate('c1 * x'), accepted())
    workbench = bench(tmp_path, model)
    project = workbench.create('Doubling', tests=[{'inputs': {'x': 2}, 'expected': 4}, {'inputs': {'x': 5}, 'expected': 10}])
    result = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    record = result['rounds'][0]
    assert record['candidate']['formula'] == '2 * x' and record['fitted'] == {'shape': 'c1 * x', 'constants': {'c1': 2.0}}
    assert result['state'] == 'TESTED'


def test_the_proposer_sees_every_earlier_attempt_and_the_fitted_baseline(tmp_path):
    model = ScriptedModel(candidate('x * 3'), accepted())
    workbench = bench(tmp_path, model)
    noisy = [{'inputs': {'x': x}, 'expected': value} for x, value in ((1, 3), (2, 9), (3, 4), (4, 12))]
    project = workbench.create('Something hard', tests=noisy)
    result = workbench.run(project['id'], cycles=1, research=False)
    assert [record['method'] for record in result['rounds']] == ['fit', 'model']
    context = json.loads(model.calls[0][1].content)
    assert context['tried_so_far'][0]['round'] == 1 and context['tried_so_far'][0]['formula']
    assert 'c1 or c2' in model.calls[0][0].content and 'sqrt' in model.calls[0][0].content


def test_trying_a_shape_fits_it_and_says_so(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Line', tests=LINE)
    trial = workbench.try_formula(project['id'], 'c1*x + c2')
    assert trial['formula'] == '2 * x + 3' and trial['fitted_constants'] == {'c1': 2.0, 'c2': 3.0}
    assert trial['measurement']['passed'] == 4 and workbench.get(project['id'])['rounds'] == []


def test_simulating_a_project_keeps_the_best_formulas_result(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Line', tests=LINE)
    with pytest.raises(ValueError, match='no formula to simulate'):
        workbench.simulate(project['id'])
    workbench.work_out(project['id'])
    mine = workbench.simulate(project['id'], formula='c1*x', spread=0.2)
    assert mine['formula'].endswith(' * x') and 'c1' not in mine['formula'] and mine['monte_carlo']['spread'] == 0.2
    assert workbench.get(project['id'])['simulation']['formula'] == '3 + 2 * x'  # yours is shown, not saved over it


# -- tools, commands and replies --------------------------------------------------------------

def test_tools_and_chat_commands_reach_work_out_and_simulate(tmp_path):
    from app.invention_commands import command
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry

    identifier = 'invent-' + 'a' * 32
    assert command(f'Work out invention {identifier}') == ('invention_work_out', {'project_id': identifier})
    assert command(f'simulate invention {identifier} with 20% spread') == (
        'invention_simulate', {'project_id': identifier, 'spread': 0.2})
    assert command(f'simulate formula c1*x on invention {identifier}')[1]['formula'] == 'c1*x'
    assert command(f'simulate invention {identifier} with 80% spread') is None
    workbench = bench(tmp_path, None)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    project = workbench.create('Line', tests=LINE)
    worked = registry.execute('invention_work_out', {'project_id': project['id']})
    assert worked.success and worked.output['state'] == 'TESTED'
    simulated = registry.execute('invention_simulate', {'project_id': project['id'], 'spread': 0.05})
    assert simulated.success and simulated.output['monte_carlo']['spread'] == 0.05


def test_replies_describe_a_worked_out_project_and_a_simulation(tmp_path):
    from app.observed_reply import render

    workbench = bench(tmp_path, None)
    project = workbench.create('Line', tests=LINE)
    text = render(workbench.work_out(project['id']))
    assert 'TESTED' in text and 'worked out by fitting, no model' in text and '3 + 2 * x' in text
    assert 'Predicting each case from the others' in text and 'novelty and physical validity are not verified' in text
    simulation = render(workbench.simulate(project['id']))
    assert simulation.startswith('Simulation of 3 + 2 * x:') and 'does not show that a physical device' in simulation


# -- the window --------------------------------------------------------------------------------

def test_the_workbench_window_works_it_out_and_draws_the_simulation(shared_tk, tmp_path):
    from app.gui_invention import InventionPanel
    from app.gui_theme import Palette
    from app.safety.permissions import PermissionManager
    from app.tools.invention import register_invention_tools
    from app.tools.registry import ToolRegistry
    from tests.conftest import close_window, open_window

    workbench = bench(tmp_path, None)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    runtime = SimpleNamespace(registry=registry, invention=workbench, session=SimpleNamespace(session_id='gui-fixture'))
    root = open_window(shared_tk, '1100x760-4000-4000')
    panel = InventionPanel(root, Palette, runtime, lambda *_: None)
    panel.pack(fill='both', expand=True)

    def finish():
        until = time.monotonic() + 20
        while panel._pending and time.monotonic() < until:
            root.update()
            time.sleep(.01)
        assert not panel._pending
    try:
        project = workbench.create('Pendulum period', tests=PENDULUM, tolerance=0.001)
        panel.refresh()
        finish()
        panel.projects.selection_set(project['id'])
        panel.open_project()
        finish()
        panel.work_button.invoke()
        finish()
        assert panel.data['rounds'][0]['method'] == 'fit' and panel.state_badge.cget('text') == 'TESTED'
        assert '2.006 * sqrt(L)' in panel.sim_text.get('1.0', 'end')
        panel.round_list.selection_set('1')
        panel.round_selected()
        assert 'no model was used' in panel.round_text.get('1.0', 'end')
        assert 'Other shapes that came close' in panel.round_text.get('1.0', 'end')
        panel.formula.set('c1*sqrt(L)')
        panel.spread.set('20')
        panel.simulate_mine_button.invoke()
        finish()
        root.update()
        assert panel.tabs.index('current') == 3 and 'sqrt(L)' in panel.sim_text.get('1.0', 'end')
        assert panel.simulation['monte_carlo']['spread'] == 0.2
        panel.draw_simulation()
        texts = [panel.sim_chart.itemcget(item, 'text') for item in panel.sim_chart.find_all()
                 if panel.sim_chart.type(item) == 'text']
        assert 'L · rises' in texts and 'Effect of +10 %' in texts
    finally:
        close_window(root)


# -- harder: laws outside the first shape list, tiny constants, aliasing --------------------------

@pytest.mark.parametrize("name", ["gravitation (tiny constant)", "radioactive decay", "battery with internal resistance",
                                  "damped oscillation", "Kepler's third law", "compound interest"])
def test_hard_laws_are_worked_out_and_predict_unseen_cases(name):
    """From scripts/benchmark_invention.py, which solves all 16 of its laws (2026-10-17)."""
    from scripts.benchmark_invention import TASKS, run_task

    result = run_task(next(task for task in TASKS if task["name"] == name))
    assert result["solved"], result


def test_the_benchmark_formulas_read_like_the_textbook():
    from scripts.benchmark_invention import TASKS, run_task

    expected = {"gravitation (tiny constant)": "6.674e-11 * m1 * m2 / r ** 2", "compound interest": "P * (1 + r) ** n",
                "battery with internal resistance": "V / (R + 1 / 2)", "damped oscillation": "exp(-(3 / 10) * t) * cos(2 * t)"}
    for task in TASKS:
        if task["name"] in expected:
            assert run_task(task)["formula"] == expected[task["name"]]


def test_among_equally_good_fits_the_plainer_one_wins():
    cases = [{"inputs": {"x": x}, "expected": format(math.sin(x) / x, ".10g")} for x in (0.5, 1, 1.5, 2, 3, 4, 5, 6)]
    # sin(11.5664 x) / x matches at every multiple of 0.5 too (aliasing); the smaller constants are the answer.
    assert sim.fit("c1*sin(c2*x)/x", cases, tolerance=0.001)["formula"] == "sin(x) / x"


def test_a_large_intermediate_step_falls_back_to_floating_point():
    case = [{"inputs": {"m1": "5.97e24", "m2": "1.99e30", "r": "1.5e11"}, "expected": "3.5216e22"}]
    graded = sim.grade("6.674e-11 * m1 * m2 / r ** 2", case, tolerance=0.001)
    assert graded["passed"] == 1 and graded["cases"][0]["exact"] is False


def test_a_formula_that_fails_inside_python_is_an_ordinary_case_failure():
    run = sim.compile_formula("x ** 0.5")
    with pytest.raises(ValueError):
        run({"x": -4.0})  # a complex result, not a crash
    with pytest.raises(ValueError, match="c9"):
        sim.compile_formula("c9 * x")({"x": 1.0})


def test_readable_output_rules():
    assert sim._substitute("c1*V**c2*R**c3", {"c1": "1", "c2": "2", "c3": "-1"}) == "V ** 2 / R"
    assert sim._substitute("c1*x + c2", {"c1": "-1", "c2": "3"}) == "-x + 3"
    assert sim._substitute("c1*L**c2", {"c1": "2", "c2": "1/2"}) == "2 * sqrt(L)"
    assert sim._substitute("c1*x + c2*y", {"c1": "0", "c2": "1/3"}) == "y / 3"


# -- the model loop: no repeats, and the critic sees the measurement ----------------------------------

def test_a_repeated_formula_is_sent_back_once_for_a_different_one(tmp_path):
    model = ScriptedModel(candidate('x * 3'), accepted(), candidate('x * 3'), candidate('x * 2'), accepted())
    workbench = bench(tmp_path, model)
    project = workbench.create('Doubling', tests=[{'inputs': {'x': 2}, 'expected': 4}, {'inputs': {'x': 5}, 'expected': 10}])
    result = workbench.run(project['id'], cycles=2, research=False, work_out=False)
    assert [record['candidate']['formula'] for record in result['rounds']] == ['x * 3', 'x * 2']
    asked_again = model.calls[3][-1].content
    assert 'already tried in round 1' in asked_again and 'passed 0 of 2' in asked_again
    assert result['state'] == 'TESTED'


def test_the_critic_reviews_against_the_measurement(tmp_path):
    model = ScriptedModel(candidate('x * 3'), accepted())
    workbench = bench(tmp_path, model)
    project = workbench.create('Doubling', tests=[{'inputs': {'x': 2}, 'expected': 4}, {'inputs': {'x': 5}, 'expected': 10}])
    workbench.run(project['id'], cycles=1, research=False, work_out=False)
    critic_input = model.calls[1][-1].content
    assert 'Measured on the frozen cases before this review: x * 3 passed 0 of 2' in critic_input
    assert "expected 4, got 6" in critic_input and 'the measurement outranks any argument' in critic_input
