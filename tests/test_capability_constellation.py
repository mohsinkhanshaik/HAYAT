"""The Home sphere: every particle a real part of HAYAT, every link a real relation."""
from __future__ import annotations

import json
import random
import sqlite3
import statistics
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import capabilities
from app import capability_constellation as cc


def synthetic_tools(total: int) -> dict[str, str]:
    """Tool names that land in real capability areas, enough to fill ``total``."""
    names: list[str] = []
    patterns = [pattern for capability in capabilities.CATALOG for pattern in capability.tools]
    round_ = 0
    while len(names) < total:
        for pattern in patterns:
            name = pattern[:-1] + f"x{round_}" if pattern.endswith("*") else (pattern if round_ == 0 else "")
            if name and name not in names:
                names.append(name)
                if len(names) == total:
                    break
        round_ += 1
    return {name: f"Does {name.replace('_', ' ')}." for name in names}


def synthetic_co_use(tools: dict[str, str], pairs: int = 160) -> dict[tuple[str, str], dict[str, int]]:
    rng = random.Random(7)
    names = sorted(tools)
    found: dict[tuple[str, str], dict[str, int]] = {}
    while len(found) < pairs:
        a, b = sorted(rng.sample(names, 2))
        found[(a, b)] = {"together": rng.randint(0, 3), "sequence": rng.randint(0, 5)}
    return found


# ---------------------------------------------------------------- pure model

def test_every_tool_becomes_one_particle_in_its_own_area():
    tools = synthetic_tools(300)
    built = cc.build(tools)
    keys = [particle.key for particle in built.particles]
    assert len(keys) == len(set(keys))
    assert {key[5:] for key in keys if key.startswith("tool:")} == set(tools)
    assert built.counts() == {"tool": 300, "area": len(capabilities.CATALOG)}
    for particle in built.particles:
        if particle.kind == "tool":
            areas = [cap.key for cap in capabilities.CATALOG if capabilities.tools_in(cap, [particle.name])]
            assert particle.region == areas[0]
        assert abs(sum(v * v for v in particle.point) - 1) < 1e-9


def test_no_filler_the_total_is_the_real_inventory_not_the_target():
    built = cc.build(synthetic_tools(50))
    assert len(built.particles) == 50 + len(capabilities.CATALOG) < cc.TARGET_PARTICLES
    assert not any(particle.kind not in {"tool", "area", "feed", "model"} for particle in built.particles)


def test_areas_cluster_into_compact_regions():
    built = cc.build(synthetic_tools(864 - len(capabilities.CATALOG)))
    assert len(built.particles) == 864
    regions = built.regions()
    spreads, overall = [], []
    for members in regions.values():
        if len(members) < 4:
            continue
        points = [built.particles[i].point for i in members]
        centre = [sum(p[axis] for p in points) / len(points) for axis in range(3)]
        spreads.append(statistics.mean(cc._distance2(p, centre) for p in points))
    everything = [particle.point for particle in built.particles]
    middle = [sum(p[axis] for p in everything) / len(everything) for axis in range(3)]
    overall = statistics.mean(cc._distance2(p, middle) for p in everything)
    # A region is a patch: its points sit far closer together than the sphere's.
    assert max(spreads) < overall * .45
    # And each area's hub is the particle nearest that patch's middle.
    for region, members in regions.items():
        hub = built.index(f"area:{region}")
        if hub is not None:
            assert members[0] == hub


