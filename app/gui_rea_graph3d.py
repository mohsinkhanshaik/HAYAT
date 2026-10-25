"""A 3D call graph of the target open in REA, drawn on the shared OrbitScene engine (app/gui_space3d.py).

Procedures become points and calls become segments. Depth (how far a procedure sits from an entry point) maps
to height, so entry points sit high and leaf helpers low; each depth layer is spread on a circle so the shape
reads at a glance. A point's size grows with how many calls touch it, and double-clicking one decompiles it.

The layout here is pure and deterministic (``graph_scene``), so it is tested without a window; the window only
fetches the graph through HAYAT's governed read tools and hands the scene to OrbitScene.
"""
from __future__ import annotations

import math
import json
import tkinter as tk
from collections import defaultdict, deque
from typing import Any, Callable

from app.gui_space3d import OrbitScene
from app.gui_resources import StringVar


def normalise_graph(procedures: list[Any], edges: list[Any], limit: int = 120) -> dict[str, Any]:
    """REA's list_procedures rows and call edges, reduced to {nodes, edges} for the scene.

    Accepts loose shapes: a procedure is a name string or a dict with name/value/label; an edge is a (from, to)
    pair or a dict with from/to (or caller/callee, source/target). Unknown endpoints are dropped. Bounded to
    ``limit`` nodes, keeping the best-connected ones so a huge binary still draws."""
    def pname(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or item.get("value") or item.get("label") or item.get("procedure") or "").strip()
        return str(item or "").strip()

    def ends(edge: Any) -> tuple[str, str]:
        if isinstance(edge, dict):
            a = edge.get("from") or edge.get("caller") or edge.get("source") or edge.get("a")
            b = edge.get("to") or edge.get("callee") or edge.get("target") or edge.get("b")
            return str(a or "").strip(), str(b or "").strip()
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            return str(edge[0] or "").strip(), str(edge[1] or "").strip()
        return "", ""

    names = [name for name in dict.fromkeys(pname(item) for item in procedures) if name]
    pairs = [(a, b) for a, b in (ends(edge) for edge in edges) if a and b and a != b]
    known = set(names)
    for a, b in pairs:  # an edge may name a procedure the listing did not include
        known.update((a, b))
    degree: dict[str, int] = defaultdict(int)
    for a, b in pairs:
        degree[a] += 1
        degree[b] += 1
    ordered = sorted(known, key=lambda name: (-degree[name], name))[:max(1, limit)]
    kept = set(ordered)
    pairs = [(a, b) for a, b in dict.fromkeys(pairs) if a in kept and b in kept]
    return {"nodes": ordered, "edges": pairs}


