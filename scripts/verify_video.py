"""Build a known local video/audio fixture and exercise the real admitted pipeline."""
import json
import wave


def main():
    import av
    import numpy as np
    from app.config import PROJECT_ROOT, load_settings
    from app.safety.permissions import PermissionManager
    from app.security.scanner import DefenderFileScanner
    from app.video import VideoAnalyzer
    from app.tools.registry import ToolRegistry
    from app.video import register_video_tools

    source = PROJECT_ROOT / ".test-runs/speech-smoke.wav"
    if not source.is_file():
        raise ValueError("Run python -m scripts.verify_speech first")
    with wave.open(str(source), "rb") as audio:
        rate = audio.getframerate()
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").reshape(1, -1)
    target = PROJECT_ROOT / ".test-runs/video-smoke.avi"
    count = int(np.ceil(samples.shape[1] / rate * 10))
    with av.open(str(target), "w") as container:
        video = container.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 160, 120, "yuv420p"
        sound = container.add_stream("pcm_s16le", rate=rate)
        sound.layout = "mono"
        for i in range(count):
            pixels = np.full((120, 160, 3), 0 if i < count // 2 else 255, dtype=np.uint8)
            for packet in video.encode(av.VideoFrame.from_ndarray(pixels, format="rgb24")):
                container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = rate
        for packet in sound.encode(frame):
            container.mux(packet)
        for packet in sound.encode():
            container.mux(packet)
    permissions = PermissionManager()
    analyzer = VideoAnalyzer(permissions, DefenderFileScanner(), PROJECT_ROOT / ".test-runs/video-smoke-cache",
                             stt_config={**load_settings().get("voice", {}).get("stt", {}), "model": "tiny.en"})
    registry = ToolRegistry(permissions)
    register_video_tools(registry, analyzer)
    result = registry.execute("video_analyze", {"path": str(target), "scenes": True, "transcribe_audio": True})
    if not result.success:
        print(json.dumps({"status": "failed", "error": result.error}, indent=2))
        return 1
    data = result.output
    transcript = " ".join(segment["text"] for segment in data["audio"]["segments"])
    passed = data["metadata"]["coverage_complete"] and len(data["frames"]) == 2 and "speech" in transcript.lower()
    report = {"status": "passed" if passed else "failed", "audio_status": data["audio_status"],
              "transcript": transcript, "scene_count": len(data["frames"]),
              "decoded_frames": data["metadata"]["decoded_frames"], "fixture_frames": count,
              "audio_error": data["audio"].get("error")}
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
