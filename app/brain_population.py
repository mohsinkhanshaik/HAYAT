"""The Brain 5D neuron population: the brain's parts multiplied, every added neuron a real stored unit.

History. The brain's own neurons are software parts: 21 subsystems and the component neurons of
app/brain_components (tools, capability areas, intent routes, model roles, specialists, risk tiers,
services) -- 774 on 2026-11-08. The owner asked for six times that (4,644), then ten times the new
count (46,440 = 60 × 774). They are not the language model's weights: those cannot be multiplied
without retraining the model, and nothing here claims to show or grow them.

Every added neuron is one unit HAYAT really stores and uses, filled in this order (most cognitive first):

* memory -- a memory in the memory graph (data/memory/agent.db);
* claim -- a fact research recorded, with its support status and confidence (knowledge.db);
* knowledge -- an entity of the knowledge graph, grouped into communities found from its own synapses;
* answer -- a synthesised research answer; investigation -- one research run; source -- a document read;
  evidence -- an excerpt that supports or disputes a claim;
* lesson, procedure, task, step -- what was tried, what worked, and the steps taken;
* topic -- something HAYAT queued to learn; situation -- what the owner was doing;
* turn -- one message of a conversation; tool experience -- one recorded tool call (the record tool
  judgment learns from).

Synapses are the links those stores already record (memory links, a claim naming an entity, evidence
for a claim from a source, a source found by an investigation, a step of a task, a reply that recalled
a memory, the next message of a conversation…), at most 12 per neuron from each kind of link: never
all-to-all. Activity is recorded use. When the stores hold fewer units than the target, the population
is smaller and says by how much; nothing is padded.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

#: 6 × (2026-11-08), 10 × that, then 10 × again (2026-11-09): 600 times the brain's 774 parts and subsystems.
#: The stores may hold fewer real units than that; the population then says how many short (nothing is padded).
MULTIPLE = 600
#: Then (2026-11-09, latest) fifteen times the population actually built at that point: 15 × 158,974. The target is
#: that number itself, not a multiple of the parts, because it was asked of the real count.
#: Then thirty times the 5,469,525 that had been reached (2026-11-09, later still): 164,085,750. HAYAT runs two
#: models (the answer writer, which also sees and escalates, and the memory embedder), both already counted, so
#: the real total stops near 5.5 million; the gap is shown, never filled.
REQUESTED_TOTAL = 30 * 5_469_525
#: The brain's subsystem nodes (app/brain_mesh.BrainMesh.NODES); counted in the base.
SUBSYSTEMS = 21
#: Kinds in filling order: (kind, home subsystem in app/brain_layout.LAYOUT, plain name, what one is).
KINDS = (
    ("memory", "memory", "memory", "a memory in the memory graph"),
    ("claim", "dna", "claim", "a fact research recorded, with its support status and confidence"),
    ("knowledge", "research", "knowledge entity", "a thing the knowledge graph knows about"),
    ("answer", "research", "research answer", "an answer research synthesised from its sources"),
    ("investigation", "inquiry", "investigation", "one research run: a question and the sources it read"),
    ("source", "network", "source", "a document HAYAT read"),
    ("evidence", "verifier", "evidence", "an excerpt from a source that supports or disputes a claim"),
    ("lesson", "learning", "lesson", "what a finished task taught: what worked, what failed"),
    ("procedure", "learning", "procedure", "a learned way of doing something, with its success record"),
    ("task", "planner", "task", "a task HAYAT carried out"),
    ("step", "tools", "task step", "one step of a task: the tool used and whether it was verified"),
    ("topic", "learning", "learning topic", "something queued for HAYAT to learn"),
    ("situation", "context", "situation", "what the owner was doing, from the situation model"),
    ("turn", "interface", "conversation turn", "one message of a conversation"),
    ("tool experience", "tool_judgment", "tool experience", "one recorded tool call, the record tool judgment learns from"),
    # 2026-11-09, ten times again: the rest of what HAYAT records about itself and the world.
    ("fact revision", "dna", "fact revision", "a change to what a claim's evidence says: support status and confidence"),
    ("decision", "authority", "permission decision", "one decision of the authority kernel: allowed, asked or refused"),
    ("world object", "network", "world object", "a confirmed world situation Global Intelligence is following"),
    ("feed check", "perception", "feed check", "one check of a public feed: whether it answered and how much it gave"),
    ("own code", "cortex", "own code file", "one file of HAYAT's own code, as the code monitor records it"),
    ("signal", "observability", "signal", "one event on HAYAT's internal event log: a turn, a tool, a task step"),
    ("own function", "cortex", "own function", "one function or class in HAYAT's own code: a working part of itself"),
    # The language models' own neurons: counted from their files, drawn from a sample (app/model_anatomy.py).
    ("model neuron", "dual_brain", "language-model neuron", "one feed-forward neuron of the model that writes HAYAT's answers"),
    ("embedding neuron", "memory", "memory-model neuron", "one feed-forward neuron of the model that turns memories into vectors"),
)
HOMES = {kind: home for kind, home, _name, _what in KINDS}
NAMES = {kind: name for kind, _home, name, _what in KINDS}
WHAT = {kind: what for kind, _home, _name, what in KINDS}
KIND_CODES = {kind: code for code, (kind, *_rest) in enumerate(KINDS)}
#: The sparse wiring budget: at most this many synapses per neuron from each kind of link.
MAX_DEGREE = 12
#: A built population is reused this long: agent.db changes every turn, the structure much more slowly.
REBUILD_SECONDS = 600
_JUNK = re.compile(r"^(?:on|in|at|the|a|an|of|for|to|and|or|by|with|from|as|is|was|this|that|these|those|it|its|"
                   r"their|our|we|you|he|she|they|i|if|when|then|than|but|not|no|yes|all|some|any|more|most|"
                   r"new|other|such|also|after|before|during|since|until|about)\b", re.IGNORECASE)
#: Sentence starters, RFC 2119 keywords and record labels that entity extraction picked up as "names".
_NOT_THINGS = frozenset("""how what why where who which whose however here there once rather note form number
must should shall may might will would can could required recommended optional final task action actions
first second third one two three yes okay ok please thanks thank figure table section appendix example
examples step steps result results summary overview introduction conclusion today yesterday tomorrow
now later also still just only even both each every many much few several various many other others
see use using used make made get got set let new old good great best better more less very well
your yours my mine his her hers our ours their theirs me him them us""".split())
_MONTHS = re.compile(r"^(?:january|february|march|april|may|june|july|august|september|october|november|december)$",
                     re.IGNORECASE)


_DEFINITION = re.compile(r"^\s*(?:async\s+)?(def|class)\s+([A-Za-z_]\w*)")


def _epoch(value: Any) -> float:
    if not value:
        return math.nan
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment.timestamp()
    except ValueError:
        return math.nan


def _clip(text: Any, size: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[:size - 1].rstrip() + "…"


def plausible_entity(name: str) -> bool:
    """Leave out extraction fragments ("On July", "the", "2019") that are not things to know about."""
    text = " ".join(str(name or "").split())
    if len(text) < 3 or len(text) > 80 or not re.search(r"[A-Za-z]", text):
        return False
    if _JUNK.match(text) or _MONTHS.match(text) or text.casefold() in _NOT_THINGS:
        return False
    return not re.fullmatch(r"[\d\W_]+", text)


@dataclass
class Population:
    """Neurons as parallel arrays (tens of thousands of them), with their synapses as index pairs."""
    ids: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    uses: np.ndarray = field(default_factory=lambda: np.zeros(0))
    last_used: np.ndarray = field(default_factory=lambda: np.zeros(0))
    evidence: np.ndarray = field(default_factory=lambda: np.zeros(0))
    kind_codes: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int8))
    positions: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    links: np.ndarray = field(default_factory=lambda: np.zeros((0, 2), dtype=np.int64))
    link_weights: np.ndarray = field(default_factory=lambda: np.zeros(0))
    link_kinds: list[str] = field(default_factory=list)
    fired: dict[int, float] = field(default_factory=dict)
    #: The language models' own neurons (app/model_anatomy.py), counted from their files and addressed by layer,
    #: expert and unit arithmetically: millions of them without a string or position each.
    blocks: list[dict] = field(default_factory=list)
    base: int = 0
    target: int = 0
    available: dict[str, int] = field(default_factory=dict)
    build_ms: float = 0.0
    built_at: float = 0.0

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def total(self) -> int:
        """All neurons in the brain: the base parts, the stored units, and the language models' own neurons."""
        return self.base + len(self.ids) + sum(block["count"] for block in self.blocks)

    def block_of(self, index: int) -> tuple[dict, int] | None:
        """For an index past the stored units: its model block and its place in it."""
        local = index - len(self.ids)
        for block in self.blocks:
            if 0 <= local < block["count"]:
                return block, local
            local -= block["count"]
        return None

    @staticmethod
    def address(block: dict, local: int) -> tuple[int, int | None, int]:
        """(layer, expert or None for the shared expert / a dense layer, unit) of one model neuron."""
        experts, units, shared = block["experts"], block["per_expert"], block["shared"]
        per_layer = experts * units + shared if experts else units
        layer, rest = divmod(local, per_layer)
        if experts and rest < experts * units:
            expert, unit = divmod(rest, units)
            return layer, expert, unit
        return layer, None, rest - experts * units if experts else rest

    @property
    def multiple(self) -> float:
        return self.total / self.base if self.base else 0.0

    def counts(self) -> dict[str, Any]:
        """Counted once per population: the legend asks every frame, and 45,000 kinds plus 70,000 synapse
        kinds cost 17 ms each time."""
        cached = self.__dict__.get("_counts")
        if cached is not None and cached[0] == (len(self.ids), len(self.link_kinds)):
            return dict(cached[1])
        value = self._count()
        self.__dict__["_counts"] = ((len(self.ids), len(self.link_kinds)), value)
        return dict(value)

    def _count(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for kind in self.kinds:
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for block in self.blocks:
            by_kind[block["kind"]] = by_kind.get(block["kind"], 0) + block["count"]
        by_link: dict[str, int] = {}
        for kind in self.link_kinds:
            by_link[kind] = by_link.get(kind, 0) + 1
        model = sum(block["count"] for block in self.blocks)
        return {"base": self.base, "population": len(self.ids) + model, "stored_units": len(self.ids),
                "model_neurons": model, "total": self.total, "target": self.target,
                "multiple": round(self.multiple, 3), "short_by": max(0, self.target - self.total),
                "by_kind": by_kind, "synapses": len(self.link_kinds), "by_synapse": by_link,
                "available": dict(self.available),
                "models": [{key: block[key] for key in ("name", "kind", "count", "layers", "experts", "per_expert", "shared")}
                           for block in self.blocks]}

    def neighbours(self, index: int) -> list[tuple[int, float, str]]:
        if not len(self.links) or index >= len(self.ids):
            return []
        rows = np.nonzero((self.links[:, 0] == index) | (self.links[:, 1] == index))[0]
        out = []
        for row in rows.tolist():
            a, b = int(self.links[row, 0]), int(self.links[row, 1])
            out.append((b if a == index else a, float(self.link_weights[row]), self.link_kinds[row]))
        return sorted(out, key=lambda item: -item[1])

    def describe(self, index: int) -> str:
        """The neuron in words: what it is, where it comes from, its recorded use and its strongest synapses."""
        if index >= len(self.ids):
            found = self.block_of(index)
            if found is None:
                raise IndexError(index)
            block, local = found
            layer, expert, unit = self.address(block, local)
            where = (f"layer {layer + 1} of {block['layers']}, " + (f"expert {expert + 1} of {block['experts']}, unit {unit + 1} "
                     f"of {block['per_expert']}" if expert is not None else
                     (f"shared expert, unit {unit + 1} of {block['shared']}" if block['experts'] else
                      f"unit {unit + 1} of {block['per_expert']}")))
            role = ("writes HAYAT's answers" if block["kind"] == "model neuron" else "turns memories into meaning vectors for recall")
            return (f"{block['name']} · {where}\n{NAMES.get(block['kind'], block['kind']).capitalize()}: one feed-forward neuron of the "
                    f"model that {role}. {block['count']:,} such neurons in {block['layers']} layers"
                    + (f"; {block['experts']} experts per layer, {block['used']} used for each word" if block['experts'] else "")
                    + ".\nCounted from the model file's header. HAYAT knows where this neuron is, not when it fires: "
                      "the model server exposes no activations.")
        kind, group = self.kinds[index], self.groups[index]
        lines = [self.labels[index]]
        if kind == "memory":
            lines.append(f"Memory neuron · {group} memory · {self.ids[index]} in the memory graph")
        elif kind == "knowledge":
            lines.append(f"Knowledge neuron · {group} · {int(self.evidence[index])} verified claim"
                         f"{'s' if self.evidence[index] != 1 else ''} name it")
        else:
            lines.append(f"{NAMES[kind].capitalize()} neuron · {group} · {WHAT[kind]} ({self.ids[index]})")
        if self.anchors and self.anchors[index]:
            lines.append(f"Belongs to the part {self.anchors[index]}.")
        uses, last = int(self.uses[index]), self.last_used[index]
        lines.append(f"Recorded use: {uses:,}" + (time.strftime(", last %Y-%m-%d", time.localtime(last))
                                                   if not math.isnan(last) else ", never used yet"))
        if index in self.fired:
            lines.append(time.strftime("Recalled for a reply at %Y-%m-%d %H:%M.", time.localtime(self.fired[index])))
        if self.texts[index]:
            lines.append("")
            lines.append(self.texts[index])
        near = self.neighbours(index)
        if near:
            lines.append("")
            lines.append(f"{len(near)} synapses; strongest:")
            lines.extend(f"  {self.labels[other][:70]} ({kind_}, {weight:.2g})" for other, weight, kind_ in near[:6])
        return "\n".join(lines)


# ------------------------------------------------------------------------------------- reading

def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2)


