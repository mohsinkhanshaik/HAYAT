"""Evidence-based self-model and adaptive control for HAYAT.

This is operational metacognition, not a claim of subjective consciousness.
It observes measurable outcomes, decides what evidence is missing, delegates
to local models by verified performance, and queues only public-safe learning.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.intelligent_router import RouteRequest
from app.safety.sensitive import redact_text
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


@dataclass(slots=True)
class LearningPriority:
    topic: str
    reason: str
    source: str
    priority: int
    verification: str
    public_safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdaptiveControlPlane:
    """Coordinate learning, delegation, reflection, and upgrade proposals."""

    CAPABILITIES = ("conversation", "reasoning", "planning", "coding", "research", "teaching")

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.cycles = 0
        self.last_queued: list[str] = []

    @staticmethod
    def _public_safe(text: str) -> bool:
        cleaned, findings = redact_text(str(text))
        private = re.search(
            r"(?i)(?:\b[A-Z]:\\|\\\\|\b(?:localhost|127\.0\.0\.1|10\.\d|192\.168\.)|"
            r"\b(?:password|secret|api[_ -]?key|private key|customer|client)\b)", cleaned,
        )
        return not findings and not private and len(cleaned.strip()) >= 8

    def curriculum(self, limit: int = 12) -> list[dict[str, Any]]:
        """Rank concrete gaps from evidence already held by the runtime."""
        priorities: list[LearningPriority] = []
        knowledge = getattr(getattr(self.runtime, "research", None), "knowledge", None)
        if knowledge is not None:
            try:
                with knowledge.connect() as db:
                    conflicts = db.execute(
                        "SELECT a.claim_text,b.claim_text FROM claim_conflicts cc "
                        "JOIN claims a ON a.id=cc.claim_id JOIN claims b ON b.id=cc.conflicting_claim_id "
                        "WHERE cc.resolution_status='unresolved' ORDER BY cc.confidence DESC LIMIT 5"
                    ).fetchall()
                    stale = db.execute(
                        "SELECT query FROM knowledge WHERE status='active' AND valid_until IS NOT NULL "
                        "AND valid_until<=? ORDER BY confidence DESC LIMIT 5",
                        (datetime.now(UTC).isoformat(),),
                    ).fetchall()
                for left, right in conflicts:
                    topic = f"Resolve conflicting evidence: {left} Alternative claim: {right}"[:500]
                    priorities.append(LearningPriority(
                        topic, "stored claims disagree and must not be recalled as settled",
                        "unresolved_conflict", 98, "independent primary evidence resolves scope and date",
                        self._public_safe(topic),
                    ))
                for (query,) in stale:
                    topic = f"Refresh with current primary sources: {query}"[:500]
                    priorities.append(LearningPriority(
                        topic, "stored knowledge passed its validity horizon", "stale_knowledge", 88,
                        "fresh independent sources and an updated validity horizon", self._public_safe(topic),
                    ))
            except Exception:
                log_swallowed(_log, "reading stale knowledge priorities")
        failures = getattr(self.runtime, "failures", None)
        if failures is not None:
            try:
                for item in reversed(failures.recent(12)):
                    subject = str(item.get("dependency") or item.get("service") or "").strip()
                    if not subject:
                        continue
                    topic = f"{subject} {item.get('failure_class', '')} official troubleshooting and reliability"[:500]
                    priorities.append(LearningPriority(
                        topic, "a runtime dependency recently failed", "runtime_failure", 92,
                        "official documentation plus a reproducible local diagnostic", self._public_safe(topic),
                    ))
            except Exception:
                log_swallowed(_log, "reading failure priorities")
        learning = getattr(self.runtime, "learning", None)
        if learning is not None:
            try:
                for item in learning.weaknesses()[:5]:
                    topic = str(item.get("topic") or "").strip()
                    if topic:
                        priorities.append(LearningPriority(
                            topic, "measured mastery is weak or due for review", "measured_weakness", 72,
                            "retrieval practice improves the recorded mastery score", self._public_safe(topic),
                        ))
            except Exception:
                log_swallowed(_log, "reading weak-mastery priorities")
        unique: dict[str, LearningPriority] = {}
        for item in priorities:
            key = " ".join(item.topic.casefold().split())
            if key not in unique or item.priority > unique[key].priority:
                unique[key] = item
        ranked = sorted(unique.values(), key=lambda item: (-item.priority, item.topic))
        return [item.to_dict() for item in ranked[:max(1, min(int(limit), 50))]]

    def queue_curriculum(self, limit: int = 3) -> dict[str, Any]:
        learner = getattr(self.runtime, "learner", None)
        if learner is None:
            return {"queued": 0, "topics": [], "reason": "continuous learner unavailable"}
        queued: list[str] = []
        skipped_private = 0
        for item in self.curriculum(limit=max(3, int(limit) * 3)):
            if not item["public_safe"]:
                skipped_private += 1
                continue
            if learner.enqueue(item["topic"], source=f"adaptive_{item['source']}", priority=item["priority"]):
                queued.append(item["topic"])
            if len(queued) >= max(1, min(int(limit), 10)):
                break
        self.cycles += 1
        self.last_queued = queued
        return {
            "queued": len(queued), "topics": queued, "skipped_private": skipped_private,
            "authority": "BACKGROUND_LEARNER", "internet_learning": True,
            "verification_required": True,
        }

    def delegation_map(self) -> dict[str, Any]:
        router = getattr(self.runtime, "intelligent_router", None)
        if router is None:
            return {"available": False, "routes": []}
        routes = []
        for capability in self.CAPABILITIES:
            try:
                decision = router.route(RouteRequest(
                    capability, "complex" if capability in {"reasoning", "coding", "research"} else "simple",
                    "text", ("AUTO",), "local", 8000, "text",
                ))
                routes.append({
                    "capability": capability, "primary": decision.primary,
                    "fallbacks": decision.fallbacks, "reason": decision.reason,
                    "evidence": decision.scores[:3],
                })
            except Exception as exc:
                routes.append({"capability": capability, "primary": None, "error": type(exc).__name__})
        return {"available": True, "routes": routes, "selection": "verified_outcomes_only"}

    def review_upgrades(self, *, apply_small: bool = True) -> dict[str, Any]:
        improvement = getattr(self.runtime, "improvement", None)
        if improvement is None:
            return {"proposals": [], "applied": [], "needs_owner_review": []}
        proposals = improvement.review()
        applied: list[dict[str, Any]] = []
        review: list[dict[str, Any]] = []
        for proposal in proposals:
            if proposal.needs_permission:
                review.append(proposal.to_dict())
            elif apply_small:
                applied.append({"proposal": proposal.to_dict(), "result": improvement.apply(proposal.proposal_id)})
        return {
            "proposals": [item.to_dict() for item in proposals], "applied": applied,
            "needs_owner_review": review,
            "policy": "small reversible maintenance may apply; behavior, code, weights, reach, and permissions remain reviewed and regression-gated",
        }

    def sensory_state(self) -> dict[str, Any]:
        """Fuse confidence-bearing sensor status without inventing perception."""
        camera = getattr(self.runtime, "camera", None)
        voice = getattr(self.runtime, "voice", None)
        displays = getattr(self.runtime, "displays", None)
        try:
            camera_status = camera.status() if camera is not None else {}
        except Exception:
            camera_status = {}
        try:
            voice_status = voice.status() if voice is not None else {}
        except Exception:
            voice_status = {}
        try:
            display_status = displays.status() if displays is not None else {}
        except Exception:
            display_status = {}
        observation = camera_status.get("last_observation") or {}
        capture = voice_status.get("last_capture") or {}
        return {
            "camera": {
                "active": bool(camera_status.get("active")),
                "frames_captured": int(observation.get("frames_captured", 0) or 0),
                "frames_analyzed": int(observation.get("frames_analyzed", 0) or 0),
                "method": observation.get("analysis_method") or "multi_frame_ranked_consensus",
                "ocr_frames_compared": int((observation.get("ocr") or {}).get("frames_compared", 0) or 0),
            },
            "voice": {
                "state": voice_status.get("state", "off"), "mode": voice_status.get("mode", "off"),
                "stt": voice_status.get("stt_provider"), "tts": voice_status.get("tts_provider"),
                "capture": capture,
            },
            "screen": {"displays": int(display_status.get("count", 0) or 0)},
            "wifi_sensing": self._wifi_sensing(),
            "grounding_rule": "multiple observations outrank single-frame inference; current sensor evidence outranks memory",
            "uncertainty": "missing or stale measurements remain unknown rather than becoming guessed facts",
        }

    def _wifi_sensing(self) -> dict[str, Any]:
        monitor = getattr(getattr(self.runtime, "registry", None), "sensing", None)
        if monitor is None:
            return {"available": False}
        try:
            report = monitor.status_report()
        except Exception:
            return {"available": False}
        return {"available": True, "connected": report["connected"], "receiving": report["receiving"],
                "real_csi": report["real_csi"], "level": report["level"], "summary": report["summary"]}

    def adapt(self, limit: int = 3) -> dict[str, Any]:
        return {"learning": self.queue_curriculum(limit), "upgrades": self.review_upgrades(apply_small=True),
                "delegation": self.delegation_map()}

    def status(self) -> dict[str, Any]:
        curriculum = self.curriculum(8)
        learner = getattr(self.runtime, "learner", None)
        improvement = getattr(self.runtime, "improvement", None)
        return {
            "operational_metacognition": True,
            "subjective_consciousness_claimed": False,
            "self_model": "measured runtime state, outcomes, gaps, and uncertainty",
            "control_loop": "observe → prioritize → delegate → verify → retain → review",
            "curriculum_candidates": len(curriculum), "top_priorities": curriculum[:5],
            "continuous_learning_running": bool(getattr(learner, "running", False)),
            "safe_upgrade_proposals": len(getattr(improvement, "_proposals", {}) or {}),
            "delegation": "local models selected by verified quality, latency, resources, and failure history",
            "senses": self.sensory_state(),
            "cycles": self.cycles, "last_queued": list(self.last_queued),
            "weight_changes": "candidate preparation allowed; promotion remains explicit and regression-gated",
        }
