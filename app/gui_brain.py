"""The brain mesh in 3D: HAYAT's real subsystems, their contracts, their health.

Every node is a ``BrainMesh`` node and each pathway is a ``BrainMesh`` edge.
The triangular shell is a spatial guide, not a measurement. Node colour comes
from the last self-check; moving pulses mark paths of work running now.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from functools import lru_cache
from typing import Any, Callable

from app.brain_mesh import BrainMesh
from app.gui_depth import DepthNavigator, blend, rounded

from app.brain_layout import LAYOUT  # noqa: E402  (subsystem positions, shared with Tk-free code)

#: The part of the pipeline each kind of running work passes through.
FLOWS: dict[str, tuple[str, ...]] = {
    "model": ("interface", "intent", "context", "router", "helpers", "planner"),
    "tool": ("planner", "tool_judgment", "authority", "tools", "verifier"),
    "research": ("network", "research", "verifier", "memory"),
    "learning": ("learning", "memory", "dna"),
    "hearing": ("perception", "interface"),
    "speaking": ("verifier", "interface"),
    # Looking through the camera or at the screen: what is seen is evidence
    # for the Truth Gate, and reaches you through the interface.
    "seeing": ("perception", "verifier", "interface"),
}
LAYERS = {node.node_id: node.layer for node in BrainMesh.NODES}


def standing_wave(point: tuple[float, float, float], modes: tuple[int, int] = (2, 3)) -> float:
    """Static Chladni-inspired colour field, not measured neural activity.

    Two separable standing waves on the shell's x/z projection. This is a
    visual analogy, not a physical vibration solver or a training technique.
    """
    m, n = modes
    if type(m) is not int or type(n) is not int or not 1 <= m < n <= 8:
        raise ValueError("Wave orders require integers 1 <= m < n <= 8.")
    x, _, z = point
    u, v = (x / 1.25 + 1) / 2, (z / 1.15 + 1) / 2
    return (math.cos(m * math.pi * u) * math.cos(n * math.pi * v)
            - math.cos(n * math.pi * u) * math.cos(m * math.pi * v)) / 2


def contract_curve(start, end, start_radius=20.0, end_radius=20.0):
    """Clip arrows outside node discs; separate opposite directed contracts."""
    x, y = start[:2]
    xx, yy = end[:2]
    dx, dy = xx - x, yy - y
    distance = math.hypot(dx, dy)
    if distance < 1e-9:
        return (x, y, x, y, xx, yy)
    ux, uy = dx / distance, dy / distance
    a, b = min(start_radius, distance * .22), min(end_radius, distance * .22)
    bend = min(14.0, distance * .09)
    return (x + ux * a, y + uy * a, (x + xx) / 2 - uy * bend,
            (y + yy) / 2 + ux * bend, xx - ux * b, yy - uy * b)


def curve_point(curve, fraction):
    """One point on a quadratic Bezier, shared by arrows and live pulses."""
    ax, ay, mx, my, bx, by = curve
    t = max(0.0, min(1.0, fraction))
    return ((1-t)**2 * ax + 2*(1-t)*t * mx + t*t * bx,
            (1-t)**2 * ay + 2*(1-t)*t * my + t*t * by)


def neighborhood(node_id: str, hops: int = 1) -> set[str]:
    """Subsystems within a bounded number of real incoming/outgoing contracts."""
    if node_id not in LAYERS or hops not in {1, 2, 3}:
        return set()
    found, frontier = {node_id}, {node_id}
    for _ in range(hops):
        next_nodes = {edge.target for edge in BrainMesh.EDGES if edge.source in frontier}
        next_nodes.update(edge.source for edge in BrainMesh.EDGES if edge.target in frontier)
        frontier = next_nodes - found
        found.update(frontier)
    return found

def path_between(source: str, target: str) -> list[str]:
    """The shortest chain of real contracts from one subsystem to another, following edge direction.

    Returns the node ids from source to target inclusive, or [] when either is unknown or none connects them.
    Useful for "how does a request reach memory": interface → intent → … → memory."""
    if source not in LAYERS or target not in LAYERS:
        return []
    if source == target:
        return [source]
    from collections import deque
    outgoing: dict[str, list[str]] = {}
    for edge in BrainMesh.EDGES:
        outgoing.setdefault(edge.source, []).append(edge.target)
    came_from = {source: None}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for nxt in sorted(outgoing.get(node, [])):  # sorted so the path is deterministic
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


WHY_IT_MATTERS = {
    "interface": "Keeps requests and final replies in one visible conversation.",
    "intent": "A question can be answered directly; a task can be planned without treating the request as permission.",
    "context": "A short turn stays fast while a complex turn can retain more relevant history and room for an answer.",
    "router": "Uses a suitable local model or deterministic path for the work and available resources.",
    "helpers": ("A focused helper can handle coding, research, review, computer work, or repair through the same core; "
                "temporary worker teams are sized by this machine (workers per team, teams at once)."),
    "planner": "Turns a goal into bounded steps with observable completion criteria.",
    "tool_judgment": "Tool choices can use their purpose and previous outcomes instead of trying every available tool.",
    "authority": "Checks each proposed action before it reaches a tool.",
    "tools": "Runs selected capabilities and returns observations for checking.",
    "verifier": "Separates an observed result from an unsupported success claim.",
    "memory": "Preserves useful outcomes and lets later turns retrieve them.",
    "dna": "Finds related memories without loading the entire store into every turn.",
    "research": "Tracks sources, uncertainty, and disagreements for public claims.",
    "learning": "Retains validated lessons while keeping model training a separate operation.",
    "network": "Provides scoped access to public information and connected services.",
    "perception": "Provides current visual or audio observations when a sensor is enabled.",
    "observability": "Records failures and timings so problems can be diagnosed.",
    "cortex": "Keeps the active goal and constraints in working memory and stops at execution limits.",
    "dual_brain": "A skeptical critic checks a proposal before a bounded revision. Review is recorded as opinion.",
    "invention": "Develops hypotheses and tests numeric prototypes against a frozen acceptance contract.",
    "inquiry": "Before a hard answer, asks itself what the answer depends on, settles what it can and names what it cannot.",
}


def active_flows(activity: dict[str, Any], voice: dict[str, Any], camera_on: bool = False) -> list[str]:
    """Which flows are genuinely under way, from the runtime's own reports."""
    flows = []
    if camera_on:
        flows.append("seeing")
    for row in activity.get("running") or []:
        kind = str(row.get("kind", ""))
        if kind in FLOWS and kind not in flows:
            flows.append(kind)
    state = str(voice.get("state", ""))
    if voice.get("enabled") and state in {"listening", "transcribing"}:
        flows.append("hearing")
    if state == "speaking":
        flows.append("speaking")
    return flows


def flow_edges(flows: list[str]) -> list[tuple[str, str]]:
    """Real edges whose ends both lie on one active flow."""
    edges = []
    for flow in flows:
        members = set(FLOWS[flow])
        for edge in BrainMesh.EDGES:
            pair = (edge.source, edge.target)
            if edge.source in members and edge.target in members and pair not in edges:
                edges.append(pair)
    return edges


def describe(node_id: str, check: dict[str, Any] | None) -> str:
    """Plain words for one node: its job, its contracts, its last result."""
    node = next(node for node in BrainMesh.NODES if node.node_id == node_id)
    labels = {item.node_id: item.label for item in BrainMesh.NODES}
    receives = [f"{labels[edge.source]} ({edge.contract})" for edge in BrainMesh.EDGES if edge.target == node_id]
    sends = [f"{labels[edge.target]} ({edge.contract})" for edge in BrainMesh.EDGES if edge.source == node_id]
    if check is None:
        result = "Not checked yet. Use Refresh or Self Check."
    else:
        verdict = "Ready" if check.get("passed") else "Needs attention"
        detail = str(check.get("detail") or "").strip()
        result = f"{verdict} · {float(check.get('latency_ms', 0)):.2f} ms" + (f" · {detail[:140]}" if detail else "")
    lines = [f"{node.label}  ·  {node.layer}{'' if node.critical else '  ·  optional'}",
             f"Does: {node.responsibility}.",
             f"Why it matters: {WHY_IT_MATTERS[node_id]}", f"Last check: {result}"]
    observations = (check or {}).get("evidence") or {}
    if observations:
        details = "; ".join(f"{key.replace('_', ' ')}: {str(value)[:100]}"
                            for key, value in observations.items())
        lines.append("Observed: " + details)
    if receives:
        lines.append("Receives from: " + "; ".join(receives))
    if sends:
        lines.append("Sends to: " + "; ".join(sends))
    lines.append("Check scope: confirms the subsystem is connected or responding; it does not measure answer quality or model intelligence.")
    return "\n".join(lines)


