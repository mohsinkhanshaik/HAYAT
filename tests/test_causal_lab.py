"""Cause and effect: a known effect is recovered through confounding, and weak evidence is called weak."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from app.causal_lab import LIMITS, CausalLab, e_value, estimate, register_causal_tools


def confounded(rows: int = 1500, effect: float = 2.0, seed: int = 1) -> pd.DataFrame:
    """Sicker people get the treatment more often and do worse anyway: the plain comparison is badly wrong."""
    rng = np.random.default_rng(seed)
    severity = rng.normal(size=rows)
    age = rng.normal(size=rows)
    treated = (rng.random(rows) < 1 / (1 + np.exp(-1.5 * severity))).astype(int)
    outcome = effect * treated - 3.0 * severity + 0.5 * age + rng.normal(scale=1.0, size=rows)
    return pd.DataFrame({"treated": treated, "severity": severity, "age": age, "recovery": outcome})


def test_a_known_effect_is_recovered_where_the_plain_comparison_gets_the_sign_wrong():
    result = estimate(confounded(), "treated", "recovery")
    low, high = result["interval_95"]
    assert low <= 2.0 <= high and high - low < 1.0              # the true effect, tightly
    assert result["naive"]["effect"] < 0                         # the plain comparison says the treatment harms
    assert not result["naive"]["interval_95"][0] <= 2.0 <= result["naive"]["interval_95"][1]
    assert abs(result["regression_adjusted"] - 2.0) < 0.3
    assert result["verdict_kind"] == "effect" and not result["problems"]
    assert "plain comparison shows" in result["verdict"] and result["limits"] == LIMITS
    assert result["method"].startswith("augmented inverse-propensity")
    assert result["adjusted_for"] == ["severity", "age"] and result["rows"] == 1500
    checks = result["refutation"]
    assert all(check["passed"] for check in checks.values())
    assert abs(checks["placebo_treatment"]["average_effect"]) < 0.3 and checks["placebo_treatment"]["share_as_large_as_estimate"] == 0
    # Weighting makes the two groups comparable on the column that drove the treatment.
    severity = next(row for row in result["balance"] if row["column"] == "severity")
    assert abs(severity["difference_before"]) > 0.8 and abs(severity["difference_after_weighting"]) < 0.2
    assert result["e_value"]["estimate"] > result["e_value"]["interval_bound"] > 1
    json.dumps(result)                                          # a saved record holds no arrays


def test_leaving_out_the_common_cause_gives_the_wrong_answer_and_the_result_cannot_know_it():
    """The honest limit: with the confounder unmeasured every check passes and the number is still wrong."""
    result = estimate(confounded().drop(columns=["severity"]), "treated", "recovery")
    assert not result["interval_95"][0] <= 2.0 <= result["interval_95"][1]
    assert "only if the adjusted columns include every common cause" in result["limits"]
    assert "only under the assumption stated in the limits" in result["verdict"] or result["verdict_kind"] != "effect"


def test_no_effect_is_reported_as_none_detected_not_as_a_small_effect():
    result = estimate(confounded(effect=0.0, seed=2), "treated", "recovery")
    assert result["verdict_kind"] == "no_effect_detected" and result["interval_95"][0] <= 0 <= result["interval_95"][1]
    assert result["verdict"].startswith("No effect of treated on recovery can be told apart from zero")
    assert result["e_value"]["interval_bound"] == 1.0
    assert result["refutation"]["placebo_treatment"]["passed"]  # placebos do not refute "nothing there"


def test_a_numeric_treatment_gives_the_change_per_unit():
    rng = np.random.default_rng(3)
    rows = 1200
    income = rng.normal(size=rows)
    dose = 0.8 * income + rng.normal(size=rows)
    spend = 1.5 * dose + 2.0 * income + rng.normal(size=rows)
    result = estimate(pd.DataFrame({"dose": dose, "income": income, "spend": spend}), "dose", "spend")
    assert result["treatment_kind"] == "continuous" and result["method"].startswith("partially linear")
    assert result["interval_95"][0] <= 1.5 <= result["interval_95"][1]
    assert result["naive"]["effect"] > 2.0                       # the plain slope absorbs income's effect
    assert "per one unit of dose" in result["unit"] and "e_value" not in result and "balance" not in result
    assert 0.3 < result["treatment_unexplained_share"] < 0.9


def test_groups_too_different_to_compare_are_called_unreliable():
    rng = np.random.default_rng(4)
    rows = 800
    score = rng.normal(size=rows)
    treated = (score > 0.0).astype(int)                          # treatment decided entirely by the score
    outcome = 1.0 * treated + 2.0 * score + rng.normal(scale=0.5, size=rows)
    result = estimate(pd.DataFrame({"treated": treated, "score": score, "outcome": outcome}), "treated", "outcome")
    assert result["verdict_kind"] == "unreliable" and result["verdict"].startswith("Not reliable enough")
    assert result["overlap"]["share_with_extreme_propensity"] > 0.2
    assert any("no comparable counterpart" in problem for problem in result["problems"])
    assert "restrict to rows where the two groups overlap" in result["next"]


def test_words_categories_gaps_and_a_yes_no_outcome_are_handled_and_reported():
    rng = np.random.default_rng(5)
    rows = 900
    region = rng.choice(["north", "south", "east"], rows)
    push = rng.normal(size=rows) + (region == "north")
    plan = np.where(rng.random(rows) < 1 / (1 + np.exp(-push)), "premium", "basic")
    stayed = rng.random(rows) < 0.35 + 0.25 * (plan == "premium") + 0.1 * (region == "north")
    table = pd.DataFrame({"plan": plan, "region": region, "push": push, "stayed": np.where(stayed, "yes", "no"),
                          "id": [f"u{index}" for index in range(rows)], "constant": 1})
    table.loc[:9, "push"] = np.nan
    table.loc[10:14, "stayed"] = None
    result = estimate(table, "plan", "stayed")
    assert result["treatment_levels"] == {"treated": "premium", "untreated": "basic"}
    assert result["rows"] == rows - 5 and any("5 rows without" in note for note in result["notes"])
    assert any("share of rows where stayed is 'yes'" in note for note in result["notes"])
    assert any("Every other column was adjusted for" in note for note in result["notes"])
    assert {"region=south", "push"} <= set(result["adjusted_for"])
    assert any(item.startswith("id (") and "too many" in item for item in result["left_out_columns"])
    assert "constant (constant or empty)" in result["left_out_columns"]
    assert result["interval_95"][0] <= 0.25 <= result["interval_95"][1]


@pytest.mark.parametrize("change,message", [
    (lambda t: t.assign(treated=1), "only one value"),
    (lambda t: t.assign(recovery=3.0), "same value in every row"),
    (lambda t: t.head(40), "at least 60"),
    (lambda t: t.assign(treated=["a", "b", "c"] * 500), "different text values"),
    (lambda t: t.assign(treated=[1] * 10 + [0] * 1490), "Fewer than 20 rows"),
])
def test_tables_that_cannot_answer_the_question_are_refused_with_the_reason(change, message):
    with pytest.raises(ValueError, match=message):
        estimate(change(confounded()), "treated", "recovery")


def test_column_mistakes_are_named():
    table = confounded()
    with pytest.raises(ValueError, match="no column named 'dose'"):
        estimate(table, "dose", "recovery")
    with pytest.raises(ValueError, match="must be different"):
        estimate(table, "treated", "treated")
    with pytest.raises(ValueError, match="No such column to adjust for: weight"):
        estimate(table, "treated", "recovery", ["weight"])
    with pytest.raises(ValueError, match="cannot also be adjusted for"):
        estimate(table, "treated", "recovery", ["recovery"])
    with pytest.raises(ValueError, match="Name the treatment"):
        estimate(table, "", "recovery")
    nothing = estimate(table, "treated", "recovery", [], refute=False)
    assert nothing["adjusted_for"] == [] and any("Nothing was adjusted for" in note for note in nothing["notes"])
    # With nothing adjusted the estimate is the plain comparison (up to the fold-by-fold averaging).
    assert nothing["effect"] == pytest.approx(nothing["naive"]["effect"], abs=0.01) and "refutation" not in nothing


def test_the_e_value_matches_its_published_formula():
    assert e_value(0.0, 1.0) == 1.0
    ratio = float(np.exp(0.91 * 0.5))
    assert e_value(0.5, 1.0) == pytest.approx(ratio + np.sqrt(ratio * (ratio - 1)), abs=1e-3)
    assert e_value(-0.5, 1.0) == e_value(0.5, 1.0) and e_value(1.0, 0.0) is None and e_value(float("nan"), 1.0) is None


def test_runs_are_saved_reopened_and_compared_only_on_the_same_table(tmp_path):
    path = tmp_path / "trial.csv"
    confounded(rows=600).to_csv(path, index=False)
    events = []
    lab = CausalLab(tmp_path / "causal.db")
    lab.events = type("Bus", (), {"publish": lambda self, kind, source, payload: events.append((kind, source, payload))})()
    assert lab.recent() == [] and lab.detail(1) is None and lab.status() == {"estimates": 0, "last": "none yet"}
    first = lab.run(str(path), "treated", "recovery")
    assert first["estimate_id"] == 1 and first["comparison"] == {"comparable": False, "reason": "No earlier run for this input."}
    assert events == [("LAB_RUN_COMPLETED", "causal_lab", {"estimate_id": 1, "verdict_kind": first["verdict_kind"]})]
    second = lab.run(str(path), "treated", "recovery")
    assert second["comparison"]["comparable"] and second["comparison"]["change"] == "unchanged"   # seeded
    assert second["comparison"]["direction"] == "neither" and second["comparison"]["previous_estimate_id"] == 1
    confounded(rows=600, seed=9).to_csv(path, index=False)
    assert not lab.run(str(path), "treated", "recovery")["comparison"]["comparable"]
    path.unlink()                                               # reopening reads the record, not the table
    assert lab.detail(2)["interval_95"] == second["interval_95"] and lab.detail(99) is None
    assert [row["id"] for row in lab.recent()] == [3, 2, 1]
    assert lab.status()["estimates"] == 3 and "treated → recovery" in lab.status()["last"]
    record = first["evaluation"]
    assert record["protocol"] == "hayat-causal-estimate-v1" and len(record["dataset_sha256"]) == 64
    assert str(tmp_path) not in json.dumps(record)


def test_the_tool_and_the_plain_request_reach_the_estimate(tmp_path):
    from app.research_lab import lab_request
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    path = tmp_path / "trial.csv"
    confounded(rows=600).to_csv(path, index=False)
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_causal_tools(registry, CausalLab(tmp_path / "causal.db"))
    done = registry.execute("causal_analyze", {"source": str(path), "treatment": "treated", "outcome": "recovery",
                                               "confounders": ["severity", "age"]})
    assert done.success, done.error
    assert done.output["verdict_kind"] == "effect" and done.output["adjusted_for"] == ["severity", "age"]
    assert registry.execute("causal_analyze", {"action": "recent"}).output["estimates"][0]["id"] == 1
    assert registry.execute("causal_analyze", {"action": "detail", "estimate_id": 1}).output["estimate"]["rows"] == 600
    refused = registry.execute("causal_analyze", {"source": str(path), "treatment": "nope", "outcome": "recovery"})
    assert not refused.success and "no column named" in refused.error
    assert registry.get("causal_analyze").permission == "filesystem_read"
    for said in (f"does treated cause recovery in {path}", f"what is the effect of treated on recovery in {path}",
                 f"Estimate the causal effect of treated on recovery using {path}"):
        assert lab_request(said) == ("causal_analyze", {"action": "estimate", "source": str(path),
                                                        "treatment": "treated", "outcome": "recovery"}), said
    assert lab_request("does coffee cause cancer") is None      # no table: a question for research, not this lab
    assert lab_request(f"predict recovery from {path}")[0] == "model_discover"


def test_an_explicit_empty_adjustment_survives_the_persisted_lab_api(tmp_path):
    table = confounded(rows=300)
    lab = CausalLab(tmp_path / "causal.db", tables=lambda _source: table)
    result = lab.run("in-memory-fixture", "treated", "recovery", [])
    assert result["adjusted_for"] == []
    assert result["effect"] == result["naive"]["effect"]
    assert lab.detail(result["estimate_id"])["adjusted_for"] == []


def test_a_binary_numeric_treatment_cannot_encode_infinity_as_a_valid_group():
    table = confounded(rows=100)
    table["treated"] = table["treated"].replace(1, np.inf)
    with pytest.raises(ValueError, match="not finite"):
        estimate(table, "treated", "recovery", refute=False)


def test_no_adjustment_numeric_estimate_is_the_plain_slope_independent_of_folds():
    rng = np.random.default_rng(22)
    dose = rng.normal(size=100)
    table = pd.DataFrame({"dose": dose, "outcome": 1.7 * dose + rng.normal(size=100)})
    first = estimate(table, "dose", "outcome", [], seed=1, refute=False)
    second = estimate(table, "dose", "outcome", [], seed=29, refute=False)
    assert first["effect"] == first["naive"]["effect"] == second["effect"]
    assert any("linear, additive" in note for note in first["notes"])
    assert any("not run" in note for note in first["notes"])
    assert not first["evaluation"]["refutation_enabled"]


def test_perfect_group_separation_is_unreliable_even_when_regularized_propensities_are_moderate():
    rng = np.random.default_rng(19)
    treated = np.repeat([0, 1], 40)
    table = pd.DataFrame({"treated": treated, "assignment_rule": treated,
                          "outcome": treated + rng.normal(scale=.05, size=80)})
    result = estimate(table, "treated", "outcome", ["assignment_rule"], refute=False)
    assert result["verdict_kind"] == "unreliable"
    assert any("overlap is absent" in problem for problem in result["problems"])


def test_unadjustable_nonconstant_columns_cannot_silently_produce_a_reliable_effect():
    table = confounded(rows=100)
    table["district"] = [f"district-{index}" for index in range(100)]
    result = estimate(table, "treated", "recovery", ["severity", "age", "district"], refute=False)
    assert result["verdict_kind"] == "unreliable"
    assert any("could not adjust" in problem and "district" in problem for problem in result["problems"])


def test_binary_outcomes_do_not_receive_a_continuous_outcome_evalue_conversion():
    rng = np.random.default_rng(21)
    treated = np.repeat([0, 1], 100)
    outcome = (rng.random(200) < (.2 + .5 * treated)).astype(int)
    result = estimate(pd.DataFrame({"treated": treated, "outcome": outcome}), "treated", "outcome", [], refute=False)
    assert result["e_value"]["estimate"] is None and result["e_value"]["interval_bound"] is None
    assert "continuous-outcome conversion does not apply" in result["e_value"]["meaning"]
    assert e_value(1000.0, 1.0) is None
    large_finite = e_value(500.0, 1.0)
    assert large_finite is not None and math.isfinite(large_finite)


def test_duplicate_column_names_and_overflowing_numeric_ranges_are_refused():
    table = confounded(rows=100)
    duplicate = pd.concat([table, table[["treated"]]], axis=1)
    with pytest.raises(ValueError, match="unique"):
        estimate(duplicate, "treated", "recovery", refute=False)
    table["recovery"] *= 1e200
    with pytest.raises(ValueError, match="rescale"):
        estimate(table, "treated", "recovery", refute=False)


def test_placebo_randomization_reports_finite_sample_uncertainty():
    result = estimate(confounded(rows=300), "treated", "recovery")
    placebo = result["refutation"]["placebo_treatment"]
    assert placebo["monte_carlo_pvalue"] >= round(1 / 21, 4)
    assert result["evaluation"]["refutation_enabled"]
