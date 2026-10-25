"""Video uses an SD 1.5 checkpoint and comes out smooth (2026-10-04).

Video used the image model; once that became RealVisXL (SDXL), every clip paired an SDXL checkpoint with an SD 1.5
AnimateDiff motion module. Clips also came out at 8 frames a second, which reads as stutter.
"""
from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager

import pytest

from app.models import diffusion as d


def _models(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(b"x" * 1024)
    return tmp_path


def test_video_animates_an_sd15_checkpoint_never_an_sdxl_one(tmp_path):
    root = _models(tmp_path, "realvisxl-v5.0-Q4_K.gguf", "dreamshaper_8.safetensors", "sd-v1-5-pruned-emaonly.safetensors")
    assert d.find_model(root).name == "realvisxl-v5.0-Q4_K.gguf"      # images keep the SDXL model
    assert d.find_video_model(root).name == "dreamshaper_8.safetensors"
    only_xl = tmp_path / "xl"
    only_xl.mkdir()
    (only_xl / "juggernautxl.safetensors").write_bytes(b"x")
    assert d.find_video_model(only_xl) is None


def test_the_video_command_uses_the_sd15_checkpoint(tmp_path, monkeypatch):
    import app.resources as resources

    root = _models(tmp_path, "realvisxl-v5.0-Q4_K.gguf", "dreamshaper_8.safetensors")
    motion = tmp_path / "motion"
    motion.mkdir()
    (motion / "mm_sd_v15_v2.ckpt").write_bytes(b"x")
    engine = tmp_path / "sd-cli.exe"
    engine.write_bytes(b"x")
    generator = d.ImageGenerator(engine_path=engine, model_path=root / "realvisxl-v5.0-Q4_K.gguf",
                                 output_root=tmp_path / "out")
    generator.motion_root = motion
    generator.video_verdict = lambda *_: {"runnable": True, "placement": "GPU"}
    captured = {}

    class Popen:
        returncode = 1

        def __init__(self, command, **_kwargs):
            captured["command"] = command

        def communicate(self, timeout=None):
            return ("", "")

    @contextmanager
    def heavy(*_args, **_kwargs):
        yield

    monkeypatch.setattr(subprocess, "Popen", Popen)
    monkeypatch.setattr(resources, "heavy", heavy)
    generator.generate_video("a koi fish", quality="fast")
    command = captured["command"]
    assert command[command.index("-m") + 1].endswith("dreamshaper_8.safetensors")


def test_a_clip_is_smoothed_to_24_fps_and_small_clips_are_enlarged(tmp_path):
    from app.video_workflow import executable

    ffmpeg, ffprobe = executable("ffmpeg"), executable("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg is not available")
    clip = tmp_path / "clip.webm"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=256x256:rate=8:duration=1",
                    "-c:v", "libvpx-vp9", str(clip)], check=True, timeout=120)
    smoothed = d.smooth_video(clip, width=256)
    assert smoothed is not None and smoothed.suffix == ".mp4" and clip.is_file()
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,r_frame_rate", "-of", "json", str(smoothed)],
                                      capture_output=True, text=True, timeout=60).stdout)["streams"][0]
    assert probe["width"] == 512 and probe["r_frame_rate"] == "24/1"
    assert d.validate_video(smoothed)[0]


def test_a_better_motion_module_is_preferred_when_present(tmp_path):
    root = tmp_path / "motion"
    root.mkdir()
    (root / "mm_sd_v15_v2.ckpt").write_bytes(b"x" * 2048)
    (root / "mm_sd_v14.ckpt").write_bytes(b"x" * 4096)  # larger, but older
    assert d.find_motion_module(root).name == "mm_sd_v15_v2.ckpt"  # v2 beats v1 even though smaller
    (root / "v3_sd15_mm.ckpt").write_bytes(b"x" * 1024)  # newest, smallest
    assert d.find_motion_module(root).name == "v3_sd15_mm.ckpt"
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "unknown_a.ckpt").write_bytes(b"x" * 10)
    (plain / "unknown_b.ckpt").write_bytes(b"x" * 99)
    assert d.find_motion_module(plain).name == "unknown_b.ckpt"  # no known name: largest wins
    assert d.find_motion_module(tmp_path / "missing") is None


def test_the_deep_tier_builds_a_30fps_upscaled_sharpened_finish(tmp_path, monkeypatch):
    captured = {}
    def fake_run(command, *args, **kwargs):
        captured["command"] = command
        from pathlib import Path as _P
        _P(command[-1]).write_bytes(b"x" * 2048)  # pretend ffmpeg wrote the output
        import types
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(d, "validate_video", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr("app.video_workflow.executable", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(d, "run_video_process", fake_run)
    clip = tmp_path / "clip.webm"
    clip.write_bytes(b"x" * 1024)
    out = d.smooth_video(clip, width=512, fps=30, target_width=768, sharpen=True)
    assert out is not None
    filters = captured["command"][captured["command"].index("-vf") + 1]
    assert "scale=768" in filters and "fps=30" in filters and "unsharp=" in filters
    # The default (balanced) finish stays 24 fps, 512, no sharpen.
    d.smooth_video(clip, width=256)
    base = captured["command"][captured["command"].index("-vf") + 1]
    assert "fps=24" in base and "scale=512" in base and "unsharp" not in base
