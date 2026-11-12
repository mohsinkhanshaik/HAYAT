"""Brain neurons × 6 from real stored units (app/brain_population.py), 2026-11-08.

The owner asked for the brain's neurons multiplied by six. The added neurons must be real memories and
knowledge-graph entities with their real synapses and use; never padding, never model weights.
"""
from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.brain_population import MAX_DEGREE, build, plausible_entity


def memory_db(path, memories=30):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY, timestamp TEXT, memory_type TEXT, content TEXT, importance REAL,
                               source TEXT, task_id TEXT, metadata_json TEXT, session_id TEXT, state_class TEXT, content_digest TEXT);
        CREATE TABLE memory_links (source_id INTEGER, target_id INTEGER, relation TEXT, weight REAL, evidence_json TEXT,
                                   created_at TEXT, last_reinforced_at TEXT, use_count INTEGER);
        CREATE TABLE conversations (id INTEGER PRIMARY KEY, session_id TEXT, timestamp TEXT, role TEXT, content TEXT,
                                    model TEXT, metadata_json TEXT);""")
    for index in range(1, memories + 1):
        text = "The owner prefers Python for scripts." if index == 1 else f"Episode {index}: searched the web for news."
        db.execute("INSERT INTO memories VALUES (?, '2026-11-01T10:00:00+00:00', ?, ?, ?, 'test', '', ?, '', '', '')",
                   (index, "semantic" if index % 3 == 0 else "episodic", text, .9 if index == 1 else .5,
                    json.dumps({"successful_uses": 2} if index == 1 else {})))
    for index in range(1, memories):
        db.execute("INSERT INTO memory_links VALUES (?, ?, 'related', ?, '{}', '2026-11-01', '2026-11-07T10:00:00+00:00', ?)",
                   (index, index + 1, .5 + index / 100, 1 if index == 1 else 0))
    db.execute("INSERT INTO conversations VALUES (1, 's', '2026-11-08T09:00:00+00:00', 'assistant', 'Paris.', 'hayat', ?)",
               (json.dumps({"recalled_memory_ids": [1, 2]}),))
    db.commit()
    db.close()


def knowledge_db(path, entities=200):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT, normalized_name TEXT, entity_type TEXT);
        CREATE TABLE claims (id TEXT PRIMARY KEY, claim_text TEXT, topic TEXT, retrieval_count INTEGER, successful_uses INTEGER,
                             last_used TEXT, status TEXT DEFAULT 'SUPPORTED', confidence REAL DEFAULT .8, last_verified TEXT,
                             updated_at TEXT, run_id TEXT);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_id TEXT, source_id INTEGER, excerpt TEXT, stance TEXT, strength REAL,
                               retrieved_at TEXT);
        CREATE TABLE sources (id INTEGER PRIMARY KEY, title TEXT, domain TEXT, source_type TEXT, retrieved_at TEXT, quality REAL);
        CREATE TABLE research_runs (id TEXT PRIMARY KEY, query TEXT, level TEXT, completed_at TEXT, confidence REAL);
        CREATE TABLE run_sources (run_id TEXT, source_id INTEGER, rank INTEGER);
        CREATE TABLE claim_entities (claim_id TEXT, entity_id INTEGER, role TEXT);
        CREATE TABLE relationships (id INTEGER PRIMARY KEY, subject_entity_id INTEGER, predicate TEXT, object_entity_id INTEGER,
                                    claim_id TEXT, confidence REAL, valid_from TEXT, valid_to TEXT);""")
    names = ["Python", "However", "MUST", "Ollama", "On July"] + [f"Entity {index}" for index in range(entities)]
    for index, name in enumerate(names, 1):
        db.execute("INSERT INTO entities VALUES (?, ?, ?, 'concept')", (index, name, name.lower()))
        db.execute("INSERT INTO claims (id, claim_text, topic, retrieval_count, successful_uses, last_used) "
                   "VALUES (?, 'x', 't', ?, 0, ?)",
                   (f"c{index}", 5 if name == "Python" else 0, "2026-11-07T00:00:00+00:00" if name == "Python" else None))
        db.execute("INSERT INTO claim_entities VALUES (?, ?, 'subject')", (f"c{index}", index))
    # Python and Ollama named in one claim; a chain of relationships among the generic entities.
    db.execute("INSERT INTO claim_entities VALUES ('c1', 4, 'object')")
    db.execute("UPDATE claims SET run_id='r1' WHERE id='c1'")
    db.execute("INSERT INTO research_runs VALUES ('r1', 'what is python', 'normal', '2026-11-07T00:00:00+00:00', .8)")
    db.execute("INSERT INTO sources VALUES (1, 'Python docs', 'docs.python.org', 'official', '2026-11-07T00:00:00+00:00', .9)")
    db.execute("INSERT INTO run_sources VALUES ('r1', 1, 0)")
    db.execute("INSERT INTO evidence VALUES (1, 'c1', 1, 'Python is a programming language.', 'supports', .9, '2026-11-07')")
    for index in range(6, 6 + entities - 1):
        db.execute("INSERT INTO relationships VALUES (NULL, ?, 'related_to', ?, 'c1', .7, NULL, NULL)", (index, index + 1))
    db.commit()
    db.close()