def _usable(path: Path | None) -> bool:
    try:
        return bool(path) and Path(str(path)).is_file()
    except (OSError, ValueError):  # model names such as "hayat:latest" travel in the same dict and are not files
        return False


def read_memories(path: Path | None, limit: int) -> tuple[list[dict[str, Any]], list[tuple[int, int, float, str]], dict[int, float]]:
    """Memories (most important and most used first), their synapses and the replies that recalled them."""
    if not _usable(path) or limit <= 0:
        return [], [], {}
    with _connect(path) as db:
        uses: dict[int, float] = {}
        last: dict[int, float] = {}
        synapses = []
        for source, target, relation, weight, use_count, reinforced in db.execute(
                "SELECT source_id, target_id, relation, weight, use_count, last_reinforced_at FROM memory_links"):
            synapses.append((int(source), int(target), float(weight or 0), str(relation)))
            for node in (int(source), int(target)):
                uses[node] = uses.get(node, 0.0) + float(use_count or 0)
                stamp = _epoch(reinforced)
                if not math.isnan(stamp):
                    last[node] = max(last.get(node, 0.0), stamp)
        rows = []
        for memory_id, memory_type, content, importance, metadata, stamp, task_id in db.execute(
                "SELECT id, memory_type, content, importance, metadata_json, timestamp, task_id FROM memories"):
            try:
                meta = json.loads(metadata or "{}")
            except ValueError:
                meta = {}
            used = uses.get(int(memory_id), 0.0) + float(meta.get("successful_uses") or 0) + float(meta.get("failed_uses") or 0)
            rows.append({"id": int(memory_id), "group": str(memory_type or "memory"), "text": " ".join(str(content or "").split()),
                         "importance": float(importance or 0), "uses": used, "task": str(task_id or ""),
                         "last": last.get(int(memory_id), math.nan), "created": _epoch(stamp)})
    rows.sort(key=lambda row: (-row["importance"], -row["uses"], -(row["created"] if not math.isnan(row["created"]) else 0)))
    return rows[:limit], synapses, recalled(path)


