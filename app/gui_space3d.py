"""An interactive 3D scene on a Tk canvas: orbit, pan, zoom at the pointer, pick, and full keyboard control.

`OrbitScene` draws points, line segments and surface faces inside a unit cube, with the cues that
make depth readable on a flat screen: perspective, farther things fainter (fog), a floor grid,
each point's shadow on the floor and a drop line down to it, and depth-sorted drawing.

Hands and keys:
* drag to turn (a flick keeps it turning and slows down), Shift+drag or right-drag to move it,
  the wheel zooms toward the pointer;
* hover shows what a point or a surface patch means; click selects; double-click opens it, or
  resets the view on empty space;
* arrows turn, +/- zoom, 1-4 preset views (3D, front, top, side), Home resets, Space spins,
  Tab and Shift+Tab step through the points, Enter opens the one selected.

Motion follows Windows' animation setting (app/gui_motion.py): with motion off, a flick stops,
view changes snap and spinning is unavailable, but every action still works.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Any, Callable

from app.gui_depth import blend
from app.gui_motion import MOTION

HINT = "Drag to turn · Shift+drag to move · wheel zooms at the pointer · double-click opens · 1–4 views · Home resets"


def inside(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Even-odd test: is (x, y) inside the polygon?"""
    found = False
    for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            found = not found
    return found


