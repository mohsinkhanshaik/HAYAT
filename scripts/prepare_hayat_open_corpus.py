"""Select publisher-reviewed public conversations, preserving tree-level holdouts.

Human review fields come from OpenAssistant, not from the laptop owner. They
establish dataset selection criteria, not independent verification of every fact.
The owner's private registry is never opened or changed.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVISION = "fdf72ae0827c1cda404aff25b6603abec9e3399b"


def labels(row):
    value = row.get("labels") or {}
    return dict(zip(value.get("name", []), value.get("value", [])))


def eligible(row):
    scores = labels(row)
    return (row.get("lang") == "en" and row.get("review_result") is True
            and row.get("review_count", 0) >= 1 and not row.get("deleted")
            and not row.get("synthetic") and bool(str(row.get("text", "")).strip())
            and all(scores.get(key, 0) == 0 for key in ("spam", "pii", "lang_mismatch", "fails_task")))


def split_for_tree(tree_id, source_split):
    if source_split == "validation":
        return "test"
    return "validation" if int(hashlib.sha256(tree_id.encode()).hexdigest()[:8], 16) % 10 == 0 else "train"


def main():
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    import pyarrow.parquet as pq
    from transformers import AutoTokenizer
    from app.safety.sensitive import contains_secret
    from scripts.train_hayat_adapter import _format_pair, _validate_corpus

    dataset = ROOT / "data/datasets/oasst1"
    destination = ROOT / "data/models/hayat-open-4b/corpus"
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.glob("*.jsonl")):
        raise FileExistsError("Corpus already exists; preserve its hashes for the training run.")
    tokenizer = AutoTokenizer.from_pretrained(ROOT / "data/models/hayat-open-4b/foundation",
                                              local_files_only=True, trust_remote_code=False)
    system = (ROOT / "models/hayat-open/system.txt").read_text(encoding="utf-8").strip()
    selected = collections.defaultdict(list)
    rejected = collections.Counter()
    seen_prompts, seen_trees = set(), set()
    source_hashes, lengths = {}, []
    for source_split in ("validation", "train"):
        path, = (dataset / "data").glob(f"{source_split}-*.parquet")
        source_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = pq.read_table(path).to_pylist()
        by_id = {row["message_id"]: row for row in rows}
        for row in sorted(rows, key=lambda r: r["message_id"]):
            if row["role"] != "assistant":
                continue
            parent = by_id.get(row["parent_id"])
            if (not eligible(row) or row.get("rank") != 0 or labels(row).get("quality", 0) < .75
                    or not parent or parent.get("parent_id") is not None or not eligible(parent)):
                rejected["review_rank_quality_or_not_first_turn"] += 1
                continue
            prompt_key = re.sub(r"\s+", " ", parent["text"]).strip().casefold()
            tree = row["message_tree_id"]
            if prompt_key in seen_prompts or tree in seen_trees:
                rejected["duplicate_prompt_or_tree"] += 1
                continue
            if re.search(r"\b(?:I am|I'm|as) (?:an? )?(?:ChatGPT|OpenAssistant|language model)", row["text"], re.I):
                rejected["conflicting_identity"] += 1
                continue
            sample = {"messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": parent["text"].strip()},
                                    {"role": "assistant", "content": row["text"].strip()}]}
            split = split_for_tree(tree, source_split)
            canonical = json.dumps(sample, sort_keys=True, default=str)
            record = {
                "sample": sample, "category": "conversation", "privacy_scope": "STANDARD", "local_only": True,
                "split": split, "state": "HELD_OUT" if split == "test" else "TRAINING_READY",
                "content_hash": hashlib.sha256(canonical.encode()).hexdigest(),
                "provenance": {"source": "OpenAssistant/oasst1", "revision": REVISION,
                               "source_split": source_split, "message_id": row["message_id"],
                               "message_tree_id": tree, "license": "Apache-2.0"},
                "validation": {"human_approved": False, "deterministic_correct": False,
                    "verification": {"passed": True, "kind": "publisher_human_review_selection",
                        "review_result": row["review_result"], "review_count": row["review_count"],
                        "rank": row["rank"], "quality": labels(row)["quality"],
                        "scope": "Publisher-reviewed, top-ranked human answer; factual correctness not independently established."}},
                "consent": {"scope": "public_apache_2_dataset", "local_only": True}}
            if contains_secret(record):
                rejected["credential_like_content"] += 1
                continue
            pair = _format_pair(record, tokenizer)
            length = len(tokenizer(pair["prompt"] + pair["completion"])["input_ids"]) + 1
            if length > 1024:
                rejected["over_1024_tokens_no_truncation"] += 1
                continue
            selected[split].append(record)
            lengths.append(length)
            seen_prompts.add(prompt_key)
            seen_trees.add(tree)
    if sum(map(len, selected.values())) < 1000 or any(not selected[s] for s in ("train", "validation", "test")):
        raise ValueError(f"Insufficient selected data: {dict((s,len(v)) for s,v in selected.items())}")
    files = {}
    for split in ("train", "validation", "test"):
        path = destination / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected[split]), encoding="utf-8")
        files[split] = str(path)
    counts = _validate_corpus(files)
    report = {"dataset": "OpenAssistant/oasst1", "revision": REVISION, "license": "Apache-2.0",
              "split_counts": counts, "source_sha256": source_hashes,
              "dataset_sha256": {s: hashlib.sha256(Path(p).read_bytes()).hexdigest() for s,p in files.items()},
              "selection": "English, reviewed, rank 0, quality >= 0.75, first-turn, no labeled PII/spam/task failure",
              "max_tokens": max(lengths), "mean_tokens": sum(lengths)/len(lengths), "rejected": dict(rejected),
              "split_policy": "Original validation reserved for test; remaining trees hash-split 90/10; one example per tree",
              "limitations": ["Public 2023 human ratings are not independent factual verification.",
                              "English and first-turn selection narrows coverage; this is not a bias certification.",
                              "Exact normalized prompt/tree deduplication does not detect every paraphrase."]}
    (destination / "selection-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