def recalled(path: Path | None, turns: int = 50) -> dict[int, float]:
    """Memory ids the last replies recalled, with when (the reply's recorded recalled_memory_ids)."""
    fired: dict[int, float] = {}
    if not _usable(path):
        return fired
    try:
        with _connect(path) as db:
            for metadata, stamp in db.execute("SELECT metadata_json, timestamp FROM conversations WHERE role='assistant' "
                                              "AND metadata_json LIKE '%recalled_memory_ids%' ORDER BY id DESC LIMIT ?", (turns,)):
                for memory_id in (json.loads(metadata or "{}").get("recalled_memory_ids") or []):
                    fired.setdefault(int(memory_id), _epoch(stamp))
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return fired


def read_entities(path: Path | None, limit: int) -> tuple[list[dict[str, Any]], list[tuple[int, int, float, str]]]:
    """Knowledge entities ranked by evidence and use, with relationships and shared-claim synapses among them."""
    if not _usable(path) or limit <= 0:
        return [], []
    with _connect(path) as db:
        rows = []
        for entity_id, name, kind, claims, retrievals, last in db.execute(
                "SELECT e.id, e.name, e.entity_type, COUNT(DISTINCT c.id), "
                "COALESCE(SUM(COALESCE(c.retrieval_count,0) + COALESCE(c.successful_uses,0)),0), MAX(c.last_used) "
                "FROM entities e JOIN claim_entities ce ON ce.entity_id = e.id JOIN claims c ON c.id = ce.claim_id "
                "GROUP BY e.id"):
            if plausible_entity(name):
                rows.append({"id": int(entity_id), "label": " ".join(str(name).split()), "group": str(kind or "concept"),
                             "claims": int(claims), "uses": float(retrievals or 0), "last": _epoch(last)})
        rows.sort(key=lambda row: (-(row["claims"] + 2 * row["uses"]), row["label"].casefold()))
        rows = rows[:limit]
        chosen = {row["id"] for row in rows}
        pairs: dict[tuple[int, int], list[float]] = {}
        for subject, obj, confidence in db.execute("SELECT subject_entity_id, object_entity_id, confidence FROM relationships"):
            if subject in chosen and obj in chosen and subject != obj:
                key = (min(subject, obj), max(subject, obj))
                pairs.setdefault(key, []).append(float(confidence or .5))
        synapses = [(a, b, sum(values), "relationship") for (a, b), values in pairs.items()]
        shared: dict[tuple[int, int], int] = {}
        claim_members: dict[str, list[int]] = {}
        for claim_id, entity_id in db.execute("SELECT claim_id, entity_id FROM claim_entities"):
            if entity_id in chosen:
                claim_members.setdefault(claim_id, []).append(int(entity_id))
        for members in claim_members.values():
            members = sorted(set(members))[:8]
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if (a, b) not in pairs:
                        shared[(a, b)] = shared.get((a, b), 0) + 1
        synapses += [(a, b, float(count), "same claim") for (a, b), count in shared.items()]
    return rows, synapses


def _rows(path: Path | None, sql: str, limit: int, args: tuple = ()) -> list[tuple]:
    """Rows from a store, or none when the store or its table is missing."""
    if not _usable(path) or limit <= 0:
        return []
    try:
        with _connect(path) as db:
            return db.execute(sql + " LIMIT ?", (*args, int(limit))).fetchall()
    except sqlite3.Error:
        return []


