"""Motion for HAYAT's desktop windows: eased, pixel-smooth wheel scrolling.

Tk scrolls a text or canvas by whole lines or units, so every wheel notch was a jump.
`SmoothScroller` moves the view toward a target a fraction of the remaining distance
each frame, which reads as one continuous glide; further notches extend the target
instead of queueing jumps. Dragging the scrollbar cancels a glide in progress.

`fade_in` eases a newly opened window in from transparent (about 0.15 s) instead of
having it pop up fully drawn.

Motion follows Windows' own "Animation effects" setting, and `set_motion(False)`
turns every glide into an instant move and every window entrance into an instant one.
"""

from __future__ import annotations

import logging
import itertools
import sys
import time
import tkinter as tk
from contextlib import contextmanager
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MOTION = {"enabled": True}
_anchors = itertools.count()


def system_allows_motion() -> bool:
    """Windows "Show animations in Windows" (SPI_GETCLIENTAREAANIMATION); True elsewhere."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        value = ctypes.c_bool(True)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except Exception:
        log_swallowed(_log, "reading the Windows animation setting")
    return True


def set_motion(enabled: bool) -> None:
    MOTION["enabled"] = bool(enabled)


class SmoothScroller:
    STEP_PX = 72      # one wheel notch
    FRAME_MS = 15
    EASE = 0.3        # share of the remaining distance covered each frame

    def __init__(self, widget: Any) -> None:
        self.widget = widget
        self.target: float | None = None
        self.last_set: float | None = None
        self.job: str | None = None

    def _span(self) -> tuple[float, float, float]:
        first, last = self.widget.yview()
        visible = max(1e-6, last - first)
        return first, visible, max(1.0, self.widget.winfo_height() / visible)

    def wheel(self, event: Any) -> str:
        delta = getattr(event, "delta", 0)
        if not delta:
            delta = {4: 120, 5: -120}.get(getattr(event, "num", 0), 0)
        elif sys.platform == "darwin":
            delta *= 120  # Tk reports trackpad deltas in units on macOS.
        if not delta:
            return "break"
        try:
            first, visible, total = self._span()
        except tk.TclError:
            return "break"
        if visible >= 0.999:
            return "break"  # everything is already in view
        if self.target is not None and self.last_set is not None and abs(first - self.last_set) > 1e-4:
            self.target = None  # the scrollbar was dragged mid-glide: start from where the view is now
        if self.target is None:
            # A completed glide must not keep ownership of its old position.
            # Appending a reply, resizing or dragging the bar can move the
            # view between gestures. Comparing that new view to last_set in
            # _tick otherwise cancels every future wheel event indefinitely.
            self.last_set = first
        base = self.target if self.target is not None else first
        self.target = min(max(0.0, base - (delta / 120) * self.STEP_PX / total), 1.0 - visible)
        if not MOTION["enabled"]:
            self._move(self.target)
            self.target = None
        elif self.job is None:
            self.job = self.widget.after(self.FRAME_MS, self._tick)
        return "break"

    def _move(self, fraction: float) -> None:
        self.widget.yview_moveto(fraction)
        self.last_set = self.widget.yview()[0]

    def _tick(self) -> None:
        self.job = None
        if self.target is None:
            return
        try:
            first, _visible, total = self._span()
            if self.last_set is not None and abs(first - self.last_set) > 1e-4:
                self.target = None  # the owner took over with the scrollbar
                return
            gap = self.target - first
            if abs(gap) * total < 2.0:
                self._move(self.target)
                self.target = None
                return
            self._move(first + gap * self.EASE)
            if abs(self.last_set - first) < 1e-7:
                # Text views snap to whole pixels; a step too small to move ends the glide.
                self._move(self.target)
                self.target = None
                return
        except tk.TclError:
            self.target = None
            return
        self.job = self.widget.after(self.FRAME_MS, self._tick)

    def cancel(self) -> None:
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
        self.job, self.target, self.last_set = None, None, None


FADE_MS = 150
FRAME_MS = 15


def fade_in(window: Any, duration_ms: int = FADE_MS) -> bool:
    """Ease a window in from transparent the first time it appears; True when a fade started.

    Skipped when motion is off and in the Glass appearance: its acrylic blur needs a fully
    opaque window, and a colour-keyed window (`-transparentcolor`) is already layered.
    """
    if not MOTION["enabled"] or getattr(window, "_hayat_faded", False):
        return False
    window._hayat_faded = True
    try:
        if str(window.wm_attributes("-transparentcolor") or ""):
            return False
        window.wm_attributes("-alpha", 0.0)
    except tk.TclError:  # not Windows, or the window is already gone
        return False
    started = time.monotonic()

    def step() -> None:
        try:
            if not window.winfo_exists():
                return
            progress = min(1.0, (time.monotonic() - started) * 1000 / max(1, duration_ms))
            # Ease-out cubic: quick to appear, gentle to settle. The end is exactly 1.0, which also
            # drops Windows' layered style so nothing stays half-transparent.
            window.wm_attributes("-alpha", 1.0 if progress >= 1 else 1 - (1 - progress) ** 3)
            if progress < 1:
                window.after(FRAME_MS, step)
        except tk.TclError:
            pass

    window.after_idle(step)
    return True


def attach(widget: Any) -> SmoothScroller:
    """Give a Text, Canvas or Listbox eased wheel scrolling (replacing its jumpy default)."""
    existing = getattr(widget, "_smooth_scroller", None)
    if existing is not None:
        return existing
    scroller = SmoothScroller(widget)
    widget.bind("<MouseWheel>", scroller.wheel)
    widget.bind("<Button-4>", scroller.wheel)
    widget.bind("<Button-5>", scroller.wheel)
    widget._smooth_scroller = scroller
    widget.bind("<Destroy>", lambda event: scroller.cancel() if event.widget is widget else None, add="+")
    # Embedded image/document/player cards are child windows of the chat Text.
    # Their labels and buttons must route wheel events to that nearest parent.
    top = widget.winfo_toplevel()
    if not getattr(top, "_hayat_wheel_routing", False):
        top._hayat_wheel_routing = True
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            top.bind(sequence, _wheel_from_child, add="+")
    return scroller


def _wheel_from_child(event: Any) -> str | None:
    current = event.widget
    while current is not None:
        scroller = getattr(current, "_smooth_scroller", None)
        if scroller is not None:
            return scroller.wheel(event)
        # An independent editor, map, list or value control owns its wheel.
        if current.winfo_class() in {"Text", "Canvas", "Listbox", "Treeview", "Scale", "TScale", "TCombobox", "Spinbox", "TSpinbox"}:
            return None
        current = getattr(current, "master", None)
    return None


def near_bottom(widget: Any) -> bool:
    first, last = widget.yview()
    visible = max(1e-6, last - first)
    scroller = getattr(widget, "_smooth_scroller", None)
    target = getattr(scroller, "target", None)
    # A percentage of a long transcript can span several screens. Following
    # within its last 2% pulled the reader back down after every new message.
    if target is not None:
        if target < first:
            return False  # honour an upward wheel before its first frame
        last = target + last - first
    total = max(1.0, widget.winfo_height() / visible)
    return (1.0 - last) * total <= 24


@contextmanager
def reading_position(widget: Any, *, follow: bool | None = None):
    """Keep the top visible line anchored as asynchronous content is inserted."""
    follow = near_bottom(widget) if follow is None else follow
    anchor = f"hayat-view-{next(_anchors)}"
    widget.mark_set(anchor, widget.index("@0,0"))
    widget.mark_gravity(anchor, "left")
    try:
        yield follow
    finally:
        try:
            if follow:
                scroller = getattr(widget, "_smooth_scroller", None)
                if scroller is not None:
                    scroller.cancel()
                widget.see("end")
            else:
                widget.yview(anchor)
            widget.mark_unset(anchor)
        except tk.TclError:
            pass  # The window can be closed while a worker result is queued.