class BrainGraph(DepthNavigator):
    """Rotatable brain mesh. Camera, keys and motion rules come from the navigator."""

    VIEWS = tuple((node.node_id, node.label) for node in BrainMesh.NODES)
    DESCRIPTIONS = {node.node_id: node.responsibility for node in BrainMesh.NODES}
    HOME = (-.5, -.3, 1.0)
    PULSE_SPEED = .7
    SURFACES = ("Folded", "Wireframe", "Standing waves", "Hidden")
    GEOMETRIES = ("Sketchfab brain", "Procedural")
    DETAILS = {"Auto": (16, 24), "Low": (8, 12), "Normal": (16, 24), "High": (20, 32), "Ultra": (20, 32)}
    HEALTH_FILTERS = ("All checks", "Needs attention", "Not checked", "Ready")

    def __init__(self, parent: tk.Misc, palette: Any, on_open: Callable[[str], None],
                 **kwargs: Any) -> None:
        self.checks: dict[str, dict[str, Any]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.evidence_on = True
        self.flows: list[str] = []
        self._edges: list[tuple[str, str]] = []
        # The fourth dimension: when a past moment is shown, the live flows
        # are kept aside and the brain shows what was recorded then.
        self._live_flows: list[str] = []
        self.moment: float | None = None
        self._moment_marks: dict[str, str] = {}
        self._echo_edges: list[tuple[str, str]] = []
        #: How busy each part was over the timeline's window, 0..1. The
        #: fourth dimension folded into the third: the brain glows with
        #: its own history.
        self.heat: dict[str, float] = {}
        #: The contract being explained by the "How it works" tour.
        self.tour_edge: tuple[str, str] | None = None
        self.focus_connections = False
        self.connection_hops = 1
        self.view_mode = "5D"
        self.layer_filter = "All layers"
        self.health_filter = "All checks"
        self.path_only = False
        self.contract_active: tuple[str, str] | None = None
        self.contract_hover: tuple[str, str] | None = None
        self._contract_curves: dict[tuple[str, str], tuple[Any, float]] = {}
        self._link_cycle: tuple[str, str, int] | None = None
        self.surface = "Folded"
        self.mesh_detail = "Normal"
        self.wave_modes = (2, 3)
        self.geometry = "Sketchfab brain"
        self.asset_loaded = False
        self.asset_problem = ""
        self.render_detail = "Normal"
        self._geometry_cache = {}
        self._load_geometry()
        kwargs.setdefault("height", 300)
        #: A traced contract path between two subsystems (advanced interactive view): the nodes and directed
        #: edges on the shortest route, lit prominently so the eye follows it. Set before super().__init__ so the
        #: initial redraw it triggers can read them.
        self._path_nodes: set[str] = set()
        self._path_chain: tuple[str, ...] = ()
        self._path_edges: set[tuple[str, str]] = set()
        self._path_from: str | None = None
        #: Component neurons (app.brain_components): the real parts inside
        #: each subsystem, their live numbers and their recorded links. Set
        #: before super().__init__ for the same reason as the path state.
        self.components: dict[str, Any] = {}
        self.component_states: dict[str, Any] = {}
        self.component_links: tuple[Any, ...] = ()
        self.show_components = True
        #: Every tool neuron at once, as small dots; otherwise an area's tools show when it is chosen or working.
        self.show_all_tools = False
        self.component_hover: str | None = None
        self.component_active: str | None = None
        self.component_heat: dict[str, float] = {}
        self.on_component: Callable[[str], None] | None = None
        self.on_component_drill: Callable[[str], None] | None = None
        self._component_base: dict[str, tuple[float, float, float]] = {}
        self._component_positions: dict[str, tuple[float, float, float, float]] = {}
        self._tool_owners: dict[str, tuple[str, ...]] = {}
        self._model_owners: dict[str, list[str]] = {}
        self._active_components: set[str] = set()
        self._live_components: set[str] = set()
        self._component_marks: dict[str, str] = {}
        self._component_links_by_end: dict[str, list[Any]] = {}
        #: Memory and knowledge neurons (app/brain_population.py): thousands of real stored units, drawn in one
        #: batched image instead of one canvas item each, and hit-tested from their projected arrays.
        self.population: Any = None
        self.population_hover: int | None = None
        self.population_active: int | None = None
        self.on_population: Callable[[int], None] | None = None
        self._population_photo: Any = None
        self._population_screen: Any = None
        self._population_ms = 0.0
        super().__init__(parent, palette, on_open, **kwargs)
        self.active = ""
        #: Set by the host to show the path status line; a plain callback keeps this widget host-agnostic.
        self.on_path_status: Callable[[str], None] | None = None
        self.bind("<Key-p>", self._path_key)
        self.bind("<Key-P>", self._path_key)
        self.bind("<Key-i>", lambda _event: self._link_key("incoming"))
        self.bind("<Key-o>", lambda _event: self._link_key("outgoing"))
        self.bind("<Key-f>", self._connections_key)
        self.bind("<Key-h>", self._health_key)
        self.bind("<Return>", self._enter_key)

    def _path_key(self, _event: Any = None) -> str:
        message = self.toggle_path_point()
        if callable(self.on_path_status):
            self.on_path_status(message)
        return "break"

    # ----------------------------------------------------------- inputs

    def trace_path(self, source: str, target: str) -> list[str]:
        """Light the shortest contract path from one subsystem to another and return it (see path_between)."""
        chain = path_between(source, target)
        self._path_chain = tuple(chain)
        self._path_nodes = set(chain)
        self._path_edges = {(chain[i], chain[i + 1]) for i in range(len(chain) - 1)}
        self._refresh_views()
        self.redraw()
        return chain

    def clear_path(self) -> None:
        self._path_from = None
        self._path_chain = ()
        if self._path_nodes or self._path_edges:
            self._path_nodes, self._path_edges = set(), set()
            self._refresh_views()
            self.redraw()

    def _announce_inspection(self, message: str = "") -> None:
        if message and callable(getattr(self, 'on_path_status', None)):
            self.on_path_status(message)
        self.event_generate('<<BrainInspectionChanged>>', when='tail')

    def _enter_key(self, _event: Any = None) -> str:
        if self.contract_active is not None:
            source, target = self.contract_active
            focus = self._hover or self.active
            destination = source if focus == target else target
            if destination in dict(self.VIEWS):
                self.select(destination)
                self._hover = destination
                if self.on_preview:
                    self.on_preview(destination)
                self.redraw()
                self._announce_inspection('Focused ' + dict(self.VIEWS)[destination] + '; I/O inspects its contracts.')
            else:
                self._announce_inspection('That endpoint is hidden by the current filters.')
        elif self.VIEWS:
            self._activate(self.VIEWS[self._keyboard_index % len(self.VIEWS)][0])
        return 'break'

    def _step_key(self, direction: int) -> str:
        if not self.VIEWS:
            self._announce_inspection('No subsystems match these filters. Press H to change the check filter.')
            return 'break'
        self.contract_active = self.contract_hover = None
        result = super()._step_key(direction)
        self._announce_inspection()
        return result

    def _connections_key(self, _event: Any = None) -> str:
        self.focus_connections = not self.focus_connections
        self.redraw()
        self._announce_inspection('Showing focused connections.' if self.focus_connections else 'Showing all visible contracts.')
        return 'break'

    def _health_key(self, _event: Any = None) -> str:
        self.set_health_filter(self.HEALTH_FILTERS[(self.HEALTH_FILTERS.index(self.health_filter) + 1) % len(self.HEALTH_FILTERS)])
        return 'break'

    def _link_key(self, direction: str) -> str:
        focus = self._hover or self.active
        if focus not in LAYERS:
            self._announce_inspection('Select a subsystem, then I inspects incoming contracts and O outgoing contracts.')
            return 'break'
        links = self.linked_contracts(focus, direction)
        if not links:
            self._announce_inspection(f'No {direction} contracts are visible for {dict(self.VIEWS).get(focus, focus)}.')
            return 'break'
        index = ((self._link_cycle[2] + 1) % len(links)
                 if self._link_cycle is not None and self._link_cycle[:2] == (focus, direction) else 0)
        self._link_cycle = (focus, direction, index)
        self.select_contract(*links[index])
        detail = self.contract_detail(*links[index])
        self._announce_inspection(f"{detail['source_label']} → {detail['target_label']}: {detail['contract']}")
        return 'break'

    def linked_contracts(self, node_id: str, direction: str = 'both') -> list[tuple[str, str]]:
        if direction not in {'incoming', 'outgoing', 'both'}:
            raise ValueError('Choose incoming, outgoing, or both contract directions.')
        return sorted((edge.source, edge.target) for edge in BrainMesh.EDGES
                      if ((direction != 'outgoing' and edge.target == node_id)
                          or (direction != 'incoming' and edge.source == node_id))
                      and self._visible_edge(edge.source, edge.target))

    def select_contract(self, source: str, target: str) -> None:
        if not any(edge.source == source and edge.target == target for edge in BrainMesh.EDGES):
            raise ValueError('That directed contract is not in the brain mesh.')
        self.contract_active = (source, target)
        self.component_active = self.population_active = None
        self.redraw()
        self._announce_inspection()

    def contract_detail(self, source: str, target: str) -> dict[str, Any]:
        edge = next((edge for edge in BrainMesh.EDGES if edge.source == source and edge.target == target), None)
        if edge is None:
            raise ValueError('That directed contract is not in the brain mesh.')
        labels = {node.node_id: node.label for node in BrainMesh.NODES}
        historical = self.moment is not None
        def endpoint(key: str) -> dict[str, Any]:
            check = None if historical else self.checks.get(key)
            return {'state': 'not recorded at this moment' if historical else
                    'not checked' if check is None else 'ready' if check.get('passed') else 'needs attention',
                    'checked_at': None if historical else self.evidence.get(key, {}).get('checked_at'),
                    'detail': '' if check is None else str(check.get('detail') or ''),
                    'gaps': [] if historical else self.evidence.get(key, {}).get('gaps', [])}
        return {'source': source, 'target': target, 'source_label': labels[source], 'target_label': labels[target],
                'contract': edge.contract, 'source_check': endpoint(source), 'target_check': endpoint(target),
                'visible': self._visible_edge(source, target), 'on_path': (source, target) in self._path_edges,
                'highlighted_by_work': (source, target) in self._edges,
                'scope': 'Declared directed software contract. Endpoint readiness does not verify delivery on this link; '
                         'work highlights show the reported work category, not measured neural firing.'}

    def _cancel_interaction(self, _event: Any = None) -> str:
        self.contract_active = self.contract_hover = None
        self._link_cycle = None
        self.clear_path()
        result = super()._cancel_interaction(_event)
        self._announce_inspection()
        return result

    def toggle_path_point(self) -> str:
        """Interactive tracing: the first call pins the focused subsystem as the start, the second traces from
        it to the now-focused one. Returns a short status line for the caller to show."""
        focus = self.active or self._hover
        if not focus:
            return "Focus a subsystem first, then press P to start a path."
        if self._path_from is None or self._path_from == focus:
            self._path_from = focus
            labels = dict(self.VIEWS)
            return f"Path from {labels.get(focus, focus)} — focus another subsystem and press P."
        source = self._path_from
        chain = self.trace_path(source, focus)
        self._path_from = None
        labels = dict(self.VIEWS)
        if not chain:
            return f"No contract path from {labels.get(source, source)} to {labels.get(focus, focus)}."
        return "Path: " + " → ".join(labels.get(node, node) for node in chain)

    def set_surface(self, surface: str, *, detail: str | None = None,
                    modes: tuple[int, int] | None = None) -> None:
        if surface not in self.SURFACES or detail is not None and detail not in self.DETAILS:
            raise ValueError("Unknown mesh surface or detail.")
        if modes is not None:
            standing_wave((0, 0, 0), modes)
            self.wave_modes = modes
        self.surface = surface
        self.mesh_detail = detail or self.mesh_detail
        self._load_geometry()
        self.redraw()

    def set_geometry(self, geometry: str) -> None:
        if geometry not in self.GEOMETRIES:
            raise ValueError("Unknown brain geometry")
        self.geometry = geometry
        self._geometry_cache.clear()
        self._load_geometry()
        self.redraw()

    def _load_geometry(self) -> None:
        detail = self._effective_detail()
        cached = self._geometry_cache.get((self.geometry, detail))
        if cached:
            self._shell, self._normals, self.asset_loaded, self.asset_problem = cached
            self.render_detail = detail
            self._announce_geometry()
            return
        self.asset_loaded, self.asset_problem = False, ""
        if self.geometry == "Sketchfab brain":
            from app.brain_asset import asset_facets
            try:
                self._shell = asset_facets(detail)
                self.asset_loaded = True
                self._cache_normals()
                self._remember_geometry(detail)
                return
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                self.asset_problem = "Bundled model unavailable; using procedural geometry."
        self._shell = self._shell_facets(*self.DETAILS[detail])
        self._cache_normals()
        self._remember_geometry(detail)

    def _effective_detail(self) -> str:
        requested = self.mesh_detail
        # Interaction uses a cached small mesh; exact geometry returns once the camera settles.
        if getattr(self, '_drag_x', None) is not None or getattr(self, '_target', None) is not None or getattr(self, '_velocity', (0, 0)) != (0, 0):
            return 'Low' if requested != 'Low' else requested
        if requested == 'Auto':
            # Idle spinning is camera motion too. Keep the default animated
            # view light enough that scrolling and other Tk controls get time;
            # paused/hovered views retain the normal size-dependent mesh.
            if (getattr(self, 'motion', False) and not getattr(self, '_inside', False)
                    and self._clock() >= getattr(self, '_idle_after', 0) and self.winfo_ismapped()):
                return 'Low'
            width = self.winfo_width() if hasattr(self, 'tk') else 0
            height = self.winfo_height() if hasattr(self, 'tk') else 0
            return 'High' if width >= 700 and height >= 450 else 'Normal'
        return requested

    def _remember_geometry(self, detail):
        self.render_detail = detail
        # Failed asset loads are retryable, rather than permanently cached as a fallback.
        if not self.asset_problem:
            self._geometry_cache[(self.geometry, detail)] = (self._shell, self._normals, self.asset_loaded, self.asset_problem)
        self._announce_geometry()

    def _announce_geometry(self):
        if hasattr(self, 'tk'):
            self.event_generate('<<GeometryChanged>>', when='tail')

    def _release(self, event):
        pressed = self._drag_x is not None
        super()._release(event)
        # A click between subsystems chooses the component neuron under it.
        if pressed and not self._dragged and self._hit(float(event.x), float(event.y)) is None:
            component = self._hit_component(float(event.x), float(event.y))
            if component is not None:
                self.select_component(component)
                if callable(self.on_component):
                    self.on_component(component)
            else:
                neuron = self._hit_population(float(event.x), float(event.y))
                if neuron is not None:
                    self.select_population(neuron)
                    if callable(self.on_population):
                        self.on_population(neuron)
                else:
                    link = self._hit_contract(float(event.x), float(event.y))
                    if link is not None:
                        self.select_contract(*link)
        # Paused/reduced-motion scenes have no animation loop to restore their detail.
        if self._effective_detail() != self.render_detail:
            self.redraw()

    def geometry_status(self) -> dict:
        return {'model': self.geometry, 'requested_detail': self.mesh_detail, 'render_detail': self.render_detail,
                'triangles': len(self._shell), 'asset_loaded': self.asset_loaded, 'problem': self.asset_problem,
                'scope': 'Illustrative geometry; subsystem overlays show software contracts.'}

    def _cache_normals(self) -> None:
        """Geometry is fixed; calculate unit normals only when it changes."""
        self._normals = []
        for a, b, c in self._shell:
            u = tuple(b[i] - a[i] for i in range(3))
            v = tuple(c[i] - a[i] for i in range(3))
            normal = (u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0])
            length = max(math.sqrt(sum(value * value for value in normal)), 1e-9)
            self._normals.append(tuple(value / length for value in normal))

    def set_layer(self, layer: str) -> None:
        if layer != "All layers" and layer not in LAYERS.values():
            raise ValueError("Unknown brain layer")
        self.layer_filter = layer
        self._refresh_views()
        self.redraw()
        self._announce_inspection()

    def set_health_filter(self, state: str) -> None:
        if state not in self.HEALTH_FILTERS:
            raise ValueError('Unknown brain check filter.')
        self.health_filter = state
        self._refresh_views()
        self.redraw()
        self._announce_inspection('Check filter: ' + state + (' (suspended during history)' if self.moment is not None else ''))

    def set_path_only(self, enabled: bool) -> None:
        self.path_only = bool(enabled)
        self._refresh_views()
        self.redraw()
        self._announce_inspection('Trace a path with P before isolating it.' if self.path_only and not self._path_nodes else '')

    def _refresh_views(self) -> None:
        def matches(key: str) -> bool:
            if self.path_only and self._path_nodes and key not in self._path_nodes:
                return False
            # Latest readiness is not a historical observation.
            if self.moment is not None or self.health_filter == 'All checks':
                return True
            check = self.checks.get(key)
            if self.health_filter == 'Not checked':
                return check is None
            return check is not None and bool(check.get('passed')) == (self.health_filter == 'Ready')
        self.VIEWS = tuple((node.node_id, node.label) for node in BrainMesh.NODES
                           if (self.layer_filter == "All layers" or node.layer == self.layer_filter) and matches(node.node_id))
        self._keyboard_index = 0
        self._hover = None
        if self.active not in dict(self.VIEWS):
            self.active = ""
        if self.contract_active is not None and not set(self.contract_active) <= set(dict(self.VIEWS)):
            self.contract_active = None
        self.contract_hover = None
        self._link_cycle = None

    def _visible_edge(self, source: str, target: str) -> bool:
        if source not in self._positions or target not in self._positions:
            return False
        if self.path_only and self._path_edges and (source, target) not in self._path_edges:
            return False
        focus = self._hover or self.active
        if not self.focus_connections or not focus or self.tour_edge is not None:
            return True
        members = neighborhood(focus, self.connection_hops)
        return source in members and target in members

    def set_checks(self, nodes: dict[str, dict[str, Any]]) -> None:
        self.checks = {key: dict(value) for key, value in nodes.items() if key in LAYERS}
        self._refresh_views()
        self.redraw()
        self._announce_inspection()

    def set_evidence(self, layer: dict[str, Any]) -> None:
        self.evidence = dict(layer.get("nodes", {}))
        self.redraw()
        self._announce_inspection()

    def evidence_detail(self, key: str) -> str:
        if self.moment is not None:
            return "Historical evidence was not recorded for this moment. Return to Live for the latest checks."
        item = self.evidence.get(key)
        if not item:
            return "Evidence: not checked yet. Press Self Check."
        gaps = "\n".join("• " + gap for gap in item.get("gaps", [])) or "No gaps found by this check."
        return f"Evidence: {item['state']}\nChecked: {item['checked_at']}\n{gaps}\n{item['scope']}"

    def set_activity(self, activity: dict[str, Any], voice: dict[str, Any], camera_on: bool = False) -> None:
        flows = active_flows(activity, voice, camera_on)
        self._live_flows = flows
        working = self.components_for_activity(activity, voice, camera_on)
        self._live_components = working
        if self.moment is None and (flows != self.flows or working != self._active_components):
            self.flows, self._edges = flows, flow_edges(flows)
            self._active_components = working
            if self.winfo_ismapped():
                self._wake()
                self.redraw()

    # ------------------------------------------------------- components

    def set_components(self, snapshot: Any) -> None:
        """Show a ``brain_components.Snapshot``; None removes the component layer."""
        from app.brain_components import place

        if snapshot is None:
            self.components, self.component_states, self.component_links = {}, {}, ()
            self._component_base, self._tool_owners, self._model_owners = {}, {}, {}
            self._component_links_by_end = {}
        else:
            self.components = {item.component_id: item for item in snapshot.components}
            self.component_states = dict(snapshot.states)
            self.component_links = tuple(link for link in snapshot.links
                                         if link.a in self.components and link.b in self.components)
            self._component_base = place(snapshot.components, LAYOUT)
            self._tool_owners = snapshot.tool_index()
            self._model_owners = {}
            for item in snapshot.components:
                if item.kind == "model" and " / " in item.detail:
                    self._model_owners.setdefault(item.detail.split(" / ", 1)[1], []).append(item.component_id)
            self._component_links_by_end = {}
            for link in self.component_links:
                self._component_links_by_end.setdefault(link.a, []).append(link)
                self._component_links_by_end.setdefault(link.b, []).append(link)
        for name in ("component_hover", "component_active"):
            if getattr(self, name) not in self.components:
                setattr(self, name, None)
        population = getattr(snapshot, "population", None) if snapshot is not None else None
        if population is not self.population:
            kept = (self.population.ids[self.population_active]
                    if self.population is not None and self.population_active is not None else None)
            self.population, self.population_hover = population, None
            self.population_active = (population.ids.index(kept) if population is not None and kept in population.ids
                                      else None)
        self.redraw()

    # ------------------------------------------------------ neuron population
    #: Synapses drawn when nothing is chosen (the strongest), and with every neuron showing.
    POPULATION_LINKS = 450
    POPULATION_LINKS_ALL = 2500

    def _draw_population(self, w: float, h: float) -> None:
        """Every memory and knowledge neuron in one image: projected together, splatted together."""
        import numpy as np
        from PIL import Image, ImageDraw, ImageTk

        population = self.population
        started = time.perf_counter()
        moving = self._effective_detail() == "Low" and self.mesh_detail != "Low"
        scale = .5 if moving else 1.0
        # The stored units, then a sample of the language models' own neurons (placed by layer and expert).
        stored = len(population.ids)
        model_index, model_positions, model_codes = self._population_models(population, moving, np)
        # Joined once per population and sample, as 32-bit numbers: the same 230,000 rows were stacked again for
        # every frame, and projecting them in double precision buys nothing at screen resolution.
        joined_key = (id(population), stored, len(model_index), moving)
        if getattr(self, "_population_joined_key", None) != joined_key:
            joined = population.positions if not len(model_index) else np.vstack([population.positions, model_positions])
            self._population_joined = np.ascontiguousarray(joined, dtype=np.float32)
            self._population_joined_map = np.concatenate([np.arange(stored, dtype=np.int64), model_index])
            self._population_joined_key = joined_key
        positions = self._population_joined
        self._population_index_map = self._population_joined_map
        x, y, z = positions[:, 0], positions[:, 1], positions[:, 2]
        ca, sa, cp, sp = math.cos(self.angle), math.sin(self.angle), math.cos(self.pitch), math.sin(self.pitch)
        x, z = x * ca + z * sa, z * ca - x * sa
        y, z = y * cp - z * sp, y * sp + z * cp
        factor = self._scale(w, h) * 3.8 / (3.8 - z)
        sx, sy = w / 2 + x * factor, h * self.CENTRE_Y + y * factor
        self._population_screen = (sx, sy, z)
        # Only the area the neurons occupy becomes an image: handing Tk a whole-canvas RGBA image cost 136 ms.
        on_canvas = (sx >= 0) & (sx < w) & (sy >= 0) & (sy < h)
        if not on_canvas.any():
            return
        x0, x1 = max(0, int(sx[on_canvas].min()) - 8), min(int(w), int(sx[on_canvas].max()) + 9)
        y0, y1 = max(0, int(sy[on_canvas].min()) - 8), min(int(h), int(sy[on_canvas].max()) + 9)
        key = (id(population), round(self.angle, 5), round(self.pitch, 5), round(self.zoom, 5), int(w), int(h), scale,
               self.population_hover, self.population_active, self.show_all_tools, len(population.fired), self.palette.HOLO,
               len(model_index))
        if key == getattr(self, "_population_key", None) and self._population_photo is not None:
            # Nothing it shows has changed (a pulse or a part redrew the scene): reuse the image.
            self.create_image(x0, y0, image=self._population_photo, anchor="nw", tags=("scene", "population"))
            self._population_ms = (time.perf_counter() - started) * 1000
            return
        self._population_key = key
        width, height = max(8, int((x1 - x0) * scale)), max(8, int((y1 - y0) * scale))
        lx, ly = (sx - x0) * scale, (sy - y0) * scale
        near = np.clip((z + 1.1) / 2.2, 0, 1)
        p = self.palette
        rgb = lambda colour: np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float32)
        used, lit = rgb(p.GOLD), rgb(p.WHITE)
        base_colours = colours = self._population_colours(population, np)
        if len(model_index):
            base_colours = colours = np.vstack([colours, self._population_colour_table[model_codes.astype(np.int64)]])
        now = time.time()
        # Active = used in the last week, or recalled for a recent reply (a tool call or step once ever is not "active").
        # Model neurons are never marked active: their firing is not visible to HAYAT.
        active = (now - np.nan_to_num(population.last_used, nan=0.0)) < 7 * 86400
        if len(model_index):
            active = np.concatenate([active, np.zeros(len(model_index), dtype=bool)])
        for index in population.fired:
            active[index] = True
        colours = np.where(active[:, None], colours * .45 + used * .55, colours)
        alpha = (.28 + .5 * near + .22 * active) * 255
        # Chosen neurons by population index (links), and where they are on screen (rings).
        chosen = {index for index in (self.population_hover, self.population_active) if index is not None and index < stored}
        chosen_screen = [screen for screen in (self._screen_index(index, np) for index in
                                               (self.population_hover, self.population_active) if index is not None)
                         if screen is not None]
        px, py = np.round(lx).astype(np.int64), np.round(ly).astype(np.int64)
        inside = (px >= 0) & (px < width) & (py >= 0) & (py < height)
        # A soft glow of neuron density under the dots: brighter where neurons are dense, and no fully transparent
        # gaps between them. Tk traces every transparent run of an image; 45,000 separate dots made that 120 ms.
        from PIL import ImageFilter
        density = np.bincount(py[inside] * width + px[inside], minlength=width * height).reshape(height, width)
        blurred = np.asarray(Image.fromarray(np.clip(density * 80, 0, 255).astype(np.uint8), "L")
                             .filter(ImageFilter.GaussianBlur(radius=max(3.0, 8 * scale))), dtype=np.float32)
        pixels = np.zeros((height, width, 4), dtype=np.uint8)
        pixels[..., :3] = rgb(blend(p.PANEL, p.ROYAL_SOFT, .55)).astype(np.uint8)
        pixels[..., 3] = np.where(blurred > .5, np.clip(14 + blurred * .3, 0, 60), 0).astype(np.uint8)
        # Neurons: a 2x2 splat each (plus a cross for active ones). Every pixel of every dot goes into one array,
        # sorted dimmest first, and is written in a single assignment: the brightest write lands last and wins.
        everyone = self._population_sample(population, active, moving, np)
        self._population_drawn = everyone
        lit_ones = everyone[active[everyone]]
        xs_, ys_, alphas_, owners_ = [], [], [], []
        for dx, dy, weight, members in ((-1, 0, .45, lit_ones), (0, -1, .45, lit_ones), (1, 1, .5, everyone),
                                        (1, 0, .7, everyone), (0, 1, .7, everyone), (0, 0, 1.0, everyone)):
            xs_.append(px[members] + dx)
            ys_.append(py[members] + dy)
            alphas_.append(alpha[members] * weight)
            owners_.append(members)
        qx, qy, qa, owner = (np.concatenate(xs_), np.concatenate(ys_), np.concatenate(alphas_), np.concatenate(owners_))
        keep = (qx >= 0) & (qx < width) & (qy >= 0) & (qy < height)
        qx, qy, qa, owner = qx[keep], qy[keep], qa[keep], owner[keep]
        # Sorted as bytes (what the alpha channel holds): numpy sorts 8-bit keys with a radix sort, several times
        # faster here than comparing 180,000 floats, and the picture is the same to the last bit.
        qa = np.clip(qa, 0, 255).astype(np.uint8)
        order = np.argsort(qa, kind="stable")
        qx, qy, qa, owner = qx[order], qy[order], qa[order], owner[order]
        pixels[qy, qx, :3] = np.clip(colours[owner], 0, 255).astype(np.uint8)
        pixels[qy, qx, 3] = np.maximum(pixels[qy, qx, 3], qa)
        layer = Image.fromarray(pixels, "RGBA")
        draw = ImageDraw.Draw(layer, "RGBA")
        # Synapses over the neurons: the chosen neuron's own, then the strongest (fewer while the brain moves).
        if len(population.links):
            budget = 0 if moving and not chosen else (self.POPULATION_LINKS_ALL if self.show_all_tools else self.POPULATION_LINKS)
            # The strongest-first order of the synapses changes only with the population, not with the camera:
            # sorting every link again for every frame was pure repetition (and was done even to draw none).
            if getattr(self, "_population_link_order_key", None) != (id(population), len(population.links)):
                self._population_link_order = np.argsort(-population.link_weights)
                self._population_link_order_key = (id(population), len(population.links))
            rows = self._population_link_order[:budget].tolist() if budget else []
            if chosen:
                touching = np.nonzero(np.isin(population.links[:, 0], list(chosen)) | np.isin(population.links[:, 1], list(chosen)))[0]
                rows = list(dict.fromkeys(touching.tolist() + rows))
            for row in rows:
                a, b = int(population.links[row, 0]), int(population.links[row, 1])
                touched = a in chosen or b in chosen
                depth = float(near[a] + near[b]) / 2
                colour = (*[int(v) for v in (used if touched else base_colours[a])],
                          int(255 * (.85 if touched else .1 + .16 * depth)))
                draw.line((lx[a], ly[a], lx[b], ly[b]), fill=colour, width=2 if touched else 1)
        for index in chosen_screen:
            cx, cy = lx[index], ly[index]
            draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=(*[int(v) for v in lit], 255), width=2)
        if scale != 1.0:
            layer = layer.resize((max(1, x1 - x0), max(1, y1 - y0)), Image.BILINEAR)
        if self._population_photo is not None and (self._population_photo.width(), self._population_photo.height()) == layer.size:
            self._population_photo.paste(layer)
        else:
            self._population_photo = ImageTk.PhotoImage(layer, master=self)
        self.create_image(x0, y0, image=self._population_photo, anchor="nw", tags=("scene", "population"))
        self._population_ms = (time.perf_counter() - started) * 1000

    #: Dots drawn at most (still / moving). Every neuron still counts in the density glow, the counts and the graph.
    POPULATION_DRAW_LIMIT = 60_000
    POPULATION_DRAW_LIMIT_MOVING = 30_000

    def _population_sample(self, population: Any, active: Any, moving: bool, np: Any) -> Any:
        """Which neurons get their own dot: all of them up to the limit; above it every active one, then an even
        sample of the rest (fixed per population and minute, so the picture does not flicker)."""
        limit = self.POPULATION_DRAW_LIMIT_MOVING if moving else self.POPULATION_DRAW_LIMIT
        total = len(active)
        if total <= limit:
            return np.arange(total)
        key = (id(population), limit, int(time.time() // 60), self.population_hover, self.population_active)
        if getattr(self, "_population_sample_key", None) == key:
            return self._population_sample_cache
        chosen = np.nonzero(active)[0][:limit]
        rest = np.nonzero(~active)[0]
        room = max(0, limit - len(chosen))
        if room and len(rest):
            rest = rest[np.linspace(0, len(rest) - 1, num=min(room, len(rest))).astype(np.int64)]
            chosen = np.concatenate([chosen, rest])
        extra = [screen for screen in (self._screen_index(index, np) for index in (self.population_hover, self.population_active)
                                       if index is not None) if screen is not None]
        if extra:
            chosen = np.unique(np.concatenate([chosen, np.array(extra, dtype=np.int64)]))
        self._population_sample_key, self._population_sample_cache = key, chosen
        return chosen

    def _population_colours(self, population: Any, np: Any) -> Any:
        """One colour per kind of neuron (memory teal, knowledge blue, sources and evidence green, experience
        violet…), looked up from the kind codes once per population and palette, not per neuron per frame."""
        key = (id(population), self.palette.HOLO, self.palette.ROYAL_SOFT)
        if getattr(self, "_population_colour_key", None) == key:
            return self._population_colour_cache
        p = self.palette
        rgb = lambda colour: [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
        from app.brain_population import KINDS
        by_kind = {"memory": p.HOLO, "claim": p.ROYAL_SOFT, "knowledge": p.ROYAL_SOFT, "answer": p.ROYAL_SOFT,
                   "investigation": p.GOLD_SOFT, "source": p.OLIVE_SOFT, "evidence": p.OLIVE_SOFT,
                   "lesson": p.GOLD_SOFT, "procedure": p.GOLD_SOFT, "task": p.WHITE, "step": p.WHITE,
                   "topic": p.GOLD_SOFT, "situation": p.HOLO, "turn": p.HOLO, "tool experience": "#B79CFF",
                   # Decisions are mostly approvals: amber, not the alert red that would read as errors.
                   "fact revision": p.ROYAL_SOFT, "decision": "#E0B07A", "world object": p.OLIVE_SOFT,
                   "feed check": p.OLIVE_SOFT, "own code": "#8FD3C8", "signal": "#9AA7B8",
                   "own function": "#8FD3C8", "model neuron": "#8C7BFF", "embedding neuron": p.HOLO}
        table = np.array([rgb(by_kind.get(kind, p.ROYAL_SOFT)) for kind, *_rest in KINDS], dtype=np.float32)
        codes = getattr(population, "kind_codes", None)
        if codes is None or len(codes) != len(population):
            codes = np.array([0 if kind == "memory" else 2 for kind in population.kinds], dtype=np.int8)
        self._population_colour_table = table
        self._population_colour_key, self._population_colour_cache = key, table[codes.astype(np.int64)]
        return self._population_colour_cache

    #: Model neurons drawn as dots (still / moving); all of them are counted.
    MODEL_DRAW_LIMIT = 24_000
    MODEL_DRAW_LIMIT_MOVING = 10_000

    def _population_models(self, population: Any, moving: bool, np: Any) -> tuple[Any, Any, Any]:
        """A fixed sample of the model blocks' neurons with their positions (cached per population and limit)."""
        if not getattr(population, "blocks", None):
            return np.zeros(0, dtype=np.int64), np.zeros((0, 3)), np.zeros(0, dtype=np.int8)
        limit = self.MODEL_DRAW_LIMIT_MOVING if moving else self.MODEL_DRAW_LIMIT
        key = (id(population), limit)
        cache = getattr(self, "_population_model_cache", {})
        if key not in cache:
            from app.brain_population import block_sample
            # Keep the still and the moving sample of this population side by side: replacing one with the other
            # re-sampled millions of model neurons at the start and the end of every drag.
            cache = {name: value for name, value in cache.items() if name[0] == id(population)}
            cache[key] = block_sample(population, limit)
            self._population_model_cache = cache
        return cache[key]

    def _screen_index(self, index: int, np: Any) -> int | None:
        """Where a population index is in the drawn arrays: stored units first, then the sampled model neurons."""
        stored = len(self.population.ids) if self.population is not None else 0
        if index < stored:
            return index
        mapping = getattr(self, "_population_index_map", None)
        if mapping is None:
            return None
        found = np.nonzero(mapping[stored:] == index)[0]
        return int(stored + found[0]) if len(found) else None

    def _hit_population(self, x: float, y: float) -> int | None:
        if self.population is None or self._population_screen is None or not self.show_components:
            return None
        import numpy as np

        sx, sy, z = self._population_screen
        drawn = getattr(self, "_population_drawn", None)
        if drawn is None or not len(drawn):
            drawn = np.arange(len(sx))
        # Only neurons that have their own dot can be pointed at.
        distance = np.hypot(sx[drawn] - x, sy[drawn] - y) - (z[drawn] > self.COMPONENT_HIDDEN_Z) * .5
        best = int(np.argmin(distance))
        if distance[best] > 5.5:
            return None
        mapping = getattr(self, "_population_index_map", None)
        screen = int(drawn[best])
        return int(mapping[screen]) if mapping is not None and screen < len(mapping) else screen

    def select_population(self, index: int | None) -> None:
        self.population_active = index if self.population is not None and index is not None \
            and 0 <= index < len(self.population) else None
        self.redraw()

    def _draw_population_label(self, index: int, w: float) -> None:
        from app.gui_theme import owned_font

        import numpy as np

        p = self.palette
        sx, sy, _z = self._population_screen
        screen = self._screen_index(index, np)
        if screen is None or screen >= len(sx):
            return
        x, y = float(sx[screen]), float(sy[screen])
        population = self.population
        from app.brain_population import NAMES

        if index >= len(population.ids):
            # A language-model neuron: its real address, and the honest limit on what is known about it.
            first = population.describe(index).splitlines()
            lines = [first[0], NAMES.get(population.block_of(index)[0]["kind"], "model neuron").capitalize()
                     + " · structure from the model file", "Its firing is not visible to HAYAT"]
        else:
            kind = population.kinds[index]
            lines = [population.labels[index], f"{NAMES.get(kind, kind)} neuron · {population.groups[index]}",
                     f"{int(population.uses[index]):,} recorded uses · {len(population.neighbours(index))} synapses"]
        if not hasattr(self, "_measure_font"):
            self._measure_font = owned_font(self, family="Segoe UI", size=8)
        face = self._measure_font
        for line in range(3):
            while face.measure(lines[line]) > max(80, min(420, w - 36)) and len(lines[line]) > 4:
                lines[line] = lines[line][:-2].rstrip("…") + "…"
        width = min(w - 12, max(face.measure(text) for text in lines) + 24)
        left, top = max(6, min(w - width - 6, x - width / 2)), max(24, y - 76)
        rounded(self, (left, top, left + width, top + 59), radius=10, tags=("scene", "label", "population-label"),
                fill=blend(p.PANEL, p.BLACK, .4), outline=blend(p.PANEL, p.HOLO, .7))
        for offset, text, colour, font in ((7, lines[0], p.WHITE, ("Segoe UI Semibold", 9)), (26, lines[1], p.MUTED, ("Segoe UI", 8)),
                                           (44, lines[2], p.GOLD_SOFT, ("Segoe UI", 8))):
            self.create_text(left + 11, top + offset, anchor="nw", text=text, fill=colour, font=font,
                             tags=("scene", "label", "population-label"))

    def components_for_activity(self, activity: dict[str, Any], voice: dict[str, Any],
                                camera_on: bool = False) -> set[str]:
        """The component neurons doing recorded work right now, from the activity register."""
        if not self.components:
            return set()
        working: set[str] = set()
        for row in activity.get("running") or []:
            kind, label, detail = str(row.get("kind", "")), str(row.get("label", "")), str(row.get("detail", ""))
            if kind == "tool":
                working.update(self._tool_owners.get(label, ()))
            elif kind == "model":
                # "ollama/hayat:latest": the role(s) configured with that model are thinking.
                working.update(self._model_owners.get(detail.split("/", 1)[-1], ()))
            elif kind == "learning":
                working.add("cap:learning")
            elif kind == "task":
                working.add("svc:teams" if label == "Worker team" else f"helper:{label}")
            elif kind == "service" and label in {"Load model", "Unload model"}:
                working.add("svc:model_resources")
        if camera_on:
            working.add("cap:senses")
        state = str(voice.get("state", ""))
        if (voice.get("enabled") and state in {"listening", "transcribing"}) or state == "speaking":
            working.add("cap:voice")
        return {key for key in working if key in self.components}

    def set_component_heat(self, tool_seconds: dict[str, float] | None) -> None:
        """Glow each component by its tools' recorded busy time over the window; None clears it."""
        busy: dict[str, float] = {}
        for tool, seconds in (tool_seconds or {}).items():
            for owner in self._tool_owners.get(tool, ()):
                busy[owner] = busy.get(owner, 0.0) + max(0.0, float(seconds))
        most = max(busy.values(), default=0.0)
        self.component_heat = {key: value / most for key, value in busy.items() if value > 0} if most else {}
        self.redraw()

    def select_component(self, component_id: str | None) -> None:
        self.component_active = component_id if component_id in self.components else None
        self.redraw()

    def select(self, key: str) -> None:
        # Choosing a subsystem puts any chosen component down.
        if self.component_active is not None and key in LAYERS:
            self.component_active = None
        self.contract_active = self.contract_hover = None
        self._link_cycle = None
        super().select(key)
        self._announce_inspection()

    def component_detail(self, component_id: str) -> str:
        from app.brain_components import describe as describe_component

        component = self.components[component_id]
        labels = {key: item.label for key, item in self.components.items()}
        parent = dict((node.node_id, node.label) for node in BrainMesh.NODES).get(component.parent, "")
        return describe_component(component, self.component_states.get(component_id), parent_label=parent,
                                  links=self._component_links_by_end.get(component_id, ()), labels=labels)

    def set_heat(self, seconds: dict[str, float] | None) -> None:
        """Glow each part by its share of the busiest part's time; None clears it."""
        seconds = {key: value for key, value in (seconds or {}).items() if key in LAYERS and value > 0}
        most = max(seconds.values(), default=0.0)
        self.heat = {key: value / most for key, value in seconds.items()} if most else {}
        self.redraw()

    def show_moment(self, moment: float | None, flows: list[str] | None = None,
                    marks: list[Any] | None = None, echoes: list[str] | None = None,
                    tools: list[str] | None = None) -> None:
        """Show the brain as recorded at ``moment``, or live again with None.

        ``marks`` are failures, denials and approval requests near that
        moment; each lights the node it happened at. ``echoes`` are flows
        used just before it: their paths stay faintly lit, so a scrub shows
        where the work had just been as well as where it is.
        """
        self.moment = moment
        self._refresh_views()
        self._echo_edges = []
        self._component_marks = {}
        if moment is None:
            flows, self._moment_marks = self._live_flows, {}
            self._active_components = set(self._live_components)
        else:
            flows = [flow for flow in (flows or []) if flow in FLOWS]
            self._moment_marks = {}
            for mark in marks or []:
                # A failure outranks an approval request on the same node.
                if mark.node in LAYERS and self._moment_marks.get(mark.node) != "failure":
                    self._moment_marks[mark.node] = mark.kind
                for owner in self._tool_owners.get(getattr(mark, "tool", "") or "", ()):
                    if self._component_marks.get(owner) != "failure":
                        self._component_marks[owner] = mark.kind
            # The components whose tools were recorded running at that moment.
            self._active_components = {owner for tool in tools or () for owner in self._tool_owners.get(tool, ())}
        self.flows, self._edges = list(flows), flow_edges(list(flows))
        if moment is not None and echoes:
            current = set(self._edges)
            self._echo_edges = [edge for edge in flow_edges([flow for flow in echoes if flow in FLOWS])
                                if edge not in current]
        self._wake()
        self.redraw()

    # --------------------------------------------------------- geometry

    def _base(self, index: int) -> tuple[float, float, float]:
        return LAYOUT[self.VIEWS[index][0]]

    def _point(self, point: tuple[float, float, float], w: float, h: float) -> tuple[float, float, float]:
        x, y, z = point
        x, z = x * math.cos(self.angle) + z * math.sin(self.angle), z * math.cos(self.angle) - x * math.sin(self.angle)
        y, z = y * math.cos(self.pitch) - z * math.sin(self.pitch), y * math.sin(self.pitch) + z * math.cos(self.pitch)
        scale = self._scale(w, h) * 3.8 / (3.8 - z)
        return w / 2 + x * scale, h * self.CENTRE_Y + y * scale, z

    #: Where the brain sits and how much of the canvas it may fill. The old
    #: 0.30/0.33 left a third of a short canvas empty under a floor grid.
    CENTRE_Y = .5
    SPAN = (.3, .37)
    #: Shell facets whose centre lies this far behind the brain's middle are
    #: hidden by the near side; the fissure and silhouette stay drawn.
    HIDDEN_FACET_Z = -.35

    def _scale(self, w: float, h: float) -> float:
        return max(1e-9, min(w * self.SPAN[0], h * self.SPAN[1]) * self.zoom)

    def _draw_backdrop(self, w: float, h: float) -> None:
        """A soft glow behind the brain. No floor: the brain floats, and the
        floor grid cost the bottom fifth of the canvas."""
        p = self.palette
        key = ("brain", w, h, p.PANEL, p.ROYAL)
        if key == self._backdrop:
            return
        self._backdrop = key
        self.delete("backdrop")
        cx, cy = w / 2, h * self.CENTRE_Y
        radius = min(w * self.SPAN[0], h * self.SPAN[1]) * 1.25
        for i in range(14, 0, -1):
            r = radius * i / 11
            self.create_oval(cx - r, cy - r * .78, cx + r, cy + r * .78, tags="backdrop",
                             fill=blend(p.PANEL, p.ROYAL, (1 - i / 15) * .16), outline="")
        self.tag_lower("backdrop")

    @staticmethod
    @lru_cache(maxsize=4)
    def _shell_facets(rows: int = 16, columns: int = 24) -> list[tuple[tuple[float, float, float], ...]]:
        """A bounded folded cortical scaffold with an open central fissure.

        Fixed geometry keeps idle frames stable. The folds are illustrative,
        rather than an anatomical reconstruction or a runtime measurement.
        """
        if type(rows) is not int or type(columns) is not int or not 4 <= rows <= 20 or not 8 <= columns <= 32:
            raise ValueError("Mesh geometry must stay within the bounded detail budget.")
        facets = []
        for side in (-1, 1):
            grid = []
            for row in range(rows + 1):
                latitude = -1.22 + row * 2.44 / rows
                ring = []
                for column in range(columns + 1):
                    longitude = -1.42 + column * 2.84 / columns
                    envelope = math.cos(latitude) ** 2 * math.cos(longitude) ** 2
                    fold = envelope * (.045 * math.sin(17 * latitude + 3 * math.sin(4 * longitude))
                                       + .028 * math.sin(19 * longitude - 2 * math.sin(5 * latitude)))
                    radius = 1 + fold
                    ring.append((side * (.055 + 1.17 * radius * math.cos(latitude) * math.cos(longitude)),
                                 .88 * radius * math.sin(latitude),
                                 1.08 * radius * math.cos(latitude) * math.sin(longitude)))
                grid.append(ring)
            for row in range(rows):
                for column in range(columns):
                    a, b = grid[row][column:column + 2]
                    d, c = grid[row + 1][column:column + 2]
                    facets.extend(((a, b, c), (a, c, d)))
        return facets

    # ---------------------------------------------------------- drawing

    def _node_colour(self, key: str) -> str:
        p = self.palette
        if self.moment is not None:
            return p.ROYAL_SOFT
        check = self.checks.get(key)
        if check is None:
            return p.ROYAL_SOFT
        if check.get("passed"):
            return p.OLIVE_SOFT
        critical = next(node.critical for node in BrainMesh.NODES if node.node_id == key)
        return p.ALERT if critical else p.GOLD

    def redraw(self) -> None:
        if self._effective_detail() != self.render_detail:
            self._load_geometry()
        w, h = self._size()
        p = self.palette
        self.configure(bg=p.PANEL, highlightbackground=p.LINE, highlightcolor=p.GOLD)
        self._draw_backdrop(w, h)
        self.delete("scene")
        self._contract_curves = {}
        points = [self._project(i, w, h) for i in range(len(self.VIEWS))]
        self._positions = {key: points[i] for i, (key, _label) in enumerate(self.VIEWS)}
        focus = self._hover or self.active
        busy_nodes = {key for flow in self.flows for key in FLOWS[flow]}
        layers: list[tuple[float, int, Callable[[], None]]] = []
        ca, sa, cp, sp = math.cos(self.angle), math.sin(self.angle), math.cos(self.pitch), math.sin(self.pitch)
        projected_vertices = {}
        # Shades are quantised and cached: 1,536 facets per frame would
        # otherwise cost thousands of colour blends while the brain turns.
        shades: dict[tuple[int, int], tuple[str, str]] = {}
        for facet, normal in zip(self._shell, self._normals) if self.surface != "Hidden" else ():
            # Adjacent triangles share vertices. Project each point once per
            # frame rather than once for every triangle that uses it.
            for point in facet:
                if point not in projected_vertices:
                    projected_vertices[point] = self._point(point, w, h)
            projected = [projected_vertices[point] for point in facet]
            z = sum(point[2] for point in projected) / 3
            if z < self.HIDDEN_FACET_Z:
                # Painted first and covered by the near side of the shell, so
                # drawing it only cost time on every frame of a rotation.
                continue
            near = max(0.0, min(1.0, (z + 1.2) / 2.4))
            # Camera-space surface normals give folds consistent directional
            # lighting as the brain turns. Keep the shell translucent-looking
            # so subsystem labels and pathways remain the visual foreground.
            nx, ny, nz = normal
            nx, nz = nx * ca + nz * sa, nz * ca - nx * sa
            ny, nz = ny * cp - nz * sp, ny * sp + nz * cp
            light = abs(nx * -.36 + ny * -.48 + nz * .8)
            key = (round(near * 12), round(light * 6))
            if key not in shades:
                n, lit = key[0] / 12, key[1] / 6
                fill = blend(p.PANEL, p.ROYAL_SOFT, .06 + n * (.10 + .22 * lit))
                # Filled surfaces keep triangulation quiet; the dedicated
                # Wireframe mode exposes the triangle edges when requested.
                outline = fill
                shades[key] = (fill, outline)
            fill, outline = shades[key]
            if self.surface == "Wireframe":
                fill = ""
                outline = blend(p.PANEL, p.ROYAL_SOFT, .16 + .35 * near)
            elif self.surface == "Standing waves":
                centre = tuple(sum(point[i] for point in facet) / 3 for i in range(3))
                wave = standing_wave(centre, self.wave_modes)
                tint = p.GOLD_SOFT if abs(wave) < .10 else p.ROYAL_SOFT if wave > 0 else p.HOLO
                fill = blend(p.PANEL, tint, .09 + .16 * near)
                outline = blend(p.PANEL, tint, .18 + .24 * near)
            layers.append((z - 2, 0, lambda s=projected, f=fill, o=outline: self.create_polygon(
                *[v for point in s for v in point[:2]], fill=f, outline=o, width=1, tags=("scene", "mesh"))))
        fissure = [self._point((0, .88 * math.sin(-1.2 + row * .3),
                                 1.08 * math.cos(-1.2 + row * .3)), w, h) for row in range(9)]
        for start, end in zip(fissure, fissure[1:]) if self.surface != "Hidden" and not self.asset_loaded else ():
            z = (start[2] + end[2]) / 2
            colour = blend(p.PANEL, p.GOLD_SOFT, .34 + .26 * max(0, z))
            layers.append((z - 1, 0, lambda a=start, b=end, c=colour: self.create_line(
                *a[:2], *b[:2], fill=c, width=1.6, tags=("scene", "fissure"))))
        for edge in BrainMesh.EDGES:
            if not self._visible_edge(edge.source, edge.target):
                continue
            (x, y, z), (xx, yy, zz) = self._positions[edge.source], self._positions[edge.target]
            flowing = (edge.source, edge.target) in self._edges
            touring = (edge.source, edge.target) == getattr(self, "tour_edge", None)
            inspected = (edge.source, edge.target) in (self.contract_active, self.contract_hover)
            touched = inspected or touring or (focus in (edge.source, edge.target) and getattr(self, "tour_edge", None) is None)
            echo = not flowing and (edge.source, edge.target) in getattr(self, "_echo_edges", ())
            on_path = (edge.source, edge.target) in self._path_edges
            near = (z + zz + 2) / 4
            colour = (p.GOLD_SOFT if on_path else p.GOLD if touched else p.HOLO if flowing
                      else blend(p.PANEL, p.HOLO, .45) if echo
                      else blend(p.PANEL, p.ROYAL_SOFT, .15 + near * .35))
            curve = contract_curve((x, y), (xx, yy), self._radius(z) * .8 + 10, self._radius(zz) * .8 + 12)
            self._contract_curves[(edge.source, edge.target)] = (curve, (z + zz) / 2)
            path = tuple(coordinate for i in range(17) for coordinate in curve_point(curve, i / 16))
            layers.append(((z + zz) / 2, 1, lambda path=path, c=colour, t=touched, f=flowing, e=echo,
                           r=touring, q=on_path, selected=inspected, pair=(edge.source, edge.target):
                           self.create_line(*path, fill=c,
                                            width=5 if q else 4 if r or selected else 2 if (t or f) else 1.5 if e else 1,
                                            dash=(3, 3) if e else (), arrow="last",
                                            arrowshape=(7, 8, 3), tags=("scene", "contract", f'contract-{pair[0]}-{pair[1]}',
                                            'inspected-contract' if selected else 'echo' if e else 'declared-contract'))))
        self._queue_components(layers, w, h, focus)
        if self.population is not None and len(self.population) and self.show_components:
            # Above the shell (whose layers end at z - 2 <= -0.8), below the pathways and the parts.
            layers.append((-.79, 0, lambda: self._draw_population(w, h)))
        else:
            self._population_screen = None
        for i, (key, label) in enumerate(self.VIEWS):
            layers.append((points[i][2], 3, lambda i=i, key=key, label=label:
                           self._draw_brain_node(key, label, *points[i], key in busy_nodes)))
        self._names: list[tuple[float, float, float, float, str, bool]] = []
        self._discs: list[tuple[float, float, float, float]] = []
        for _z, _order, draw in sorted(layers, key=lambda layer: layer[:2]):
            draw()
        self._draw_legend(w, h)
        self._legend_boxes = [self.bbox(tag) for tag in ("legend-header", "legend-footer") if self.bbox(tag)]
        if self.contract_hover is not None or self.contract_active is not None:
            self._draw_contract_label(self.contract_hover or self.contract_active, w, h)
            if self.bbox('contract-detail'):
                self._legend_boxes.append(self.bbox('contract-detail'))
        names = self._draw_names(w, h)
        self._draw_layers(w, h, names + self._discs + self._legend_boxes)
        if not self.VIEWS:
            self.create_text(w / 2, h / 2, text='No subsystems match these filters.\nH changes checks; select All layers to widen the view.',
                             width=max(80, w - 32), justify='center', fill=p.MUTED,
                             font=('Segoe UI', 10), tags=('scene', 'empty-filter'))
        self._draw_pulses(w, h)
        self._draw_component_pulses()
        if self._pulse is not None and self._pulse[0] in self._positions:
            x, y, z = self._positions[self._pulse[0]]
            t = min(1.0, (self._clock() - self._pulse[1]) / self.PULSE_SECONDS)
            r = self._radius(z) + 4 + 22 * t
            self.create_oval(x - r, y - r, x + r, y + r, tags="scene", width=2,
                             outline=blend(p.GOLD, p.PANEL, t))
        if self._hover in self._positions:
            self._draw_label(self._hover, w)
        elif self.component_hover in self._component_positions:
            self._draw_component_label(self.component_hover, w)
        elif self.population_hover is not None and self._population_screen is not None:
            self._draw_population_label(self.population_hover, w)
        self.tag_raise("legend")

    def _draw_brain_node(self, key: str, label: str, x: float, y: float, z: float, busy: bool) -> None:
        p = self.palette
        tags = ("scene", "node", f"node-{key}")
        near = max(0.0, min(1.0, (z + 1.1) / 2.2))
        colour = self._node_colour(key)
        chosen = key in (self.active, self._hover)
        r = self._radius(z) * .8 + (2 if chosen else 0)
        if self.evidence_on and self.moment is None:
            state = self.evidence.get(key, {}).get("state", "unknown")
            # Unknown is the usual state before a Self Check, so it is a faint
            # hairline: seventeen bright dotted rings buried the brain itself.
            if state == "unknown":
                ring, width, dash, radius = blend(p.PANEL, p.MUTED, .35 + .25 * near), 1, (), r + 6
            else:
                ring = p.OLIVE_SOFT if state == "checked" else p.GOLD
                width, dash, radius = 2, () if state == "checked" else (2, 3), r + 8
            self.create_oval(x - radius, y - radius, x + radius, y + radius,
                             outline=ring, width=width, dash=dash, tags=(*tags, "evidence"))
        warmth = self.heat.get(key, 0.0)
        if warmth > .02:
            # A part busy through the window glows; an idle one does not.
            glow = r + 5 + 12 * warmth
            self.create_oval(x - glow, y - glow, x + glow, y + glow, outline="", tags=(*tags, "heat"),
                             fill=blend(p.PANEL, p.GOLD, .10 + .30 * warmth * (.6 + .4 * near)))
        if busy or chosen:
            halo = p.GOLD if chosen else p.HOLO
            self.create_oval(x - r - 6, y - r - 6, x + r + 6, y + r + 6, tags=tags,
                             fill=blend(p.PANEL, halo, .16), outline=blend(p.PANEL, halo, .6))
        marked = self._moment_marks.get(key)
        if marked:
            # Something happened here at the moment being shown.
            ring = p.ALERT if marked == "failure" else p.GOLD
            self.create_oval(x - r - 9, y - r - 9, x + r + 9, y + r + 9, tags=tags,
                             outline=ring, width=2, dash=(4, 3))
        self.create_oval(x - r + 2, y - r + 3, x + r + 2, y + r + 3, fill=p.BLACK, outline="", tags=tags)
        body = blend(p.PANEL, colour, .3 + .45 * near)
        self.create_oval(x - r, y - r, x + r, y + r, fill=body, tags=tags,
                         outline=blend(p.PANEL, colour, .5 + .5 * near), width=2 if chosen else 1)
        g = r * .38
        self.create_oval(x - r * .55, y - r * .6, x - r * .55 + g, y - r * .6 + g * .8, outline="", tags=tags,
                         fill=blend(body, p.WHITE, .4 * near))
        # Names on the near side only; the far side stays legible by hover,
        # and the hovered node's name is already in its label.
        self._discs.append((x - r - 3, y - r - 3, x + r + 3, y + r + 3))
        if (z > -.25 or key == self.active or key in {'cortex', 'dual_brain', 'invention'}) and key != self._hover:
            self._names.append((z, x, y, r, label, key == self.active))

    #: Level of detail: components this far behind the brain's middle are not
    #: drawn unless chosen, working or marked at the moment shown.
    COMPONENT_HIDDEN_Z = -.3
    #: Unfocused recorded links drawn at once; links of the chosen component always show.
    MAX_COMPONENT_LINKS = 60

    def _component_colour(self, key: str) -> str:
        p = self.palette
        state = self.component_states.get(key)
        if state is None or not (state.calls or state.failures):
            return blend(p.PANEL, p.MUTED, .6)          # nothing recorded yet
        if state.last_ok is False:
            return p.ALERT                              # its last recorded outcome failed
        return p.OLIVE_SOFT

    def _queue_components(self, layers: list[tuple[float, int, Callable[[], None]]], w: float, h: float,
                          focus: str | None) -> None:
        """Membership lines, recorded links and component dots, painted in depth order."""
        self._component_positions = {}
        if not self.show_components or not self.components:
            return
        p = self.palette
        chosen = {self.component_hover, self.component_active} - {None}
        members = (neighborhood(focus, self.connection_hops)
                   if self.focus_connections and focus and self.tour_edge is None else None)
        positions = self._component_positions
        for key, base in self._component_base.items():
            component = self.components[key]
            tool = component.kind == "tool"
            # A tool hangs off its capability area (drawn just before it); everything else off a subsystem.
            parent = positions.get(component.parent) if tool else self._positions.get(component.parent)
            home = self.components[component.parent].parent if tool and component.parent in self.components \
                else component.parent
            if parent is None or (members is not None and home not in members and key not in chosen):
                continue
            working = key in self._active_components
            marked = self._component_marks.get(key)
            area_chosen = tool and component.parent in chosen
            if tool and not (self.show_all_tools or area_chosen or key in chosen or working or marked):
                continue
            x, y, z = self._point(base, w, h)
            if z < self.COMPONENT_HIDDEN_Z and key not in chosen and not working and not marked and not area_chosen:
                continue
            near = max(0.0, min(1.0, (z + 1.1) / 2.2))
            state = self.component_states.get(key)
            calls = state.calls if state is not None else 0
            r = (1.9 + min(2.4, math.log10(1 + calls) * .8)) * (.75 + .4 * near) * (.62 if tool else 1)
            positions[key] = (x, y, z, r)
            lit = key in chosen
            colour = p.GOLD if lit else p.HOLO if working else blend(p.PANEL, p.ROYAL_SOFT, .10 + .18 * near)
            if not tool or area_chosen or lit or working:
                # With every tool showing, only the chosen area's tools keep their lines: 600 lines would be noise.
                layers.append(((z + parent[2]) / 2, 1, lambda a=parent, x=x, y=y, c=colour, lw=1.6 if lit or working else 1:
                               self.create_line(a[0], a[1], x, y, fill=c, width=lw,
                                                tags=("scene", "component-link", "membership"))))
            layers.append((z, 2, lambda key=key, x=x, y=y, r=r, near=near, working=working, lit=lit, marked=marked:
                           self._draw_component(key, x, y, r, near, working, lit, marked)))
        touched_by = self.component_hover or self.component_active
        drawn = 0
        for link in self.component_links:
            a, b = positions.get(link.a), positions.get(link.b)
            if a is None or b is None:
                continue
            touched = touched_by in (link.a, link.b)
            if link.kind == "governs" and not touched:
                continue                                 # governance shows on focus only
            if not touched:
                if drawn >= self.MAX_COMPONENT_LINKS:
                    continue
                drawn += 1
            near = (a[2] + b[2] + 2) / 4
            working = link.a in self._active_components and link.b in self._active_components
            colour = (p.GOLD_SOFT if touched else p.HOLO if working
                      else blend(p.PANEL, p.GOLD_SOFT, .36 + .18 * near) if link.kind == 'integration'
                      else blend(p.PANEL, p.GOLD_SOFT, .22 + .12 * near) if link.kind == "routes"
                      else blend(p.PANEL, p.HOLO, .16 + .16 * near))
            width = (1 + min(1.6, math.log10(1 + link.weight) * .8)) if link.kind == "co-use" else 1
            curve = contract_curve(a, b, a[3] + 2, b[3] + 2)
            path = tuple(value for i in range(9) for value in curve_point(curve, i / 8))
            layers.append(((a[2] + b[2]) / 2, 1, lambda path=path, c=colour, lw=width + (1 if touched else 0),
                           dash=(2, 3) if link.kind == "governs" else (), kind=link.kind:
                           self.create_line(*path, fill=c, width=lw, dash=dash,
                                            arrow='last' if kind in {'integration', 'routes', 'governs'} else 'none',
                                            arrowshape=(5, 6, 2),
                                            tags=("scene", "component-link", kind))))

    def _draw_component(self, key: str, x: float, y: float, r: float, near: float, working: bool,
                        lit: bool, marked: str | None) -> None:
        p = self.palette
        tags = ("scene", "component", f"component-{key}")
        warmth = self.component_heat.get(key, 0.0)
        if warmth > .02:
            glow = r + 3 + 7 * warmth
            self.create_oval(x - glow, y - glow, x + glow, y + glow, outline="", tags=(*tags, "component-heat"),
                             fill=blend(p.PANEL, p.GOLD, .08 + .25 * warmth * (.6 + .4 * near)))
        if marked:
            ring = p.ALERT if marked == "failure" else p.GOLD
            self.create_oval(x - r - 4, y - r - 4, x + r + 4, y + r + 4, outline=ring, width=1.5, dash=(2, 2),
                             tags=tags)
        if working or lit:
            halo = p.GOLD if lit else p.HOLO
            self.create_oval(x - r - 3, y - r - 3, x + r + 3, y + r + 3, outline=halo, width=1.5, tags=tags)
        colour = p.HOLO if working else self._component_colour(key)
        outline = ""
        if self.evidence_on and self.moment is None:
            # The fifth dimension for a component: its own readiness, when one was checked.
            state = self.component_states.get(key)
            outline = {"ready": p.OLIVE_SOFT, "setup": p.GOLD, "off": p.ALERT}.get(
                getattr(state, "readiness", "") or "", "")
        self.create_oval(x - r, y - r, x + r, y + r, fill=blend(p.PANEL, colour, .45 + .5 * near),
                         outline=outline or blend(p.PANEL, colour, .5 + .4 * near), width=1, tags=tags)
        if key == self.component_active and key != self.component_hover:
            label = self.components[key].label
            self.create_text(x, y + r + 8, text=label, font=("Segoe UI", 8), fill=p.GOLD_SOFT,
                             tags=("scene", "component-name"))

    def _draw_component_label(self, key: str, w: float) -> None:
        from app.gui_theme import owned_font

        p = self.palette
        x, y, _z, r = self._component_positions[key]
        component = self.components[key]
        state = self.component_states.get(key)
        parent = next((node.label for node in BrainMesh.NODES if node.node_id == component.parent), "")
        from app.brain_components import KINDS

        kind = f"{KINDS.get(component.kind, component.kind)} · in {parent}"
        if state is not None and (state.calls or state.failures):
            last = ("last ok" if state.last_ok else "last failed" if state.last_ok is False else "last")
            when = time.strftime("%b %d %H:%M", time.localtime(state.last_at)) if state.last_at else "?"
            numbers = f"{state.calls:,} {state.unit.split(' (')[0]} · {state.failures:,} failed · {last} {when}"
        else:
            numbers = "No recorded use yet"
        if not hasattr(self, "_measure_font"):
            self._measure_font = owned_font(self, family="Segoe UI", size=8)
        face = self._measure_font
        lines = [component.label, kind, numbers]
        for index in (1, 2):
            while face.measure(lines[index]) > max(80, w - 36) and len(lines[index]) > 4:
                lines[index] = lines[index][:-2].rstrip("…") + "…"
        width = min(w - 12, max(face.measure(text) for text in lines) + 24)
        left = max(6, min(w - width - 6, x - width / 2))
        top = max(24, y - r - 70)
        rounded(self, (left, top, left + width, top + 59), radius=10, tags=("scene", "label", "component-label"),
                fill=blend(p.PANEL, p.BLACK, .4), outline=blend(p.PANEL, p.HOLO, .7))
        self.create_text(left + 11, top + 7, anchor="nw", text=lines[0], fill=p.WHITE,
                         font=("Segoe UI Semibold", 9), tags=("scene", "label", "component-label"))
        self.create_text(left + 11, top + 26, anchor="nw", text=lines[1], fill=p.MUTED,
                         font=("Segoe UI", 8), tags=("scene", "label", "component-label"))
        self.create_text(left + 11, top + 44, anchor="nw", text=lines[2], fill=self._component_colour(key),
                         font=("Segoe UI", 8), tags=("scene", "label", "component-label"))

    def _draw_component_pulses(self) -> None:
        """A dot per working component, travelling from its subsystem out to it."""
        if not self._active_components or not self._component_positions:
            return
        p = self.palette
        t = self._clock() * self.PULSE_SPEED if self.motion else .5
        for index, key in enumerate(sorted(self._active_components)):
            target = self._component_positions.get(key)
            source = self._positions.get(self.components[key].parent) if key in self.components else None
            if target is None or source is None:
                continue
            f = (t + index * .29) % 1.0
            x, y = source[0] + (target[0] - source[0]) * f, source[1] + (target[1] - source[1]) * f
            self.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=p.WHITE, outline=p.HOLO, width=1.5,
                             tags=("scene", "component-flow"))

    def _hit_component(self, x: float, y: float) -> str | None:
        # The dot under the pointer wins; between equally close dots, the nearer one.
        best: tuple[float, float, str] | None = None
        for key, (px, py, z, r) in self._component_positions.items():
            distance = math.hypot(x - px, y - py)
            if distance <= max(6.0, r + 3) and (best is None or (distance, -z) < best[:2]):
                best = (distance, -z, key)
        return best[2] if best else None

    def _rehover(self) -> None:
        """Hover a subsystem first; between subsystems, the nearest component neuron."""
        if not self._inside or self._pointer is None or self._drag_x is not None:
            return
        key = self._hit(*self._pointer)
        component = None if key else self._hit_component(*self._pointer)
        neuron = None if key or component else self._hit_population(*self._pointer)
        link = None if key or component or neuron is not None else self._hit_contract(*self._pointer)
        self.configure(cursor="hand2" if key or component or neuron is not None or link else "fleur")
        if key != self._hover or component != self.component_hover or neuron != self.population_hover or link != self.contract_hover:
            if key != self._hover and self.on_preview:
                self.on_preview(key or self.active)
            self._hover, self.component_hover, self.population_hover = key, component, neuron
            self.contract_hover = link
            self.redraw()
            self._announce_inspection()

    def _leave(self, event: Any) -> None:
        self.component_hover = self.population_hover = None
        self.contract_hover = None
        super()._leave(event)

    def _double(self, event: Any) -> None:
        if self._hit(float(event.x), float(event.y)) is None:
            component = self._hit_component(float(event.x), float(event.y))
            if component is not None:
                self.select_component(component)
                if callable(self.on_component_drill):
                    self.on_component_drill(component)
                return
        super()._double(event)

    def _hit(self, x: float, y: float) -> str | None:
        # A finger-sized target while preserving frontmost wins for overlaps.
        matches = [(z, key) for key, (px, py, z) in self._positions.items()
                   if math.hypot(x - px, y - py) <= max(14, self._radius(z) + 5)]
        return max(matches)[1] if matches else None

    def _hit_contract(self, x: float, y: float) -> tuple[str, str] | None:
        nearest = None
        for pair, (curve, z) in self._contract_curves.items():
            start = curve_point(curve, 0)
            for index in range(1, 17):
                end = curve_point(curve, index / 16)
                dx, dy = end[0] - start[0], end[1] - start[1]
                length = dx * dx + dy * dy
                fraction = max(0.0, min(1.0, ((x - start[0]) * dx + (y - start[1]) * dy) / length)) if length else 0
                distance = math.hypot(x - start[0] - fraction * dx, y - start[1] - fraction * dy)
                candidate = (distance, -z, pair)
                if distance <= 5 and (nearest is None or candidate < nearest):
                    nearest = candidate
                start = end
        return nearest[2] if nearest is not None else None

    def _draw_contract_label(self, pair: tuple[str, str], w: float, h: float) -> None:
        info, p = self.contract_detail(*pair), self.palette
        width = min(550, max(80, w - 32))
        tags = ('scene', 'contract-detail')
        footer = self.bbox('legend-footer')
        bottom = footer[1] - 10 if footer else h - 18
        lines = [(f"{info['source_label']} → {info['target_label']}", p.GOLD_SOFT, 'Segoe UI Semibold'),
                 (info['contract'], p.WHITE, 'Segoe UI'),
                 (f"Checks: {info['source_check']['state']} → {info['target_check']['state']}", p.MUTED, 'Segoe UI'),
                 ('Declared contract · I/O inspect links · Enter follows · Esc clears', p.MUTED, 'Segoe UI')]
        items, y = [], 0
        for text, colour, font in lines:
            item = self.create_text(26, y, anchor='nw', text=text, width=width - 20, fill=colour,
                                    font=(font, 8), tags=tags)
            items.append(item)
            y = self.bbox(item)[3] + 4
        top = max(12, bottom - y - 8)
        for item in items:
            self.move(item, 0, top + 7)
        panel = rounded(self, (16, top, 16 + width, top + y + 8), radius=10,
                        fill=blend(p.PANEL, p.BLACK, .52), outline=blend(p.PANEL, p.GOLD, .62), tags=tags)
        self.tag_lower(panel, items[0])

    def _draw_label(self, key: str, w: float) -> None:
        from app.gui_theme import owned_font

        p = self.palette
        x, y, z = self._positions[key]
        title = dict(self.VIEWS)[key]
        detail = self.DESCRIPTIONS[key]
        check = self.checks.get(key)
        state = ("Historical check not recorded" if self.moment is not None else
                 "Not checked" if check is None else "Ready" if check.get("passed") else "Needs attention")
        if not hasattr(self, "_measure_font"):
            self._measure_font = owned_font(self, family="Segoe UI", size=8)
        face = self._measure_font
        while face.measure(detail) > max(80, w - 36):
            detail = detail[:-2].rstrip("…") + "…"
        width = min(w - 12, max(face.measure(title), face.measure(detail), face.measure(state)) + 24)
        left = max(6, min(w - width - 6, x - width / 2))
        top = max(24, y - self._radius(z) - 70)
        rounded(self, (left, top, left + width, top + 59), radius=10, tags=("scene", "label"),
                fill=blend(p.PANEL, p.BLACK, .4), outline=blend(p.PANEL, p.GOLD, .7))
        self.create_text(left + 11, top + 7, anchor="nw", text=title,
                         fill=p.WHITE, font=("Segoe UI Semibold", 9), tags=("scene", "label"))
        self.create_text(left + 11, top + 26, anchor="nw", text=detail,
                         fill=p.MUTED, font=("Segoe UI", 8), tags=("scene", "label"))
        self.create_text(left + 11, top + 44, anchor="nw", text=state,
                         fill=self._node_colour(key), font=("Segoe UI", 8), tags=("scene", "label"))

    def _draw_layers(self, w: float, h: float, taken: list[tuple[float, float, float, float]]) -> None:
        """Name each region of the brain where its parts sit, turning with it.

        Dots alone meant nothing without the README open. The layer names
        (interaction, reasoning, control, knowledge...) say what each part of
        the brain is for; they fade with depth like everything else.
        """
        p = self.palette
        for layer in dict.fromkeys(LAYERS.values()):
            members = [LAYOUT[key] for key, value in LAYERS.items() if value == layer and key in self._positions]
            if not members:
                continue
            centre = tuple(sum(point[axis] for point in members) / len(members) for axis in range(3))
            x, y, z = self._point(centre, w, h)
            near = max(0.0, min(1.0, (z + 1.1) / 2.2))
            # A part's own name matters more than its region's: the region
            # label moves above or below, and gives way if both collide.
            half = self._layer_font.measure(layer.upper()) / 2 + 2
            tall = self._layer_font.metrics("linespace") / 2
            # Above, below, then to either side, nearest first.
            for dx, dy in ((0, -18), (0, 18), (0, -32), (0, 32), (-half - 14, 0), (half + 14, 0),
                           (-half - 14, -16), (half + 14, -16), (-half - 14, 16), (half + 14, 16), (0, -46), (0, 46)):
                box = (x + dx - half, y + dy - tall, x + dx + half, y + dy + tall)
                inside = 4 <= box[0] and box[2] <= w - 4 and 24 <= box[1] and box[3] <= h - 20
                if inside and not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3]
                                      for b in taken):
                    taken.append(box)
                    self.create_text(x + dx, y + dy, text=layer.upper(), font=self._layer_font,
                                     tags=("scene", "layer"), fill=blend(p.PANEL, p.GOLD_SOFT, .25 + .45 * near))
                    break

    @staticmethod
    def _overlaps(box: tuple[float, float, float, float], others: list[tuple[float, float, float, float]]) -> bool:
        return any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in others)

    def _draw_names(self, w: float = 0.0, h: float = 0.0) -> list[tuple[float, float, float, float]]:
        """Chosen and nearest names first, each beside its own part.

        A name tries below its part, then above, right and left, and takes
        the first spot that covers no other part and no placed name. Only a
        name with no free spot at all is left out; hovering still shows it.
        """
        p = self.palette
        # Measured, not estimated: at 125% display scaling the same 8pt text
        # is a quarter wider, and a per-character guess let names collide.
        from app.gui_theme import owned_font

        if not hasattr(self, "_measure_font"):
            self._measure_font = owned_font(self, family="Segoe UI", size=8)
        face = self._measure_font
        tall = face.metrics("linespace") / 2 + 1
        placed: list[tuple[float, float, float, float]] = []
        if not hasattr(self, "_layer_font"):
            self._layer_font = owned_font(self, family="Segoe UI Semibold", size=7)
        discs = getattr(self, "_discs", [])
        for z, x, y, r, label, chosen in sorted(self._names, key=lambda row: (not row[5], -row[0])):
            half = face.measure(label) / 2 + 3
            own = (x - r - 3, y - r - 3, x + r + 3, y + r + 3)
            others = [disc for disc in discs if disc != own]
            for dx, dy in ((0, r + 9), (0, -r - 9), (r + 6 + half, 0), (-r - 6 - half, 0)):
                box = (x + dx - half, y + dy - tall, x + dx + half, y + dy + tall)
                inside = not w or (2 <= box[0] and box[2] <= w - 2 and 20 <= box[1] and box[3] <= h - 18)
                if (inside and not self._overlaps(box, placed) and not self._overlaps(box, others)
                        and not self._overlaps(box, getattr(self, "_legend_boxes", []))):
                    break
            else:
                continue
            placed.append(box)
            near = max(0.0, min(1.0, (z + 1.1) / 2.2))
            rounded(self, box, radius=4, fill=blend(p.PANEL, p.BLACK, .18),
                    outline=blend(p.PANEL, p.GOLD_SOFT, .55) if chosen else "",
                    tags=("scene", "name-background"))
            self.create_text(x + dx, y + dy, text=label, tags=("scene", "name"), font=("Segoe UI", 8),
                             fill=p.GOLD_SOFT if chosen else blend(p.PANEL, p.WHITE, .72 + .23 * near))
        return placed

    def _draw_pulses(self, w: float, h: float) -> None:
        """A dot per flowing edge, travelling source to target."""
        if not self._edges:
            return
        p = self.palette
        t = self._clock() * self.PULSE_SPEED if self.motion else .5
        for index, (source, target) in enumerate(self._edges):
            if not self._visible_edge(source, target):
                continue
            a, b = self._positions[source], self._positions[target]
            f = (t + index * .37) % 1.0
            curve = contract_curve(a, b, self._radius(a[2]) * .8 + 10, self._radius(b[2]) * .8 + 12)
            x, y = curve_point(curve, f)
            self.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, fill=p.WHITE,
                             outline=p.HOLO, width=2, tags=("scene", "flow"))

    def _draw_legend(self, w: float, h: float) -> None:
        p = self.palette
        from app.gui_theme import owned_font

        if not hasattr(self, "_legend_font"):
            self._legend_font = owned_font(self, family="Segoe UI", size=8)
        header = ("scene", "legend", "legend-header")
        footer = ("scene", "legend", "legend-footer")
        compact = w < 720
        available = max(60, w - 24)
        title = "BRAIN / 5D · space + time + evidence" if self.view_mode == "5D" else "BRAIN / 3D · live architecture"
        title_item = self.create_text(12, 12, text=title, anchor="nw", fill=p.MUTED,
                         width=available if compact else w / 2 - 24,
                         font=("Segoe UI Semibold", 8), tags=header)
        next_y = self.bbox(title_item)[3] + 4
        words = {"model": "reasoning", "tool": "using a tool", "research": "researching",
                 "learning": "learning", "hearing": "hearing you", "speaking": "speaking",
                 "seeing": "looking"}
        doing = ", ".join(words[flow] for flow in self.flows)
        status = ""
        if self.moment is not None:
            when = time.strftime("%H:%M:%S", time.localtime(self.moment))
            status = f"At {when}: {doing or 'idle'}"
        elif self.flows:
            status = "Active: " + doing
        if status:
            item = self.create_text(12 if compact else w - 12, next_y if compact else 12,
                                    anchor="nw" if compact else "ne", justify="left" if compact else "right",
                                    width=available if compact else w / 2 - 24,
                                    fill=p.GOLD if self.moment is not None else p.HOLO,
                                    font=("Segoe UI Semibold", 8), text=status, tags=(*header, "activity-caption"))
            next_y = max(next_y, self.bbox(item)[3] + 4)
        if self.asset_loaded and self.surface != "Hidden":
            from app.brain_asset import MODEL_CREDIT
            item = self.create_text(12, next_y, text=MODEL_CREDIT, anchor="nw", fill=p.MUTED,
                             font=("Segoe UI", 8), width=available, tags=(*header, "model-credit"))
            next_y = self.bbox(item)[3] + 4
        elif self.asset_problem:
            item = self.create_text(12, next_y, text=self.asset_problem, anchor="nw", fill=p.GOLD_SOFT,
                             font=("Segoe UI", 8), width=available, tags=(*header, "model-credit"))
            next_y = self.bbox(item)[3] + 4
        if self.surface == "Standing waves":
            self.create_text(12, next_y, anchor="nw", fill=p.GOLD_SOFT, font=("Segoe UI", 8),
                             tags=(*header, "wave-caption"), width=available,
                             text="Cymatics illustration · not neural activity")
        if self._focused:
            self.create_text(12, h - 9, anchor="sw", fill=p.MUTED, font=("Segoe UI", 8), tags=footer,
                             width=available,
                             text="←/→ pick · I/O inspect links · F focus · H checks · P trace · Enter details · Esc clear")
            return
        rows = [[]]
        x = 12
        entries = [(p.OLIVE_SOFT, "ready"), (p.GOLD, "optional, check"),
                   (p.ALERT, "critical, check"), (p.ROYAL_SOFT, "not checked")]
        if self.moment is not None:
            entries = [(p.ROYAL_SOFT, 'checks not recorded at this moment')]
        visible_contracts = sum(self._visible_edge(edge.source, edge.target) for edge in BrainMesh.EDGES)
        entries.append((p.ROYAL_SOFT, f'{len(self.VIEWS)}/{len(BrainMesh.NODES)} subsystems · '
                                     f'{visible_contracts}/{len(BrainMesh.EDGES)} declared contracts'))
        if self.components and self.show_components:
            entries.append((p.HOLO, f"{len(self.components)} parts · small dots"))
        if self.population is not None and self.show_components:
            counts = self.population.counts()
            stored, model = counts.get("stored_units", counts["population"]), counts.get("model_neurons", 0)
            entries.append((p.GOLD_SOFT, f"{counts['total']:,} neurons: {counts['base']:,} parts · "
                                         f"{stored:,} stored memories, facts and experiences"
                                         + (f" · {model:,} language-model neurons (from the model files)" if model else "")
                                         + f" · {counts['synapses']:,} synapses"
                                         + (f" · {counts['short_by']:,} short of {counts['target']:,}" if counts["short_by"]
                                            else f" · target {counts['target']:,} reached")))
        for colour, text in entries:
            width = self._legend_font.measure(text) + 24
            if x + width > w - 12 and rows[-1]:
                rows.append([])
                x = 12
            rows[-1].append((x, colour, text))
            x += width
        line_height = self._legend_font.metrics("linespace") + 5
        for index, row in enumerate(rows):
            y = h - 12 - (len(rows) - index - 1) * line_height
            for x, colour, text in row:
                self.create_oval(x, y - 3.5, x + 7, y + 3.5, fill=colour, outline="", tags=footer)
                self.create_text(x + 11, y, anchor="w", text=text, fill=p.MUTED,
                                 font=self._legend_font, tags=footer)

    # -------------------------------------------------------- animation

    def _overlay(self, *, redrawn: bool) -> bool:
        # Pulses move every frame; the mesh only when the camera does.
        moving = bool(self._edges) or bool(self._active_components and self._component_positions)
        if moving and not redrawn:
            self.delete("flow")
            self.delete("component-flow")
            self._draw_pulses(*self._size())
            self._draw_component_pulses()
            self.tag_raise("label")
        return moving