def _pairs(path: Path | None, sql: str) -> list[tuple]:
    if not _usable(path):
        return []
    try:
        with _connect(path) as db:
            return db.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def _top_groups(values: list[str], keep: int = 12) -> list[str]:
    """Each value, or "other" for the ones outside the `keep` most common: a dozen clusters, not hundreds."""
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    top = set(sorted(counts, key=lambda value: -counts[value])[:keep])
    return [value if value in top else "other" for value in values]


# ------------------------------------------------------------------------------------ placing

def _centre(name: str) -> tuple[float, float, float]:
    from app.brain_layout import LAYOUT

    return LAYOUT[name]


def place(kinds: list[str], groups: list[str], ids: list[str], *, shell=(1.22, .88, 1.08)) -> np.ndarray:
    """Hierarchical positions: each kind round its home region, each group a lobe of that region, members on
    a golden-angle ball by rank; all inside the shell. Vectorised, so tens of thousands place in milliseconds."""
    positions = np.zeros((len(ids), 3))
    golden = math.pi * (3 - math.sqrt(5))
    by_cluster: dict[tuple[str, str], list[int]] = {}
    for index, (kind, group) in enumerate(zip(kinds, groups)):
        by_cluster.setdefault((HOMES.get(kind, "memory"), kind, group), []).append(index)
    per_home: dict[str, list[tuple[str, str, str]]] = {}
    for key in sorted(by_cluster, key=lambda key: -len(by_cluster[key])):
        per_home.setdefault(key[0], []).append(key)
    for home_name, clusters in per_home.items():
        heart = np.array(_centre(home_name)) * .78
        total = sum(len(by_cluster[key]) for key in clusters)
        for rank, key in enumerate(clusters):
            members = np.array(by_cluster[key])
            share = len(members) / max(1, total)
            if rank == 0:
                centre = heart
            else:
                t = (rank - .5) / max(1, len(clusters) - 1)
                z, theta = 1 - 2 * t, rank * golden
                ring = math.sqrt(max(0.0, 1 - z * z))
                centre = heart + (.2 + .14 * t) * np.array([ring * math.cos(theta), z * .8, ring * math.sin(theta)])
            radius = .05 + .2 * math.sqrt(share)
            order = np.arange(len(members))
            t = (order + .5) / len(members)
            r = radius * t ** (1 / 3)
            theta, z = order * golden, 1 - 2 * t
            ring = np.sqrt(np.maximum(0.0, 1 - z * z))
            positions[members] = centre + r[:, None] * np.stack([ring * np.cos(theta), z, ring * np.sin(theta)], axis=1)
    axes = np.array(shell) * .94
    reach = np.sqrt(((positions / axes) ** 2).sum(axis=1))
    outside = reach > 1
    positions[outside] /= reach[outside][:, None]
    return positions


# ------------------------------------------------------------------------------------- building

_cache_lock = threading.Lock()
_cache: dict[tuple, Population] = {}


def build(base: int, memory_path: Path | None, knowledge_path: Path | None, *, multiple: int | None = None,
          stores: dict[str, Path | None] | None = None, force: bool = False) -> Population:
    """The population that brings the brain to `multiple` × `base` neurons, from real stored units only.

    `stores` may name the other stores: tool_understanding, experience, learning (paths, or None).
    """
    started = time.perf_counter()
    stores = dict(stores or {})
    key = (base, multiple, str(memory_path), str(knowledge_path), tuple(sorted((k, str(v)) for k, v in stores.items())))
    stamps = tuple(Path(path).stat().st_mtime if _usable(path) else 0
                   for path in (memory_path, knowledge_path, *stores.values()))
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and not force and (getattr(cached, "_stamps", None) == stamps
                                              or time.time() - cached.built_at < REBUILD_SECONDS):
        cached.fired = recalled(memory_path)  # activity is cheap to refresh; the structure is not
        return cached
    population = _build(base, memory_path, knowledge_path, multiple, stores)
    population._stamps = stamps  # type: ignore[attr-defined]
    population.build_ms = round((time.perf_counter() - started) * 1000, 1)
    population.built_at = time.time()
    with _cache_lock:
        _cache.clear()
        _cache[key] = population
    return population


