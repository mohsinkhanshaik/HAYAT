"""Fault injection for provider contracts and complete shutdown."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.local import OpenAICompatibleProvider
from app.recovery import CrashRecovery


def test_compatible_provider_preserves_budget_and_truncation(monkeypatch):
    post = Mock(return_value=SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"model": "local-coder", "choices": [{
            "message": {"content": "partial answer"}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 24}},
    ))
    monkeypatch.setattr("app.models.local.httpx.post", post)
    result = OpenAICompatibleProvider("lm_studio", "http://localhost:1234/v1", "local-coder").generate(
        [{"role": "user", "content": "explain"}], max_tokens=24)
    assert post.call_args.kwargs["json"]["max_tokens"] == 24
    assert result.truncated and result.model == "local-coder"


def _runtime(tmp_path):
    from app.runtime import Runtime

    runtime = object.__new__(Runtime)
    runtime._closed = False
    runtime.shutdown_errors = []
    for name in ("voice", "event_triggers", "browser_extension", "learner", "camera",
                 "scheduler", "browser", "research", "events"):
        setattr(runtime, name, SimpleNamespace(stop=Mock(return_value=None),
                close=Mock(return_value=None), request_stop=Mock(return_value=None), publish=Mock()))
    runtime.recovery = CrashRecovery(None, tmp_path / "alive.json")
    runtime.recovery.mark_started("test")
    return runtime


@pytest.mark.parametrize("failed", ["voice", "camera", "browser", "research", "events", "learner"])
def test_failed_cleanup_does_not_skip_other_services_or_claim_clean_exit(tmp_path, failed):
    runtime = _runtime(tmp_path)
    method = "stop" if failed in {"voice", "learner"} else "close"
    getattr(getattr(runtime, failed), method).side_effect = RuntimeError("offline")
    runtime.close()
    runtime.voice.stop.assert_called_once()
    runtime.learner.stop.assert_called_once()
    runtime.camera.close.assert_called_once()
    runtime.browser.close.assert_called_once()
    runtime.events.close.assert_called_once()
    assert runtime.shutdown_errors[0]["component"] == failed
    assert runtime.recovery.marker.is_file()
    runtime.close()
    runtime.events.close.assert_called_once()


def test_successful_shutdown_clears_marker_only_after_events_close(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.events.close.side_effect = lambda: runtime.recovery.marker.is_file() or pytest.fail("early marker removal")
    runtime.close()
    assert not runtime.shutdown_errors
    assert not runtime.recovery.marker.exists()


def test_timed_out_learner_is_not_a_clean_shutdown(tmp_path):
    runtime = _runtime(tmp_path)
    runtime.learner.stop.return_value = False
    runtime.close()
    assert runtime.shutdown_errors[0]["component"] == "learner"
    assert runtime.recovery.marker.exists()


def test_other_live_process_marker_is_not_reconciled_or_removed(tmp_path, monkeypatch):
    import json
    marker = tmp_path / "alive.json"
    original = json.dumps({"pid": 123456, "session_id": "still-active", "owner_token": "other"})
    marker.write_text(original)
    recovery = CrashRecovery(None, marker)
    monkeypatch.setattr(recovery, "_process_alive", lambda pid: True)
    assert not recovery.mark_started("second")["unclean_shutdown"]
    assert recovery.greeting() == ""
    recovery.mark_clean_exit()
    assert marker.read_text() == original


def test_old_runtime_cannot_remove_new_runtime_marker(tmp_path):
    first = CrashRecovery(None, tmp_path / "alive.json")
    second = CrashRecovery(None, first.marker)
    first.mark_started("one")
    second.mark_started("two")
    first.mark_clean_exit()
    assert first.marker.exists()
    second.mark_clean_exit()
    assert not second.marker.exists()


def test_disabled_background_never_starts_configured_scheduler(monkeypatch):
    import app.runtime as module
    original = module.load_settings
    monkeypatch.setattr(module, "load_settings", lambda: original() | {"scheduler": {"enabled": True}})
    start = Mock()
    monkeypatch.setattr(module.AgentScheduler, "start", start)
    runtime = module.build_runtime(use_model=False, start_background_services=False)
    try:
        start.assert_not_called()
    finally:
        runtime.close()
