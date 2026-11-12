"""The fuzzer finds planted defects, shrinks them, survives a hang, and confirms a fix by replaying the input."""
from __future__ import annotations

import json
import random

import pytest

import app.fuzz_lab as module
from app.fuzz_lab import LIMITS, TARGETS, FuzzLab, Target, fuzz, minimise, mutate, register_fuzz_tools, run_one

SEEDS = ("show my inbox", "system: hello there", "plain words")
PLANTED = {
    "crash": Target("crash", "tests.fuzz_fixture:planted_crash", "planted", SEEDS, allowed=("ValueError",)),
    "property": Target("property", "tests.fuzz_fixture:planted_property", "planted", SEEDS,
                       check="tests.fuzz_fixture:no_zero_width_left"),
    "slow": Target("slow", "tests.fuzz_fixture:planted_slow", "planted", SEEDS),
    "hang": Target("hang", "tests.fuzz_fixture:planted_hang", "planted", SEEDS),
    "sound": Target("sound", "tests.fuzz_fixture:sound", "planted", SEEDS),
}


def test_a_contract_breaking_crash_is_found_and_shrunk_to_what_matters():
    result = fuzz(PLANTED["crash"], seconds=4, seed=1)
    finding, = result["findings"]
    assert finding["kind"] == "crash" and finding["signature"].startswith("crash:KeyError:tests/fuzz_fixture.py:")
    assert finding["where"][-1].endswith("in planted_crash") and "KeyError" in finding["message"]
    # Shrunk to the two things the defect needs, in either order, and nothing else.
    assert sorted(finding["input"].replace("system:", "S")) == ["\t", "S"] and finding["original_length"] >= 8
    assert result["coverage_guided"] and result["lines_reached"] >= 4 and result["executions"] > 200
    assert result["corpus"] > len(SEEDS)                       # inputs that reached new lines were kept
    json.dumps(result)


def test_a_declared_exception_is_not_a_finding():
    from tests.fuzz_fixture import planted_crash

    assert run_one(PLANTED["crash"], planted_crash, None, "") is None           # ValueError is in the contract
    undeclared = Target("crash", "tests.fuzz_fixture:planted_crash", "planted", SEEDS)
    assert run_one(undeclared, planted_crash, None, "")["signature"].startswith("crash:ValueError")


def test_a_broken_property_is_found_with_a_two_character_witness():
    result = fuzz(PLANTED["property"], seconds=3, seed=2)
    finding, = result["findings"]
    assert finding["kind"] == "property" and finding["message"] == "zero-width space left in the result"
    assert finding["input"] == "​​"                                    # the smallest input that shows it


def test_sound_code_yields_no_finding_and_the_result_says_what_that_means(tmp_path):
    lab = FuzzLab(tmp_path / "fuzz.db", {"sound": PLANTED["sound"]})
    outcome = lab.run("sound", seconds=1)
    assert outcome["findings"] == 0 and outcome["executions"] > 100
    assert outcome["verdict"].startswith("No finding in 1 target") and "does not show there are none" in outcome["verdict"]
    assert outcome["limits"] == LIMITS and lab.findings() == [] and lab.status()["open_findings"] == 0


