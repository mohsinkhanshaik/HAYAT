"""Glassmorphism for HAYAT's window: a frosted "Glass" appearance and title bars that match it.

Tk cannot make a widget see-through or blur what is behind it, so real glass is
out of reach inside the window. What makes glass read as glass can be drawn:

* a deep, cool backdrop with frosted panels a few shades lighter than it;
* a soft coloured glow around each panel, as light passing through;
* a bright top edge with a short specular highlight, and a frosted rim whose
  tint fades from the top edge downward;
* a dark shadow line under each panel, so it floats.

Windows draws the title bar itself, so a light system bar sat on top of a dark
interface. :func:`style_title_bar` asks Windows (DWM) for a dark title bar in
the appearance's own colours, on the main window and every dialog.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

_log = logging.getLogger(__name__)

#: The Glass appearance. Every value is distinct, because switching
#: appearances recolours widgets by mapping each old colour to its new one.
GLASS = {
    "BLACK": "#070B17", "PANEL": "#131B30", "PANEL_2": "#1C2642", "PANEL_HOVER": "#26335A",
    "LINE": "#33446F", "WHITE": "#F3F6FF", "MUTED": "#A3AFD0", "GOLD": "#E8C46C",
    "GOLD_SOFT": "#F6DFA2", "ROYAL": "#3E6CFF", "ROYAL_SOFT": "#ADC4FF", "HOLO": "#8FE3FF",
    "OLIVE": "#4FAF8F", "OLIVE_SOFT": "#94E2C4", "ALERT": "#FF6B7B",
}


#: The second glow colour, opposite the cyan HOLO light.
GLOW_VIOLET = "#8B6CFF"


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def blend(first: str, second: str, amount: float) -> str:
    a, b = _rgb(first), _rgb(second)
    return "#" + "".join(f"{round(x + (y - x) * max(0.0, min(1.0, amount))):02x}" for x, y in zip(a, b))


def is_glass(palette: Any) -> bool:
    """Whether the palette is currently the Glass appearance."""
    return str(getattr(palette, "BLACK", "")).lower() == GLASS["BLACK"].lower()


def draw_glass_surface(canvas: Any, width: int, height: int, palette: Any) -> bool:
    """Draw one panel as frosted glass on its canvas; False when the appearance is not Glass."""
    if not is_glass(palette) or width < 24 or height < 24:
        return False
    from app.gui_depth import rounded

    p = palette
    w, h = width, height
    if live_glass_active():
        # The desktop shows frosted around the pane itself: no painted
        # backdrop or shadow, just the pane, its rim and its light.
        canvas.configure(bg=GLASS_KEY)
        rounded(canvas, (2, 2, w - 5, h - 7), fill=p.PANEL, outline=blend(p.LINE, p.HOLO, .35), width=1,
                tags="surface")
        for row in range(6):
            canvas.create_line(16, 3 + row, w - 19, 3 + row, fill=blend(p.PANEL, p.WHITE, .16 - row * .025),
                               tags=("surface", "glass-frost"))
        canvas.create_line(18, 3, w - 21, 3, fill=blend(p.PANEL, p.WHITE, .30), tags=("surface", "light"))
        canvas.create_line(22, 3, 22 + max(30, (w - 44) * .28), 3, fill=blend(p.PANEL, p.WHITE, .62),
                           tags=("surface", "glass-sheen"))
        return True
    # The aurora behind the pane, so the glow runs on behind its edges instead
    # of stopping at a black margin.
    try:
        root = canvas.winfo_toplevel()
        from PIL import ImageTk

        image = aurora_image((w, h), (root.winfo_width(), root.winfo_height()),
                             (canvas.winfo_rootx() - root.winfo_rootx(), canvas.winfo_rooty() - root.winfo_rooty()), p)
        canvas._glass_photo = ImageTk.PhotoImage(image, master=canvas)
        canvas.create_image(0, 0, image=canvas._glass_photo, anchor="nw", tags=("surface", "glass-aurora"))
    except Exception:
        from app.logging import log_swallowed

        log_swallowed(_log, "painting the aurora behind a glass panel")
    # The shadow the pane casts, then the pane with a coloured edge glow.
    rounded(canvas, (6, 10, w - 2, h - 2), fill=blend(p.BLACK, "#000000", .55), outline="", tags="surface")
    # The pane itself, with a frosted rim lighter at the top edge.
    rounded(canvas, (2, 2, w - 5, h - 7), fill=p.PANEL, outline=blend(p.LINE, p.HOLO, .25), width=1,
            tags="surface")
    for row in range(6):
        canvas.create_line(16, 3 + row, w - 19, 3 + row, fill=blend(p.PANEL, p.WHITE, .16 - row * .025),
                           tags=("surface", "glass-frost"))
    # A bright top edge with a short specular highlight near the left.
    canvas.create_line(18, 3, w - 21, 3, fill=blend(p.PANEL, p.WHITE, .30), tags=("surface", "light"))
    canvas.create_line(22, 3, 22 + max(30, (w - 44) * .28), 3, fill=blend(p.PANEL, p.WHITE, .62),
                       tags=("surface", "glass-sheen"))
    # A faint cool reflection along the bottom rim, and the shadow it casts.
    canvas.create_line(20, h - 9, w - 24, h - 9, fill=blend(p.PANEL, p.HOLO, .14), tags=("surface", "glass-rim"))
    return True


# ------------------------------------------------------------------ title bars

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(color: str) -> int:
    red, green, blue = _rgb(color)
    return red | (green << 8) | (blue << 16)


def style_title_bar(window: Any, palette: Any) -> bool:
    """Give a window's title bar the appearance's colours (Windows 10/11); False where it cannot."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        dwm = ctypes.windll.dwmapi

        def setting(attribute: int, value: int) -> bool:
            data = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)) == 0

        dark = sum(_rgb(palette.BLACK)) < 384
        if not setting(_DWMWA_USE_IMMERSIVE_DARK_MODE, int(dark)):
            setting(_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, int(dark))
        # Caption, text and border colours exist from Windows 11; older systems ignore them.
        setting(_DWMWA_CAPTION_COLOR, _colorref(palette.BLACK))
        setting(_DWMWA_TEXT_COLOR, _colorref(palette.WHITE))
        setting(_DWMWA_BORDER_COLOR, _colorref(blend(palette.LINE, palette.GOLD, .35)))
        return True
    except Exception:
        from app.logging import log_swallowed

        log_swallowed(_log, "styling a window title bar")
        return False


