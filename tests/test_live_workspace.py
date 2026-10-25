"""Generate an app with HAYAT's real model, then exercise its actual browser behavior."""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_LIVE") != "1", reason="live check: set HAYAT_LIVE=1")


def test_replica_build_on_hayat_model_works_in_browser(tmp_path):
    from app.runtime import build_runtime
    from playwright.sync_api import sync_playwright
    runtime = build_runtime(use_model=True, start_background_services=False)
    workspace = tmp_path / "counter-app"
    (workspace / "replica/design").mkdir(parents=True)
    (workspace / "replica/recon.md").write_text("Requirements fixture supplied locally: an original offline counter. No competitor or live reference was observed.")
    (workspace / "replica/architecture.md").write_text("One index.html. Inline JavaScript, no dependencies or networking. A button increments a visible count.")
    (workspace / "replica/design/tokens.json").write_text('{"color":{"bg":"#ffffff","text":"#111111"}}')
    (workspace / "replica/features.csv").write_text("feature,area,priority,original,clone,notes\nIncrement,counter,must,yes,no,Local fixture\n")
    try:
        result = runtime.registry.execute("replica_run", {"stage": "build", "workspace": str(workspace),
            "goal": "Build the original offline counter app described in the local requirements. Write exactly index.html and replica/build-log.md. "
                    "In index.html include a button with id=increment and a visible output with id=count starting at 0. "
                    "Each click increases its integer text by one. Inline CSS and JS only, no dependencies, URLs or network access. "
                    "Keep the interface readable and keyboard accessible. build-log.md must describe generated files and say browser verification is pending; do not claim tests ran."})
        assert result.success, result.error
        output = result.output
        assert output["files"] and not output["tests_run"] and not output["published"]
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith("file:") else route.abort())
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto((workspace / "index.html").as_uri())
                assert page.locator("#count").inner_text().strip() == "0"
                for _ in range(3):
                    page.locator("#increment").click()
                assert page.locator("#count").inner_text().strip() == "3"
                page.locator("#increment").focus()
                page.keyboard.press("Enter")
                assert page.locator("#count").inner_text().strip() == "4"
                assert not errors, errors
                report = Path(__file__).resolve().parents[1] / ".test-runs/live-replica-build.json"
                report.write_text(json.dumps({"generated": output, "actual_browser_checks": {"initial_count": 0, "three_clicks": 3,
                    "keyboard_enter": 4, "page_errors": errors, "network_requests_blocked": True}, "passed": True}, indent=2), encoding="utf-8")
            finally:
                browser.close()
    finally:
        runtime.close()