class BrainExplorer(tk.Frame):
    """Accessible filters and a copyable contract inspector attached to an existing graph."""

    def __init__(self, parent: tk.Misc, graph: BrainGraph, palette: Any, **kwargs: Any) -> None:
        from tkinter import ttk
        from app.gui_panels import ScrollableColumn
        from app.gui_resources import BooleanVar, StringVar

        super().__init__(parent, bg=palette.PANEL, padx=10, pady=10, **kwargs)
        self.graph, self.palette = graph, palette
        self._labels = {node.node_id: node.label for node in BrainMesh.NODES}
        self._ids = {label: key for key, label in self._labels.items()}
        self._pairs: dict[str, tuple[str, str]] = {}
        self.layer = StringVar(self, graph.layer_filter)
        self.health = StringVar(self, graph.health_filter)
        self.direction = StringVar(self, 'Both')
        self.isolate = BooleanVar(self, graph.path_only)
        self.focus_links = BooleanVar(self, graph.focus_connections)
        self.source = StringVar(self, self._labels['interface'])
        self.target = StringVar(self, self._labels['memory'])
        tk.Label(self, text='Contract explorer', bg=palette.PANEL, fg=palette.WHITE,
                 font=('Segoe UI Semibold', 12), anchor='w').pack(fill='x')
        self.summary = tk.Label(self, bg=palette.PANEL, fg=palette.MUTED, anchor='w', justify='left',
                                wraplength=420, font=('Segoe UI', 8))
        self.summary.pack(fill='x', pady=(3, 8))
        # The Operator mounts this inspector in roughly 300 px. Keep the
        # evidence readable while its less frequent view/trace controls scroll
        # in their own tab, rather than packing them above a one-pixel result.
        self.tabs = ttk.Notebook(self, style='Side.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        inspection = tk.Frame(self.tabs, bg=palette.PANEL)
        inspection.columnconfigure(0, weight=1)
        inspection.rowconfigure(1, weight=1, minsize=60)
        self.settings = ScrollableColumn(self.tabs, palette, width=270)
        self.settings.body.configure(bg=palette.PANEL)
        self.settings.canvas.configure(bg=palette.PANEL)
        self.tabs.add(inspection, text='Connections')
        self.evidence_page = tk.Frame(self.tabs, bg=palette.PANEL)
        self.tabs.add(self.evidence_page, text='Evidence')
        self.tabs.add(self.settings, text='View & trace')
        filters = tk.Frame(self.settings.body, bg=palette.PANEL)
        filters.pack(fill='x')
        for index, (label, variable, values, command) in enumerate([
                ('Layer', self.layer, ('All layers', *dict.fromkeys(LAYERS.values())), lambda: graph.set_layer(self.layer.get())),
                ('Checks', self.health, graph.HEALTH_FILTERS, lambda: graph.set_health_filter(self.health.get()))]):
            tk.Label(filters, text=label, bg=palette.PANEL, fg=palette.MUTED, anchor='w',
                     font=('Segoe UI', 8)).grid(row=index * 2, column=0, sticky='w')
            picker = ttk.Combobox(filters, textvariable=variable, values=values, state='readonly', width=18)
            picker.grid(row=index * 2 + 1, column=0, sticky='ew', pady=(0, 6))
            picker.bind('<<ComboboxSelected>>', lambda _event, command=command: command())
            filters.columnconfigure(0, weight=1)
        actions = tk.Frame(self.settings.body, bg=palette.PANEL)
        actions.pack(fill='x', pady=(8, 8))
        ttk.Checkbutton(actions, text='Focused connections', variable=self.focus_links,
                        command=self._set_focus).pack(anchor='w')
        ttk.Checkbutton(actions, text='Trace only', variable=self.isolate,
                        command=lambda: graph.set_path_only(self.isolate.get())).pack(anchor='w')
        route = tk.Frame(self.settings.body, bg=palette.PANEL)
        route.pack(fill='x')
        for index, variable in enumerate((self.source, self.target)):
            tk.Label(route, text='From' if index == 0 else 'To', bg=palette.PANEL, fg=palette.MUTED,
                     font=('Segoe UI', 8), anchor='w').grid(row=index * 2, column=0, columnspan=2, sticky='w')
            picker = ttk.Combobox(route, textvariable=variable, values=tuple(self._ids), state='readonly', width=15)
            picker.grid(row=index * 2 + 1, column=0, columnspan=2, sticky='ew', pady=(0, 6))
            route.columnconfigure(index, weight=1)
        ttk.Button(route, text='Trace', style='HayatAccent.TButton', command=self.trace).grid(row=4, column=0, sticky='ew', pady=(5, 0), padx=(0, 6))
        ttk.Button(route, text='Clear', style='Hayat.TButton', command=self.clear).grid(row=4, column=1, sticky='ew', pady=(5, 0))
        self.route_note = tk.Label(self.settings.body, bg=palette.PANEL, fg=palette.GOLD_SOFT, anchor='w', justify='left',
                                  wraplength=420, font=('Segoe UI', 8))
        self.route_note.pack(fill='x', pady=(5, 6))
        choice = tk.Frame(inspection, bg=palette.PANEL)
        choice.grid(row=0, column=0, sticky='ew')
        choice.columnconfigure(0, weight=1)
        self.link_title = tk.Label(choice, bg=palette.PANEL, fg=palette.WHITE, font=('Segoe UI Semibold', 9), anchor='w')
        self.link_title.grid(row=0, column=0, sticky='ew')
        picker = ttk.Combobox(choice, textvariable=self.direction, values=('Both', 'Incoming', 'Outgoing'), state='readonly', width=10)
        picker.grid(row=0, column=1, sticky='e')
        picker.bind('<<ComboboxSelected>>', lambda _event: self.refresh())
        table = tk.Frame(inspection, bg=palette.PANEL)
        table.grid(row=1, column=0, sticky='nsew', pady=(4, 6))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.links = ttk.Treeview(table, columns=('contract',), show='tree headings', selectmode='browse', height=4)
        self.links.heading('#0', text='Directed connection')
        self.links.heading('contract', text='Contract')
        self.links.column('#0', width=120, minwidth=90)
        self.links.column('contract', width=150, minwidth=90)
        self.links.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.links.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.links.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.links.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.links.bind('<<TreeviewSelect>>', self._selected)
        self.links.bind('<Return>', self._selected)
        evidence = tk.Frame(self.evidence_page, bg=palette.PANEL)
        evidence.pack(fill='both', expand=True)
        self.detail = tk.Text(evidence, bg=palette.BLACK, fg=palette.WHITE, font=('Segoe UI', 9), wrap='word',
                              height=8, width=34, padx=9, pady=8, relief='flat', takefocus=True)
        scroll = ttk.Scrollbar(evidence, orient='vertical', command=self.detail.yview)
        self.detail.configure(yscrollcommand=scroll.set)
        self.detail.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self._binding = graph.bind('<<BrainInspectionChanged>>', lambda _event: self.refresh(), add='+')
        self.bind('<Map>', lambda event: self.refresh() if event.widget is self else None, add='+')
        self.bind('<Configure>', self._fit_explorer)
        self.bind('<Destroy>', self._dispose, add='+')
        self.settings.adopt(self.settings.body)
        self.refresh()

    def _fit_explorer(self, event: Any) -> None:
        if event.widget is not self:
            return
        self.summary.configure(wraplength=max(180, event.width - 24))
        self.route_note.configure(wraplength=max(180, event.width - 45))
        self.link_title.configure(wraplength=max(75, event.width - 170))
        if event.height < 420:
            self.summary.pack_forget()
        elif not self.summary.winfo_manager():
            self.summary.pack(fill='x', pady=(3, 8), before=self.tabs)

    def _dispose(self, event: Any) -> None:
        if event.widget is self and self.graph.winfo_exists():
            self.graph.unbind('<<BrainInspectionChanged>>', self._binding)

    def _set_focus(self) -> None:
        self.graph.focus_connections = self.focus_links.get()
        self.graph.redraw()
        self.graph._announce_inspection()

    def trace(self) -> None:
        graph = self.graph
        chain = graph.trace_path(self._ids[self.source.get()], self._ids[self.target.get()])
        self.route_note.configure(text=' → '.join(self._labels[key] for key in chain) if chain else
                                  'No directed contract path exists between these subsystems.')
        graph._announce_inspection()

    def clear(self) -> None:
        self.graph.clear_path()
        self.graph.contract_active = self.graph.contract_hover = None
        self.graph.redraw()
        self.route_note.configure(text='')
        self.graph._announce_inspection()

    def _selected(self, _event: Any = None) -> str:
        selection = self.links.selection()
        pair = self._pairs.get(selection[0]) if selection else None
        if pair is not None and pair != self.graph.contract_active:
            self.graph.select_contract(*pair)
        return 'break'

    def refresh(self) -> None:
        graph = self.graph
        self.layer.set(graph.layer_filter)
        self.health.set(graph.health_filter)
        self.isolate.set(graph.path_only)
        self.focus_links.set(graph.focus_connections)
        focus = graph._hover or graph.active
        visible = [(edge.source, edge.target) for edge in BrainMesh.EDGES if graph._visible_edge(edge.source, edge.target)]
        pairs = graph.linked_contracts(focus, self.direction.get().lower()) if focus in LAYERS else visible
        self.summary.configure(text=f'{len(graph.VIEWS)} of {len(BrainMesh.NODES)} subsystems · '
                               f'{len(visible)} of {len(BrainMesh.EDGES)} declared contracts visible\n'
                               + ('Historical view: current checks are hidden.' if graph.moment is not None else
                                  'Checks show endpoint readiness; links show declared wiring.'))
        self.link_title.configure(text=self._labels.get(focus, 'Visible contracts'))
        self._pairs = {source + '|' + target: (source, target) for source, target in pairs}
        for iid in set(self.links.get_children()) - set(self._pairs):
            self.links.delete(iid)
        for iid, pair in self._pairs.items():
            info = graph.contract_detail(*pair)
            options = {'text': f"{info['source_label']} → {info['target_label']}", 'values': (info['contract'],)}
            if self.links.exists(iid):
                self.links.item(iid, **options)
            else:
                self.links.insert('', 'end', iid=iid, **options)
        pair = graph.contract_active
        if pair is not None:
            if pair != getattr(self, '_shown_pair', None):
                self.tabs.select(self.evidence_page)
            iid = '|'.join(pair)
            if iid in self._pairs and self.links.selection() != (iid,):
                self.links.selection_set(iid)
                self.links.see(iid)
            info = graph.contract_detail(*pair)
            lines = [f"{info['source_label']} → {info['target_label']}", info['contract'], '']
            for key in ('source', 'target'):
                check = info[key + '_check']
                lines.append(f"{info[key + '_label']}: {check['state']} · checked {check['checked_at'] or 'time not recorded'}")
                if check['detail']:
                    lines.append(check['detail'])
                lines.extend('• ' + str(gap) for gap in check['gaps'])
            lines.extend(['', info['scope']])
            text = '\n'.join(lines)
        else:
            text = ('Choose a connection to inspect its directed contract and endpoint evidence.\n\n'
                    'In the 3D view: drag to rotate, I/O inspect incoming/outgoing links, P traces a route, '
                    'F focuses a neighborhood, H cycles check filters, and Escape clears inspection.\n\n'
                    'Every link here comes from the real BrainMesh topology. A visible link does not establish '
                    'that a request traversed it or that the model fired a neuron.')
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        from app.gui_presentation import safe_text
        self.detail.insert('1.0', safe_text(text))
        self.detail.configure(state='disabled')
        self._shown_pair = pair


class BrainTimeline(tk.Canvas):
    """The fourth dimension of the brain view: lanes over time, and a playhead.

    Each lane is one flow; each bar a recorded span of it. The playhead shows
    the 3D brain as it was at any moment. The view can be zoomed and panned
    inside the loaded window; the right edge of an unzoomed view is "now",
    where the brain follows live activity again.
    """

    LANES = (("hearing", "Hearing"), ("seeing", "Seeing"), ("model", "Reasoning"), ("tool", "Tools"),
             ("research", "Research"), ("learning", "Learning"))
    LEFT, TOP, LANE, GAP = 78, 18, 13, 4
    #: Within this many pixels of the right edge counts as "now".
    LIVE_SNAP = 6
    #: The narrowest view, in seconds: a zoom can reach one burst of work.
    MIN_VIEW = 20.0
    #: Replay speeds: how long one pass through the view takes, in seconds.
    SPEEDS = (("Slow", 30.0), ("Normal", 12.0), ("Fast", 4.0))

    def __init__(self, parent: tk.Misc, palette: Any, on_moment: Callable[[float | None], None],
                 on_replay: Callable[[bool], None] | None = None) -> None:
        height = self.TOP + len(self.LANES) * (self.LANE + self.GAP) + 30
        super().__init__(parent, height=height, bg=palette.PANEL, bd=0, highlightthickness=1,
                         highlightbackground=palette.LINE, highlightcolor=palette.GOLD,
                         takefocus=True, cursor="sb_h_double_arrow")
        self.palette, self.on_moment, self.on_replay = palette, on_moment, on_replay
        self.history: Any = None
        self.moment: float | None = None
        #: The visible slice of the loaded window; None means all of it.
        self.view: tuple[float, float] | None = None
        self.speed = 1                    # index into SPEEDS
        self._replay: str | None = None
        self._replay_at: float | None = None
        self._pan_from: tuple[float, tuple[float, float]] | None = None
        #: One part's history: its flows stay bright, the rest step back.
        self.focus_node: str | None = None
        self.focus_label = ""
        self.focus_flows: set[str] | None = None
        self._tip: tuple[float, float] | None = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<ButtonPress-1>", self._scrub)
        self.bind("<B1-Motion>", self._scrub)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<ButtonPress-3>", self._pan_start)
        self.bind("<B3-Motion>", self._pan)
        self.bind("<Shift-ButtonPress-1>", self._pan_start)
        self.bind("<Shift-B1-Motion>", self._pan)
        self.bind("<ButtonRelease-3>", self._pan_end)
        self.bind("<Shift-ButtonRelease-1>", self._pan_end)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Shift-MouseWheel>", lambda event: self._pan_by(-1 if event.delta > 0 else 1))
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _event: self._hide_tip())
        self.bind("<Left>", lambda _event: self._step(-1))
        self.bind("<Right>", lambda _event: self._step(1))
        self.bind("<Shift-Left>", lambda _event: self._step(-6))
        self.bind("<Shift-Right>", lambda _event: self._step(6))
        self.bind("<bracketleft>", lambda _event: self.jump_mark(-1))
        self.bind("<bracketright>", lambda _event: self.jump_mark(1))
        self.bind("<Home>", lambda _event: (self.seek(self._view()[0]), "break")[1])
        self.bind("<End>", lambda _event: (self.seek(None), "break")[1])
        self.bind("<space>", lambda _event: (self.toggle_replay(), "break")[1])
        for sequence in ("<plus>", "<equal>", "<KP_Add>"):
            self.bind(sequence, lambda _event: (self.zoom(1 / 1.5), "break")[1])
        for sequence in ("<minus>", "<KP_Subtract>"):
            self.bind(sequence, lambda _event: (self.zoom(1.5), "break")[1])
        self.bind("<Key-0>", lambda _event: (self.fit(), "break")[1])
        self.bind("<Destroy>", lambda _event: self.stop_replay())

    def colours(self) -> dict[str, str]:
        p = self.palette
        return {"hearing": getattr(p, "HOLO", p.ROYAL_SOFT), "model": p.GOLD_SOFT, "tool": p.OLIVE_SOFT,
                "research": p.ROYAL_SOFT, "learning": blend(p.ROYAL_SOFT, p.WHITE, .45),
                "seeing": blend(p.GOLD, p.ALERT, .45)}

    # ------------------------------------------------------------ inputs

    def set_history(self, history: Any) -> None:
        """Take a freshly loaded window, keeping a zoomed view where it was."""
        self.history = history
        if self.view is not None and history is not None:
            begin, finish = self.view
            begin, finish = max(history.start, begin), min(history.end, finish)
            self.view = (begin, finish) if finish - begin >= self.MIN_VIEW else None
        self.redraw()

    def set_focus(self, node: str | None, label: str = "", flows: set[str] | None = None) -> None:
        """Brighten one part's own activity; None shows everything again."""
        self.focus_node, self.focus_label = node, label
        self.focus_flows = None if node is None else set(flows or ())
        self.redraw()

    def seek(self, moment: float | None) -> None:
        """Put the playhead at a moment, or back on "now" with None."""
        if moment is not None and self.history is not None:
            moment = max(self.history.start, min(self.history.end, moment))
            begin, finish = self._view()
            if not begin <= moment <= finish:
                # Keep the playhead in view: slide the view to it.
                width = finish - begin
                begin = max(self.history.start, min(self.history.end - width, moment - width / 2))
                self.view = (begin, begin + width)
        self.moment = moment
        self.redraw()
        self.on_moment(moment)

    # ------------------------------------------------------------ view

    def _view(self) -> tuple[float, float]:
        if self.history is None:
            return (0.0, 1.0)
        return self.view or (self.history.start, self.history.end)

    @property
    def zoomed(self) -> bool:
        return self.view is not None

    def _set_view(self, begin: float, finish: float) -> None:
        history = self.history
        width = max(self.MIN_VIEW, finish - begin)
        full = history.end - history.start
        if width >= full:
            self.view = None
        else:
            begin = max(history.start, min(history.end - width, begin))
            self.view = (begin, begin + width)
        self.redraw()

    def zoom(self, factor: float, around: float | None = None) -> None:
        """Scale the view by ``factor`` (below 1 zooms in), keeping ``around`` still."""
        if self.history is None:
            return
        begin, finish = self._view()
        anchor = around if around is not None else (self.moment if self.moment is not None
                                                    else (begin + finish) / 2)
        anchor = max(begin, min(finish, anchor))
        self._set_view(anchor - (anchor - begin) * factor, anchor + (finish - anchor) * factor)

    def fit(self) -> None:
        self.view = None
        self.redraw()

    def zoom_to(self, begin: float, finish: float) -> None:
        pad = max(2.0, (finish - begin) * .15)
        self._set_view(begin - pad, finish + pad)

    def _pan_by(self, direction: int) -> str:
        if self.history is not None and self.zoomed:
            begin, finish = self._view()
            shift = (finish - begin) * .15 * direction
            self._set_view(begin + shift, finish + shift)
        return "break"

    # ------------------------------------------------------------ replay

    @property
    def replaying(self) -> bool:
        return self._replay is not None

    def replay(self, seconds: float | None = None) -> None:
        """Play the view through the brain from its start, then go live."""
        self.stop_replay()
        if self.history is None:
            return
        self._replay_at = self._view()[0]
        self._run_replay(seconds)

    def toggle_replay(self) -> None:
        """Pause a running replay, resume a paused one, or start one."""
        if self.replaying:
            self.stop_replay(keep_position=True)
        elif self._replay_at is not None and self.history is not None:
            self._run_replay(None)
        else:
            self.replay()

    def _run_replay(self, seconds: float | None) -> None:
        begin, finish = self._view()
        duration = seconds if seconds is not None else self.SPEEDS[self.speed][1]
        step = max(1e-3, (finish - begin) / max(1.0, duration * 20))

        def advance() -> None:
            moment = self._replay_at
            if self.history is None or moment is None or moment >= finish:
                self._replay, self._replay_at = None, None
                self._announce()
                self.seek(None if finish >= self.history.end - 1e-6 else finish)
                return
            self.seek(moment)
            self._replay_at = moment + step
            self._replay = self.after(50, advance)

        self._replay = "starting"
        self._announce()
        advance()

    def stop_replay(self, keep_position: bool = False) -> None:
        handle, self._replay = self._replay, None
        if handle not in (None, "starting"):
            try:
                self.after_cancel(handle)
            except tk.TclError:
                pass
        had_pause = self._replay_at is not None
        if not keep_position:
            self._replay_at = None
        # Stopping a paused replay changes the state too (Resume -> Replay).
        if handle is not None or (had_pause and not keep_position):
            self._announce()

    def cycle_speed(self) -> str:
        self.speed = (self.speed + 1) % len(self.SPEEDS)
        if self.replaying:
            self.stop_replay(keep_position=True)
            self._run_replay(None)
        return self.SPEEDS[self.speed][0]

    def _announce(self) -> None:
        if self.on_replay is not None:
            try:
                self.on_replay(self.replaying)
            except tk.TclError:
                pass

    # ------------------------------------------------------------ marks

    def jump_mark(self, direction: int) -> str:
        """Move the playhead to the previous or next failure or authority mark."""
        if self.history is None or not self.history.marks:
            return "break"
        current = self.moment if self.moment is not None else self.history.end
        times = sorted({mark.at for mark in self.history.marks})
        if direction < 0:
            earlier = [at for at in times if at < current - 1e-6]
            target = earlier[-1] if earlier else None
        else:
            later = [at for at in times if at > current + 1e-6]
            target = later[0] if later else None
        if target is not None:
            self.stop_replay()
            self.seek(target)
        return "break"

    # ------------------------------------------------------------ mapping

    def _usable(self) -> float:
        return max(1.0, max(self.LEFT + 40, self.winfo_width()) - self.LEFT - 10)

    def _x(self, moment: float) -> float:
        begin, finish = self._view()
        return self.LEFT + (moment - begin) / max(1e-6, finish - begin) * self._usable()

    def _time(self, x: float) -> float | None:
        """The moment under ``x``; None for "now" at the right edge of a live view."""
        if self.history is None:
            return None
        begin, finish = self._view()
        at_edge = x >= self.LEFT + self._usable() - self.LIVE_SNAP
        if at_edge and finish >= self.history.end - 1e-6:
            return None
        fraction = max(0.0, min(1.0, (x - self.LEFT) / self._usable()))
        return begin + fraction * (finish - begin)

    def _lane_at(self, y: float) -> str | None:
        for index, (flow, _title) in enumerate(self.LANES):
            top = self.TOP + index * (self.LANE + self.GAP)
            if top <= y <= top + self.LANE:
                return flow
        return None

    def _mark_row(self) -> float:
        return self.TOP + len(self.LANES) * (self.LANE + self.GAP)

    def item_at(self, x: float, y: float) -> Any:
        """The span or mark under the pointer, if any."""
        if self.history is None or x < self.LEFT:
            return None
        begin, finish = self._view()
        per_pixel = (finish - begin) / self._usable()
        moment = begin + (x - self.LEFT) * per_pixel
        row = self._mark_row()
        if row <= y <= row + 9:
            near = [mark for mark in self.history.marks if abs(mark.at - moment) <= per_pixel * 5]
            return min(near, key=lambda mark: abs(mark.at - moment)) if near else None
        flow = self._lane_at(y)
        return self.history.span_at(flow, moment, per_pixel * 2) if flow else None

    # ------------------------------------------------------------ pointer

    def _scrub(self, event: Any) -> str:
        self.focus_set()
        self.stop_replay()
        self.seek(self._time(float(event.x)))
        return "break"

    def _step(self, direction: int) -> str:
        """Arrow keys: one sixtieth of the view per press (Shift: a tenth)."""
        if self.history is None:
            return "break"
        self.stop_replay()
        begin, finish = self._view()
        current = self.moment if self.moment is not None else self.history.end
        target = current + direction * (finish - begin) / 60
        self.seek(None if target >= self.history.end else target)
        return "break"

    def _double(self, event: Any) -> str:
        item = self.item_at(float(event.x), float(event.y))
        if item is not None and hasattr(item, "flow"):
            self.zoom_to(item.start, item.end)
            self.seek(item.start)
        else:
            self.fit()
        return "break"

    def _pan_start(self, event: Any) -> str:
        if self.history is not None:
            self._pan_from = (float(event.x), self._view())
            self.configure(cursor="fleur")
        return "break"

    def _pan(self, event: Any) -> str:
        if self._pan_from is None or self.history is None:
            return "break"
        origin, (begin, finish) = self._pan_from
        shift = (origin - float(event.x)) / self._usable() * (finish - begin)
        if self.view is not None:
            self._set_view(begin + shift, finish + shift)
        return "break"

    def _wheel(self, event: Any) -> str:
        if self.history is not None and getattr(event, "delta", 0):
            begin, finish = self._view()
            fraction = max(0.0, min(1.0, (float(event.x) - self.LEFT) / self._usable()))
            self.zoom(1 / 1.35 if event.delta > 0 else 1.35, begin + fraction * (finish - begin))
        return "break"

    def _pan_end(self, _event: Any = None) -> str:
        self._pan_from = None
        self.configure(cursor="sb_h_double_arrow")
        return "break"

    def _hover(self, event: Any) -> None:
        self._tip = (float(event.x), float(event.y))
        self._draw_tip()

    def _hide_tip(self) -> None:
        self._tip = None
        self.delete("tip")

    def tip_text(self, item: Any) -> str:
        if item is None:
            return ""
        clock = lambda at: time.strftime("%H:%M:%S", time.localtime(at))
        if hasattr(item, "flow"):
            seconds = item.end - item.start
            length = f"{seconds:.1f} s" if seconds < 90 else f"{seconds / 60:.1f} min"
            ending = "end not recorded" if getattr(item, "open", False) else length
            return f"{item.label} · {clock(item.start)} · {ending}"
        return f"{clock(item.at)} · {item.label}"

    # ------------------------------------------------------------ drawing

    def _draw_tip(self) -> None:
        self.delete("tip")
        if self._tip is None:
            return
        text = self.tip_text(self.item_at(*self._tip))
        if not text:
            return
        p = self.palette
        x, y = self._tip
        label = self.create_text(0, 0, text=text, anchor="nw", fill=p.WHITE, font=("Segoe UI", 8), tags="tip")
        left, top, right, bottom = self.bbox(label)
        width, height = right - left + 12, bottom - top + 6
        w = max(self.LEFT + 40, self.winfo_width())
        tx = max(4, min(w - width - 4, x + 12))
        ty = max(2, y - height - 6)
        self.coords(label, tx + 6, ty + 3)
        box = self.create_rectangle(tx, ty, tx + width, ty + height, fill=blend(p.PANEL, p.BLACK, .5),
                                    outline=p.GOLD, tags="tip")
        self.tag_lower(box, label)

    def redraw(self) -> None:
        p = self.palette
        self.configure(bg=p.PANEL, highlightbackground=p.LINE, highlightcolor=p.GOLD)
        self.delete("all")
        w = max(self.LEFT + 40, self.winfo_width())
        colours = self.colours()
        for index, (_flow, title) in enumerate(self.LANES):
            y = self.TOP + index * (self.LANE + self.GAP)
            self.create_text(self.LEFT - 8, y + self.LANE / 2, text=title, anchor="e", fill=p.MUTED,
                             font=("Segoe UI", 8))
            self.create_rectangle(self.LEFT, y, w - 10, y + self.LANE, fill=blend(p.PANEL, p.BLACK, .35),
                                  outline="")
        if self.history is None:
            self.create_text(self.LEFT, 3, anchor="nw", text="Loading history…", fill=p.MUTED,
                             font=("Segoe UI", 8))
            return
        begin, finish = self._view()
        lanes = {flow: index for index, (flow, _title) in enumerate(self.LANES)}
        for span in self.history.spans:
            if span.flow not in lanes or span.end < begin or span.start > finish:
                continue
            y = self.TOP + lanes[span.flow] * (self.LANE + self.GAP)
            x0 = max(self.LEFT, self._x(span.start))
            x1 = min(self.LEFT + self._usable(), self._x(span.end))
            colour = colours[span.flow]
            focused = self.focus_flows is None or span.flow in self.focus_flows
            if not focused:
                # Another part's work: still there, but stepped back.
                colour = blend(p.PANEL, colour, .22)
            tags = ("span", "focused") if focused and self.focus_flows is not None else ("span",)
            if getattr(span, "open", False):
                # Its end was never recorded: hatched, so an inferred or
                # unknown end is never mistaken for measured work.
                self.create_rectangle(x0, y + 2, max(x0 + 2, x1), y + self.LANE - 2, outline=colour,
                                      fill=colour, stipple="gray25", tags=(*tags, "open"))
            else:
                self.create_rectangle(x0, y + 1, max(x0 + 2, x1), y + self.LANE - 1, outline="",
                                      fill=colour, tags=tags)
        bottom = self._mark_row()
        for mark in self.history.marks:
            if not begin <= mark.at <= finish:
                continue
            x = self._x(mark.at)
            colour = (p.ALERT if mark.kind == "failure" else getattr(p, "HOLO", p.ROYAL_SOFT) if mark.kind == "look"
                      else p.GOLD)
            if self.focus_node is not None and mark.node != self.focus_node:
                colour = blend(p.PANEL, colour, .22)
            self.create_polygon(x, bottom + 1, x - 4, bottom + 8, x + 4, bottom + 8, fill=colour,
                                outline="", tags="mark")
        # The view's start, middle and end, as clock times; seconds once zoomed in.
        detail = "%H:%M:%S" if finish - begin < 3600 else "%H:%M"
        for fraction in (0, .5, 1):
            moment = begin + fraction * (finish - begin)
            anchor = "nw" if fraction == 0 else "ne" if fraction == 1 else "n"
            label = ("now" if fraction == 1 and finish >= self.history.end - 1e-6
                     else time.strftime(detail, time.localtime(moment)))
            self.create_text(self._x(moment), bottom + 9, anchor=anchor, text=label, fill=p.MUTED,
                             font=("Segoe UI", 8))
        if self.zoomed:
            # Where this view sits in the whole window.
            total = max(1e-6, self.history.end - self.history.start)
            left = self.LEFT + (begin - self.history.start) / total * self._usable()
            right = self.LEFT + (finish - self.history.start) / total * self._usable()
            self.create_line(self.LEFT, bottom + 25, self.LEFT + self._usable(), bottom + 25,
                             fill=blend(p.PANEL, p.LINE, .8), tags="overview")
            self.create_line(left, bottom + 25, max(left + 3, right), bottom + 25, width=3,
                             fill=p.GOLD, tags="overview")
        live = self.moment is None
        if live and finish >= self.history.end - 1e-6:
            playhead = self.LEFT + self._usable()
        elif live or not begin <= self.moment <= finish:
            playhead = None
        else:
            playhead = self._x(self.moment)
        if playhead is not None:
            self.create_line(playhead, 2, playhead, bottom + 8, width=2, tags="playhead",
                             fill=getattr(p, "HOLO", p.ROYAL_SOFT) if live else p.GOLD)
        heading = "LIVE" if live else time.strftime("%H:%M:%S", time.localtime(self.moment))
        if self.replaying:
            heading += " · replaying"
        elif self._replay_at is not None:
            heading += " · paused"
        if self.focus_node is not None:
            heading += f" · {self.focus_label} only"
        if self.zoomed:
            span = finish - begin
            heading += f" · zoom {span:.0f} s" if span < 120 else f" · zoom {span / 60:.0f} min"
        title = self.create_text(self.LEFT, 3, anchor="nw", text=f"TIME · {heading}", tags="heading",
                                 font=("Segoe UI Semibold", 8),
                                 fill=getattr(p, "HOLO", p.ROYAL_SOFT) if live else p.GOLD)
        hint = self.create_text(w - 12, 3, anchor="ne", fill=p.MUTED, font=("Segoe UI", 8), tags="hint",
                                text="drag: time · wheel: zoom · space: replay")
        # The heading grows while zoomed or replaying; the key hint gives way
        # rather than printing over it.
        if self.bbox(hint)[0] < self.bbox(title)[2] + 12:
            self.delete(hint)
        self._draw_tip()