def gguf(path, values):
    """A minimal GGUF header: magic, version, no tensors, then the key/values (u32/u64/str/array of u32)."""
    import struct

    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data
    body = b""
    for key, value in values.items():
        if isinstance(value, str):
            body += string(key) + struct.pack("<I", 8) + string(value)
        elif isinstance(value, list):
            body += string(key) + struct.pack("<IIQ", 9, 4, len(value)) + b"".join(struct.pack("<I", item) for item in value)
        else:
            body += string(key) + struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(values)) + body)


def test_model_neurons_are_counted_from_the_file_and_addressed(tmp_path):
    import json as _json

    from app.brain_population import block_sample
    from app.model_anatomy import anatomy, gguf_metadata, model_anatomy

    blob = tmp_path / "blobs" / "sha256-abc"
    blob.parent.mkdir(parents=True)
    gguf(blob, {"general.architecture": "qwen35moe", "qwen35moe.block_count": 4, "qwen35moe.expert_count": 8,
                "qwen35moe.expert_used_count": 2, "qwen35moe.expert_feed_forward_length": 16,
                "qwen35moe.expert_shared_feed_forward_length": 16, "tokenizer.ggml.token_type": [1, 2, 3]})
    manifest = tmp_path / "manifests" / "registry.ollama.ai" / "library" / "hayat" / "latest"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(_json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:abc"}]}))
    found = model_anatomy(tmp_path, "hayat:latest")
    assert found["neurons"] == 4 * (8 * 16 + 16) and found["experts"] == 8 and found["experts_used_per_token"] == 2
    assert "tokenizer.ggml.token_type" not in gguf_metadata(blob)  # big arrays are skipped, not decoded
    dense = anatomy({"general.architecture": "bert", "bert.block_count": 2, "bert.feed_forward_length": 10})
    assert dense["neurons"] == 20 and dense["experts"] == 0
    memory_db(tmp_path / "agent.db", memories=3)
    population = build(5, tmp_path / "agent.db", None, multiple=6,
                       stores={"models_dir": tmp_path, "answer_model": "hayat:latest"})
    counts = population.counts()
    assert counts["model_neurons"] == 576 and counts["total"] == 5 + counts["stored_units"] + 576 and counts["short_by"] == 0
    first_model = len(population.ids)
    assert population.address(population.blocks[0], 0) == (0, 0, 0)
    assert population.address(population.blocks[0], 8 * 16) == (0, None, 0)  # the shared expert's first unit
    assert population.address(population.blocks[0], 144 + 17) == (1, 1, 1)
    text = population.describe(first_model + 144 + 17)
    assert "layer 2 of 4, expert 2 of 8, unit 2 of 16" in text and "not when it fires" in text
    indices, positions, codes = block_sample(population, 100)
    assert len(indices) == len(positions) == len(codes) <= 100 and indices.min() >= first_model


@pytest.fixture
def stores(tmp_path):
    memory_db(tmp_path / "agent.db")
    knowledge_db(tmp_path / "knowledge.db")
    return tmp_path / "agent.db", tmp_path / "knowledge.db"


def test_the_brain_reaches_exactly_six_times_its_parts_with_real_units(stores):
    population = build(40, *stores, multiple=6)
    counts = population.counts()
    assert counts["total"] == counts["target"] == 240 and counts["multiple"] == 6.0 and counts["short_by"] == 0
    # Every memory, then the claims, then the best-evidenced entities: filled in order, exactly to the target.
    assert counts["by_kind"] == {"memory": 30, "claim": 170}
    assert len(set(population.ids)) == len(population)


def test_with_too_few_real_units_it_says_so_instead_of_padding(stores):
    population = build(500, *stores, multiple=6)  # wants 2,500 more than the stores hold
    counts = population.counts()
    assert counts["by_kind"]["memory"] == 30 and counts["by_kind"]["knowledge"] == 202
    assert counts["short_by"] == 3000 - counts["total"] > 0 and counts["multiple"] < 6


def test_extraction_fragments_are_not_neurons(stores):
    population = build(500, *stores, multiple=6)
    labels = {label for label, kind in zip(population.labels, population.kinds) if kind == "knowledge"}
    assert "Python" in labels and "Ollama" in labels
    assert not {"However", "MUST", "On July"} & labels
    assert plausible_entity("Red Hat") and not plausible_entity("2019") and not plausible_entity("Your")


def test_synapses_are_real_sparse_and_bridge_memory_to_knowledge(stores):
    population = build(500, *stores, multiple=6)
    kinds = set(population.link_kinds)
    assert {"related", "relationship", "same claim", "names", "supports", "quoted from", "found by", "learned in",
            "followed by"} - {"followed by"} <= kinds
    index = {label: position for position, (label, kind) in enumerate(zip(population.labels, population.kinds))
             if kind == "knowledge"}
    bridge = [(a, b) for (a, b), kind in zip(population.links.tolist(), population.link_kinds) if kind == "names"]
    assert any(population.labels[b] == "Python" and population.kinds[a] == "memory" for a, b in bridge)
    assert any({a, b} == {index["Python"], index["Ollama"]} for (a, b), kind in
               zip(population.links.tolist(), population.link_kinds) if kind == "same claim")
    # The chain of provenance: evidence supports a claim and is quoted from a source an investigation found.
    claim, evidence = population.ids.index("claim:c1"), population.ids.index("evidence:1")
    source, run = population.ids.index("source:1"), population.ids.index("investigation:r1")
    links = {frozenset((a, b)): kind for (a, b), kind in zip(population.links.tolist(), population.link_kinds)}
    assert links[frozenset((evidence, claim))] == "supports" and links[frozenset((evidence, source))] == "quoted from"
    assert links[frozenset((source, run))] == "found by" and links[frozenset((claim, run))] == "learned in"
    degree = np.bincount(population.links.ravel(), minlength=len(population))
    assert degree.max() <= 16 * MAX_DEGREE  # sparse: a bounded number per kind of link, never all-to-all


def test_activity_comes_from_recorded_use_and_recall(stores):
    population = build(500, *stores, multiple=6)
    first = population.ids.index("memory:1")
    assert population.uses[first] >= 3  # two successful uses + a reinforced synapse
    assert first in population.fired and population.ids.index("memory:2") in population.fired
    python = next(i for i, (label, kind) in enumerate(zip(population.labels, population.kinds))
                  if label == "Python" and kind == "knowledge")
    assert population.uses[python] == 5 and not np.isnan(population.last_used[python])
    text = population.describe(first)
    assert "Memory neuron" in text and "Recalled for a reply" in text and "synapses" in text


def test_connected_entities_form_named_communities_and_everything_sits_inside_the_brain(stores):
    population = build(500, *stores, multiple=6)
    groups = {group for kind, group in zip(population.kinds, population.groups) if kind == "knowledge"}
    assert any(group.startswith("around ") for group in groups)
    axes = np.array((1.22, .88, 1.08))
    assert (np.sqrt(((population.positions / axes) ** 2).sum(axis=1)) <= 1.0001).all()
    again = build(500, *stores, multiple=6)
    assert again is population  # cached: the structure is not rebuilt on every refresh


def test_a_missing_store_gives_what_the_other_has(tmp_path):
    memory_db(tmp_path / "agent.db", memories=5)
    population = build(10, tmp_path / "agent.db", tmp_path / "absent.db", multiple=6)
    assert population.counts()["by_kind"] == {"memory": 5, "turn": 1} and population.counts()["short_by"] == 44


# ------------------------------------------------------------------------------------- the view

tk = pytest.importorskip("tkinter")


@pytest.fixture
def graph(shared_tk, stores):
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette
    from tests.conftest import close_window, open_window
    from tests.test_brain_components import full_snapshot

    root = open_window(shared_tk, "900x560-4000-4000")
    widget = BrainGraph(root, Palette, lambda *_: None)
    chosen = []
    widget.on_population = chosen.append
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    root.update()
    snapshot = full_snapshot()
    snapshot.population = build(len(snapshot.components) + 21, *stores, multiple=6)
    widget.set_components(snapshot)
    yield widget, chosen
    widget.destroy()
    close_window(root)


def test_the_population_is_drawn_in_one_image_and_counted_in_the_legend(graph):
    widget, _chosen = graph
    assert len(widget.find_withtag("population")) == 1  # thousands of neurons, one canvas item
    legend = " ".join(widget.itemcget(item, "text") for item in widget.find_withtag("legend-footer")
                      if widget.type(item) == "text")
    assert "neurons:" in legend and "stored memories" in legend and "synapses" in legend
    started = time.perf_counter()
    widget.redraw()
    assert (time.perf_counter() - started) < 2.0 and widget._population_ms > 0


def test_hover_and_click_a_neuron(graph):
    widget, chosen = graph
    sx, sy, z = widget._population_screen
    candidates = [index for index in np.argsort(-z).tolist()
                  if widget._hit(float(sx[index]), float(sy[index])) is None
                  and widget._hit_component(float(sx[index]), float(sy[index])) is None]
    index = candidates[0]
    x, y = float(sx[index]), float(sy[index])
    widget._inside, widget._pointer = True, (x, y)
    widget._rehover()
    assert widget.population_hover is not None and widget.find_withtag("population-label")
    hovered = widget.population_hover
    widget._press(SimpleNamespace(x=x, y=y))
    widget._release(SimpleNamespace(x=x, y=y))
    assert chosen == [hovered] and widget.population_active == hovered
    widget._leave(SimpleNamespace())
    assert widget.population_hover is None


def test_parts_off_hides_the_population(graph):
    widget, _chosen = graph
    widget.show_components = False
    widget.redraw()
    assert not widget.find_withtag("population") and widget._hit_population(10, 10) is None
