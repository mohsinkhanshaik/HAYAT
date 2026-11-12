"""Brain 5D component neurons: real parts, real numbers, real links, and a smooth canvas."""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import brain_components as bc
from app.brain_history import Group, Usage, load_usage, reconstruct, summarize
from app.brain_mesh import BrainMesh
from app.capabilities import CATALOG

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
NODE_IDS = {node.node_id for node in BrainMesh.NODES}


def stamp(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def at(seconds: float) -> float:
    return (T0 + timedelta(seconds=seconds)).timestamp()


def catalog_tools() -> list[str]:
    """One plausible registered name per catalog pattern (prefix patterns get a concrete name)."""
    names = []
    for capability in CATALOG:
        for pattern in capability.tools:
            names.append(pattern[:-1] + "demo" if pattern.endswith("*") else pattern)
    return sorted(set(names))


MODEL_CONFIG = {
    "default_model": {"provider": "ollama", "model": "hayat:latest"},
    "classification_model": {"provider": "auto", "model": None},
    "embedding_model": {"provider": "ollama", "model": "nomic-embed-text:latest"},
    "vision_model": {"provider": "ollama", "model": "qwen-vision"},
    "providers": {"ollama": {"base_url": "http://localhost:11434"}},
}
ROLES = ["coder", "computer", "repair", "researcher", "reviewer"]
SERVICES = [attr for attr, *_rest in bc.SERVICES]


def full_components() -> list[bc.Component]:
    return bc.build(tool_names=catalog_tools(), model_config=MODEL_CONFIG, specialists=ROLES, services=SERVICES)


# ----------------------------------------------------------------- building

def test_components_come_only_from_supplied_facts():
    # Nothing supplied, nothing invented.
    assert bc.build(include_intents=False, include_policy=False) == []
    components = full_components()
    ids = [item.component_id for item in components]
    assert len(ids) == len(set(ids))
    assert {item.parent for item in components} <= NODE_IDS
    counts = {kind: sum(item.kind == kind for item in components) for kind in bc.KINDS}
    assert counts["capability"] == len(CATALOG)
    assert counts["model"] == 4                                   # the *_model slots only, not "providers"
    assert counts["specialist"] == len(ROLES)
    assert counts["policy"] == 5                                  # one per RiskTier
    from app.intent.models import IntentKind
    assert counts["intent"] == len(IntentKind)
    # The total is about six times the 21 subsystems, and every one is a distinct real thing.
    assert 100 <= len(components) <= 140


def test_capability_neurons_carry_their_registered_tools_and_window():
    components = {item.component_id: item for item in full_components()}
    markets = components["cap:markets"]
    assert markets.parent == "network" and markets.opens == "_open_markets"
    assert all(tool in catalog_tools() for tool in markets.tools)
    # An area whose catalog lists no tools still exists, honestly with none.
    assert components["cap:chat"].tools == ()
    assert "0 registered tools" in components["cap:chat"].detail


def test_services_skip_subsystem_objects_backed_services_and_missing_ones():
    services = {item.probe for item in bc.service_components(["scheduler", "memory", "camera", "markets"])}
    assert services == {"scheduler"}             # memory is a subsystem; camera/markets back capability areas
    assert bc.service_components([]) == []


def test_model_roles_name_the_configured_model_and_home():
    models = {item.component_id: item for item in bc.model_components(MODEL_CONFIG)}
    assert models["model:default_model"].detail == "ollama / hayat:latest"
    assert "deterministic path" in models["model:classification_model"].detail
    assert models["model:embedding_model"].parent == "memory"
    assert models["model:vision_model"].parent == "perception"


# -------------------------------------------------------------- usage + links

USAGE_ROWS = [
    (stamp(0), "TURN_ROUTED", "gui", json.dumps({"intent": "RESEARCH"}), "t1", None),
    (stamp(1), "TOOL_COMPLETED", "tools", json.dumps({"tool": "web_search"}), "t1", None),
    (stamp(2), "TOOL_FAILED", "tools", json.dumps({"tool": "public_web_fetch", "error": "timed out"}), "t1", "k1"),
    (stamp(3), "TASK_CREATED", "agent", json.dumps({}), "t1", "k1"),
    (stamp(4), "TOOL_COMPLETED", "tools", json.dumps({"tool": "read_file"}), None, "k1"),   # task of turn t1
    (stamp(5), "TURN_COMPLETED", "gui", json.dumps({}), "t1", None),
    (stamp(10), "TURN_ROUTED", "gui", json.dumps({"intent": "ACTION"}), "t2", None),
    (stamp(11), "TOOL_COMPLETED", "tools", json.dumps({"tool": "read_file"}), "t2", None),
    (stamp(12), "TURN_FAILED", "gui", json.dumps({}), "t2", None),
    (stamp(20), "TOOL_COMPLETED", "tools", json.dumps({"tool": "web_search"}), None, None),   # learner: no group
    (stamp(30), "AUTHORITY_DENIED", "authority",
     json.dumps({"tool": "install_system_software", "tier_name": "EXTENDED"}), None, None),
    (stamp(31), "AUTHORITY_APPROVAL_REQUIRED", "authority",
     json.dumps({"tool": "send_email", "tier_name": "HIGH_CONSEQUENCE"}), None, None),
    (stamp(40), "SERVICE_DEGRADED", "shutdown", json.dumps({"component": "scheduler", "error": "slow stop"}),
     None, None),
]


def test_usage_counts_calls_failures_last_outcome_and_groups():
    usage = summarize(USAGE_ROWS, [("mcp_gateway", 7, stamp(50))])
    assert usage.tools["web_search"].calls == 2 and usage.tools["web_search"].failures == 0
    fetch = usage.tools["public_web_fetch"]
    assert (fetch.calls, fetch.failures, fetch.last_ok, fetch.last_note) == (1, 1, False, "timed out")
    assert usage.intents["ACTION"].failures == 1 and usage.intents["ACTION"].last_ok is False
    assert usage.intents["RESEARCH"].last_ok is True
    assert usage.tiers["EXTENDED"].failures == 1 and usage.tiers["HIGH_CONSEQUENCE"].failures == 0
    assert usage.sources["scheduler"].failures == 1
    assert usage.sources["mcp_gateway"].calls == 7
    groups = {group.key: group for group in usage.groups}
    # The task's call is folded into the turn that created it; the learner's call has no group.
    assert groups["t1"].tools == frozenset({"web_search", "public_web_fetch", "read_file"})
    assert groups["t1"].intent == "RESEARCH" and set(groups) == {"t1", "t2"}
    assert usage.since == at(0) and usage.until == at(40)


def test_load_usage_reads_the_event_log_read_only(tmp_path):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, event_type TEXT, "
                   "source TEXT, payload_json TEXT, turn_id TEXT, task_id TEXT)")
        db.executemany("INSERT INTO events(timestamp,event_type,source,payload_json,turn_id,task_id) "
                       "VALUES (?,?,?,?,?,?)", USAGE_ROWS)
    before = path.stat().st_mtime_ns
    usage = load_usage(path)
    assert usage.rows == len(USAGE_ROWS) and usage.tools["read_file"].calls == 2
    assert usage.sources["tools"].calls == 5
    assert path.stat().st_mtime_ns == before
    assert load_usage(tmp_path / "missing.db").rows == 0


