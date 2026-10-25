"""Isolated sequential scene detection and timestamped local audio transcription."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

MAX_SECONDS = 1800
MAX_SCENES = 96
MAX_DECODED_FRAMES = 108000


def scenes(request):
    import cv2
    cv2.setNumThreads(1)
    capture = cv2.VideoCapture(request["path"])
    try:
        if not capture.isOpened():
            raise ValueError("No installed decoder could open this video")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width, height = (int(capture.get(key)) for key in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(fps) or fps <= 0 or count <= 0:
            raise ValueError("Video has no reliable frame rate or length")
        if width <= 0 or height <= 0 or width * height > 12_000_000:
            raise ValueError("Video dimensions exceed the decoder limit")
        if count > MAX_DECODED_FRAMES or count / fps > MAX_SECONDS:
            raise ValueError("Scene analysis supports clips up to 30 minutes and 108,000 frames")
        frames, previous, decoded = [], None, 0
        # A visual cut is not the only way a scene changes. Continuous shots
        # can reveal new objects or text, so keep a few timed views as well.
        sample_interval = max(3.0, min(30.0, count / fps / 48))
        last_frame = None
        while decoded < min(count, MAX_DECODED_FRAMES):
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[0] * frame.shape[1] > 12_000_000:
                raise ValueError("Decoded frame exceeds the pixel limit")
            small = cv2.resize(frame, (64, 36))
            difference = float(cv2.absdiff(previous, small).mean() / 255) if previous is not None else 1.0
            seconds = decoded / fps
            # Inspect every frame for cuts, and sample within long uncut shots.
            if (not frames or (difference >= .22 and seconds - frames[-1]["seconds"] >= .3)
                    or seconds - frames[-1]["seconds"] >= sample_interval):
                if len(frames) >= MAX_SCENES:
                    raise ValueError("Scene limit reached (96); split this clip into shorter videos")
                scale = min(1, 960 / max(frame.shape[:2]))
                preview = cv2.resize(frame, (max(1, round(frame.shape[1] * scale)), max(1, round(frame.shape[0] * scale))))
                name = f"scene-{len(frames):03}.jpg"
                if not cv2.imwrite(str(Path(request["output"]) / name), preview):
                    raise OSError("Could not save scene preview")
                frames.append({"file": name, "seconds": round(seconds, 3), "frame_index": decoded,
                               "visual_change": round(difference, 3)})
            previous = small
            last_frame = frame
            decoded += 1
        if not frames:
            raise ValueError("No video frames decoded")
        # Include the reveal at the end of a continuous clip when it is far
        # enough from the last keyframe to add useful visual evidence.
        tail_seconds = (decoded - 1) / fps
        if tail_seconds - frames[-1]["seconds"] >= 1.5:
            if len(frames) >= MAX_SCENES:
                raise ValueError("Scene limit reached (96); split this clip into shorter videos")
            scale = min(1, 960 / max(last_frame.shape[:2]))
            preview = cv2.resize(last_frame, (max(1, round(width * scale)), max(1, round(height * scale))))
            name = f"scene-{len(frames):03}.jpg"
            if not cv2.imwrite(str(Path(request["output"]) / name), preview):
                raise OSError("Could not save scene preview")
            frames.append({"file": name, "seconds": round(tail_seconds, 3), "frame_index": decoded - 1,
                           "visual_change": 0.0})
        for index, frame in enumerate(frames):
            frame["end_seconds"] = frames[index + 1]["seconds"] if index + 1 < len(frames) else decoded / fps
        return {"width": width, "height": height, "fps": fps, "frame_count": count,
                "duration_seconds": count / fps, "frames": frames, "decoded_frames": decoded,
                "coverage_complete": decoded == count,
                "coverage_note": "Decoded frames were scanned for visual cuts; descriptions use cut, timed and final keyframes."}
    finally:
        capture.release()


def transcribe(request):
    import av
    max_seconds = min(7200, max(1, int(request.get("max_seconds", MAX_SECONDS))))
    # An absent audio stream is different from a missing recognition model.
    with av.open(request["path"]) as container:
        if not container.streams.audio:
            return {"status": "no_audio", "segments": []}
        if container.duration and container.duration / av.time_base > max_seconds:
            raise ValueError(f"Audio transcription supports videos up to {max_seconds // 60} minutes")
    from app.voice.stt import FasterWhisperSTT
    from faster_whisper import WhisperModel
    provider = FasterWhisperSTT(request.get("stt", {}))
    model = WhisperModel(provider.model_name, device=provider.device, compute_type=provider.compute_type,
                         download_root=str(provider.download_root), local_files_only=True)
    segments, info = model.transcribe(request["path"], beam_size=provider.beam_size,
        language=None if provider.language == "auto" else provider.language, vad_filter=False)
    result, characters = [], 0
    for segment in segments:
        text = segment.text.strip()
        characters += len(text)
        if segment.end > max_seconds or len(result) >= 5000 or characters > 200000:
            raise ValueError("Audio transcript limit exceeded; split this clip")
        result.append({"start": round(segment.start, 3), "end": round(segment.end, 3), "text": text})
    return {"status": "transcribed", "language": info.language, "segments": result}


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read(64000))
        result = scenes(request) if request["operation"] == "scenes" else transcribe(request)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
