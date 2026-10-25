"""Where HAYAT's pop-up windows open.

Tk leaves a new Toplevel to Windows, which cascades it from the top-left
corner of the screen. With Voice Access (or any bar) docked at the top, the
Vision window's title bar -- and its only close button -- opened underneath
that bar, over half of the main window, with no visible way out. Every
pop-up is now centred over the window that opened it and kept inside the
usable area of that window's monitor, which excludes docked bars and the
taskbar.
"""
from __future__ import annotations

import sys
import tkinter as tk

#: Room left between a pop-up and the edge of the usable area.
MARGIN = 12


def work_area(widget: tk.Misc) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the usable area of ``widget``'s monitor.

    On Windows this is the monitor's work area, which already leaves out the
    taskbar and docked application bars. Elsewhere, or if Windows cannot
    say, the whole screen.
    """
    fallback = (0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight())
    if sys.platform != "win32":
        return fallback
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        top = widget.winfo_toplevel()
        frame = int(top.wm_frame(), 16) or top.winfo_id()
        monitor = ctypes.windll.user32.MonitorFromWindow(wintypes.HWND(frame), 2)  # nearest
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not monitor or not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return fallback
        work = info.rcWork
        if work.right - work.left < 200 or work.bottom - work.top < 200:
            return fallback
        return work.left, work.top, work.right, work.bottom
    except Exception:
        return fallback


def placement(size: tuple[int, int], parent: tuple[int, int, int, int],
              area: tuple[int, int, int, int]) -> str:
    """A ``WxH+X+Y`` geometry centred over ``parent`` and wholly inside ``area``.

    ``parent`` is (x, y, width, height) of the opening window; ``area`` is
    (left, top, right, bottom). The pop-up's top edge -- where its title bar
    and close button are -- is never above the usable area.
    """
    left, top, right, bottom = area
    width = max(120, min(int(size[0]), right - left - 2 * MARGIN))
    height = max(80, min(int(size[1]), bottom - top - 2 * MARGIN - 40))
    px, py, pw, ph = parent
    x = px + (pw - width) // 2
    y = py + max(0, (ph - height) // 3)
    x = max(left + MARGIN, min(x, right - width - MARGIN))
    y = max(top + MARGIN, min(y, bottom - height - MARGIN - 40))
    return f"{width}x{height}+{x}+{y}"


def cancel_pending_reveal(window: tk.Misc) -> None:
    """Keep a deliberately closed window closed after placement settles."""
    handle = getattr(window, "_placement_reveal_handle", None)
    window._placement_reveal_handle = None
    if handle is not None:
        try:
            window.after_cancel(handle)
        except tk.TclError:
            pass


def place_over(window: tk.Toplevel, parent: tk.Misc, size: tuple[int, int] | None = None) -> None:
    """Size and position ``window`` over ``parent``, inside the usable area."""
    try:
        cancel_pending_reveal(window)
        opener = parent.winfo_toplevel()
        opener.update_idletasks()
        window.update_idletasks()
        wanted = size or (window.winfo_reqwidth(), window.winfo_reqheight())
        frame = (opener.winfo_rootx(), opener.winfo_rooty(), opener.winfo_width(), opener.winfo_height())
        if frame[2] < 50 or frame[3] < 50 or not opener.winfo_viewable():
            # The opener is hidden: centre on its monitor instead.
            area = work_area(opener)
            frame = (area[0], area[1], area[2] - area[0], area[3] - area[1])
        window.geometry(placement(wanted, frame, work_area(opener)))
        # Windows may deliver an older parent-unmap event after a child was
        # deiconified, withdrawing that new transient again. Recheck once
        # after native mapping events settle, without opening hidden parents.
        def reveal() -> None:
            window._placement_reveal_handle = None
            try:
                if window.winfo_exists() and opener.winfo_exists() and opener.state() != "withdrawn" and window.state() == "withdrawn":
                    window.deiconify()
            except tk.TclError:
                pass
        window._placement_reveal_handle = window.after(80, reveal)
        if not getattr(window, "_placement_reveal_bound", False):
            def cancel_reveal(event):
                if event.widget is window:
                    cancel_pending_reveal(window)
            window.bind('<Destroy>', cancel_reveal, add='+')
            window._placement_reveal_bound = True
    except tk.TclError:
        pass
