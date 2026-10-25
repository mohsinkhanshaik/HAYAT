from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from app.runtime import build_runtime


def main() -> int:
    runtime = build_runtime(use_model=False)
    captured = {}
    try:
        voice = runtime.voice
        if voice is None:
            raise RuntimeError("Voice runtime is unavailable.")

        def listen() -> None:
            captured["audio"] = voice.recorder.record_until_silence(voice.cancellation.shutdown, speech_start_timeout=6.0, silence_seconds=1.0)

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        time.sleep(1.25)
        requested = "Create a text file called voice dash test dot T X T containing hello from my AI."
        voice.tts.speak(requested, wait=True)
        listener.join(timeout=10)
        audio = captured.get("audio")
        if audio is None:
            print(json.dumps({"passed": False, "error": "Microphone capture did not finish."}, indent=2))
            return 1
        transcription = voice.stt.transcribe(audio)
        response = voice.process_transcript(transcription.text)
        report = Path("voice-test.txt").resolve()
        content = report.read_text(encoding="utf-8") if report.exists() else ""
        peak = float(np.max(np.abs(audio.samples))) if len(audio.samples) else 0.0
        result = {
            "microphone_captured": len(audio.samples) > 0,
            "duration_seconds": round(audio.duration_seconds, 3),
            "peak": round(peak, 6),
            "transcription": transcription.text,
            "stt_duration_ms": transcription.duration_ms,
            "task_id": response.task_id,
            "task_complete": response.complete,
            "spoken_response": response.spoken_response,
            "written_paths": response.metadata.get("written_paths", []),
            "file_exists": report.exists(),
            "file_content": content,
            "passed": response.complete is True and content == "hello from my ai" and str(report) in response.metadata.get("written_paths", []),
            "note": "This is a real speaker-to-air-to-microphone voice-command loop, not direct audio injection.",
            "verified_at": datetime.now(UTC).isoformat(),
        }
        destination = Path("data/benchmarks/voice/hardware.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
        # The verified content is recorded in the benchmark artifact; the
        # disposable command target must not accumulate in the project root.
        report.unlink(missing_ok=True)
        voice.tts.speak(response.spoken_response, wait=True)
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
