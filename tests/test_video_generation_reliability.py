"""Video readiness, measured delivery, and cancellation of owned local workers."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.emergency import ExecutionInterlock
from app.models import diffusion as d
from app.models import video_generation as v
from app.models.streaming import GenerationCancelled, answer_stream


def generator(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    image = models / "realvisxl.gguf"
    image.write_bytes(b"x" * 400)
    (models / "dreamshaper_8.safetensors").write_bytes(b"m" * 200)
    motion = tmp_path / "motion"
    motion.mkdir()
    (motion / "mm_sd_v15_v2.ckpt").write_bytes(b"v" * 100)
    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"engine")
    return d.ImageGenerator(engine_path=engine, model_path=image, motion_root=motion,
                            output_root=tmp_path / "out")


def test_video_readiness_requires_a_compatible_checkpoint(tmp_path):
    g = generator(tmp_path)
    (g.model_path.parent / "dreamshaper_8.safetensors").unlink()
    assert g.available
    assert not g.video_available()
    assert "SD 1.5" in g.video_status()["reason"]
    assert not g.generate_video("clouds").ok


def test_probe_missing_is_not_reported_ready(tmp_path, monkeypatch):
    g = generator(tmp_path)
    monkeypatch.setattr("app.video_workflow.executable", lambda _: None)
    assert not g.video_available()
    assert "FFprobe" in g.generate_video("clouds").detail


def test_memory_verdict_measures_sd15_plus_selected_motion(tmp_path, monkeypatch):
    g = generator(tmp_path)
    seen = []

    class Hardware:
        def can_run(self, size):
            seen.append(size)
            return {"runnable": True}

    monkeypatch.setattr("app.hardware.profile", lambda **_: Hardware())
    g.video_verdict(d.find_video_model(g.model_path.parent), d.find_motion_module(g.motion_root))
    assert seen == [300]  # selected SDXL image is deliberately excluded


def test_optional_motion_requires_explicit_selection_and_rejects_paths(tmp_path):
    g = generator(tmp_path)
    optional = g.motion_root / "optional"
    optional.mkdir()
    v3 = optional / "v3_sd15_mm.ckpt"
    v3.write_bytes(b"new")
    assert d.select_motion_module(g.motion_root).name == "mm_sd_v15_v2.ckpt"
    assert d.select_motion_module(g.motion_root, "v3") == v3
    assert "optional/v3_sd15_mm.ckpt" in g.video_status()["motion_modules"]
    for path in ("../outside.ckpt", str(v3.resolve()), "https://example.com/mm.ckpt"):
        with pytest.raises(ValueError, match="installed motion"):
            d.select_motion_module(g.motion_root, path)


def test_cancelled_owned_process_is_stopped_and_ticket_cannot_resume(monkeypatch):
    interlock = ExecutionInterlock()
    ticket = interlock.ticket()
    stopped = []

    class Process:
        pid = 123456
        args = ["owned-video-engine"]

        def communicate(self, timeout):
            interlock.halt()
            interlock.resume()
            raise subprocess.TimeoutExpired(self.args, timeout)

    monkeypatch.setattr("app.process.stop_owned_process", lambda process, pid: stopped.append((process, pid)))
    process = Process()
    with pytest.raises(PermissionError, match="emergency stop"):
        with v.video_generation_controls(check=lambda: interlock.check(ticket)):
            v.wait_video_process(process, 10)
    assert stopped == [(process, process.pid)]


def test_deadline_stops_only_the_recorded_worker(monkeypatch):
    stopped = []

    class Process:
        pid = 234567
        args = ["owned"]

        def communicate(self, timeout):
            raise subprocess.TimeoutExpired(self.args, timeout)

    process = Process()
    monkeypatch.setattr("app.process.stop_owned_process", lambda proc, pid: stopped.append((proc, pid)))
    with pytest.raises(subprocess.TimeoutExpired):
        v.wait_video_process(process, .01)
    assert stopped == [(process, process.pid)]


def test_a_queued_video_can_cancel_before_loading_any_model(monkeypatch):
    import app.resources

    event = threading.Event()
    calls = []

    @contextmanager
    def busy_lane(label, needs_bytes, wait):
        calls.append(wait)
        event.set()
        raise app.resources.NotEnoughMemory("another heavy job is running")
        yield  # pragma: no cover - contextmanager's generator contract

    monkeypatch.setattr(app.resources, "heavy", busy_lane)
    with answer_stream(lambda *_: None, event), pytest.raises(GenerationCancelled):
        with v.reserve_video_memory("clip", 1024):
            pytest.fail("cancelled queued clip entered the heavy lane")
    assert calls == [.2]


def test_stop_reply_really_terminates_an_owned_native_worker(monkeypatch):
    import app.process

    event = threading.Event()
    stopped = []
    actual_stop = app.process.stop_owned_process

    def stop(process, pid):
        stopped.append(process)
        return actual_stop(process, pid, graceful_timeout=1, kill_timeout=1)

    monkeypatch.setattr(app.process, "stop_owned_process", stop)
    timer = threading.Timer(.25, event.set)
    timer.start()
    try:
        with answer_stream(lambda *_: None, event), pytest.raises(GenerationCancelled):
            v.run_video_process([sys.executable, "-c", "import time; time.sleep(60)"], 15)
    finally:
        timer.cancel()
    assert len(stopped) == 1
    assert stopped[0].poll() is not None


@pytest.fixture
def real_clip(tmp_path):
    from app.video_workflow import executable

    ffmpeg = executable("ffmpeg")
    if not ffmpeg or not executable("ffprobe"):
        pytest.skip("local FFmpeg/FFprobe not installed")
    target = tmp_path / "fixture.avi"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=256x256:rate=8:duration=2", "-c:v", "mjpeg", str(target)],
                   check=True, capture_output=True, timeout=30)
    return target


def test_probe_counts_decoded_frames_and_rejects_header_only_junk(real_clip, tmp_path):
    metadata = v.probe_generated_video(real_clip)
    assert metadata["decoded_verified"]
    assert (metadata["width"], metadata["height"], metadata["fps"], metadata["frames"]) == (256, 256, 8, 16)
    junk = tmp_path / "junk.avi"
    junk.write_bytes(b"RIFF" + bytes(2048))
    assert d.validate_video(junk)[0]  # a container signature alone proves nothing
    with pytest.raises(ValueError, match="decoded"):
        v.probe_generated_video(junk)


def fixture_engine(monkeypatch, g, source):
    import app.resources

    actual_popen = subprocess.Popen
    captured = []

    class Engine:
        returncode = 0

        def communicate(self, timeout):
            return "seed 42", ""

    def popen(command, **kwargs):
        if str(command[0]) != str(g.engine_path):
            return actual_popen(command, **kwargs)
        captured.append(command)
        shutil.copyfile(source, command[command.index("-o") + 1])
        return Engine()

    @contextmanager
    def heavy(*_, **__):
        yield

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(app.resources, "heavy", heavy)
    monkeypatch.setattr(g, "video_verdict", lambda *_: {"runnable": True, "placement": "GPU"})
    return captured


def test_delivery_and_manifest_describe_decoded_output_not_image_model(real_clip, tmp_path, monkeypatch):
    g = generator(tmp_path)
    commands = fixture_engine(monkeypatch, g, real_clip)
    result = g.generate_video("a moving test pattern", quality="fast", width=256, height=256, seed=42)
    assert result.ok, result.detail
    measured = v.probe_generated_video(result.path)
    assert (result.width, result.height) == (measured["width"], measured["height"]) == (512, 512)
    assert result.video["fps"] == measured["fps"] == 24
    assert result.video["frames"] == measured["frames"]
    assert result.video["generation_fps"] == 8
    assert result.video["generation_width"] == 256
    row = json.loads((g.video_root / "manifest.jsonl").read_text())
    assert row["model"] == row["video"]["model"] == "dreamshaper_8.safetensors"
    assert row["fps"] == 24 and row["width"] == 512
    assert row["sha256"] == hashlib.sha256(result.path.read_bytes()).hexdigest()
    command = commands[0]
    assert Path(command[command.index("-o") + 1]).suffix == ".avi"
    assert command[command.index("--sampling-method") + 1] == "euler"
    assert command[command.index("--scheduler") + 1] == "discrete"
    assert command[command.index("--cfg-scale") + 1] == "8.0"


def test_invalid_engine_output_never_enters_history(tmp_path, monkeypatch):
    source = tmp_path / "bad.avi"
    source.write_bytes(b"RIFF" + bytes(2048))
    g = generator(tmp_path)
    fixture_engine(monkeypatch, g, source)
    result = g.generate_video("clouds", quality="fast")
    assert not result.ok
    assert not (g.video_root / "manifest.jsonl").exists()
    assert not list(g.video_root.glob("*.avi"))


def test_image_result_payload_is_unchanged_when_it_is_not_video():
    assert "video" not in d.ImageResult(ok=True).to_dict()


def test_nested_video_controls_preserve_the_initiating_ticket():
    interlock = ExecutionInterlock()
    ticket = interlock.ticket()
    with pytest.raises(PermissionError, match="emergency stop"):
        with v.video_generation_controls(check=lambda: interlock.check(ticket)):
            interlock.halt()
            interlock.resume()
            # A newly captured handler ticket cannot replace a revoked GUI ticket.
            newer = interlock.ticket()
            with v.video_generation_controls(check=lambda: interlock.check(newer)):
                pytest.fail("nested call replaced the original execution ticket")
    with v.video_generation_controls():
        v.check_video_cancelled()  # a rejected call does not poison later requests


def test_video_tool_exposes_explicit_selection_and_captures_the_interlock(tmp_path, monkeypatch):
    from app.safety.permissions import PermissionManager
    from app.tools.images import register_image_tools
    from app.tools.registry import ToolRegistry

    g = generator(tmp_path)
    registry = ToolRegistry(PermissionManager())
    register_image_tools(registry, g)
    tool = registry.get("generate_video")
    assert tool.permission == "filesystem_write_project"
    assert "motion_module" in tool.input_schema["properties"]
    seen = []

    def generate(prompt, **kwargs):
        seen.append(kwargs["motion_module"])
        registry.interlock.halt()
        registry.interlock.resume()
        v.check_video_cancelled()
        pytest.fail("revoked video call continued")

    monkeypatch.setattr(g, "generate_video", generate)
    with pytest.raises(PermissionError, match="emergency stop"):
        tool.handler("clouds", motion_module="v3")
    assert seen == ["v3"]
