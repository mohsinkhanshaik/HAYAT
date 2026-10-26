"""Reference-inspired neural workspace and charts backed by runtime samples."""
from __future__ import annotations

from collections import deque
import math
import tkinter as tk
from typing import Any, Callable

from app.gui_depth import DepthNavigator, blend, rounded


class NeuralWorkspace(DepthNavigator):
    """Perspective particles with stationary, readable capability controls.

    Camera inertia, keyboard navigation, reduced motion and lifecycle management
    are inherited from the desktop navigator. Particle links are decorative;
    the small status labels are supplied by the runtime.
    """

    CAPABILITIES = (
        ("listening", "Voice", "Speech & listening", "mic"),
        ("vision", "Camera eye", "Camera · images · screen", "eye"),
        ("research", "Research", "Search & understand", "search"),
        ("reasoning", "Planning", "Steps & dependencies", "brain"),
        ("automations", "Automation", "Tasks & workflows", "gear"),
        ("execute", "Tools", "Choose an action", "bolt"),
    )

    def __init__(self, parent: tk.Misc, palette: Any, on_open: Callable[[str], None],
                 on_action: Callable[[str], None], **kwargs: Any) -> None:
        self.on_action = on_action
        self._card_boxes: dict[str, tuple[float, float, float, float]] = {}
        self._status: dict[str, str] = {"listening": "Voice off"}
        self._busy_capability: str | None = None
        self._cap_index = 0
        self._camera_on = False
        self._particles = []
        for i in range(144):
            y = 1 - 2 * (i + .5) / 144
            rr = math.sqrt(1-y*y)
            a = i * math.pi * (3-math.sqrt(5))
            self._particles.append((rr*math.cos(a), y, rr*math.sin(a)))
        # One bounded mesh, reused on every frame; no random flicker.
        edges = set()
        for i, point in enumerate(self._particles):
            nearest = sorted(range(len(self._particles)), key=lambda j: sum(
                (a-b)**2 for a, b in zip(point, self._particles[j])))
            for j in nearest[1:4]:
                edges.add(tuple(sorted((i, j))))
        self._links = sorted(edges)
        # The voice shell: a smaller sphere at the core whose surface is
        # displaced by what the microphone hears and what HAYAT is saying.
        self._voice_points = []
        for i in range(72):
            y = 1 - 2 * (i + .5) / 72
            rr = math.sqrt(1-y*y)
            a = i * math.pi * (3-math.sqrt(5))
            self._voice_points.append((rr*math.cos(a), y, rr*math.sin(a)))
        voice_edges = set()
        for i, point in enumerate(self._voice_points):
            nearest = sorted(range(len(self._voice_points)), key=lambda j: sum(
                (a-b)**2 for a, b in zip(point, self._voice_points[j])))
            for j in nearest[1:4]:
                voice_edges.add(tuple(sorted((i, j))))
        self._voice_links = sorted(voice_edges)
        self._voice_state = "off"
        self._voice_level = 0.0
        self._voice_drawn = 0.0
        self._voice_box = (0.0, 0.0, 0.0)
        #: Returns {"input": 0..1, "output": 0..1}; None when voice is absent.
        self.voice_levels: Callable[[], dict[str, float]] | None = None
        super().__init__(parent, palette, on_open, height=430, **kwargs)
        self.bind("<Return>", lambda _event: self._activate("cap:" + self.CAPABILITIES[self._cap_index][0]))

    def _geometry(self, w: float, h: float) -> tuple[float, float, float, bool]:
        compact = h < 280 or w < 650
        cy = h*(.33 if compact else .46)
        # The nearest particles and the orbits reach about 1.3 radii from the
        # centre in perspective; the old 0.34*h radius pushed the top of the
        # globe past the canvas edge, so the radius also respects the room above.
        radius = min(w*.23, h*(.22 if compact else .34), max(20.0, (cy - 22) / 1.32))
        return w*.5, cy, radius, compact

    def _point(self, point: tuple[float, float, float], w: float, h: float) -> tuple[float, float, float]:
        x, y, z = point
        x, z = x*math.cos(self.angle)+z*math.sin(self.angle), z*math.cos(self.angle)-x*math.sin(self.angle)
        y, z = y*math.cos(self.pitch)-z*math.sin(self.pitch), y*math.sin(self.pitch)+z*math.cos(self.pitch)
        cx, cy, radius, _compact = self._geometry(w, h)
        scale = radius*self.zoom*4.5/(4.5-z)
        return cx+x*scale, cy+y*scale, z

    def _draw_backdrop(self, w: float, h: float) -> None:
        p = self.palette
        key = (w, h, p.PANEL, p.ROYAL_SOFT, p.BLACK)
        if key == self._backdrop:
            return
        self._backdrop = key
        self.delete("backdrop")
        blue = getattr(p, "HOLO", p.ROYAL_SOFT)
        for i in range(40):
            colour = blend(p.BLACK, p.PANEL, .3 + .45*math.sin(i/39*math.pi))
            self.create_rectangle(0, h*i/40, w, h*(i+1)/40+1, fill=colour, outline="", tags="backdrop")
        for i in range(46):
            x, y = (i*173.7+37) % w, (i*i*71.2+29) % max(1, h-20)
            r = 1.3 if i % 7 == 0 else .65
            self.create_oval(x-r, y-r, x+r, y+r, fill=blend(p.PANEL, blue, .24), outline="", tags="backdrop")
        cx, cy, radius, compact = self._geometry(w, h)
        floor = min(h-20, cy+radius*1.36)
        if not compact:
            for i in range(-7, 8):
                self.create_line(cx+i*18, floor-10, cx+i*80, h, fill=blend(p.BLACK, blue, .12), tags="backdrop")
            for y in (floor, floor+17, floor+42, h-5):
                self.create_line(12, y, w-12, y, fill=blend(p.BLACK, blue, .1), tags="backdrop")
        rounded(self, (2, 2, w-3, h-3), radius=18, fill="", outline=blend(p.PANEL, blue, .6), tags="backdrop")
        self.tag_lower("backdrop")

    def redraw(self) -> None:
        w, h = self._size()
        p = self.palette
        blue = getattr(p, "HOLO", p.ROYAL_SOFT)
        self.configure(bg=p.BLACK, highlightbackground=p.LINE)
        self._draw_backdrop(w, h)
        self.delete("scene")
        cx, cy, radius, compact = self._geometry(w, h)
        floor = min(h-20, cy+radius*1.36)
        # A luminous plinth gives the globe height and an obvious rotation axis.
        if not compact:
            for i in range(5, 0, -1):
                rr = radius*(.9+i*.15)
                self.create_oval(cx-rr, floor-rr*.11, cx+rr, floor+rr*.11,
                                 outline=blend(p.PANEL, p.GOLD if i == 2 else blue, .28+i*.08),
                                 width=2 if i == 2 else 1, tags="scene")
            # The stand starts under the globe, not inside it: a pillar drawn
            # through the front of the sphere read as a crack in the mesh.
            top = cy + radius * 1.18
            self.create_line(cx, top, cx, floor, fill=blend(p.PANEL, blue, .22), width=9, tags="scene")
            self.create_line(cx, top, cx, floor, fill=blend(p.PANEL, blue, .8), width=1, tags="scene")
        projected = [self._point(point, w, h) for point in self._particles]
        for i, j in sorted(self._links, key=lambda edge: projected[edge[0]][2]+projected[edge[1]][2]):
            x, y, z = projected[i]
            xx, yy, zz = projected[j]
            near = (z+zz+2)/4
            tint = p.GOLD if (i+j) % 13 == 0 else blue
            self.create_line(x, y, xx, yy, fill=blend(p.PANEL, tint, .08+near*.44), tags="scene")
        # Several tilted orbits are projected in the same coordinate system.
        for orbit in range(3):
            points = []
            for j in range(65):
                t = j*math.tau/64
                tilt = .45+orbit*.77
                pos = (math.cos(t)*1.24, math.sin(t)*math.sin(tilt)*1.22, math.sin(t)*math.cos(tilt)*1.22)
                points.extend(self._point(pos, w, h)[:2])
            tint = p.GOLD_SOFT if orbit == 1 else blue
            self.create_line(*points, fill=blend(p.PANEL, tint, .55), width=1, smooth=True, tags="scene")
        for i in sorted(range(len(projected)), key=lambda j: projected[j][2]):
            x, y, z = projected[i]
            near = (z+1)/2
            r = .7+near*1.4
            tint = p.GOLD_SOFT if i % 9 == 0 else blue
            if i % 9 == 0 or i % 17 == 0:
                self.create_oval(x-6, y-6, x+6, y+6, fill=blend(p.PANEL, tint, .1), outline="", tags="scene")
                self.create_line(x-7, y, x+7, y, fill=blend(p.PANEL, tint, .4), tags="scene")
                self.create_line(x, y-7, x, y+7, fill=blend(p.PANEL, tint, .4), tags="scene")
            self.create_oval(x-r, y-r, x+r, y+r, fill=blend(p.PANEL, tint, .25+near*.7), outline="", tags="scene")
        self._draw_voice(cx, cy, radius)
        self._positions = {key: self._project(i, w, h) for i, (key, _title) in enumerate(self.VIEWS)}
        for key, (x, y, z) in self._positions.items():
            colour = p.GOLD_SOFT if key == self.active else blue
            r = 4 if key == self._hover else 2.5
            self.create_oval(x-r, y-r, x+r, y+r, fill=colour, outline="", tags="scene")
        # The voice caption stays readable: a view dot drawn later sat on its first letter.
        self.tag_raise("voice_caption")
        self._draw_cards(w, h, cx, cy, radius, compact)
        heading = "←/→ choose · Enter open · ↑/↓ tilt · Home reset" if self._focused else "HAYAT  /  NEURAL WORKSPACE"
        self.create_text(18, 16, text=heading, anchor="nw", fill=blue,
                         font=("Segoe UI Semibold", 8), tags="scene")
        if h > 170:
            self.create_text(w-18, 16, text=f"{round(self.zoom*100)}%", anchor="ne", fill=p.MUTED,
                             font=("Segoe UI", 8), tags="scene")
        if self._hover in dict(self.VIEWS):
            self._draw_label(self._hover, w)

    def _draw_cards(self, w: float, h: float, cx: float, cy: float, radius: float, compact: bool) -> None:
        p = self.palette
        blue = getattr(p, "HOLO", p.ROYAL_SOFT)
        self._card_boxes.clear()
        for i, (key, title, subtitle, icon) in enumerate(self.CAPABILITIES):
            if compact:
                cw = (w-40)/3
                height = 40 if h >= 250 else 31
                x = 12 + (i % 3)*(cw+5)
                y = h-(2-i//3)*(height+6)-8
            else:
                cw, height = min(184, w*.235), 64
                x = 20 if i < 3 else w-cw-20
                y = h*(.20+(i % 3)*.235)-height/2
            box = (x, y, x+cw, y+height)
            self._card_boxes[key] = box
            lit = (self._hover == "cap:" + key or self._busy_capability == key
                   or (self._focused and self._cap_index == i))
            colour = p.GOLD_SOFT if lit else blue
            if not compact:
                edge = x+cw if i < 3 else x
                end = cx-radius*.75 if i < 3 else cx+radius*.75
                self.create_line(edge, y+height/2, end, cy+(i%3-1)*radius*.5,
                                 fill=blend(p.PANEL, colour, .55), arrow="last", tags="scene")
            for glow in (5, 3, 1):
                rounded(self, (x-glow, y-glow, x+cw+glow, y+height+glow), radius=13,
                        fill="", outline=blend(p.PANEL, colour, .08+(5-glow)*.045), width=2, tags="scene")
            rounded(self, box, radius=11, fill=blend(p.PANEL, p.ROYAL, .10 if lit else .035),
                    outline=blend(p.PANEL, colour, .85 if lit else .65), tags="scene")
            self._icon(icon, x+22, y+height/2, colour, 10 if compact else 12)
            self.create_text(x+43, y+height/2 if compact else y+21, text=title, anchor="w", fill=p.WHITE,
                             font=("Segoe UI Semibold", 9 if compact else 10), tags="scene")
            if not compact:
                self.create_text(x+43, y+43, text=self._status.get(key, subtitle), anchor="w",
                                 fill=colour if lit else p.MUTED, font=("Segoe UI", 8), tags="scene")

    def _icon(self, kind: str, x: float, y: float, colour: str, r: float) -> None:
        opts = dict(fill=colour, width=2, tags="scene")
        if kind == "mic":
            rounded(self, (x-r*.4, y-r, x+r*.4, y+r*.35), radius=5, fill="", outline=colour, width=2, tags="scene")
            self.create_arc(x-r*.7, y-r*.25, x+r*.7, y+r*.7, start=180, extent=180, style="arc", outline=colour, width=2, tags="scene")
            self.create_line(x, y+r*.65, x, y+r, **opts)
        elif kind == "eye":
            if not self._camera_on:
                self.create_line(x-r, y-2, x, y+r*.6, x+r, y-2, smooth=True, **opts)
                for dx in (-r*.6, 0, r*.6):
                    self.create_line(x+dx, y+r*.3, x+dx-2, y+r*.7, **opts)
                return
            self.create_polygon(x-r, y, x, y-r*.7, x+r, y, x, y+r*.7, smooth=True, fill="", outline=colour, width=2, tags="scene")
            self.create_oval(x-3, y-3, x+3, y+3, outline=colour, width=2, tags="scene")
        elif kind == "search":
            self.create_oval(x-r*.7, y-r*.7, x+r*.3, y+r*.3, outline=colour, width=2, tags="scene")
            self.create_line(x+r*.2, y+r*.2, x+r*.8, y+r*.8, **opts)
        elif kind == "bolt":
            self.create_polygon(x+2, y-r, x-r*.6, y+2, x, y+2, x-2, y+r,
                                x+r*.6, y-2, x, y-2, fill=colour, tags="scene")
        else:
            for i in range(6):
                a = i*math.tau/6
                xx, yy = x+math.cos(a)*r*.7, y+math.sin(a)*r*.7
                self.create_line(x, y, xx, yy, **opts)
                self.create_oval(xx-2, yy-2, xx+2, yy+2, fill=colour, outline="", tags="scene")
            self.create_oval(x-4, y-4, x+4, y+4, outline=colour, width=2, tags="scene")

    def _hit(self, x: float, y: float) -> str | None:
        for key, (left, top, right, bottom) in self._card_boxes.items():
            if left <= x <= right and top <= y <= bottom:
                return "cap:" + key
        node = super()._hit(x, y)
        if node is None:
            vx, vy, size = self._voice_box
            if size and math.hypot(x - vx, y - vy) <= size * 1.1:
                # The voice shell is the Voice control, in 3D.
                return "cap:listening"
        return node

    def _activate(self, key: str) -> None:
        if key.startswith("cap:"):
            self._cap_index = [row[0] for row in self.CAPABILITIES].index(key[4:])
            self.on_action(key[4:])
        else:
            super()._activate(key)

    def _step_key(self, direction: int) -> str:
        self._cap_index = (self._cap_index + direction) % len(self.CAPABILITIES)
        self.redraw()
        return "break"

    def _digit(self, event: Any) -> str | None:
        if getattr(event, "state", 0) & (0x0004 | 0x20000):
            return None
        char = str(getattr(event, "char", ""))
        if char not in "123456" or len(char) != 1:
            return None
        self._cap_index = int(char) - 1
        self._activate("cap:" + self.CAPABILITIES[self._cap_index][0])
        return "break"

    def set_status(self, activity: dict[str, Any], voice: dict[str, Any], camera_on: bool) -> None:
        self._camera_on = bool(camera_on)
        running = activity.get("running") or []
        kind = str(running[-1].get("kind", "")) if running else ""
        busy = {"model": "reasoning", "tool": "execute", "research": "research", "learning": "research"}.get(kind)
        state = self.voice_form(voice)
        status = {"listening": self.VOICE_FORMS[state][2],
                  "vision": "Camera on" if camera_on else "Camera off · eye closed"}
        voice_changed = state != self._voice_state
        self._voice_state = state
        if status != self._status or busy != self._busy_capability:
            self._status, self._busy_capability = status, busy
            if self.winfo_ismapped():
                self.redraw()
        elif self.winfo_ismapped() and not self.motion:
            # No animation loop runs with motion off, so the shell follows
            # state and loudness at the window's own polling rate instead.
            level = self._sample_voice()
            if voice_changed or abs(level - self._voice_drawn) > .03:
                self._redraw_voice()

    # ------------------------------------------------------------- voice

    #: Voice state -> (palette colour, surface motion, caption). Blue is the
    #: owner speaking, gold is HAYAT, olive is healthy and waiting.
    VOICE_FORMS = {
        "off": ("MUTED", "still", "Voice off"),
        "stopped": ("MUTED", "still", "Voice stopped"),
        "idle": ("OLIVE_SOFT", "breathe", "Ready to listen"),
        "ready": ("OLIVE_SOFT", "breathe", "Ready to listen"),
        "completed": ("OLIVE_SOFT", "breathe", "Ready to listen"),
        "listening": ("HOLO", "ripple", "Listening"),
        "transcribing": ("GOLD_SOFT", "swirl", "Transcribing"),
        "thinking": ("GOLD_SOFT", "swirl", "Thinking"),
        "acting": ("GOLD_SOFT", "swirl", "Working"),
        "verifying": ("GOLD_SOFT", "swirl", "Checking the result"),
        "approval_required": ("GOLD", "pulse", "Waiting for your approval"),
        "speaking": ("GOLD", "wave", "Speaking"),
        "error": ("ALERT", "still", "Voice needs attention"),
    }

    @classmethod
    def voice_form(cls, voice: dict[str, Any]) -> str:
        """Which form the shell takes for one voice status report."""
        state = str(voice.get("state", "off"))
        if not voice.get("enabled") and state not in {"speaking", "error"}:
            return "off"
        return state if state in cls.VOICE_FORMS else "idle"

    def _sample_voice(self) -> float:
        """Read live loudness, attacking fast and releasing slowly."""
        levels: dict[str, float] = {}
        if self.voice_levels is not None:
            try:
                levels = self.voice_levels() or {}
            except Exception:
                levels = {}
        heard, spoken = float(levels.get("input", 0) or 0), float(levels.get("output", 0) or 0)
        if spoken > .02 and self._voice_state not in {"approval_required", "error"}:
            # Audio really is playing, whatever the last polled state said.
            self._voice_state = "speaking"
        form = self.VOICE_FORMS[self._voice_state][1]
        target = spoken if form == "wave" else heard if form in {"ripple", "breathe"} else 0.0
        target = max(0.0, min(1.0, target))
        self._voice_level += (target - self._voice_level) * (.6 if target > self._voice_level else .25)
        if self._voice_level < .002:
            self._voice_level = 0.0
        return self._voice_level

    @staticmethod
    def _voice_shape(form: str, point: tuple[float, float, float], t: float, level: float) -> float:
        """Radial displacement of one shell point. Amplitude follows loudness."""
        x, y, z = point
        if form == "ripple":
            # Rings travelling up the shell, as tall as what is being heard.
            return (.04 + .3 * level) * math.sin(y * 6 - t * 7)
        if form == "wave":
            # Lobes turning round the equator, as large as the spoken audio.
            return (.05 + .5 * level) * math.sin(3 * math.atan2(z, x) + t * 6) * (1 - y * y)
        if form == "swirl":
            return .09 * math.sin(2 * math.atan2(z, x) + y * 4 - t * 4)
        if form == "pulse":
            return .1 * math.sin(t * 5)
        if form == "breathe":
            return .035 * math.sin(t * 1.6) + .3 * level * math.sin(y * 7 - t * 7)
        return 0.0

    def _draw_voice(self, cx: float, cy: float, radius: float) -> None:
        p = self.palette
        self.delete("voice")
        colour_name, form, caption = self.VOICE_FORMS[self._voice_state]
        colour = getattr(p, colour_name, p.ROYAL_SOFT)
        live = self.motion and form != "still"
        t = self._clock() if live else 0.0
        level = self._voice_level
        self._voice_drawn = level
        size = max(16.0, min(radius * .32, getattr(self, "VOICE_SIZE_LIMIT", 50.0))) * self.zoom * (.8 if form == "still" else 1.0)
        self._voice_box = (cx, cy, size)
        tags = ("scene", "voice")
        for i in range(6, 0, -1):
            r = size * (.3 + i * .12) * (1 + level * .35)
            self.create_oval(cx-r, cy-r, cx+r, cy+r, outline="", tags=tags,
                             fill=blend(p.PANEL, colour, (1 - i / 7) * (.28 if form == "still" else .55)))
        spin = self.angle + (t * .5 if live else 0.0)
        cos_s, sin_s = math.cos(spin), math.sin(spin)
        cos_p, sin_p = math.cos(self.pitch), math.sin(self.pitch)
        projected = []
        for point in self._voice_points:
            stretch = 1 + self._voice_shape(form, point, t, level)
            x, y, z = point
            x, z = x * cos_s + z * sin_s, z * cos_s - x * sin_s
            y, z = y * cos_p - z * sin_p, y * sin_p + z * cos_p
            depth = 3.2 / (3.2 - z)
            projected.append((cx + x * size * stretch * depth, cy + y * size * stretch * depth, z))
        # Depth shades are quantised so ~140 links cost 12 blends, not 140.
        reach = .45 if form == "still" else .8
        shades = [blend(p.PANEL, colour, .12 + step / 11 * reach) for step in range(12)]
        for i, j in sorted(self._voice_links, key=lambda edge: projected[edge[0]][2] + projected[edge[1]][2]):
            x, y, z = projected[i]
            xx, yy, zz = projected[j]
            near = (z + zz + 2) / 4
            self.create_line(x, y, xx, yy, tags=tags, width=1.6 if near > .62 else 1,
                             fill=shades[max(0, min(11, round(near * 11)))])
        # The ring's brightness and width are the loudness, read directly.
        ring = size * 1.45
        self.create_oval(cx - ring, cy - ring * .24, cx + ring, cy + ring * .24, tags=tags,
                         outline=blend(p.PANEL, colour, .25 + .75 * level), width=1 + 2.5 * level)
        # A dark pill keeps the caption readable over the mesh behind it.
        caption_y = cy + size * 1.45 + 11
        caption_tags = (*tags, "voice_caption")
        item = self.create_text(cx, caption_y, text=caption, tags=caption_tags,
                                fill=colour, font=("Segoe UI Semibold", 8))
        left, top, right, bottom = self.bbox(item)
        rounded(self, (left - 8, top - 2, right + 8, bottom + 2), radius=8, tags=caption_tags,
                fill=blend(p.BLACK, p.PANEL, .35), outline=blend(p.PANEL, colour, .45))
        self.tag_raise(item)

    def _redraw_voice(self) -> None:
        w, h = self._size()
        cx, cy, radius, _compact = self._geometry(w, h)
        self._draw_voice(cx, cy, radius)
        self.tag_raise("label")

    def _overlay(self, *, redrawn: bool) -> bool:
        before = self._voice_level
        level = self._sample_voice()
        form = self.VOICE_FORMS[self._voice_state][1]
        moving = form in {"ripple", "wave", "swirl", "pulse"} or level > .01
        if not redrawn and (moving or form == "breathe" or abs(level - before) > .005):
            self._redraw_voice()
        return moving


class ResourceCharts(tk.Frame):
    """Four small charts, recording each received health sample exactly once."""

    def __init__(self, parent: tk.Misc, palette: Any) -> None:
        super().__init__(parent, bg=palette.PANEL)
        self.palette = palette
        self.samples = {key: deque(maxlen=60) for key in ("cpu", "gpu", "ram", "disk")}
        self._latest: dict[str, Any] | None = None
        self.canvases = {}
        for i, key in enumerate(self.samples):
            canvas = tk.Canvas(self, width=150, height=68, bg=palette.PANEL_2,
                               highlightthickness=1, highlightbackground=palette.LINE)
            canvas.grid(row=i//2, column=i%2, sticky="ew", padx=3, pady=3)
            canvas.bind("<Configure>", lambda _event, k=key: self._draw(k))
            self.canvases[key] = canvas
        self.grid_columnconfigure(0, weight=1, uniform="charts")
        self.grid_columnconfigure(1, weight=1, uniform="charts")
        self.refresh_theme()

    def set_metrics(self, metrics: dict[str, Any]) -> None:
        if metrics is self._latest:
            return
        self._latest = metrics
        for key, samples in self.samples.items():
            value = metrics.get(key)
            samples.append(min(100., max(0., float(value))) if isinstance(value, (float, int)) and math.isfinite(value) else None)
            self._draw(key)

    def _draw(self, key: str) -> None:
        canvas, p = self.canvases[key], self.palette
        w, h = max(120, canvas.winfo_width()), max(70, canvas.winfo_height())
        colour = {"cpu": getattr(p, "HOLO", p.ROYAL_SOFT), "gpu": p.OLIVE_SOFT,
                  "ram": p.ROYAL_SOFT, "disk": p.GOLD_SOFT}[key]
        canvas.configure(bg=p.PANEL_2, highlightbackground=p.LINE)
        canvas.delete("all")
        samples = list(self.samples[key])
        value = samples[-1] if samples else None
        name = "Storage" if key == "disk" else key.upper()
        canvas.create_text(9, 9, text=f"{name}  {value:.0f}%" if value is not None else f"{name}  --",
                           anchor="nw", fill=p.WHITE, font=("Segoe UI Semibold", 9))
        for y in (h-10, h-27, h-44):
            canvas.create_line(8, y, w-8, y, fill=blend(p.PANEL_2, colour, .12))
        if not samples or value is None:
            canvas.create_text(9, h-15, text="Awaiting measurement", anchor="sw", fill=p.MUTED, font=("Segoe UI", 8))
        segment = []
        for i, sample in enumerate(samples):
            if sample is None:
                if len(segment) >= 4:
                    canvas.create_line(*segment, fill=colour, width=2)
                segment = []
                continue
            x = 8+i*(w-16)/59
            y = h-10-sample*(h-40)/100
            segment.extend((x, y))
        if len(segment) >= 4:
            canvas.create_line(*segment, fill=colour, width=2)
        elif len(segment) == 2:
            x, y = segment
            canvas.create_oval(x-2, y-2, x+2, y+2, fill=colour, outline="")

    def refresh_theme(self) -> None:
        self.configure(bg=self.palette.PANEL)
        for key in self.canvases:
            self._draw(key)
