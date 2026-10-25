"""Download a free pre-trained openWakeWord model for the low-power wake engine.

    .\\.venv\\Scripts\\python.exe -m scripts.setup_wakeword hey_jarvis

Then set ``voice.wake_word.engine: openwakeword`` (and ``model``) in
config/settings.yaml. About 9 MB from the openWakeWord GitHub releases.
"""
from __future__ import annotations

import sys


def main() -> None:
    from app.voice.wake_engine import PRETRAINED, download

    model = sys.argv[1] if len(sys.argv) > 1 else "hey_jarvis"
    if model not in PRETRAINED:
        raise SystemExit(f"Choose one of: {', '.join(PRETRAINED)}")
    print("Downloaded:", ", ".join(download(model)))


if __name__ == "__main__":
    main()
