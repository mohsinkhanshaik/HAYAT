from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.perception.screen import ScreenPerception
from app.perception.screenshot import ScreenshotPerception
from app.runtime import build_runtime


def main() -> int:
    runtime = build_runtime(interactive_approvals=False, use_model=False)
    report = {"models": runtime.router.available(), "browser": {}, "screen": {}}
    failed = False
    try:
        launch = runtime.registry.execute("browser_launch", {"headless": True, "channel": "chrome"})
        navigate = runtime.registry.execute("browser_navigate", {"url": "https://example.com"})
        state = runtime.registry.execute("browser_read_page", {})
        accessibility = runtime.registry.execute("browser_accessibility", {})
        screenshot = runtime.registry.execute("browser_screenshot", {"path": "data/artifacts/browser_example.png", "full_page": True})
        runtime.browser._page().set_content('<a id="download" download="sample.txt" href="data:text/plain,quarantined">download</a>')
        download = runtime.browser.download("#download")
        runtime.registry.execute("browser_close", {})
        browser_ok = all(item.success for item in (launch, navigate, state, accessibility, screenshot))
        if browser_ok:
            output = state.output
            browser_ok = output["title"] == "Example Domain" and output["url"].startswith("https://example.com") and "Example Domain" in accessibility.output["aria_snapshot"] and download["quarantined"] and not download["executed"]
        report["browser"] = {
            "success": browser_ok,
            "title": state.output.get("title") if state.success else None,
            "url": state.output.get("url") if state.success else None,
            "screenshot": screenshot.output if screenshot.success else screenshot.error,
            "accessibility_tree": accessibility.success,
            "download": download,
            "errors": [item.error for item in (launch, navigate, state, accessibility, screenshot) if not item.success],
        }
        failed = failed or not browser_ok

        perception = ScreenPerception(ScreenshotPerception(runtime.computer))
        screen = perception.observe("data/artifacts/windows_screen.png")
        report["screen"] = screen
        failed = failed or not screen.get("exists", False)
    except Exception as exc:
        report["integration_error"] = f"{type(exc).__name__}: {exc}"
        failed = True
    finally:
        runtime.close()
    print(json.dumps(report, indent=2, default=str))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
