import json
import threading

import pytest

from app.dual_brain import DualBrain
from app.executive import ExecutiveCortex, ExecutiveLimit
from app.invention import InventionWorkbench
from app.models.base import ModelResponse
from app.models.streaming import GenerationCancelled


class ScriptedModel:
    name, model = 'test', 'fixture'

    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.options = []

    def generate(self, messages, **kwargs):
        self.calls.append(messages)
        self.options.append(kwargs)
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return value if isinstance(value, ModelResponse) else ModelResponse(value, self.model, self.name)


def candidate(formula='x * 2'):
    return json.dumps({'title': 'Numeric prototype', 'hypothesis': 'Double the supplied input.',
                       'design': 'A bounded formula prototype.', 'formula': formula,
                       'assumptions': ['Inputs are supplied by the owner.'], 'next_tests': ['Test physical implementation.']})


def accepted():
    return '{"verdict":"accept","issues":[]}'


def bench(tmp_path, model, research=None):
    cortex = ExecutiveCortex()
    return InventionWorkbench(tmp_path, cortex, DualBrain(cortex), lambda: model, research)


def test_executive_enforces_call_cycle_time_and_cancellation_budgets(monkeypatch):
    cortex = ExecutiveCortex()
    frame = cortex.prepare('Evaluate a design', ['Cost below supplied bound'], ['Measured cases pass'],
                           max_calls=1, max_cycles=1)
    cortex.checkpoint(frame, 'propose', model_call=True, cycle=True)
    with pytest.raises(ExecutiveLimit):
        cortex.checkpoint(frame, 'again', model_call=True)
    with pytest.raises(ExecutiveLimit):
        cortex.checkpoint(frame, 'again', cycle=True)
    event = threading.Event(); event.set()
    with pytest.raises(GenerationCancelled):
        cortex.checkpoint(frame, 'cancel', cancel=event)
    monkeypatch.setattr('app.executive.time.monotonic', lambda: frame.started + 301)
    with pytest.raises(ExecutiveLimit):
        cortex.checkpoint(frame, 'late')
    assert frame.calls == frame.cycles == 1


def test_working_memory_and_status_are_bounded_independent_snapshots():
    cortex = ExecutiveCortex()
    frame = cortex.prepare('Test a goal')
    for number in range(40):
        cortex.observe(frame, 'test', {'number': number})
    status = cortex.status()
    assert len(status['last_goal']['observations']) == 16
    status['last_goal']['goal'] = 'tampered'
    assert cortex.status()['last_goal']['goal'] == 'Test a goal'


def test_critic_requests_correction_without_claiming_independent_verification():
    model = ScriptedModel('{"verdict":"revise","issues":["The product is 391."]}', '17 × 23 = 391.')
    result = DualBrain(ExecutiveCortex()).review('Multiply 17 by 23.', ModelResponse('392', 'fixture', 'test'), model)
    assert result.response.content == '17 × 23 = 391.'
    assert result.report['revised'] and result.report['same_model']
    assert result.report['verified'] is False and len(model.calls) == 2


@pytest.mark.parametrize('output', ['not json', '{"verdict":"accept","issues":"none"}',
                                    ModelResponse('{}', 'fixture', 'test', finish_reason='length')])
def test_bad_critic_result_retains_draft_and_reports_review_failure(output):
    model = ScriptedModel(output)
    draft = ModelResponse('Original answer.', 'fixture', 'test')
    result = DualBrain(ExecutiveCortex()).review('A question', draft, model)
    assert result.response is draft and result.report['state'] == 'unavailable'
    assert result.report['verified'] is False


