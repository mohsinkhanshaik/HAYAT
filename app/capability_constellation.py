"""The Home sphere as a map of what HAYAT can really do.

The Home workspace used to draw 144 evenly spaced points joined to their three
nearest neighbours. They meant nothing: no point stood for anything, so a
glowing point could not say what was happening and a click could not explain
anything. This module replaces the decoration with an inventory.

Every particle is one real, working part of HAYAT:

* one per registered tool (``runtime.registry``), placed in the region of its
  plain-language area from :mod:`app.capabilities`;
* one hub per capability area, at the heart of its region;
* one per public data feed the global-intelligence workspace actually fetches
  (keyless feeds, plus keyed ones only when their key is set);
* one per configured model role (``config/models.yaml``) that names a model.

Every link is a real relation:

* ``area``      -- two tools of the same area, joined sparsely (a spanning tree
  over the region plus each point's second-nearest neighbour), so a region
  reads as one connected patch;
* ``together``  -- the two tools ran in the same turn or task (events.db);
* ``sequence``  -- one started within :data:`SEQUENCE_WINDOW` seconds of the
  other on the same running HAYAT, at least twice (events.db);
* ``areas``     -- two area hubs whose tools have run together;
* ``same_model`` / ``built_on`` -- model roles served by the same weights;
* ``reads``     -- the feed catalog and the tool that fetches it.

Nothing here imports Tk, so the inventory, layout and links are unit-tested on
their own. :mod:`app.gui_hologram` only projects and paints them.
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import os
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: The owner asked for six times the old 144 particles. The sphere shows the
#: real inventory instead: if HAYAT has fewer real parts than this, it shows
#: fewer, and never pads the difference with filler.
TARGET_PARTICLES = 864
#: Links kept in the model. The widget draws only the facing ones.
MAX_LINKS = 2600
#: Back-to-back calls this close together count as used together.
SEQUENCE_WINDOW = 20.0
#: Back-to-back pairs need this many sightings; one coincidence is not a habit.
SEQUENCE_MINIMUM = 2
#: A turn that ran more distinct tools than this links only its neighbours in
#: call order, so one enormous turn cannot wire everything to everything.
TURN_PAIR_LIMIT = 12
#: Rows read from the event log, newest first.
EVENT_ROWS = 200_000

FEEDS_REGION = ("feeds", "Live data feeds")
MODELS_REGION = ("models", "Local models")

#: Model role -> what it is used for, in plain words.
MODEL_ROLES = {
    "default_model": "Default answer writer",
    "conversation_model": "Everyday conversation",
    "classification_model": "Sorting requests",
    "reasoning_model": "Reasoning and planning",
    "coding_model": "Writing code",
    "research_synthesis_model": "Summarising research",
    "embedding_model": "Memory search (embeddings)",
    "vision_model": "Seeing images, camera and screen",
    "escalation_model": "DEEP-mode second opinion",
    "identity_model": "HAYAT's own identity model",
}


@dataclass(frozen=True)
class Particle:
    key: str                    # "tool:web_search", "area:research", "feed:earthquakes", "model:vision_model"
    kind: str                   # tool | area | feed | model
    name: str                   # the tool / area / feed / role name itself
    title: str                  # what a person would call it
    region: str                 # region key: an area key, "feeds" or "models"
    region_title: str
    description: str
    point: tuple[float, float, float] = (0.0, 0.0, 1.0)
    #: Extra facts for the tooltip and explanation (model id, feed interval...).
    facts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Link:
    a: int
    b: int
    kind: str                   # area | together | sequence | areas | same_model | built_on | reads
    weight: float = 1.0


@dataclass
class Constellation:
    particles: tuple[Particle, ...]
    links: tuple[Link, ...]
    #: Track record by particle key: tools from tool_understanding.db, feeds from feed health.
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: How the links were derived, for the docs and the explanation.
    sources: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._index = {particle.key: i for i, particle in enumerate(self.particles)}
        self._partners: dict[int, list[tuple[int, Link]]] = defaultdict(list)
        for link in self.links:
            self._partners[link.a].append((link.b, link))
            self._partners[link.b].append((link.a, link))

    def index(self, key: str) -> int | None:
        return self._index.get(key)

    def counts(self) -> dict[str, int]:
        return dict(Counter(particle.kind for particle in self.particles))

    def link_counts(self) -> dict[str, int]:
        return dict(Counter(link.kind for link in self.links))

    def regions(self) -> dict[str, list[int]]:
        grouped: dict[str, list[int]] = defaultdict(list)
        for i, particle in enumerate(self.particles):
            grouped[particle.region].append(i)
        return dict(grouped)

    def partners(self, key: str, kinds: Iterable[str] = ("together", "sequence"), limit: int = 5) -> list[tuple[Particle, Link]]:
        """The parts this one most often works with, strongest first."""
        i = self.index(key)
        if i is None:
            return []
        wanted = set(kinds)
        rows = [(self.particles[j], link) for j, link in self._partners.get(i, []) if link.kind in wanted]
        rows.sort(key=lambda row: -row[1].weight)
        return rows[:limit]


# ----------------------------------------------------------------- inventory

def tool_inventory(registry: Any) -> dict[str, str]:
    """Every registered tool and its own description."""
    tools: dict[str, str] = {}
    for name in sorted(registry.names()):
        try:
            tools[name] = " ".join(str(registry.get(name).description or "").split())
        except Exception:
            log_swallowed(_log, f"reading the description of tool {name}")
            tools[name] = ""
    return tools


def feed_inventory(environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Public feeds the intelligence workspace really fetches here.

    A keyed feed counts only when its key is set: without it the feed is
    skipped by the fetcher, so drawing it would be decoration.
    """
    from app.intelligence.feeds import ALL_FEEDS

    environ = os.environ if environ is None else environ
    rows = []
    for feed in ALL_FEEDS:
        if feed.key_env and not environ.get(feed.key_env):
            continue
        host = feed.url.split("//", 1)[-1].split("/", 1)[0]
        rows.append({"key": feed.key, "title": feed.title, "host": host,
                     "interval": int(feed.interval or 0), "keyed": bool(feed.key_env)})
    return rows


