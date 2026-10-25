import base64
import json
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.task_graph import GraphStore, ordered_nodes, plan_text
from app.voice.isolated import IsolatedWhisperSTT


def echo_worker(connection, config):
    while True:
        operation, audio = connection.recv()
        connection.send({"result": None if operation == "warmup" else {"text": "hello", "alternatives": []}})


def crash_worker(connection, config):
    connection.recv()
    os._exit(42)


def stuck_worker(connection, config):
    connection.recv()
    time.sleep(30)


def test_native_worker_crash_does_not_crash_parent_and_next_request_recovers():
    provider = IsolatedWhisperSTT({"worker_timeout_seconds": 8}, worker=crash_worker)
    try:
        with pytest.raises(RuntimeError, match="worker exited"):
            provider.warmup()
        provider._worker = echo_worker
        provider.warmup()
        assert provider._process.is_alive()
    finally:
        provider.close()
    assert provider._process is None


def test_worker_timeout_and_cancellation_reap_the_process():
    provider = IsolatedWhisperSTT({"worker_timeout_seconds": 1}, worker=stuck_worker)
    with pytest.raises(TimeoutError):
        provider.warmup()
    assert provider._process is None
    cancel = threading.Event()
    provider.timeout = 8
    timer = threading.Timer(.5, cancel.set)
    timer.start()
    try:
        assert provider._request("warmup", cancel=cancel) is None
        assert provider._process is None
    finally:
        timer.cancel()
        provider.close()


def test_worker_reuses_model_process_and_reopens_after_voice_toggle():
    provider = IsolatedWhisperSTT({"worker_timeout_seconds": 8}, worker=echo_worker)
    try:
        provider.warmup()
        pid = provider._process.pid
        provider.warmup()
        assert provider._process.pid == pid
        provider.close()
        provider.reopen()
        provider.warmup()
        assert provider._process.pid != pid
    finally:
        provider.close()


def test_graph_drafts_persist_topological_order_and_reject_cycles(tmp_path):
    nodes = [{"id": "2", "goal": "Verify", "depends_on": ["1"]},
             {"id": "1", "goal": "Build", "depends_on": []}]
    store = GraphStore(tmp_path / "workspace.db")
    store.save("Release", nodes)
    assert [node["id"] for node in store.load("Release")] == ["1", "2"]
    assert "2. Verify (after: 1)" in plan_text(nodes)
    nodes[1]["depends_on"] = ["2"]
    with pytest.raises(ValueError, match="cycle"):
        store.save("Release", nodes)
    assert store.load("Release")[0]["depends_on"] == []
    store.delete("Release")
    assert store.names() == []


@pytest.mark.parametrize("nodes", [
    [{"id": "1", "goal": "x", "depends_on": ["2"]}],
    [{"id": "1", "goal": "x"}, {"id": "1", "goal": "y"}],
    [{"id": "1", "goal": ""}],
    [{"id": "1", "goal": "x", "depends_on": "1"}],
])
def test_invalid_graphs_are_rejected(nodes):
    with pytest.raises(ValueError):
        ordered_nodes(nodes)


def test_gallery_unifies_origins_and_preview_respects_permission_and_scanner(tmp_path):
    from PIL import Image
    from app.gallery import gallery_records, register_gallery_tools
    from app.memory.database import MemoryDatabase
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    target = tmp_path / "selected.png"
    Image.new("RGB", (1000, 500), "blue").save(target)
    memory.store_memory("semantic", "attached image", .5, "attachment", metadata={"file_kind": "image", "path": str(target)})
    images = SimpleNamespace(history=lambda count: [{"path": str(tmp_path / "generated.png"), "prompt": "mountain"}])
    assert {row["origin"] for row in gallery_records(images, memory)} == {"Generated", "Attached"}
    assert len(gallery_records(images, memory, "mountain")) == 1
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    scanner = SimpleNamespace(scan=Mock(return_value={"clean": True}))
    registry = ToolRegistry(permissions)
    register_gallery_tools(registry, permissions, scanner, images, memory)
    result = registry.execute("image_preview", {"path": str(target)})
    assert result.success and base64.b64decode(result.output["png"]).startswith(b"\x89PNG")
    permissions.policies["filesystem_read"] = "DENY"
    scanner.scan.reset_mock()
    assert not registry.execute("image_preview", {"path": str(target)}).success
    scanner.scan.assert_not_called()
    permissions.policies["filesystem_read"] = "ALLOW"
    scanner.scan.return_value = {"clean": False}
    assert not registry.execute("image_preview", {"path": str(target)}).success


def test_gallery_marks_deleted_files_so_they_are_not_previewed(tmp_path):
    """Records outlive their files. Previewing each deleted one logged an
    image_preview failure, and HAYAT's self-review then called the tool
    unreliable (20 of 36 calls) when every failure was a file that was gone."""
    from PIL import Image
    from app.gallery import register_gallery_tools
    from app.memory.database import MemoryDatabase
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    present = tmp_path / "present.png"
    Image.new("RGB", (10, 10), "blue").save(present)
    gone = tmp_path / "deleted.png"
    images = SimpleNamespace(history=lambda count: [
        {"path": str(present), "prompt": "kept"}, {"path": str(gone), "prompt": "deleted"},
    ])
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    registry = ToolRegistry(permissions)
    register_gallery_tools(registry, permissions, SimpleNamespace(scan=Mock(return_value={"clean": True})), images, memory)

    rows = {row["prompt"]: row for row in registry.execute("image_gallery", {}).output["images"]}

    assert rows["kept"]["missing"] is False
    assert rows["deleted"]["missing"] is True


def test_supervisor_exclusive_lock_and_bounded_backoff(tmp_path):
    from scripts.supervise import acquire_lock, restart_delay
    lock = acquire_lock(tmp_path / "supervisor.lock")
    try:
        assert lock is not None
        assert acquire_lock(tmp_path / "supervisor.lock") is None
        assert [restart_delay(i) for i in range(1, 7)] == [5, 15, 45, 135, 300, 300]
    finally:
        lock.close()
    second = acquire_lock(tmp_path / "supervisor.lock")
    assert second is not None
    second.close()


def test_supervisor_stops_after_normal_close_and_caps_crash_loop(tmp_path, monkeypatch):
    from scripts import supervise
    monkeypatch.setattr(supervise, "STATE", tmp_path)
    monkeypatch.setattr(supervise, "existing_runtime", lambda: False)
    monkeypatch.setattr(supervise.time, "sleep", lambda seconds: None)
    launch = Mock(return_value=SimpleNamespace(poll=lambda: 0, returncode=0, pid=1))
    monkeypatch.setattr(supervise.subprocess, "Popen", launch)
    assert supervise.main() == 0
    assert launch.call_count == 1
    launch.reset_mock()
    launch.return_value = SimpleNamespace(poll=lambda: 42, returncode=42, pid=1)
    assert supervise.main() == 1
    assert launch.call_count == 5
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "failed"


def test_endurance_records_completed_time_and_clean_shutdown(tmp_path):
    from scripts.endurance import run
    result = run(tmp_path, seconds=.4, interval=.05)
    assert result["status"] == "passed" and result["cycles"] >= 1
    assert result["elapsed_seconds"] >= .4
    with pytest.raises(ValueError, match="fresh"):
        run(tmp_path, seconds=.1)
