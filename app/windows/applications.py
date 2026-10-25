from __future__ import annotations

import logging
import time
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

try:
    from pywinauto import Desktop, keyboard
except Exception:  # pragma: no cover - non-Windows: desktop automation is Windows-only
    # Let the module import on macOS/Linux so the assistant core can start;
    # the Windows-only automation methods below degrade to empty/failed
    # results (they already guard their pywinauto calls in try/except) rather
    # than crashing the whole runtime at import time.
    Desktop = None  # type: ignore[assignment]
    keyboard = None  # type: ignore[assignment]


class WindowsApplicationController:
    TITLE_PATTERNS = {
        "notepad": r"(?i).*(?:Notepad).*$", "calculator": r"(?i).*Calculator.*",
        "chrome": r"(?i).*Chrome.*", "edge": r"(?i).*(?:Microsoft Edge|Edge).*$",
        "vscode": r"(?i).*(?:Visual Studio Code).*$", "file_explorer": r"(?i).*(?:File Explorer|Explorer).*$",
        # The desktop app, not a WhatsApp Web tab in a browser window.
        "whatsapp": r"(?i)^(?:\(\d+\)\s*)?WhatsApp(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*$",
        # Office titles end with the app name ("Budget.xlsx - Excel").
        "word": r"(?i)^(?:.* - )?Word$", "excel": r"(?i)^(?:.* - )?Excel$",
        "powerpoint": r"(?i)^(?:.* - )?PowerPoint$",
        "outlook": r"(?i)^(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*\bOutlook\b.*$",
        "onenote": r"(?i)^(?:.* - )?OneNote(?: for Windows 10)?$",
        # Chat and media apps; a web version in a browser tab does not count.
        "teams": r"(?i)^(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*\bMicrosoft Teams\b.*$",
        "discord": r"(?i)^(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*\bDiscord$",
        "zoom": r"(?i)^(?!.*(?:Chrome|Edge|Firefox|Mozilla))(?:Zoom(?: Workplace| Meeting)?)$",
        "telegram": r"(?i)^(?:\(\d+\)\s*)?Telegram(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*$",
        "signal": r"(?i)^(?:\(\d+\)\s*)?Signal$",
        "spotify": r"(?i)^(?!.*(?:Chrome|Edge|Firefox|Mozilla)).*Spotify(?: Premium| Free)?$",
    }

    def windows(self, application: str) -> list[dict[str, Any]]:
        pattern = self.TITLE_PATTERNS.get(application)
        if not pattern:
            return []
        result: list[dict[str, Any]] = []
        try:
            windows = Desktop(backend="uia").windows(title_re=pattern, visible_only=True, top_level_only=True)
        except Exception:
            return []
        for window in windows:
            try:
                result.append({"handle": int(window.handle), "pid": int(window.process_id()), "title": window.window_text(), "visible": window.is_visible(), "enabled": window.is_enabled()})
            except Exception:
                log_swallowed(_log, "reading a window's details")
                continue
        return result

    def wait_for_window(self, application: str, timeout: float = 8.0) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if windows := self.windows(application):
                return windows[-1]
            time.sleep(.15)
        return None

    def focus(self, application: str, handle: int | None = None) -> dict[str, Any]:
        window = self._window(application, handle)
        self._ensure_foreground(window)
        focused = self._focused_handle() == int(window.handle)
        return {"application": application, "window": self._describe(window), "focused": focused}

    def read_notepad(self, handle: int | None = None) -> dict[str, Any]:
        window = self._window("notepad", handle)
        editor = self._notepad_editor(window)
        text = self._control_text(editor)
        focused = self._focused_handle() in {int(window.handle), int(getattr(editor, "handle", 0) or 0)}
        return {"application": "notepad", "window": self._describe(window), "focused": focused, "text": text, "text_length": len(text)}

    def write_notepad(self, text: str, append_line: bool = False, handle: int | None = None) -> dict[str, Any]:
        if not text:
            raise ValueError("Notepad text cannot be empty.")
        window = self._window("notepad", handle)
        editor = self._notepad_editor(window)
        self._ensure_foreground(window)
        try:
            editor.set_focus()
        except Exception:
            log_swallowed(_log, "focusing the Notepad editor")
        before = self._control_text(editor)
        payload = (("\r\n" if before and append_line else "") + text)
        self._paste_text(payload)
        time.sleep(.25)
        observed = self._control_text(editor)
        return {
            "application": "notepad", "target_application": "notepad", "window": self._describe(window),
            "running": True,
            "focused": self._focused_handle() in {int(window.handle), int(getattr(editor, "handle", 0) or 0)},
            "requested_text": text, "text": observed, "text_present": text in observed,
            "before_length": len(before), "after_length": len(observed), "append_line": append_line,
        }

    def close(self, application: str, handle: int | None = None, save_choice: str = "ask", timeout: float = 4.0) -> dict[str, Any]:
        if save_choice not in {"ask", "save", "discard", "cancel"}:
            raise ValueError("save_choice must be ask, save, discard, or cancel.")
        window = self._window(application, handle)
        target = self._describe(window)
        target_handle = int(window.handle)
        window.set_focus()
        dialog = self._save_dialog(window)
        if not dialog:
            window.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(item["handle"] == target_handle for item in self.windows(application)):
                return {"application": application, "window": target, "closed": True, "target_window_exists": False, "remaining_windows": self.windows(application)}
            dialog = self._save_dialog(window)
            if dialog:
                choices = self._dialog_choices(dialog)
                if save_choice == "ask":
                    return {"application": application, "window": target, "closed": False, "target_window_exists": True, "requires_user_choice": True, "choices": choices, "reason": "Unsaved changes require an explicit save choice."}
                label = {"save": "Save", "discard": "Don't Save", "cancel": "Cancel"}[save_choice]
                button = self._button(dialog, label)
                if button is None:
                    return {"application": application, "window": target, "closed": False, "target_window_exists": True, "requires_user_choice": True, "choices": choices, "reason": f"The {label} control was not found."}
                try:
                    button.invoke()
                except Exception:
                    button.click_input()
                if save_choice == "cancel":
                    return {"application": application, "window": target, "closed": False, "target_window_exists": True, "cancelled": True}
            time.sleep(.15)
        exists = any(item["handle"] == target_handle for item in self.windows(application))
        return {"application": application, "window": target, "closed": not exists, "target_window_exists": exists, "remaining_windows": self.windows(application)}

    def state(self, application: str, handle: int | None = None) -> dict[str, Any]:
        windows = self.windows(application)
        target = next((item for item in windows if handle and item["handle"] == handle), windows[-1] if windows else None)
        result: dict[str, Any] = {"application": application, "running": bool(windows), "window_count": len(windows), "windows": windows, "target_window_exists": bool(target)}
        if target:
            result["window"] = target
        if application == "notepad" and target:
            try:
                result.update({key: value for key, value in self.read_notepad(target["handle"]).items() if key in {"focused", "text", "text_length"}})
            except Exception as exc:
                result["observation_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def _window(self, application: str, handle: int | None):
        if handle:
            try:
                window = Desktop(backend="uia").window(handle=handle)
                if window.exists() and window.is_visible():
                    return window.wrapper_object()
            except Exception:
                log_swallowed(_log, "reusing a window by handle")
        windows = self.windows(application)
        if not windows:
            raise LookupError(f"No visible {application} window was found.")
        return Desktop(backend="uia").window(handle=windows[-1]["handle"]).wrapper_object()

    @staticmethod
    def _describe(window) -> dict[str, Any]:
        return {"handle": int(window.handle), "pid": int(window.process_id()), "title": window.window_text(), "visible": window.is_visible(), "enabled": window.is_enabled()}

    @staticmethod
    def _focused_handle() -> int | None:
        try:
            import win32gui
            foreground = int(win32gui.GetForegroundWindow())
            return int(win32gui.GetAncestor(foreground, 2) or foreground)
        except Exception:
            return None

    @classmethod
    def _ensure_foreground(cls, window, attempts: int = 4) -> bool:
        """Bring `window` to the real OS foreground, retrying with escalating force.

        Windows restricts SetForegroundWindow from processes that did not
        receive the most recent user input
        (https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-setforegroundwindow):
        a background automation process can silently fail to steal focus, in
        which case a paste/keystroke lands wherever focus actually is instead
        of the intended window, without raising an exception. `window.set_focus()`
        alone hits this intermittently (observed ~30% of consecutive runs on
        this machine). Each retry after the first uses AttachThreadInput to
        borrow the current foreground thread's input state, which is the
        standard bypass for this restriction.
        """
        handle = int(window.handle)
        for attempt in range(attempts):
            if cls._focused_handle() == handle:
                return True
            try:
                if attempt == 0:
                    window.set_focus()
                else:
                    cls._force_foreground(handle)
            except Exception:
                log_swallowed(_log, "bringing a window to the foreground")
            time.sleep(.12)
        return cls._focused_handle() == handle

    @staticmethod
    def _force_foreground(handle: int) -> None:
        import win32api
        import win32con
        import win32gui
        import win32process

        foreground = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(handle)
        foreground_thread, _ = win32process.GetWindowThreadProcessId(foreground) if foreground else (0, 0)
        attached_foreground = attached_target = False
        try:
            if foreground_thread and foreground_thread != current_thread:
                win32process.AttachThreadInput(current_thread, foreground_thread, True)
                attached_foreground = True
            if target_thread and target_thread != current_thread:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                attached_target = True
            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(handle)
            win32gui.SetForegroundWindow(handle)
        finally:
            if attached_foreground:
                win32process.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                win32process.AttachThreadInput(current_thread, target_thread, False)

    @staticmethod
    def _notepad_editor(window):
        descendants = window.descendants(control_type="Document") + window.descendants(control_type="Edit")
        candidates = [item for item in descendants if item.is_visible() and item.is_enabled()]
        if not candidates:
            raise LookupError("A visible editable Notepad document control was not found.")
        return max(candidates, key=lambda item: item.rectangle().width() * item.rectangle().height())

    @staticmethod
    def _control_text(control) -> str:
        try:
            pattern = control.iface_value
            value = pattern.CurrentValue
            if value is not None:
                return str(value).replace("\r\n", "\n").replace("\r", "\n")
        except Exception:
            log_swallowed(_log, "reading control text through UIA")
        try:
            return str(control.window_text()).replace("\r\n", "\n").replace("\r", "\n")
        except Exception:
            return ""

    @staticmethod
    def _paste_text(text: str) -> None:
        import win32clipboard
        old_text: str | None = None
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                old_text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                log_swallowed(_log, "closing the clipboard")
        keyboard.send_keys("^v")
        time.sleep(.1)
        if old_text is not None:
            try:
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(old_text, win32clipboard.CF_UNICODETEXT)
            finally:
                try:
                    win32clipboard.CloseClipboard()
                except Exception:
                    log_swallowed(_log, "closing the clipboard after restore")

    @staticmethod
    def _save_dialog(window):
        try:
            candidates = [window] + window.descendants(control_type="Window") + window.descendants(control_type="Pane")
            for item in candidates:
                labels = {
                    button.window_text().lower().replace("’", "'").replace("&", "").strip()
                    for button in item.descendants(control_type="Button")
                    if button.window_text()
                }
                if "save" in labels and any(label in labels for label in {"don't save", "dont save"}):
                    return item
        except Exception:
            return None
        return None

    @staticmethod
    def _dialog_choices(dialog) -> list[str]:
        try:
            return [item.window_text() for item in dialog.descendants(control_type="Button") if item.window_text()]
        except Exception:
            return []

    @staticmethod
    def _button(dialog, label: str):
        normalized = label.lower().replace("’", "'")
        try:
            for button in dialog.descendants(control_type="Button"):
                actual = button.window_text().lower().replace("’", "'").replace("&", "")
                if normalized == actual or normalized in actual:
                    return button
        except Exception:
            return None
        return None