def install_title_bars(root: Any, palette: Any) -> None:
    """Style the main window now, and every dialog the moment it first appears."""
    style_title_bar(root, palette)
    apply_window_glass(root, palette)
    if getattr(root, "_hayat_title_bars", False):
        return
    root._hayat_title_bars = True

    def styled(event: Any) -> None:
        widget = event.widget
        if isinstance(widget, str) or getattr(widget, "_hayat_title_styled", None) == palette.BLACK:
            return
        try:
            if widget.winfo_toplevel() is widget:
                widget._hayat_title_styled = palette.BLACK
                style_title_bar(widget, palette)
        except Exception:
            from app.logging import log_swallowed

            log_swallowed(_log, "styling a dialog title bar")

    root.bind_class("Toplevel", "<Map>", styled, add="+")


# ------------------------------------------------------------------ aurora backdrop

#: Soft light behind the glass, laid out over the whole window as
#: (x fraction, y fraction, radius as a fraction of width, colour key, alpha).
_AURORA = (
    (0.12, 0.18, 0.38, "HOLO", 58),
    (0.88, 0.80, 0.42, "VIOLET", 64),
    (0.55, 0.02, 0.30, "GOLD", 24),
    (0.38, 0.98, 0.34, "OLIVE", 40),
    (0.97, 0.10, 0.22, "ROYAL", 52),
)
_SCALE = 6


