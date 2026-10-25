"""Local screen lifecycle, actual Replica checkers and bounded person inference."""
import json
import shutil
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app.models.base import ModelResponse
from app.perception.live_screen import LiveScreenReader
from app.replica import BUNDLE, STAGES, ReplicaWorkspace
from app.safety.permissions import PermissionManager


def wait_for(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


def test_live_reader_deduplicates_changes_then_clears_on_stop():
    text = ["first"]
    reader = LiveScreenReader(lambda **_: {"window": "fixture", "text": text[0]})
    try:
        reader.start(interval=.5, duration=4)
        wait_for(lambda: reader.status()["samples"] >= 2)
        assert reader.status()["changes"] == 1
        text[0] = "second"
        wait_for(lambda: reader.status()["changes"] == 2)
        state = reader.status()
        assert state["latest"]["text"] == "second"
        assert not state["cloud"] and not state["input_sent"]
        with pytest.raises(RuntimeError, match="already running"):
            reader.start()
    finally:
        assert reader.stop()
    assert reader.status()["latest"] is None


def test_privacy_revocation_immediately_discards_latest():
    allowed = [True]
    reader = LiveScreenReader(lambda **_: {"text": "private"}, lambda: allowed[0])
    reader.read()
    allowed[0] = False
    assert reader.status()["latest"] is None
    with pytest.raises(PermissionError):
        reader.read()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows COM apartment check")
def test_existing_com_apartment_is_respected_and_not_uninitialized(monkeypatch):
    import comtypes
    calls = []
    error = OSError("Existing apartment")
    error.winerror = -2147417850
    def existing(_flags):
        raise error
    monkeypatch.setattr(comtypes, "CoInitializeEx", existing)
    monkeypatch.setattr(comtypes, "CoUninitialize", lambda: calls.append("uninitialized"))
    assert LiveScreenReader(lambda **_: {"text": "existing apartment"}).read()["text"] == "existing apartment"
    assert not calls


def test_stop_discards_a_read_already_in_progress():
    entered, finish = threading.Event(), threading.Event()
    def read(**_):
        entered.set()
        assert finish.wait(4)
        return {"text": "late capture"}
    reader = LiveScreenReader(read)
    errors = []
    def work():
        try:
            reader.read()
        except PermissionError as exc:
            errors.append(exc)
    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(4)
    reader.stop(timeout=0)
    finish.set()
    worker.join(4)
    assert errors and reader.status()["latest"] is None


def test_session_expires_and_errors_do_not_reuse_old_text():
    reader = LiveScreenReader(lambda **_: {"text": "short session"})
    reader.start(interval=.5, duration=1)
    wait_for(lambda: not reader.status()["running"])
    assert reader.status()["samples"] >= 1 and reader.status()["latest"] is None
    reader.read()
    def unavailable(**_):
        raise LookupError("window closed")
    reader.reader = unavailable
    with pytest.raises(LookupError):
        reader.read()
    assert reader.status()["latest"] is None


@pytest.mark.parametrize("args", [{"interval": 0}, {"interval": float("nan")}, {"duration": 0}, {"duration": 3601}, {"method": "cloud"}])
def test_screen_session_bounds(args):
    with pytest.raises(ValueError):
        LiveScreenReader().start(**args)


@pytest.fixture
def replica(tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "allowed_roots": [str(tmp_path)]}, project_root=tmp_path)
    return ReplicaWorkspace(permissions)


def test_all_workflows_prepare_without_claude_and_check_prerequisites(replica, tmp_path):
    assert len(replica.catalog()["stages"]) == 11
    for stage in STAGES:
        result = replica.prepare(stage, str(tmp_path))
        assert not result["claude_required"]
        assert "HAYAT" in result["instructions"]
        assert "Claude" not in result["instructions"]
        assert ".claude" not in result["instructions"]
    assert replica.prepare("build", str(tmp_path))["missing"]
    assert (tmp_path / "replica/templates/design/tokens.json").is_file()
    with pytest.raises(RuntimeError, match="Configure HAYAT"):
        replica.run("recon", str(tmp_path), "A task list")


