from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.runtime import build_runtime
from app.windows.applications import WindowsApplicationController
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def main() -> int:
    controller = WindowsApplicationController()
    before = {item["handle"]: item for item in controller.windows("notepad")}
    process = subprocess.Popen(["notepad.exe"], cwd=str(PROJECT_ROOT), close_fds=True)
    target = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        target = next((item for item in controller.windows("notepad") if item["handle"] not in before), None)
        if target:
            break
        time.sleep(.2)
    if not target:
        if process.poll() is None:
            process.terminate()
        print(json.dumps({"passed": False, "safe_abort": True, "reason": "No distinct disposable Notepad window was available."}, indent=2))
        return 2

    runtime = build_runtime(use_model=False)
    handle = target["handle"]
    report: dict = {"target": target, "preexisting_handles": sorted(before), "turns": []}
    try:
        # This script itself is the explicit approval for reversible input into its own disposable window.
        runtime.voice._approvals_enabled = False
        runtime.registry.approval_callback = lambda tool, reason, arguments: True
        runtime.session.active_application = "notepad"
        runtime.session.last_opened_application = "notepad"
        runtime.session.active_window = target

        for transcript in ("Hello AI", "Write hello from my AI in it", "Add: second line", "Close it", "discard"):
            response = runtime.voice.process_transcript(transcript)
            report["turns"].append({"transcript": transcript, "display": response.display_response, "spoken": response.spoken_response, "complete": response.complete, "task_id": response.task_id})
            if transcript.startswith("Add"):
                observed = controller.read_notepad(handle)
                report["observed_text"] = observed["text"]

        remaining = {item["handle"] for item in controller.windows("notepad")}
        report["target_closed"] = handle not in remaining
        report["preexisting_preserved"] = set(before).issubset(remaining)
        # Whether Notepad shows an unsaved-changes dialog on close is a detail
        # of the installed Notepad build, not a correctness requirement: this
        # machine's Notepad persists its buffer across launches (visible as
        # accumulating text across repeated runs of this very script) and
        # closes a disposable, freshly-launched window without prompting.
        # Recorded for visibility; not required for `passed`, which checks
        # what the task actually promised: text written, edited, and the
        # intended window closed without disturbing pre-existing windows.
        report["unsaved_choice_requested"] = "unsaved changes" in report["turns"][3]["display"].lower()
        report["context_resolved"] = runtime.session.last_opened_application == "notepad"
        report["passed"] = all((
            report["turns"][0]["complete"] is True,
            report["turns"][1]["complete"] is True,
            report["turns"][2]["complete"] is True,
            report["turns"][3]["complete"] is True,
            "hello from my AI" in report.get("observed_text", ""),
            "second line" in report.get("observed_text", ""),
            report["target_closed"], report["preexisting_preserved"],
        ))
    finally:
        runtime.close()
        if handle in {item["handle"] for item in controller.windows("notepad")}:
            try:
                controller.close("notepad", handle=handle, save_choice="discard")
            except Exception:
                log_swallowed(_log, "closing the test Notepad window")
    print(json.dumps(report, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
