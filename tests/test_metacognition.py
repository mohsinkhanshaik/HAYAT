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
