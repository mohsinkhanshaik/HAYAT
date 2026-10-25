from __future__ import annotations

import ctypes
import os
import threading
import time
from dataclasses import dataclass
from typing import Any


_VIRTUAL_KEYS = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12,
    "space": 0x20, "enter": 0x0D, "escape": 0x1B, "esc": 0x1B,
    "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


@dataclass(slots=True)
class CancellationController:
    shutdown: threading.Event
    stop_speaking: threading.Event

    @classmethod
    def create(cls) -> "CancellationController":
        return cls(threading.Event(), threading.Event())


_MODIFIERS = {0x10, 0x11, 0x12}
_WM_KEYDOWN, _WM_KEYUP, _WM_SYSKEYDOWN, _WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
_LLKHF_INJECTED = 0x10


class KeyboardClaim:
    """Keeps the push-to-talk combination from reaching other applications.

    Polling the key state saw Ctrl+Space without taking it, so every press
    also went to whatever had focus -- in an editor that is "show
    suggestions", and holding the keys to talk opened one popup after another.
    A low-level keyboard hook now swallows the trigger key while the
    modifiers are down. Windows stops updating a swallowed key's async state,
    so the hook keeps its own pressed flag. Keys sent by software (HAYAT's own
    automation) are never swallowed.
    """

    def __init__(self, keys: list[int]) -> None:
        self.modifiers = [key for key in keys if key in _MODIFIERS]
        self.triggers = {key for key in keys if key not in _MODIFIERS}
        self.down = False
        self.enabled = False
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._proc = None
        self._ready = threading.Event()
        self.error = ""

    def decide(self, vk: int, message: int, injected: bool, modifiers_down: bool) -> bool:
        """Whether to swallow this key event; also tracks the trigger's state."""
        if vk not in self.triggers:
            return False
        if message in (_WM_KEYUP, _WM_SYSKEYUP):
            was_down, self.down = self.down, False
            return was_down and self.enabled and not injected
        if not (self.enabled and modifiers_down) or injected:
            return False
        self.down = True
        return True

    def start(self) -> bool:
        if os.name != "nt" or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="hayat-push-to-talk-hook", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return not self.error

    def _run(self) -> None:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        hookproc = ctypes.WINFUNCTYPE(lresult, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, hookproc, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = lresult
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        hook = None

        def callback(code, wparam, lparam):
            try:
                if code == 0:
                    data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    modifiers = all(user32.GetAsyncKeyState(key) & 0x8000 for key in self.modifiers)
                    if self.decide(int(data.vkCode), int(wparam), bool(data.flags & _LLKHF_INJECTED), modifiers):
                        return 1
            except Exception:  # noqa: S110 -- a hook must never raise into the keyboard pipeline
                pass
            return user32.CallNextHookEx(hook, code, wparam, lparam)

        self._proc = hookproc(callback)
        hook = user32.SetWindowsHookExW(13, self._proc, kernel32.GetModuleHandleW(None), 0)   # WH_KEYBOARD_LL
        if not hook:
            self.error = f"SetWindowsHookEx failed ({ctypes.get_last_error()})"
            self._ready.set()
            return
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(hook)

    def stop(self) -> None:
        self.enabled = False
        self.down = False
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)   # WM_QUIT
            self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = 0


class WindowsHotkey:
    def __init__(self, combination: str = "ctrl+space", *, exclusive: bool = True) -> None:
        parts = [part.strip().lower() for part in combination.split("+") if part.strip()]
        if not parts:
            raise ValueError("Push-to-talk hotkey cannot be empty.")
        try:
            self.keys = [_VIRTUAL_KEYS.get(part, ord(part.upper()) if len(part) == 1 else -1) for part in parts]
        except TypeError as exc:
            raise ValueError(f"Unsupported hotkey: {combination}") from exc
        if any(key < 0 for key in self.keys):
            raise ValueError(f"Unsupported hotkey: {combination}")
        self.combination = combination
        self._names = set(parts)
        self._pressed_names: set[str] = set()
        self._listener = None
        self._listener_error = ""
        self.claim = KeyboardClaim(self.keys) if exclusive and os.name == "nt" and any(
            key not in _MODIFIERS for key in self.keys) else None
        if os.name != "nt":
            self._start_portable_listener()

    def claim_keys(self, active: bool) -> bool:
        """Swallow the combination (push-to-talk on) or let it through again."""
        if self.claim is None:
            return False
        if active and not self.claim.enabled:
            self.claim.enabled = self.claim.start()
        elif not active:
            self.claim.enabled = False
            self.claim.down = False
        return self.claim.enabled

    def release(self) -> None:
        if self.claim is not None:
            self.claim.stop()

    @staticmethod
    def _portable_name(key: Any) -> str:
        char = getattr(key, "char", None)
        if char:
            return str(char).casefold()
        name = str(key).replace("Key.", "").casefold()
        aliases = {"ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
                   "shift_l": "shift", "shift_r": "shift",
                   "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt"}
        return aliases.get(name, name)

    def _start_portable_listener(self) -> None:
        try:
            from pynput import keyboard

            def down(key: Any) -> None:
                self._pressed_names.add(self._portable_name(key))

            def up(key: Any) -> None:
                self._pressed_names.discard(self._portable_name(key))

            self._listener = keyboard.Listener(on_press=down, on_release=up)
            self._listener.daemon = True
            self._listener.start()
        except Exception as exc:
            self._listener_error = f"{type(exc).__name__}: {exc}"

    def pressed(self) -> bool:
        if os.name != "nt":
            if self._listener_error:
                raise RuntimeError(
                    "Global push-to-talk is unavailable on this desktop; use hands-free mode"
                    f" ({self._listener_error})."
                )
            return self._names.issubset(self._pressed_names)
        get_state = ctypes.windll.user32.GetAsyncKeyState
        if self.claim is not None and self.claim.enabled:
            # The swallowed trigger's async state no longer changes; the hook's flag does.
            return self.claim.down and all(bool(get_state(key) & 0x8000) for key in self.claim.modifiers)
        return all(bool(get_state(key) & 0x8000) for key in self.keys)

    def wait_for_press(self, stop_event: Any, poll_seconds: float = 0.02) -> bool:
        """Block until the hotkey is pressed, or `stop_event` says give up.

        `stop_event` is anything with `is_set()`, not necessarily a real
        Event -- see AnyEvent. Waiting on shutdown alone is what made
        hands-free look broken: switching to continuous mode set a field the
        loop could not see, because the loop was parked in here until someone
        pressed a key they should not have needed to press.
        """
        while not stop_event.is_set():
            if self.pressed():
                return True
            time.sleep(poll_seconds)
        return False

    def wait_for_release(self, stop_event: Any, poll_seconds: float = 0.01) -> None:
        while self.pressed() and not stop_event.is_set():
            time.sleep(poll_seconds)


class AnyEvent:
    """Set when any of the events it was given is set.

    A blocking wait usually needs to end for more than one reason, and
    threading has no built-in way to wait on several events at once. Rather
    than teaching every waiter about every reason, they keep taking one
    "should I stop?" object and this composes the reasons behind it.
    """

    __slots__ = ("_events",)

    def __init__(self, *events: Any) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(event.is_set() for event in self._events)

    def wait(self, timeout: float | None = None) -> bool:
        """Poll rather than block, because there is nothing to block on.

        The interval is short enough that a mode change feels immediate and
        long enough that idling costs nothing measurable.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.02)
        return True
