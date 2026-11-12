"""The graph network is judged on links it never saw, against simple rules, and says when it learned nothing."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

pytest.importorskip("torch")

from app.graph_network import (HEURISTICS, LIMITS, MARGIN, GraphLab, clean_edges, edge_file, learn_links,  # noqa: E402
                               memory_graph, register_graph_tools)


def communities(groups: int = 4, size: int = 30, inside: float = 0.25, across: float = 0.01, seed: int = 0):
    """Dense groups with few links between them: structure a link predictor can learn."""
    rng = np.random.default_rng(seed)
    count = groups * size
    edges = [(a, b) for a in range(count) for b in range(a + 1, count)
             if rng.random() < (inside if a // size == b // size else across)]
    return count, edges


def random_graph(count: int = 120, links: int = 500, seed: int = 0):
    rng = np.random.default_rng(seed)
    found = set()
    while len(found) < links:
        a, b = (int(value) for value in rng.integers(0, count, 2))
        if a != b:
            found.add((min(a, b), max(a, b)))
    return count, sorted(found)


def test_hidden_links_in_a_structured_graph_are_found_far_better_than_chance():
    count, edges = communities()
    result = learn_links(count, edges)
    assert result["network"]["auc"] > 0.8 and result["network"]["average_precision"] > 0.75
    assert result["baselines"]["common neighbours"]["auc"] > 0.7           # the simple rules are measured too
    assert set(result["baselines"]) == set(HEURISTICS)
    assert result["hidden_links_tested"] == int(len(edges) * 0.15) and result["training_links"] < len(edges)
    assert result["gain_over_best_baseline"] == pytest.approx(
        result["network"]["auc"] - result["baselines"][result["best_baseline"]]["auc"], abs=1e-4)
    assert result["network_beats_baselines"] == (result["gain_over_best_baseline"] >= MARGIN)
    assert result["suggested_by"] == result["selection"]["predictor"]
    assert result["selection"]["basis"] == "validation only"
    # Suggestions are new pairs, and in a community graph they fall inside one community.
    existing = set(clean_edges(count, edges))
    suggested = [(row["a"], row["b"]) for row in result["suggested_links"]]
    assert suggested and not existing & set(suggested)
    assert sum(a // 30 == b // 30 for a, b in suggested) >= 0.8 * len(suggested)
    assert result["limits"] == LIMITS and "never created" in LIMITS
    json.dumps(result)


def test_a_graph_with_no_structure_scores_like_chance_and_suggests_nothing():
    count, edges = random_graph()
    result = learn_links(count, edges)
    assert 0.35 < result["network"]["auc"] < 0.62
    assert not result["network_beats_baselines"] or result["network"]["auc"] >= 0.6
    if result["selection"]["predictor"] == "none":
        assert result["verdict"].startswith("No learnable structure") and result["suggested_links"] == []
        assert result["suggested_by"] == "none"


def test_node_features_that_describe_the_groups_are_used():
    count, edges = communities(inside=0.12, seed=2)
    features = np.eye(4)[np.arange(count) // 30] + np.random.default_rng(0).normal(scale=0.1, size=(count, 4))
    result = learn_links(count, edges, features)
    # A sparse graph (about 200 links): shared neighbours are rare, so the simple rules are near chance, while
    # features that say which group a node is in let the network do clearly better.
    best = result["baselines"][result["best_baseline"]]["auc"]
    assert result["network"]["node_features"] == "given"
    assert result["network"]["auc"] > 0.65 and result["network"]["auc"] > best + 0.1 and result["network_beats_baselines"]
    assert result["suggested_by"] == "graph network"
    assert result["evaluation"]["features_sha256"] and learn_links(count, edges)["evaluation"]["features_sha256"] is None


def test_the_same_graph_and_seed_give_the_same_split_and_record():
    count, edges = communities(seed=3)
    first, second = learn_links(count, edges, epochs=40), learn_links(count, edges, epochs=40)
    assert first["evaluation"]["comparison_key"] == second["evaluation"]["comparison_key"]
    assert first["baselines"] == second["baselines"]                        # heuristics are exact
    assert first["network"]["auc"] == pytest.approx(second["network"]["auc"], abs=0.02)
    shuffled = [(b, a) for a, b in reversed(edges)] + edges[:5]             # order, direction and repeats do not matter
    assert learn_links(count, shuffled, epochs=40)["evaluation"]["graph_sha256"] == first["evaluation"]["graph_sha256"]


def test_no_hidden_link_reaches_the_encoder_or_the_heuristics(monkeypatch):
    """The leak that makes link prediction look better than it is: scoring a link the model was shown."""
    import app.graph_network as module

    count, edges = communities(seed=4)
    seen = {}
    real_train, real_neighbours = module._train, module._neighbours

    def train(nodes, train_links, validation, features, taken, seed, epochs, **kwargs):
        seen["train"], seen["validation"] = set(train_links), set(validation)
        return real_train(nodes, train_links, validation, features, taken, seed, epochs, **kwargs)

    def neighbours(nodes, links):
        seen.setdefault("heuristic_views", []).append(set(links))
        return real_neighbours(nodes, links)

    monkeypatch.setattr(module, "_train", train)
    monkeypatch.setattr(module, "_neighbours", neighbours)
    result = learn_links(count, edges, epochs=20)
    everything = set(clean_edges(count, edges))
    hidden = everything - seen["train"] - seen["validation"]
    assert len(hidden) == result["hidden_links_tested"] and not hidden & seen["train"]
    assert seen["heuristic_views"][0] == seen["train"]                      # scored on training links only
    assert not seen["train"] & seen["validation"]


@pytest.mark.parametrize("count,edges,message", [
    (5, [(0, 1)], "between 12 and"),
    (40, [(0, 1), (1, 2)], "distinct links"),
    (40, [(0, 99)] * 40, "names node 99"),
    (True, [], "between 12 and"),
])
def test_graphs_too_small_or_malformed_are_refused(count, edges, message):
    with pytest.raises(ValueError, match=message):
        learn_links(count, edges)


def test_bad_features_and_labels_are_refused():
    count, edges = communities()
    with pytest.raises(ValueError, match="one finite numeric row per node"):
        learn_links(count, edges, np.zeros((3, 2)))
    with pytest.raises(ValueError, match="one label per node"):
        learn_links(count, edges, labels=["a"])


def _memory_store(path, count=60):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT)")
        db.execute("CREATE TABLE memory_links(source_id INTEGER, target_id INTEGER, relation TEXT)")
        db.executemany("INSERT INTO memories VALUES(?,?)", [(100 + index, f"memory   number {index}\nwith a second line " + "x" * 200)
                                                           for index in range(count)])
        _nodes, edges = communities(groups=3, size=count // 3, seed=5)
        db.executemany("INSERT INTO memory_links VALUES(?,?,?)", [(100 + a, 100 + b, "related") for a, b in edges])
    return edges


def test_the_memory_graph_is_read_without_writing_and_its_run_is_kept(tmp_path):
    store = tmp_path / "agent.db"
    edges = _memory_store(store)
    before = store.read_bytes()
    count, found, labels = memory_graph(store)
    assert count == 60 and len(found) == len(edges) and all(0 <= a < 60 and 0 <= b < 60 for a, b in found)
    assert labels[0].startswith("memory number 0 with a second line") and len(labels[0]) <= 80   # short, one line
    lab = GraphLab(tmp_path / "graph.db", memory_path=lambda: store)
    events = []
    lab.events = type("Bus", (), {"publish": lambda self, *event: events.append(event)})()
    assert lab.recent() == [] and lab.status() == {"runs": 0, "last": "none yet"}
    result = lab.run("memory")
    assert result["source"] == "links between HAYAT's memories" and result["run_id"] == 1
    assert all("a_label" in row and row["a_label"].startswith("memory number") for row in result["suggested_links"])
    assert events == [("LAB_RUN_COMPLETED", "graph_lab", {"run_id": 1, "auc": result["metric"]})]
    assert store.read_bytes() == before                                    # nothing written, no link created
    again = lab.run("memory")
    assert again["comparison"]["comparable"] and lab.detail(1)["network"] == result["network"] and lab.detail(9) is None
    assert [row["id"] for row in lab.recent()] == [2, 1] and lab.status()["runs"] == 2
    with pytest.raises(ValueError, match="memory store was not found"):
        GraphLab(tmp_path / "g2.db", memory_path=lambda: tmp_path / "missing.db").run("memory")
    with pytest.raises(RuntimeError, match="isn't available"):
        GraphLab(tmp_path / "g3.db").run("memory")


def test_a_link_file_and_the_tool(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    _count, edges = communities(groups=3, size=20, seed=6)
    path = tmp_path / "links.csv"
    path.write_text("from,to\n" + "\n".join(f"n{a},n{b}" for a, b in edges), encoding="utf-8")
    count, found, names = edge_file(path)
    assert count == len(names) <= 60 and len(found) == len(edges) and names[0].startswith("n")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_graph_tools(registry, GraphLab(tmp_path / "graph.db"))
    done = registry.execute("graph_learn", {"source": str(path)})
    assert done.success, done.error
    assert done.output["nodes"] == count and done.output["suggested_links"][0]["a_label"].startswith("n")
    assert registry.execute("graph_learn", {"action": "recent"}).output["runs"][0]["id"] == 1
    assert registry.execute("graph_learn", {"action": "detail", "run_id": 1}).output["run"]["links"] == len(set(edges))
    assert registry.get("graph_learn").permission == "filesystem_read"
