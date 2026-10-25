import json

from tests.test_networking_actions import runtime as runtime
from tests.test_public_contacts import manager


def test_actual_assistant_tool_and_evidence_workflow_keeps_durable_contact_content_redacted(runtime, monkeypatch):
    parent, calls = manager()
    parent.fetcher.close = lambda: None
    original = runtime.research.fetcher
    monkeypatch.setattr(runtime.research, 'search', parent.search)
    monkeypatch.setattr(runtime.research, 'fetcher', parent.fetcher)
    try:
        reply = runtime.assistant.handle('Find professional contacts for Ada Example at Example Institute')
        assert reply.complete, reply.display_response
        assert calls and 'ada@institute.example' in reply.display_response
        assert 'Team route (not the named person)' in reply.display_response
        assert all(item['tool'] == 'public_contact_search' for item in reply.outcome.history)
        runtime.events.flush()
        durable = json.dumps([r for r in runtime.events.recent(500) if r['event_type'] in {
            'TASK_STARTED', 'TASK_CREATED', 'TASK_COMPLETED', 'TOOL_STARTED', 'TOOL_COMPLETED'}])
        assert 'ada@institute.example' not in durable and 'Ada Example' not in durable
    finally:
        original.close()


def test_denial_reaches_no_public_provider(runtime, monkeypatch):
    parent, calls = manager()
    original = runtime.research.fetcher
    monkeypatch.setattr(runtime.research, 'search', parent.search)
    monkeypatch.setattr(runtime.research, 'fetcher', parent.fetcher)
    parent.fetcher.close = lambda: None
    monkeypatch.setitem(runtime.registry.permissions.policies, 'public_web_read', 'DENY')
    try:
        result = runtime.registry.execute('public_contact_search', {'name': 'Ada Example', 'organization': 'Example Institute'})
        assert not result.success and not calls
    finally:
        original.close()
