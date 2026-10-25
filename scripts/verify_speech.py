"""Offline speech-model smoke test with generated audio; no microphone or speaker."""
import json
import os
import wave
from types import SimpleNamespace


def main():
    os.environ["HF_HUB_OFFLINE"] = "1"
    import numpy as np
    from app.config import load_settings, PROJECT_ROOT
    from app.voice.models import AudioData
    from app.voice.stt import create_stt_provider
    from app.voice.tts import create_tts_provider
    config = load_settings().get("voice", {})
    stt = create_stt_provider({**config.get("stt", {}), "model": "tiny.en", "worker_timeout_seconds": 90})
    tts = create_tts_provider(config.get("tts", {}), SimpleNamespace(resolve=lambda *args: None))
    target = PROJECT_ROOT / ".test-runs/speech-smoke.wav"
    try:
        tts.synthesize_to_file("This is a local speech recognition test. The system is ready.", target)
        with wave.open(str(target), "rb") as source:
            rate = source.getframerate()
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768
        # Resample the generated mono waveform to the recognizer's PCM contract.
        positions = np.arange(round(len(samples) * 16000 / rate)) * rate / 16000
        samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        result = stt.transcribe(AudioData(samples, 16000))
        report = {"synthesis": "passed", "transcription": result.text,
                  "recognition": "passed" if "speech" in result.text.lower() and "ready" in result.text.lower() else "failed",
                  "microphone_used": False, "speaker_used": False}
        print(json.dumps(report, indent=2))
        return 0 if report["recognition"] == "passed" else 1
    finally:
        stt.close()
        if hasattr(tts, "close"):
            tts.close()


if __name__ == "__main__":
    raise SystemExit(main())
