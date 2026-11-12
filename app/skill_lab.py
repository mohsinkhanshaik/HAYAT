"""Three questions about learning, each answered by a measurement rather than a claim.

The setting is small enough to measure exactly: problems are "turn this list of numbers into that result", given as
a few examples. A problem is solved by finding a program, built from a fixed set of operations, that reproduces the
examples; whether it really generalises is checked on examples the search never saw.

1. **Can a learned strategy solve unfamiliar problems?** After solving a set of practice problems, the pieces that
   recur across their solutions (for instance "sum of the squares") are kept as *concepts*: single steps in later
   searches. Unfamiliar problems, none of them seen in practice, are then attempted twice under the same search
   budget: without the concepts and with them.
2. **Can it improve a model of its own?** A small neural network is trained on problems whose solutions were
   verified by running them. It learns to predict, from the examples alone, which operations a solution uses, and
   steers the search. It is exported as plain weight arrays and used with a few lines of arithmetic: no language
   model is involved in training or in use. It is kept only if it solves more held-out problems than no guidance.
3. **Can it discover a useful new concept?** Each concept is a hypothesis that a combination is worth a name. It is
   tested by removing it: a concept whose removal loses no unfamiliar problem is reported as not useful. A program
   that fits the examples but fails the checking examples is a refuted hypothesis; the examples that refuted it
   are added and the search continues.

What this does and does not show is stated in every result. It shows these three things happening, measurably, in a
domain of list programs. It does not show that HAYAT's language model has learned anything: its weights are not
touched here, and nothing in this module is a claim about general intelligence.
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

BOUND = 10**9
LIMITS = ("Measured on list-transformation problems with a fixed set of operations. It shows a strategy, a trained "
          "network and a concept each improving results on problems not seen before, in this domain. It is not "
          "evidence about HAYAT's language model, whose weights are not changed here, or about problems outside it. "
          "A solution is checked on examples held back from the search, not proved: it can differ from the intended "
          "rule on a case no example covers. With a large enough search budget plain search reaches the same "
          "problems, so what is learned buys speed within a budget, not new reach beyond the operations.")


def _keep(values: list[int]) -> list[int] | None:
    return values if all(-BOUND <= value <= BOUND for value in values) else None


def _number(value: int) -> int | None:
    return value if -BOUND <= value <= BOUND else None


@dataclass(frozen=True)
class Op:
    name: str
    takes: tuple[str, ...]            # "L" a list of whole numbers, "I" one whole number
    gives: str
    run: Callable[..., Any]
    chain: tuple[str, ...] = ()       # a concept: the operations it stands for, outermost first


BASE: tuple[Op, ...] = (
    Op("reverse", ("L",), "L", lambda v: v[::-1]),
    Op("sort", ("L",), "L", lambda v: sorted(v)),
    Op("tail", ("L",), "L", lambda v: v[1:]),
    Op("init", ("L",), "L", lambda v: v[:-1]),
    Op("evens", ("L",), "L", lambda v: [n for n in v if n % 2 == 0]),
    Op("odds", ("L",), "L", lambda v: [n for n in v if n % 2]),
    Op("positives", ("L",), "L", lambda v: [n for n in v if n > 0]),
    Op("dedupe", ("L",), "L", lambda v: list(dict.fromkeys(v))),
    Op("double_all", ("L",), "L", lambda v: _keep([n * 2 for n in v])),
    Op("inc_all", ("L",), "L", lambda v: [n + 1 for n in v]),
    Op("square_all", ("L",), "L", lambda v: _keep([n * n for n in v])),
    Op("negate_all", ("L",), "L", lambda v: [-n for n in v]),
    Op("sum", ("L",), "I", lambda v: _number(sum(v))),
    Op("length", ("L",), "I", lambda v: len(v)),
    Op("head", ("L",), "I", lambda v: v[0] if v else 0),
    Op("last", ("L",), "I", lambda v: v[-1] if v else 0),
    Op("maximum", ("L",), "I", lambda v: max(v) if v else 0),
    Op("minimum", ("L",), "I", lambda v: min(v) if v else 0),
    Op("inc", ("I",), "I", lambda n: n + 1),
    Op("double", ("I",), "I", lambda n: _number(n * 2)),
    Op("square", ("I",), "I", lambda n: _number(n * n)),
    Op("negate", ("I",), "I", lambda n: -n),
    Op("add", ("I", "I"), "I", lambda a, b: _number(a + b)),
    Op("sub", ("I", "I"), "I", lambda a, b: _number(a - b)),
    Op("mul", ("I", "I"), "I", lambda a, b: _number(a * b)),
)
BY_NAME = {op.name: op for op in BASE}
X = ("x",)


def concept(chain: tuple[str, ...]) -> Op:
    """A named combination: the operations of ``chain`` applied innermost-last, as one step."""
    steps = [BY_NAME[name] for name in chain]
    if len(steps) < 2 or any(len(step.takes) != 1 for step in steps) or any(
            outer.takes[0] != inner.gives for outer, inner in zip(steps, steps[1:])):
        raise ValueError("A concept is two or more one-input operations whose types fit together.")

    def run(value: Any) -> Any:
        for step in reversed(steps):
            if value is None:
                return None
            value = step.run(value)
        return value
    return Op("·".join(chain), steps[-1].takes, steps[0].gives, run, tuple(chain))


def show(program: tuple) -> str:
    """The program written out in base operations (concepts expanded), so it can be read and checked."""
    if program == X:
        return "x"
    name, *parts = program
    inner = ", ".join(show(part) for part in parts)
    for step in reversed(name.split("·")):
        inner = f"{step}({inner})"
    return inner


def execute(program: tuple, value: list[int], ops: dict[str, Op] | None = None) -> Any:
    if program == X:
        return list(value)
    name, *parts = program
    op = (ops or BY_NAME).get(name) or concept(tuple(name.split("·")))
    arguments = [execute(part, value, ops) for part in parts]
    if any(argument is None for argument in arguments):
        return None
    return op.run(*arguments)


def _frozen(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def synthesise(inputs: list[list[int]], outputs: list[Any], ops: tuple[Op, ...], budget: int,
               max_size: int = 9) -> tuple[tuple | None, int]:
    """The smallest program reproducing the examples, by bottom-up search; (program or None, programs tried).

    Programs are built in order of size. Two programs that give the same results on every example are the same
    hypothesis, so only the first is kept: the search is over behaviours, not spellings.
    """
    target = tuple(_frozen(value) for value in outputs)
    wanted = "L" if isinstance(outputs[0], list) else "I"
    start = tuple(tuple(value) for value in inputs)
    if wanted == "L" and start == target:
        return X, 1
    bank: dict[int, list[tuple[tuple, tuple, str]]] = {1: [(X, start, "L")]}
    seen = {("L", start)}
    tried = 1
    unary = [op for op in ops if len(op.takes) == 1]
    binary = [op for op in ops if len(op.takes) == 2]
    for size in range(2, max_size + 1):
        fresh: list[tuple[tuple, tuple, str]] = []

        def consider(program: tuple, values: tuple, kind: str) -> bool:
            if (kind, values) in seen:
                return False
            seen.add((kind, values))
            fresh.append((program, values, kind))
            return kind == wanted and values == target

        # Combinations of two numbers first: there are few numbers and many lists, so trying every one-input step
        # on every list before any combination spent the whole budget before a combination was ever tried.
        for op in binary:
            for left_size in range(1, size - 1):
                for left, left_values, left_kind in bank.get(left_size, ()):
                    if left_kind != "I":
                        continue
                    for right, right_values, right_kind in bank.get(size - 1 - left_size, ()):
                        if right_kind != "I":
                            continue
                        tried += 1
                        results = []
                        for a, b in zip(left_values, right_values):
                            result = op.run(a, b)
                            if result is None:
                                break
                            results.append(result)
                        else:
                            if consider((op.name, left, right), tuple(results), "I"):
                                return (op.name, left, right), tried
                        if tried >= budget:
                            return None, tried
        for op in unary:
            for program, values, kind in bank.get(size - 1, ()):
                if kind != op.takes[0]:
                    continue
                tried += 1
                results = []
                for value in values:
                    result = op.run(list(value) if kind == "L" else value)
                    if result is None:
                        break
                    results.append(_frozen(result))
                else:
                    if consider((op.name, program), tuple(results), op.gives):
                        return (op.name, program), tried
                if tried >= budget:
                    return None, tried
        bank[size] = fresh
        if not fresh:
            break
    return None, tried


@dataclass(frozen=True)
class Task:
    name: str
    examples: tuple[tuple[tuple[int, ...], Any], ...]     # (input, output); outputs are ints or tuples
    seen: int = 3                                         # shown to the search
    checks: int = 3                                       # used to refute a program that fits by accident

    def split(self) -> tuple[list, list, list]:
        rows = [(list(given), list(result) if isinstance(result, tuple) else result) for given, result in self.examples]
        return rows[:self.seen], rows[self.seen:self.seen + self.checks], rows[self.seen + self.checks:]


def make_task(name: str, program: tuple, seed: int, count: int = 10) -> Task:
    """A problem defined by a hidden program and random inputs. Only the examples are ever shown to a solver."""
    rng = random.Random(seed)  # noqa: S311 -- problem generation, not security
    rows: list[tuple[tuple[int, ...], Any]] = []
    while len(rows) < count:
        given = [rng.randint(-9, 9) for _ in range(rng.randint(3, 7))]
        result = execute(program, given)
        if result is not None:
            rows.append((tuple(given), _frozen(result)))
    return Task(name, tuple(rows))


def solve(task: Task, ops: tuple[Op, ...], budget: int, order: Callable[[list, list], tuple[Op, ...]] | None = None) -> dict[str, Any]:
    """Search, check, and if the checking examples refute the program, add them and search again."""
    shown, checks, tests = task.split()
    examples, refuted, tried_total = list(shown), 0, 0
    names = {op.name: op for op in ops}
    program = None
    for _round in range(6):   # each round a refuted program adds the examples that refuted it
        inputs, outputs = [row[0] for row in examples], [row[1] for row in examples]
        left = budget - tried_total
        if left <= 0:
            program = None
            break
        if order is not None:   # guided: the operations the network expects first, then all of them
            first = order(inputs, outputs)
            program, tried = synthesise(inputs, outputs, first, left // 2)
            if program is None:
                again, more = synthesise(inputs, outputs, ops, left - tried)
                program, tried = again, tried + more
        else:
            program, tried = synthesise(inputs, outputs, ops, left)
        tried_total += tried
        if program is None:
            break
        wrong = [row for row in checks if _frozen(execute(program, row[0], names)) != _frozen(row[1])]
        if not wrong:
            break
        refuted += 1                                         # fitted the examples, failed the checks: a refuted hypothesis
        examples += wrong
        checks = [row for row in checks if row not in wrong]
        program = None
    passed = bool(program is not None and tests
                  and all(_frozen(execute(program, row[0], names)) == _frozen(row[1]) for row in tests))
    return {"task": task.name, "solved": passed, "program": show(program) if program is not None else None,
            "steps": program, "tried": tried_total, "refuted_hypotheses": refuted,
            "fits_but_fails_unseen": bool(program is not None and not passed)}


def chains(program: tuple) -> list[tuple[str, ...]]:
    """Every run of two or three one-input steps inside a program: the candidates for a concept."""
    found: list[tuple[str, ...]] = []

    def walk(node: tuple) -> None:
        if node == X:
            return
        run, at = [], node
        while at != X and len(at) == 2:
            run += at[0].split("·")
            at = at[1]
        for length in (2, 3):
            found.extend(tuple(run[start:start + length]) for start in range(len(run) - length + 1))
        if at != X:
            for part in at[1:]:
                walk(part)
        # the links below the first step of a run are visited through the longer runs above
    walk(program)
    return found


def learn_concepts(solutions: list[tuple], limit: int = 6) -> list[tuple[str, ...]]:
    """Combinations that recur in the solutions of at least two different problems, most saving first."""
    counts: dict[tuple[str, ...], int] = {}
    for program in solutions:
        for chain in set(chains(program)):
            counts[chain] = counts.get(chain, 0) + 1
    recurring = [chain for chain, count in counts.items() if count >= 2]
    return sorted(recurring, key=lambda chain: (-counts[chain] * (len(chain) - 1), chain))[:limit]


# ---------------------------------------------------------------------------------------------- the trained guide

GUIDE_OPS = tuple(op.name for op in BASE)


def features(inputs: list[list[int]], outputs: list[Any]) -> list[float]:
    """What the examples look like, averaged: the only thing the network sees."""
    rows = []
    for given, result in zip(inputs, outputs):
        size, total = max(1, len(given)), sum(given)
        if isinstance(result, (list, tuple)):
            out = list(result)
            row = [0.0, len(out) / size, float(out == sorted(out)), float(out == sorted(out, reverse=True)),
                   float(all(n % 2 == 0 for n in out)), float(all(n % 2 for n in out)), float(all(n > 0 for n in out)),
                   float(all(n >= 0 for n in out)), float(set(out) <= set(given)), float(len(out) == len(set(out))),
                   float(out == given[::-1]), float(len(out) == len(given)), float(len(out) == len(given) - 1),
                   float(sum(out) == total), float(sum(out) == -total), float(sum(out) == 2 * total),
                   float(sum(out) == total + len(given)), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        else:
            n = int(result)
            row = [1.0] + [0.0] * 16 + [float(n == total), float(n == len(given)), float(n == (given[0] if given else 0)),
                                        float(n == (given[-1] if given else 0)), float(n == max(given, default=0)),
                                        float(n == min(given, default=0)), float(n >= 0), float(n % 2 == 0),
                                        float(abs(n) > 4 * (abs(total) + size))]
        rows.append(row)
    return [sum(column) / len(rows) for column in zip(*rows)]


def random_program(rng: random.Random, size: int) -> tuple:
    """A random well-typed program of about ``size`` steps, for self-made practice problems."""
    program, kind = X, "L"
    for _ in range(size):
        options = [op for op in BASE if len(op.takes) == 1 and op.takes[0] == kind]
        if kind == "I" and rng.random() < 0.3:
            other, other_kind = X, "L"
            for _ in range(rng.randint(1, 2)):
                choices = [op for op in BASE if len(op.takes) == 1 and op.takes[0] == other_kind]
                step = rng.choice(choices)
                other, other_kind = (step.name, other), step.gives
            if other_kind == "I":
                program = (rng.choice(("add", "sub", "mul")), program, other)
                continue
        step = rng.choice(options)
        program, kind = (step.name, program), step.gives
    return program


def used(program: tuple) -> set[str]:
    return set() if program == X else {name for name in program[0].split("·")} | {name for part in program[1:] for name in used(part)}


def practice(count: int, seed: int, sizes: tuple[int, ...] = (2, 3, 4, 5)) -> list[tuple[Task, tuple]]:
    """Self-made problems with known answers. A degenerate one (the same output for every input) is thrown away."""
    rng, made = random.Random(seed), []  # noqa: S311
    while len(made) < count:
        program = random_program(rng, rng.choice(sizes))
        task = make_task(f"practice-{len(made)}", program, rng.randrange(10**9))
        if len({row[1] for row in task.examples}) > 2:
            made.append((task, program))
    return made


def train_guide(problems: list[tuple[Task, tuple]], seed: int = 0) -> dict[str, Any]:
    """Fit the network on verified problems; returns its weights as plain lists."""
    import numpy as np
    from sklearn.neural_network import MLPClassifier

    x = np.array([features([list(row[0]) for row in task.examples[:task.seen]],
                           [row[1] for row in task.examples[:task.seen]]) for task, _program in problems])
    y = np.array([[float(name in used(program)) for name in GUIDE_OPS] for _task, program in problems])
    network = MLPClassifier(hidden_layer_sizes=(48,), max_iter=400, random_state=seed)
    network.fit(x, y)
    return {"weights": [layer.tolist() for layer in network.coefs_], "biases": [layer.tolist() for layer in network.intercepts_],
            "operations": list(GUIDE_OPS), "trained_on": len(problems), "inputs": int(x.shape[1]),
            "parameters": int(sum(layer.size for layer in network.coefs_) + sum(layer.size for layer in network.intercepts_))}


def guide_scores(guide: dict[str, Any], inputs: list[list[int]], outputs: list[Any]) -> dict[str, float]:
    """The network run by hand: two matrix products. Nothing but the saved weights is needed."""
    import numpy as np

    value = np.array(features(inputs, outputs))
    layers = list(zip(guide["weights"], guide["biases"]))
    for index, (weights, biases) in enumerate(layers):
        value = value @ np.array(weights) + np.array(biases)
        value = np.maximum(value, 0) if index < len(layers) - 1 else 1 / (1 + np.exp(-value))
    return dict(zip(guide["operations"], (float(item) for item in value)))


def guided_order(guide: dict[str, Any], extra: tuple[Op, ...] = (), keep: int = 10) -> Callable[[list, list], tuple[Op, ...]]:
    def order(inputs: list, outputs: list) -> tuple[Op, ...]:
        scores = guide_scores(guide, inputs, outputs)
        best = sorted(BASE, key=lambda op: -scores.get(op.name, 0.0))[:keep]
        return tuple(best) + tuple(extra)
    return order


# ---------------------------------------------------------------------------------------------- the experiment

PRACTICE = {  # solved first; none of these is among the unfamiliar problems. Each strategy appears in two of them.
    "sum of squares": ("sum", ("square_all", X)),
    "one more than the sum of squares": ("inc", ("sum", ("square_all", X))),
    "count of evens": ("length", ("evens", X)),
    "twice the count of evens": ("double", ("length", ("evens", X))),
    "largest of the positives": ("maximum", ("positives", X)),
    "square of the largest positive": ("square", ("maximum", ("positives", X))),
    "sorted without repeats": ("sort", ("dedupe", X)),
    "sorted without repeats, largest first": ("reverse", ("sort", ("dedupe", X))),
}
UNFAMILIAR = {  # different problems that need the same strategies combined in ways practice never showed
    "sum of squares of the evens": ("sum", ("square_all", ("evens", X))),
    "sum of squares minus the count of evens": ("sub", ("sum", ("square_all", X)), ("length", ("evens", X))),
    "largest positive times the count of evens": ("mul", ("maximum", ("positives", X)), ("length", ("evens", X))),
    "sum of squares of the odds plus the largest positive": ("add", ("sum", ("square_all", ("odds", X))), ("maximum", ("positives", X))),
    "count of evens among the distinct sorted values": ("length", ("evens", ("sort", ("dedupe", X)))),
    "sum of squares of the positives minus the largest positive": ("sub", ("sum", ("square_all", ("positives", X))), ("maximum", ("positives", X))),
    "largest positive plus the count of evens, doubled": ("double", ("add", ("maximum", ("positives", X)), ("length", ("evens", X)))),
    "sum of squares of the distinct sorted values, less the first": ("sub", ("sum", ("square_all", ("sort", ("dedupe", X)))), ("head", X)),
}


def experiment(budget: int = 60_000, seed: int = 0, practice_problems: int = 600, held_out: int = 60) -> dict[str, Any]:
    """Run all three measurements and report them; see the module docstring."""
    from app.lab_evidence import digest, environment

    started = time.perf_counter()
    training = [make_task(name, program, seed * 1000 + index) for index, (name, program) in enumerate(PRACTICE.items())]
    unfamiliar = [make_task(name, program, seed * 1000 + 500 + index) for index, (name, program) in enumerate(UNFAMILIAR.items())]
    # ---- 1 and 3: learn from practice, then meet unfamiliar problems with and without what was learned
    practised = [solve(task, BASE, budget) for task in training]
    learned = learn_concepts([result["steps"] for result in practised if result["solved"]])
    library = tuple(concept(chain) for chain in learned)
    without = [solve(task, BASE, budget) for task in unfamiliar]
    with_library = [solve(task, BASE + library, budget) for task in unfamiliar]
    solved_without, solved_with = sum(r["solved"] for r in without), sum(r["solved"] for r in with_library)
    judged = []
    for chain in learned:   # a concept is useful if taking it away loses a problem
        rest = tuple(op for op in library if op.chain != chain)
        kept = sum(solve(task, BASE + rest, budget)["solved"] for task in unfamiliar)
        judged.append({"concept": " of ".join(chain), "problems_lost_without_it": solved_with - kept,
                       "useful": solved_with - kept > 0})
    # ---- 2: train a network on verified practice problems; keep it only if it beats no guidance on held-out ones
    made = practice(practice_problems, seed + 1)
    guide = train_guide(made, seed)
    unseen = [pair for pair in practice(held_out, seed + 7919, sizes=(4, 5, 6))]
    tight = max(2_000, budget // 20)                                   # a budget where guidance has to matter
    plain = [solve(task, BASE, tight) for task, _program in unseen]
    guided = [solve(task, BASE, tight, guided_order(guide)) for task, _program in unseen]
    plain_solved, guided_solved = sum(r["solved"] for r in plain), sum(r["solved"] for r in guided)
    median = lambda rows: sorted(row["tried"] for row in rows)[len(rows) // 2]   # noqa: E731
    kept = guided_solved > plain_solved
    result: dict[str, Any] = {
        "strategy_transfer": {
            "question": "Can a strategy learned on practice problems solve unfamiliar ones?",
            "practice_solved": f"{sum(r['solved'] for r in practised)} of {len(practised)}",
            "unfamiliar_problems": len(unfamiliar), "solved_without_what_was_learned": solved_without,
            "solved_with_what_was_learned": solved_with, "search_budget": budget,
            "newly_solved": [r["task"] for r, before in zip(with_library, without) if r["solved"] and not before["solved"]],
            "still_unsolved": [r["task"] for r in with_library if not r["solved"]],
            "answer": ("Yes, here: " if solved_with > solved_without else "Not shown here: ")
                      + f"{solved_with} of {len(unfamiliar)} unfamiliar problems solved with the learned concepts, "
                        f"{solved_without} without, under the same budget of {budget:,} programs."},
        "trained_model": {
            "question": "Can a network trained on verified experience improve results with no large model in use?",
            "network": f"{guide['inputs']} inputs → 48 → {len(GUIDE_OPS)} outputs, {guide['parameters']:,} weights",
            "trained_on": f"{guide['trained_on']} self-made problems, each verified by running its program",
            "held_out_problems": len(unseen), "solved_unguided": plain_solved, "solved_guided": guided_solved,
            "median_programs_tried_unguided": median(plain), "median_programs_tried_guided": median(guided),
            "search_budget": tight, "kept": kept,
            "inference": "two matrix products over the saved weights; no language model is called",
            "answer": (f"Yes, here: guided search solved {guided_solved} of {len(unseen)} held-out problems against "
                       f"{plain_solved} unguided, so the network is kept." if kept else
                       f"Not shown here: guided search solved {guided_solved} of {len(unseen)} held-out problems, "
                       f"unguided {plain_solved}. The network is not kept.")},
        "concept_discovery": {
            "question": "Can it discover a new concept that is actually useful?",
            "concepts": judged, "useful_concepts": sum(row["useful"] for row in judged),
            "refuted_hypotheses": sum(r["refuted_hypotheses"] for r in practised + without + with_library),
            "answer": (f"Yes, here: {sum(row['useful'] for row in judged)} of {len(judged)} discovered concepts are "
                       "needed (removing one loses an unfamiliar problem); the rest are reported as not useful."
                       if any(row["useful"] for row in judged) else
                       "Not shown here: no discovered concept was needed for any unfamiliar problem.")},
        "tool": "HAYAT skill laboratory (bottom-up program search, scikit-learn network)", "limits": LIMITS,
        "next": "Add operations or problems from your own work with the 'learn' action; a concept or a network is "
                "kept only while it keeps beating the comparison.",
    }
    result["guide"] = guide if kept else None
    result["library"] = [list(chain) for chain in learned]
    result["solutions"] = {r["task"]: r["program"] for r in with_library if r["solved"]}
    evaluation = {"protocol": "hayat-skill-experiment-v1", "seed": seed, "budget": budget,
                  "problems_sha256": digest([task.examples for task in training + unfamiliar]),
                  "practice_problems": practice_problems, "held_out": held_out,
                  "environment": environment("numpy", "scikit-learn"),
                  "scope": "Seeded problems and search; the same seed gives the same problems."}
    evaluation["comparison_key"] = digest(evaluation)
    result["evaluation"] = evaluation
    result["metric"] = solved_with / len(unfamiliar)
    result["seconds"] = round(time.perf_counter() - started, 2)
    result["verdict"] = " ".join(result[key]["answer"] for key in ("strategy_transfer", "trained_model", "concept_discovery"))
    return result


class SkillLab:
    """Keeps experiments, the concepts and network they earned, and skills learned from the owner's own examples."""
    SOURCE = "skill_lab"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.events: Any = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS experiments(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                metric REAL, seconds REAL NOT NULL, result_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS skills(name TEXT PRIMARY KEY, program TEXT NOT NULL, steps_json TEXT NOT NULL,
                learned_at TEXT NOT NULL, examples INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        """)
        return db

    def _state(self, key: str) -> Any:
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT value_json FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value_json"]) if row else None

    def library(self) -> tuple[Op, ...]:
        return tuple(concept(tuple(chain)) for chain in self._state("library") or [])

    def run_experiment(self, budget: int = 60_000) -> dict[str, Any]:
        from app.lab_evidence import compare_metric

        if isinstance(budget, bool) or not isinstance(budget, int) or not 2_000 <= budget <= 400_000:
            raise ValueError("Give a search budget between 2,000 and 400,000 programs.")
        result = experiment(budget)
        guide, library = result.pop("guide"), result["library"]
        useful = [row["concept"].split(" of ") for row in result["concept_discovery"]["concepts"] if row["useful"]]
        with self._lock, closing(self._db()) as db, db:
            previous = db.execute("SELECT result_json FROM experiments ORDER BY id DESC LIMIT 1").fetchone()
            result["comparison"] = compare_metric(result, json.loads(previous["result_json"]) if previous else None)
            cursor = db.execute("INSERT INTO experiments(at,metric,seconds,result_json) VALUES(?,?,?,?)",
                                (datetime.now(UTC).isoformat(timespec="seconds"), result["metric"], result["seconds"],
                                 json.dumps(result)))
            result["experiment_id"] = int(cursor.lastrowid or 0)
            # Only what earned its place is kept for later use: concepts that were needed, a network that won.
            db.execute("INSERT OR REPLACE INTO state(key,value_json) VALUES('library',?)", (json.dumps(useful),))
            if guide is not None:
                db.execute("INSERT OR REPLACE INTO state(key,value_json) VALUES('guide',?)", (json.dumps(guide),))
        result["kept_for_use"] = {"concepts": [" of ".join(chain) for chain in useful], "network": guide is not None,
                                  "discovered": len(library)}
        if self.events is not None:
            try:
                self.events.publish("LAB_RUN_COMPLETED", self.SOURCE, {"experiment_id": result["experiment_id"],
                                                                       "metric": result["metric"]})
            except Exception:
                log_swallowed(_log, "publishing a skill experiment")
        return result

    def learn(self, name: str, examples: list[dict[str, Any]], budget: int = 60_000) -> dict[str, Any]:
        """Learn a skill from the owner's examples ({"input": [...], "output": ...}); kept only if it passes
        examples that were held back from the search."""
        name = " ".join(str(name or "").split())[:80]
        if not name:
            raise ValueError("Give the skill a name.")
        rows = []
        for row in examples or []:
            given, result = row.get("input"), row.get("output")
            if not isinstance(given, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in given):
                raise ValueError("Each example needs an input list of whole numbers.")
            if isinstance(result, list):
                if not all(isinstance(n, int) and not isinstance(n, bool) for n in result):
                    raise ValueError("An output list must hold whole numbers.")
                result = tuple(result)
            elif not isinstance(result, int) or isinstance(result, bool):
                raise ValueError("Each example needs an output: a whole number or a list of them.")
            rows.append((tuple(given), result))
        if len(rows) < 5:
            raise ValueError("Give at least five examples: some are held back to check that the skill generalises.")
        held = max(2, len(rows) // 3)
        task = Task(name, tuple(rows), seen=len(rows) - 2 * held if len(rows) - 2 * held >= 2 else 2,
                    checks=held if len(rows) - 2 * held >= 2 else max(1, len(rows) - 2 - held))
        guide = self._state("guide")
        library = self.library()
        found = solve(task, BASE + library, budget, guided_order(guide, library) if guide else None)
        outcome = {"name": name, "learned": found["solved"], "program": found["program"], "programs_tried": found["tried"],
                   "refuted_hypotheses": found["refuted_hypotheses"], "examples_shown": task.seen,
                   "examples_held_back": len(rows) - task.seen, "used_concepts": [op.name for op in library],
                   "used_trained_network": guide is not None, "tool": "HAYAT skill laboratory", "limits": LIMITS}
        if found["solved"]:
            with self._lock, closing(self._db()) as db, db:
                db.execute("INSERT OR REPLACE INTO skills(name,program,steps_json,learned_at,examples) VALUES(?,?,?,?,?)",
                           (name, found["program"], json.dumps(found["steps"]),
                            datetime.now(UTC).isoformat(timespec="seconds"), len(rows)))
            outcome["verdict"] = (f"Learned “{name}”: {found['program']}. It reproduces the {outcome['examples_held_back']} "
                                  "examples that were held back from the search.")
        else:
            outcome["verdict"] = (f"Not learned: no program within {budget:,} tries reproduces every example"
                                  + (", including the ones held back" if found["fits_but_fails_unseen"] else "")
                                  + ". The operations available may not be able to express it.")
        return outcome

    def apply(self, name: str, value: list[int]) -> dict[str, Any]:
        if not isinstance(value, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in value):
            raise ValueError("Give a list of whole numbers.")
        with self._lock, closing(self._db()) as db, db:
            row = db.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone() if self.path.is_file() else None
            if row is None:
                raise ValueError(f"No learned skill is called {name!r}.")
            db.execute("UPDATE skills SET used=used+1 WHERE name=?", (name,))

        def steps(node: Any) -> tuple:
            return tuple(steps(part) if isinstance(part, list) else part for part in node)
        return {"name": name, "program": row["program"], "input": value, "output": execute(steps(json.loads(row["steps_json"])), value)}

    def skills(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(self._db()) as db:
            return [dict(row) | {"steps_json": None} for row in db.execute("SELECT * FROM skills ORDER BY learned_at DESC")]

    def report(self) -> dict[str, Any]:
        """The last experiment's answers, read from its record; nothing is run."""
        if not self.path.is_file():
            return {"experiments": 0, "last": None}
        with self._lock, closing(self._db()) as db:
            count = db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            row = db.execute("SELECT at,result_json FROM experiments ORDER BY id DESC LIMIT 1").fetchone()
        return {"experiments": count, "last": ({**json.loads(row["result_json"]), "at": row["at"]} if row else None),
                "skills": len(self.skills())}

    def status(self) -> dict[str, Any]:
        last = self.report().get("last") or {}
        return {"experiments": self.report()["experiments"], "learned_skills": len(self.skills()),
                "concepts_in_use": len(self._state("library") or []), "trained_network_in_use": self._state("guide") is not None,
                "last": (last.get("strategy_transfer") or {}).get("answer", "no experiment yet")}


def register_skill_tools(registry: Any, lab: SkillLab) -> None:
    def run(action: str = "report", name: str = "", examples: list[dict[str, Any]] | None = None,
            value: list[int] | None = None, budget: int = 60_000) -> dict[str, Any]:
        if action == "experiment":
            return lab.run_experiment(budget)
        if action == "learn":
            return lab.learn(name, examples or [], budget)
        if action == "apply":
            return lab.apply(name, value or [])
        if action == "skills":
            return {"skills": lab.skills(), "status": lab.status()}
        return lab.report()

    registry.register(
        "skill_lab",
        "Measure three things about learning in a small exact domain (programs over lists of whole numbers): whether "
        "strategies learned on practice problems solve unfamiliar ones, whether a small neural network trained on "
        "verified problems improves the search with no language model in use, and whether discovered concepts are "
        "actually needed. 'learn' finds a program from the owner's input/output examples and keeps it as a named "
        "skill only if it passes examples held back from the search; 'apply' runs a learned skill. Results state "
        "their limits: this is not evidence about the language model.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["report", "experiment", "learn", "apply", "skills"]},
            "name": {"type": "string", "maxLength": 80},
            "examples": {"type": "array", "maxItems": 40, "items": {"type": "object", "properties": {
                "input": {"type": "array", "maxItems": 30, "items": {"type": "integer"}},
                "output": {"type": ["integer", "array"], "items": {"type": "integer"}}},
                "required": ["input", "output"], "additionalProperties": False}},
            "value": {"type": "array", "maxItems": 30, "items": {"type": "integer"}},
            "budget": {"type": "integer", "minimum": 2000, "maximum": 400000}},
         "additionalProperties": False},
        "low", "system_read", run)