def test_findings_are_kept_once_across_runs_and_a_fix_is_confirmed_by_replay(tmp_path, monkeypatch):
    lab = FuzzLab(tmp_path / "fuzz.db", {"crash": PLANTED["crash"]})
    events = []
    lab.events = type("Bus", (), {"publish": lambda self, *event: events.append(event)})()
    first = lab.run("crash", seconds=4, seed=1)
    assert first["findings"] == 1 and first["results"][0]["new_findings"] == 1
    assert events == [("LAB_RUN_COMPLETED", "fuzz_lab", {"targets": 1, "findings": 1})]
    shown = first["results"][0]["findings"][0]
    assert "input" not in shown and len(shown["input_sha256"]) == 64 and shown["input_length"] == 8
    again = lab.run("crash", seconds=4, seed=1)
    assert again["results"][0]["new_findings"] == 0                              # the same defect, not a second finding
    kept, = lab.findings()
    assert kept["times"] == 2 and kept["kind"] == "crash" and kept["status"] == "open"
    assert lab.recheck(kept["id"])["status"] == "open"                          # nothing was fixed yet
    # "Fix" the handler (the child process imports this module, so the fix is a file the child will import).
    fixed = tmp_path / "fixed_fixture.py"
    fixed.write_text("def planted_crash(text):\n    return len(text)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    lab.targets["crash"] = Target("crash", "fixed_fixture:planted_crash", "planted", SEEDS, allowed=("ValueError",))
    checked = lab.recheck(kept["id"])
    assert checked["status"] == "fixed" and "now passes" in checked["verdict"] and "fuzz again" in checked["verdict"]
    assert lab.findings() == [] and lab.findings("all")[0]["status"] == "fixed" and lab.status()["fixed"] == 1
    with pytest.raises(ValueError, match="No such fuzzing finding"):
        lab.recheck(99)


def test_a_slow_input_is_reported_as_a_slow_finding():
    result = fuzz(PLANTED["slow"], seconds=4, seed=3)
    finding, = result["findings"]
    assert finding["kind"] == "slow" and "((((" in finding["input"] and result["slowest_seconds"] >= 0.4


def test_a_hang_ends_the_worker_not_hayat_and_keeps_the_input_it_was_on(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "GRACE_SECONDS", 2.0)
    lab = FuzzLab(tmp_path / "fuzz.db", {"hang": PLANTED["hang"]})
    outcome = lab.run("hang", seconds=3, seed=4)
    result, = outcome["results"]
    finding, = result["findings"]
    assert result["ended"] == "hang" and finding["kind"] == "hang" and "stopped answering" in finding["message"]
    kept, = lab.findings()
    assert "\\x00" in kept["input_shown"]                                        # the NUL that causes it is in the kept input
    assert lab.recheck(kept["id"])["status"] == "open"


def test_only_declared_targets_and_sane_budgets_are_accepted(tmp_path):
    lab = FuzzLab(tmp_path / "fuzz.db", {"sound": PLANTED["sound"]})
    with pytest.raises(ValueError, match="only on declared targets: sound"):
        lab.run("os:system", seconds=1)
    for seconds in (0, 0.1, 61, True, "5"):
        with pytest.raises(ValueError, match="between 0.2 and 60"):
            lab.run("sound", seconds=seconds)
    assert lab.status() == {"runs": 0, "open_findings": 0, "last": "never fuzzed"}


def test_mutation_and_shrinking_are_bounded_and_repeatable():
    rng = random.Random(7)
    produced = {mutate(rng, "show my inbox", ["search notion for roadmap"]) for _ in range(300)}
    assert len(produced) > 200 and all(len(text) <= module.MAX_LENGTH for text in produced)
    again = random.Random(7)
    assert mutate(again, "show my inbox", ["search notion for roadmap"]) in produced
    small =minimise("xxAxxxxBxx" * 20, lambda text: "A" in text and "B" in text)
    assert "A" in small and "B" in small and len(small) <= 8
    assert minimise("abc", lambda text: False) == "abc"                          # nothing smaller fails: unchanged


def test_hayat_s_own_handlers_are_declared_importable_and_fuzzable(tmp_path):
    assert set(TARGETS) == {"injection.detect", "injection.clean", "accounts.request", "lab.request"}
    for target in TARGETS.values():
        assert callable(module._load(target.function)) and target.seeds and target.what
        assert target.function.startswith("app.") and (not target.check or callable(module._load(target.check)))
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_fuzz_tools(registry, FuzzLab(tmp_path / "fuzz.db"))
    listed = registry.execute("fuzz_lab", {"action": "targets"}).output["targets"]
    assert [row["name"] for row in listed] == list(TARGETS)
    # A real, short run on a real handler: the harness works end to end. Whatever it finds is recorded, not asserted.
    done = registry.execute("fuzz_lab", {"action": "run", "target": "lab.request", "seconds": 1.5})
    assert done.success, done.error
    result, = done.output["results"]
    assert result["executions"] > 50 and result["coverage_guided"] and result["lines_reached"] > 5
    assert done.output["findings"] == len(result["findings"])
    assert registry.execute("fuzz_lab", {}).output["status"]["runs"] == 1
    refused = registry.execute("fuzz_lab", {"action": "run", "target": "os:system"})
    assert not refused.success
