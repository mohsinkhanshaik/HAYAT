from unittest.mock import Mock


def test_safe_mode_survives_broken_optional_configuration(monkeypatch, tmp_path):
    import app.runtime as module
    from app.recovery import CrashRecovery
    from app.safety.authority_store import AuthorityStore

    monkeypatch.setattr(module, "AuthorityStore", lambda: AuthorityStore(tmp_path / "authority.db"))
    monkeypatch.setattr(module, "CrashRecovery", lambda memory: CrashRecovery(memory, tmp_path / "alive.json"))
    connector = Mock(side_effect=ValueError("broken optional connector configuration"))
    skills = Mock(side_effect=ValueError("broken custom skill"))
    monkeypatch.setattr(module, "ConnectorRegistry", connector)
    monkeypatch.setattr(module.SkillRegistry, "reload", skills)
    runtime = module.build_runtime(use_model=False, safe_mode=True)
    try:
        assert runtime.safe_mode
        assert runtime.connectors is None and runtime.mcp_gateway is None
        assert runtime.browser_extension is None
        assert not runtime.scheduler.scheduler.running and not runtime.learner.running
        assert runtime.registry.permissions is not None and runtime.registry.authority is runtime.authority
        assert "read_file" in runtime.registry.names()
        assert runtime.assistant is not None
        connector.assert_not_called()
        skills.assert_not_called()
    finally:
        runtime.close()