def _depths(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Longest-path depth from an entry (a node nothing calls); cycles fall back to 0, so every node gets a layer."""
    outgoing: dict[str, list[str]] = defaultdict(list)
    indeg: dict[str, int] = {name: 0 for name in nodes}
    for a, b in edges:
        outgoing[a].append(b)
        indeg[b] += 1
    depth = {name: 0 for name in nodes}
    queue = deque(sorted(name for name in nodes if indeg[name] == 0))
    seen = set(queue)
    # Kahn-style longest path on the acyclic part; nodes only reachable through a cycle keep depth 0.
    remaining = dict(indeg)
    while queue:
        name = queue.popleft()
        for nxt in outgoing[name]:
            depth[nxt] = max(depth[nxt], depth[name] + 1)
            remaining[nxt] -= 1
            if remaining[nxt] <= 0 and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return depth


# A readable, colour-blind-friendly palette for modules; entry points stay gold on top of this.
_MODULE_COLOURS = ("#7FB3FF", "#F2A541", "#8CD9A3", "#E57AC8", "#C3A6FF", "#5FD0D6", "#F28B82", "#B6C94A",
                   "#9AA7B4", "#D6A66B")


def module_of(name: str) -> str:
    """The module/package a procedure name belongs to: the part before the last separator (:: ! / . \\),
    or '·' when the name is a bare symbol."""
    text = str(name or "")
    for sep in ("!", "::"):  # module!func, ns::func (native / C++)
        if sep in text:
            return text.split(sep, 1)[0] or "·"
    if "/" in text:  # a/b/module.js -> a/b
        return text.rsplit("/", 1)[0] or "·"
    if "." in text and not text.startswith("."):  # pkg.mod.func -> pkg.mod
        return text.rsplit(".", 1)[0]
    return "·"


def call_path(nodes: list[str], edges: list[Any], source: str, target: str) -> list[str]:
    """The shortest chain of calls from source to target, following edge direction; [] if none or endpoints
    unknown. source == target returns [source]."""
    graph = normalise_graph(nodes, edges)
    names, pairs = set(graph["nodes"]), graph["edges"]
    if source not in names or target not in names:
        return []
    if source == target:
        return [source]
    outgoing: dict[str, list[str]] = defaultdict(list)
    for a, b in pairs:
        outgoing[a].append(b)
    came_from: dict[str, str | None] = {source: None}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for nxt in sorted(outgoing.get(node, [])):  # sorted → deterministic shortest path
            if nxt not in came_from:
                came_from[nxt] = node
                if nxt == target:
                    chain, step = [], target
                    while step is not None:
                        chain.append(step)
                        step = came_from[step]
                    return list(reversed(chain))
                queue.append(nxt)
    return []


def graph_scene(nodes: list[str], edges: list[Any], palette: Any, selected: str | None = None,
                name_filter: str = "", colour_by: str = "module", focus: str = "",
                path: tuple[str, str] | None = None) -> dict[str, Any]:
    """Points and segments for OrbitScene: procedures by depth (height) spread on per-layer circles, calls as
    arrows from caller to callee. Deterministic for a given graph, so the same target always draws the same.

    name_filter keeps only procedures whose name contains it (and the calls between them); focus keeps one
    procedure with just its direct callers and callees (its neighbourhood), so a big graph becomes explorable;
    colour_by is 'module' (each module a distinct colour) or 'depth' (entry/middle/leaf shading). Entry points
    are always gold."""
    graph = normalise_graph(nodes, edges)
    names, pairs = graph["nodes"], graph["edges"]
    focus_active = bool(focus) and focus in set(names)
    if focus_active:
        neighbourhood = {focus} | {b for a, b in pairs if a == focus} | {a for a, b in pairs if b == focus}
        names = [name for name in names if name in neighbourhood]
        pairs = [(a, b) for a, b in pairs if a in neighbourhood and b in neighbourhood]
    needle = name_filter.strip().casefold()
    if needle:
        names = [name for name in names if needle in name.casefold()]
        kept = set(names)
        pairs = [(a, b) for a, b in pairs if a in kept and b in kept]
    if not names:
        empty = ("No procedures match '" + name_filter + "'." if needle
                 else "No procedures to show. Open a target and analyse it first.")
        return {"points": [], "segments": [], "note": empty, "legend": []}
    # Assign each module a stable colour (ordered by how many procedures it has, so the biggest modules are
    # the most distinct), used when colour_by == 'module'.
    module_counts: dict[str, int] = defaultdict(int)
    for name in names:
        module_counts[module_of(name)] += 1
    ranked_modules = sorted(module_counts, key=lambda m: (-module_counts[m], m))
    module_colour = {module: _MODULE_COLOURS[index % len(_MODULE_COLOURS)] for index, module in enumerate(ranked_modules)}
    depth = _depths(names, pairs)
    max_depth = max(depth.values()) or 1
    layers: dict[int, list[str]] = defaultdict(list)
    for name in names:
        layers[depth[name]].append(name)
    degree: dict[str, int] = defaultdict(int)
    for a, b in pairs:
        degree[a] += 1
        degree[b] += 1
    top_degree = max(degree.values(), default=1) or 1
    position: dict[str, tuple[float, float, float]] = {}
    for layer, members in layers.items():
        members = sorted(members, key=lambda name: (-degree[name], name))
        count = len(members)
        # Entry points high (y near +1), leaves low; each layer offset so columns do not line up.
        y = 1.0 - 2.0 * (layer / max_depth)
        radius = 0.25 + 0.6 * (1 - abs(y))  # widest band in the middle of the tree
        for index, name in enumerate(members):
            angle = 2 * math.pi * index / count + layer * 0.7
            position[name] = (radius * math.cos(angle), y, radius * math.sin(angle)) if count > 1 else (0.0, y, 0.0)
    # The highlighted call path (source → target), if both are present in what is drawn.
    chain = call_path(names, pairs, path[0], path[1]) if path and path[0] and path[1] else []
    on_path = set(chain)
    path_edges = {(chain[i], chain[i + 1]) for i in range(len(chain) - 1)}
    points = []
    for name in names:
        x, y, z = position[name]
        calls = degree[name]
        root, leaf = depth[name] == 0, not any(a == name for a, _b in pairs)
        module = module_of(name)
        if root:
            colour = palette.GOLD
        elif colour_by == "module":
            colour = module_colour[module]
        else:
            colour = blend(palette, "#7FB3FF", "#9AA7B4", 0.5) if leaf else "#B6F0C8"
        focused = bool(focus) and name == focus
        on = name in on_path
        if on:
            dot_colour = "#FF5D73"  # the call path is drawn in a hot colour over everything else
        elif focused and hasattr(palette, "GOLD_SOFT"):
            dot_colour = palette.GOLD_SOFT
        else:
            dot_colour = colour
        points.append({"id": name, "label": _short(name), "x": x, "y": y, "z": z,
                       "size": (1.8 if focused or on else 0.5 + 1.1 * (calls / top_degree)),
                       "colour": dot_colour,
                       "tip": f"{name}\n{calls} call{'s' if calls != 1 else ''} touch it"
                              + (f"\nmodule: {module}" if module != "·" else "")
                              + ("\nentry point" if root else "") + ("\nfocused" if focused else "")
                              + (f"\non the path ({chain.index(name) + 1} of {len(chain)})" if on else "")
                              + "\ndouble-click: decompile"})
    segments = []
    for a, b in pairs:
        ax, ay, az = position[a]
        bx, by, bz = position[b]
        lit = (a, b) in path_edges
        segments.append({"a": (ax, ay, az), "b": (bx, by, bz),
                         "colour": "#FF5D73" if lit else "#5B6B7A", "width": 3.0 if lit else 1.0})
    legend = ([{"label": module if module != "·" else "(no module)", "colour": module_colour[module],
                "count": module_counts[module]} for module in ranked_modules[:10]] if colour_by == "module" else [])
    note = (f"{len(names)} procedures · {len(pairs)} calls"
            + (f" · path {path[0]} → {path[1]}: {len(chain)} steps" if path and path[0] and path[1] and chain else
               f" · no call path {path[0]} → {path[1]}" if path and path[0] and path[1] else "")
            + (f" · focused on {focus} (its callers and callees)" if focus_active else "")
            + (f" · filtered by '{name_filter}'" if needle else "")
            + (f" · {len(ranked_modules)} modules" if colour_by == "module" else "")
            + " · higher = closer to an entry point · bigger = more calls touch it · double-click to decompile")
    return {"points": points, "segments": segments, "note": note, "legend": legend,
            "axes": {"y": ("call depth", [(1, "entry"), (0, ""), (-1, "leaf")])}, "selected": selected}


def blend(palette: Any, a: str, b: str, fraction: float) -> str:
    helper = getattr(palette, "blend", None)
    if callable(helper):
        try:
            return helper(a, b, fraction)
        except Exception:  # noqa: BLE001, S110 -- a palette without a 2-colour blend: fall back to the first colour
            return a
    return a


def _short(name: str, limit: int = 28) -> str:
    name = str(name)
    return name if len(name) <= limit else "…" + name[-(limit - 1):]


def _rows_of(result: Any) -> list[Any]:
    """A list of items from a REA read result, whatever shape it came in (structured list/dict, or text lines)."""
    if not isinstance(result, dict):
        return []
    def rows(value: Any, depth: int = 0):
        if depth > 6:
            return None
        if isinstance(value, list):
            return value[:400]
        if isinstance(value, dict):
            for key in ("normalized_result", "result"):
                if key in value:
                    found = rows(value[key], depth + 1)
                    if found is not None:
                        return found
            for key in ("rows", "procedures", "functions", "results", "nodes", "names", "items"):
                if key in value:
                    found = rows(value[key], depth + 1)
                    if found is not None:
                        return found
        return None

    structured = result.get("structured")
    found = rows(structured)
    if found is not None:
        return found
    report = str(result.get("report") or "")
    try:
        parsed = json.loads(report)
    except (ValueError, RecursionError):
        parsed = None
    if parsed is not None:
        return rows(parsed) or []  # metadata JSON is never a procedure name
    if structured is not None:
        return []
    lines = [line.strip(" \t•-") for line in report.splitlines() if line.strip()]
    return [line for line in lines if line and not line.endswith(":")][:400]


def engine_loader(engine: Any, limit: int = 40) -> Callable[[], dict[str, Any]]:
    """A loader that reads the open target's procedures and calls through HAYAT's governed read tools. Best
    bounded per-procedure callees. get_call_graph requires a root address and
    must not be invoked with empty arguments or mistaken for the whole binary."""
    def name_of(item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("name") or item.get("value") or item.get("label") or item.get("procedure") or "").strip()
        return str(item or "").strip()

    def load() -> dict[str, Any]:
        listing = engine.read("list_procedures", {})
        expected_target = listing.get("open_target")
        nodes = _rows_of(listing)
        edges: list[Any] = []
        names = list(dict.fromkeys(n for n in (name_of(item) for item in nodes) if n))
        aliases = {str(item["address"]): name_of(item) for item in nodes
                   if isinstance(item, dict) and item.get("address") and name_of(item)}
        checked, failed = 0, []
        for name in names[:max(1, min(limit, 120))]:
            try:
                response = engine.read("procedure_callees", {"procedure": name})
            except Exception:  # noqa: S112 -- retain explicit missing coverage instead of inventing an empty result
                failed.append(name)
                continue
            if expected_target is not None and response.get("open_target") != expected_target:
                raise RuntimeError("The REA target changed while building the graph; refresh the current target.")
            try:
                for callee in _rows_of(response):
                    target = name_of(callee)
                    if target:
                        edges.append((name, aliases.get(target, target)))
                checked += 1
            except Exception:  # noqa: S112 -- retain explicit missing coverage instead of inventing an empty result
                failed.append(name)
        return {"nodes": nodes, "edges": edges, "coverage": {
            "listed": len(names), "checked": checked, "failed": failed,
            "limited": checked + len(failed) < len(names) or len(nodes) >= 400,
            "scope": "Provider-reported static calls; unresolved dynamic calls may be absent."}}
    return load


class ReaGraph3DWindow(tk.Toplevel):
    """A window that loads the open target's call graph and shows it in 3D. ``load`` returns {nodes, edges};
    ``decompile`` is called with a procedure name when the owner double-clicks a point."""

    def __init__(self, gui: Any, load: Callable[[], dict[str, Any]],
                 decompile: Callable[[str], None] | None = None) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self._load, self._decompile = gui, load, decompile
        self._closed = False
        self._poll_id = None
        self._loading = False
        self._coverage_note = ""
        self.title("HAYAT · Reverse engineering · 3D call graph")
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self.status = StringVar(self, "Loading the call graph…")
        self._graph: dict[str, Any] = {"nodes": [], "edges": []}
        caption = tk.Label(self, textvariable=self.status, bg=gui.BLACK, fg=gui.GOLD, anchor="w", justify="left")
        caption.pack(fill="x", padx=12, pady=(10, 4))
        self.bind("<Configure>", lambda event: caption.configure(wraplength=max(200, event.width - 24))
                  if event.widget is self else None, add="+")
        controls = tk.Frame(self, bg=gui.BLACK)
        controls.pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(controls, text="Filter", bg=gui.BLACK, fg=gui.WHITE).pack(side="left")
        self.filter = StringVar(self, "")
        entry = tk.Entry(controls, textvariable=self.filter, width=28)
        entry.pack(side="left", padx=(6, 12))
        entry.bind("<KeyRelease>", lambda _event: self._render())
        self.colour_by = StringVar(self, "module")
        tk.Checkbutton(controls, text="Colour by module", bg=gui.BLACK, fg=gui.WHITE, selectcolor=gui.BLACK,
                       activebackground=gui.BLACK, activeforeground=gui.GOLD,
                       variable=self.colour_by, onvalue="module", offvalue="depth",
                       command=self._render).pack(side="left")
        self._selected_key: str | None = None
        self._focus = ""
        self._focus_button = tk.Button(controls, text="Focus selected", command=self._focus_selected,
                                       bg=gui.PANEL if hasattr(gui, "PANEL") else "#1b1f24", fg=gui.WHITE, relief="flat")
        self._focus_button.pack(side="left", padx=(12, 0))
        self._path_from: str | None = None
        self._path: tuple[str, str] | None = None
        panel_bg = gui.PANEL if hasattr(gui, "PANEL") else "#1b1f24"
        tk.Button(controls, text="Path from", command=self._set_path_from, bg=panel_bg, fg=gui.WHITE,
                  relief="flat").pack(side="left", padx=(12, 0))
        tk.Button(controls, text="to", command=self._set_path_to, bg=panel_bg, fg=gui.WHITE,
                  relief="flat").pack(side="left", padx=(4, 0))
        self._showall_button = tk.Button(controls, text="Show all", command=self._show_all,
                                         bg=panel_bg, fg=gui.WHITE, relief="flat")
        self.legend = tk.Label(controls, text="", bg=gui.BLACK, fg=gui.MUTED if hasattr(gui, "MUTED") else gui.WHITE,
                               anchor="e", justify="right")
        self.legend.pack(side="right")
        self.scene = OrbitScene(self, gui, on_select=self._selected, on_open=self._open)
        self.scene.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        from app.gui_windows import place_over
        place_over(self, gui.root, (1040, 760))
        self.deiconify()
        self.refresh()

    def refresh(self) -> None:
        if self._closed or self._loading:
            return
        self._loading = True
        import queue
        import threading
        results: queue.Queue = queue.Queue()

        def work() -> None:
            try:
                graph = self._load() or {}
                results.put(("ok", graph))
            except Exception as exc:  # noqa: BLE001 -- shown in the status line, never a traceback
                results.put(("error", f"{type(exc).__name__}: {str(exc)[:200]}"))
        threading.Thread(target=work, name="hayat-rea-graph3d", daemon=True).start()
        self._poll(results)

    def _poll(self, results: Any) -> None:
        import queue

        self._poll_id = None
        if self._closed:
            return
        try:
            kind, value = results.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(60, lambda: self._poll(results))
            return
        self._loading = False
        if kind == "ok":
            self._graph = {"nodes": value.get("nodes", []), "edges": value.get("edges", [])}
            coverage = value.get("coverage")
            if coverage:
                self._coverage_note = (f"Callees checked: {coverage['checked']}/{coverage['listed']}; "
                                       f"failed: {len(coverage['failed'])}" + ("; bounded sample" if coverage['limited'] else "") +
                                       ". " + coverage["scope"])
            self._render()
        else:
            self.status.set(f"Could not load the call graph: {value}")

    def _render(self) -> None:
        """Rebuild the scene from the cached graph with the current filter and colouring (no re-read of REA)."""
        if self._closed:
            return
        scene = graph_scene(self._graph.get("nodes", []), self._graph.get("edges", []), self.gui,
                            name_filter=self.filter.get(), colour_by=self.colour_by.get(), focus=self._focus,
                            path=self._path)
        self.scene.set_scene(**{key: scene[key] for key in ("points", "segments") if key in scene},
                             axes=scene.get("axes"), note=scene.get("note", ""))
        legend = scene.get("legend") or []
        self.legend.configure(text="  ".join(f"● {item['label']} ({item['count']})" for item in legend[:6]))
        self._showall_button.pack(side="left", padx=(6, 0)) if (self._focus or self._path) else self._showall_button.pack_forget()
        note = scene.get("note", "")
        self.status.set(note + ("\n" + self._coverage_note if self._coverage_note else ""))

    def _selected(self, key: str | None) -> None:
        self._selected_key = key
        if key:
            self.status.set(key + ("\n" + self._coverage_note if self._coverage_note else ""))

    def _focus_selected(self) -> None:
        """Show only the selected procedure with its direct callers and callees."""
        if self._selected_key:
            self._focus = self._selected_key
            self._render()

    def _set_path_from(self) -> None:
        if self._selected_key:
            self._path_from = self._selected_key
            self.status.set(f"Path from {self._path_from} — now select a target and press 'to'.")

    def _set_path_to(self) -> None:
        if self._path_from and self._selected_key:
            self._path = (self._path_from, self._selected_key)
            self._render()

    def _show_all(self) -> None:
        self._focus = ""
        self._path = None
        self._path_from = None
        self._render()

    def _open(self, key: str) -> None:
        if key and self._decompile is not None:
            self._decompile(key)

    def close(self) -> None:
        self._closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        try:
            self.destroy()
        except tk.TclError:
            pass
