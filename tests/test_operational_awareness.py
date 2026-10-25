from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from app.operational_awareness import execution_state
from app.self_view import describe_self


def test_previous_task_evidence_is_not_current_work_or_a_percentage():
    previous = SimpleNamespace(task_id='old', goal='Earlier task', complete=False, needs_user_input=True,
        goal_verification={}, history=[{'tool': 'probe', 'arguments': {'secret': 'private-value'},
            'verification': {'passed': False}}, {'tool': 'probe', 'verification': {'passed': True}},
            {'tool': 'unknown', 'verification': None}])
    agent = SimpleNamespace(is_running=True, active_goal='New task', active_activity='executing',
        active_plan=[{}, {}], active_step={'tool': 'current_probe'}, active_history=[])
    evidence = execution_state(agent, SimpleNamespace(last_task=previous))
    assert evidence['current']['attempts'] == 0 and evidence['current']['goal'] == 'New task'
    assert evidence['last_task']['status'] == 'waiting_for_input'
    assert evidence['last_task']['verified_attempts'] == 1
    assert evidence['last_task']['failed_checks'] == 1
    assert evidence['last_task']['unchecked_attempts'] == 1
    assert evidence['last_task']['goal_verified'] is None
    assert 'private-value' not in str(evidence)
    rendered = describe_self({'execution': evidence})
    assert 'not recorded' in rendered and 'not a percentage' in rendered


def test_completion_report_does_not_override_a_failed_goal_check():
    outcome = SimpleNamespace(complete=True, goal='Conflict', goal_verification={'passed': False}, history=[])
    evidence = execution_state(SimpleNamespace(is_running=False), SimpleNamespace(last_task=outcome))
    assert evidence['last_task']['verification_conflict']
    assert 'Needs review' in describe_self({'execution': evidence})


def test_real_agent_reports_active_retries_then_clears_current_evidence(tmp_path, monkeypatch):
    from app.agent.core import Agent
    from app.agent.executor import Executor
    from app.agent.planner import Planner, PlanStep
    from app.agent.verifier import Verifier
    from app.memory.database import MemoryDatabase
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    memory = MemoryDatabase(tmp_path / 'tasks.db')
    memory.initialize()
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW'}, tmp_path))
    reached, release = Event(), Event()
    attempts = []
    def measured():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('Transient fixture error')
        return {'value': 42}
    def finish():
        reached.set()
        assert release.wait(10)
        return {'finished': True}
    for name, handler in [('measure', measured), ('finish', finish)]:
        registry.register(name, 'Task fixture', {'type': 'object', 'properties': {}}, 'low', 'system_read', handler)
    planner = Planner(registry)
    monkeypatch.setattr(planner, 'create_plan', lambda *args: [PlanStep('measure', 'measure', {}, max_retries=1),
                                                             PlanStep('finish', 'finish', {}, max_retries=0)])
    agent = Agent(planner, Executor(registry), Verifier(), memory)
    session = SimpleNamespace(last_task=None)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(agent.run, 'Complete the measured fixture')
        try:
            assert reached.wait(10)
            current = execution_state(agent, session)['current']
            assert current['current_tool'] == 'finish' and current['attempts'] == 2
            assert current['verified_attempts'] == 1 and current['failed_checks'] == 1
        finally:
            release.set()
        session.last_task = future.result(10)
    assert session.last_task.complete
    final = execution_state(agent, session)
    assert final['current'] is None and not final['running'] and agent.active_history == []
    assert final['last_task']['attempts'] == 3 and final['last_task']['verified_attempts'] == 2


@pytest.mark.parametrize('question,control', [
    ('show your self-view', 'SELF_AWARENESS'), ('what have you actually verified?', 'SELF_AWARENESS'),
    ('what do you know about yourself?', 'SELF_AWARENESS'), ('are you conscious?', 'SELF_EXPERIENCE'),
    ('are you self aware?', 'SELF_EXPERIENCE'), ('do you have subjective experience?', 'SELF_EXPERIENCE')])
def test_self_awareness_questions_use_observations_without_a_model(question, control):
    from app.intent.router import IntentRouter
    from app import self_knowledge
    assert IntentRouter().route(question).control == control
    if control == 'SELF_EXPERIENCE':
        assert 'debated' in self_knowledge.answer(control, {}, None)


def test_actual_self_view_tool_and_chat_agree_without_a_model():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        result = runtime.registry.execute('about_hayat', {'topic': 'awareness'})
        assert result.success and 'Task evidence in this session' in result.output['text']
        assert 'No completed task record' in result.output['text']
        answer = runtime.assistant.handle('show your self-view').display_response
        assert 'Task evidence in this session' in answer and 'No active model' in answer
    finally:
        runtime.close()
