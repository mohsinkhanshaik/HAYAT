"""The four laboratories: model discovery, robustness, code defence, and routing to them."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.code_defense import CodeDefense, confirm, fingerprint
from app.model_discovery import ModelDiscovery, discover, market_table, profile
from app.research_lab import direct_lab_plan, ghidra_status, lab_request
from app.robustness import BENIGN, INJECTED, RobustnessLab, injection_suite, misspell, model_suite, text_suite


def _table(rows: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    table = pd.DataFrame({"a": rng.normal(size=rows), "b": rng.normal(size=rows),
                          "kind": rng.choice(["x", "y"], rows), "same": 1})
    table["y"] = 2 * table["a"] - table["b"] + (table["kind"] == "x") + rng.normal(scale=0.4, size=rows)
    table["leak"] = table["y"] * 3 + 1
    return table


# ---------------------------------------------------------------------------------------------- model discovery

def test_a_real_relationship_is_found_and_the_disguised_target_is_left_out():
    result = discover(_table(), "y")
    assert result["beats_baseline"] and result["task"] == "regression"
    assert "leak" in result["left_out"] and "same" in result["left_out"]
    assert any("target in disguise" in warning for warning in result["integrity"])
    assert result["what_matters"][0]["feature"] == "a"
    assert result["uncertainty"]["measured_on"] == 80 and result["candidates"][0]["model"].startswith("baseline")


def test_noise_is_reported_as_nothing_predictable():
    rng = np.random.default_rng(4)
    noise = pd.DataFrame({"a": rng.normal(size=300), "b": rng.normal(size=300), "y": rng.normal(size=300)})
    result = discover(noise, "y")
    assert not result["beats_baseline"] and result["verdict"].startswith("Nothing predictable found")
    assert "what_matters" not in result


def test_time_ordered_data_is_validated_past_to_future_only():
    rng = np.random.default_rng(5)
    prices = 100 * np.cumprod(1 + rng.normal(scale=0.01, size=500))
    candles = [{"time": 1_700_000_000 + 3600 * index, "open": price, "high": price * 1.01, "low": price * 0.99,
                "close": price, "volume": 10.0 + index % 7, "trades": 5} for index, price in enumerate(prices)]
    table = market_table(candles)
    assert table["time"].is_monotonic_increasing and "next_return" in table
    # The target is strictly later than every feature: the last candle has no "next" and is dropped.
    assert len(table) < len(candles) - 48
    result = discover(table, "next_return", time_column="time")
    assert result["validation"].startswith("chronological")
    assert not result["beats_baseline"]  # a random walk: nothing to find, and it says so
    assert profile(table)["time_columns"] == ["time"]


def test_every_run_is_kept_with_its_source(tmp_path):
    path = tmp_path / "t.csv"
    _table().drop(columns=["leak"]).to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    result = discovery.run(str(path), "y")
    assert result["experiment_id"] == 1 and result["source"] == str(path) and result["seconds"] >= 0
    assert discovery.recent()[0]["beats_baseline"] and discovery.status()["experiments"] == 1
    assert discovery.last_fit["task"] == "regression"
    with pytest.raises(ValueError):
        discovery.run(str(path), "")  # no target named
    (tmp_path / "t.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError):
        discovery.run(str(tmp_path / "t.exe"), "y")  # only tables are read


# ---------------------------------------------------------------------------------------------- robustness

def test_misspelling_is_seeded_and_leaves_short_words_alone():
    import random

    first = misspell("please compare these different results", 1.0, random.Random(1))
    assert first == misspell("please compare these different results", 1.0, random.Random(1))
    assert first != "please compare these different results" and misspell("a bit odd", 1.0, random.Random(1)) == "a bit odd"


def test_the_injection_guard_catches_the_injected_passages_without_false_alarms():
    result = injection_suite()
    assert result["detection_rate"] == 1.0, result["missed"]
    assert result["false_alarm_rate"] == 0.0, result["false_alarms"]
    assert len(INJECTED) == len(BENIGN) == 8 and result["tool"] == "detect_prompt_injection"


def test_routing_holds_under_typing_noise():
    result = text_suite()
    assert [level["misspelt_share"] for level in result["levels"]] == [0.0, 0.1, 0.2]
    assert result["levels"][0]["relation_accuracy"] == 1.0 and result["metric"] >= 0.9
    assert all(level["contamination_rate"] == 0.0 for level in result["levels"])


def test_a_discovered_classifier_is_attacked_with_art_and_the_run_is_kept(tmp_path):
    pytest.importorskip("art")
    table = _table(300).drop(columns=["leak", "same", "kind"])
    table["label"] = np.where(table.pop("y") > 0, "up", "down")
    path = tmp_path / "labels.csv"
    table.to_csv(path, index=False)
    discovery = ModelDiscovery(tmp_path / "md.db")
    assert discovery.run(str(path), "label")["task"] == "classification"
    lab = RobustnessLab(tmp_path / "rob.db", discovery)
    assert model_suite(None)["headline"].startswith("No model to test yet")
    outcome = lab.run("model")
    result = outcome["results"][0]
    assert result["tool"].startswith("ART HopSkipJump") and result["tested"] == 30
    assert result["flipped"] >= 1 and result["median_change_needed"] > 0
    assert lab.history()[0]["suite"] == "model" and "model" in outcome["report"]["suites_run"]
    assert outcome["report"]["not_run_yet"] == ["text", "injection", "poisoning"]


# ---------------------------------------------------------------------------------------------- code defence

def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "keys.py").write_text(
        "import hashlib\n\n\ndef key(text):\n    return hashlib.md5(text.encode()).hexdigest()\n", encoding="utf-8")
    (root / "tests" / "test_keys.py").write_text(
        "from app.keys import key\n\n\ndef test_key():\n    assert len(key('a')) == 32\n", encoding="utf-8")

    def git(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=root, check=True, capture_output=True)  # noqa: S603, S607

    git("init", "-q")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "first")
    return root


def test_findings_are_confirmed_ranked_kept_and_marked_fixed_when_gone(tmp_path):
    root = _repository(tmp_path)
    defense = CodeDefense(tmp_path / "cd.db", root=root)
    scan = defense.scan()
    assert scan["confirmed_in_source"] == 1 and scan["new"] == 1 and scan["tool"].startswith("bandit")
    finding = defense.findings()[0]
    assert finding["path"] == "app/keys.py" and finding["severity"] == "HIGH" and finding["priority"] == 9
    assert confirm(root, finding["path"], finding["line"], finding["evidence"])
    assert not confirm(root, finding["path"], 1, "something_else()")
    assert fingerprint("B324", "a.py", "x  =  1") == fingerprint("B324", "a.py", "x = 1")
    defense.set_status(finding["fingerprint"], "accepted", "cache key")
    assert defense.findings() == [] and defense.status()["accepted"] == 1
    defense.set_status(finding["fingerprint"], "open")
    (root / "app" / "keys.py").write_text(
        "import hashlib\n\n\ndef key(text):\n    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()\n",
        encoding="utf-8")
    assert defense.scan()["no_longer_present"] == 1 and defense.status()["fixed"] == 1


def test_a_patch_is_tested_on_an_isolated_copy_and_never_applied(tmp_path):
    root = _repository(tmp_path)
    defense = CodeDefense(tmp_path / "cd.db", root=root)
    before = (root / "app" / "keys.py").read_text(encoding="utf-8")
    diff = ("--- a/app/keys.py\n+++ b/app/keys.py\n@@ -2,4 +2,4 @@\n \n \n def key(text):\n"
            "-    return hashlib.md5(text.encode()).hexdigest()\n"
            "+    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()\n")
    result = defense.validate_patch(diff, ["tests/test_keys.py"])
    assert result["applies"] and result["tests_passed"] is True
    assert result["findings_in_changed_files"] == {"before": 1, "after": 0} and result["findings_removed"] == 1
    assert "HAYAT does not apply patches" in result["next"]
    assert (root / "app" / "keys.py").read_text(encoding="utf-8") == before  # the real file is untouched
    breaking = diff.replace("usedforsecurity=False).hexdigest()", "usedforsecurity=False).hexdigest()[:8]")
    assert defense.validate_patch(breaking, ["tests/test_keys.py"])["verdict"] == "The tests failed with this patch."
    assert defense.validate_patch("--- a/app/keys.py\n+++ b/app/keys.py\n@@ -1,1 +1,1 @@\n-nope\n+x\n")["applies"] is False
    listed = subprocess.run(["git", "worktree", "list"], cwd=root, capture_output=True, text=True, check=True)  # noqa: S603, S607
    assert len(listed.stdout.strip().splitlines()) == 1  # the copies are gone


def test_only_authorised_folders_are_inspected_and_privacy_mode_stays_offline(tmp_path):
    root = _repository(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    runtime = SimpleNamespace(projects=SimpleNamespace(projects={}), authority=SimpleNamespace(privacy_mode=True))
    defense = CodeDefense(tmp_path / "cd.db", runtime=lambda: runtime, root=root)
    with pytest.raises(PermissionError):
        defense.scan(other)
    runtime.projects.projects["mine"] = SimpleNamespace(root=str(other))
    assert defense.authorised(other) == other.resolve()
    assert defense.dependencies() == {"checked": False, "reason": "Privacy mode is on, so the vulnerability database "
                                                                  "was not contacted."}


# ---------------------------------------------------------------------------------------------- routing

@pytest.mark.parametrize("text, tool, action", [
    ("how robust are you?", "robustness_lab", "run"),
    ("show the robustness report", "robustness_lab", "report"),
    ("scan your code for security problems", "code_defense", "scan"),
    ("check your packages for known vulnerabilities", "code_defense", "dependencies"),
    ("show me the security findings", "code_defense", "findings"),
    ("predict revenue from D:/data/sales.csv", "model_discover", "discover"),
    ("is BTC predictable?", "model_discover", "discover"),
])
def test_plain_requests_reach_the_right_laboratory(text, tool, action):
    found = lab_request(text)
    assert found is not None and found[0] == tool and found[1]["action"] == action
    assert direct_lab_plan(text, [tool])[0]["tool"] == tool


def test_ordinary_requests_are_left_alone_and_arguments_are_read():
    for text in ("how robust is this bridge design", "scan this document", "predict the weather tomorrow",
                 "is it predictable that prices rise"):
        assert lab_request(text) is None, text
    assert lab_request("predict revenue from D:/data/sales.csv")[1] == {
        "action": "discover", "source": "D:/data/sales.csv", "target": "revenue"}
    assert lab_request("is btc predictable")[1]["source"] == "market:BTC"


def test_the_installed_ghidra_is_described_from_its_own_files():
    found = ghidra_status(SimpleNamespace())
    assert set(found) >= {"installed", "folder", "tool", "limits", "next"}
    if found["installed"]:
        assert found["version"] and len(found["sha256"]) == 64 and "NationalSecurityAgency/ghidra" in found["source"]
        older = tuple(int(part) for part in found["version"].split(".")) < (12, 2)
        # An install older than the release that fixes the published advisories says so, with its source and date.
        assert ("advisory_warning" in found) == older
        if older:
            assert "analyse only files you trust" in found["advisory_warning"] and "2026-11-09" in found["advisory_warning"]
