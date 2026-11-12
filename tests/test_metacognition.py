from __future__ import annotations

from types import SimpleNamespace

from app.metacognition import AdaptiveControlPlane


class Learner:
    running = True

    def __init__(self): self.items = []
    def enqueue(self, topic, *, source, priority):
        self.items.append((topic, source, priority)); return True


def test_control_plane_builds_and_queues_only_public_safe_gaps():
    learner = Learner()
    runtime = SimpleNamespace(
        research=SimpleNamespace(knowledge=None),
        failures=SimpleNamespace(recent=lambda _limit: [
            {"dependency": "Playwright", "failure_class": "timeout"},
            {"dependency": "API key secret", "failure_class": "auth"},
        ]),
        learning=SimpleNamespace(weaknesses=lambda: [{"topic": "Bayesian calibration"}]),
        learner=learner, improvement=SimpleNamespace(_proposals={}), intelligent_router=None,
    )
    control = AdaptiveControlPlane(runtime)
    curriculum = control.curriculum()
    assert any(item["source"] == "runtime_failure" for item in curriculum)
    assert any(item["source"] == "measured_weakness" for item in curriculum)
    result = control.queue_curriculum(5)
    assert result["queued"] == 2 and result["skipped_private"] == 1
    assert all("secret" not in topic.casefold() for topic, _source, _priority in learner.items)
    status = control.status()
    assert status["operational_metacognition"] is True
    assert status["subjective_consciousness_claimed"] is False


def test_delegation_map_reports_verified_router_choices():
    class Router:
        def route(self, request):
            return SimpleNamespace(primary=f"local-{request.capability}", fallbacks=["fallback"],
                                   reason="verified evidence", scores=[{"quality": .9, "evidence_count": 4}])
    runtime = SimpleNamespace(intelligent_router=Router(), research=SimpleNamespace(knowledge=None),
                              failures=None, learning=None, learner=None, improvement=None)
    report = AdaptiveControlPlane(runtime).delegation_map()
    assert report["available"] is True
    assert len(report["routes"]) == len(AdaptiveControlPlane.CAPABILITIES)
    assert report["routes"][0]["primary"].startswith("local-")


def test_privacy_mode_does_not_enqueue_public_learning():
    learner = Learner()
    control = AdaptiveControlPlane(SimpleNamespace(authority=SimpleNamespace(privacy_mode=True), learner=learner))
    assert control.queue_curriculum()["queued"] == 0
    assert learner.items == []


def test_broken_measurements_are_visible_instead_of_healthy_empty_state():
    def broken():
        raise OSError("private local path must not be exposed")
    control = AdaptiveControlPlane(SimpleNamespace(
        research=SimpleNamespace(knowledge=SimpleNamespace(connect=broken, stats=broken)),
        learning=SimpleNamespace(weaknesses=broken), camera=SimpleNamespace(status=broken)))
    report = control.status()
    assert report["knowledge_health"]["available"] is False
    assert report["observation_errors"] == {"knowledge": "OSError", "learning": "OSError", "knowledge_health": "OSError"}
    assert report["senses"]["camera"]["available"] is False
    assert report["senses"]["camera"]["method"] == "not_observed"
    assert "private local path" not in str(report)
