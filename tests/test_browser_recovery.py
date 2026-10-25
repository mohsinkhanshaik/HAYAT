"""The browser recovers from a failed launch instead of failing forever.

browser_navigate failed 58% of the time in real use -- 7 of 12 attempts, all
fast RuntimeErrors. It was not the network. launch() started the Playwright
driver, then failed to get a browser, and left the started driver in place;
the next launch guarded only on self.browser, so it called
sync_playwright().start() a second time over the live driver, which raises a
fast RuntimeError on the same thread. Once in that state every navigate failed
identically until the process ended.
"""
from __future__ import annotations

import pytest

from app.tools import browser as browser_module
from app.tools.browser import BrowserTools
from app.tools.browser_host import PlaywrightHost


class _FakePage:
    def __init__(self, url: str = "about:blank") -> None:
        self._url = url

    def goto(self, url, **kwargs):
        self._url = url
        return type("Resp", (), {"status": 200})()

    def wait_for_load_state(self, *a, **k):
        return None

    def is_closed(self):
        return False

    @property
    def url(self):
        return self._url

    def title(self):
        return "ok"


class _FakeContext:
    def new_page(self):
        return _FakePage()


class _FakeBrowser:
    def is_connected(self):
        return True

    def new_context(self, **kwargs):
        return _FakeContext()


class _FakeChromium:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.launches = 0

    def launch(self, **kwargs):
        self.launches += 1
        if self.launches <= self.fail_times:
            raise RuntimeError("Executable doesn't exist / transient launch failure")
        return _FakeBrowser()


class _FakeDriver:
    """One sync_playwright().start() result. Records whether it was stopped."""

    _live_count = 0

    def __init__(self, fail_times: int) -> None:
        self.chromium = _FakeChromium(fail_times)
        self.stopped = False
        type(self)._live_count += 1

    def stop(self):
        self.stopped = True
        type(self)._live_count -= 1


def _install_fake(monkeypatch, fail_sequence):
    """Make sync_playwright().start() hand out drivers with a scripted number
    of chromium.launch failures, and refuse to ever run two at once."""
    drivers = []
    seq = iter(fail_sequence)

    def fake_sync_playwright():
        def start():
            # This is the invariant the bug violated: a second start() while a
            # driver is still live is exactly what raised the fast RuntimeError.
            if _FakeDriver._live_count != 0:
                raise RuntimeError("Sync API started twice on the same thread")
            driver = _FakeDriver(next(seq, 0))
            drivers.append(driver)
            return driver

        return type("PW", (), {"start": staticmethod(start)})()

    _FakeDriver._live_count = 0
    monkeypatch.setattr(browser_module, "sync_playwright", fake_sync_playwright)
    monkeypatch.setattr(browser_module, "validate_public_url", lambda u: u)
    return drivers


def test_a_failed_launch_is_cleaned_up_so_the_next_one_starts_clean(monkeypatch):
    from app.safety.permissions import PermissionManager

    # First driver: both chromium.launch attempts fail. Second driver: succeeds.
    drivers = _install_fake(monkeypatch, fail_sequence=[2, 0])
    # Unconfined host would still work, but use a real one to mirror production.
    tools = BrowserTools(PermissionManager(), host=PlaywrightHost("test-owner"))

    # The first navigate exhausts its retries against the failing driver.
    with pytest.raises(RuntimeError):
        tools.navigate("https://example.com", retries=0)

    # The broken driver must have been stopped, not left live.
    assert drivers[0].stopped is True, "a failed launch must stop the driver it started"
    assert _FakeDriver._live_count == 0

    # The very next navigate must succeed -- previously it hit "started twice".
    result = tools.navigate("https://example.com", retries=0)
    assert result["url"] == "https://example.com"
    assert len(drivers) == 2, "the second attempt starts a fresh driver"


def test_repeated_transient_failures_never_double_start(monkeypatch):
    """Even a run of failures must never leave two drivers live at once."""
    from app.safety.permissions import PermissionManager

    _install_fake(monkeypatch, fail_sequence=[2, 2, 2, 0])
    tools = BrowserTools(PermissionManager(), host=PlaywrightHost("test-owner"))

    for _ in range(3):
        with pytest.raises(RuntimeError):
            tools.navigate("https://example.com", retries=0)
        assert _FakeDriver._live_count == 0  # never two live

    assert tools.navigate("https://example.com", retries=0)["url"] == "https://example.com"


# --------------------------------------------------------------------------
# What "loaded" means. Owner turns failed 7 times asking for "networkidle2"
# (Puppeteer's word), which Playwright rejects; each retry sent it again.
# --------------------------------------------------------------------------

class _WaitRecordingPage(_FakePage):
    def __init__(self, timeouts: int = 0) -> None:
        super().__init__()
        self.asked: list[str] = []
        self.timeouts = timeouts

    def goto(self, url, **kwargs):
        wait = kwargs.get("wait_until")
        if wait not in browser_module.LOAD_STATES:
            raise ValueError(f"wait_until: expected one of (load|domcontentloaded|networkidle|commit), got {wait}")
        self.asked.append(wait)
        if self.timeouts:
            self.timeouts -= 1
            raise TimeoutError("Timeout 30000ms exceeded.")
        return super().goto(url)


def _tools_with(monkeypatch, page):
    from app.safety.permissions import PermissionManager

    _install_fake(monkeypatch, fail_sequence=[0])
    tools = BrowserTools(PermissionManager(), host=PlaywrightHost("test-owner"))
    monkeypatch.setattr(BrowserTools, "_page", lambda self: page)
    monkeypatch.setattr(BrowserTools, "_recover_page", lambda self: None)
    return tools


@pytest.mark.parametrize("asked, sent", [
    ("networkidle2", "load"), ("networkidle0", "networkidle"), ("NetworkIdle", "networkidle"),
    ("dom_content_loaded", "domcontentloaded"), ("", "domcontentloaded"), ("whenever", "domcontentloaded"),
])
def test_other_tools_words_for_loaded_are_translated(monkeypatch, asked, sent):
    page = _WaitRecordingPage()
    result = _tools_with(monkeypatch, page).navigate("https://example.com", wait_until=asked, retries=0)
    assert page.asked == [sent] and result["waited_for"] == sent


def test_a_page_that_never_goes_quiet_is_retried_with_a_lighter_wait(monkeypatch):
    page = _WaitRecordingPage(timeouts=2)
    result = _tools_with(monkeypatch, page).navigate("https://example.com", wait_until="networkidle", retries=2)
    assert page.asked == ["networkidle", "load", "domcontentloaded"]
    assert result["attempts"] == 3 and len(result["errors"]) == 2


def test_the_schema_accepts_any_wording_so_translation_can_happen(tmp_path):
    from jsonschema import validate
    from app.safety.permissions import PermissionManager
    from app.tools.browser import register_browser_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_browser_tools(registry)
    schema = registry.get("browser_navigate").input_schema
    validate(instance={"url": "https://example.com", "wait_until": "networkidle2"}, schema=schema)
    assert "networkidle" in schema["properties"]["wait_until"]["description"]
