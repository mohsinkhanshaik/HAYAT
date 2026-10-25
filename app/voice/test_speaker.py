from __future__ import annotations

import argparse
import json

from app.runtime import build_runtime


def main() -> int:
    parser = argparse.ArgumentParser(description="Speak a short phrase through the configured local TTS and output device.")
    parser.add_argument("--list", action="store_true", help="List audio outputs and installed Windows voices without playback")
    args = parser.parse_args()
    runtime = build_runtime(use_model=False)
    try:
        voice = runtime.voice
        if voice is None:
            raise RuntimeError("Voice runtime is unavailable.")
        if args.list:
            devices = voice.devices.list_devices()
            devices["voices"] = voice.tts.list_voices() if hasattr(voice.tts, "list_voices") else []
            print(json.dumps(devices, indent=2))
            return 0
        print('Speaking: "Voice system is working." Press Ctrl+C to stop.')
        try:
            voice.test_speaker(wait=True)
        except KeyboardInterrupt:
            voice.tts.stop()
            print("Playback stopped.")
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
