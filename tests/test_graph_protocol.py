"""Independent evaluation, usable larger capacity, and bounded graph work."""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import app.graph_network as module  # noqa: E402
from tests.test_graph_network import communities  # noqa: E402


def test_training_never_sees_either_evaluation_negative_partition(monkeypatch):
    count, edges = communities(groups=3, size=20)
    original_train, original_negatives = module._train, module._negatives
    seen = {"training": False, "training_negatives": set()}

    def negatives(count, taken, number, rng):
        pairs = original_negatives(count, taken, number, rng)
        if seen["training"]:
            seen["training_negatives"].update(pairs)
        return pairs

    def train(*args, **kwargs):
        seen["reserved"] = kwargs["reserved_negatives"]
        seen["validation"] = set(kwargs["validation_negatives"])
        seen["training"] = True
        try:
            return original_train(*args, **kwargs)
        finally:
            seen["training"] = False

    monkeypatch.setattr(module, "_negatives", negatives)
    monkeypatch.setattr(module, "_train", train)
    result = module.learn_links(count, edges, epochs=10)
    assert seen["training_negatives"]
    assert not seen["reserved"] & seen["training_negatives"]
    assert not seen["reserved"] & set(edges)
    assert len(seen["reserved"] - seen["validation"]) == result["test_nonlinks"]
    assert len(seen["validation"]) == result["selection"]["negative_pairs"]


def test_changed_test_results_do_not_choose_the_suggestion_method(monkeypatch):
    count, edges = communities(groups=3, size=20)
    test_size = 2 * int(len(edges) * module.TEST_SHARE)
    original = module._scores
    audit_value = [0.0]

    def scores(truth, predictions):
        if len(truth) == test_size:
            return {"auc": audit_value[0], "average_precision": audit_value[0]}
        return original(truth, predictions)

    monkeypatch.setattr(module, "_scores", scores)
    poor = module.learn_links(count, edges, epochs=10)
    audit_value[0] = 1.0
    perfect = module.learn_links(count, edges, epochs=10)
    assert poor["network"]["auc"] == 0 and perfect["network"]["auc"] == 1
    assert poor["selection"] == perfect["selection"]
    assert poor["suggested_by"] == perfect["suggested_by"]
    assert poor["suggested_links"] == perfect["suggested_links"]


@pytest.mark.parametrize("with_features", [False, True])
def test_expanded_profile_really_trains_more_than_ten_times_the_parameters(with_features):
    count, edges = communities(groups=3, size=20)
    features = np.eye(3)[np.arange(count) // 20] if with_features else None
    baseline = module.learn_links(count, edges, features, epochs=5)
    expanded = module.learn_links(count, edges, features, epochs=5, profile="expanded")
    assert expanded["network"]["parameters"] >= baseline["network"]["parameters"] * 10
    assert expanded["network"]["epochs_run"] == 5
    assert expanded["network"]["capacity_ratio"] >= 10
    assert expanded["network"]["balanced_parameters"] == baseline["network"]["parameters"]
    assert expanded["evaluation"]["comparison_key"] != baseline["evaluation"]["comparison_key"]
    assert np.isfinite(expanded["network"]["final_loss"])


def test_training_does_not_change_the_process_torch_random_state():
    count, edges = communities(groups=3, size=20)
    before = torch.random.get_rng_state().clone()
    module.learn_links(count, edges, epochs=3)
    assert torch.equal(before, torch.random.get_rng_state())


def test_one_epoch_has_real_validation_and_changed_budget_changes_comparison():
    count, edges = communities(groups=3, size=20)
    one = module.learn_links(count, edges, epochs=1)
    two = module.learn_links(count, edges, epochs=2)
    assert 0 <= one["network"]["validation_auc"] <= 1
    assert one["evaluation"]["comparison_key"] != two["evaluation"]["comparison_key"]


def test_candidate_budget_applies_before_materialising_a_hub_neighbourhood(monkeypatch):
    monkeypatch.setattr(module, "MAX_CANDIDATES", 100)
    monkeypatch.setattr(module, "MAX_CANDIDATE_STEPS", 300)
    edges = [(0, index) for index in range(1, 10_000)]
    pairs, facts = module._candidates(10_000, edges, 4)
    assert facts["truncated"] and facts["pairs_scored"] <= 100 and facts["two_hop_steps"] <= 300
    assert not set(pairs) & set(edges)
    assert all(a != b for a, b in pairs)


def test_dense_graph_sampler_returns_the_remaining_pairs_without_rejection_loop():
    all_pairs = {(a, b) for a in range(100) for b in range(a + 1, 100)}
    missing = {(1, 3), (2, 4), (3, 6)}
    assert module._negatives(100, all_pairs - missing, 50, np.random.default_rng(0)) == sorted(missing)


@pytest.mark.parametrize("kwargs", [{"epochs": 0}, {"epochs": True}, {"seed": -1},
                                    {"suggestions": -1}, {"profile": "imaginary"}])
def test_invalid_training_configuration_is_an_explicit_error(kwargs):
    count, edges = communities(groups=3, size=20)
    with pytest.raises(ValueError):
        module.learn_links(count, edges, **kwargs)


@pytest.mark.parametrize("bad", [1.4, True, "1"])
def test_fractional_boolean_or_text_node_ids_are_not_silently_reinterpreted(bad):
    with pytest.raises(ValueError, match="integer node indices"):
        module.clean_edges(12, [(bad, 2)])


@pytest.mark.parametrize("width,value", [(0, 0), (2, float("inf")), (2, 1e100)])
def test_untrainable_feature_shapes_and_float32_overflow_are_rejected(width, value):
    count, edges = communities(groups=3, size=20)
    with pytest.raises(ValueError, match="finite numeric row"):
        module.learn_links(count, edges, np.full((count, width), value), epochs=1)
