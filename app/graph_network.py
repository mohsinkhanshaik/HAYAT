"""A graph neural network for link prediction, measured against the simple heuristics it must beat.

Given a graph (by default the links between HAYAT's own memories), this hides a share of the links, trains a
two-layer graph convolutional encoder (Kipf and Welling) on the rest, and asks it to tell the hidden links from
pairs that were never linked. The same question is put to three classical heuristics that need no training:
common neighbours, Adamic-Adar and preferential attachment.

Validation chooses the suggestion method and training checkpoint; a disjoint test partition audits that choice.
Both positive and negative evaluation pairs are excluded from training. The network is selected only when its
validation AUC clears 0.6 and beats the best validation heuristic by a margin. Otherwise that heuristic is used
if it clears 0.6, or no suggestion is made. This is a finite sample, so even random graphs can occasionally pass.

Nothing here writes a link. Suggestions are a list for the owner (or a later, separately governed step) to act on.
The message passing uses only the training links, so a hidden link can never leak into the encoder that scores it.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from numbers import Integral
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MIN_NODES, MIN_EDGES, MAX_NODES, MAX_EDGES = 12, 30, 20_000, 200_000
TEST_SHARE, VALIDATION_SHARE, MARGIN = 0.15, 0.05, 0.02
HIDDEN, EMBED, EPOCHS, PATIENCE = 64, 32, 200, 25
PROFILES = {"balanced": (HIDDEN, EMBED), "expanded": (HIDDEN * 10, EMBED * 10)}
MAX_FEATURES, MAX_CANDIDATES, MAX_CANDIDATE_STEPS = 2048, 50_000, 2_000_000
LIMITS = ("Measured on links hidden from this graph only. A suggested link is a pattern-based guess that two items "
          "belong together, not a fact; links are never created by this tool. Scores are uncalibrated rankings, "
          "not probabilities. Predictor selection uses validation only; test scores are a separate audit.")


def clean_edges(count: int, edges: Any) -> list[tuple[int, int]]:
    """Undirected, deduplicated, without self-links; out-of-range endpoints are an error, not silently dropped."""
    seen = set()
    for index, (a, b) in enumerate(edges):
        if index >= MAX_EDGES * 5:
            raise ValueError("The graph input exceeds the edge processing budget.")
        if any(isinstance(value, bool) or not isinstance(value, Integral) for value in (a, b)):
            raise ValueError("Edge endpoints must be integer node indices.")
        a, b = int(a), int(b)
        if not (0 <= a < count and 0 <= b < count):
            raise ValueError(f"An edge names node {max(a, b)} but the graph has {count} nodes.")
        if a != b:
            seen.add((min(a, b), max(a, b)))
    return sorted(seen)


def _split(edges: list[tuple[int, int]], seed: int) -> tuple[list, list, list]:
    import numpy as np

    order = np.random.default_rng(seed).permutation(len(edges))
    test = max(1, int(len(edges) * TEST_SHARE))
    validation = max(1, int(len(edges) * VALIDATION_SHARE))
    pick = [edges[index] for index in order]
    return pick[test + validation:], pick[test:test + validation], pick[:test]


def _negatives(count: int, taken: set, number: int, rng: Any) -> list[tuple[int, int]]:
    """Pairs that are not linked anywhere in the graph (so a "negative" is never a hidden true link)."""
    found: set = set()
    limit = count * (count - 1) // 2 - len(taken)
    number = min(number, limit)
    # Dense graphs otherwise spend arbitrarily long rejecting already linked pairs.
    if len(taken) > count * (count - 1) // 4:
        available = [(a, b) for a in range(count) for b in range(a + 1, count) if (a, b) not in taken]
        return sorted(available[int(i)] for i in rng.choice(len(available), size=number, replace=False))
    while len(found) < number:
        a, b = (int(value) for value in rng.integers(0, count, 2))
        pair = (min(a, b), max(a, b))
        if a != b and pair not in taken and pair not in found:
            found.add(pair)
    return sorted(found)


def _candidates(count: int, edges: list, seed: int) -> tuple[list, dict]:
    """Bound work while discovering two-hop pairs, rather than constructing an unbounded set then slicing it."""
    import numpy as np

    around = _neighbours(count, edges)
    rng = np.random.default_rng(seed)
    found: set = set()
    steps = 0
    for middle in rng.permutation(count):
        neighbours = rng.permutation(sorted(around[int(middle)])).tolist()
        for index, a in enumerate(neighbours):
            for b in neighbours[index + 1:]:
                steps += 1
                if b not in around[a]:
                    found.add((min(a, b), max(a, b)))
                if len(found) >= MAX_CANDIDATES or steps >= MAX_CANDIDATE_STEPS:
                    return sorted(found), {"pairs_scored": len(found), "two_hop_steps": steps, "truncated": True}
    return sorted(found), {"pairs_scored": len(found), "two_hop_steps": steps, "truncated": False}


def _scores(truth: Any, score: Any) -> dict[str, float]:
    from sklearn.metrics import average_precision_score, roc_auc_score

    return {"auc": round(float(roc_auc_score(truth, score)), 4),
            "average_precision": round(float(average_precision_score(truth, score)), 4)}


def _neighbours(count: int, edges: list[tuple[int, int]]) -> list[set]:
    around: list[set] = [set() for _ in range(count)]
    for a, b in edges:
        around[a].add(b)
        around[b].add(a)
    return around


HEURISTICS = {
    "common neighbours": lambda around, a, b: float(len(around[a] & around[b])),
    "Adamic-Adar": lambda around, a, b: float(sum(1 / math.log(len(around[n])) for n in around[a] & around[b]
                                                  if len(around[n]) > 1)),
    "preferential attachment": lambda around, a, b: float(len(around[a]) * len(around[b])),
}


def _train(count: int, train: list, validation: list, features: Any, taken: set, seed: int, epochs: int, *,
           validation_negatives: list, reserved_negatives: set, profile: str = "balanced") -> tuple[Any, dict]:
    """Returns (score(pairs) -> numpy scores, training facts). Full-batch GCN with a dot-product decoder."""
    import numpy as np
    import torch

    # Local generators: training must not reset the RNG used by HAYAT's other models or concurrent workers.
    generator = torch.Generator(device="cpu").manual_seed(seed)
    rng = np.random.default_rng(seed)
    hidden, embed = PROFILES[profile]
    index = torch.tensor(train + [(b, a) for a, b in train] + [(n, n) for n in range(count)], dtype=torch.long).t()
    degree = torch.zeros(count).scatter_add_(0, index[0], torch.ones(index.shape[1]))
    weight = (degree[index[0]] * degree[index[1]]).rsqrt()           # D^-1/2 (A + I) D^-1/2
    adjacency = torch.sparse_coo_tensor(index, weight, (count, count), check_invariants=True).coalesce()
    given = None if features is None else torch.tensor(np.asarray(features, dtype=np.float32))
    table = torch.nn.Parameter(torch.randn(count, embed, generator=generator)) if given is None else None

    def linear(inputs: int, outputs: int) -> tuple[Any, Any]:
        weight = torch.nn.Parameter(torch.empty(outputs, inputs))
        bias = torch.nn.Parameter(torch.empty(outputs))
        torch.nn.init.kaiming_uniform_(weight, a=math.sqrt(5), generator=generator)
        torch.nn.init.uniform_(bias, -1 / math.sqrt(inputs), 1 / math.sqrt(inputs), generator=generator)
        return weight, bias

    first = linear(embed if given is None else given.shape[1], hidden)
    second = linear(hidden, embed)
    parameters = [*first, *second, *([table] if table is not None else [])]
    optimiser = torch.optim.Adam(parameters, lr=0.01, weight_decay=5e-4)

    def dropout(x: Any, training: bool) -> Any:
        return x * (torch.rand(x.shape, generator=generator) >= 0.2) / 0.8 if training else x

    def encode(training: bool) -> Any:
        # Dropout on the learned table only: given features can be a handful of numbers, and zeroing a fifth of
        # them each step throws away most of what they say.
        x = dropout(table, training) if given is None else given
        x = torch.relu(torch.sparse.mm(adjacency, torch.nn.functional.linear(x, *first)))
        x = dropout(x, training)
        return torch.sparse.mm(adjacency, torch.nn.functional.linear(x, *second))

    def logits(z: Any, pairs: list) -> Any:
        both = torch.tensor(pairs, dtype=torch.long)
        return (z[both[:, 0]] * z[both[:, 1]]).sum(1)

    check_pairs = validation + validation_negatives
    check_truth = np.array([1] * len(validation) + [0] * (len(check_pairs) - len(validation)))
    best, best_state, stale, ran = -1.0, None, 0, 0
    for epoch in range(epochs):
        ran = epoch + 1
        optimiser.zero_grad()
        z = encode(True)
        fake = _negatives(count, taken | reserved_negatives, len(train), rng)
        output = logits(z, train + fake)
        target = torch.cat([torch.ones(len(train)), torch.zeros(len(fake))])
        loss = torch.nn.functional.binary_cross_entropy_with_logits(output, target)
        if not torch.isfinite(loss):
            raise ValueError("Graph training produced a non-finite loss; check the scale of the node features.")
        loss.backward()
        optimiser.step()
        if epoch % 5 == 4 or epoch == epochs - 1:
            with torch.no_grad():
                value = _scores(check_truth, logits(encode(False), check_pairs).numpy())["auc"]
            if value > best + 1e-4:
                best, stale = value, 0
                best_state = [parameter.detach().clone() for parameter in parameters]
            else:
                stale += 5
                if stale >= PATIENCE:
                    break
    if best_state is not None:                                       # the weights that did best on validation links
        with torch.no_grad():
            for parameter, saved in zip(parameters, best_state):
                parameter.copy_(saved)
    with torch.no_grad():
        final = encode(False)

    def score(pairs: list) -> Any:
        with torch.no_grad():
            # Scoring a wide profile over 50k pairs otherwise allocates two enormous gather tensors at once.
            return np.concatenate([logits(final, pairs[start:start + 2048]).numpy()
                                   for start in range(0, len(pairs), 2048)]) if pairs else np.empty(0)
    return score, {"epochs_run": ran, "validation_auc": round(best, 4), "final_loss": round(loss.item(), 4),
                   "parameters": int(sum(parameter.numel() for parameter in parameters)),
                   "profile": profile, "hidden_width": hidden, "embedding_width": embed}


def learn_links(count: int, edges: Any, features: Any = None, *, labels: list[str] | None = None, seed: int = 0,
                epochs: int = EPOCHS, suggestions: int = 10, profile: str = "balanced") -> dict[str, Any]:
    """Train and measure a link predictor on a graph of ``count`` nodes; see the module docstring."""
    import numpy as np

    from app.lab_evidence import digest, environment

    started = time.perf_counter()
    if isinstance(count, bool) or not isinstance(count, int) or not MIN_NODES <= count <= MAX_NODES:
        raise ValueError(f"The graph needs between {MIN_NODES} and {MAX_NODES:,} nodes.")
    for name, value, lower, upper in (("epochs", epochs, 1, 1000), ("seed", seed, 0, 2**32 - 2),
                                       ("suggestions", suggestions, 0, 1000)):
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f"{name} must be an integer between {lower} and {upper}.")
    if profile not in PROFILES:
        raise ValueError("Choose the balanced or expanded graph profile.")
    edges = clean_edges(count, edges)
    if not MIN_EDGES <= len(edges) <= MAX_EDGES:
        raise ValueError(f"The graph needs between {MIN_EDGES} and {MAX_EDGES:,} distinct links; it has {len(edges)}.")
    if features is not None:
        features = np.asarray(features, dtype=float)
        if (features.ndim != 2 or features.shape[0] != count or not 1 <= features.shape[1] <= MAX_FEATURES
                or not np.isfinite(features).all() or np.abs(features).max() > np.finfo(np.float32).max):
            raise ValueError("Node features must be one finite numeric row per node.")
    if labels is not None and len(labels) != count:
        raise ValueError("Give one label per node.")
    train, validation, test = _split(edges, seed)
    taken = set(edges)
    rng = np.random.default_rng(seed + 1)
    available = count * (count - 1) // 2 - len(taken)
    if available < 3:
        raise ValueError("The graph needs at least three unlinked pairs for separate training, validation and testing.")
    test_negatives = _negatives(count, taken, min(len(test), max(1, available // 3)), rng)
    validation_negatives = _negatives(count, taken | set(test_negatives),
                                       min(len(validation), max(1, available // 5)), rng)
    reserved = set(test_negatives) | set(validation_negatives)
    test_pairs = test + test_negatives
    truth = np.array([1] * len(test) + [0] * (len(test_pairs) - len(test)))
    around = _neighbours(count, train)                               # heuristics see the training links only, too
    validation_pairs = validation + validation_negatives
    validation_truth = np.array([1] * len(validation) + [0] * len(validation_negatives))
    validation_baselines = {name: _scores(validation_truth, np.array([rule(around, a, b) for a, b in validation_pairs]))
                            for name, rule in HEURISTICS.items()}
    # Choose the baseline and predictor before looking at test scores.
    selected_baseline = max(validation_baselines, key=lambda name: validation_baselines[name]["auc"])
    score, facts = _train(count, train, validation, features, taken, seed, epochs,
                          validation_negatives=validation_negatives, reserved_negatives=reserved, profile=profile)
    validation_network = _scores(validation_truth, score(validation_pairs))
    validation_gain = round(validation_network["auc"] - validation_baselines[selected_baseline]["auc"], 4)
    selected = ("graph network" if validation_network["auc"] >= 0.6 and validation_gain >= MARGIN else
                selected_baseline if validation_baselines[selected_baseline]["auc"] >= 0.6 else "none")
    baselines = {name: _scores(truth, np.array([rule(around, a, b) for a, b in test_pairs]))
                 for name, rule in HEURISTICS.items()}
    best_name = max(baselines, key=lambda name: baselines[name]["auc"])
    network = _scores(truth, score(test_pairs))
    gain = round(network["auc"] - baselines[best_name]["auc"], 4)
    wins = network["auc"] >= 0.6 and gain >= MARGIN
    base_inputs = EMBED if features is None else features.shape[1]
    base_parameters = (count * EMBED if features is None else 0) + (base_inputs + 1) * HIDDEN + (HIDDEN + 1) * EMBED
    result: dict[str, Any] = {
        "nodes": count, "links": len(edges), "training_links": len(train), "hidden_links_tested": len(test),
        "test_nonlinks": len(test_negatives),
        "network": {"model": "2-layer graph convolutional network, dot-product decoder", **network, **facts,
                    "node_features": "given" if features is not None else f"learned ({PROFILES[profile][1]} numbers per node)",
                    "balanced_parameters": base_parameters, "capacity_ratio": round(facts["parameters"] / base_parameters, 3)},
        "baselines": baselines, "best_baseline": best_name, "gain_over_best_baseline": gain,
        "network_beats_baselines": bool(wins),
        "selection": {"basis": "validation only", "predictor": selected, "best_baseline": selected_baseline,
                      "network": validation_network, "baselines": validation_baselines,
                      "gain": validation_gain, "positive_pairs": len(validation), "negative_pairs": len(validation_negatives)},
        "tool": "PyTorch (graph network) + scikit-learn (scoring)", "limits": LIMITS,
    }
    if selected == "none":
        result["verdict"] = ("No learnable structure on validation: no predictor cleared the selection threshold. "
                             f"Independent test AUC: network {network['auc']:.2f}, best rule "
                             f"{baselines[best_name]['auc']:.2f}. No links are suggested.")
        result["suggested_links"], result["suggested_by"] = [], "none"
        result["candidates"] = {"pairs_scored": 0, "two_hop_steps": 0, "truncated": False}
    else:
        result["verdict"] = (f"Validation selected {selected}. Independent test AUC: network {network['auc']:.2f}, "
                             f"best rule {baselines[best_name]['auc']:.2f} ({best_name}). "
                             "Test scores did not choose the predictor; suggested links still need review.")
        # Candidates: pairs two steps apart (they share a neighbour), scored by whichever method won.
        candidates, result["candidates"] = _candidates(count, edges, seed + 2)
        if candidates:
            values = score(candidates) if selected == "graph network" else np.array(
                [HEURISTICS[selected](around, a, b) for a, b in candidates])
            top = np.argsort(-values, kind="stable")[:max(0, int(suggestions))]
            result["suggested_links"] = [
                {"a": int(candidates[i][0]), "b": int(candidates[i][1]), "score": round(float(values[i]), 4),
                 **({"a_label": labels[candidates[i][0]], "b_label": labels[candidates[i][1]]} if labels else {})}
                for i in top]
        else:
            result["suggested_links"] = []
        result["suggested_by"] = selected
    result["next"] = ("Review the suggested links; none is created for you." if result["suggested_links"] else
                      "A graph with more links, or node features that describe the items, may carry more signal.")
    evaluation = {"protocol": "hayat-link-prediction-v2", "seed": seed, "graph_sha256": digest([count, edges]),
                  "features_sha256": digest(features.tolist()) if features is not None else None,
                  "test_share": TEST_SHARE, "validation_share": VALIDATION_SHARE, "margin": MARGIN,
                  "profile": profile, "epochs_requested": epochs,
                  "split_sha256": digest([train, validation, test, validation_negatives, test_negatives]),
                  "negative_partition": "Evaluation non-links excluded from all training negatives; validation and test disjoint.",
                  "environment": environment("torch", "numpy", "scikit-learn"),
                  "reproducibility": "seeded split and initial weights; scores can differ slightly across machines",
                  "scope": "Hidden links of this graph only; records fingerprints, not the graph."}
    evaluation["comparison_key"] = digest({key: evaluation[key] for key in
                                           ("protocol", "seed", "graph_sha256", "features_sha256", "environment",
                                            "profile", "epochs_requested", "split_sha256")})
    result["evaluation"] = evaluation
    result["metric"] = network["auc"]
    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def memory_graph(path: Path | str, limit: int = MAX_NODES) -> tuple[int, list[tuple[int, int]], list[str]]:
    """The links between memories, read-only: (node count, edges, short labels). Text stays on this computer."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("The memory store was not found.")
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        links = db.execute("SELECT source_id, target_id FROM memory_links LIMIT ?", (MAX_EDGES,)).fetchall()
        ids = sorted({value for link in links for value in link})[:limit]
        position = {value: index for index, value in enumerate(ids)}
        labels = [""] * len(ids)
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for identity, content in db.execute(f"SELECT id, substr(content, 1, 80) FROM memories WHERE id IN ({marks})",  # noqa: S608 -- placeholders only
                                                chunk):
                labels[position[identity]] = " ".join(str(content).split())
    edges = [(position[a], position[b]) for a, b in links if a in position and b in position]
    return len(ids), edges, labels


