from types import SimpleNamespace

from app.selfimprove import SelfImprovement, Improvement, SMALL


def test_repeated_review_has_stable_ids_and_clears_resolved_gaps():
    failures = [{"tool": "file_reader"}] * 6
    review = SelfImprovement(SimpleNamespace(failures=SimpleNamespace(recent=lambda n: failures)))
    first = review.review()[0]
    for _ in range(10):
        current = review.review()[0]
        assert current.proposal_id == first.proposal_id
        assert current.proposed_at == first.proposed_at
    assert len(review.pending()) == 1
    failures.clear()
    assert review.review() == [] and review.pending() == []


def test_index_repair_is_verified_and_experience_survives_restart(tmp_path):
    memory = SimpleNamespace(path=tmp_path / "memory.db", health=lambda: {"healthy": False},
                             maintain_indexes=lambda: {"maintained": True})
    runtime = SimpleNamespace(memory=memory)
    improvement = SelfImprovement(runtime)
    proposal = Improvement("Repair indexes", "missing vectors", scale=SMALL, action="maintain_memory_indexes")
    improvement._proposals[proposal.proposal_id] = proposal
    result = improvement.apply(proposal.proposal_id)
    assert result["applied"] is False
    restored = SelfImprovement(runtime)
    assert restored.diagnostics()["recent_outcomes"][-1]["result"]["applied"] is False


def test_failure_is_recorded_without_claiming_maintenance_succeeded(tmp_path):
    def fail():
        raise OSError("disk full")
    runtime = SimpleNamespace(memory=SimpleNamespace(path=tmp_path / "memory.db", maintain_indexes=fail))
    improvement = SelfImprovement(runtime)
    proposal = Improvement("Repair indexes", "missing vectors", scale=SMALL, action="maintain_memory_indexes")
    improvement._proposals[proposal.proposal_id] = proposal
    assert improvement.apply(proposal.proposal_id)["applied"] is False
    assert "disk full" in SelfImprovement(runtime)._outcomes[-1]["result"]["reason"]


def test_improvement_history_redacts_secrets(tmp_path):
    runtime = SimpleNamespace(memory=SimpleNamespace(path=tmp_path / "memory.db"))
    improvement = SelfImprovement(runtime)
    improvement._apply = lambda *a, **kw: {"applied": False, "password": "do-not-retain"}
    improvement.apply("test")
    assert "do-not-retain" not in improvement.history_path.read_text()


def test_missing_evidence_labels_get_a_verified_small_maintenance_action():
    state = {"missing": 2}
    def audit(*, repair=False):
        count = state["missing"]
        if repair:
            state["missing"] = 0
        return {"missing_evidence": count, "demoted": count if repair else 0}
    knowledge = SimpleNamespace(stats=lambda: {"unsupported_claims": state["missing"]}, audit_claim_evidence=audit)
    improvement = SelfImprovement(SimpleNamespace(research=SimpleNamespace(knowledge=knowledge)))
    proposal = next(p for p in improvement.review() if p.action == "repair_evidence_labels")
    assert proposal.scale == SMALL
    result = improvement.apply(proposal.proposal_id)
    assert result["applied"] and result["demoted"] == 2
    assert improvement.review() == []