#: One request's path through the brain, for the "How it works" tour.
TOUR = (
    ("interface", "intent"), ("intent", "router"), ("router", "planner"),
    ("planner", "authority"),
    ("authority", "tools"), ("tools", "verifier"), ("verifier", "memory"), ("verifier", "interface"),
)

#: Why each step exists, in plain words. The contract text is BrainMesh's own.
TOUR_WHY = {
    ("interface", "intent"): "Whatever you type or say arrives here first, from the window, the terminal or your voice.",
    ("intent", "router"): "HAYAT works out what kind of request it is before choosing how to handle it. Conversation replies also use Context Budget to size history and answer room.",
    ("router", "planner"): "The router picks the local model or fixed rule best suited, and the planner turns the request into steps.",
    ("planner", "authority"): "The planner consults Tool Judgment about purpose and past outcomes, then proposes a tool and exact arguments. Nothing runs yet.",
    ("authority", "tools"): "Only Authority can allow an action; the model cannot approve itself. Risky actions ask you first.",
    ("tools", "verifier"): "A tool reports what it observed. That is evidence, not proof of success.",
    ("verifier", "memory"): "The Truth Gate checks the real result. Only what it verified is kept as a memory.",
    ("verifier", "interface"): "The answer you see is built from what was verified, and says plainly when something failed.",
}


def tour_text(index: int) -> str:
    """The explanation for one step of the tour."""
    source, target = TOUR[index]
    labels = {node.node_id: node for node in BrainMesh.NODES}
    contract = next(edge.contract for edge in BrainMesh.EDGES if (edge.source, edge.target) == (source, target))
    return (f"How it works · step {index + 1} of {len(TOUR)}\n"
            f"{labels[source].label} → {labels[target].label}\n\n"
            f"{TOUR_WHY[(source, target)]}\n\n"
            f"Passes along: {contract}.\n"
            f"{labels[target].label}: {labels[target].responsibility}.")