def edge_file(path: Path | str) -> tuple[int, list[tuple[int, int]], list[str]]:
    """A CSV whose first two columns name the two ends of each link."""
    from app.model_discovery import load_table

    table = load_table(str(path))
    if table.shape[1] < 2:
        raise ValueError("The link table needs two columns: the two ends of each link.")
    ends = table.iloc[:, :2].dropna().astype(str)
    names = sorted(set(ends.iloc[:, 0]) | set(ends.iloc[:, 1]))
    position = {name: index for index, name in enumerate(names)}
    return len(names), [(position[a], position[b]) for a, b in ends.itertuples(index=False)], names


class GraphLab:
    """Trains and keeps link-prediction runs; `source` is "memory" or a CSV of links."""
    SOURCE = "graph_lab"

    def __init__(self, path: Path | str, memory_path: Any = None) -> None:
        self.path = Path(path)
        #: () -> path of the memory store, resolved when a run starts.
        self.memory_path = memory_path
        self._lock = threading.Lock()
        self.events: Any = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                   "source TEXT NOT NULL, auc REAL, beats INTEGER NOT NULL, seconds REAL NOT NULL, result_json TEXT NOT NULL)")
        return db

    def graph(self, source: str) -> tuple[int, list[tuple[int, int]], list[str]]:
        value = str(source or "memory").strip()
        if value.lower() == "memory":
            if not callable(self.memory_path):
                raise RuntimeError("The memory store isn't available in this session.")
            return memory_graph(self.memory_path())
        return edge_file(value)

    def run(self, source: str = "memory", profile: str = "balanced") -> dict[str, Any]:
        from app.lab_evidence import compare_metric

        source = str(source or "memory").strip() or "memory"
        count, edges, labels = self.graph(source)
        result = learn_links(count, edges, labels=labels, profile=profile)
        result["source"] = "links between HAYAT's memories" if source.lower() == "memory" else source
        with self._lock, closing(self._db()) as db, db:
            previous = db.execute("SELECT id,result_json FROM runs WHERE source=? ORDER BY id DESC LIMIT 1",
                                  (result["source"],)).fetchone()
            result["comparison"] = compare_metric(result, json.loads(previous["result_json"]) if previous else None)
            cursor = db.execute("INSERT INTO runs(at,source,auc,beats,seconds,result_json) VALUES(?,?,?,?,?,?)",
                                (datetime.now(UTC).isoformat(timespec="seconds"), result["source"], result["metric"],
                                 int(result["network_beats_baselines"]), result["seconds"], json.dumps(result, default=str)))
            result["run_id"] = int(cursor.lastrowid or 0)
        if self.events is not None:
            try:
                self.events.publish("LAB_RUN_COMPLETED", self.SOURCE, {"run_id": result["run_id"], "auc": result["metric"]})
            except Exception:
                log_swallowed(_log, "publishing a graph network run")
        return result

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT id,at,source,auc,beats,seconds FROM runs ORDER BY id DESC LIMIT ?",
                              (max(1, min(100, int(limit))),)).fetchall()
        return [{**dict(row), "beats": bool(row["beats"])} for row in rows]

    def detail(self, run_id: int) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT id,at,result_json FROM runs WHERE id=?", (int(run_id),)).fetchone()
        return {**json.loads(row["result_json"]), "run_id": row["id"], "at": row["at"]} if row else None

    def status(self) -> dict[str, Any]:
        rows = self.recent(1)
        if not rows:
            return {"runs": 0, "last": "none yet"}
        return {"runs": len(self.recent(100)), "last_auc": rows[0]["auc"],
                "last": "network beat the simple rules" if rows[0]["beats"] else "simple rules were as good or better"}


def register_graph_tools(registry: Any, lab: GraphLab) -> None:
    def run(action: str = "learn", source: str = "memory", run_id: int = 0, profile: str = "balanced") -> dict[str, Any]:
        if action == "recent":
            return {"runs": lab.recent()}
        if action == "detail":
            return {"run": lab.detail(run_id)}
        return lab.run(source, profile=profile)

    registry.register(
        "graph_learn",
        "Train a graph neural network (2-layer graph convolution) to predict missing links in a graph and measure it "
        "on hidden links against common neighbours, Adamic-Adar and preferential attachment. source is 'memory' (the "
        "links between HAYAT's own memories) or a CSV whose first two columns are the two ends of each link. Returns "
        "independent test AUC and average precision. Validation alone selects the suggestion method. The expanded "
        "profile trains at least 10 times as many parameters as balanced; this is capacity, not a guarantee of better "
        "predictions. Never creates a link.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["learn", "recent", "detail"]},
            "source": {"type": "string"}, "run_id": {"type": "integer", "minimum": 1},
            "profile": {"type": "string", "enum": ["balanced", "expanded"]}},
         "additionalProperties": False},
        "low", "filesystem_read", run)
