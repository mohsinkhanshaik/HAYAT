from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.agent.verifier import Verifier
from app.session import SessionState
from app.tools.registry import ToolResult


def event(tool, output, success=True):
    return {'tool': tool, 'result': {'success': success, 'output': output}}


def test_final_browser_state_must_be_latest_observation():
    history = [event('browser_navigate', {'url': 'https://example.com/docs'}),
               event('browser_navigate', {'url': 'https://elsewhere.test/'})]
    assert not Verifier().check_goal([{'type': 'browser_destination', 'value': 'https://example.com/docs'}], history).passed


def test_window_closed_then_reopened_is_not_still_verified_closed():
    history = [event('close_application', {'application': 'notepad', 'closed': True, 'target_window_exists': False}),
               event('launch_application', {'application': 'notepad', 'running': True, 'window': {'visible': True}})]
    assert not Verifier().check_goal([{'type': 'target_window_absent', 'target': 'notepad'}], history).passed


def test_unrelated_tool_does_not_erase_a_valid_browser_observation():
    history = [event('browser_navigate', {'url': 'https://example.com/docs'}),
               event('read_file', {'path': 'README.md', 'content': 'hello'})]
    assert Verifier().check_goal([{'type': 'browser_destination', 'value': 'https://example.com/docs'}], history).passed


@pytest.mark.parametrize('kind', ['url_matches', 'browser_destination'])
@pytest.mark.parametrize('actual', ['https://example.com.evil.test/docs', 'https://example.com/docs-fake',
                                   'http://example.com/docs', 'https://example.com:8443/docs',
                                   'https://example.com/docs?v=wrong', 'https://x@example.com/docs?v=right'])
def test_destinations_compare_origin_path_boundaries_and_query(kind, actual):
    result = ToolResult('browser_navigate', True, {'url': actual})
    assert not Verifier().check({'type': kind, 'value': 'https://example.com/docs?v=right'}, result).passed


def test_nested_destination_and_extra_query_are_allowed():
    result = ToolResult('browser_navigate', True, {'url': 'https://www.example.com/docs/guide?v=right&lang=en'})
    assert Verifier().check({'type': 'browser_destination', 'value': 'https://example.com/docs?v=right'}, result).passed


def test_nonzero_process_exit_cannot_pass_generic_success():
    checked = Verifier().check({'type': 'tool_success'}, ToolResult('python', True, {'exit_code': 1, 'stderr': 'SyntaxError'}))
    assert not checked.passed


def test_failed_result_cannot_replace_session_targets():
    session = SessionState(active_application='notepad', last_file='real.txt', last_browser_tab={'url': 'https://real.test'})
    session.update_from_outcome(SimpleNamespace(history=[event('bad', {
        'application': 'chrome', 'path': 'missing.txt', 'url': 'https://failed.test'}, False)]))
    assert session.active_application == 'notepad'
    assert session.last_file == 'real.txt'
    assert session.last_browser_tab == {'url': 'https://real.test'}


def make_agent(tmp_path, plan, max_steps):
    from app.agent.core import Agent
    from app.memory.database import MemoryDatabase
    memory = MemoryDatabase(tmp_path / 'accuracy.db')
    memory.initialize()
    planner = SimpleNamespace(create_plan=Mock(return_value=plan), replan=Mock(return_value=[]),
                              last_knowledge_ids=[], last_direct_response=None)
    registry = SimpleNamespace(reset_budget=Mock())
    executor = SimpleNamespace(registry=registry, execute=Mock(return_value=ToolResult('test', False, error='failure')))
    return Agent(planner, executor, Verifier(), memory, max_steps=max_steps)


def test_retries_stop_at_the_total_call_budget(tmp_path):
    from app.agent.planner import PlanStep
    agent = make_agent(tmp_path, [PlanStep('Try a read', 'test', {}, max_retries=10)], 2)
    outcome = agent.run('Read the file')
    assert not outcome.complete
    assert agent.executor.execute.call_count == 2
    agent.planner.replan.assert_not_called()
    assert 'execution budget' in outcome.summary


def test_oversized_plan_is_rejected_without_executing_a_partial_task(tmp_path):
    from app.agent.planner import PlanStep
    agent = make_agent(tmp_path, [PlanStep('Do a step', 'test', {}) for _ in range(3)], 2)
    outcome = agent.run('Complete all three steps')
    assert not outcome.complete and 'shorten the task' in outcome.summary
    agent.executor.execute.assert_not_called()


def test_oversized_recovery_cannot_discard_the_original_remaining_step(tmp_path):
    from app.agent.planner import PlanStep
    agent = make_agent(tmp_path, [PlanStep('First', 'test', {}, max_retries=0),
                                 PlanStep('Required last step', 'last', {})], 3)
    agent.planner.replan.return_value = [PlanStep('Recovery', 'test', {}) for _ in range(2)]
    outcome = agent.run('Do both')
    assert not outcome.complete and 'no partial recovery' in outcome.summary
    assert agent.executor.execute.call_count == 1


def test_unverified_result_cannot_replace_session_targets():
    session = SessionState(last_file='real.txt')
    row = event('read_file', {'path': 'missing.txt'})
    row['verification'] = {'passed': False}
    session.update_from_outcome(SimpleNamespace(history=[row]))
    assert session.last_file == 'real.txt'


def test_unmatched_task_cannot_succeed_by_listing_the_project(tmp_path):
    from app.agent.planner import Planner
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry(PermissionManager({'default': 'ALLOW', 'permissions': {}}, tmp_path))
    registry.register('list_folder', 'List a folder', {'type': 'object'}, 'low', 'filesystem_read', Mock())
    with pytest.raises(ValueError, match='No substitute action'):
        Planner(registry).create_plan('Perform a task that has no matching tool')