def _build(base: int, memory_path, knowledge_path, multiple: int | None, stores: dict[str, Path | None]) -> Population:
    target = base * multiple if multiple else max(REQUESTED_TOTAL, base * MULTIPLE)
    remaining = max(0, target - base)
    population = Population(base=base, target=target)
    index_of: dict[str, int] = {}
    uses: list[float] = []
    last: list[float] = []
    weight: list[float] = []

    def add(kind: str, native: Any, label: str, group: str, *, text: str = "", used: float = 0.0, when: float = math.nan,
            strength: float = 0.0, anchor: str = "") -> None:
        key = f"{kind}:{native}"
        if key in index_of:
            return
        index_of[key] = len(population.ids)
        population.ids.append(key)
        population.kinds.append(kind)
        population.groups.append(group)
        population.labels.append(_clip(label, 90) or key)
        population.texts.append(text)
        population.anchors.append(anchor)
        uses.append(float(used or 0))
        last.append(when)
        weight.append(float(strength or 0))

    def room() -> int:
        return max(0, remaining - len(population.ids))

    tools_db, experience_db, learning_db = stores.get("tool_understanding"), stores.get("experience"), stores.get("learning")
    # Memories.
    memories, memory_synapses, fired = read_memories(memory_path, room())
    for row in memories:
        add("memory", row["id"], row["text"], row["group"], text=row["text"][:600], used=row["uses"], when=row["last"],
            strength=row["importance"])
    # Claims: supported and used first.
    claims = _rows(knowledge_path, "SELECT id, claim_text, status, confidence, retrieval_count, successful_uses, last_used, "
                                   "COALESCE(last_verified, updated_at), run_id FROM claims ORDER BY (status='SUPPORTED') DESC, "
                                   "COALESCE(retrieval_count,0)+COALESCE(successful_uses,0) DESC, confidence DESC", room())
    for claim_id, text, status, confidence, retrievals, successes, used_at, verified, _run in claims:
        add("claim", claim_id, text, str(status or "unknown").lower().replace("_", " "),
            text=f"{_clip(text, 560)}\nStatus: {str(status or '').lower().replace('_', ' ')} · confidence {float(confidence or 0):.2f}",
            used=(retrievals or 0) + (successes or 0), when=_epoch(used_at) if used_at else _epoch(verified),
            strength=float(confidence or 0))
    # Knowledge entities.
    entities, entity_synapses = read_entities(knowledge_path, room())
    for row in entities:
        add("knowledge", row["id"], row["label"], row["group"], used=row["uses"], when=row["last"], strength=row["claims"])
    # Research answers, investigations, sources, evidence.
    for knowledge_id, query, content, confidence, freshness, retrievals, used_at, updated in _rows(
            knowledge_path, "SELECT id, query, content, confidence, freshness_class, retrieval_count, last_used, updated_at "
                            "FROM knowledge ORDER BY COALESCE(retrieval_count,0) DESC, updated_at DESC", room()):
        add("answer", knowledge_id, query, str(freshness or "answer"), text=_clip(content, 600), used=retrievals or 0,
            when=_epoch(used_at) if used_at else _epoch(updated), strength=float(confidence or 0))
    runs = _rows(knowledge_path, "SELECT r.id, r.query, r.level, r.completed_at, r.confidence, COUNT(s.source_id) "
                                 "FROM research_runs r LEFT JOIN run_sources s ON s.run_id = r.id GROUP BY r.id "
                                 "ORDER BY r.completed_at DESC", room())
    for run_id, query, level, completed, confidence, count in runs:
        add("investigation", run_id, query, str(level or "research"), text=f"Asked: {_clip(query, 400)}\nRead {count} sources.",
            used=count, when=_epoch(completed), strength=float(confidence or 0))
    sources = _rows(knowledge_path, "SELECT s.id, s.title, s.domain, s.source_type, s.retrieved_at, s.quality, "
                                    "(SELECT COUNT(*) FROM evidence e WHERE e.source_id = s.id) "
                                    "FROM sources s ORDER BY 7 DESC, s.retrieved_at DESC", room())
    domains = _top_groups([str(domain or "web") for _id, _title, domain, *_rest in sources])
    for (source_id, title, domain, kind, retrieved, quality, cited), group in zip(sources, domains):
        add("source", source_id, title or domain or source_id, group, text=f"{_clip(title, 200)}\n{domain or ''}",
            used=cited, when=_epoch(retrieved), strength=float(quality or 0))
    for evidence_id, excerpt, stance, strength, retrieved in _rows(
            knowledge_path, "SELECT id, excerpt, stance, strength, retrieved_at FROM evidence ORDER BY strength DESC, retrieved_at DESC",
            room()):
        add("evidence", evidence_id, excerpt, str(stance or "evidence").lower(), text=_clip(excerpt, 600),
            when=_epoch(retrieved), strength=float(strength or 0))
    # What tasks taught, learned procedures, the tasks and their steps.
    for lesson_id, at, task_id, goal, success, failure in _rows(
            experience_db, "SELECT id, at, task_id, goal, success, failure_reason FROM experiences ORDER BY at DESC", room()):
        add("lesson", lesson_id, goal, "worked" if success else "failed",
            text=_clip(goal, 300) + (f"\nWhat failed: {_clip(failure, 280)}" if failure else ""), when=_epoch(at))
    for procedure_id, name, description, successes, failures, used_at in _rows(
            memory_path, "SELECT id, name, description, success_count, failure_count, last_used_at FROM procedures ORDER BY success_count DESC",
            room()):
        add("procedure", procedure_id, name, "procedure", text=_clip(description, 500), used=(successes or 0) + (failures or 0),
            when=_epoch(used_at))
    for task_id, goal, status, created in _rows(memory_path, "SELECT id, goal, status, created_at FROM tasks ORDER BY created_at DESC",
                                                room()):
        add("task", task_id, goal, str(status or "task").lower(), text=_clip(goal, 500), when=_epoch(created))
    steps = _rows(memory_path, "SELECT id, task_id, tool, step_index, timestamp, verification_json FROM actions ORDER BY id DESC", room())
    groups = _top_groups([str(tool) for _id, _task, tool, *_rest in steps])
    for (action_id, task_id, tool, step_index, stamp, verification), group in zip(steps, groups):
        verified = '"passed": true' in str(verification or "")
        add("step", action_id, f"{tool}: step {int(step_index or 0) + 1} of a task", group, used=1 if verified else 0,
            when=_epoch(stamp), anchor=f"tool:{tool}", text="Verified." if verified else "Not verified.")
    # What HAYAT queued to learn, and what the owner was doing.
    for topic_id, topic, status, created in _rows(learning_db, "SELECT id, topic, status, created_at FROM learning_queue "
                                                                "ORDER BY priority DESC, created_at DESC", room()):
        add("topic", topic_id, topic, str(status or "queued").lower(), when=_epoch(created))
    for situation_id, at, area, text in _rows(experience_db, "SELECT id, at, area, text FROM situation_turns ORDER BY at DESC", room()):
        add("situation", situation_id, text or area, str(area or "situation"), when=_epoch(at))
    # Conversation turns, newest first.
    for turn_id, session, stamp, role, content in _rows(
            memory_path, "SELECT id, session_id, timestamp, role, content FROM conversations ORDER BY id DESC", room()):
        add("turn", turn_id, content, str(role or "turn"), text=_clip(content, 600), when=_epoch(stamp))
    # Tool experiences, newest first: each call tool judgment learned from.
    calls = _rows(tools_db, "SELECT id, tool, ok, ms, error_kind, at FROM tool_calls ORDER BY id DESC", room())
    groups = _top_groups([str(tool) for _id, tool, *_rest in calls])
    for (call_id, tool, ok, ms, error, at), group in zip(calls, groups):
        add("tool experience", call_id, f"{tool}: {'ok' if ok else error or 'failed'}, {int(ms or 0)} ms", group,
            used=1 if ok else 0, when=_epoch(at), anchor=f"tool:{tool}")
    # Everything else HAYAT records about itself and the world, newest first.
    events_db, code_db, authority_db, intelligence_db = (stores.get("events"), stores.get("code_monitor"),
                                                         stores.get("authority"), stores.get("intelligence"))
    for revision_id, claim_id, changed, old, new, old_conf, new_conf, reason in _rows(
            knowledge_path, "SELECT id, claim_id, changed_at, old_status, new_status, old_confidence, new_confidence, reason "
                            "FROM claim_history ORDER BY changed_at DESC", room()):
        add("fact revision", revision_id, f"{str(old or '?').lower()} → {str(new or '?').lower()}: {reason or ''}",
            str(new or "revision").lower().replace("_", " "),
            text=f"Confidence {float(old_conf or 0):.2f} → {float(new_conf or 0):.2f}. {_clip(reason, 300)}", when=_epoch(changed))
    decisions = _rows(authority_db, "SELECT id, at, kind, capability, decision, tier, reason FROM authority_audit ORDER BY id DESC",
                      room())
    for decision_id, at, kind, capability, decision, tier, reason in decisions:
        add("decision", decision_id, f"{kind}: {capability or ''} → {decision}", str(decision or kind or "decision").lower(),
            text=_clip(reason, 300), when=_epoch(at))
    for object_id, first_seen, last_seen, category, status, data in _rows(
            intelligence_db, "SELECT id, first_reported, last_reported, category, status, data FROM intel_objects "
                             "ORDER BY last_reported DESC", room()):
        try:
            title = json.loads(data or "{}").get("title") or object_id
        except ValueError:
            title = object_id
        add("world object", object_id, title, str(category or "world"),
            text=f"{status}: first reported {first_seen}, last {last_seen}", when=_epoch(last_seen))
    checks = _rows(intelligence_db, "SELECT id, feed, at, ok, count, error FROM feed_health ORDER BY id DESC", room())
    groups = _top_groups([str(feed) for _id, feed, *_rest in checks])
    for (check_id, feed, at, ok, count, error), group in zip(checks, groups):
        add("feed check", check_id, f"{feed}: {'answered, ' + str(count) + ' items' if ok else 'failed'}", group,
            used=1 if ok else 0, when=_epoch(at), text=_clip(error, 300))
    files = _rows(code_db, "SELECT project, path, mtime_ns, size, issue_json FROM code_files ORDER BY path", room())
    groups = _top_groups([str(path).replace("\\", "/").rsplit("/", 1)[0] if "/" in str(path).replace("\\", "/") else "."
                          for _project, path, *_rest in files])
    for (project, path, mtime, size, issues), group in zip(files, groups):
        add("own code", f"{project}/{path}", str(path), group, when=(mtime or 0) / 1e9 if mtime else math.nan,
            text=f"{int(size or 0):,} bytes" + (f"\nIssues: {_clip(issues, 300)}" if issues and issues != "null" else ""))
    signals = _rows(events_db, "SELECT id, timestamp, event_type, source, payload_json, task_id FROM events ORDER BY id DESC", room())
    groups = _top_groups([str(kind) for _id, _stamp, kind, *_rest in signals])
    for (event_id, stamp, kind, source, payload, task_id), group in zip(signals, groups):
        tool = ""
        if payload and '"tool"' in payload:
            try:
                tool = str(json.loads(payload).get("tool") or "")
            except ValueError:
                tool = ""
        add("signal", event_id, f"{kind}" + (f" · {tool}" if tool else f" · {source}"), group, when=_epoch(stamp),
            anchor=f"tool:{tool}" if tool else "")
    # Its own working parts: every function and class in its code (a line scan, not a parse: fast enough per build).
    code_root = stores.get("code_root")
    if code_root and Path(code_root).is_dir() and room():
        for path in sorted(Path(code_root).rglob("*.py")):
            if room() <= 0 or "__pycache__" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            relative = path.relative_to(Path(code_root).parent).as_posix()
            for number, line in enumerate(lines, 1):
                match = _DEFINITION.match(line)
                if match and room() > 0:
                    kind_word, name = ("class", match[2]) if match[1] == "class" else ("def", match[2])
                    add("own function", f"{relative}:{number}", f"{relative}: {kind_word} {name}", relative.rsplit("/", 1)[0],
                        when=path.stat().st_mtime, text=f"{kind_word} {name} at line {number} of {relative}.")
    # The language models' own neurons, all of them, counted from their files (no stored unit is displaced).
    models_dir = stores.get("models_dir")
    if models_dir and Path(str(models_dir)).is_dir():
        from app.model_anatomy import model_anatomy

        for kind, name in (("model neuron", stores.get("answer_model")), ("embedding neuron", stores.get("embedding_model"))):
            if not name:
                continue
            try:
                found = model_anatomy(Path(str(models_dir)), str(name))
            except (OSError, ValueError):
                found = None
            if found and found["neurons"]:
                population.blocks.append({"kind": kind, "name": str(name), "count": int(found["neurons"]),
                                          "layers": found["layers"], "experts": found["experts"],
                                          "per_expert": found["neurons_per_expert"], "shared": found["shared_neurons_per_layer"],
                                          "used": found["experts_used_per_token"], "file": found["file"]})
    population.uses, population.last_used = np.array(uses, dtype=float), np.array(last, dtype=float)
    population.evidence = np.array(weight, dtype=float)
    population.kind_codes = np.array([KIND_CODES[kind] for kind in population.kinds], dtype=np.int8)
    population.available = {NAMES[kind]: count for kind, count in Population._count(population)["by_kind"].items()}
    population.fired = {index_of[f"memory:{memory_id}"]: stamp for memory_id, stamp in fired.items()
                        if f"memory:{memory_id}" in index_of}
    _wire(population, index_of, memories, memory_synapses, entity_synapses, knowledge_path, memory_path, experience_db)
    _wire_records(population, index_of, knowledge_path, stores)
    _communities(population)
    population.positions = place(population.kinds, population.groups, population.ids)
    return population


