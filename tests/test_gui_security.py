"""Real Tk interactions: resize, scroll, local checks and closing during work."""
import os
import threading
import time
from types import SimpleNamespace

import pytest


@pytest.fixture
def security_gui(monkeypatch):
    from tests.test_gui import _build_window, _release

    monkeypatch.setattr("app.safety.watch.summary", lambda: "Local scan fixture; no camera or microphone use observed.")
    gui = _build_window()
    gui.root.geometry("1100x800+20+20")
    gui.root.deiconify()
    try:
        yield gui
    finally:
        _release(gui)


def wait(gui, condition, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        gui.root.update()
        if condition():
            return
        time.sleep(.01)
    raise AssertionError("security GUI did not reach the expected condition")


def test_security_studio_checks_scroll_resize_unban_and_activity(security_gui):
    from app.security.intrusion import IntrusionShield

    gui = security_gui
    shield = IntrusionShield()
    for index in range(30):
        shield.inspect(f"203.0.113.{index + 1}", "GET", "/.env")
    shield.decoy_response("203.0.113.1", "GET", "/.env")
    gui._remote_access = SimpleNamespace(server=SimpleNamespace(shield=shield))
    gui._open_security()
    studio = gui._security_studio
    try:
        wait(gui, lambda: not studio.busy)
        assert len(studio.bans.get_children()) == 30
        assert "Live remote shield" in studio.overview.get()
        assert "1 decoy response in" in studio.overview.get()
        assert studio.bans.item("203.0.113.1", "values")[-1] == "Sent"
        assert len(studio.tabs.tabs()) == 3
        studio.bans.yview_moveto(1.0)
        gui.root.update()
        old_view = studio.bans.yview()
        studio.refresh_bans()
        wait(gui, lambda: not studio.busy)
        assert studio.bans.yview() == old_view
        studio.bans.selection_set("203.0.113.30")
        studio.bans.event_generate("<<TreeviewSelect>>")
        gui.root.update()
        assert "secret_probe" in studio.ban_detail.get()
        studio.unban_button.invoke()
        wait(gui, lambda: not studio.busy and len(studio.bans.get_children()) == 29)
        assert shield.banned_for("203.0.113.30") == 0
        if os.environ.get("HAYAT_SHOTS") == "1":
            from tests.test_gui_screenshots import _grab

            _grab(gui, "74-security-live-defense", studio)
        studio.check_button.invoke()
        wait(gui, lambda: not studio.busy)
        assert studio.tabs.index(studio.tabs.select()) == 2
        assert "10/10 local checks passed" in studio.check_text.get("1.0", "end")
        assert "example.invalid" in studio.check_text.get("1.0", "end")
        assert len(shield.status()["banned"]) == 29  # diagnostics did not change owner bans
        studio.geometry("640x480+40+40")
        gui.root.update()
        assert studio.check_button.winfo_ismapped() and studio.check_text.winfo_height() > 80
        if os.environ.get("HAYAT_SHOTS") == "1":
            from tests.test_gui_screenshots import _grab

            _grab(gui, "75-security-local-checks-compact", studio)
        studio.tabs.select(1)
        gui.root.update()
        assert studio.scan_text.winfo_height() > 100
        assert "Snapshot taken at" in studio.scan_text.get("1.0", "end")
    finally:
        studio.destroy()


def test_slow_scan_does_not_block_controls_or_close(security_gui, monkeypatch, tmp_path):
    from app.gui_security import SecurityStudio

    gui = security_gui
    release, started, done = threading.Event(), threading.Event(), threading.Event()

    def slow_scan():
        started.set()
        release.wait(10)
        done.set()
        return "Scan finished after the window closed."

    monkeypatch.setattr("app.gui_security._ban_store", lambda: tmp_path / "empty.json")
    monkeypatch.setattr("app.safety.watch.summary", slow_scan)
    errors = []
    monkeypatch.setattr(gui.root, "report_callback_exception", lambda *args: errors.append(args))
    gui._open_security()
    studio = gui._security_studio
    try:
        wait(gui, started.is_set)
        wait(gui, lambda: "bans" not in studio._pending)
        assert "not an attack history" in studio.ban_note.get()
        assert "remote shield not running" in studio.overview.get()
        studio.check_button.invoke()
        wait(gui, lambda: "checks" not in studio._pending)
        assert "10/10" in studio.check_text.get("1.0", "end")
        assert studio.busy  # the independent slow scan is still running
        timer = studio._poll_id
        studio.destroy()
        assert studio._poll_id is None
        assert str(timer) not in gui.root.tk.call("after", "info")
        release.set()
        wait(gui, done.is_set)
        gui.root.update()
        assert not errors
        # A second window has no stale result from the closed first window.
        monkeypatch.setattr("app.safety.watch.summary", lambda: "Fresh scan")
        replacement = SecurityStudio(gui)
        wait(gui, lambda: not replacement.busy)
        assert "Fresh scan" in replacement.scan_text.get("1.0", "end")
        assert "after the window closed" not in replacement.scan_text.get("1.0", "end")
        replacement.destroy()
    finally:
        release.set()
        studio.destroy()
