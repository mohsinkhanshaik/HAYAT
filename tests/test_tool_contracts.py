"""Static tool-wide checks must not run a handler or overstate readiness."""
import json
from pathlib import Path

from app.tools.contracts import audit_tool_contracts
from app.tools.registry import ToolRegistry


def test_contract_audit_finds_bad_arguments_without_invoking_handlers():
    registry = ToolRegistry()
    def handler(required, optional='ok'):
        raise AssertionError('audit must not invoke the handler')
    registry.register('broken', 'A test tool with a mismatched contract', {'type': 'object', 'properties': {
        'wrong': {'type': 'string'}, 'optional': {'type': 'integer', 'default': 'invalid'}}, 'additionalProperties': False},
        'low', 'system_read', handler)
    result = audit_tool_contracts(registry)
    assert {item['code'] for item in result['issues']} == {'unexpected_argument', 'missing_required_argument', 'invalid_schema_default'}
    assert result['errors'] == 3 and not result['contract_checks_passed']


def test_open_kwargs_handler_and_explicit_required_arguments_are_valid():
    registry = ToolRegistry()
    registry.register('flexible', 'A test tool that accepts extra keywords', {'type': 'object', 'properties': {'name': {'type': 'string'}}, 'required': ['name']},
                      'low', 'system_read', lambda name, **_kwargs: None)
    result = audit_tool_contracts(registry)
    assert result['contract_checks_passed'] and not result['issues']
    assert 'No handler was executed' in result['basis']


def test_unknown_arguments_are_rejected_without_mutating_caller_schema(tmp_path):
    from app.safety.permissions import PermissionManager
    approvals, executions = [], []
    schema = {'type': 'object', 'properties': {}}
    registry = ToolRegistry(PermissionManager({'default': 'ASK'}, tmp_path),
                            approval_callback=lambda *args: approvals.append(args) or True)
    registry.register('read', 'Read', schema, 'low', 'system_read', lambda: executions.append(True))
    result = registry.execute('read', {'unaccepted': True})
    assert not result.success and 'ValidationError' in result.error
    assert not approvals and not executions and 'additionalProperties' not in schema


def test_full_runtime_tool_contract_report():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        result = audit_tool_contracts(runtime.registry)
        path = Path(__file__).resolve().parents[1] / '.test-runs' / 'tool-contract-audit.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(f"Tool declarations: {result['tools_checked']} checked, {result['errors']} errors, {result['warnings']} warnings")
        assert result['schemas_valid'] == result['tools_checked']
        assert result['contract_checks_passed'], result['issues']
        assert result['warnings'] == 0, [item for item in result['issues'] if item['severity'] == 'warning']
    finally:
        runtime.close()


def test_the_audit_checks_each_tool_against_the_authority():
    """A misspelt category would silently fall to the extended tier; a sending tool would lose its always-ask protection."""
    registry = ToolRegistry()
    schema = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    registry.register('typo_send', 'Sends a message for the owner to someone', schema, 'high', 'send_mesage', lambda: None)
    registry.register('odd_risk', 'A tool with a risk level nobody uses', schema, 'extreme', 'system_read', lambda: None)
    registry.register('forbidden', 'A tool in a category the authority always refuses', schema, 'high', 'purchase', lambda: None)
    registry.register('quiet_post', 'Posts publicly as the owner on a social network', schema, 'low', 'external_post', lambda: None)
    registry.register('shell', 'Runs a command in CMD and captures the output', schema, 'high', 'terminal', lambda: None)
    registry.register('terse', 'Lists.', schema, 'low', 'system_read', lambda: None)
    result = audit_tool_contracts(registry)
    codes = {(item['tool'], item['code']) for item in result['issues']}
    assert ('typo_send', 'unknown_permission') in codes and ('odd_risk', 'invalid_risk') in codes
    assert ('forbidden', 'prohibited_permission') in codes and ('quiet_post', 'risk_understated') in codes
    assert ('terse', 'thin_description') in codes
    assert not any(tool == 'shell' for tool, _code in codes)  # shell categories are resolved per command
    assert result['errors'] == 3 and result['warnings'] == 2 and not result['contract_checks_passed']