def _wire(population, index_of, memories, memory_synapses, entity_synapses, knowledge_path, memory_path, experience_db) -> None:
    links, weights, kinds = [], [], []

    def source(rows, kind, a_kind, b_kind, *, weight_of=None):
        """One kind of link, strongest first, at most MAX_DEGREE per neuron."""
        degree: dict[int, int] = {}
        for row in sorted(rows, key=(lambda row: -(weight_of(row) if weight_of else 1.0))):
            a, b = index_of.get(f"{a_kind}:{row[0]}"), index_of.get(f"{b_kind}:{row[1]}")
            if a is None or b is None or a == b or degree.get(a, 0) >= MAX_DEGREE or degree.get(b, 0) >= MAX_DEGREE:
                continue
            degree[a], degree[b] = degree.get(a, 0) + 1, degree.get(b, 0) + 1
            links.append((a, b))
            weights.append(float(weight_of(row)) if weight_of else 1.0)
            kinds.append(kind if isinstance(kind, str) else kind(row))

    source(memory_synapses, lambda row: row[3], "memory", "memory", weight_of=lambda row: row[2])
    source(entity_synapses, lambda row: row[3], "knowledge", "knowledge", weight_of=lambda row: row[2])
    source([(memory, knowledge) for memory, knowledge in _bridges(population, index_of)], "names", "memory", "knowledge")
    source([(row["id"], row["task"]) for row in memories if row["task"]], "about task", "memory", "task")
    source(_pairs(knowledge_path, "SELECT claim_id, entity_id FROM claim_entities"), "names", "claim", "knowledge")
    source(_pairs(knowledge_path, "SELECT id, claim_id, strength FROM evidence"), "supports", "evidence", "claim",
           weight_of=lambda row: float(row[2] or .5))
    source(_pairs(knowledge_path, "SELECT id, source_id FROM evidence"), "quoted from", "evidence", "source")
    source(_pairs(knowledge_path, "SELECT knowledge_id, source_id FROM claim_evidence"), "cites", "answer", "source")
    source(_pairs(knowledge_path, "SELECT source_id, run_id, rank FROM run_sources"), "found by", "source", "investigation",
           weight_of=lambda row: 1.0 / (1 + float(row[2] or 0)))
    source(_pairs(knowledge_path, "SELECT id, run_id FROM claims WHERE run_id IS NOT NULL"), "learned in", "claim", "investigation")
    source(_pairs(knowledge_path, "SELECT id, run_id FROM knowledge WHERE run_id IS NOT NULL"), "learned in", "answer", "investigation")
    source(_pairs(memory_path, "SELECT id, task_id FROM actions"), "step of", "step", "task")
    source(_pairs(experience_db, "SELECT id, task_id FROM experiences WHERE task_id IS NOT NULL"), "lesson of", "lesson", "task")
    turns = _pairs(memory_path, "SELECT id, session_id, metadata_json FROM conversations ORDER BY id")
    previous: dict[str, int] = {}
    following, recall = [], []
    for turn_id, session, metadata in turns:
        if session in previous:
            following.append((previous[session], turn_id))
        previous[session] = turn_id
        if metadata and "recalled_memory_ids" in metadata:
            try:
                recall += [(turn_id, memory_id) for memory_id in json.loads(metadata).get("recalled_memory_ids") or []]
            except ValueError:
                pass
    source(following, "followed by", "turn", "turn")
    source(recall, "recalled", "turn", "memory")
    population.links = np.array(links, dtype=np.int64).reshape(-1, 2)
    population.link_weights = np.array(weights, dtype=float)
    population.link_kinds = kinds