class Model:
    def __init__(self, files, **kwargs):
        self.files, self.kwargs, self.calls = files, kwargs, []
    def generate(self, messages, **kwargs):
        self.calls.append(messages)
        return ModelResponse(json.dumps({"files": self.files}), "fixture", "local", **self.kwargs)


@pytest.mark.parametrize('goal', ['', '   ', 'a' * 8001])
def test_invalid_replica_goal_does_not_prepare_files_or_call_the_model(replica, tmp_path, goal):
    replica.model = Model([])
    project = tmp_path / 'uncreated-project'
    with pytest.raises(ValueError, match='workflow goal'):
        replica.run('recon', str(project), goal)
    assert not project.exists() and not replica.model.calls


def test_generated_artifacts_are_validated_backed_up_and_audited(replica, tmp_path):
    replica.model = Model([{"path": "replica/recon.md", "content": "Observed: no live reference supplied."}])
    first = replica.run("recon", str(tmp_path), "Plan a local task list")
    assert first["files"][0]["backup"] is None
    assert not first["code_executed"] and not first["published"] and not first["tests_run"]
    assert json.loads(open(first["manifest"], encoding="utf-8").read())["status"] == first["status"]
    second = replica.run("recon", str(tmp_path), "Plan a local task list")
    assert second["files"][0]["backup"]


@pytest.mark.parametrize("bad", ["../outside.md", ".git/config.txt", ".env.local", "node_modules/code.js"])
def test_model_cannot_write_outside_project_or_into_credentials(replica, tmp_path, bad):
    replica.model = Model([{"path": "replica/recon.md", "content": "valid"}, {"path": bad, "content": "bad"}])
    with pytest.raises(PermissionError):
        replica.run("recon", str(tmp_path), "Plan")
    assert not (tmp_path / "replica/recon.md").exists()


def test_invalid_or_truncated_generation_writes_no_artifacts(replica, tmp_path):
    replica.model = Model([{"path": "replica/recon.md", "content": "ok"}], finish_reason="length")
    with pytest.raises(ValueError, match="output limit"):
        replica.run("recon", str(tmp_path), "Plan")
    replica.model = Model([{"path": "replica/recon.md", "content": "ok"}, {"path": "broken.py", "content": "def x("}])
    with pytest.raises(SyntaxError):
        replica.run("recon", str(tmp_path), "Plan")
    assert not (tmp_path / "replica/recon.md").exists()


