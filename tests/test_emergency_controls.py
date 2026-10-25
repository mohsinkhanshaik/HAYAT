import pytest

from app.emergency import ExecutionInterlock, branch_conversation, stop_runtime


def test_emergency_interlock_does_not_revalidate_old_calls_after_resume():
    gate = ExecutionInterlock()
    ticket = gate.ticket()
    gate.halt()
    with pytest.raises(PermissionError):
        gate.ticket()
    gate.resume()
    with pytest.raises(PermissionError):
        gate.check(ticket)
    gate.check(gate.ticket())


def test_tool_waiting_for_authority_cannot_execute_after_stop_and_resume(monkeypatch):
    from app.tools.registry import ToolRegistry
    registry, calls = ToolRegistry(), []
    registry.register('example', 'test', {'type': 'object'}, 'low', 'system_read', lambda: calls.append('ran'))
    def authorize(*_a, **_k):
        registry.interlock.halt()
        registry.interlock.resume()
        return True
    monkeypatch.setattr(registry, '_authorize', authorize)
    result = registry.execute('example')
    assert not result.success and not calls
    assert 'emergency stop' in result.error


@pytest.fixture
def runtime():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False, interactive_approvals=False)
    runtime.conversation.clear()
    runtime.session.session_id = runtime.conversation.session_id
    yield runtime
    runtime.close()


def test_emergency_stop_blocks_tools_and_assistant_and_clears_pending_work(runtime):
    runtime.session.pending_action = {'action': 'unapproved'}
    runtime.session.pending_offer = {'kind': 'research'}
    report = stop_runtime(runtime)
    assert report['halted']
    assert runtime.session.pending_action is None and runtime.session.pending_offer is None
    assert not runtime.registry.execute('system_status').success
    with pytest.raises(PermissionError):
        runtime.assistant.handle('hello')
    runtime.registry.interlock.resume()
    assert runtime.registry.execute('system_status').success


def test_branch_copies_only_prefix_clears_stale_referents_and_preserves_original(runtime):
    memory, session = runtime.memory, runtime.session
    old = session.session_id
    first = memory.record_message(old, 'user', 'Plan a trip to Boston')
    second = memory.record_message(old, 'assistant', 'We can compare train routes.')
    memory.record_message(old, 'user', 'Actually book a flight instead')
    session.pending_action = {'send': 'do not inherit'}
    session.last_response = 'wrong future answer'
    result = branch_conversation(runtime, second)
    rows = memory.conversation_history(result['session_id'])
    assert [r['content'] for r in rows] == ['Plan a trip to Boston', 'We can compare train routes.']
    assert len(memory.conversation_history(old)) == 3
    assert session.pending_action is None and not session.last_response
    assert runtime.conversation.session_id == session.session_id == result['session_id']
    assert rows[0]['metadata']['branched_from_message'] == first


def test_edit_branch_excludes_selected_request_and_tail_and_rejects_foreign_message(runtime):
    old = runtime.session.session_id
    mid = runtime.memory.record_message(old, 'user', 'wrong request')
    runtime.memory.record_message(old, 'assistant', 'old reply')
    with pytest.raises(ValueError):
        branch_conversation(runtime, mid + 999)
    info = branch_conversation(runtime, mid, edit=True)
    assert info['draft'] == 'wrong request' and info['messages'] == 0
    assert runtime.memory.conversation_history(info['session_id']) == []
    assert len(runtime.memory.conversation_history(old)) == 2


def test_branch_rejects_inflight_turn(runtime):
    sid = runtime.session.session_id
    mid = runtime.memory.record_message(sid, 'user', 'sample')
    runtime.turns.start(session_id=sid, runtime_id='test', raw_input='inflight', input_source='gui')
    with pytest.raises(ValueError, match='Wait'):
        branch_conversation(runtime, mid)