def _wire_records(population: Population, index_of: dict[str, int], knowledge_path, stores: dict) -> None:
    """Links among the records added on 2026-11-09: a revision to its claim, a signal to its task, each feed check to
    the one before it, each code file to the previous one in its folder. Appended to the existing synapses."""
    links, weights, kinds = [], [], []

    def link(a_key: str, b_key: str, kind: str) -> None:
        a, b = index_of.get(a_key), index_of.get(b_key)
        if a is not None and b is not None and a != b:
            links.append((a, b))
            weights.append(1.0)
            kinds.append(kind)

    for revision_id, claim_id in _pairs(knowledge_path, "SELECT id, claim_id FROM claim_history"):
        link(f"fact revision:{revision_id}", f"claim:{claim_id}", "revises")
    degree: dict[str, int] = {}
    for event_id, task_id in _pairs(stores.get("events"), "SELECT id, task_id FROM events WHERE task_id IS NOT NULL"):
        if degree.get(task_id, 0) < MAX_DEGREE:  # a long task has hundreds of signals: keep a few per task
            degree[task_id] = degree.get(task_id, 0) + 1
            link(f"signal:{event_id}", f"task:{task_id}", "during task")
    previous: dict[str, Any] = {}
    for check_id, feed in _pairs(stores.get("intelligence"), "SELECT id, feed FROM feed_health ORDER BY id"):
        if feed in previous:
            link(f"feed check:{previous[feed]}", f"feed check:{check_id}", "next check")
        previous[feed] = check_id
    folders: dict[str, str] = {}
    for project, path in _pairs(stores.get("code_monitor"), "SELECT project, path FROM code_files ORDER BY path"):
        folder = str(path).replace("\\", "/").rsplit("/", 1)[0] if "/" in str(path).replace("\\", "/") else "."
        key = f"own code:{project}/{path}"
        if (project, folder) in folders:
            link(folders[(project, folder)], key, "same folder")
        folders[(project, folder)] = key
    # Each function or class to the code file it lives in.
    files = {key.split("/", 1)[1]: key for key, kind in zip(population.ids, population.kinds)
             if kind == "own code" and "/" in key}
    for key, kind in zip(population.ids, population.kinds):
        if kind == "own function":
            path = key.split(":", 1)[1].rsplit(":", 1)[0]
            if path in files:
                link(key, files[path], "part of file")
    if links:
        population.links = np.concatenate([population.links, np.array(links, dtype=np.int64).reshape(-1, 2)])
        population.link_weights = np.concatenate([population.link_weights, np.array(weights, dtype=float)])
        population.link_kinds = population.link_kinds + kinds


#: Knowledge communities given their own cluster; the rest share one.
COMMUNITIES = 24


def _communities(population: Population, rounds: int = 12) -> None:
    """Group knowledge neurons by the communities their own synapses form (weighted label propagation).

    Entities that research related or named in the same claims end up together; each community is named
    after its most-evidenced member ("around Python"). Deterministic: ties go to the smaller label.
    """
    knowledge = [index for index, kind in enumerate(population.kinds) if kind == "knowledge"]
    if not knowledge:
        return
    members = set(knowledge)
    adjacency: dict[int, list[tuple[int, float]]] = {index: [] for index in knowledge}
    for (a, b), weight in zip(population.links.tolist(), population.link_weights.tolist()):
        if a in members and b in members:
            adjacency[a].append((b, weight))
            adjacency[b].append((a, weight))
    label = {index: index for index in knowledge}
    order = sorted(knowledge, key=lambda index: -population.evidence[index])
    for _round in range(rounds):
        changed = 0
        for index in order:
            if not adjacency[index]:
                continue
            votes: dict[int, float] = {}
            for other, weight in adjacency[index]:
                votes[label[other]] = votes.get(label[other], 0.0) + math.log1p(weight)
            best = min(votes, key=lambda key: (-votes[key], key))
            if best != label[index]:
                label[index], changed = best, changed + 1
        if not changed:
            break
    groups: dict[int, list[int]] = {}
    for index in knowledge:
        groups.setdefault(label[index], []).append(index)
    ranked = sorted(groups.values(), key=lambda group: -len(group))
    for rank, group in enumerate(ranked):
        if rank < COMMUNITIES and len(group) >= 3:
            head = max(group, key=lambda index: (population.evidence[index], -index))
            name = "around " + population.labels[head]
        elif len(group) == 1 and not adjacency[group[0]]:
            name = "not yet connected"   # known from its claims, but no synapse to another neuron yet
        else:
            name = "smaller topics"
        for index in group:
            population.groups[index] = name