def test_area_links_connect_each_region_and_co_use_links_follow_the_record():
    tools = synthetic_tools(400)
    co_use = synthetic_co_use(tools)
    built = cc.build(tools, co_use=co_use)
    links = built.link_counts()
    assert links["area"] > len(built.particles)          # a mesh, not a chain
    for region, members in built.regions().items():
        inside = {(link.a, link.b) for link in built.links if link.kind == "area"
                  and link.a in members and link.b in members}
        seen, frontier = {members[0]}, [members[0]]
        while frontier:
            node = frontier.pop()
            for a, b in inside:
                for x, y in ((a, b), (b, a)):
                    if x == node and y not in seen:
                        seen.add(y)
                        frontier.append(y)
        assert seen == set(members), f"region {region} is not one connected patch"
    expected_together = sum(1 for value in co_use.values() if value["together"])
    expected_sequence = sum(1 for value in co_use.values() if not value["together"]
                            and value["sequence"] >= cc.SEQUENCE_MINIMUM)
    assert links.get("together", 0) == expected_together
    assert links.get("sequence", 0) == expected_sequence
    for link in built.links:
        if link.kind == "together":
            a, b = sorted((built.particles[link.a].name, built.particles[link.b].name))
            assert link.weight == co_use[(a, b)]["together"] * 3 + co_use[(a, b)]["sequence"]
    assert len(built.links) <= cc.MAX_LINKS


def _events_db(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, event_type TEXT, source TEXT,"
               " payload_json TEXT, handled INTEGER DEFAULT 0, sequence INTEGER, runtime_id TEXT, session_id TEXT,"
               " turn_id TEXT, task_id TEXT, final INTEGER DEFAULT 0, supersedes TEXT)")
    start = datetime(2026, 11, 1, tzinfo=UTC)
    for seconds, tool, turn, runtime_id in rows:
        db.execute("INSERT INTO events(timestamp, event_type, source, payload_json, runtime_id, turn_id)"
                   " VALUES(?,?,?,?,?,?)", ((start + timedelta(seconds=seconds)).isoformat(), "TOOL_STARTED",
                                            "tools", json.dumps({"tool": tool}), runtime_id, turn))
    db.commit()
    db.close()


def test_co_use_is_read_from_turns_and_back_to_back_calls(tmp_path):
    path = tmp_path / "events.db"
    _events_db(path, [
        (0, "web_search", "turn-1", "r1"), (2, "research_topic", "turn-1", "r1"),
        (100, "web_search", None, "r1"), (105, "image_gallery", None, "r1"),
        (200, "web_search", None, "r1"), (210, "image_gallery", None, "r1"),
        (300, "web_search", None, "r1"), (400, "image_gallery", None, "r1"),   # too far apart
        (500, "read_file", None, "r2"), (501, "write_file", None, "r3"),      # different HAYATs
    ])
    found = cc.read_co_use(path)
    assert found[("research_topic", "web_search")] == {"together": 1, "sequence": 1}
    assert found[("image_gallery", "web_search")] == {"together": 0, "sequence": 2}
    assert ("read_file", "write_file") not in found


def test_a_huge_turn_links_only_neighbours_in_call_order(tmp_path):
    path = tmp_path / "events.db"
    tools = [f"tool_{i:02d}" for i in range(cc.TURN_PAIR_LIMIT + 5)]
    _events_db(path, [(i * 100, tool, "turn-big", "r1") for i, tool in enumerate(tools)])
    found = cc.read_co_use(path)
    assert sum(value["together"] for value in found.values()) == len(tools) - 1


def test_track_records_come_from_the_tool_record(tmp_path):
    path = tmp_path / "tool_understanding.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, tool TEXT, ok INTEGER, ms INTEGER, error_kind TEXT,"
               " refused INTEGER, at TEXT, error_detail TEXT)")
    db.executemany("INSERT INTO tool_calls (tool, ok, ms, error_kind, refused, at, error_detail) VALUES (?,?,?,?,?,?,?)",
                   [("web_search", 1, 100, "", 0, "2026-11-01", ""), ("web_search", 0, 300, "Timeout", 0, "2026-11-02", ""),
                    ("web_search", 0, 5, "", 1, "2026-11-03", "")])
    db.commit()
    db.close()
    record = cc.read_track_records(path)["web_search"]
    assert record == {"calls": 3, "succeeded": 1, "failed": 1, "refused": 1, "avg_ms": 135,
                      "last_at": "2026-11-03", "failure_rate": .5}
    assert cc.record_line(record) == "3 calls · 1 failed (50%) · average 135 ms · 1 refused by the safety rules"
    assert cc.record_line(None) == "Not used yet."
    assert cc.read_track_records(tmp_path / "missing.db") == {}


