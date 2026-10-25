from __future__ import annotations

import argparse
import json

from app.runtime import build_runtime
from app.voice.models import VoiceState


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the configured microphone and local speech-to-text provider.")
    parser.add_argument("--probe", action="store_true", help="Verify microphone capture without requiring speech or loading the STT model")
    args = parser.parse_args()
    runtime = build_runtime(use_model=False)
    try:
        voice = runtime.voice
        if voice is None:
            raise RuntimeError("Voice runtime is unavailable.")
        if args.probe:
            result = voice.probe_microphone(0.75)
            print(json.dumps(result, indent=2))
            return 0 if result.get("captured") else 1
        print('Say "Hello AI" after the listening message. Recording stops after a short silence.')
        voice.set_state(VoiceState.LISTENING)
        vad = voice.config.get("vad", {})
        audio = voice.recorder.record_until_silence(
            voice.cancellation.shutdown,
            float(vad.get("speech_start_timeout", 5)),
            float(vad.get("silence_seconds", 0.8)),
        )
        transcription = voice.transcribe(audio)
        print(json.dumps({"duration_seconds": audio.duration_seconds, "transcription": transcription.text, "language": transcription.language, "stt_duration_ms": transcription.duration_ms}, indent=2))
        return 0 if transcription.text else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