def test_partial_filesystem_failure_records_written_files_and_manifest(replica, tmp_path):
    (tmp_path / "blocked.py").mkdir()
    replica.model = Model([{"path": "replica/recon.md", "content": "written"}, {"path": "blocked.py", "content": "print(1)"}])
    with pytest.raises(RuntimeError, match="Review .*runs"):
        replica.run("recon", str(tmp_path), "Plan")
    reports = list((tmp_path / "replica/runs").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "partially written" and len(report["files"]) == 1


def test_explicit_html_ids_are_repaired_before_any_artifact_write(replica, tmp_path):
    class RepairModel:
        calls = 0
        def generate(self, messages, **kwargs):
            self.calls += 1
            assert not (tmp_path / "replica/recon.md").exists()
            html = '<output id="count-display">0</output>' if self.calls == 1 else '<output id="count">0</output>'
            return ModelResponse(json.dumps({"files": [{"path": "replica/recon.md", "content": "Generated; untested"},
                                                     {"path": "index.html", "content": html}], "notes": ""}), "fixture", "local")
    replica.model = RepairModel()
    result = replica.run("recon", str(tmp_path), "Include output id=count")
    assert result["generation_attempts"] == 2
    assert 'id="count"' in (tmp_path / "index.html").read_text(encoding="utf-8")


def test_reference_fetch_obeys_privacy(replica, tmp_path):
    replica.model = Model([])
    replica.network_allowed = lambda: False
    with pytest.raises(PermissionError, match="privacy"):
        replica.run("recon", str(tmp_path), "Plan", "https://example.com")
    assert not replica.model.calls


def test_pinned_resources_reject_tampering(replica, monkeypatch, tmp_path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "replica-design").mkdir()
    (folder / "replica-design/tokens.json").write_text("{}")
    monkeypatch.setattr("app.replica.BUNDLE", folder)
    with pytest.raises(ValueError, match="changed"):
        replica.resource("replica-design/tokens.json")


def test_actual_six_checkers_and_failure_gates(replica, tmp_path):
    (tmp_path / "features.csv").write_text("feature,area,priority,original,clone,notes\nSave,tasks,must,yes,done,fixture\n")
    parity = replica.check("parity", str(tmp_path), "features.csv")
    assert parity["passed"] and parity["result"]["overall"] == 100
    (tmp_path / "features.csv").write_text("feature,area,priority,original,clone,notes\nSave,tasks,must,yes,no,fixture\n")
    assert not replica.check("parity", str(tmp_path), "features.csv")["passed"]
    shutil.copy(BUNDLE / "replica-design/tokens.json", tmp_path / "tokens.json")
    assert replica.check("contrast", str(tmp_path), "tokens.json")["passed"]
    (tmp_path / "tokens.json").write_text(json.dumps({"color": {"text": "#fff", "bg": "#fff"}, "pairs": [["text", "bg"]]}))
    assert not replica.check("contrast", str(tmp_path), "tokens.json")["passed"]
    for name in ("a.png", "b.png"):
        Image.new("RGB", (48, 48), "black").save(tmp_path / name)
    compared = replica.check("image_diff", str(tmp_path), "a.png", "b.png")
    assert compared["passed"] and compared["result"]["score"] == 100
    assert compared["result"]["files"]["clone"] == str(tmp_path / "b.png")
    Image.new("RGB", (48, 48), "white").save(tmp_path / "b.png")
    assert not replica.check("image_diff", str(tmp_path), "a.png", "b.png", image_mode="pixel")["passed"]
    (tmp_path / "reviews.csv").write_text("source,url,date,rating,text\nfixture,https://example.com/review,2026-10-01,1,Too expensive\n")
    assert replica.check("reviews", str(tmp_path), "reviews.csv")["passed"]
    (tmp_path / "listing.json").write_text(json.dumps({"google_play": {"title": "Task List", "short_description": "Local tasks", "full_description": "Keep a list of tasks on your device."}}))
    assert replica.check("listing", str(tmp_path), "listing.json")["passed"]
    assert replica.check("brand", str(tmp_path), avoid="ForbiddenOriginal")["passed"]
    (tmp_path / "app.py").write_text('title = "ForbiddenOriginal"')
    assert not replica.check("brand", str(tmp_path), avoid="ForbiddenOriginal")["passed"]


def test_brand_checker_excludes_credentials_and_dependency_trees(replica, tmp_path):
    (tmp_path / ".env.local").write_text("SECRET=ForbiddenOriginal")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/app.js").write_text("ForbiddenOriginal")
    (tmp_path / "app.js").write_text("Original implementation")
    result = replica.check("brand", str(tmp_path), avoid="ForbiddenOriginal")
    assert result["passed"] and result["excluded_files"] == 1


def test_person_filter_bounds_lazy_loading_and_no_identity():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    from app.perception.people import PersonDetector
    loads = []
    def load():
        loads.append(1)
        return lambda _: [{"labels": torch.tensor([1, 2, 1, 1]), "scores": torch.tensor([.9, .99, .1, .8]),
                           "boxes": torch.tensor([[-3, 2, 60, 50], [0, 0, 4, 4], [1, 2, 3, 4], [4, 4, 2, 2]])}]
    detector = PersonDetector(loader=load)
    result = detector.frame(Image.new("RGB", (40, 30)))
    assert result["count"] == 1 and result["people"][0]["box"] == [0, 2, 40, 30]
    detector.frame(Image.new("RGB", (40, 30)))
    assert len(loads) == 1 and not result["cloud"]
    assert set(result["people"][0]) == {"box", "score"}
    with pytest.raises(ValueError):
        detector.frame(Image.new("RGB", (10, 10)), confidence=float("nan"))


def test_camera_frame_survives_until_detection_and_is_always_removed(tmp_path, monkeypatch):
    from app.perception.people import register_person_tools
    from app.tools.registry import ToolRegistry
    permissions = PermissionManager({"default": "ALLOW", "allowed_roots": [str(tmp_path)]}, project_root=tmp_path)
    registry = ToolRegistry(permissions)
    class Camera:
        capture_backend = SimpleNamespace(active=False)
        def on(self):
            self.capture_backend.active = True
        def off(self):
            self.capture_backend.active = False
            (tmp_path / "frame.png").unlink(missing_ok=True)
        def capture(self, retain):
            assert self.capture_backend.active and not retain
            Image.new("RGB", (10, 10)).save(tmp_path / "frame.png")
            return {"path": str(tmp_path / "frame.png")}
    camera = Camera()
    detector = register_person_tools(registry, camera)
    def fail(path, _confidence):
        assert path.exists()
        raise RuntimeError("inference failed")
    monkeypatch.setattr(detector, "image", fail)
    result = registry.execute("camera_people", {})
    assert not result.success and "inference failed" in result.error
    assert not camera.capture_backend.active and not (tmp_path / "frame.png").exists()


def test_password_controls_are_excluded_from_accessibility_reads():
    from tests.test_ui_automation import Control, Window, automation
    password = Control("Password", "Edit", value="do not expose")
    password.element_info.is_password = True
    normal = Control("Details", "Edit", value="normal visible content")
    result = automation(Window("Fixture", [password, normal])).read("Fixture")
    assert result["text"] == "normal visible content"


def test_auto_uses_ocr_when_accessibility_exposes_only_window_buttons(monkeypatch):
    from pathlib import Path
    from app.perception.live_screen import desktop_text
    monkeypatch.setattr("app.perception.live_screen._accessible_text", lambda *_a, **_k:
        {"text": "System\nMinimize\nMaximize\nClose", "content_lines": 0})
    monkeypatch.setattr("app.perception.live_screen._capture_window", lambda _: (123, "Fixture"))
    monkeypatch.setattr("PIL.ImageGrab.grab", lambda **_: Image.new("RGB", (100, 60)))
    captures = []
    def ocr(_self, path):
        assert path.is_file()
        captures.append(Path(path))
        return {"available": True, "text": "actual page content", "provider": "fixture"}
    monkeypatch.setattr("app.perception.camera.ocr.LocalOCR.read", ocr)
    assert desktop_text("Fixture")["text"] == "actual page content"
    assert captures and not captures[0].exists()


@pytest.mark.parametrize("command,tool", [("start live screen reading", "screen_read_start"), ("stop screen reading", "screen_read_stop"),
    ("read my screen", "screen_read"), ("detect humans in camera", "camera_people"), ("replica workflows", "replica_catalog"),
    (r"prepare replica recon in D:\AI\data\work\demo", "replica_prepare"),
    (r"run replica recon in D:\AI\data\work\demo: Plan a task list", "replica_run")])
def test_chat_routes_use_real_tools(command, tool):
    from app.workspace_actions import request, plan
    found = request(command)
    assert found[0] == tool
    assert plan(command, {tool})["tool"] == tool
    assert plan(command, set()) is None


def test_runtime_registers_new_tools_and_closes_screen_reader():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        names = set(runtime.registry.names())
        assert {"screen_read", "screen_read_start", "screen_read_stop", "screen_read_status", "detect_people", "camera_people", "people_status", "replica_catalog", "replica_prepare", "replica_run", "replica_check"} <= names
        assert runtime.registry.execute("replica_catalog", {}).success
    finally:
        runtime.close()
    assert not runtime.screen_reader.status()["running"]


def test_new_chat_requests_execute_and_report_real_registered_handlers():
    from app.runtime import build_runtime
    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        runtime.screen_reader.reader = lambda **_: {"window": "Local fixture", "text": "Verification content 1846"}
        answer = runtime.assistant.handle("read my screen")
        assert "Verification content 1846" in answer.display_response
        assert "recon" in runtime.assistant.handle("replica workflows").display_response
        assert "stopped" in runtime.assistant.handle("stop screen reading").display_response.lower()
        assert runtime.screen_reader.status()["latest"] is None
    finally:
        runtime.close()
