from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.core import redact_ephemeral_event
from app.agent.planner import Planner
from app.automation.jobs import JobRepository, ScheduledJob
from app.downloads.manager import DependencyManager, DownloadManager
from app.events import EventBus
from app.memory.database import MemoryDatabase
from app.models.router import ModelRouter
from app.perception.camera.manager import CameraManager, CameraState
from app.perception.clipboard import ClipboardManager
from app.policy.cost import CostPolicy
from app.backups import BackupManager
from app.research.search import build_search_provider
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.storage.manager import StorageManager
from app.tools.registry import ToolRegistry


def test_busy_windows_clipboard_does_not_break_system_state(monkeypatch):
    monkeypatch.setattr(
        "app.perception.clipboard.ImageGrab.grabclipboard",
        lambda: (_ for _ in ()).throw(OSError("clipboard locked")),
    )
    status = ClipboardManager().status()
    assert status["type"] == "temporarily_unavailable"
    assert status["reason"] == "clipboard_busy"


def test_zero_cost_policy_blocks_cloud_runtime_provider(monkeypatch):
    policy = CostPolicy()
    config = {
        "default_model": {"provider": "openai", "model": "paid"},
        "providers": {
            "ollama": {"base_url": "http://localhost:11434", "model": "local"},
            "lm_studio": {"base_url": "http://localhost:1234/v1", "model": ""},
            "openai": {"base_url": "https://api.openai.com/v1", "api_key": "configured", "model": "paid"},
            "anthropic": {"base_url": "https://api.anthropic.com", "api_key": "configured", "model": "paid"},
            "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta", "api_key": "configured", "model": "paid"},
        },
    }
    router = ModelRouter(config, policy)
    selected = router.get("default_model")
    assert selected is None or selected.name in {"ollama", "lm_studio"}
    assert not ({"openai", "anthropic", "gemini"} & set(router.available()))
    assert policy.status()["estimated_api_cost_usd"] == 0


def test_paid_search_key_is_ignored_in_zero_cost_mode(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "configured-but-blocked")
    provider = build_search_provider({"search_provider": "auto", "paid_search_allowed": False})
    assert "brave" not in [item.name for item in provider.providers]


def test_download_governance_rejects_insecure_or_untrusted_sources(tmp_path: Path):
    storage = StorageManager({}, project_root=Path.cwd())
    manager = DownloadManager(storage, tmp_path / "downloads")
    with pytest.raises(ValueError): manager.download("http://github.com/example/file")
    with pytest.raises(PermissionError): manager.download("https://untrusted.invalid/file")
    assert manager.status()["automatic_execution"] is False
    partial = manager.root / "interrupted.bin.partial"; partial.write_bytes(b"incomplete")
    assert manager.cleanup()["files"] == 1 and not partial.exists()
    with manager._connect() as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(downloads)")}
    assert {"purpose", "version", "license", "used_by"} <= columns


def test_camera_observation_is_temporary_and_device_is_released(tmp_path: Path):
    manager = CameraManager({"retain_frames": False}, vision=SimpleNamespace(analyze=lambda path, prompt: {"interpretation": "test"}))
    manager.temp_root = tmp_path
    manager.ocr = SimpleNamespace(status=lambda: {"available": False}, read=lambda path: {"available": False, "text": ""})

    class Backend:
        active = False; device_index = None
        def open(self, index, width, height): self.active = True; self.device_index = index; return {"active": True}
        def save(self, path): path.write_bytes(b"temporary"); return {"path": str(path), "width": 10, "height": 10, "bytes": 9}
        def close(self): self.active = False; self.device_index = None
    manager.capture_backend = Backend()
    result = manager.observe()
    assert result["raw_frame_retained"] is False
    assert result["frames_captured"] == 4
    assert result["frames_analyzed"] == 3
    assert result["analysis_method"] == "multi_frame_ranked_consensus"
    assert manager.state == CameraState.OFF and not list(tmp_path.glob("frame-*.jpg"))


def test_clipboard_ephemeral_result_is_redacted_before_persistence():
    event = {"result": {"output": {"_ephemeral": True, "type": "text", "content": "private", "characters": 7, "sha256": "hash", "sensitive_possible": True}}}
    persistent = redact_ephemeral_event(event)
    assert "content" not in persistent["result"]["output"]
    assert persistent["result"]["output"]["redacted"] is True
    assert event["result"]["output"]["content"] == "private"


def test_event_bus_is_durable_and_redacts_secret_keys(tmp_path: Path):
    path = tmp_path / "events.db"; bus = EventBus(path)
    try:
        identifier = bus.publish("TASK_STARTED", "test", {"goal": "safe", "api_key": "secret"})
        # Persistence is asynchronous; the publisher no longer waits on SQLite.
        assert bus.flush() is True
        row = bus.recent(1)[0]
        # Placeholders are diagnostic (type, length, digest); assert the property,
        # not the exact literal, so redaction detail can improve without churn.
        # `sequence` is the authoritative order assigned at publish time; the
        # SQLite rowid is incidental storage detail.
        assert row["sequence"] == identifier
        assert row["payload"]["api_key"].startswith("[REDACTED")
        assert "secret" not in row["payload"]["api_key"]
    finally:
        bus.close()
    path.replace(tmp_path / "events-moved.db")


def test_job_execution_states_and_task_checkpoints(tmp_path: Path):
    memory = MemoryDatabase(tmp_path / "memory.db"); memory.initialize()
    repository = JobRepository(memory); repository.save(ScheduledJob("job", "goal", "once", {"run_at": "2030-01-01T00:00:00Z"}))
    execution = repository.begin_execution("job"); repository.update_execution(execution, "RUNNING", checkpoint={"step": 1}); repository.update_execution(execution, "WAITING")
    assert repository.executions()[0]["state"] == "WAITING"
    memory.create_task("task", "goal", []); memory.record_checkpoint("task", 0, "RUNNING", {"safe": True})
    assert memory.checkpoints("task")[0]["checkpoint"]["safe"] is True


def test_planner_has_deterministic_multimodal_paths():
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}))
    for name in ("camera_status", "camera_on", "camera_off", "camera_devices", "camera_observe", "clipboard_status", "clipboard_read_text", "clipboard_analyze_image", "display_status", "display_screenshot", "move_active_window_to_display", "incident_snapshot"):
        registry.register(name, name, {"type": "object"}, "low", "test", lambda **kwargs: kwargs)
    planner = Planner(registry)
    session = SessionState()
    assert planner.create_plan("Turn on the camera", session=session)[0].tool == "camera_on"
    assert planner.create_plan("What am I showing you?", session=session)[0].tool == "camera_observe"
    assert planner.create_plan("Explain what I copied", session=session)[0].tool == "clipboard_read_text"
    assert planner.create_plan("Screenshot the second monitor", session=session)[0].tool == "display_screenshot"
    assert planner.create_plan("My Ollama service is down", session=session)[0].tool == "incident_snapshot"


def test_dependency_status_matches_project_environment():
    status = DependencyManager().status()
    assert status["missing"] == []
    assert any(row["package"] == "opencv-python-headless" for row in status["packages"])


def test_database_backup_is_consistent_hashed_and_excludes_env(tmp_path: Path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES('verified')")

    class Storage:
        def ensure_capacity(self, size):
            assert size >= 0

    manager = BackupManager(Storage(), tmp_path / "backups", [source])
    result = manager.create("test checkpoint")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    copied = Path(result["path"]) / "source.db"
    with sqlite3.connect(copied) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "verified"
    assert manifest["files"][0]["sha256"]
    assert manifest["includes_env"] is False
    assert result["restore"] == "manual-review-required"