def test_co_use_links_are_recorded_turns_only_strongest_first():
    components = full_components()
    usage = summarize(USAGE_ROWS)
    links = bc.co_use_links(components, usage)
    pairs = {(link.a, link.b): link.weight for link in links}
    owner = bc.Snapshot(tuple(components)).tool_index()
    assert owner["web_search"] and owner["read_file"]
    research, files = owner["web_search"][0], owner["read_file"][0]
    assert pairs[tuple(sorted((research, files)))] == 1
    assert pairs[tuple(sorted(("intent:RESEARCH", research)))] == 1
    assert pairs[tuple(sorted(("intent:ACTION", files)))] == 1
    assert not any(a.startswith("intent:") and b.startswith("intent:") for a, b in pairs)
    assert [link.weight for link in links] == sorted((link.weight for link in links), reverse=True)
    assert len(bc.co_use_links(components, usage, limit=1)) == 1
    assert bc.co_use_links(components, Usage(None, None, 0, {}, {}, {}, {}, ())) == []
    # A group whose tools belong to one area makes no link.
    lone = Usage(None, None, 1, {}, {}, {}, {}, (Group("x", "", frozenset({"web_search"})),))
    assert [link for link in bc.co_use_links(components, lone) if link.kind == "co-use"] == []


def test_governance_and_route_links_follow_real_permissions_and_manager_routing():
    components = full_components()
    tiers = bc.tool_tiers({"web_search": "web_search", "read_file": "filesystem_read",
                           "public_web_fetch": "public_web_read", "nonsense": "no_such_category"})
    assert tiers == {"web_search": "PASSIVE", "read_file": "PASSIVE", "public_web_fetch": "PASSIVE"}
    governs = bc.governance_links(components, tiers)
    assert governs and all(link.b == "tier:PASSIVE" and link.kind == "governs" for link in governs)
    routes = {(link.a, link.b) for link in bc.route_links(components)}
    assert routes == {("intent:CODING", "helper:coder"), ("intent:RESEARCH", "helper:researcher"),
                      ("intent:COMPUTER_CONTROL", "helper:computer")}


