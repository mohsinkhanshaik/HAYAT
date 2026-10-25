from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import PROJECT_ROOT
from app.runtime import build_runtime


def main() -> int:
    runtime = build_runtime(use_model=False)
    result: dict = {}
    try:
        voice = runtime.voice
        if voice is None:
            raise RuntimeError("Voice runtime is unavailable.")

        # Voice transcript -> existing manager/planner/tools/permissions/verifier/memory.
        requested = "Create a text file called voice-test.txt containing hello from my AI."
        response = voice.process_transcript(requested)
        report = PROJECT_ROOT / "voice-test.txt"
        content = report.read_text(encoding="utf-8") if report.exists() else ""
        result["voice_agent_filesystem"] = {
            "task_id": response.task_id, "complete": response.complete,
            "spoken_response": response.spoken_response, "file": str(report),
            "exists": report.exists(), "content": content,
            "written_paths": response.metadata.get("written_paths", []),
            "passed": response.complete is True and content == "hello from my ai" and str(report) in response.metadata.get("written_paths", []) and response.spoken_response == "Done. The file was created successfully.",
        }

        # Real local synthesis and output-device playback command.
        voice.tts.speak(response.spoken_response, wait=True)
        result["tts_playback_command"] = {"completed": True, "provider": voice.tts.name}
        print(json.dumps(result, indent=2))
        return 0 if all(item.get("passed", item.get("completed", False)) for item in result.values()) else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