def wallpaper_path() -> Any:
    """The desktop wallpaper, which frosted glass is made from; None when there is no usable picture."""
    if sys.platform != "win32":
        return None
    import os
    from pathlib import Path

    candidates = []
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(520)
        if ctypes.windll.user32.SystemParametersInfoW(0x73, 520, buffer, 0):  # SPI_GETDESKWALLPAPER
            candidates.append(Path(buffer.value))
    except Exception:
        from app.logging import log_swallowed

        log_swallowed(_log, "reading the desktop wallpaper setting")
    candidates.append(Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Themes" / "TranscodedWallpaper")
    for candidate in candidates:
        try:
            if str(candidate) and candidate.is_file() and candidate.stat().st_size > 1024:
                return candidate
        except OSError:
            continue
    return None


_BACKDROPS: dict[Any, Any] = {}
#: How strongly the blurred wallpaper is tinted towards the appearance's
#: backdrop colour: enough that white text stays crisp on top of it.
WALLPAPER_TINT = 0.5


def window_backdrop(window: tuple[int, int], palette: Any) -> Any:
    """The whole window's glass backdrop as one PIL image, built once per window size.

    Real see-through showed every window behind HAYAT sharply, because Tk can
    neither blur nor make only the background transparent. Frosted glass is
    made the way Windows 11 makes its own: from the desktop wallpaper, heavily
    blurred and tinted, with a soft aurora glow over it. Without a usable
    wallpaper it is the aurora alone.
    """
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    win_w, win_h = max(1, int(window[0])), max(1, int(window[1]))
    paper = wallpaper_path()
    try:
        stamp = paper.stat().st_mtime if paper is not None else 0
    except OSError:
        paper, stamp = None, 0
    key = (win_w, win_h, palette.BLACK, str(paper), stamp)
    if key in _BACKDROPS:
        return _BACKDROPS[key]
    small_size = (max(1, win_w // _SCALE), max(1, win_h // _SCALE))
    base = Image.new("RGB", small_size, _rgb(palette.BLACK))
    if paper is not None:
        try:
            with Image.open(paper) as picture:
                fitted = ImageOps.fit(picture.convert("RGB"), small_size, Image.BILINEAR)
            fitted = fitted.filter(ImageFilter.GaussianBlur(radius=max(3, small_size[0] // 45)))
            base = Image.blend(fitted, base, WALLPAPER_TINT)
        except Exception:
            from app.logging import log_swallowed

            log_swallowed(_log, "frosting the desktop wallpaper")
            paper = None
    glow = Image.new("RGBA", small_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    colours = {"HOLO": palette.HOLO, "VIOLET": GLOW_VIOLET, "GOLD": palette.GOLD, "OLIVE": palette.OLIVE,
               "ROYAL": palette.ROYAL}
    strength = 0.55 if paper is not None else 1.0
    for fx, fy, fr, name, alpha in _AURORA:
        cx, cy, radius = fx * small_size[0], fy * small_size[1], fr * small_size[0]
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     fill=(*_rgb(colours[name]), int(alpha * strength)))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(4, small_size[0] // 9)))
    base = Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")
    image = base.resize((win_w, win_h), Image.BILINEAR)
    if len(_BACKDROPS) >= 4:
        _BACKDROPS.clear()
    _BACKDROPS[key] = image
    return image


def aurora_image(size: tuple[int, int], window: tuple[int, int], offset: tuple[int, int], palette: Any) -> Any:
    """One container's slice of the window's glass backdrop (a PIL image)."""
    width, height = max(1, size[0]), max(1, size[1])
    full = window_backdrop(window, palette)
    left, top = int(offset[0]), int(offset[1])
    return full.crop((left, top, left + width, top + height))


class AuroraBackdrop:
    """A blurred aurora behind one container's contents, shown only in the Glass appearance."""

    def __init__(self, root: Any, container: Any, palette: Any) -> None:
        import tkinter as tk

        self.root, self.container, self.palette = root, container, palette
        self.label = tk.Label(container, bd=0, highlightthickness=0, bg=palette.BLACK)
        self.photo: Any = None
        self._pending: Any = None
        self._signature: Any = None
        wheel = container.bind("<MouseWheel>")
        if wheel:
            self.label.bind("<MouseWheel>", wheel)
        container.bind("<Configure>", lambda _event: self.schedule(), add="+")

    def schedule(self) -> None:
        if self._pending is not None:
            try:
                self.root.after_cancel(self._pending)
            except Exception:
                from app.logging import log_swallowed

                log_swallowed(_log, "cancelling a backdrop repaint")
        self._pending = self.root.after(180, self.paint)

    def paint(self) -> None:
        self._pending = None
        try:
            if not self.container.winfo_exists():
                return
            if not is_glass(self.palette) or live_glass_active():
                self.label.place_forget()
                self._signature = None
                return
            size = (self.container.winfo_width(), self.container.winfo_height())
            window = (self.root.winfo_width(), self.root.winfo_height())
            offset = (self.container.winfo_rootx() - self.root.winfo_rootx(),
                      self.container.winfo_rooty() - self.root.winfo_rooty())
            signature = (size, window, offset, self.palette.BLACK)
            if size[0] < 8 or size[1] < 8 or signature == self._signature:
                return
            self._signature = signature
            from PIL import ImageTk

            self.photo = ImageTk.PhotoImage(aurora_image(size, window, offset, self.palette), master=self.label)
            self.label.configure(image=self.photo, bg=self.palette.BLACK)
            self.label.place(x=0, y=0, relwidth=1, relheight=1)
            self.label.lower()
        except Exception:
            from app.logging import log_swallowed

            log_swallowed(_log, "painting the glass backdrop")


def register_backdrops(root: Any, containers: list[Any], palette: Any) -> None:
    """Give these containers an aurora behind their contents; repainted whenever the appearance changes."""
    backdrops = getattr(root, "_hayat_backdrops", [])
    known = {id(item.container) for item in backdrops}
    for container in containers:
        if container is not None and id(container) not in known:
            backdrops.append(AuroraBackdrop(root, container, palette))
    root._hayat_backdrops = backdrops
    refresh_backdrops(root)


def refresh_backdrops(root: Any) -> None:
    for backdrop in getattr(root, "_hayat_backdrops", []):
        backdrop.schedule()


# ------------------------------------------------------------------ real see-through

#: How opaque the window is in the Glass appearance. Solid by default: any
#: real transparency showed the windows behind HAYAT sharply (at 50% the owner
#: found it "way too transparent"), and the frosted wallpaper backdrop gives
#: the glass look without that clutter.
#: Override with "glass_opacity" in data/memory/appearance.json, or with the
#: Transparency slider in Appearance. Below 0.4 text becomes hard to read.
GLASS_ALPHA = 1.0
MIN_GLASS_OPACITY = 0.4


def glass_opacity(root: Any = None) -> float:
    """The saved Glass opacity, clamped to stay readable."""
    try:
        import json

        from app.config import PROJECT_ROOT

        saved = json.loads((PROJECT_ROOT / "data" / "memory" / "appearance.json").read_text(encoding="utf-8"))
        return max(MIN_GLASS_OPACITY, min(1.0, float(saved.get("glass_opacity", GLASS_ALPHA))))
    except (OSError, ValueError, TypeError, AttributeError):
        return GLASS_ALPHA


#: How much of the Glass backdrop colour tints the frosted desktop (0-255). At
#: 0xCC the glass read as plain black over a dark desktop; about a third lets
#: the blurred colours behind HAYAT come through while text stays crisp.
GLASS_TINT_ALPHA = 0x58


def _set_acrylic(window: Any, enabled: bool, tint: str) -> bool:
    """Ask Windows to blur whatever is behind the window (acrylic); False where it cannot."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class AccentPolicy(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", wintypes.DWORD), ("AnimationId", ctypes.c_int)]

        class CompositionData(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(AccentPolicy)),
                        ("SizeOfData", ctypes.c_size_t)]

        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        red, green, blue = _rgb(tint)
        policy = AccentPolicy(4 if enabled else 0, 2, (GLASS_TINT_ALPHA << 24) | (blue << 16) | (green << 8) | red, 0)
        data = CompositionData(19, ctypes.pointer(policy), ctypes.sizeof(policy))  # WCA_ACCENT_POLICY
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    except Exception:
        from app.logging import log_swallowed

        log_swallowed(_log, "setting the acrylic backdrop")
        return False


#: The colour Windows treats as see-through in the Glass window. Every
#: background area painted in it shows the desktop behind, frosted by acrylic.
#: Close to the Glass backdrop but distinct from every palette colour.
GLASS_KEY = "#03060E"
_LIVE = {"active": False}
#: ttk styles whose background is the backdrop and should turn to glass too.
_GLASS_STYLES = ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label", "TNotebook", "Workspace.TFrame",
                 "Workspace.TLabel", "TCheckbutton", "TRadiobutton")


def live_glass_active() -> bool:
    """Whether the window's background areas are real frosted glass right now."""
    return bool(_LIVE["active"])


def apply_window_glass(window: Any, palette: Any) -> dict[str, Any]:
    """Frosted glass in the Glass appearance: the desktop behind HAYAT, blurred, wherever there is no panel.

    Plain transparency showed every window behind HAYAT sharply ("way too
    transparent"). A transparent colour key on its own does the same. With
    Windows' acrylic behind it, those areas become real frosted glass while
    panels and text stay solid -- checked live on 2026-10-04. Acrylic does not
    combine with whole-window transparency, so moving the transparency slider
    above 0% switches to plain see-through instead.
    """
    glass = is_glass(palette)
    alpha = glass_opacity() if glass else 1.0
    result = {"glass": glass, "alpha": alpha, "acrylic": False}
    try:
        window.attributes("-alpha", alpha)
    except Exception:
        from app.logging import log_swallowed

        log_swallowed(_log, "setting the window's transparency")
    live = False
    if glass and alpha >= 0.999 and sys.platform == "win32":
        try:
            window.attributes("-transparentcolor", GLASS_KEY)
            live = _set_acrylic(window, True, palette.BLACK)
        except Exception:
            live = False
    if not live:
        try:
            window.attributes("-transparentcolor", "")
        except Exception:
            from app.logging import log_swallowed

            log_swallowed(_log, "clearing the glass colour key")
        _set_acrylic(window, False, palette.BLACK)
    _LIVE["active"] = live
    result["acrylic"] = live
    _glassify(window, palette, live)
    return result


def _glassify(root: Any, palette: Any, live: bool) -> None:
    """Turn every backdrop-coloured background in the main window into glass, or back."""
    import tkinter as tk
    from tkinter import ttk

    black = str(palette.BLACK).lower()

    def walk(widget: Any) -> None:
        if widget is not root and isinstance(widget, tk.Toplevel):
            return  # dialogs are their own windows and keep their own look
        try:
            current = str(widget.cget("background")).lower()
            if live and current == black:
                widget.configure(background=GLASS_KEY)
                widget._hayat_glass = True
            elif not live and getattr(widget, "_hayat_glass", False):
                widget.configure(background=palette.BLACK)
                widget._hayat_glass = False
        except Exception:  # noqa: S110 - ttk widgets and some composites have no plain background option
            pass
        for child in widget.winfo_children():
            walk(child)
        refresh = getattr(widget, "refresh_theme", None)
        if callable(refresh) and hasattr(widget, "_surface_signature"):
            widget._surface_signature = None
            refresh()

    walk(root)
    style = ttk.Style(root)
    for name in _GLASS_STYLES:
        try:
            value = str(style.lookup(name, "background")).lower()
            if live and value == black:
                style.configure(name, background=GLASS_KEY)
            elif not live and value == GLASS_KEY.lower():
                style.configure(name, background=palette.BLACK)
        except Exception:  # noqa: S110 - a style the current theme does not define
            pass
    if not getattr(root, "_hayat_glass_map", False):
        root._hayat_glass_map = True

        def mapped(event: Any) -> None:
            widget = event.widget
            if not live_glass_active() or isinstance(widget, str):
                return
            try:
                if widget.winfo_toplevel() is root and str(widget.cget("background")).lower() == \
                        str(palette.BLACK).lower():
                    widget.configure(background=GLASS_KEY)
                    widget._hayat_glass = True
            except Exception:  # noqa: S110 - widgets without a background option
                pass

        for cls in ("Frame", "Label", "Canvas", "Text", "Listbox"):
            root.bind_class(cls, "<Map>", mapped, add="+")
