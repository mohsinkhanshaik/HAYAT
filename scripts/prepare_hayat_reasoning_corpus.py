"""Add reproducible oracle-checked practice while preserving public holdouts."""
from __future__ import annotations

import hashlib
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FAMILIES = ("multiplication", "precedence", "median", "unique_count", "sort", "reverse", "passage")


def exercise(family: str, seed: int) -> tuple[str, str]:
    rng = random.Random(seed)
    if family == "multiplication":
        a, b = rng.randint(11, 99), rng.randint(11, 99)
        question, answer = f"Calculate {a} * {b}.", str(a * b)
    elif family == "precedence":
        a, b, c = rng.randint(2, 49), rng.randint(2, 19), rng.randint(1, 49)
        question, answer = f"Calculate {a * b} / {b} + {c}.", str(a + c)
    elif family == "median":
        values = rng.sample(range(-99, 100), rng.choice([3, 5, 7]))
        question, answer = f"What is the median of {values}?", str(statistics.median(values))
    elif family == "unique_count":
        values = [rng.randint(-8, 8) for _ in range(rng.randint(4, 12))]
        question, answer = f"What does Python print for len(set({values}))?", str(len(set(values)))
    elif family == "sort":
        values = [rng.randint(-30, 30) for _ in range(rng.randint(4, 12))]
        question, answer = f"Sort and deduplicate {values} in ascending order.", json.dumps(sorted(set(values)))
    elif family == "reverse":
        value = "".join(rng.choices("abcdefghijklmnopqrstuvwxyz", k=rng.randint(4, 12)))
        question, answer = f"Reverse the characters in '{value}'.", value[::-1]
    elif family == "passage":
        names = rng.sample(["Lina", "Omar", "Maya", "Noah", "Sara", "Adam", "Iris", "Theo"], 4)
        cities = rng.sample(["Rome", "Oslo", "Tokyo", "Cairo", "Lima", "Dakar", "Paris", "Delhi"], 4)
        which = rng.randrange(4)
        question = "Passage: " + " ".join(f"{n} lives in {c}." for n, c in zip(names, cities))
        question += f" Who lives in {cities[which]}?"
        answer = names[which]
    else:
        raise ValueError(f"Unknown exercise family: {family}")
    return question + " Return only the answer.", answer


def main() -> None:
    from scripts.train_hayat_adapter import _validate_corpus
    source = ROOT / "data/models/hayat-open-4b/corpus"
    destination = ROOT / "data/models/hayat-open-4b/reasoning-corpus-v2"
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("Preserve the existing candidate corpus and its hashes.")
    destination.mkdir(parents=True, exist_ok=True)
    system = (ROOT / "models/hayat-open/system.txt").read_text(encoding="utf-8").strip()
    # Source test examples remain test examples. Never turn evaluation outputs
    # or old practice answers into training targets.
    seen, files, generated = set(), {}, {}
    originals = {s: [json.loads(line) for line in (source / f"{s}.jsonl").read_text(encoding="utf-8").splitlines()]
                 for s in ("train", "validation", "test")}
    for rows in originals.values():
        seen.update(row["sample"]["messages"][-2]["content"].casefold().strip() for row in rows)
    for split, count, offset in (("test", 140, 900000), ("validation", 140, 800000), ("train", 1120, 100000)):
        rows, added, attempt = list(originals[split]), 0, 0
        while added < count:
            family, seed = FAMILIES[attempt % len(FAMILIES)], offset + attempt
            attempt += 1
            question, answer = exercise(family, seed)
            if question.casefold() in seen:
                continue
            seen.add(question.casefold())
            sample = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": question},
                                   {"role": "assistant", "content": answer}]}
            rows.append({"sample": sample, "category": "conversation", "privacy_scope": "STANDARD", "local_only": True,
                "split": split, "state": "HELD_OUT" if split == "test" else "TRAINING_READY",
                "content_hash": hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest(),
                "provenance": {"source": "HAYAT deterministic exercises v2", "family": family, "seed": seed,
                               "oracle": "Reproducible Python arithmetic, statistics, collections, string or passage contract"},
                "validation": {"human_approved": False, "deterministic_correct": True},
                "consent": {"scope": "owner_requested_local_synthetic_practice", "local_only": True}})
            added += 1
        path = destination / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        files[split], generated[split] = str(path), added
    report = {"split_counts": _validate_corpus(files), "generated_counts": generated,
              "original_sha256": {s: hashlib.sha256((source / f"{s}.jsonl").read_bytes()).hexdigest() for s in originals},
              "dataset_sha256": {s: hashlib.sha256(Path(p).read_bytes()).hexdigest() for s, p in files.items()},
              "limitations": "Randomized instances of shared exercise families; measures within-family generalization, not unseen-task intelligence. Original public test is untouched."}
    (destination / "selection-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
