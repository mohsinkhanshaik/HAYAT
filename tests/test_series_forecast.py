import math

import pytest

from app.series_forecast import forecast_series


def test_linear_trend_is_selected_using_earlier_origins_and_checked_on_later_ones():
    result = forecast_series([float(3 * i + 7) for i in range(100)], horizon=4)
    for prediction in result["predictions"]:
        assert prediction["method"] == "local_linear_trend"
        assert prediction["prediction"] == pytest.approx(3 * (99 + prediction["step"]) + 7)
        validation = prediction["validation"]
        assert validation["evaluation_mae"] == pytest.approx(0)
        assert validation["beats_baseline"]
        assert validation["selection_cases"] >= 4 and validation["evaluation_cases"] >= 4
        for trial in validation["trials"]:
            assert trial["trained_through_index"] < trial["target_index"]
            assert trial["prediction"] == pytest.approx(trial["observed"])


def test_constant_series_prefers_simple_baseline_and_zero_error():
    result = forecast_series([5.] * 60, horizon=3)
    assert all(p["method"] == "last_value" for p in result["predictions"])
    assert all(p["empirical_error_band"] == [5, 5] for p in result["predictions"])
    assert "not guaranteed" in result["uncertainty"]


def test_changing_a_future_outcome_does_not_change_its_prediction_or_method():
    values = [float(i + math.sin(i)) for i in range(100)]
    original = forecast_series(values)["predictions"][0]
    target = original["validation"]["trials"][4]["target_index"]
    values[target] += 1000
    changed = forecast_series(values)["predictions"][0]
    assert changed["method"] == original["method"]
    assert changed["validation"]["selection_mae"] == original["validation"]["selection_mae"]
    assert changed["validation"]["trials"][4]["prediction"] == original["validation"]["trials"][4]["prediction"]
    assert changed["validation"]["evaluation_mae"] > original["validation"]["evaluation_mae"]


@pytest.mark.parametrize("values,horizon", [([1.] * 23, 1), ([1.] * 24, 12), ([1.] * 2100, 1),
                                         ([1.] * 24, True), ([1.] * 24, 0), ([1.] * 24, 13),
                                         ([float("nan")] * 24, 1), ([True] * 24, 1), ([1e101] * 24, 1)])
def test_forecast_rejects_insufficient_invalid_or_unbounded_inputs(values, horizon):
    with pytest.raises(ValueError):
        forecast_series(values, horizon)
