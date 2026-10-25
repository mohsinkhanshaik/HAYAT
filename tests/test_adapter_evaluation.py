from copy import deepcopy

from scripts.evaluate_hayat_open import measured_nonregression


def results():
    baseline = {"held_out_token_loss": 1., "exact_match_passed": 3,
                "groups": {"public": {"token_loss": 1.}}, "exact_match_tasks": [{"passed": True}],
                "family_scores": {"median": {"passed": 2}, "reverse": {"passed": 1}}}
    return {"baseline": baseline, "candidate": deepcopy(baseline)}


def test_gain_in_total_score_cannot_hide_a_public_loss_regression():
    r = results()
    r["candidate"].update(held_out_token_loss=.9, exact_match_passed=4)
    r["candidate"]["groups"]["public"]["token_loss"] = 1.04
    assert not measured_nonregression(r, strict=True)


def test_gain_in_one_family_cannot_hide_another_family_or_old_answer_regression():
    r = results()
    r["candidate"]["exact_match_passed"] = 4
    r["candidate"]["family_scores"] = {"median": {"passed": 1}, "reverse": {"passed": 3}}
    assert not measured_nonregression(r, strict=True)
    r = results()
    r["candidate"]["exact_match_tasks"][0]["passed"] = False
    assert not measured_nonregression(r, strict=True)


def test_valid_gain_passes_and_nonfinite_loss_never_passes():
    r = results()
    r["candidate"]["held_out_token_loss"] = .9
    assert measured_nonregression(r, strict=True)
    r["candidate"]["held_out_token_loss"] = float("nan")
    assert not measured_nonregression(r)
