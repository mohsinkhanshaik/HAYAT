import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.attachments import AttachmentReader
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from app.video import VideoAnalyzer, register_video_tools, sample_positions


@pytest.fixture
def clip(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    target = tmp_path / "sample.avi"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    if not writer.isOpened():
        pytest.skip("MJPG encoder unavailable")
    try:
        for value in range(20):
            writer.write(np.full((48, 64, 3), value * 10, dtype=np.uint8))
    finally:
        writer.release()
    return target


def analyzer(tmp_path, clean=True):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    scanner = SimpleNamespace(scan=Mock(return_value={"clean": clean}))
    return VideoAnalyzer(permissions, scanner, tmp_path / "cache")


def test_positions_are_bounded_and_timestamp_queries_stay_in_clip():
    assert sample_positions(1000000, 30, 100)[-1] == 999999
    assert len(sample_positions(1000000, 30, 100)) == 8
    assert sample_positions(1, 30, 8) == [0]
    assert 1510 in sample_positions(3000, 10, 3, around_seconds=151)
    with pytest.raises(ValueError):
        sample_positions(10, 0, 4)
    with pytest.raises(ValueError):
        sample_positions(10, 30, 4, around_seconds=99)


def test_real_video_decode_is_bounded_cached_and_truthful(clip, tmp_path):
    reader = analyzer(tmp_path)
    result = reader.analyze(str(clip), samples=3)
    assert result["metadata"]["width"] == 64
    assert result["metadata"]["duration_seconds"] == pytest.approx(2)
    assert len(result["frames"]) == 3
    assert all(Path(frame["path"]).is_file() for frame in result["frames"])
    assert result["frames"][-1]["seconds"] == pytest.approx(1.9, abs=.15)
    assert result["audio_status"] == "not_transcribed"
    assert "unavailable" in result["text"]
    assert not result["cache_hit"]
    assert reader.analyze(str(clip), samples=3)["cache_hit"]
    assert reader.scanner.scan.call_count == 2, "cache hits still require file admission"


def test_video_intake_uses_gateway_and_existing_deny_policy(clip, tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {"filesystem_read": "DENY"}}, tmp_path)
    registry = ToolRegistry(permissions)
    engine = analyzer(tmp_path)
    register_video_tools(registry, engine)
    attachment = AttachmentReader(SimpleNamespace(registry=registry)).read(clip)
    assert attachment.kind == "video" and not attachment.ok
    engine.scanner.scan.assert_not_called()


def test_scan_failure_prevents_decoder_start(clip, tmp_path, monkeypatch):
    run = Mock()
    monkeypatch.setattr("app.video.subprocess.run", run)
    with pytest.raises(ValueError, match="antimalware"):
        analyzer(tmp_path, clean=False).analyze(str(clip))
    run.assert_not_called()


def test_decoder_timeout_is_reported_and_lane_is_released(clip, tmp_path, monkeypatch):
    import subprocess
    engine = analyzer(tmp_path)
    monkeypatch.setattr("app.video.subprocess.run", Mock(side_effect=subprocess.TimeoutExpired("decoder", 30)))
    for _ in range(2):
        with pytest.raises(ValueError, match="timed out"):
            engine.analyze(str(clip))


def test_corrupted_media_is_a_clear_failure(tmp_path):
    target = tmp_path / "corrupt.mp4"
    target.write_bytes(b"this is not a video")
    with pytest.raises(ValueError, match="decoder"):
        analyzer(tmp_path).analyze(str(target))


def test_video_attachment_delivers_timestamped_context(clip, tmp_path):
    engine = analyzer(tmp_path)
    registry = ToolRegistry(engine.permissions)
    register_video_tools(registry, engine)
    attachment = AttachmentReader(SimpleNamespace(registry=registry)).read(clip)
    assert attachment.ok and attachment.kind == "video"
    assert "[00:00:00]" in attachment.text
    assert attachment.media["trust"] == "untrusted_file_evidence"


@pytest.mark.parametrize("damage", ["missing_timestamp", "nonfinite_timestamp", "string_fps"])
def test_damaged_video_cache_is_redecoded(clip, tmp_path, damage):
    engine = analyzer(tmp_path)
    first = engine.analyze(str(clip), samples=1)
    manifest = Path(first["frames"][0]["path"]).parent / "frames.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if damage == "missing_timestamp":
        del data["frames"][0]["seconds"]
    elif damage == "nonfinite_timestamp":
        data["frames"][0]["seconds"] = float("inf")
    else:
        data["fps"] = "10"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    result = engine.analyze(str(clip), samples=1)

    assert not result["cache_hit"]
    assert result["frames"][0]["seconds"] == pytest.approx(0)
    assert result["metadata"]["fps"] == pytest.approx(10)
    assert engine.analyze(str(clip), samples=1)["cache_hit"]


def test_sequential_scene_analysis_checks_every_frame_and_detects_hard_cut(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    target = tmp_path / "cut.avi"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    assert writer.isOpened()
    for i in range(20):
        writer.write(np.full((48, 64, 3), 0 if i < 10 else 255, dtype=np.uint8))
    writer.release()
    result = analyzer(tmp_path).analyze(str(target), scenes=True)
    assert result["metadata"]["decoded_frames"] == 20
    assert result["metadata"]["coverage_complete"]
    assert len(result["frames"]) == 2
    assert result["frames"][1]["seconds"] == pytest.approx(1)
    assert result["sampling"] == "sequential_scene_detection"


def test_continuous_video_keeps_timed_views_and_final_reveal(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    target = tmp_path / "reveal.avi"
    writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    assert writer.isOpened()
    for frame in range(121):
        picture = np.full((48, 64, 3), 20 + frame // 2, dtype=np.uint8)
        picture[15:33, 18:46] = 55 + frame
        writer.write(picture)
    writer.release()

    result = analyzer(tmp_path).analyze(str(target), scenes=True)
    seconds = [frame["seconds"] for frame in result["frames"]]
    assert seconds[0] == 0
    assert len(seconds) >= 5
    assert seconds[-1] == pytest.approx(12, abs=.1)
    assert "Frame scan complete: True" in result["text"]
    assert "Coverage complete" not in result["text"]


def test_audio_absence_is_reported_without_claiming_transcription(clip, tmp_path):
    pytest.importorskip("av")
    result = analyzer(tmp_path).analyze(str(clip), transcribe_audio=True)
    assert result["audio_status"] == "no_audio"
    assert result["audio"]["segments"] == []


def test_scene_and_timestamp_modes_cannot_be_combined(clip, tmp_path):
    with pytest.raises(ValueError, match="not both"):
        analyzer(tmp_path).analyze(str(clip), scenes=True, around_seconds=1)