def _bridges(population: Population, index_of: dict[str, int], per_memory: int = 3) -> list[tuple[Any, Any]]:
    """(memory id, entity id) for each knowledge entity a memory names (whole-word match, longest names first)."""
    names: dict[str, str] = {}
    for key, kind, label in zip(population.ids, population.kinds, population.labels):
        if kind == "knowledge" and len(label) >= 4:
            names.setdefault(label.casefold(), key.split(":", 1)[1])
    if not names:
        return []
    first_words: dict[str, list[str]] = {}
    for name in sorted(names, key=len, reverse=True):
        first_words.setdefault(name.split()[0], []).append(name)
    bridges = []
    for key, kind, text in zip(population.ids, population.kinds, population.texts):
        if kind != "memory":
            continue
        lowered = text.casefold()
        words = set(re.findall(r"[\w'+#.-]+", lowered))
        found = []
        for word in words:
            for name in first_words.get(word, ()):
                if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", lowered):
                    found.append(name)
        for name in sorted(set(found), key=len, reverse=True)[:per_memory]:
            bridges.append((key.split(":", 1)[1], names[name]))
    return [(int(memory), int(entity)) for memory, entity in bridges]


def block_sample(population: Population, limit: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(global indices, positions, kind codes) for an even sample of the model blocks' neurons, at most `limit`.

    The answer model sits at the brain's core: its layers are nested shells from the inside out (layer 1 inmost),
    each expert a direction on its shell (a golden-angle spread), its units a small cloud there. The memory
    model is a small ball in the memory region. Positions follow the real address, so layer and expert are
    readable from where a dot is."""
    if not population.blocks or limit <= 0:
        return np.zeros(0, dtype=np.int64), np.zeros((0, 3)), np.zeros(0, dtype=np.int8)
    total = sum(block["count"] for block in population.blocks)
    indices, positions, codes = [], [], []
    offset = len(population.ids)
    golden = math.pi * (3 - math.sqrt(5))
    for block in population.blocks:
        share = max(1, int(limit * block["count"] / total))
        local = np.unique(np.linspace(0, block["count"] - 1, num=min(share, block["count"])).astype(np.int64))
        experts, units, shared, layers = block["experts"], block["per_expert"], block["shared"], block["layers"]
        per_layer = experts * units + shared if experts else units
        layer, rest = np.divmod(local, per_layer)
        if experts:
            expert = np.where(rest < experts * units, rest // max(1, units), experts)  # experts = the shared one
            unit = np.where(rest < experts * units, rest % max(1, units), rest - experts * units)
            directions = experts + 1
        else:
            expert, unit, directions = rest // max(1, units // 64), rest % max(1, units // 64), 65
        t = (expert + .5) / directions
        z = 1 - 2 * t
        ring = np.sqrt(np.maximum(0.0, 1 - z * z))
        theta = expert * golden
        jitter = (unit % 97) / 97 * 2 * math.pi
        wobble = .025 * np.stack([np.cos(jitter), np.sin(jitter), np.cos(jitter * 1.7)], axis=1)
        if block["kind"] == "model neuron":
            radius = (.12 + .5 * (layer + .5) / max(1, layers))[:, None]
            centre = np.array(_centre("dual_brain")) * .25
        else:
            radius = (.03 + .12 * (layer + .5) / max(1, layers))[:, None]
            centre = np.array(_centre("memory")) * .6
        points = centre + radius * np.stack([ring * np.cos(theta), z * .8, ring * np.sin(theta)], axis=1) + wobble
        indices.append(local + offset)
        positions.append(points)
        codes.append(np.full(len(local), KIND_CODES[block["kind"]], dtype=np.int8))
        offset += block["count"]
    axes = np.array((1.22, .88, 1.08)) * .94
    points = np.concatenate(positions)
    reach = np.sqrt(((points / axes) ** 2).sum(axis=1))
    points[reach > 1] /= reach[reach > 1][:, None]
    return np.concatenate(indices), points, np.concatenate(codes)


def stores_of(runtime: Any) -> dict[str, Path | None]:
    """The other stores beside agent.db and knowledge.db, from the runtime or their usual places."""
    memory_path = getattr(getattr(runtime, "memory", None), "path", None)
    data = Path(memory_path).parent.parent if memory_path else None
    found = {}
    for name, attribute, fallback in (("tool_understanding", "tool_understanding", "tool_understanding.db"),
                                      ("experience", "experience", "memory/experience.db"),
                                      ("learning", "learner", "learning/learning.db"),
                                      ("events", "events", "memory/events.db"),
                                      ("code_monitor", "code_monitor", "memory/code-monitor.db"),
                                      ("authority", "authority", "memory/authority.db"),
                                      ("intelligence", "intelligence", "memory/intelligence.db")):
        path = getattr(getattr(runtime, attribute, None), "path", None)
        if not path and data is not None:
            path = data / fallback
        found[name] = Path(path) if path else None
    found["code_root"] = Path(__file__).resolve().parent  # HAYAT's own package: its functions are its working parts
    # Its own model store and the models it really uses: the answer writer and the memory embedder.
    found["models_dir"] = Path(__file__).resolve().parents[1] / "runtime" / "ollama-models"
    config = getattr(getattr(runtime, "router", None), "config", None) or {}
    found["answer_model"] = ((config.get("default_model") or {}).get("model") if isinstance(config, dict) else None) or "hayat:latest"
    embedder = getattr(getattr(runtime, "memory", None), "embedding_provider", None)
    name = str(getattr(embedder, "model", "") or "nomic-embed-text")
    found["embedding_model"] = name if ":" in name else name + ":latest"
    return found


def from_runtime(runtime: Any, base: int) -> Population | None:
    memory_path = getattr(getattr(runtime, "memory", None), "path", None)
    knowledge = getattr(getattr(runtime, "research", None), "knowledge", None)
    knowledge_path = getattr(knowledge, "path", None)
    if not memory_path and not knowledge_path:
        return None
    return build(base, Path(memory_path) if memory_path else None, Path(knowledge_path) if knowledge_path else None,
                 stores=stores_of(runtime))