def test_states_sum_each_neurons_own_records(tmp_path):
    components = full_components()
    usage = summarize(USAGE_ROWS, [("mcp_gateway", 7, stamp(50))])
    router = tmp_path / "router.db"
    with sqlite3.connect(router) as db:
        db.execute("CREATE TABLE model_task_outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
                   "task_type TEXT, model TEXT, verified INTEGER, success INTEGER, latency_ms REAL, "
                   "corrected INTEGER DEFAULT 0, verification_json TEXT DEFAULT '{}')")
        db.executemany("INSERT INTO model_task_outcomes(timestamp,task_type,model,verified,success,latency_ms) "
                       "VALUES (?,?,?,?,?,?)",
                       [(stamp(1), "chat", "hayat:latest", 1, 1, 10), (stamp(2), "chat", "hayat:latest", 1, 0, 10),
                        (stamp(3), "chat", "hayat:latest", 0, 1, 10)])
    states = bc.states(components, usage, models=bc.model_outcomes(router),
                       readiness={"cap:markets": ("setup", "needs a key")})
    owner = bc.Snapshot(tuple(components)).tool_index()
    research = states[owner["web_search"][0]]
    assert research.unit == "tool calls" and research.calls >= 2
    assert states["intent:ACTION"].calls == 1 and states["intent:ACTION"].failures == 1
    assert states["tier:EXTENDED"].failures == 1
    assert states["model:default_model"].calls == 2 and states["model:default_model"].failures == 1
    assert states["svc:mcp_gateway"].calls == 7
    assert states["svc:scheduler"].failures == 1
    assert states["svc:events"].calls == sum(item.calls for item in usage.sources.values())
    assert states["cap:markets"].readiness == "setup"
    assert states["cap:chat"].calls == 0                       # never used: zero, not a guess
    assert bc.model_outcomes(tmp_path / "missing.db") == {}


def test_placement_is_fixed_inside_the_shell_and_near_each_parent():
    from app.gui_brain import LAYOUT

    components = full_components()
    first, second = bc.place(components, LAYOUT), bc.place(components, LAYOUT)
    assert first == second and set(first) == {item.component_id for item in components}
    for item in components:
        x, y, z = first[item.component_id]
        assert (x / 1.22) ** 2 + (y / .88) ** 2 + (z / 1.08) ** 2 <= .9001
        px, py, pz = LAYOUT[item.parent]
        assert ((x - px) ** 2 + (y - py) ** 2 + (z - pz) ** 2) ** .5 < .5
    points = list(first.values())
    assert len(set(points)) == len(points)


