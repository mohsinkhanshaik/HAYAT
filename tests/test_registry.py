from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def allow_config(tmp_path):
    return PermissionManager({"default": "DENY", "permissions": {"test": "ALLOW"}}, tmp_path)


def test_register_and_execute(tmp_path):
    registry = ToolRegistry(allow_config(tmp_path))
    registry.register("add", "Add values", {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}, "low", "test", lambda a, b: a + b)
    result = registry.execute("add", {"a": 2, "b": 3})
    assert result.success
    assert result.output == 5
    assert result.duration_ms >= 0
    assert registry.schemas()[0]["function"]["name"] == "add"


def test_schema_rejection(tmp_path):
    registry = ToolRegistry(allow_config(tmp_path))
    registry.register("integer", "Needs int", {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}, "low", "test", lambda value: value)
    result = registry.execute("integer", {"value": "wrong"})
    assert not result.success
    assert "ValidationError" in result.error


def ask_config(tmp_path):
    return PermissionManager({"default": "DENY", "permissions": {"test": "ASK"}}, tmp_path)


def test_reassigning_approval_callback_actually_changes_the_live_decision(tmp_path):
    """Regression test: registry.approval_callback used to be a plain attribute
    nobody read. execute() consults registry.approvals (an ApprovalBroker
    bound at construction), so swapping approval_callback after construction
    -- exactly what VoiceManager.activate()/stop() and the Windows integration
    check script do -- silently had no effect, and every ASK-permission tool
    call kept using the original construction-time callback. This is the root
    cause a voice-driven Notepad task could report zero verified actions even
    though the write should have been approved."""
    registry = ToolRegistry(ask_config(tmp_path), approval_callback=lambda *_: False)
    registry.register("write", "Write", {"type": "object", "properties": {}}, "high", "test", lambda: "done")

    denied = registry.execute("write", {})
    assert not denied.success
    assert denied.error == "User approval was not granted."

    registry.approval_callback = lambda *_: True
    approved = registry.execute("write", {})
    assert approved.success
    assert approved.output == "done"

    # Reading the property must reflect the live broker callback, not a stale copy.
    assert registry.approvals.callback is registry.approval_callback


def test_authority_sees_allow_configured_tools_and_can_still_deny(tmp_path):
    """Privacy/principal policy must not be bypassed by an ALLOW YAML entry."""
    from app.execution import execution_scope
    from app.safety.authority import AuthorityManager

    permissions = PermissionManager(
        {"default": "ASK", "permissions": {"web_search": "ALLOW"}}, tmp_path,
    )
    registry = ToolRegistry(permissions)
    registry.authority = AuthorityManager(owner_mode=True)
    registry.register("search", "search", {"type": "object", "properties": {}},
                      "low", "web_search", lambda: "network touched")

    registry.authority.set_privacy_mode(True)
    assert not registry.execute("search").success
    registry.authority.set_privacy_mode(False)
    with execution_scope(principal="UNTRUSTED_EXTERNAL_CONTENT"):
        result = registry.execute("search")
    assert not result.success and "External content" in result.error
