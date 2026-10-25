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
        print(json.dumps({"passed": False, "safe_abort": True, "reason": "Notepad did not create a distinguishable new window; no existing window was touched.", "existing_handles": sorted(before)}, indent=2))
        return 2

    handle = target["handle"]
    report: dict = {"target": target, "preexisting_handles": sorted(before), "text_verified": False, "close_behavior": None, "target_closed": False, "preexisting_preserved": False}
    try:
        written = controller.write_notepad("Hello from my AI.\nSecond line", handle=handle)
        observed = controller.read_notepad(handle)
        report["text_verified"] = written["text_present"] is True and "Hello from my AI." in observed["text"] and "Second line" in observed["text"]
        close = controller.close("notepad", handle=handle, save_choice="ask")
        report["close_behavior"] = "approval_required" if close.get("requires_user_choice") else "closed_without_prompt"
        if close.get("requires_user_choice"):
            close = controller.close("notepad", handle=handle, save_choice="discard")
        report["target_closed"] = close.get("target_window_exists") is False
    finally:
        remaining = {item["handle"] for item in controller.windows("notepad")}
        if handle in remaining:
            try:
                controller.close("notepad", handle=handle, save_choice="discard")
            except Exception:
                log_swallowed(_log, "closing the test Notepad window")
        final_handles = {item["handle"] for item in controller.windows("notepad")}
        report["preexisting_preserved"] = set(before).issubset(final_handles)
        report["remaining_handles"] = sorted(final_handles)
    report["passed"] = all((report["text_verified"], report["target_closed"], report["preexisting_preserved"]))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