def test_model_agreement_cannot_override_failed_frozen_tests(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate('x + 1'), accepted(), candidate(), accepted()))
    project = workbench.create('Design a doubling formula', tests=[{'inputs': {'x': 2}, 'expected': 4},
                                                                  {'inputs': {'x': -3}, 'expected': -6}])
    result = workbench.run(project['id'], cycles=2, research=False, work_out=False)
    assert result['state'] == 'TESTED' and len(result['rounds']) == 2
    assert result['rounds'][0]['measurement']['passed'] == 0
    assert result['best']['measurement']['passed'] == 2
    assert not result['best']['measurement']['novelty_verified']
    restored = bench(tmp_path, None).get(project['id'])
    assert restored['acceptance_hash'] == project['acceptance_hash']
    assert restored['tests'] == project['tests'] and restored['rounds'] == result['rounds']


def test_without_acceptance_cases_proposal_needs_validation(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate(''), accepted()))
    project = workbench.create('Design a physical sensor')
    assert workbench.run(project['id'], cycles=1, research=False)['state'] == 'NEEDS_VALIDATION'


def test_model_unavailable_is_a_persistent_blocker_not_fake_completion(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Design a monitor')
    result = workbench.run(project['id'], research=False)
    assert result['state'] == 'BLOCKED' and not result['rounds']
    assert workbench.get(project['id'])['stop_reason'] == result['stop_reason']


def test_research_failure_is_retained_as_an_evidence_gap(tmp_path):
    def unavailable(_):
        raise RuntimeError('Research permission denied')
    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()), unavailable)
    project = workbench.create('Design a formula')
    result = workbench.run(project['id'], cycles=1)
    assert not result['research']['complete'] and 'permission denied' in result['research']['error']
    assert result['state'] == 'NEEDS_VALIDATION'


