"""Download what the Arabic course needs to speak and to listen. Free, one time.

    .\\.venv\\Scripts\\python.exe -m scripts.setup_arabic

* The Arabic voice: Piper's open ar_JO-kareem-medium (63 MB) into data/voices.
  Without it the course still teaches reading and writing, but cannot say
  anything aloud, so there are no listening exercises.
* The speech model: Faster Whisper "small" (about 480 MB) into
  data/models/whisper. The default English model (base.en) cannot hear Arabic;
  this one grades what you say in speaking exercises.

Both are open weights fetched over HTTPS. Nothing is uploaded, and after the
download everything runs offline.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.learning.language import ArabicListener, ArabicVoice  # noqa: E402


def install_voice() -> str:
    from app.voice.tts import VOICE_ROOT

    voice = ArabicVoice()
    if voice.available:
        return f"Arabic voice: already installed ({ArabicVoice.VOICE})."
    from piper.download_voices import download_voice

    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    download_voice(ArabicVoice.VOICE, VOICE_ROOT)
    return f"Arabic voice: {'installed' if voice.available else 'download did not produce a model'} ({ArabicVoice.VOICE})."


def install_speech_model(model: str) -> str:
    listener = ArabicListener(model=model)
    if listener._model_present():
        return f"Speech model: already installed (faster-whisper {model})."
    from faster_whisper import WhisperModel

    root = PROJECT_ROOT / "data" / "models" / "whisper"
    root.mkdir(parents=True, exist_ok=True)
    WhisperModel(model, device="cpu", compute_type="int8", download_root=str(root))
    return f"Speech model: {'installed' if listener._model_present() else 'download failed'} (faster-whisper {model})."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--voice-only", action="store_true", help="Install only the Arabic voice")
    parser.add_argument("--speech-model", default=ArabicListener.MODEL, choices=("base", "small", "medium"),
                        help="Whisper model for speaking exercises (default: small)")
    args = parser.parse_args(argv)
    lines = [install_voice()]
    if not args.voice_only:
        lines.append(install_speech_model(args.speech_model))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
