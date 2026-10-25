from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from datetime import UTC, datetime
from typing import Any

from PIL import ImageGrab

from app.config import PROJECT_ROOT


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class DisplayManager:
    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32 if os.name == "nt" else None

    def displays(self) -> list[dict[str, Any]]:
        if self.user32 is None:
            try:
                import mss

                with mss.MSS() as capture:
                    rows = []
                    for index, monitor in enumerate(capture.monitors[1:], 1):
                        left, top = int(monitor["left"]), int(monitor["top"])
                        width, height = int(monitor["width"]), int(monitor["height"])
                        rows.append({
                            "index": index, "handle": index, "primary": index == 1,
                            "left": left, "top": top, "right": left + width,
                            "bottom": top + height, "width": width, "height": height,
                            "work_area": {"left": left, "top": top,
                                          "right": left + width, "bottom": top + height},
                        })
                    return rows
            except Exception:
                return []
        rows: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

        def callback(handle, _dc, _rect, _data):
            info = MONITORINFO(); info.cbSize = ctypes.sizeof(MONITORINFO)
            if self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect, work = info.rcMonitor, info.rcWork
                rows.append({
                    "index": len(rows) + 1, "handle": int(handle), "primary": bool(info.dwFlags & 1),
                    "left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom,
                    "width": rect.right - rect.left, "height": rect.bottom - rect.top,
                    "work_area": {"left": work.left, "top": work.top, "right": work.right, "bottom": work.bottom},
                })
            return 1

        procedure = callback_type(callback)
        self.user32.EnumDisplayMonitors(0, None, procedure, 0)
        return sorted(rows, key=lambda row: (not row["primary"], row["left"], row["top"]))

    def status(self) -> dict[str, Any]:
        rows = self.displays()
        return {"count": len(rows), "displays": rows, "active_window": self.active_window()}

    def active_window(self) -> dict[str, Any]:
        if self.user32 is None:
            return {"available": False, "reason": "native active-window metadata unavailable"}
        handle = self.user32.GetForegroundWindow()
        if not handle:
            return {"available": False}
        length = self.user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(handle, buffer, len(buffer))
        rect = RECT(); self.user32.GetWindowRect(handle, ctypes.byref(rect))
        return {"available": True, "handle": int(handle), "title": buffer.value, "left": rect.left, "top": rect.top, "right": rect.right, "bottom": rect.bottom}

    def screenshot(self, monitor: int = 1, path: str = "") -> dict[str, Any]:
        rows = self.displays()
        if monitor < 1 or monitor > len(rows):
            raise IndexError(f"Monitor {monitor} is unavailable; detected {len(rows)} monitor(s).")
        row = rows[monitor - 1]
        destination = (PROJECT_ROOT / (path or f"data/artifacts/monitor-{monitor}.png")).resolve()
        destination.relative_to(PROJECT_ROOT.resolve()); destination.parent.mkdir(parents=True, exist_ok=True)
        if self.user32 is None:
            import mss
            from PIL import Image

            with mss.MSS() as capture:
                raw = capture.grab({"left": row["left"], "top": row["top"],
                                    "width": row["width"], "height": row["height"]})
                image = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        else:
            image = ImageGrab.grab(
                bbox=(row["left"], row["top"], row["right"], row["bottom"]),
                all_screens=True,
            )
        image.save(destination)
        return {"path": str(destination), "monitor": monitor, "width": image.width,
                "height": image.height, "captured_at": datetime.now(UTC).isoformat(),
                "exists": destination.is_file()}

    def move_active_window(self, monitor: int) -> dict[str, Any]:
        rows = self.displays()
        if monitor < 1 or monitor > len(rows):
            raise IndexError(f"Monitor {monitor} is unavailable; detected {len(rows)} monitor(s).")
        window = self.active_window()
        if not window.get("available"):
            raise RuntimeError("No foreground window is available.")
        target = rows[monitor - 1]["work_area"]
        width = max(320, window["right"] - window["left"]); height = max(200, window["bottom"] - window["top"])
        width = min(width, target["right"] - target["left"]); height = min(height, target["bottom"] - target["top"])
        ok = self.user32.SetWindowPos(window["handle"], 0, target["left"], target["top"], width, height, 0x0004 | 0x0040)
        if not ok:
            raise ctypes.WinError()
        return {"moved": True, "monitor": monitor, "title": window["title"], "verified_window": self.active_window()}


def register_display_tools(registry, manager: DisplayManager) -> None:
    obj = lambda props=None, required=None: {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}
    registry.register("display_status", "Read real Windows monitor geometry and foreground-window metadata.", obj(), "low", "computer_observe", manager.status)
    registry.register("display_screenshot", "Capture a selected monitor to a project artifact.", obj({"monitor": {"type": "integer", "minimum": 1}, "path": {"type": "string"}}), "low", "computer_observe", manager.screenshot)
    registry.register("move_active_window_to_display", "Move the foreground window to a selected monitor and verify its resulting rectangle.", obj({"monitor": {"type": "integer", "minimum": 1}}, ["monitor"]), "medium", "computer_input", manager.move_active_window)
