"""Check generated exercise answers with independently expressed calculations."""
import ast
import json
import re
import hashlib

import pytest

from scripts.prepare_hayat_reasoning_corpus import FAMILIES, exercise


@pytest.mark.parametrize("family", FAMILIES)
def test_oracles_match_literal_inputs_without_running_supplied_code(family):
    for seed in range(100, 120):
        prompt, answer = exercise(family, seed)
        if family == "multiplication":
            a, b = map(int, re.search(r"Calculate (\d+) \* (\d+)", prompt).groups())
            assert int(answer) == sum(a for _ in range(b))
        elif family == "precedence":
            a, b, c = map(int, re.search(r"Calculate (\d+) / (\d+) \+ (\d+)", prompt).groups())
            assert int(answer) * b == a + c * b
        elif family in {"median", "unique_count", "sort"}:
            values = ast.literal_eval(re.search(r"\[.*\]", prompt).group())
            if family == "median":
                assert int(answer) == sorted(values)[len(values) // 2]
            elif family == "unique_count":
                assert int(answer) == sum(v not in values[:i] for i, v in enumerate(values))
            else:
                actual = json.loads(answer)
                assert all(a < b for a,b in zip(actual, actual[1:])) and set(actual) == set(values)
        elif family == "reverse":
            word = re.search(r"'([a-z]+)'", prompt).group(1)
            assert len(answer) == len(word) and all(answer[i] == word[len(word)-i-1] for i in range(len(word)))
        else:
            city = re.search(r"Who lives in (\w+)\?", prompt).group(1)
            assert re.search(rf"(\w+) lives in {city}\.", prompt).group(1) == answer


def test_approval_flag_and_rehashed_wrong_target_cannot_bypass_the_oracle(tmp_path):
    from scripts.train_hayat_adapter import _validate_corpus
    prompt, _ = exercise("multiplication", 100001)
    sample = {"messages": [{"role": "system", "content": "Local assistant"},
                          {"role": "user", "content": prompt}, {"role": "assistant", "content": "WRONG"}]}
    row = {"sample": sample, "split": "train", "state": "TRAINING_READY", "local_only": True,
           "privacy_scope": "STANDARD", "category": "conversation",
           "provenance": {"source": "HAYAT deterministic exercises v2", "family": "multiplication", "seed": 100001},
           "validation": {"deterministic_correct": True},
           "content_hash": hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest()}
    path = tmp_path / "train.jsonl"
    path.write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match="did not match its oracle"):
        _validate_corpus({"train": str(path)})
