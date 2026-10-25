"""Login controls should be understandable and never block the desktop window."""
from __future__ import annotations

import time

from app.gui_startup import StartupDialog, status_text
from app.gui_theme import Palette, style_controls
from app.voice.tray import VoiceTray


def test_startup_status_uses_plain_language():
    assert status_text({"installed": False}) == "Off. HAYAT will open only when you launch it."
    assert "will open when you sign in" in status_text({"installed": True})
    assert "last session" in status_text({"installed": True, "last_run": {
        "state": "restart_wait", "updated_at": 0}}).lower()


def test_login_dialog_can_enable_and_disable_without_blocking(shared_tk, monkeypatch):
    from app import gui_startup

    installed = {"value": False}
    monkeypatch.setattr(gui_startup.autostart, "status", lambda: {"installed": installed["value"]})
    monkeypatch.setattr(gui_startup.autostart, "install", lambda: installed.update(value=True))
    monkeypatch.setattr(gui_startup.autostart, "remove", lambda: installed.update(value=False))
    style_controls(shared_tk, Palette)
    dialog = StartupDialog(shared_tk, Palette)
    try:
        assert dialog.winfo_exists()
        assert "Off." in dialog._message.get()
        dialog.enable_button.invoke()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and dialog._busy:
            shared_tk.update()
            time.sleep(.01)
        assert installed["value"]
        assert "On." in dialog._message.get()
        dialog.disable_button.invoke()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and dialog._busy:
            shared_tk.update()
            time.sleep(.01)
        assert not installed["value"]
        assert "Off." in dialog._message.get()
    finally:
        dialog.destroy()


def test_tray_uses_the_project_icon():
    image = VoiceTray._image()
    assert image.size == (64, 64)
    assert image.getbbox() is not None


def test_unreadable_startup_status_does_not_offer_a_misleading_toggle(shared_tk, monkeypatch):
    from app import gui_startup

    def fail():
        raise OSError("Startup folder unavailable")

    monkeypatch.setattr(gui_startup.autostart, "status", fail)
    dialog = StartupDialog(shared_tk, Palette)
    try:
        assert str(dialog.enable_button.cget("state")) == "disabled"
        assert str(dialog.disable_button.cget("state")) == "disabled"
        assert "unavailable" in dialog._detail.get()
        monkeypatch.setattr(gui_startup.autostart, "status", lambda: {"installed": False})
        dialog.refresh_button.invoke()
        assert str(dialog.enable_button.cget("state")) == "normal"
    finally:
        dialog.destroy()