def test_feeds_need_their_key_and_models_need_a_model():
    keyless = cc.feed_inventory({})
    from app.intelligence.feeds import ALL_FEEDS

    keyed = [feed for feed in ALL_FEEDS if feed.key_env]
    assert len(keyless) == len(ALL_FEEDS) - len(keyed)
    if keyed:
        with_key = cc.feed_inventory({keyed[0].key_env: "secret"})
        assert len(with_key) == len(keyless) + 1
    config = {"default_model": {"provider": "ollama", "model": "hayat:latest"},
              "reasoning_model": {"provider": "ollama", "model": "hayat:latest"},
              "classification_model": {"provider": "auto", "model": None},
              "identity_model": {"provider": "ollama", "model": "hayat:latest", "base_model": "qwen:q4"},
              "vision_model": {"provider": "ollama", "model": "qwen:q4"}}
    models = cc.model_inventory(config)
    assert [row["role"] for row in models] == ["default_model", "reasoning_model", "vision_model", "identity_model"]
    built = cc.build({}, models=models, feeds=keyless[:5])
    links = built.link_counts()
    assert links["same_model"] == 2 and links["built_on"] == 1
    assert built.counts()["feed"] == 5


def test_explanations_are_plain_and_name_real_partners():
    tools = {"web_search": "Search the web.", "research_topic": "Research a topic."}
    built = cc.build(tools, co_use={("research_topic", "web_search"): {"together": 2, "sequence": 1}},
                     records={"web_search": {"calls": 4, "failed": 1, "refused": 0, "avg_ms": 1500}})
    text = cc.explain(built, "tool:web_search")
    assert "Search the web." in text and "4 calls · 1 failed (25%) · average 1.5 s" in text
    assert "Often works with: research_topic (ran in the same turn, 7x)" in text
    area = cc.explain(built, "area:research")
    assert "tools in this area" in area and "web_search" in area
    assert cc.explain(built, "tool:nothing") == "That part is no longer on the sphere."


# ------------------------------------------------------------------ the widget

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, "1280x430-4000-4000")
    yield window
    close_window(window)


@pytest.fixture
def sphere(root):
    from app.gui_hologram import NeuralWorkspace
    from app.gui_theme import Palette

    root.geometry("1280x430-4000-4000")
    opened, actions = [], []
    widget = NeuralWorkspace(root, Palette, opened.append, actions.append)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    tools = synthetic_tools(864 - len(capabilities.CATALOG))
    widget.set_constellation(cc.build(tools, co_use=synthetic_co_use(tools)))
    root.update()
    yield widget, opened, actions
    widget.destroy()


def _front_particle(widget, kind="tool"):
    sx, sy, z = widget._projected
    order = sorted(range(len(z)), key=lambda i: -z[i])
    for i in order:
        particle = widget.constellation.particles[i]
        if particle.kind == kind and widget._hit(float(sx[i]), float(sy[i])) == particle.key:
            return i, particle
    raise AssertionError("no facing particle can be pointed at")


def test_frame_time_with_864_particles(sphere, root):
    widget, _opened, _actions = sphere
    assert len(widget.constellation.particles) == 864
    widget.angle, widget.pitch = 0.3, -0.2
    widget.redraw()
    root.update_idletasks()
    frames, sphere_only = [], []
    for step in range(40):
        widget.angle += .05
        started = time.perf_counter()
        widget.redraw()
        root.update_idletasks()         # includes Tk actually painting the canvas
        frames.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        widget._paint_sphere(*widget._size(), geometry=True)
        sphere_only.append((time.perf_counter() - started) * 1000)
    shown = int(widget._pool["line_shown"].sum())
    median, p95 = statistics.median(frames), sorted(frames)[int(len(frames) * .95) - 1]
    print(f"\n864 particles, {len(widget.constellation.links)} links ({shown} drawn): frame median {median:.1f} ms,"
          f" p95 {p95:.1f} ms; sphere update alone median {statistics.median(sphere_only):.1f} ms")
    assert 0 < shown < len(widget.constellation.links)   # the far hemisphere is not drawn
    # The goal is under ~20 ms on the owner's PC. Measured 2026-11-08 with other test runs loading the
    # machine: ~31 ms, against ~49 ms for the old 144-point globe in the same run. The bound leaves room
    # for a busy test machine; the printed numbers are the measurement.
    assert median < 70