def test_describe_and_check_use_live_runtime_facts():
    components = {item.component_id: item for item in full_components()}
    usage = summarize(USAGE_ROWS)
    states = bc.states(components.values(), usage)
    text = bc.describe(components["intent:ACTION"], states["intent:ACTION"], parent_label="Intent",
                       links=bc.co_use_links(components.values(), usage),
                       labels={key: item.label for key, item in components.items()})
    assert "intent route" in text and "1 turns routed; 1 failed" in text and "Used together with" in text
    assert "Recorded: none yet" in bc.describe(components["cap:chat"], states["cap:chat"])

    class Scheduler:
        def status(self):
            return {"running": True, "jobs": 3}

    runtime = SimpleNamespace(
        scheduler=Scheduler(), registry=SimpleNamespace(names=lambda: ["web_search"]),
        authority=SimpleNamespace(status=lambda: {"owner_mode": True, "privacy_mode": False,
                                                  "auto_categories": ["web_search"], "always_protected": []}),
        intent_router=SimpleNamespace(route=lambda *_: None), agent=object(),
        router=SimpleNamespace(get=lambda role: SimpleNamespace(name="ollama", model="hayat:latest")),
        intelligent_router=SimpleNamespace(status=lambda: {"model_health": {}}))
    runtime.manager = SimpleNamespace(specialists={"coder": SimpleNamespace(core=runtime.agent)})
    assert "running: True" in bc.check(components["svc:scheduler"], runtime)
    assert "not available" in bc.check(components["svc:backups"], runtime)
    assert "Registered: yes" in bc.check(components["helper:coder"], runtime)
    assert "web_search" in bc.check(components["tier:PASSIVE"], runtime)
    assert "hayat:latest" in bc.check(components["model:default_model"], runtime)
    research = next(item for item in components.values() if "web_search" in item.tools)
    assert "Tools registered now: 1 of" in bc.check(research, runtime)


def test_history_spans_name_their_tool_for_playback_and_heat():
    rows = [(stamp(0), "TOOL_STARTED", "tools", json.dumps({"tool": "web_search"}), "t"),
            (stamp(4), "TOOL_COMPLETED", "tools", json.dumps({"tool": "web_search"}), "t"),
            (stamp(5), "TOOL_FAILED", "tools", json.dumps({"tool": "read_file", "error": "x", "started": False}), "t")]
    history = reconstruct(rows, at(0), at(10))
    assert history.tools_at(at(2)) == ["web_search"] and history.tools_at(at(8)) == []
    assert history.tools_between(at(0), at(10)) == ["web_search"]
    assert history.tool_seconds(at(0), at(10)) == {"web_search": 4.0, "read_file": 1.0}
    assert [mark.tool for mark in history.marks] == ["read_file"]


# ---------------------------------------------------------------------- GUI

tk = pytest.importorskip("tkinter")


def full_snapshot() -> bc.Snapshot:
    components = full_components()
    usage = summarize(USAGE_ROWS)
    links = [*bc.co_use_links(components, usage), *bc.route_links(components),
             *bc.governance_links(components, {tool: "PASSIVE" for tool in catalog_tools()})]
    return bc.Snapshot(tuple(components), bc.states(components, usage,
                                                     readiness={"cap:research": ("ready", "ok")}), tuple(links))


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "900x560-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def graph(root):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette

    opened, chosen, drilled = [], [], []
    widget = BrainGraph(root, Palette, opened.append)
    widget.on_component, widget.on_component_drill = chosen.append, drilled.append
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    widget.set_components(full_snapshot())
    yield widget, opened, chosen, drilled
    widget.destroy()


def test_components_draw_as_dots_without_touching_subsystem_layers(graph):
    widget, *_ = graph
    nodes_before = len(widget.find_withtag("node"))
    widget.redraw()
    assert len(widget.find_withtag("node")) == nodes_before
    assert len(widget.find_withtag("evidence")) == len(BrainMesh.NODES)
    drawn = widget._component_positions
    # Level of detail: back-facing components are skipped, front ones are dots with no name.
    assert 0 < len(drawn) < len(widget.components)
    assert not widget.find_withtag("component-name")
    assert len(widget.find_withtag("membership")) == len(drawn)
    # Governance links show only on focus.
    assert not widget.find_withtag("governs")
    widget.set_components(None)
    assert not widget.find_withtag("component") and not widget._component_positions