def test_generated_formula_cannot_execute_imports_or_modify_acceptance_cases(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate('__import__("os").system("echo bad")'), accepted()))
    project = workbench.create('Test a formula', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    result = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    assert result['state'] == 'TEST_FAILED'
    assert 'Unsupported' in result['rounds'][0]['measurement']['cases'][0]['error']
    assert result['tests'] == project['tests']


def test_stopping_during_generation_preserves_rounds_and_prevents_critic(tmp_path):
    started, release = threading.Event(), threading.Event()
    class SlowModel(ScriptedModel):
        def generate(self, messages, **kwargs):
            started.set()
            assert release.wait(5)
            return super().generate(messages, **kwargs)
    model = SlowModel(candidate())
    workbench = bench(tmp_path, model)
    project = workbench.create('Design a formula')
    worker = threading.Thread(target=lambda: workbench.run(project['id'], cycles=1, research=False))
    worker.start()
    assert started.wait(5)
    assert workbench.stop(project['id'])['stop_requested']
    release.set(); worker.join(5)
    assert not worker.is_alive()
    result = workbench.get(project['id'])
    assert result['state'] == 'STOPPED' and result['rounds'] == [] and len(model.calls) == 1


def test_explicit_commands_route_and_plan_without_a_model(tmp_path):
    from app.agent.planner import Planner
    from app.intent.router import IntentRouter
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    from app.tools.invention import register_invention_tools
    workbench = bench(tmp_path, None)
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    register_invention_tools(registry, workbench, workbench.executive, workbench.dual_brain)
    text = 'Create invention project: design a formula'
    assert IntentRouter(None).route(text).classification_source == 'invention_command'
    plan = Planner(registry).create_plan(text)
    assert plan[0].tool == 'invention_create'
    result = registry.execute(plan[0].tool, plan[0].arguments)
    assert result.success and result.output['state'] == 'DRAFT'


def test_blocked_workbench_cannot_pass_final_run_verification():
    from app.agent.verifier import Verifier
    from app.tools.registry import ToolResult
    result = ToolResult('invention_run', True, {'state': 'BLOCKED', 'rounds': []})
    assert not Verifier().check({'type': 'invention_run_finished'}, result).passed


def test_revised_prototype_keeps_schema_and_uses_measured_cases(tmp_path):
    model = ScriptedModel(candidate('x + 1'), '{"verdict":"revise","issues":["This does not double x."]}', candidate())
    workbench = bench(tmp_path, model)
    project = workbench.create('Double each input', tests=[{'inputs': {'x': 3}, 'expected': 6}])
    result = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    assert result['state'] == 'TESTED' and result['best']['review']['revised']
    assert model.options[2]['response_schema'] == workbench.CANDIDATE_SCHEMA
    assert workbench.executive.status()['last_goal']['calls'] == 3


def test_invalid_structured_revision_retains_original_candidate(tmp_path):
    model = ScriptedModel(candidate(), '{"verdict":"revise","issues":["Explain assumptions."]}', '{"formula":"x*2"}')
    workbench = bench(tmp_path, model)
    project = workbench.create('Double each input', tests=[{'inputs': {'x': 3}, 'expected': 6}])
    result = workbench.run(project['id'], cycles=1, research=False, work_out=False)
    assert result['state'] == 'TESTED'
    assert result['best']['review']['state'] == 'revision_failed'
    assert not result['best']['review']['revised']
    snapshot = workbench.dual_brain.status()
    snapshot['last_review']['issues'].append('Tampered')
    assert 'Tampered' not in workbench.dual_brain.status()['last_review']['issues']


def test_live_project_lease_survives_reopen_and_refuses_second_runner(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Test lease recovery')
    claimed = workbench._claim(project['id'])
    other = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    assert other.get(project['id'])['state'] == 'RUNNING'
    with pytest.raises(RuntimeError, match='already running'):
        other.run(project['id'], cycles=1, research=False)
    assert other.get(project['id']) == claimed
    assert not other.model_supplier().calls


def test_dead_project_lease_is_recovered_and_resumable(tmp_path):
    workbench = bench(tmp_path, None)
    project = workbench.create('Double an input', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    project.update(state='RUNNING', run_owner={'pid': -1, 'created': 0})
    workbench._save(project)
    recovered = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    assert recovered.get(project['id'])['state'] == 'INTERRUPTED'
    result = recovered.run(project['id'], cycles=1, research=False)
    assert result['state'] == 'TESTED' and 'run_owner' not in result


def test_altered_acceptance_contract_stops_before_model_inference(tmp_path):
    model = ScriptedModel(candidate(), accepted())
    workbench = bench(tmp_path, model)
    project = workbench.create('Double an input', tests=[{'inputs': {'x': 2}, 'expected': 4}])
    project['tests'][0]['expected'] = 5
    workbench._save(project)
    result = workbench.run(project['id'], cycles=1, research=False)
    assert result['state'] == 'BLOCKED' and 'Acceptance contract changed' in result['stop_reason']
    assert model.calls == []


def test_commands_accept_uppercase_verbs_and_project_identifiers():
    from app.invention_commands import command
    identifier = 'invent-' + 'a' * 32
    assert command(f'RUN INVENTION {identifier.upper()} FOR 2 CYCLES') == (
        'invention_run', {'project_id': identifier, 'cycles': 2, 'research': True})
    assert command(f'STOP INVENTION {identifier} FOR 2 CYCLES') is None


def test_an_accepted_draft_is_kept_without_a_revision_call():
    """Revising on an 'accept' verdict leaked review talk ("the advisor confirmed it") into replies."""
    model = ScriptedModel('{"verdict":"accept","issues":["Could mention Toronto."]}')
    draft = ModelResponse('She lives in Canada.', 'fixture', 'test')
    result = DualBrain(ExecutiveCortex()).review('Which country does she live in?', draft, model)
    assert result.response is draft and not result.report['revised']
    assert len(model.calls) == 1


def test_the_critic_and_reviser_see_the_conversation():
    """Live probe 2026-10-10: reviewed without context, 'Remove milk and add rice' became '- Rice'."""
    model = ScriptedModel('{"verdict":"revise","issues":["Say milk was removed."]}',
                          'Eggs, bread, apples, coffee and rice; milk removed.')
    draft = ModelResponse('Eggs, bread, apples, coffee and rice.', 'fixture', 'test')
    earlier = [('user', 'Here is my shopping list: eggs, milk, bread, apples, coffee.'), ('assistant', 'Got it.')]
    DualBrain(ExecutiveCortex()).review("Remove milk and add rice. What's on the list now?", draft, model,
                                        conversation=earlier)
    for call in model.calls:
        payload = json.loads(call[-1].content)
        assert payload['conversation_so_far'][0]['content'].startswith('Here is my shopping list')


def test_a_tolerance_is_part_of_the_frozen_contract_and_lets_close_answers_pass(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate('x / 3'), accepted()))
    exact = workbench.create('Third of a number', tests=[{'inputs': {'x': 1}, 'expected': '0.3333'}])
    assert exact['tolerance'] == '0' and exact['acceptance_hash'] == workbench._hash(exact['tests'])  # older journals stay valid
    loose = workbench.create('Third of a number', tests=[{'inputs': {'x': 1}, 'expected': '0.3333'}], tolerance=0.001)
    assert loose['acceptance_hash'] != exact['acceptance_hash']
    done = workbench.run(loose['id'], cycles=1, research=False)
    assert done['state'] == 'TESTED' and done['best']['measurement']['cases'][0]['relative_error'] < .001
    assert workbench.try_formula(exact['id'], 'x / 3')['measurement']['passed'] == 0  # exact means exact
    with pytest.raises(ValueError):
        workbench.create('Too loose', tests=[{'inputs': {'x': 1}, 'expected': 1}], tolerance=0.9)
    # Changing the tolerance after creation breaks the contract.
    tampered = workbench.get(loose['id'])
    tampered['tolerance'] = '0.5'
    workbench._save(tampered)
    blocked = bench(tmp_path, ScriptedModel(candidate(), accepted())).run(loose['id'], cycles=1, research=False)
    assert blocked['state'] == 'BLOCKED' and 'Acceptance contract changed' in blocked['stop_reason']


def test_the_closest_round_is_kept_as_best_when_none_pass(tmp_path):
    model = ScriptedModel(candidate('x * 3'), accepted(), candidate('x * 2 + 1'), accepted())
    workbench = bench(tmp_path, model)
    project = workbench.create('Double each input', tests=[{'inputs': {'x': 10}, 'expected': 20}, {'inputs': {'x': 20}, 'expected': 40}])
    done = workbench.run(project['id'], cycles=2, research=False, work_out=False)
    errors = [record['measurement']['mean_relative_error'] for record in done['rounds']]
    assert errors == [pytest.approx(.5), pytest.approx(.0375)] and done['best']['round'] == 2 and done['state'] == 'TEST_FAILED'


def test_trying_a_formula_grades_it_without_saving_and_the_report_reads_well(tmp_path):
    workbench = bench(tmp_path, ScriptedModel(candidate(), accepted()))
    project = workbench.create('Double an input', constraints=['Integers only'], tests=[{'inputs': {'x': 2}, 'expected': 4}])
    trial = workbench.try_formula(project['id'], 'x + x')
    assert trial['measurement']['passed'] == 1 and trial['saved'] is False and workbench.get(project['id'])['rounds'] == []
    assert workbench.try_formula(project['id'], '__import__("os")')['measurement']['cases'][0].get('error')
    with pytest.raises(ValueError):
        workbench.try_formula(project['id'], '')
    workbench.run(project['id'], cycles=1, research=False, work_out=False)
    text = workbench.report_markdown(project['id'])
    assert text.startswith('# Double an input') and '**TESTED**' in text and '- Integers only' in text
    assert '| 1 | model | Numeric prototype | 1/1 |' in text and 'Formula: `x * 2`' in text and 'have not been verified' in text


def test_the_try_command_routes_to_the_tool():
    from app.invention_commands import command
    identifier = 'invent-' + 'a' * 32
    assert command(f'try formula x * 2 on invention {identifier}') == ('invention_try', {'project_id': identifier, 'formula': 'x * 2'})
