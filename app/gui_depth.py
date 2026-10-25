"""Local canvas surfaces and a perspective workspace navigator for Tk."""
from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Any, Callable


def blend(first: str, second: str, amount: float) -> str:
    """Blend palette colours without a graphics dependency."""
    a = tuple(int(first[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(second[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def rounded(canvas: tk.Canvas, box: tuple[float, float, float, float], radius: float = 14,
            **options: Any) -> int:
    x, y, right, bottom = box
    r = min(radius, max(0, (right - x) / 2), max(0, (bottom - y) / 2))
    return canvas.create_polygon(
        x + r, y, right - r, y, right, y, right, y + r,
        right, bottom - r, right, bottom, right - r, bottom,
        x + r, bottom, x, bottom, x, bottom - r, x, y + r, x, y,
        smooth=True, splinesteps=16, **options,
    )


class DepthSurface(tk.Canvas):
    """Frosted, softly lit panel hosting ordinary accessible Tk controls.

    Body height drives the panel's requested height, so text and controls still
    participate in Tk layout. Shadows use reserved space and never move targets.
    Tk child controls remain opaque: blended tints and static rim reflections
    simulate glass without desktop capture, window alpha or a blur timer.
    """

    def __init__(self, parent: tk.Misc, palette: Any, *, padding: int = 12) -> None:
        super().__init__(parent, bg=palette.BLACK, bd=0, highlightthickness=0,
                         width=200, height=50)
        self.palette = palette
        self.body = tk.Frame(self, bg=palette.PANEL, padx=padding, pady=padding)
        self.content = self.body
        self._body_window = self.create_window(8, 7, window=self.body, anchor="nw")
        self._surface_signature = None
        self._light_at = 0.0
        self._remap: str | None = None
        self._pointer_tag = f"depth-light-{self}"
        self.bind_class(self._pointer_tag, "<Motion>", self._light)
        self.bind_class(self._pointer_tag, "<Leave>", self._unlight)
        self.body.bind("<Configure>", self._fit, add="+")
        self.bind("<Configure>", self._resize)
        self.bind("<Map>", self._bind_pointer)
        self.bind("<Destroy>", self._dispose)

    def _bind_pointer(self, _event: Any = None) -> None:
        self.itemconfigure(self._body_window, state="normal")
        if self._remap is None:
            self._remap = self.after_idle(self._restore_body)
        def visit(widget: tk.Misc) -> None:
            tags = widget.bindtags()
            if self._pointer_tag not in tags:
                widget.bindtags((*tags[:-1], self._pointer_tag, tags[-1]))
            for child in widget.winfo_children():
                visit(child)
        visit(self)

    def _restore_body(self) -> None:
        self._remap = None
        self.itemconfigure(self._body_window, state="normal")

    def _light(self, event: Any) -> None:
        now = time.monotonic()
        if now - self._light_at < .035:
            return
        self._light_at = now
        w = self.winfo_width()
        if w < 50:
            return
        x = max(22, min(w-22, event.x_root-self.winfo_rootx()))
        self.coords("light", max(19, x-45), 4, min(w-22, x+45), 4)
        self.itemconfigure("light", fill=blend(self.palette.ROYAL_SOFT, self.palette.WHITE, .3))

    def _unlight(self, event: Any) -> None:
        x, y = event.x_root-self.winfo_rootx(), event.y_root-self.winfo_rooty()
        if not (0 <= x < self.winfo_width() and 0 <= y < self.winfo_height()):
            self.coords("light", 19, 4, self.winfo_width()-22, 4)
            self.itemconfigure("light", fill=blend(self.palette.PANEL, self.palette.WHITE, .16))

    def _dispose(self, event: Any) -> None:
        if event.widget is self:
            if self._remap is not None:
                self.after_cancel(self._remap)
                self._remap = None
            self.unbind_class(self._pointer_tag, "<Motion>")
            self.unbind_class(self._pointer_tag, "<Leave>")

    def _fit(self, _event: Any = None) -> None:
        wanted = self.content.winfo_reqheight() + 18
        if int(float(self.cget("height"))) != wanted:
            self.configure(height=wanted)

    def _resize(self, event: Any) -> None:
        self.itemconfigure(self._body_window, width=max(1, event.width - 16))
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = self.palette
        from app.gui_glass import GLASS_KEY, is_glass, live_glass_active

        self.configure(bg=GLASS_KEY if live_glass_active() and is_glass(p) else p.BLACK)
        self.content.configure(bg=p.PANEL)
        w, h = self.winfo_width(), self.winfo_height()
        if w < 24 or h < 24:
            return
        signature = (w, h, p.BLACK, p.PANEL, p.LINE, p.WHITE, p.ROYAL_SOFT, p.GOLD_SOFT, p.OLIVE_SOFT)
        if signature == self._surface_signature:
            return
        self._surface_signature = signature
        self.delete("surface")
        from app.gui_glass import draw_glass_surface

        if draw_glass_surface(self, w, h, p):
            self.tag_lower("surface")
            return
        light_surface = sum(int(p.BLACK[i:i + 2], 16) for i in (1, 3, 5)) > 384
        high_contrast = p.BLACK.lower() == '#000000'
        rounded(self, (5, 9, w - 2, h - 1), fill=blend(p.BLACK, "#000000", .07 if light_surface else .35),
                outline="", tags="surface")
        if not high_contrast:
            rounded(self, (3, 5, w - 3, h - 3), fill=blend(p.BLACK, p.ROYAL_SOFT, .04),
                    outline='', tags='surface')
        rounded(self, (2, 2, w - 5, h - 7), fill=p.PANEL,
                outline=p.LINE if high_contrast else blend(p.LINE, p.WHITE, .12), width=1, tags="surface")
        if not high_contrast:
            rounded(self, (3, 3, w - 6, h - 8), fill='',
                    outline=blend(p.PANEL, p.WHITE, .12), width=1, tags=('surface', 'glass-edge'))
            # Bounded static reflections: gold → olive → royal blue across the
            # exposed rim. The real controls cover the middle of the panel.
            for index in range(12):
                fraction = index / 11
                tint = (blend(p.GOLD_SOFT, p.OLIVE_SOFT, fraction * 2) if fraction <= .5
                        else blend(p.OLIVE_SOFT, p.ROYAL_SOFT, (fraction - .5) * 2))
                left = 20 + (w - 43) * index / 12
                right = 20 + (w - 43) * (index + 1) / 12
                self.create_line(left, 5, right, 5, fill=blend(p.PANEL, tint, .23),
                                 tags=('surface', 'glass-reflection'))
            self.create_line(20, h - 8, w - 24, h - 8, fill=blend(p.PANEL, p.ROYAL_SOFT, .1),
                             tags=('surface', 'glass-reflection'))
        self.create_line(19, 4, w - 22, 4, fill=blend(p.PANEL, p.WHITE, .16), tags=("surface", "light"))
        self.tag_lower("surface")


class DepthNavigator(tk.Canvas):
    """A bounded 3D globe with clear, stable shortcuts supplied by its parent.

    Direct manipulation -- drag, wheel, keys -- always applies at once. With
    motion enabled the globe also idles, coasts after a flick, eases round to
    face the chosen destination and pulses when one opens. With motion off
    each of those snaps or is skipped, so reduced motion never costs a feature.
    """

    VIEWS = (
        ("chat", "Chat"), ("operator", "System map"), ("memory", "Memory"),
        ("tasks", "Tasks"), ("gallery", "Gallery"), ("tools", "Tools"),
        ("health", "Health"),
    )
    DESCRIPTIONS = {
        "chat": "Ask a question or describe what you want to do.",
        "operator": "Inspect system checks and how HAYAT's parts connect.",
        "memory": "Find saved knowledge and earlier discoveries.",
        "tasks": "Follow work in progress and review its outcome.",
        "gallery": "Browse images you created or added.",
        "tools": "See the tools available to help with your work.",
        "health": "Check local services, resources, and diagnostics.",
    }
    HOME = (-.35, -.25, 1.0)
    PITCH_LIMIT = 1.1
    ZOOM_LIMITS = (.65, 1.25)
    #: A faced node sits front-right of the core instead of hiding it.
    FACE_OFFSET = .55
    #: Fast frames only while something is actually moving.
    FRAME_MS, IDLE_MS = 33, 50
    #: Idle rotation waits this long after the owner last touched the globe.
    IDLE_DELAY = 3.0
    PULSE_SECONDS = .6
    _owns_mousewheel = True

    def __init__(self, parent: tk.Misc, palette: Any,
                 on_open: Callable[[str], None], *, width: int = 340,
                 height: int = 232, on_preview: Callable[[str], None] | None = None,
                 on_motion: Callable[[bool], None] | None = None,
                 on_drill: Callable[[str], None] | None = None) -> None:
        super().__init__(parent, width=width, height=height, bg=palette.PANEL,
                         bd=0, highlightthickness=1, highlightbackground=palette.LINE,
                         highlightcolor=palette.GOLD, takefocus=True, cursor="fleur")
        self.palette, self.on_open, self.on_preview = palette, on_open, on_preview
        self.on_motion = on_motion
        # Double-clicking a node "drills in": the caller decides what that opens.
        self.on_drill = on_drill
        self.angle, self.pitch, self.zoom = self.HOME
        self.active = "chat"
        self.motion = True
        # Fine-grained: Windows' monotonic clock ticks every ~15ms, too coarse
        # to measure how fast a flick was.
        self._clock: Callable[[], float] = time.perf_counter
        self._hover: str | None = None
        self._positions: dict[str, tuple[float, float, float]] = {}
        self._drag_x: float | None = None
        self._drag_y = 0.0
        self._drag_at = 0.0
        self._press_position = (0.0, 0.0)
        self._pointer: tuple[float, float] | None = None
        self._dragged = False
        self._inside = False
        self._focused = False
        self._keyboard_index = 0
        self._velocity = (0.0, 0.0)
        self._target: tuple[float, float, float | None, float] | None = None
        self._pulse: tuple[str, float] | None = None
        self._idle_after = 0.0
        self._backdrop: tuple[Any, ...] | None = None
        self._frame: str | None = None
        self._last_frame = self._clock()
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._move)
        self.bind("<Enter>", lambda _event: setattr(self, "_inside", True))
        self.bind("<Leave>", self._leave)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda _event: self._zoom(1))
        self.bind("<Button-5>", lambda _event: self._zoom(-1))
        self.bind("<Left>", lambda _event: self._step_key(-1))
        self.bind("<Right>", lambda _event: self._step_key(1))
        self.bind("<Up>", lambda _event: self._tilt(-1))
        self.bind("<Down>", lambda _event: self._tilt(1))
        for sequence in ("<plus>", "<equal>", "<KP_Add>"):
            self.bind(sequence, lambda _event: (self._zoom(1), "break")[1])
        for sequence in ("<minus>", "<KP_Subtract>"):
            self.bind(sequence, lambda _event: (self._zoom(-1), "break")[1])
        self.bind("<Key>", self._digit)
        self.bind("<Return>", lambda _event: self._activate(self.VIEWS[self._keyboard_index][0]))
        self.bind("<Home>", lambda _event: self.reset())
        self.bind("<Escape>", self._cancel_interaction)
        self.bind("<space>", lambda _event: self.set_motion(not self.motion))
        self.bind("<FocusIn>", lambda _event: self._set_focused(True))
        self.bind("<FocusOut>", lambda _event: self._set_focused(False))
        self.bind("<Map>", self._start)
        self.bind("<Unmap>", self._stop)
        self.bind("<Destroy>", self._stop)
        self.redraw()

    # ------------------------------------------------------------- public

    def select(self, key: str) -> None:
        """Mark the open view and turn the globe to face it."""
        if self.active == key:
            return
        self.active = key
        if key in dict(self.VIEWS):
            self._keyboard_index = [item[0] for item in self.VIEWS].index(key)
            self.face(key)
        self.redraw()

    def face(self, key: str) -> None:
        """Bring one destination round to the front of the globe."""
        x, y, z = self._base([item[0] for item in self.VIEWS].index(key))
        yaw = self.FACE_OFFSET - math.atan2(x, z)
        # Polar nodes also need a tilt; capped so the globe never flips over.
        pitch = max(-.7, min(.7, math.atan2(y, math.hypot(x, z) * math.cos(self.FACE_OFFSET))))
        self._aim(math.remainder(yaw, math.tau), pitch)

    def set_motion(self, enabled: bool) -> str:
        self.motion = bool(enabled)
        self._stop()
        if self.motion:
            self._start()
        else:
            # Reduced motion finishes any pending move instantly rather than
            # abandoning it half way round.
            self._velocity, self._pulse = (0.0, 0.0), None
            if self._target is not None:
                self._arrive()
            self.redraw()
        if self.on_motion:
            self.on_motion(self.motion)
        self.event_generate("<<MotionChanged>>")
        return "break"

    def reset(self) -> None:
        self._aim(*self.HOME)

    def camera_view(self, name: str) -> None:
        """Shared, deterministic poses for brain, tools and voice workspaces."""
        poses = {'Front': (0.0, 0.0, 1.0), 'Left': (-math.pi / 2, 0.0, 1.0),
                 'Right': (math.pi / 2, 0.0, 1.0), 'Above': (0.0, -.85, 1.0)}
        if name == 'Home':
            self.reset()
        elif name in poses:
            self._aim(*poses[name])
        else:
            raise ValueError('Unknown 3D camera view.')

    def remember_camera(self) -> None:
        # Finish a pending move first; bookmarks should match what is visible.
        self._target = None
        self._velocity = (0.0, 0.0)
        self._camera_bookmark = (self.angle, self.pitch, self.zoom)

    def restore_camera(self) -> None:
        if hasattr(self, '_camera_bookmark'):
            self._aim(*self._camera_bookmark)


    def refresh_theme(self) -> None:
        self._backdrop = None
        self.redraw()

    # --------------------------------------------------------- animation

    def _aim(self, angle: float, pitch: float, zoom: float | None = None) -> None:
        """Head for a camera pose, turning whichever way round is shorter."""
        self._velocity = (0.0, 0.0)
        self._idle_after = self._clock() + self.IDLE_DELAY
        self._target = (self.angle + math.remainder(angle - self.angle, math.tau),
                        max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, pitch)), zoom, angle)
        if self.motion and self.winfo_ismapped():
            self._wake()
        else:
            self._arrive()
            self.redraw()

    def _arrive(self) -> None:
        # Land on the requested angle itself, not its unwrapped twin.
        _near, self.pitch, zoom, self.angle = self._target  # type: ignore[misc]
        if zoom is not None:
            self.zoom = zoom
        self._target = None

    def _wake(self) -> None:
        """Switch a sleeping or idle loop to full frame rate right away."""
        if not self.motion or not self.winfo_ismapped():
            return
        if self._frame is not None:
            self.after_cancel(self._frame)
        self._last_frame = self._clock()
        self._frame = self.after(self.FRAME_MS, self._animate)

    def _start(self, _event: Any = None) -> None:
        if self.motion and self._frame is None and self.winfo_ismapped():
            self._last_frame = self._clock()
            self._frame = self.after(self.IDLE_MS, self._animate)

    def _stop(self, _event: Any = None) -> None:
        if self._frame is not None:
            self.after_cancel(self._frame)
            self._frame = None

    def _visible(self) -> bool:
        top = self.winfo_toplevel()
        y = self.winfo_rooty() - top.winfo_rooty()
        return y < top.winfo_height() and y + self.winfo_height() > 0

    def _advance(self, dt: float, now: float) -> tuple[bool, bool]:
        """Move the camera one frame. Returns (changed, still busy)."""
        changed = busy = False
        if self._target is not None:
            angle, pitch, zoom, _land = self._target
            ease = 1 - math.exp(-dt * 9)
            self.angle += (angle - self.angle) * ease
            self.pitch += (pitch - self.pitch) * ease
            if zoom is not None:
                self.zoom += (zoom - self.zoom) * ease
            if (abs(angle - self.angle) < .002 and abs(pitch - self.pitch) < .002
                    and (zoom is None or abs(zoom - self.zoom) < .002)):
                self._arrive()
            changed = busy = True
        elif self._drag_x is None and self._velocity != (0.0, 0.0):
            spin, tilt = self._velocity
            self.angle += spin * dt
            pitch = self.pitch + tilt * dt
            self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, pitch))
            if self.pitch != pitch:
                tilt = 0.0
            decay = math.exp(-dt * 3.2)
            spin, tilt = spin * decay, tilt * decay
            self._velocity = (spin, tilt) if math.hypot(spin, tilt) > .04 else (0.0, 0.0)
            self._idle_after = now + self.IDLE_DELAY
            changed = busy = True
        elif not self._inside and self._drag_x is None and now >= self._idle_after:
            self.angle += dt * .14
            changed = True
        if self._pulse is not None:
            changed = True
            if now - self._pulse[1] >= self.PULSE_SECONDS:
                self._pulse = None
            else:
                busy = True
        return changed, busy

    def _animate(self) -> None:
        self._frame = None
        if not self.motion or not self.winfo_ismapped():
            return
        now = self._clock()
        changed, busy = self._advance(min(.1, now - self._last_frame), now)
        self._last_frame = now
        visible = self._visible()
        if changed and visible:
            self.redraw()
            self._rehover()
        if visible and self._overlay(redrawn=changed):
            busy = True
        self._frame = self.after(self.FRAME_MS if busy else self.IDLE_MS, self._animate)

    def _overlay(self, *, redrawn: bool) -> bool:
        """Per-frame hook for a layer that animates without the camera.

        Subclasses redraw only that layer when ``redrawn`` is false, and
        return True while it still needs full frame rate.
        """
        return False

    # ----------------------------------------------------------- geometry

    def _point(self, point: tuple[float, float, float], w: float, h: float) -> tuple[float, float, float]:
        x, y, z = point
        x, z = x * math.cos(self.angle) + z * math.sin(self.angle), z * math.cos(self.angle) - x * math.sin(self.angle)
        y, z = y * math.cos(self.pitch) - z * math.sin(self.pitch), y * math.sin(self.pitch) + z * math.cos(self.pitch)
        scale = min(w * .28, h * .34) * self.zoom * 3.8 / (3.8 - z)
        return w / 2 + x * scale, h * .47 + y * scale, z

    def _base(self, index: int) -> tuple[float, float, float]:
        # Evenly spaced points on a sphere, independent of camera movement.
        y = 1 - 2 * (index + .5) / len(self.VIEWS)
        radius = math.sqrt(1 - y * y)
        theta = index * math.pi * (3 - math.sqrt(5))
        return radius * math.cos(theta), y, radius * math.sin(theta)

    def _project(self, index: int, width: float, height: float) -> tuple[float, float, float]:
        return self._point(self._base(index), width, height)

    @staticmethod
    def _radius(z: float) -> float:
        return 8 + (z + 1) * 2

    # ------------------------------------------------------------ drawing

    def _size(self) -> tuple[float, float]:
        w, h = max(1, self.winfo_width()), max(1, self.winfo_height())
        if w < 20 or h < 20:
            w, h = float(self.cget("width")), float(self.cget("height"))
        return w, h

    def _draw_backdrop(self, w: float, h: float) -> None:
        """Glow and floor never move with the camera, so draw them once."""
        p = self.palette
        key = (w, h, p.PANEL, p.ROYAL, p.LINE)
        if key == self._backdrop:
            return
        self._backdrop = key
        self.delete("backdrop")
        cx, cy = w / 2, h * .47
        radius = min(w * .3, h * .4)
        for i in range(16, 0, -1):
            r = radius * i / 12
            self.create_oval(cx-r, cy-r*.83, cx+r, cy+r*.83, tags="backdrop",
                             fill=blend(p.PANEL, p.ROYAL, (1-i/17)*.14), outline="")
        floor = h * .86
        for i in range(-4, 5):
            self.create_line(cx+i*16, floor-12, cx+i*52, h, fill=blend(p.PANEL, p.LINE, .55), tags="backdrop")
        for offset in (0, 9, 23):
            self.create_line(12, floor+offset, w-12, floor+offset, fill=blend(p.PANEL, p.LINE, .55), tags="backdrop")
        self.tag_lower("backdrop")

    def redraw(self) -> None:
        w, h = self._size()
        p = self.palette
        self.configure(bg=p.PANEL, highlightbackground=p.LINE, highlightcolor=p.GOLD)
        self._draw_backdrop(w, h)
        self.delete("scene")
        cx, cy = w / 2, h * .47
        floor = h * .86
        # The shadow widens as the globe comes closer, which reads as height.
        shadow = min(w * .3, h * .4) * .75 * self.zoom
        self.create_oval(cx-shadow, floor-8, cx+shadow, floor+4, tags="scene",
                         fill=blend(p.PANEL, p.BLACK, .35), outline=blend(p.PANEL, p.ROYAL_SOFT, .4))

        points = [self._project(i, w, h) for i in range(len(self.VIEWS))]
        self._positions = {key: points[i] for i, (key, _label) in enumerate(self.VIEWS)}
        focus = self._hover or self.active
        # Everything is painted far-to-near so the core genuinely hides the
        # far side and nearer nodes overlap farther ones.
        layers: list[tuple[float, int, Callable[[], None]]] = []
        for curve in self._curves():
            projected = [self._point(point, w, h) for point in curve]
            for start in range(0, 48, 4):
                segment = projected[start:start+5]
                z = sum(point[2] for point in segment) / len(segment)
                layers.append((z, 0, lambda s=segment, z=z: self.create_line(
                    *[v for point in s for v in point[:2]], smooth=True, tags="scene",
                    width=1.4 if z > .35 else 1, fill=blend(p.PANEL, p.ROYAL_SOFT, .12 + (z+1)*.25))))
        for (key, _label), (x, y, z) in zip(self.VIEWS, points):
            lit = key == focus
            layers.append((z / 2, 1, lambda x=x, y=y, lit=lit: self.create_line(
                cx, cy, x, y, tags="scene", width=2 if lit else 1, dash=() if lit else (2, 5),
                fill=blend(p.PANEL, p.GOLD if lit else p.ROYAL_SOFT, .6 if lit else .25))))
        layers.append((0.0, 2, lambda: self._draw_core(cx, cy)))
        for i, (key, _label) in enumerate(self.VIEWS):
            layers.append((points[i][2], 3, lambda i=i, key=key: self._draw_node(i, key, *points[i])))
        for _z, _order, draw in sorted(layers, key=lambda layer: layer[:2]):
            draw()

        if self._pulse is not None and self._pulse[0] in self._positions:
            x, y, z = self._positions[self._pulse[0]]
            t = min(1.0, (self._clock() - self._pulse[1]) / self.PULSE_SECONDS)
            r = self._radius(z) + 4 + 26 * t
            self.create_oval(x-r, y-r, x+r, y+r, tags="scene", width=2,
                             outline=blend(p.GOLD, p.PANEL, t))
        if self._hover in self._positions:
            self._draw_label(self._hover, w)
        self.create_text(12, 12, text="WORKSPACE / 3D", anchor="nw", fill=p.MUTED,
                         font=("Segoe UI Semibold", 8), tags="scene")
        self.create_text(w-12, h-9, text=f"{round(self.zoom*100)}%", anchor="se", fill=p.MUTED,
                         font=("Segoe UI", 8), tags="scene")
        if self._focused:
            self.create_text(12, h-9, anchor="sw", fill=p.MUTED, font=("Segoe UI", 8), tags="scene",
                             text="←/→ pick · Enter open · ↑/↓ tilt · Home reset")

    @staticmethod
    def _curves() -> list[list[tuple[float, float, float]]]:
        """Latitude rings and meridians, before any camera transform."""
        curves = []
        for latitude in (-.9, -.45, 0, .45, .9):
            rr, yy = math.cos(latitude), math.sin(latitude)
            curves.append([(rr*math.cos(t*math.tau/48), yy, rr*math.sin(t*math.tau/48)) for t in range(49)])
        for longitude in range(6):
            a = longitude * math.pi / 6
            curves.append([(math.cos(t*math.tau/48)*math.cos(a), math.sin(t*math.tau/48),
                            math.cos(t*math.tau/48)*math.sin(a)) for t in range(49)])
        return curves

    def _draw_core(self, cx: float, cy: float) -> None:
        # Shaded core: decorative, not a fabricated live system reading.
        p = self.palette
        core = 21 * self.zoom
        for i in range(12, 0, -1):
            r = core * i / 12
            self.create_oval(cx-r-3, cy-r-4, cx+r-3, cy+r-4, outline="", tags=("scene", "core"),
                             fill=blend(p.ROYAL, p.ROYAL_SOFT, (1-i/12)*.8))
        self.create_oval(cx-9, cy-12, cx-2, cy-5, outline="", tags=("scene", "core"),
                         fill=blend(p.ROYAL_SOFT, p.WHITE, .65))

    def _draw_node(self, index: int, key: str, x: float, y: float, z: float) -> None:
        p = self.palette
        tags = ("scene", "node", f"node-{key}")
        near = (z + 1) / 2
        r = self._radius(z) + (2 if key == self._hover else 0)
        active = key == self.active or key == self._hover
        colour = p.GOLD if active else p.ROYAL_SOFT
        if active:
            self.create_oval(x-r-5, y-r-5, x+r+5, y+r+5, tags=tags,
                             fill=blend(p.PANEL, colour, .14), outline=blend(p.PANEL, colour, .4))
        self.create_oval(x-r+2, y-r+3, x+r+2, y+r+3, fill=p.BLACK, outline="", tags=tags)
        # Farther nodes sink into the panel colour: atmospheric perspective.
        body = blend(p.PANEL, colour, (.6 if active else .22) * (.55 + .45 * near))
        self.create_oval(x-r, y-r, x+r, y+r, fill=body, tags=tags,
                         outline=blend(p.PANEL, colour, .45 + .55 * near), width=2 if active else 1)
        # A specular glint sells the sphere without any real lighting model.
        g = r * .38
        self.create_oval(x-r*.55, y-r*.6, x-r*.55+g, y-r*.6+g*.8, outline="", tags=tags,
                         fill=blend(body, p.WHITE, .35 * near))
        self.create_text(x, y, text=str(index+1), fill=blend(p.PANEL, p.WHITE, .5 + .5 * near),
                         font=("Segoe UI Semibold", 9), tags=tags)

    def _draw_label(self, key: str, w: float) -> None:
        p = self.palette
        x, y, z = self._positions[key]
        text = dict(self.VIEWS)[key]
        width = 7 * len(text) + 18
        left = max(6, min(w - width - 6, x - width / 2))
        top = max(24, y - self._radius(z) - 30)
        rounded(self, (left, top, left + width, top + 21), radius=9, tags=("scene", "label"),
                fill=blend(p.PANEL, p.BLACK, .4), outline=blend(p.PANEL, p.GOLD, .7))
        self.create_text(left + width / 2, top + 10.5, text=text, fill=p.WHITE,
                         font=("Segoe UI Semibold", 9), tags=("scene", "label"))

    # --------------------------------------------------------- pointer

    def _hit(self, x: float, y: float) -> str | None:
        # Frontmost node wins when spheres overlap. No invisible 30px targets.
        matches = [(z, key) for key, (px, py, z) in self._positions.items()
                   if math.hypot(x-px, y-py) <= self._radius(z) + 2]
        return max(matches)[1] if matches else None

    def _activate(self, key: str) -> None:
        if self.motion:
            self._pulse = (key, self._clock())
            self._wake()
        self.on_open(key)
        self.select(key)

    def _press(self, event: Any) -> None:
        self.focus_set()
        # Catching a spinning globe stops it, as it would in the hand.
        self._velocity, self._target = (0.0, 0.0), None
        self._drag_x, self._drag_y = float(event.x), float(event.y)
        self._drag_at = self._clock()
        self._press_position = (self._drag_x, self._drag_y)
        self._dragged = False

    def _drag(self, event: Any) -> None:
        if self._drag_x is None:
            return
        x, y = float(event.x), float(event.y)
        if math.hypot(x-self._press_position[0], y-self._press_position[1]) >= 4:
            if not self._dragged:
                self.configure(cursor="fleur")
            self._dragged = True
        now = self._clock()
        if self._dragged:
            spin, tilt = (x-self._drag_x)*.009, (y-self._drag_y)*.009
            self.angle += spin
            self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch+tilt))
            dt = now - self._drag_at
            if dt > 0:
                # Smoothed so one jittery sample cannot fling the globe.
                old_spin, old_tilt = self._velocity
                self._velocity = (old_spin*.4 + spin/dt*.6, old_tilt*.4 + tilt/dt*.6)
            self._idle_after = now + self.IDLE_DELAY
            self.redraw()
        self._drag_x, self._drag_y, self._drag_at = x, y, now

    def _release(self, event: Any) -> None:
        pressed = self._drag_x is not None
        if pressed and math.hypot(float(event.x) - self._press_position[0],
                                  float(event.y) - self._press_position[1]) >= 4:
            self._dragged = True  # a coalesced/missing motion event is still a drag
        self._drag_x = None
        if pressed and self._dragged:
            spin, tilt = self._velocity
            # Only a flick coasts: pausing before letting go means "put it here".
            if self.motion and self._clock() - self._drag_at < .08:
                self._velocity = (max(-6.0, min(6.0, spin)), max(-3.0, min(3.0, tilt)))
                self._wake()
            else:
                self._velocity = (0.0, 0.0)
        self._move(event)
        if pressed and not self._dragged:
            key = self._hit(float(event.x), float(event.y))
            if key:
                self._activate(key)

    def _cancel_interaction(self, _event: Any = None) -> str:
        self._drag_x = None
        self._dragged = True
        self._velocity, self._target = (0.0, 0.0), None
        self.redraw()
        return "break"

    def _double(self, event: Any) -> None:
        key = self._hit(float(event.x), float(event.y))
        if key is None:
            self.reset()
        elif self.on_drill is not None:
            # Double-click a part to go there. Single click still just selects it.
            self.select(key)
            self.on_drill(key)

    def _move(self, event: Any) -> None:
        self._pointer = (float(event.x), float(event.y))
        if self._drag_x is None:
            self._rehover()

    def _rehover(self) -> None:
        """Keep hover truthful when the globe turns under a still pointer."""
        if not self._inside or self._pointer is None or self._drag_x is not None:
            return
        key = self._hit(*self._pointer)
        self.configure(cursor="hand2" if key else "fleur")
        if key != self._hover:
            self._hover = key
            if self.on_preview:
                self.on_preview(key or self.active)
            self.redraw()

    def _leave(self, _event: Any) -> None:
        self._inside = False
        self._pointer = None
        self._hover = None
        if self.on_preview:
            self.on_preview(self.active)
        self.redraw()

    # -------------------------------------------------------- keyboard

    def _set_focused(self, focused: bool) -> None:
        self._focused = focused
        self.redraw()

    def _step_key(self, direction: int) -> str:
        self._keyboard_index = (self._keyboard_index+direction) % len(self.VIEWS)
        self._hover = self.VIEWS[self._keyboard_index][0]
        if self.on_preview:
            self.on_preview(self._hover)
        self.face(self._hover)
        self.redraw()
        return "break"

    def _tilt(self, direction: int) -> str:
        self._velocity, self._target = (0.0, 0.0), None
        self._idle_after = self._clock() + self.IDLE_DELAY
        self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch + direction * .15))
        self.redraw()
        return "break"

    def _digit(self, event: Any) -> str | None:
        # Plain digits only: Ctrl/Alt chords stay with the window's shortcuts.
        # (0x20000 is Alt on Windows; 0x0008 there is NumLock, not Alt.)
        if getattr(event, "state", 0) & (0x0004 | 0x20000) or str(getattr(event, "char", "")) not in tuple("123456789"):
            return None
        index = int(event.char) - 1
        if not 0 <= index < len(self.VIEWS):
            return None
        self._keyboard_index = index
        self._activate(self.VIEWS[index][0])
        return "break"

    def _zoom(self, direction: int) -> None:
        low, high = self.ZOOM_LIMITS
        self.zoom = max(low, min(high, self.zoom+direction*.08))
        if self._target is not None:
            self._target = (*self._target[:2], None, self._target[3])
        self.redraw()

    def _wheel(self, event: Any) -> str:
        if event.delta:
            self._zoom(1 if event.delta > 0 else -1)
        return "break"