class OrbitScene(tk.Canvas):
    HOME = (-.65, .42, 1.0)
    #: (key, label, yaw, pitch)
    PRESETS = (("iso", "3D", -.65, .42), ("front", "Front", 0.0, 0.0), ("top", "Top", 0.0, 1.5), ("side", "Side", -math.pi / 2, 0.0))
    PITCH_LIMIT = 1.52
    ZOOM_LIMITS = (.45, 4.0)
    DISTANCE = 3.6
    FRAME_MS = 20
    SPIN = .3  # radians a second
    FOG = .62  # how far the farthest things fade toward the background

    def __init__(self, parent: tk.Misc, palette: Any, *, on_select: Callable[[str | None], None] | None = None,
                 on_open: Callable[[str], None] | None = None, height: int = 380, empty: str = "Loading…",
                 hint: bool = True) -> None:
        self.palette = palette
        self.background = blend(palette.PANEL, palette.BLACK, .5)
        super().__init__(parent, bg=self.background, height=height, bd=0, highlightthickness=1, highlightbackground=palette.LINE,
                         highlightcolor=palette.GOLD, takefocus=True, cursor="fleur")
        self.on_select, self.on_open = on_select, on_open
        self.hint = hint  # the controls line along the bottom; a parent that says it elsewhere turns it off
        self._labels: list[tuple[int, float, float, float, float, str]] = []
        self.yaw, self.pitch, self.zoom = self.HOME
        self.pan = (0.0, 0.0)
        self.points: list[dict[str, Any]] = []
        self.segments: list[dict[str, Any]] = []
        self.faces: list[dict[str, Any]] = []
        self.axes: dict[str, tuple[str, list[tuple[float, str]]]] = {}
        self.note, self.empty = "", empty
        self.selected: str | None = None
        self.hover: tuple[str, str] | None = None
        self.tooltip = ""
        self.spinning = False
        self._clock: Callable[[], float] = time.perf_counter
        self._velocity = (0.0, 0.0)
        self._target: tuple[float, float, float | None] | None = None
        self._press: tuple[float, float] | None = None
        self._last: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._panning = False
        self._dragged = False
        self._pointer: tuple[float, float] | None = None
        self._hits: list[tuple[float, str, str, Any]] = []
        self._frame: str | None = None
        self._frame_at = self._clock()
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._down)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._up)
        for button in (2, 3):
            self.bind(f"<ButtonPress-{button}>", lambda event: self._down(event, pan=True))
            self.bind(f"<B{button}-Motion>", self._drag)
            self.bind(f"<ButtonRelease-{button}>", self._up)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._move)
        self.bind("<Leave>", self._leave)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda event: self.zoom_at(event.x, event.y, 1.15))
        self.bind("<Button-5>", lambda event: self.zoom_at(event.x, event.y, 1 / 1.15))
        self.bind("<Left>", lambda _event: self.turn(-.15, 0))
        self.bind("<Right>", lambda _event: self.turn(.15, 0))
        self.bind("<Up>", lambda _event: self.turn(0, .12))
        self.bind("<Down>", lambda _event: self.turn(0, -.12))
        for sequence in ("<plus>", "<equal>", "<KP_Add>"):
            self.bind(sequence, lambda _event: self._key_zoom(1.15))
        for sequence in ("<minus>", "<KP_Subtract>"):
            self.bind(sequence, lambda _event: self._key_zoom(1 / 1.15))
        self.bind("<Home>", lambda _event: (self.reset(), "break")[1])
        self.bind("<Escape>", self._cancel_interaction)
        self.bind("<space>", lambda _event: (self.set_spin(not self.spinning), "break")[1])
        self.bind("<Tab>", lambda _event: self.step(1))
        self.bind("<Shift-Tab>", lambda _event: self.step(-1))
        self.bind("<ISO_Left_Tab>", lambda _event: self.step(-1))
        self.bind("<Return>", lambda _event: self._open_selected())
        self.bind("<Key>", self._digit)
        self.bind("<FocusIn>", lambda _event: self.redraw())
        self.bind("<FocusOut>", lambda _event: self.redraw())
        self.bind("<Unmap>", lambda _event: self._halt())
        self.bind("<Map>", lambda _event: self._wake())
        self.bind("<Destroy>", lambda event: self._halt() if event.widget is self else None)

    # ------------------------------------------------------------------ content
    def set_scene(self, *, points: Any = (), segments: Any = (), faces: Any = (), axes: dict | None = None, note: str = "",
                  keep_view: bool = True) -> None:
        """points: {id, x, y, z, size, colour, label, tip}; segments: {a, b, colour, width, dash}; faces: {corners, colour, tip, id}."""
        self.points, self.segments, self.faces = list(points), list(segments), list(faces)
        self.axes, self.note = dict(axes or {}), note
        if self.selected not in {point["id"] for point in self.points}:
            self.selected = None
        if not keep_view:
            self.yaw, self.pitch, self.zoom = self.HOME
            self.pan = (0.0, 0.0)
        # A new scene keeps no reading from the old one on screen.
        self.hover = None
        self.tooltip = (self._point(self.selected) or {}).get("tip", "")
        self.redraw()

    def clear(self, message: str) -> None:
        self.empty = message
        self.set_scene()

    def select(self, key: str | None, *, notify: bool = True) -> None:
        self.selected = key
        point = self._point(key)
        self.tooltip = point.get("tip", "") if point else ""
        if notify and self.on_select:
            self.on_select(key)
        self.redraw()

    # ------------------------------------------------------------------ camera
    @property
    def motion(self) -> bool:
        return bool(MOTION.get("enabled", True))

    def project(self, x: float, y: float, z: float) -> tuple[float, float, float, float]:
        """(screen x, screen y, depth toward the viewer, perspective scale)."""
        width, height = self._size()
        cos_yaw, sin_yaw = math.cos(self.yaw), math.sin(self.yaw)
        x1, z1 = x * cos_yaw + z * sin_yaw, -x * sin_yaw + z * cos_yaw
        cos_pitch, sin_pitch = math.cos(self.pitch), math.sin(self.pitch)
        y2, z2 = y * cos_pitch - z1 * sin_pitch, y * sin_pitch + z1 * cos_pitch
        scale = self.DISTANCE / (self.DISTANCE - z2)
        # The whole box fits at zoom 1 whichever way it is turned, with room for the note above it.
        unit = min(width, height - 30) * .25 * self.zoom
        centre_x, centre_y = self._centre()
        return (centre_x + self.pan[0] + x1 * unit * scale, centre_y + self.pan[1] - y2 * unit * scale, z2, scale)

    def _centre(self) -> tuple[float, float]:
        width, height = self._size()
        return width / 2, height / 2 + 10

    def view(self, key: str) -> None:
        """Turn to a preset view: eased with motion, at once without."""
        _key, _label, yaw, pitch = next(item for item in self.PRESETS if item[0] == key)
        self._aim(yaw, pitch, None)

    def reset(self) -> None:
        self.pan = (0.0, 0.0)
        self._aim(*self.HOME)

    def turn(self, yaw: float, pitch: float) -> str:
        self._velocity, self._target = (0.0, 0.0), None
        self.yaw += yaw
        self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch + pitch))
        self.redraw()
        return "break"

    def zoom_at(self, x: float, y: float, factor: float) -> str:
        """Zoom so the spot under the pointer stays under it."""
        low, high = self.ZOOM_LIMITS
        new = max(low, min(high, self.zoom * factor))
        if new != self.zoom:
            centre_x, centre_y = self._centre()
            ratio = new / self.zoom
            # screen = centre + pan + zoom·q, so keeping (x, y) fixed moves the pan by the same ratio.
            self.pan = (x - centre_x - (x - centre_x - self.pan[0]) * ratio, y - centre_y - (y - centre_y - self.pan[1]) * ratio)
            self.zoom = new
            if self._target is not None:
                self._target = (*self._target[:2], None)
            self.redraw()
        return "break"

    def set_spin(self, enabled: bool) -> None:
        self.spinning = bool(enabled) and self.motion
        if self.spinning:
            self._wake()
        self.event_generate("<<SpinChanged>>")

    def _aim(self, yaw: float, pitch: float, zoom: float | None) -> None:
        self._velocity = (0.0, 0.0)
        target = (self.yaw + math.remainder(yaw - self.yaw, math.tau), max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, pitch)), zoom)
        if self.motion and self.winfo_ismapped():
            self._target = target
            self._wake()
        else:
            self._target = None
            self.yaw, self.pitch = target[0], target[1]
            if zoom is not None:
                self.zoom = zoom
            self.redraw()

    def _key_zoom(self, factor: float) -> str:
        width, height = self._size()
        return self.zoom_at(width / 2, height / 2, factor)

    # ------------------------------------------------------------------ animation
    def _wake(self) -> None:
        if self._frame is None and self.winfo_ismapped() and self.motion:
            self._frame_at = self._clock()
            self._frame = self.after(self.FRAME_MS, self._animate)

    def _halt(self) -> None:
        if self._frame is not None:
            try:
                self.after_cancel(self._frame)
            except tk.TclError:
                pass  # the canvas is already gone
            self._frame = None

    def _animate(self) -> None:
        self._frame = None
        now = self._clock()
        dt = min(.1, max(.001, now - self._frame_at))
        self._frame_at = now
        if not self.motion:
            self._velocity, self.spinning = (0.0, 0.0), False
            if self._target is not None:
                self.yaw, self.pitch = self._target[0], self._target[1]
                self.zoom = self._target[2] if self._target[2] is not None else self.zoom
                self._target = None
            self.redraw()
            return
        moving = False
        if self._target is not None:
            yaw, pitch, zoom = self._target
            ease = 1 - math.exp(-9 * dt)
            self.yaw += (yaw - self.yaw) * ease
            self.pitch += (pitch - self.pitch) * ease
            if zoom is not None:
                self.zoom += (zoom - self.zoom) * ease
            if abs(yaw - self.yaw) < .002 and abs(pitch - self.pitch) < .002 and (zoom is None or abs(zoom - self.zoom) < .002):
                self.yaw, self.pitch = yaw, pitch
                self.zoom = zoom if zoom is not None else self.zoom
                self._target = None
            moving = True
        elif any(self._velocity) and self._press is None:
            spin, tilt = self._velocity
            self.yaw += spin * dt
            self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch + tilt * dt))
            decay = math.exp(-3.5 * dt)
            self._velocity = (spin * decay, tilt * decay)
            if abs(self._velocity[0]) < .02 and abs(self._velocity[1]) < .02:
                self._velocity = (0.0, 0.0)
            moving = True
        if self.spinning and self._press is None and self._target is None:
            self.yaw += self.SPIN * dt
            moving = True
        self.redraw()
        if moving:
            self._frame = self.after(self.FRAME_MS, self._animate)

    # ------------------------------------------------------------------ pointer
    def _down(self, event: Any, pan: bool = False) -> None:
        self.focus_set()
        self._velocity, self._target = (0.0, 0.0), None
        self._press = (float(event.x), float(event.y))
        self._last = (float(event.x), float(event.y), self._clock())
        self._panning = pan or bool(getattr(event, "state", 0) & 0x0001)
        self._dragged = False

    def _drag(self, event: Any) -> None:
        if self._press is None:
            return
        x, y = float(event.x), float(event.y)
        if not self._dragged and math.hypot(x - self._press[0], y - self._press[1]) < 4:
            return
        self._dragged = True
        last_x, last_y, last_at = self._last
        now = self._clock()
        if self._panning:
            self.pan = (self.pan[0] + x - last_x, self.pan[1] + y - last_y)
        else:
            spin, tilt = (x - last_x) * .01, (y - last_y) * .01
            self.yaw += spin
            self.pitch = max(-self.PITCH_LIMIT, min(self.PITCH_LIMIT, self.pitch + tilt))
            if now > last_at:
                old_spin, old_tilt = self._velocity
                self._velocity = (old_spin * .4 + spin / (now - last_at) * .6, old_tilt * .4 + tilt / (now - last_at) * .6)
        self._last = (x, y, now)
        self.tooltip = ""
        self.redraw()

    def _up(self, event: Any) -> None:
        pressed, self._press = self._press, None
        if pressed is None:
            return
        if self._panning or math.hypot(float(event.x) - pressed[0], float(event.y) - pressed[1]) >= 4:
            self._dragged = True
        if self._dragged:
            # Only a flick coasts: pausing before letting go means "leave it here".
            if not self._panning and self.motion and self._clock() - self._last[2] < .08:
                spin, tilt = self._velocity
                self._velocity = (max(-8.0, min(8.0, spin)), max(-4.0, min(4.0, tilt)))
                self._wake()
            else:
                self._velocity = (0.0, 0.0)
            self._move(event)
            return
        found = self._hit(float(event.x), float(event.y))
        if found and found[0] == "point":
            self.select(found[1])
        elif found is None and self.selected is not None:
            self.select(None)

    def _cancel_interaction(self, _event: Any = None) -> str:
        self._press = None
        self._dragged = True
        self._velocity, self._target = (0.0, 0.0), None
        self.redraw()
        return "break"

    def _double(self, event: Any) -> None:
        found = self._hit(float(event.x), float(event.y))
        if found and found[0] == "point":
            self.select(found[1], notify=False)
            if self.on_open:
                self.on_open(found[1])
        elif found is None:
            self.reset()

    def _move(self, event: Any) -> None:
        self._pointer = (float(event.x), float(event.y))
        if self._press is not None:
            return
        found = self._hit(*self._pointer)
        self.configure(cursor="hand2" if found and found[0] == "point" else "fleur")
        if found != self.hover:
            self.hover = found
            self.tooltip = self._tip(found) if found else (self._point(self.selected) or {}).get("tip", "")
            self.redraw()

    def _leave(self, _event: Any) -> None:
        self._pointer, self.hover = None, None
        self.tooltip = (self._point(self.selected) or {}).get("tip", "")
        self.redraw()

    def _wheel(self, event: Any) -> str:
        if event.delta:
            return self.zoom_at(event.x, event.y, 1.15 ** max(-8, min(8, event.delta / 120)))
        return "break"

    # ------------------------------------------------------------------ keys
    def _digit(self, event: Any) -> str | None:
        if getattr(event, "state", 0) & (0x0004 | 0x20000):
            return None
        char = str(getattr(event, "char", ""))
        if char in "123456789" and len(char) == 1 and int(char) <= len(self.PRESETS):
            self.view(self.PRESETS[int(char) - 1][0])
            return "break"
        return None

    def step(self, direction: int) -> str:
        """Select the next (or previous) point, nearest first from left to right on screen."""
        if not self.points:
            return "break"
        order = sorted(self.points, key=lambda point: self.project(point["x"], point["y"], point["z"])[0])
        keys = [point["id"] for point in order]
        index = keys.index(self.selected) + direction if self.selected in keys else (0 if direction > 0 else -1)
        self.select(keys[index % len(keys)])
        return "break"

    def _open_selected(self) -> str:
        if self.selected is not None and self.on_open:
            self.on_open(self.selected)
        return "break"

    # ------------------------------------------------------------------ picking
    def _point(self, key: str | None) -> dict[str, Any] | None:
        return next((point for point in self.points if point["id"] == key), None) if key is not None else None

    def _tip(self, found: tuple[str, str]) -> str:
        kind, key = found
        items = self.points if kind == "point" else self.faces
        return next((item.get("tip", "") for item in items if str(item.get("id")) == key), "")

    def _hit(self, x: float, y: float) -> tuple[str, str] | None:
        """The frontmost point under (x, y), else the frontmost face."""
        best: tuple[float, str, str] | None = None
        for depth, kind, key, shape in self._hits:
            if kind == "point":
                px, py, radius = shape
                if math.hypot(x - px, y - py) <= radius + 5 and (best is None or best[1] != "point" or depth > best[0]):
                    best = (depth, kind, key)
            elif (best is None or (best[1] == "face" and depth > best[0])) and inside(x, y, shape):
                best = (depth, kind, key)
        return (best[1], best[2]) if best else None

    # ------------------------------------------------------------------ drawing
    def _size(self) -> tuple[float, float]:
        return max(120, self.winfo_width()), max(120, self.winfo_height())

    def _fog(self, colour: str, depth: float) -> str:
        far = min(1.0, max(0.0, (1.75 - depth) / 3.5))
        return blend(colour, self.background, far * self.FOG)

    def redraw(self) -> None:
        self.delete("all")
        p = self.palette
        width, height = self._size()
        self._hits = []
        if not (self.points or self.faces or self.segments):
            self.create_text(width / 2, height / 2, text=self.empty, fill=p.MUTED, font=("Segoe UI", 10), width=width - 40)
            return
        items: list[tuple[float, int, Callable[[], None]]] = []
        self._labels = []
        # The floor grid and the box edges give the eye something to measure depth against.
        for step in range(5):
            value = -1 + step * .5
            for a, b in (((value, -1, -1), (value, -1, 1)), ((-1, -1, value), (1, -1, value))):
                items.append(self._segment_item({"a": a, "b": b, "colour": blend(p.LINE, self.background, .35), "width": 1}))
        for a, b in (((-1, -1, -1), (-1, 1, -1)), ((1, -1, -1), (1, 1, -1)), ((-1, -1, 1), (-1, 1, 1)), ((1, -1, 1), (1, 1, 1))):
            items.append(self._segment_item({"a": a, "b": b, "colour": blend(p.LINE, self.background, .55), "width": 1, "dash": (2, 4)}))
        for face in self.faces:
            items.append(self._face_item(face))
        for segment in self.segments:
            items.append(self._segment_item(segment))
        for point in self.points:
            items += self._point_items(point)
        items.sort(key=lambda item: (item[0], item[1]))
        for _depth, _layer, draw in items:
            draw()
        self._draw_labels()
        self._draw_axes()
        self._draw_overlay(width, height)

    def _segment_item(self, segment: dict[str, Any]) -> tuple[float, int, Callable[[], None]]:
        x1, y1, d1, _s1 = self.project(*segment["a"])
        x2, y2, d2, _s2 = self.project(*segment["b"])
        depth = (d1 + d2) / 2
        colour = self._fog(segment.get("colour", self.palette.LINE), depth)
        options = {"fill": colour, "width": segment.get("width", 1)}
        if segment.get("dash"):
            options["dash"] = segment["dash"]
        return depth, 0, lambda: self.create_line(x1, y1, x2, y2, **options)

    def _face_item(self, face: dict[str, Any]) -> tuple[float, int, Callable[[], None]]:
        projected = [self.project(*corner) for corner in face["corners"]]
        polygon = [(x, y) for x, y, _depth, _scale in projected]
        depth = sum(item[2] for item in projected) / len(projected)
        key = str(face.get("id", id(face)))
        self._hits.append((depth, "face", key, polygon))
        hovered = self.hover == ("face", key)
        fill = self._fog(face.get("colour", self.palette.GOLD), depth)
        outline = self.palette.WHITE if hovered else blend(fill, self.background, .35)
        flat = [value for xy in polygon for value in xy]
        return depth, 0, lambda: self.create_polygon(*flat, fill=fill, outline=outline, width=2 if hovered else 1)

    def _point_items(self, point: dict[str, Any]) -> list[tuple[float, int, Callable[[], None]]]:
        p = self.palette
        x, y, depth, scale = self.project(point["x"], point["y"], point["z"])
        floor_x, floor_y, floor_depth, floor_scale = self.project(point["x"], -1, point["z"])
        radius = (3.5 + 5.5 * point.get("size", 1.0)) * scale * math.sqrt(self.zoom)
        key = str(point["id"])
        self._hits.append((depth, "point", key, (x, y, radius)))
        colour = self._fog(point.get("colour", p.GOLD), depth)
        selected, hovered = key == self.selected, self.hover == ("point", key)
        shadow = blend(self.background, p.BLACK, .45)
        drop = self._fog(blend(p.LINE, self.background, .2), depth)

        def ground() -> None:
            self.create_oval(floor_x - radius * .9, floor_y - radius * .3 * floor_scale, floor_x + radius * .9,
                             floor_y + radius * .3 * floor_scale, fill=shadow, outline="")
            if abs(floor_y - y) > radius:
                self.create_line(x, y, floor_x, floor_y, fill=drop, dash=(1, 3))

        def body() -> None:
            # A lit sphere: the body, a softer rim and a small highlight toward the light (upper left).
            self.create_oval(x - radius, y - radius, x + radius, y + radius, fill=colour,
                             outline=p.GOLD if selected else p.WHITE if hovered else blend(colour, p.BLACK, .35),
                             width=2.5 if selected else 1.5 if hovered else 1)
            if radius >= 5:
                shine = blend(colour, "#FFFFFF", .45)
                self.create_oval(x - radius * .55, y - radius * .6, x - radius * .1, y - radius * .15, fill=shine, outline="")
            if point.get("label"):
                self._labels.append((2 if selected or hovered else 0, depth, x + radius + 3, y, radius, point["label"]))
        return [(floor_depth - 4, 0, ground), (depth, 1, body)]

    def _draw_labels(self) -> None:
        """Names beside the points, nearest first, skipping any that would overlap one already placed.

        In a tight cluster the far names would otherwise pile into one unreadable blot; the hovered and
        the selected point are always named, and turning or zooming brings the others out."""
        p = self.palette
        placed: list[tuple[float, float, float, float]] = []
        for priority, depth, x, y, _radius, text in sorted(self._labels, key=lambda item: (-item[0], -item[1])):
            box = (x, y - 7, x + len(text) * 6 + 2, y + 7)
            if not priority and any(box[0] < other[2] and other[0] < box[2] and box[1] < other[3] and other[1] < box[3] for other in placed):
                continue
            placed.append(box)
            self.create_text(x, y, text=text, anchor="w", fill=p.WHITE if priority else self._fog(p.WHITE, depth),
                             font=("Segoe UI Semibold" if priority else "Segoe UI", 8))

    def _draw_axes(self) -> None:
        p = self.palette
        ends = {"x": ((-1, -1, 1), (1, -1, 1)), "y": ((-1, -1, -1), (-1, 1, -1)), "z": ((1, -1, -1), (1, -1, 1))}
        for axis, (label, ticks) in self.axes.items():
            start, end = ends[axis]
            x1, y1, _d1, _s1 = self.project(*start)
            x2, y2, _d2, _s2 = self.project(*end)
            self.create_line(x1, y1, x2, y2, fill=p.GOLD_SOFT, width=1.5)
            # The name sits beside the middle of its axis, pushed outward from the box (the y axis: above its top).
            outward = {"x": (0, 0, .3), "y": (0, .14, 0), "z": (.3, 0, 0)}[axis]
            anchor = end if axis == "y" else [(a + b) / 2 for a, b in zip(start, end)]
            mx, my, _dm, _sm = self.project(*[value + shift for value, shift in zip(anchor, outward)])
            self.create_text(mx, my, text=label, fill=p.GOLD_SOFT, font=("Segoe UI Semibold", 8))
            for position, text in ticks:
                spot = [a + (b - a) * (position + 1) / 2 for a, b in zip(start, end)]
                tx, ty, _dt, _st = self.project(*spot)
                self.create_oval(tx - 1.5, ty - 1.5, tx + 1.5, ty + 1.5, fill=p.GOLD_SOFT, outline="")
                self.create_text(tx + (-4 if axis == "y" else 0), ty + (0 if axis == "y" else 4), text=text, fill=p.MUTED,
                                 font=("Segoe UI", 7), anchor="e" if axis == "y" else "n")

    def _draw_overlay(self, width: float, height: float) -> None:
        p = self.palette
        if self.note:
            self.create_text(10, 8, text=self.note, anchor="nw", fill=p.MUTED, font=("Segoe UI", 8), width=width - 20)
        if self.hint:
            self.create_text(10, height - 6, text=HINT + (" · Space spins" if self.motion else ""), anchor="sw",
                             fill=blend(p.MUTED, self.background, .3), font=("Segoe UI", 7), width=width - 20)
        if self.tooltip:
            lines = self.tooltip.splitlines()
            text_width = max(len(line) for line in lines) * 6 + 16
            box_height = len(lines) * 14 + 10
            if self._pointer is not None and self.hover is not None:
                left, top = self._pointer[0] + 16, self._pointer[1] + 12
            else:
                left, top = width - text_width - 10, 26
            left = max(6, min(left, width - text_width - 6))
            top = max(6, min(top, height - box_height - 22))
            self.create_rectangle(left, top, left + text_width, top + box_height, fill=blend(p.PANEL, p.BLACK, .15),
                                  outline=p.GOLD_SOFT)
            self.create_text(left + 8, top + 5, text=self.tooltip, anchor="nw", fill=p.WHITE, font=("Segoe UI", 8))
        if self.focus_get() is self:
            self.create_rectangle(1, 1, width - 2, height - 2, outline=blend(p.GOLD, self.background, .4))

    # ------------------------------------------------------------------ tests and tools
    def snapshot(self) -> dict[str, Any]:
        """Where everything is on screen now: for tests and for checking a view by its numbers."""
        return {"yaw": self.yaw, "pitch": self.pitch, "zoom": self.zoom, "pan": self.pan, "selected": self.selected,
                "hover": self.hover, "tooltip": self.tooltip, "spinning": self.spinning,
                "points": {key: shape for _depth, kind, key, shape in self._hits if kind == "point"}}
