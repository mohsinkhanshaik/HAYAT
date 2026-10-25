from pathlib import Path
from types import SimpleNamespace

from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.development.code_monitor import CodeMonitor
from app.downloads.manager import DownloadManager
from app.intent.router import IntentRouter
from app.intent.local_files import local_file_request
from app.observed_reply import render
from app.projects import ProjectManager
from app.selfimprove import SelfImprovement
from app.tools.registry import ToolRegistry, ToolResult


def test_registered_projects_are_scanned_incrementally_and_errors_have_source(tmp_path):
    projects = ProjectManager(tmp_path / "projects.json")
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "ok.py").write_text("answer = 42\n", encoding="utf-8")
    (two / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (two / "node_modules").mkdir()
    (two / "node_modules" / "ignore.py").write_text("def nope(:", encoding="utf-8")
    projects.register("One", str(one))
    projects.register("Two", str(two))
    notices = []
    monitor = CodeMonitor(projects, path=tmp_path / "monitor.db", notify=notices.append)

    first = monitor.scan_all()
    assert first["One"]["files"] == 1
    assert first["Two"]["files"] == 1
    assert first["Two"]["issue_count"] == 1
    assert sum("in Two:" in notice for notice in notices) == 1
    proposals = SelfImprovement(SimpleNamespace(code_monitor=monitor))._from_code_monitor()
    assert any(item.evidence["project"] == "Two" for item in proposals)
    assert monitor.scan_all()["Two"]["changed"] == 0
    assert sum("in Two:" in notice for notice in notices) == 1

    traceback = f'File "{two / "bad.py"}", line 1\nSyntaxError: invalid syntax'
    explanation = monitor.explain_error(traceback)
    assert explanation["project"] == "Two"
    assert explanation["path"] == "bad.py"
    assert explanation["context"]

    (two / "bad.py").write_text("def repaired():\n    pass\n", encoding="utf-8")
    repaired = monitor.scan_project("Two")
    assert repaired["issue_count"] == 0
    assert monitor.status()["projects"][-1]["issue_count"] == 0
    (two / "bad.py").unlink()
    assert monitor.scan_project("Two")["removed"] == 1


def test_code_monitor_and_project_registration_chat_routes(tmp_path):
    router = IntentRouter()
    assert router.route("code monitor status").control == "CODE_MONITOR_STATUS"
    assert router.route("scan all registered projects").control == "CODE_SCAN"
    assert router.route("explain code error: SyntaxError: bad code").entities["error_text"] == "SyntaxError: bad code"
    register = router.route(f"register project Demo at {tmp_path}")
    assert register.control == "PROJECT_REGISTER"
    assert register.entities["project"] == "Demo"


def test_local_file_handler_accepts_windows_paths_with_spaces():
    path = r"C:\Users\mohsin khan\Documents\notes.txt"
    assert local_file_request(f"read {path}") == {"file_operation": "read", "path": path}
    folder = r"C:\Users\mohsin khan\Documents"
    assert local_file_request(f'list files in "{folder}"') == {
        "file_operation": "list", "path": folder}


def test_file_download_plans_and_renders_only_saved_result(tmp_path):
    intent = IntentRouter().route("download https://github.com/example/data.txt")
    assert intent.is_action and intent.control != "MODEL_PULL"
    registry = ToolRegistry()
    registry.register("download_public_file", "download", {"type": "object"}, "medium",
                      "public_download", lambda **kwargs: kwargs)
    plan = Planner(registry).create_plan(intent.goal, intent=intent)
    assert len(plan) == 1
    assert plan[0].tool == "download_public_file"
    assert plan[0].expected["type"] == "download_complete"

    saved = tmp_path / "data.txt"
    saved.write_bytes(b"data")
    output = {"path": str(saved), "bytes": 4, "bytes_downloaded": 4,
              "sha256": "abcd", "complete": True}
    assert Verifier().check(plan[0].expected, ToolResult("download_public_file", True, output)).passed
    assert "Downloaded to" in render(output)
    saved.unlink()
    assert render(output) is None


def test_download_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    class Response:
        url = "https://github.com/example/data.txt"
        headers = {"content-length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self, _):
            yield b"new!"

    monkeypatch.setattr("app.downloads.manager.httpx.stream", lambda *_, **__: Response())
    scanner = SimpleNamespace(scan=lambda _path: {"clean": True, "status": "clean", "provider": "test"})
    storage = SimpleNamespace(ensure_capacity=lambda _bytes: None)
    manager = DownloadManager(storage, tmp_path / "downloads", scanner=scanner)
    original = manager.root / "data.txt"
    original.write_bytes(b"old!")
    result = manager.download("https://github.com/example/data.txt")
    assert original.read_bytes() == b"old!"
    assert Path(result["path"]).read_bytes() == b"new!"
    assert result["complete"] and result["bytes_downloaded"] == 4


def test_download_rejects_redirect_before_contacting_untrusted_host(tmp_path, monkeypatch):
    class Redirect:
        status_code = 302
        url = "https://github.com/example/data.txt"
        headers = {"location": "https://untrusted.invalid/payload"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    requested = []

    def stream(_method, url, **_kwargs):
        requested.append(url)
        return Redirect()

    monkeypatch.setattr("app.downloads.manager.httpx.stream", stream)
    scanner = SimpleNamespace(scan=lambda _path: {"clean": True})
    storage = SimpleNamespace(ensure_capacity=lambda _bytes: None)
    manager = DownloadManager(storage, tmp_path / "downloads", scanner=scanner)
    import pytest

    with pytest.raises(PermissionError, match="redirected outside"):
        manager.download("https://github.com/example/data.txt")
    assert requested == ["https://github.com/example/data.txt"]