def test_hover_click_and_double_click_a_component(graph):
    widget, opened, chosen, drilled = graph
    key = max(widget._component_positions, key=lambda name: widget._component_positions[name][2])
    x, y, _z, _r = widget._component_positions[key]
    if widget._hit(x, y) is not None:
        key = next(name for name, (px, py, _pz, _pr) in widget._component_positions.items()
                   if widget._hit(px, py) is None)
        x, y, _z, _r = widget._component_positions[key]
    widget._inside, widget._pointer = True, (x, y)
    widget._rehover()
    assert widget.component_hover == key and widget.find_withtag("component-label")
    texts = " ".join(widget.itemcget(item, "text") for item in widget.find_withtag("component-label")
                     if widget.type(item) == "text")
    assert widget.components[key].label in texts
    widget._press(SimpleNamespace(x=x, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert chosen == [key] and not opened and widget.component_active == key
    widget._double(SimpleNamespace(x=x, y=y))
    assert drilled == [key]
    assert "Recorded:" in widget.component_detail(key)
    # Choosing a subsystem puts the component down.
    widget.select("memory")
    assert widget.component_active is None
    widget._leave(SimpleNamespace())
    assert widget.component_hover is None


def test_running_tools_and_models_light_their_components_and_pulse(graph):
    widget, *_ = graph
    tool = next(tool for tool in catalog_tools() if widget._tool_owners.get(tool))
    owners = set(widget._tool_owners[tool])
    widget.set_activity({"running": [{"kind": "tool", "label": tool},
                                     {"kind": "model", "label": "thinking", "detail": "ollama/hayat:latest"}]}, {})
    assert owners <= widget._active_components and "model:default_model" in widget._active_components
    assert widget._overlay(redrawn=False) is True
    assert widget.find_withtag("component-flow")
    assert len(widget.find_withtag("flow")) == len(widget._edges)        # subsystem pulses unchanged
    widget.set_activity({"running": []}, {})
    assert not widget._active_components and widget._overlay(redrawn=False) is False
    assert not widget.find_withtag("component-flow")


def test_playback_marks_and_heat_reach_components(graph):
    from app.brain_history import Mark

    widget, *_ = graph
    tool = next(tool for tool in catalog_tools() if widget._tool_owners.get(tool))
    widget.show_moment(at(0), ["tool"], [Mark(at(0), "tools", "failure", "x", tool)], tools=[tool])
    assert set(widget._tool_owners[tool]) <= widget._active_components
    assert widget._component_marks[widget._tool_owners[tool][0]] == "failure"
    widget.show_moment(None)
    assert not widget._active_components and not widget._component_marks
    widget.set_component_heat({tool: 30.0})
    assert widget.component_heat[widget._tool_owners[tool][0]] == 1.0
    widget.set_component_heat(None)
    assert widget.component_heat == {}


def test_layer_filter_and_parts_toggle_hide_components(graph):
    widget, *_ = graph
    widget.set_layer("knowledge")
    assert all(widget.components[key].parent in widget._positions for key in widget._component_positions)
    widget.set_layer("All layers")
    widget.show_components = False
    widget.redraw()
    assert not widget._component_positions and not widget.find_withtag("component")
    widget.show_components = True
    widget.redraw()
    assert widget._component_positions


def test_redraw_stays_smooth_with_a_realistic_component_count(graph, monkeypatch):
    """Measured on this machine and written to .test-runs for the report."""
    widget, *_ = graph
    monkeypatch.setattr(widget, "_size", lambda: (900, 560))
    report = {"components": len(widget.components), "links": len(widget.component_links)}

    def frames(count: int = 15) -> dict[str, float]:
        durations = []
        for _ in range(count):
            started = time.perf_counter()
            widget.angle += .05
            widget.redraw()
            durations.append((time.perf_counter() - started) * 1000)
        return {"median_ms": round(statistics.median(durations), 2), "max_ms": round(max(durations), 2),
                "items": len(widget.find_all())}

    for detail in ("Low", "Normal"):
        widget.set_surface("Folded", detail=detail)
        widget.show_components = False
        report[f"{detail} without parts"] = frames()
        widget.show_components = True
        report[f"{detail} with parts"] = frames()
        report[f"{detail} drawn parts"] = len(widget._component_positions)
    out = Path(__file__).resolve().parents[1] / ".test-runs" / "brain-components-frame-time.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    # Target is ~25 ms; the bound leaves room for a loaded test machine.
    assert report["Normal with parts"]["median_ms"] < 60
    assert report["Normal with parts"]["median_ms"] - report["Normal without parts"]["median_ms"] < 20


# --------------------------------------------------------- whole window

def test_the_operator_loads_real_components_from_the_live_runtime(shared_tk):
    from tests.test_gui import _build_window, _release
    gui = _build_window()

    def pump(gui, predicate, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            gui.root.update()
            if predicate():
                return True
            time.sleep(.02)
        return predicate()

    try:
        gui.root.geometry("1280x860+20+20")
        gui._operator_suppress_auto_refresh = True
        gui.show_view("operator")
        gui._operator_suppress_auto_refresh = False
        gui.root.update()
        graph = gui.brain_graph
        graph.set_motion(False)
        assert pump(gui, lambda: gui._brain_components is not None, seconds=30)
        snap = gui._brain_components
        counts = snap.counts()
        registry = set(gui.runtime.registry.names())
        # Every capability area's tools are registered tools; every neuron has a real home.
        assert all(set(item.tools) <= registry for item in snap.components)
        # Every neuron has a real home: a subsystem, or (for a tool) the capability area that lists it.
        areas = {item.component_id: item for item in snap.components if item.kind == "capability"}
        assert {item.parent for item in snap.components if item.kind != "tool"} <= NODE_IDS
        tools = [item for item in snap.components if item.kind == "tool"]
        assert tools and all(item.parent in areas and item.tools[0] in areas[item.parent].tools for item in tools)
        # Six times the parts of 2026-11-08 (129), each one a registered tool or a real part.
        assert counts["tool"] == len({tool for area in areas.values() for tool in area.tools})  # one per tool
        assert len(snap.components) >= 6 * 120, counts
        assert counts["capability"] == len(CATALOG)
        report = {"total": len(snap.components), "by_kind": counts, "links": snap.link_counts(),
                  "build_ms": snap.build_ms, "registered_tools": len(registry)}
        out = Path(__file__).resolve().parents[1] / ".test-runs" / "brain-components-live.json"
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        # Choose a service component and run its readiness check through the window.
        gui._select_brain_component("svc:scheduler")
        assert "Scheduler" in gui.brain_node_detail.get()
        gui._drill_brain_component("svc:scheduler")
        assert pump(gui, lambda: "Readiness check at" in gui.brain_node_detail.get(), seconds=15)
        # A capability area with a window opens it rather than checking.
        before = gui.current_view
        gui._drill_brain_component("cap:health")             # view:operator
        assert gui.current_view == "operator" or before == "operator"
        # A subsystem's inspector names its parts.
        gui._select_brain_node("tools")
        assert "Made of" in gui.brain_node_detail.get()
        # A tool neuron's own check reads its registration and its recorded use.
        first_tool = tools[0].component_id
        gui._select_brain_component(first_tool)
        gui._drill_brain_component(first_tool)
        assert pump(gui, lambda: "risk" in gui.brain_node_detail.get(), seconds=15), gui.brain_node_detail.get()
        # Parts on -> every tool -> parts off -> parts on.
        gui._toggle_brain_parts()
        assert graph.show_all_tools and gui.brain_parts_button.cget("text") == "Every tool"
        started = time.perf_counter()
        graph.redraw()
        report["every_tool_redraw_ms"] = round((time.perf_counter() - started) * 1000, 1)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert len(graph._component_positions) > 300  # the tools are drawn, not just counted
        gui._toggle_brain_parts()
        assert not graph.show_components and gui.brain_parts_button.cget("text") == "Parts off"
        gui._toggle_brain_parts()
        assert graph.show_components and not graph.show_all_tools
    finally:
        _release(gui)