def test_scene_items_stay_bounded_while_turning(sphere):
    widget, _opened, _actions = sphere
    widget.redraw()
    count = len(widget.find_all())
    for _ in range(12):
        widget.angle += .2
        widget.pitch = -widget.pitch
        widget.redraw()
    assert len(widget.find_all()) == count


def test_hover_explains_a_particle_with_its_track_record(sphere, root):
    widget, _opened, _actions = sphere
    i, particle = _front_particle(widget)
    sx, sy, _z = widget._projected
    widget._inside = True
    widget._move(SimpleNamespace(x=float(sx[i]), y=float(sy[i])))
    assert widget._hover == particle.key
    texts = [widget.itemcget(item, "text") for item in widget.find_withtag("tip") if widget.type(item) == "text"]
    assert particle.name in texts and particle.region_title in texts
    assert "Not used yet." in texts
    widget._leave(SimpleNamespace())
    assert not widget.find_withtag("tip")


def test_click_opens_the_explanation_or_the_tool_browser(sphere):
    widget, opened, actions = sphere
    i, particle = _front_particle(widget)
    sx, sy, _z = widget._projected
    point = SimpleNamespace(x=float(sx[i]), y=float(sy[i]))
    widget._press(point)
    widget._release(point)
    assert opened == ["toolbox"] and actions == []
    asked = []
    widget.explain = lambda title, work: asked.append((title, work()))
    widget._press(point)
    widget._release(point)
    assert asked[0][0] == f"Explain {particle.name}"
    assert particle.description in asked[0][1]


def test_the_six_cards_still_win_over_particles(sphere):
    widget, _opened, actions = sphere
    for key, (left, top, right, bottom) in widget._card_boxes.items():
        point = SimpleNamespace(x=(left + right) / 2, y=(top + bottom) / 2)
        widget._press(point)
        widget._release(point)
    assert actions == [row[0] for row in widget.CAPABILITIES]


def test_a_running_tool_glows_and_a_failed_call_flashes_red(sphere):
    widget, _opened, _actions = sphere
    i, particle = _front_particle(widget)
    dot = widget._pool["dots"][i]
    before = widget.itemcget(dot, "fill")
    widget.set_status({"running": [{"kind": "tool", "label": particle.name}]}, {"state": "off"}, False)
    assert widget._glow[i][0] == "run"
    shades = widget._palette_shades()
    assert widget.itemcget(dot, "fill") in shades[3]["run"] and widget.itemcget(dot, "fill") != before
    halos = [item for item in widget._pool["halos"] if widget.itemcget(item, "state") == "normal"]
    assert halos, "a working tool gets a halo"
    # The call ends: the glow fades rather than vanishing.
    widget.set_status({"running": []}, {"state": "off"}, False)
    assert widget._glow[i][0] == "done"
    # A failure heard from the event bus turns it red.
    widget._hear({"event_type": "TOOL_FAILED", "payload": {"tool": particle.name}})
    widget.set_status({"running": []}, {"state": "off"}, False)
    assert widget._glow[i][0] == "fail"
    assert widget.itemcget(dot, "fill") in shades[3]["fail"]
    assert widget._session[particle.name] == [0, 1]
    # And everything returns to rest once the flash is over.
    widget._glow[i] = ("fail", time.monotonic() - 60)
    widget.set_status({"running": []}, {"state": "off"}, False)
    assert i not in widget._glow
    assert widget.itemcget(dot, "fill") not in shades[3]["fail"]


