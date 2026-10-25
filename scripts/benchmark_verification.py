from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agent.verifier import Verifier
from app.tools.registry import ToolResult


def main() -> int:
    verifier = Verifier()
    with tempfile.TemporaryDirectory() as folder:
        partial = Path(folder) / "partial.pdf"
        partial.write_bytes(b"partial")
        cases = [
            ("app_failed_to_open", {"type": "application_window_exists", "application": "notepad"}, {"application": "notepad", "running": False, "window": None}),
            ("text_wrong_window", {"type": "ui_text_contains", "application": "notepad", "value": "hello"}, {"application": "calculator", "focused": True, "text": "hello", "text_present": True}),
            ("wrong_file_contents", {"type": "content_contains", "value": "hello"}, {"content": "goodbye"}),
            ("wrong_browser_url", {"type": "browser_destination", "value": "https://example.com"}, {"url": "https://example.org"}),
            ("application_still_running", {"type": "target_window_absent", "application": "notepad"}, {"application": "notepad", "closed": True, "target_window_exists": True}),
            ("incomplete_download", {"type": "download_complete", "bytes": 100}, {"path": str(partial), "complete": False, "bytes_downloaded": 7, "expected_bytes": 100}),
        ]
        details = []
        for name, expected, output in cases:
            result = verifier.check(expected, ToolResult(name, True, output))
            details.append({"scenario": name, "detected_failure": not result.passed, "reason": result.reason})
    report = {"cases": len(details), "failure_detection_accuracy": sum(item["detected_failure"] for item in details) / len(details), "details": details}
    destination = PROJECT_ROOT / "data/benchmarks/verification/after.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, indent=2))
    print(destination.resolve())
    return 0 if report["failure_detection_accuracy"] == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
