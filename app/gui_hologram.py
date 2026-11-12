"""Reference-inspired neural workspace and charts backed by runtime samples."""
from __future__ import annotations

from collections import deque
import logging
import math
import textwrap
import threading
import time
import tkinter as tk
from typing import Any, Callable

import numpy as np

from app import capability_constellation as constellation_model
from app.gui_depth import DepthNavigator, blend, rounded
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class NeuralWorkspace(DepthNavigator):
    """Perspective particles with stationary, readable capability controls.

    Camera inertia, keyboard navigation, reduced motion and lifecycle management
    are inherited from the desktop navigator. Every particle on the sphere is
    one real part of HAYAT (app/capability_constellation.py): a tool, an area
    hub, a data feed or a model role, clustered by area. Every link is a real
    relation: same area, or recorded as used together. A particle glows while
    its tool runs and flashes red when the call fails; hovering explains it,
    clicking opens the explanation in chat.
    """

    #: Seconds a finished call keeps fading, and a failed call stays red.
    GLOW_FADE, FAIL_SECONDS = 2.5, 1.8
    #: Halo rings drawn round the brightest glowing particles.
    HALOS = 24
    #: Below this sphere radius in pixels the fine mesh links (each point's
    #: 2nd/3rd nearest neighbour) are left out: Tk's painting of ~900 lines
    #: measured ~13 ms a frame, and at Home size the mesh is too dense to read.
    #: The spanning tree, co-use, model and hub links always draw.
    DETAIL_RADIUS = 165
    #: A link is drawn only while both ends face the viewer.
    FACING = 0.0
    #: Tool track records are re-read from the record at most this often.
    RECORD_SECONDS = 30.0

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
        # The sphere's inventory. Until a runtime is attached it holds the
        # capability areas themselves (real catalog entries, no tools yet);
        # attach_runtime() loads the full inventory in the background.
        self._runtime: Any = None
        #: Called with (title, work) to show an explanation in chat.
        self.explain: Callable[[str, Callable[[], str]], None] | None = None
        self._loaded: constellation_model.Constellation | None = None
        self._heard: deque[tuple[str, str, float]] = deque(maxlen=512)
        self._unsubscribe: list[Callable[[], None]] = []
        self._glow: dict[int, tuple[str, float]] = {}
        self._running: set[int] = set()
        self._session: dict[str, list[int]] = {}
        self._fresh: dict[str, tuple[float, dict[str, Any]]] = {}
        self._pool: dict[str, Any] | None = None
        self._shades: tuple[Any, ...] | None = None
        self._sphere_hidden = False
        self._projected: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self.frame_ms: deque[float] = deque(maxlen=120)
        self._set_constellation(constellation_model.build({}))
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
        self.bind("<Destroy>", self._detach, add="+")

    # ------------------------------------------------------- constellation

    def attach_runtime(self, runtime: Any, explain: Callable[[str, Callable[[], str]], None] | None = None) -> None:
        """Show HAYAT's real inventory and light it from real tool calls.

        The inventory is read off the Tk loop (the event log can be large);
        it is picked up by the next status tick. Tool events arrive on the
        publishing thread and are only queued here; Tk is touched on the
        loop alone.
        """
        self._runtime = runtime
        if explain is not None:
            self.explain = explain
        events = getattr(runtime, "events", None)
        if events is not None and hasattr(events, "subscribe"):
            for name in ("TOOL_STARTED", "TOOL_COMPLETED", "TOOL_FAILED"):
                try:
                    self._unsubscribe.append(events.subscribe(name, self._hear))
                except Exception:
                    log_swallowed(_log, f"subscribing the Home sphere to {name}")
        threading.Thread(target=self._load, args=(runtime,), name="hayat-home-sphere", daemon=True).start()

    def _load(self, runtime: Any) -> None:
        try:
            self._loaded = constellation_model.from_runtime(runtime)
        except Exception:
            log_swallowed(_log, "building the Home sphere inventory", level=logging.WARNING)

    def _hear(self, record: dict[str, Any]) -> None:
        # Publishing thread: a deque append is atomic, nothing else happens here.
        tool = str((record.get("payload") or {}).get("tool") or "")
        if tool:
            self._heard.append((str(record.get("event_type", "")), tool, time.monotonic()))

    def _detach(self, event: Any = None) -> None:
        if event is not None and getattr(event, "widget", self) is not self:
            return
        for unsubscribe in self._unsubscribe:
            try:
                unsubscribe()
            except Exception:
                log_swallowed(_log, "unsubscribing the Home sphere")
        self._unsubscribe.clear()

    def set_constellation(self, constellation: constellation_model.Constellation) -> None:
        """Replace the inventory on the sphere and repaint it."""
        self._set_constellation(constellation)
        self.redraw()

    def _set_constellation(self, constellation: constellation_model.Constellation) -> None:
        self.constellation = constellation
        particles = constellation.particles
        self._particles = [particle.point for particle in particles]
        self._links = [(link.a, link.b) for link in constellation.links]
        self._points = np.array(self._particles or [(0.0, 0.0, 1.0)], dtype=float)[:len(particles)]
        regions = list(dict.fromkeys(particle.region for particle in particles))
        # Neighbouring regions take different hues, so the areas read as a map.
        self._hue = np.array([regions.index(particle.region) % 3 for particle in particles], dtype=int)
        records = constellation.records
        size = []
        bright = []
        for particle in particles:
            record = records.get(particle.key) or {}
            if particle.kind == "area":
                size.append(3.0)
                bright.append(1)
            elif particle.kind == "model":
                size.append(2.3)
                bright.append(1)
            elif particle.kind == "feed":
                size.append(1.2)
                bright.append(1 if record.get("successes") else 0)
            else:
                calls = int(record.get("calls", 0))
                # Busier tools are larger: the sphere shows where the work goes.
                size.append(1.15 + min(1.6, .55 * math.log10(1 + calls)))
                bright.append(1 if calls else 0)
        self._size_base = np.array(size, dtype=float)
        self._bright = np.array(bright, dtype=int)
        self._kind_area = np.array([particle.kind == "area" for particle in particles], dtype=bool)
        self._link_a = np.array([link.a for link in constellation.links], dtype=int)
        self._link_b = np.array([link.b for link in constellation.links], dtype=int)
        self._link_detail = np.array([link.kind == "area" and link.weight < 1 for link in constellation.links], dtype=bool)
        kinds = {"area": 0, "together": 1, "sequence": 2, "areas": 1, "same_model": 3, "built_on": 3, "reads": 3}
        self._link_kind = np.array([kinds.get(link.kind, 0) for link in constellation.links], dtype=int)
        self._region_members: dict[int, list[int]] = {}
        hubs = {particle.region: i for i, particle in enumerate(particles) if particle.kind == "area"}
        self._hub_of = [hubs.get(particle.region, -1) for particle in particles]
        self._tools = {particle.name: i for i, particle in enumerate(particles) if particle.kind == "tool"}
        self._models = [(i, dict(particle.facts).get("model", "")) for i, particle in enumerate(particles)
                        if particle.kind == "model"]
        self._glow = {}
        self._running = set()
        self._projected = None
        if self._pool is not None:
            self.delete("sphere")
            self._pool = None

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
        if h < 170:
            # Large text or a short window can leave only a small action
            # strip. Keep the six controls; decorative geometry and its title
            # must not draw across them.
            self.delete('voice')
            self._positions = {}
            self._voice_box = (0, 0, 0)
            self._hide_sphere()
            self._draw_cards(w, h, cx, cy, radius, True)
            return
        started = time.perf_counter()
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
        self._paint_sphere(w, h, geometry=True)
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
        self._draw_voice(cx, cy, radius)
        self._positions = {key: self._project(i, w, h) for i, (key, _title) in enumerate(self.VIEWS)}
        for key, (x, y, z) in self._positions.items():
            colour = p.GOLD_SOFT if key == self.active else blue
            r = 4 if key == self._hover else 2.5
            self.create_oval(x-r, y-r, x+r, y+r, fill=colour, outline="", tags="scene")
        # The voice caption stays readable: a view dot drawn later sat on its first letter.
        self.tag_raise("voice_caption")
        self._draw_cards(w, h, cx, cy, radius, compact)
        counts = self.constellation.counts()
        inventory = (f"  ·  {len(self.constellation.particles)} PARTS · {counts.get('tool', 0)} TOOLS · "
                     f"{len(self.constellation.links)} LINKS") if not compact else ""
        heading = ("←/→ choose · Enter open · ↑/↓ tilt · Home reset" if self._focused
                   else "HAYAT  /  NEURAL WORKSPACE" + inventory)
        self.create_text(18, 16, text=heading, anchor="nw", fill=blue,
                         font=("Segoe UI Semibold", 8), tags="scene")
        if h > 170:
            self.create_text(w-18, 16, text=f"{round(self.zoom*100)}%", anchor="ne", fill=p.MUTED,
                             font=("Segoe UI", 8), tags="scene")
        if self._hover in dict(self.VIEWS):
            self._draw_label(self._hover, w)
        elif self._hover and self.constellation.index(self._hover) is not None:
            self._draw_tip(self._hover, w, h)
        self.frame_ms.append((time.perf_counter() - started) * 1000)

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
            node = self._particle_at(x, y)
        return node

    def _particle_at(self, x: float, y: float) -> str | None:
        """The facing particle under the pointer, nearest first."""
        if self._projected is None or self._sphere_hidden or not len(self.constellation.particles):
            return None
        sx, sy, z = self._projected
        distance = (sx - x) ** 2 + (sy - y) ** 2
        distance = np.where(z > -.05, distance, np.inf)
        i = int(np.argmin(distance))
        reach = max(6.0, float(self._radii[i]) + 3) if getattr(self, "_radii", None) is not None else 6.0
        return self.constellation.particles[i].key if distance[i] <= reach * reach else None

    def _activate(self, key: str) -> None:
        if key.startswith("cap:"):
            self._cap_index = [row[0] for row in self.CAPABILITIES].index(key[4:])
            self.on_action(key[4:])
        elif self.constellation.index(key) is not None:
            self.open_particle(key)
        else:
            super()._activate(key)

    def open_particle(self, key: str) -> None:
        """Explain one part in chat; without a chat, open the tool browser."""
        i = self.constellation.index(key)
        if i is None:
            return
        particle = self.constellation.particles[i]
        if self.explain is None:
            self.on_open("toolbox")
            return
        constellation, runtime = self.constellation, self._runtime
        self.explain(f"Explain {particle.name}",
                     lambda: constellation_model.explain(constellation, key, runtime))

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
        if self._loaded is not None:
            loaded, self._loaded = self._loaded, None
            self._set_constellation(loaded)
            self._status = {}  # forces the full repaint below
        glow_changed = self._follow_work(running)
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
            if glow_changed:
                self._repaint_glow()

    # ------------------------------------------------------- live glow

    def _follow_work(self, running: list[dict[str, Any]] | None) -> bool:
        """Light the particles of what is really running. True if any glow changed.

        ``running`` is the activity register's list; None means "no new
        reading" (an animation frame), so only events and fading apply.
        """
        before = dict(self._glow)
        now = time.monotonic()
        self._drain_heard(now)
        if running is not None:
            self._follow_running(running, now)
        for i, (state, since) in list(self._glow.items()):
            if state == "run" and i not in self._running and now - since > 120:
                # A start whose end was never heard: do not glow forever.
                self._glow[i] = ("done", now)
            elif state != "run" and self._intensity(state, since, now) <= 0:
                del self._glow[i]
        return before != self._glow or any(state != "run" for state, _since in self._glow.values())

    def _drain_heard(self, now: float) -> None:
        while self._heard:
            kind, tool, _at = self._heard.popleft()
            i = self._tools.get(tool)
            calls = self._session.setdefault(tool, [0, 0])
            if kind == "TOOL_STARTED":
                calls[0] += 1
            elif kind == "TOOL_FAILED":
                calls[1] += 1
            if i is None:
                continue
            if kind == "TOOL_STARTED":
                self._glow[i] = ("run", now)
            elif kind == "TOOL_FAILED":
                self._glow[i] = ("fail", now)
            elif kind == "TOOL_COMPLETED" and self._glow.get(i, ("",))[0] != "fail":
                self._glow[i] = ("done", now)

    def _follow_running(self, running: list[dict[str, Any]], now: float) -> None:
        live: set[int] = set()
        for item in running:
            kind, label, detail = str(item.get("kind", "")), str(item.get("label", "")), str(item.get("detail", ""))
            if kind == "tool" and label in self._tools:
                live.add(self._tools[label])
            elif kind == "model":
                live.update(i for i, model in self._models if model and model in detail)
                hub = self.constellation.index("area:chat")
                if hub is not None:
                    live.add(hub)
            elif kind in {"research", "learning"}:
                hub = self.constellation.index(f"area:{kind}")
                if hub is not None:
                    live.add(hub)
        # Voice is not lit here: the voice shell at the core already shows it.
        for i in live:
            state, since = self._glow.get(i, ("", now))
            if state == "fail" and i in self._running:
                continue  # the failure flash outranks "still listed as running"
            self._glow[i] = ("run", since if state == "run" else now)
        for i in self._running - live:
            # Finished without an event saying how (models, hubs): fade out.
            if self._glow.get(i, ("",))[0] == "run":
                self._glow[i] = ("done", now)
        self._running = live

    def _intensity(self, state: str, since: float, now: float) -> float:
        if state == "run":
            return 1.0
        if state == "fail":
            age = now - since
            return 1.0 if age < self.FAIL_SECONDS * .5 else max(0.0, 1 - (age - self.FAIL_SECONDS * .5) / (self.FAIL_SECONDS * .5))
        return max(0.0, 1 - (now - since) / self.GLOW_FADE)

    def _repaint_glow(self) -> None:
        if self._projected is None or self._sphere_hidden or not self.winfo_ismapped():
            return
        w, h = self._size()
        self._paint_sphere(w, h, geometry=False)

    # ---------------------------------------------------------- painting
    #
    # The sphere is a fixed pool of canvas items created once per inventory
    # and moved with one batched Tcl script per frame. Re-creating ~2,300
    # items every frame (as the old 144-point globe did with its 400) costs
    # tens of milliseconds; moving them costs a few. Colours are quantised to
    # depth bands, and an item is recoloured only when its band changes.

    DEPTH_BANDS = 6

    def _palette_shades(self) -> tuple[Any, ...]:
        p = self.palette
        signature = (p.PANEL, getattr(p, "HOLO", p.ROYAL_SOFT), p.ROYAL_SOFT, p.OLIVE_SOFT, p.GOLD, p.GOLD_SOFT,
                     getattr(p, "ALERT", "#F05D5E"), p.MUTED)
        if self._shades is not None and self._shades[0] == signature:
            return self._shades
        panel, holo, royal, olive, gold, gold_soft, alert, _muted = signature
        hues = (holo, royal, olive)
        bands = self.DEPTH_BANDS
        # dot[hue][bright][band]; a used part is brighter than one never used.
        dot = [[[blend(panel, hue, (.16 + .36 * band / (bands - 1)) * (.55 if not bright else 1)
                       + (.0 if not bright else .3 * band / (bands - 1)))
                 for band in range(bands)] for bright in (0, 1)] for hue in hues]
        hub = [blend(panel, gold_soft, .3 + .65 * band / (bands - 1)) for band in range(bands)]
        # glow[state][level 1..4]
        glow = {"run": [blend(gold_soft, gold, 0)] + [blend(panel, gold_soft, .45 + .55 * level / 4) for level in range(1, 5)],
                "done": [gold] + [blend(panel, gold_soft, .35 + .6 * level / 4) for level in range(1, 5)],
                "fail": [alert] + [blend(panel, alert, .35 + .65 * level / 4) for level in range(1, 5)]}
        # line[kind][band]: area links take the region hue, co-use links gold.
        tints = (holo, gold_soft, gold, olive)
        line = [[blend(panel, tint, .07 + .38 * band / (bands - 1)) for band in range(bands)] for tint in tints]
        area_lines = [[blend(panel, hue, .06 + .36 * band / (bands - 1)) for band in range(bands)] for hue in hues]
        self._shades = (signature, dot, hub, glow, line, area_lines)
        if self._pool is not None:
            # A new theme: every item must take its new colour.
            self._pool["dot_colour"][:] = -1
            self._pool["line_colour"][:] = -1
            for item in self._pool["labels"]:
                self.itemconfigure(item, fill=blend(panel, gold_soft, .8))
        return self._shades

    def _ensure_pool(self) -> dict[str, Any]:
        if self._pool is not None:
            return self._pool
        n, m = len(self.constellation.particles), len(self.constellation.links)
        tags = ("sphere",)
        widths = []
        for link in self.constellation.links:
            widths.append(1 if link.kind == "area" else min(3.0, 1 + .6 * math.log10(1 + link.weight)))
        lines = [self.create_line(0, 0, 0, 0, width=widths[k], state="hidden", tags=tags) for k in range(m)]
        # Tools and feeds are 1-3 px: a filled square looks the same and Tk
        # paints it several times faster than an oval. Hubs and models stay round.
        dots = [(self.create_oval if self._kind_area[i] or self.constellation.particles[i].kind == "model"
                 else self.create_rectangle)(0, 0, 0, 0, outline="", width=0, tags=tags) for i in range(n)]
        hubs = [i for i in range(n) if self._kind_area[i]]
        rings = [self.create_oval(0, 0, 0, 0, outline="", width=1, tags=tags) for _ in hubs]
        labels = [self.create_text(0, 0, text=self.constellation.particles[i].region_title.upper(), state="hidden",
                                   font=("Segoe UI Semibold", 7), tags=tags) for i in hubs]
        halos = [self.create_oval(0, 0, 0, 0, outline="", width=2, state="hidden", tags=tags) for _ in range(self.HALOS)]
        self._pool = {
            "lines": np.array(lines, dtype=int), "dots": np.array(dots, dtype=int), "hubs": hubs,
            "rings": rings, "labels": labels, "halos": halos,
            "dot_colour": np.full(n, -1, dtype=int), "line_colour": np.full(m, -1, dtype=int),
            "line_shown": np.zeros(m, dtype=bool), "label_shown": [False] * len(hubs), "ring_lit": [False] * len(hubs),
            "halo_shown": [False] * self.HALOS,
        }
        self._shades = None
        self._palette_shades()
        return self._pool

    def _hide_sphere(self) -> None:
        if self._pool is not None and not self._sphere_hidden:
            self.itemconfigure("sphere", state="hidden")
            self._pool["line_shown"][:] = False
            self._pool["label_shown"] = [False] * len(self._pool["hubs"])
            self._pool["halo_shown"] = [False] * self.HALOS
        self._sphere_hidden = True

    def _paint_sphere(self, w: float, h: float, *, geometry: bool) -> None:
        """Move and colour the pooled sphere items in one Tcl round trip."""
        n = len(self.constellation.particles)
        if not n:
            return
        pool = self._ensure_pool()
        _signature, dot, hub_shades, glow_shades, line_shades, area_lines = self._palette_shades()
        path = self._w
        script: list[str] = []
        if self._sphere_hidden:
            for item in pool["dots"].tolist() + pool["rings"]:
                script.append(f"{path} itemconfigure {item} -state normal")
            self._sphere_hidden = False
            geometry = True
        cx, cy, radius, compact = self._geometry(w, h)
        if geometry or self._projected is None:
            P = self._points
            ca, sa = math.cos(self.angle), math.sin(self.angle)
            cp, sp = math.cos(self.pitch), math.sin(self.pitch)
            x = P[:, 0] * ca + P[:, 2] * sa
            z = P[:, 2] * ca - P[:, 0] * sa
            y = P[:, 1] * cp - z * sp
            z = P[:, 1] * sp + z * cp
            scale = radius * self.zoom * 4.5 / (4.5 - z)
            self._projected = (cx + x * scale, cy + y * scale, z)
        sx, sy, z = self._projected
        near = (z + 1) / 2
        bands = np.clip(np.rint(near * (self.DEPTH_BANDS - 1)), 0, self.DEPTH_BANDS - 1).astype(int)
        radii = self._size_base * (.5 + .7 * near) * max(.7, min(1.25, radius / 150))
        # Colour code per particle: hue*100 + bright*10 + band, hubs 300+band, glows 400+.
        codes = np.where(self._kind_area, 300 + bands, self._hue * 100 + self._bright * 10 + bands)
        now = time.monotonic()
        glowing: list[tuple[float, int, str]] = []
        region_glow: dict[int, float] = {}
        for i, (state, since) in self._glow.items():
            level = self._intensity(state, since, now)
            if level <= 0:
                continue
            glowing.append((level, i, state))
            hub = self._hub_of[i]
            if hub >= 0 and hub != i:
                region_glow[hub] = max(region_glow.get(hub, 0.0), level * .7)
        for hub, level in region_glow.items():
            if hub not in self._glow:
                glowing.append((level, hub, "done"))
        lit = np.zeros(n, dtype=bool)
        for level, i, state in glowing:
            q = max(1, min(4, round(level * 4)))
            codes[i] = 400 + {"run": 0, "done": 10, "fail": 20}[state] + q
            radii[i] *= 1 + 1.1 * level
            lit[i] = True
        self._radii = radii
        changed = np.nonzero(codes != pool["dot_colour"])[0]
        for i in changed.tolist():
            code = int(codes[i])
            if code >= 400:
                state = ("run", "done", "fail")[(code - 400) // 10]
                colour = glow_shades[state][code % 10]
            elif code >= 300:
                colour = hub_shades[code - 300]
            else:
                colour = dot[code // 100][(code // 10) % 10][code % 10]
            script.append(f"{path} itemconfigure {pool['dots'][i]} -fill {colour}")
        pool["dot_colour"] = codes
        x0, y0 = (sx - radii).round(1).tolist(), (sy - radii).round(1).tolist()
        x1, y1 = (sx + radii).round(1).tolist(), (sy + radii).round(1).tolist()
        if geometry or len(glowing) or len(changed):
            for item, a, b, c, d in zip(pool["dots"].tolist(), x0, y0, x1, y1):
                script.append(f"{path} coords {item} {a} {b} {c} {d}")
        # Hub rings mark each area's heart.
        for k, i in enumerate(pool["hubs"]):
            r = radii[i] + 3.5
            script.append(f"{path} coords {pool['rings'][k]} {sx[i] - r:.1f} {sy[i] - r:.1f} {sx[i] + r:.1f} {sy[i] + r:.1f}")
            if geometry or lit[i] or pool["ring_lit"][k]:
                colour = glow_shades["run"][4] if lit[i] else hub_shades[max(0, int(bands[i]) - 1)]
                script.append(f"{path} itemconfigure {pool['rings'][k]} -outline {colour}")
                pool["ring_lit"][k] = bool(lit[i])
        # Links: only between facing particles; far-away detail is left out.
        if len(self._link_a):
            za, zb = z[self._link_a], z[self._link_b]
            shown = (za > self.FACING) & (zb > self.FACING)
            if compact or radius * self.zoom < self.DETAIL_RADIUS:
                shown &= ~self._link_detail
            link_band = np.clip(np.rint((za + zb + 2) / 4 * (self.DEPTH_BANDS - 1)), 0, self.DEPTH_BANDS - 1).astype(int)
            hot = lit[self._link_a] | lit[self._link_b]
            link_codes = np.where(hot, 900 + link_band,
                                  np.where(self._link_kind == 0, 500 + self._hue[self._link_a] * 10 + link_band,
                                           600 + self._link_kind * 10 + link_band))
            on = np.nonzero(shown & ~pool["line_shown"])[0].tolist()
            off = np.nonzero(~shown & pool["line_shown"])[0].tolist()
            lines = pool["lines"]
            for k in off:
                script.append(f"{path} itemconfigure {lines[k]} -state hidden")
            for k in on:
                script.append(f"{path} itemconfigure {lines[k]} -state normal")
            pool["line_shown"] = shown
            recolour = np.nonzero(shown & (link_codes != pool["line_colour"]))[0].tolist()
            for k in recolour:
                code = int(link_codes[k])
                if code >= 900:
                    colour = glow_shades["run"][max(1, min(4, code - 900))]
                elif code >= 600:
                    colour = line_shades[(code - 600) // 10][code % 10]
                else:
                    colour = area_lines[(code - 500) // 10][code % 10]
                script.append(f"{path} itemconfigure {lines[k]} -fill {colour}")
                pool["line_colour"][k] = code
            if geometry:
                a, b = self._link_a, self._link_b
                visible = np.nonzero(shown)[0]
                coords = np.stack([sx[a[visible]], sy[a[visible]], sx[b[visible]], sy[b[visible]]], axis=1).round(1).tolist()
                for item, (p, q, r, s) in zip(lines[visible].tolist(), coords):
                    script.append(f"{path} coords {item} {p} {q} {r} {s}")
        # Area names on the facing side, so the sphere reads as a map.
        for k, i in enumerate(pool["hubs"]):
            show = not compact and z[i] > .5 and radius >= 120
            if show:
                script.append(f"{path} coords {pool['labels'][k]} {sx[i]:.1f} {sy[i] - radii[i] - 9:.1f}")
            if show != pool["label_shown"][k]:
                script.append(f"{path} itemconfigure {pool['labels'][k]} -state {'normal' if show else 'hidden'}")
                pool["label_shown"][k] = show
        glowing.sort(reverse=True)
        for k, item in enumerate(pool["halos"]):
            if k < len(glowing):
                level, i, state = glowing[k]
                r = radii[i] + 4 + 5 * level
                colour = glow_shades[state][max(1, min(4, round(level * 4)))]
                script.append(f"{path} coords {item} {sx[i] - r:.1f} {sy[i] - r:.1f} {sx[i] + r:.1f} {sy[i] + r:.1f}")
                script.append(f"{path} itemconfigure {item} -outline {colour} -state normal")
                pool["halo_shown"][k] = True
            elif pool["halo_shown"][k]:
                script.append(f"{path} itemconfigure {item} -state hidden")
                pool["halo_shown"][k] = False
        if script:
            self.tk.eval("\n".join(script))

    def _record(self, key: str) -> dict[str, Any]:
        """The freshest track record for one part, plus this session's calls."""
        particle = self.constellation.particles[self.constellation.index(key)]  # type: ignore[index]
        record = dict(self.constellation.records.get(key) or {})
        understanding = getattr(self._runtime, "tool_understanding", None)
        if particle.kind == "tool" and understanding is not None and hasattr(understanding, "record_for"):
            cached = self._fresh.get(particle.name)
            if cached is None or time.monotonic() - cached[0] > self.RECORD_SECONDS:
                try:
                    cached = (time.monotonic(), understanding.record_for(particle.name) or {})
                except Exception:
                    log_swallowed(_log, f"reading the track record of {particle.name}")
                    cached = (time.monotonic(), record)
                self._fresh[particle.name] = cached
            record = dict(cached[1]) or record
        return record

    def _draw_tip(self, key: str, w: float, h: float) -> None:
        """Hover card: what the part is, its track record and its partners."""
        i = self.constellation.index(key)
        if i is None or self._projected is None:
            return
        p = self.palette
        particle = self.constellation.particles[i]
        sx, sy, _z = self._projected
        x, y = float(sx[i]), float(sy[i])
        lines = [(particle.title if particle.kind != "tool" else particle.name, ("Segoe UI Semibold", 9), p.WHITE),
                 (particle.region_title, ("Segoe UI", 8), p.GOLD_SOFT)]
        for row in textwrap.wrap(particle.description or "", 58)[:3]:
            lines.append((row, ("Segoe UI", 8), p.MUTED))
        facts = [f"{name}: {value}" for name, value in particle.facts if value and name != "icon"]
        if facts:
            lines.append((" · ".join(facts)[:70], ("Segoe UI", 8), p.MUTED))
        if particle.kind in {"tool", "feed"}:
            lines.append((constellation_model.record_line(self._record(key), particle.kind), ("Segoe UI", 8), p.WHITE))
        session = self._session.get(particle.name)
        if particle.kind == "tool" and session and session[0]:
            lines.append((f"This session: {session[0]} calls, {session[1]} failed", ("Segoe UI", 8), p.WHITE))
        partners = self.constellation.partners(key, limit=3)
        if partners:
            lines.append(("Works with: " + ", ".join(other.name for other, _link in partners), ("Segoe UI", 8), p.MUTED))
        lines.append(("Click to explain in chat" if self.explain else "Click to open the tool browser",
                      ("Segoe UI", 8), getattr(p, "HOLO", p.ROYAL_SOFT)))
        width = 14 + max(6.3 * len(text) for text, _font, _colour in lines)
        height = 10 + 15 * len(lines)
        left = max(6, min(w - width - 6, x + 12))
        top = max(24, min(h - height - 6, y - height / 2))
        tags = ("scene", "label", "tip")
        rounded(self, (left, top, left + width, top + height), radius=9, tags=tags,
                fill=blend(p.PANEL, p.BLACK, .35), outline=blend(p.PANEL, p.GOLD, .7))
        for row, (text, font, colour) in enumerate(lines):
            self.create_text(left + 8, top + 6 + 15 * row, text=text, anchor="nw", fill=colour, font=font, tags=tags)

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
        if self.winfo_height() < 170:
            self.delete('voice')
            return
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
        if self._pool is not None and (self._heard or self._glow):
            # Events light particles, and glows fade, at frame rate while the camera is still.
            if self._follow_work(None) and not redrawn:
                self._repaint_glow()
            moving = moving or any(state != "run" for state, _since in self._glow.values())
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