def test_glow_runs_with_motion_paused_and_stops_with_the_window(sphere, root):
    widget, _opened, _actions = sphere
    assert widget.motion is False and widget._frame is None   # Pause motion: no animation loop
    i, particle = _front_particle(widget)
    widget._hear({"event_type": "TOOL_STARTED", "payload": {"tool": particle.name}})
    widget.set_status({"running": []}, {"state": "off"}, False)
    assert widget._glow[i][0] == "run"                         # the status tick still lights it
    widget.set_motion(True)
    assert widget._overlay(redrawn=False) in {True, False}
    widget.pack_forget()
    root.update()
    assert widget._frame is None                               # hidden: no frames at all
    widget.pack(fill="both", expand=True)
    root.update()
    widget.set_motion(False)


def test_short_strip_hides_the_sphere_and_brings_it_back(sphere, root):
    widget, _opened, _actions = sphere
    root.geometry("930x110-4000-4000")
    root.update()
    widget.redraw()
    assert all(widget.itemcget(item, "state") == "hidden" for item in widget.find_withtag("sphere"))
    assert widget._hit(465, 55) is None or widget._hit(465, 55).startswith("cap:")
    root.geometry("1280x430-4000-4000")
    root.update()
    widget.redraw()
    assert widget.itemcget(widget._pool["dots"][0], "state") in {"", "normal"}


def test_theme_change_recolours_the_pool(sphere, root):
    from app.gui_theme import Palette, apply_theme

    widget, _opened, _actions = sphere

    class LocalPalette(Palette):
        pass

    widget.palette = LocalPalette
    before = widget.itemcget(widget._pool["dots"][0], "fill")
    apply_theme(root, LocalPalette, "Light")
    widget.refresh_theme()
    assert widget.itemcget(widget._pool["dots"][0], "fill") != before


# ------------------------------------------------------------- the real runtime

@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    built = build_runtime(use_model=False, start_background_services=False)
    yield built
    built.close()


def test_the_real_inventory_puts_every_registered_tool_on_the_sphere(runtime):
    built = cc.from_runtime(runtime)
    names = set(runtime.registry.names())
    counts = built.counts()
    assert {particle.name for particle in built.particles if particle.kind == "tool"} == names
    assert counts["area"] == len(capabilities.CATALOG)
    assert counts["tool"] == len(names)
    assert len(built.particles) == sum(counts.values())
    assert not [particle for particle in built.particles if particle.kind == "tool" and particle.region == "other"]
    print(f"\nReal inventory: {cc.summary(built)}")


def test_attached_runtime_lights_particles_from_real_tool_calls(runtime, root):
    from app.gui_hologram import NeuralWorkspace
    from app.gui_theme import Palette

    widget = NeuralWorkspace(root, Palette, lambda key: None, lambda key: None)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    try:
        root.update()
        widget.attach_runtime(runtime)
        deadline = time.monotonic() + 60
        while widget._loaded is None and time.monotonic() < deadline:
            time.sleep(.05)
        widget.set_status({"running": []}, {"state": "off"}, False)
        assert widget.constellation.counts()["tool"] == len(runtime.registry.names())
        result = runtime.registry.execute("about_hayat", {"topic": "limits"})
        assert result.success
        widget.set_status({"running": []}, {"state": "off"}, False)
        i = widget.constellation.index("tool:about_hayat")
        assert widget._glow[i][0] == "done"                     # heard TOOL_STARTED then TOOL_COMPLETED
        assert widget._session["about_hayat"] == [1, 0]
        text = cc.explain(widget.constellation, "tool:about_hayat", runtime)
        assert "about_hayat" in text and "Track record:" in text
    finally:
        widget.destroy()
    assert not widget._unsubscribe                               # destroying the widget unsubscribes