def model_inventory(config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Model roles in config/models.yaml that name a model."""
    rows = []
    for role, purpose in MODEL_ROLES.items():
        entry = (config or {}).get(role)
        if not isinstance(entry, Mapping) or not entry.get("model"):
            continue
        rows.append({"role": role, "purpose": purpose, "model": str(entry["model"]),
                     "provider": str(entry.get("provider") or ""),
                     "base_model": str(entry.get("base_model") or "")})
    return rows


# --------------------------------------------------------------- the records

def _read_only(path: Path | str) -> sqlite3.Connection | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        log_swallowed(_log, f"opening {path.name} read-only")
        return None


def read_track_records(path: Path | str) -> dict[str, dict[str, Any]]:
    """Calls, failures, refusals and average time for every tool, in one query."""
    db = _read_only(path)
    if db is None:
        return {}
    try:
        rows = db.execute(
            "SELECT tool, COUNT(*), COALESCE(SUM(ok), 0), COALESCE(SUM(refused), 0), AVG(ms), MAX(at) "
            "FROM tool_calls GROUP BY tool").fetchall()
    except sqlite3.Error:
        log_swallowed(_log, "reading tool track records")
        return {}
    finally:
        db.close()
    records = {}
    for tool, calls, succeeded, refused, avg_ms, last in rows:
        attempted = int(calls) - int(refused)
        failed = max(0, attempted - int(succeeded))
        records[str(tool)] = {"calls": int(calls), "succeeded": int(succeeded), "failed": failed,
                              "refused": int(refused), "avg_ms": int(avg_ms or 0), "last_at": last,
                              "failure_rate": round(failed / attempted, 3) if attempted else 0.0}
    return records


def read_co_use(path: Path | str, *, window: float = SEQUENCE_WINDOW,
                rows: int = EVENT_ROWS) -> dict[tuple[str, str], dict[str, int]]:
    """Which tools ran together, from TOOL_STARTED events.

    Two measures, kept apart because they mean different things: ``together``
    counts turns or tasks that used both tools; ``sequence`` counts times one
    started within ``window`` seconds of the other on the same running HAYAT.
    """
    db = _read_only(path)
    if db is None:
        return {}
    try:
        found = db.execute(
            "SELECT runtime_id, COALESCE(turn_id, task_id), json_extract(payload_json, '$.tool'), timestamp "
            "FROM events WHERE event_type = 'TOOL_STARTED' ORDER BY id DESC LIMIT ?", (int(rows),)).fetchall()
    except sqlite3.Error:
        log_swallowed(_log, "reading tool events for co-use")
        return {}
    finally:
        db.close()
    found.reverse()
    pairs: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"together": 0, "sequence": 0})
    groups: dict[str, list[str]] = defaultdict(list)
    last: dict[str, tuple[str, float]] = {}
    for runtime_id, group, tool, stamp in found:
        if not tool:
            continue
        tool = str(tool)
        if group:
            groups[str(group)].append(tool)
        try:
            moment = datetime.fromisoformat(str(stamp)).timestamp()
        except ValueError:
            continue
        previous = last.get(str(runtime_id))
        if previous and previous[0] != tool and moment - previous[1] <= window:
            pairs[tuple(sorted((previous[0], tool)))]["sequence"] += 1  # type: ignore[index]
        last[str(runtime_id)] = (tool, moment)
    for tools in groups.values():
        ordered = list(dict.fromkeys(tools))
        if len(ordered) < 2:
            continue
        combos = (itertools.combinations(sorted(ordered), 2) if len(ordered) <= TURN_PAIR_LIMIT
                  else (tuple(sorted(pair)) for pair in zip(ordered, ordered[1:])))
        for a, b in combos:
            pairs[(a, b)]["together"] += 1
    return {pair: dict(value) for pair, value in pairs.items()}


def read_feed_health(intelligence: Any) -> dict[str, dict[str, Any]]:
    """Fetch attempts and successes per feed, from the intelligence store."""
    store = getattr(intelligence, "store", None)
    if store is None or not hasattr(store, "health_summary"):
        return {}
    try:
        summary = store.health_summary()
    except Exception:
        log_swallowed(_log, "reading feed health")
        return {}
    return {str(feed): {"attempts": int(row.get("attempts", 0)), "successes": int(row.get("successes", 0)),
                        "last_at": row.get("last_at")} for feed, row in (summary or {}).items()}


# -------------------------------------------------------------------- layout

def fibonacci_sphere(n: int) -> list[tuple[float, float, float]]:
    golden = math.pi * (3 - math.sqrt(5))
    points = []
    for i in range(n):
        y = 1 - 2 * (i + .5) / n
        r = math.sqrt(max(0.0, 1 - y * y))
        points.append((r * math.cos(i * golden), y, r * math.sin(i * golden)))
    return points


def _distance2(a: Sequence[float], b: Sequence[float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _partition(points: list[int], coords: list[tuple[float, float, float]],
               regions: list[tuple[str, int]], out: dict[str, list[int]]) -> None:
    """Split the sphere into one compact patch per region, sized to the region.

    Recursive bisection: the regions are cut into two runs of about equal
    size, the points are cut along their widest axis in the same proportion,
    and each half recurses. Neighbouring regions in catalog order therefore
    land next to each other, and no region is scattered.
    """
    if len(regions) == 1:
        out[regions[0][0]] = points
        return
    total = sum(size for _key, size in regions)
    running, best, cut = 0, None, 1
    for k in range(1, len(regions)):
        running += regions[k - 1][1]
        gap = abs(running - total / 2)
        if best is None or gap < best:
            best, cut = gap, k
    left_size = sum(size for _key, size in regions[:cut])
    spreads = []
    for axis in range(3):
        values = [coords[i][axis] for i in points]
        spreads.append(max(values) - min(values))
    axis = spreads.index(max(spreads))
    ordered = sorted(points, key=lambda i: (coords[i][axis], i))
    _partition(ordered[:left_size], coords, regions[:cut], out)
    _partition(ordered[left_size:], coords, regions[cut:], out)


def _spanning_links(members: list[int], coords: list[tuple[float, float, float]]) -> list[tuple[int, int, float]]:
    """(a, b, weight): a spanning tree over one region, then a sparse mesh.

    The tree (weight 1) keeps every region one connected patch; each point's
    second and third nearest neighbours in the same region (weight .5) add the
    mesh. Far-away detail drops the mesh edges first.
    """
    if len(members) < 2:
        return []
    tree: set[tuple[int, int]] = set()
    best = {m: (_distance2(coords[m], coords[members[0]]), members[0]) for m in members[1:]}
    while best:
        m = min(best, key=lambda key: best[key][0])
        _d, parent = best.pop(m)
        tree.add((min(m, parent), max(m, parent)))
        for other in best:
            d = _distance2(coords[other], coords[m])
            if d < best[other][0]:
                best[other] = (d, m)
    mesh: set[tuple[int, int]] = set()
    if len(members) > 3:
        for m in members:
            nearest = sorted((other for other in members if other != m),
                             key=lambda other: (_distance2(coords[m], coords[other]), other))[1:3]
            mesh.update((min(m, other), max(m, other)) for other in nearest)
    return [(a, b, 1.0) for a, b in sorted(tree)] + [(a, b, .5) for a, b in sorted(mesh - tree)]


# --------------------------------------------------------------------- build

def _regions_in_order() -> list[tuple[str, str, Any]]:
    from app import capabilities

    order: list[tuple[str, str, Any]] = []
    for capability in capabilities.CATALOG:
        order.append((capability.key, capability.title, capability))
        if capability.key == "chat":
            order.append((*MODELS_REGION, None))
        if capability.key == "public_intelligence":
            order.append((*FEEDS_REGION, None))
    keys = [key for key, _title, _cap in order]
    if MODELS_REGION[0] not in keys:
        order.append((*MODELS_REGION, None))
    if FEEDS_REGION[0] not in keys:
        order.append((*FEEDS_REGION, None))
    return order


def _area_of(name: str, catalog: Sequence[Any]) -> str | None:
    from app import capabilities

    for capability in catalog:
        if capabilities.tools_in(capability, [name]):
            return capability.key
    return None


def build(tools: Mapping[str, str], *, feeds: Sequence[Mapping[str, Any]] = (),
          models: Sequence[Mapping[str, Any]] = (), co_use: Mapping[tuple[str, str], Mapping[str, int]] | None = None,
          records: Mapping[str, Mapping[str, Any]] | None = None,
          feed_health: Mapping[str, Mapping[str, Any]] | None = None,
          include_areas: bool = True, max_links: int = MAX_LINKS) -> Constellation:
    """Lay out and link a real inventory. Deterministic for the same inputs."""
    from app import capabilities

    order = _regions_in_order()
    region_titles = {key: title for key, title, _cap in order}
    members: dict[str, list[Particle]] = defaultdict(list)
    for key, title, capability in order:
        if capability is not None and include_areas:
            members[key].append(Particle(f"area:{key}", "area", key, title, key, title,
                                         capability.summary, facts=(("icon", capability.icon),)))
    other = "other"
    for name, description in sorted(tools.items()):
        region = _area_of(name, capabilities.CATALOG) or other
        if region == other and other not in region_titles:
            region_titles[other] = "Other tools"
            order.append((other, "Other tools", None))
        members[region].append(Particle(f"tool:{name}", "tool", name, name.replace("_", " "), region,
                                        region_titles[region], description))
    for feed in feeds:
        facts = (("source", str(feed.get("host", ""))), ("every", f"{int(feed.get('interval') or 0)} s"))
        members[FEEDS_REGION[0]].append(Particle(
            f"feed:{feed['key']}", "feed", str(feed["key"]), str(feed.get("title") or feed["key"]),
            FEEDS_REGION[0], FEEDS_REGION[1],
            f"Public data feed from {feed.get('host', 'its publisher')}, fetched by the global intelligence workspace.",
            facts=facts))
    for model in models:
        facts = (("model", model["model"]), ("provider", model.get("provider", "")))
        if model.get("base_model"):
            facts += (("built on", model["base_model"]),)
        members[MODELS_REGION[0]].append(Particle(
            f"model:{model['role']}", "model", str(model["role"]), str(model.get("purpose") or model["role"]),
            MODELS_REGION[0], MODELS_REGION[1],
            f"{model.get('purpose', model['role'])}: served by {model['model']}.", facts=facts))

    regions = [(key, len(members[key])) for key, _title, _cap in order if members.get(key)]
    total = sum(size for _key, size in regions)
    coords = fibonacci_sphere(total)
    patches: dict[str, list[int]] = {}
    if total:
        _partition(list(range(total)), coords, regions, patches)

    particles: list[Particle] = []
    region_points: dict[str, list[int]] = {}
    for key, _size in regions:
        points = patches[key]
        centre = [sum(coords[i][axis] for i in points) / len(points) for axis in range(3)]
        # The hub sits at the heart of its region; the tools fill outwards from it.
        points = sorted(points, key=lambda i: (_distance2(coords[i], centre), i))
        start = len(particles)
        for particle, point in zip(members[key], points):
            particles.append(replace(particle, point=coords[point]))
        region_points[key] = list(range(start, len(particles)))
    placed = [particle.point for particle in particles]

    index = {particle.key: i for i, particle in enumerate(particles)}
    co_links: list[Link] = []
    area_weights: Counter[tuple[str, str]] = Counter()
    for (a, b), counts in sorted((co_use or {}).items()):
        i, j = index.get(f"tool:{a}"), index.get(f"tool:{b}")
        if i is None or j is None or i == j:
            continue
        together, sequence = int(counts.get("together", 0)), int(counts.get("sequence", 0))
        if together:
            co_links.append(Link(min(i, j), max(i, j), "together", float(together * 3 + sequence)))
        elif sequence >= SEQUENCE_MINIMUM:
            co_links.append(Link(min(i, j), max(i, j), "sequence", float(sequence)))
        else:
            continue
        ra, rb = particles[i].region, particles[j].region
        if ra != rb:
            area_weights[tuple(sorted((ra, rb)))] += together * 3 + sequence  # type: ignore[index]
    co_links.sort(key=lambda link: (-link.weight, link.a, link.b))

    hub_links = []
    for (ra, rb), weight in sorted(area_weights.items()):
        i, j = index.get(f"area:{ra}"), index.get(f"area:{rb}")
        if i is not None and j is not None:
            hub_links.append(Link(min(i, j), max(i, j), "areas", float(weight)))

    model_links = []
    roles = [i for i in region_points.get(MODELS_REGION[0], [])]
    by_model: dict[str, list[int]] = defaultdict(list)
    for i in roles:
        by_model[dict(particles[i].facts)["model"]].append(i)
    for same in by_model.values():
        model_links.extend(Link(a, b, "same_model") for a, b in zip(same, same[1:]))
    for i in roles:
        base = dict(particles[i].facts).get("built on")
        if base and by_model.get(base):
            model_links.append(Link(min(i, by_model[base][0]), max(i, by_model[base][0]), "built_on"))

    bridge_links = []
    reader = index.get("tool:intelligence_refresh")
    feed_points = region_points.get(FEEDS_REGION[0], [])
    if reader is not None and feed_points:
        nearest = min(feed_points, key=lambda i: _distance2(placed[i], placed[reader]))
        bridge_links.append(Link(min(nearest, reader), max(nearest, reader), "reads"))

    area_links = []
    for key, points in region_points.items():
        area_links.extend(Link(a, b, "area", weight) for a, b, weight in _spanning_links(points, placed))

    links: list[Link] = []
    seen: set[tuple[int, int]] = set()
    for link in itertools.chain(co_links, model_links, bridge_links, hub_links, area_links):
        pair = (link.a, link.b)
        if pair in seen:
            continue
        seen.add(pair)
        links.append(link)
        if len(links) >= max_links:
            break

    kept: dict[str, dict[str, Any]] = {}
    for name, record in (records or {}).items():
        if f"tool:{name}" in index:
            kept[f"tool:{name}"] = dict(record)
    for name, record in (feed_health or {}).items():
        if f"feed:{name}" in index:
            kept[f"feed:{name}"] = dict(record)
    return Constellation(tuple(particles), tuple(links), kept, {
        "tools": len(tools), "co_use_pairs": len(co_use or {}), "target": TARGET_PARTICLES,
        "sequence_window_seconds": SEQUENCE_WINDOW, "sequence_minimum": SEQUENCE_MINIMUM,
        "links_before_cap": len(seen) if len(links) < max_links else None,
    })


def from_runtime(runtime: Any) -> Constellation:
    """Everything real the runtime offers. Each source is optional."""
    tools = tool_inventory(runtime.registry)
    try:
        feeds = feed_inventory() if getattr(runtime, "intelligence", None) is not None else []
    except Exception:
        log_swallowed(_log, "listing intelligence feeds")
        feeds = []
    router = getattr(runtime, "router", None)
    models = model_inventory(getattr(router, "config", None))
    events = getattr(runtime, "events", None)
    co_use = read_co_use(events.path) if getattr(events, "path", None) else {}
    understanding = getattr(runtime, "tool_understanding", None) or getattr(runtime.registry, "understanding", None)
    records = read_track_records(understanding.path) if getattr(understanding, "path", None) else {}
    health = read_feed_health(getattr(runtime, "intelligence", None))
    return build(tools, feeds=feeds, models=models, co_use=co_use, records=records, feed_health=health)


# --------------------------------------------------------------- explaining

def record_line(record: Mapping[str, Any] | None, kind: str = "tool") -> str:
    """One plain line of track record."""
    if not record:
        return "Not used yet." if kind == "tool" else "No fetch recorded yet." if kind == "feed" else ""
    if kind == "feed":
        attempts, ok = int(record.get("attempts", 0)), int(record.get("successes", 0))
        return f"Last {attempts} fetches: {ok} worked, {attempts - ok} failed."
    calls, failed = int(record.get("calls", 0)), int(record.get("failed", 0))
    attempted = calls - int(record.get("refused", 0))
    share = f" ({round(100 * failed / attempted)}%)" if attempted else ""
    text = f"{calls} calls · {failed} failed{share} · average {_duration(int(record.get('avg_ms', 0)))}"
    if record.get("refused"):
        text += f" · {record['refused']} refused by the safety rules"
    return text


def _duration(ms: int) -> str:
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s"


def describe_link(link: Link) -> str:
    return {"together": "ran in the same turn", "sequence": "ran back to back",
            "area": "same area", "areas": "their tools work together",
            "same_model": "same model", "built_on": "built on it", "reads": "fetches it"}.get(link.kind, link.kind)


def explain(constellation: Constellation, key: str, runtime: Any = None) -> str:
    """A plain explanation of one particle for the chat."""
    i = constellation.index(key)
    if i is None:
        return "That part is no longer on the sphere."
    particle = constellation.particles[i]
    record = constellation.records.get(key)
    lines: list[str] = []
    if particle.kind == "tool" and runtime is not None:
        from app import self_knowledge

        text = self_knowledge.explain_tool_answer(runtime, particle.name)
        if text:
            lines.append(text)
    if not lines:
        lines.append(f"{particle.title} ({particle.region_title})")
        if particle.description:
            lines.append(particle.description)
        lines.extend(f"{name.capitalize()}: {value}" for name, value in particle.facts if value and name != "icon")
        if particle.kind in {"tool", "feed"}:
            lines.append("Track record: " + record_line(record, particle.kind))
    if particle.kind == "area":
        from app import capabilities

        capability = next((cap for cap in capabilities.CATALOG if cap.key == particle.name), None)
        if capability is not None:
            members = [constellation.particles[j].name for j in constellation.regions().get(particle.region, [])
                       if constellation.particles[j].kind == "tool"]
            lines.append(capability.how)
            if capability.examples:
                lines.append("Try: \"" + capability.examples[0] + "\"")
            lines.append(f"{len(members)} tools in this area" + (": " + ", ".join(members[:24]) if members else "."))
    partners = constellation.partners(key)
    if partners:
        lines.append("Often works with: " + ", ".join(
            f"{other.name} ({describe_link(link)}, {int(link.weight)}x)" for other, link in partners))
    return "\n".join(lines)


def summary(constellation: Constellation) -> str:
    """One line for logs and docs: what the sphere is made of."""
    counts = constellation.counts()
    links = constellation.link_counts()
    return (f"{len(constellation.particles)} particles ({json.dumps(counts, sort_keys=True)}), "
            f"{len(constellation.links)} links ({json.dumps(links, sort_keys=True)})")
