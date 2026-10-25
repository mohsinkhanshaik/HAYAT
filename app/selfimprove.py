"""HAYAT deciding for itself that something should be better.

The repair specialist can already fix what is broken, but only when asked.
Nothing lets HAYAT look at its own record and conclude that something is worth
improving -- so a tool that fails a third of the time keeps failing, because
nobody counted.

This counts. It reads evidence the system already produces (failure records,
repeated manual work, a provider that keeps restarting, procedures that could
be learned) and turns it into proposals with the reason attached. It is
explicitly not a loop that changes things: proposals are surfaced, and the
size of the change decides who may act on it.

Small and reversible -- writing down a procedure, adjusting a retry -- HAYAT
may do and report. Anything that changes how it behaves, what it may reach, or
what Mohsin will see waits for him. That line is not friction for its own
sake: an assistant that improves itself unattended is one whose behaviour
drifts away from what its owner last agreed to, and the whole point of the
authority kernel is that this never happens quietly.
"""
from __future__ import annotations

import logging
import hashlib
import json
from pathlib import Path
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class Scale(str):
    """How large a change is, which decides who may approve it."""


SMALL = "small"      # reversible, invisible, HAYAT may do it and report
LARGE = "large"      # changes behaviour, reach, or what Mohsin sees -- ask first


@dataclass
class Improvement:
    """Something HAYAT thinks would make it better, and why."""

    title: str
    reason: str
    scale: str = LARGE
    kind: str = "general"
    evidence: dict[str, Any] = field(default_factory=dict)
    proposal_id: str = field(default_factory=lambda: f"imp-{uuid.uuid4().hex[:8]}")
    proposed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    action: str = ""

    @property
    def needs_permission(self) -> bool:
        return self.scale != SMALL

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id, "title": self.title, "reason": self.reason,
            "scale": self.scale, "kind": self.kind, "evidence": self.evidence,
            "action": self.action, "needs_permission": self.needs_permission,
            "proposed_at": self.proposed_at,
        }

    def describe(self) -> str:
        gate = "needs your OK" if self.needs_permission else "I can just do this"
        return f"{self.proposal_id} [{gate}] {self.title}\n    because {self.reason}"


#: A tool failing this often is a real problem rather than bad luck.
FAILURE_RATE_THRESHOLD = 0.34
MINIMUM_ATTEMPTS = 4


class SelfImprovement:
    """Reads HAYAT's own record and proposes what would make it better."""

    def __init__(self, runtime: Any = None) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._apply_lock = threading.Lock()
        self._proposals: dict[str, Improvement] = {}
        memory_path = getattr(getattr(runtime, "memory", None), "path", None)
        self.history_path = Path(memory_path).with_name("improvement-history.json") if isinstance(memory_path, (str, Path)) else None
        self._outcomes: list[dict[str, Any]] = []
        if self.history_path is not None:
            try:
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._outcomes = [item for item in data[-100:] if isinstance(item, dict)]
            except (OSError, ValueError):
                log_swallowed(_log, "loading improvement outcomes")

    # ------------------------------------------------------------- noticing

    def review(self) -> list[Improvement]:
        """Look at the evidence and say what should change.

        Every proposal cites something that actually happened. A suggestion
        without evidence is an opinion, and HAYAT has no business having those
        about its own design.
        """
        found: list[Improvement] = []
        found.extend(self._from_failures())
        found.extend(self._from_tool_record())
        found.extend(self._from_repetition())
        found.extend(self._from_provider())
        found.extend(self._from_memory())
        found.extend(self._from_learning())
        found.extend(self._from_code_monitor())
        with self._lock:
            active = {}
            for item in found:
                signature = str(item.evidence.get("signature", ""))
                key = "\0".join((item.kind, item.action, item.title, signature))
                item.proposal_id = "imp-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
                previous = self._proposals.get(item.proposal_id)
                if previous is not None:
                    item.proposed_at = previous.proposed_at
                active[item.proposal_id] = item
            # A review replaces the active snapshot. Resolved gaps disappear
            # instead of accumulating a new random proposal every 15 minutes.
            self._proposals = active
        return found

    def _from_code_monitor(self) -> list[Improvement]:
        monitor = getattr(self.runtime, "code_monitor", None)
        if monitor is None:
            return []
        try:
            projects = monitor.status().get("projects", [])
        except Exception:
            return []
        found = []
        for project in projects:
            if not project.get("scanned_at") or not project.get("issue_count"):
                continue
            first = (project.get("issues") or [{}])[0]
            location = f"{first.get('path', 'unknown')}:{first.get('line', 0)}"
            found.append(Improvement(
                title=f"repair syntax issues in {project['project']}",
                reason=f"the code monitor recorded {project['issue_count']} parser issue(s); first at {location}",
                scale=LARGE, kind="code_quality",
                evidence={"project": project["project"], "issue_count": project["issue_count"],
                          "first_issue": first, "scanned_at": project["scanned_at"]},
                action=f"scan and repair the registered {project['project']} project with tests",
            ))
        return found

    def _from_failures(self) -> list[Improvement]:
        failures = getattr(self.runtime, "failures", None)
        if failures is None:
            return []
        try:
            recent = failures.recent(50)
        except Exception:
            return []
        counts: dict[str, int] = {}
        for record in recent:
            key = record.get("tool") or record.get("service") or record.get("stage")
            if key:
                counts[str(key)] = counts.get(str(key), 0) + 1
        proposals = []
        for name, count in counts.items():
            if count < MINIMUM_ATTEMPTS:
                continue
            proposals.append(Improvement(
                title=f"look into {name}, which keeps failing",
                reason=f"it accounts for {count} of the last {len(recent)} recorded failures",
                scale=LARGE, kind="reliability",
                evidence={"subject": name, "failures": count},
                action=f"ask the repair specialist to diagnose {name}",
            ))
        return proposals

    def _from_tool_record(self) -> list[Improvement]:
        """Tools that fail often, judged against how often they are used.

        FAILURE_RATE_THRESHOLD was declared when this module was written and
        was never once applied, because nothing counted the calls that
        succeeded -- and a rate with no denominator is not a rate. A tool that
        failed four times out of four and one that failed four times out of
        four hundred produced the same proposal. The tool record supplies the
        missing half, so the threshold finally means what it says.
        """
        understanding = getattr(self.runtime, "tool_understanding", None)
        if understanding is None:
            return []
        try:
            poor = understanding.unreliable(FAILURE_RATE_THRESHOLD)
        except Exception:
            return []
        proposals = []
        for record in poor:
            percent = round(record.get('recent_failure_rate', record['failure_rate']) * 100)
            failures = record.get('recent_typical_failures', record['typical_failures'])
            usual = failures[0]['kind'] if failures else ''
            failed = record.get('recent_failed', record['failed'])
            attempts = record.get('recent_attempts', record['calls'] - record['refused'])
            proposals.append(Improvement(
                title=f"{record['tool']} fails {percent}% of recent attempts",
                reason=(f"{failed} of {attempts} recent real attempts "
                        f"failed" + (f", usually {usual}" if usual else "")
                        + " -- that is a pattern, not bad luck"),
                scale=LARGE, kind="reliability",
                evidence=record,
                action=f"ask the repair specialist to diagnose the {record['tool']} tool",
            ))
        return proposals

    def _from_repetition(self) -> list[Improvement]:
        """Work done the same way repeatedly is work worth writing down."""
        apprenticeship = getattr(self.runtime, "apprenticeship", None)
        if apprenticeship is None:
            return []
        proposals = []
        for lesson in apprenticeship.status().get("ready_to_learn", []):
            proposals.append(Improvement(
                title=f"write down how to: {lesson['goal'][:60]}",
                reason=(f"I've done this {lesson['occurrences']} times the same way, so I can "
                        "run it directly instead of planning it again"),
                # Writing a procedure down is reversible and invisible until
                # promoted, so HAYAT may do this much on its own.
                scale=SMALL, kind="procedure",
                evidence={"signature": lesson["signature"], "occurrences": lesson["occurrences"]},
                action="learn_procedure",
            ))
        return proposals

    def _from_provider(self) -> list[Improvement]:
        supervisor = getattr(self.runtime, "supervisor", None)
        if supervisor is None:
            return []
        try:
            status = supervisor.status()
        except Exception:
            return []
        if int(status.get("restart_count", 0)) >= 3:
            return [Improvement(
                title="the local model service keeps needing restarts",
                reason=f"it has restarted {status['restart_count']} times this session",
                scale=LARGE, kind="reliability",
                evidence={"restart_count": status.get("restart_count"),
                          "last_error": status.get("last_error")},
                action="investigate the provider, or pin a smaller model",
            )]
        return []

    def _from_memory(self) -> list[Improvement]:
        """Notice when derived recall indexes no longer cover durable memory."""
        memory = getattr(self.runtime, "memory", None)
        if memory is None or not hasattr(memory, "health"):
            return []
        try:
            health = memory.health()
        except Exception:
            return []
        if health.get("healthy"):
            return []
        return [Improvement(
            title="repair the memory recall indexes",
            reason=(f"integrity={health.get('integrity')}; "
                    f"missing hashed vectors={health.get('missing_hashed_vectors', 0)}; "
                    f"missing full-text rows={health.get('missing_fts_rows', 0)}"),
            scale=SMALL, kind="memory_recall", evidence=health,
            action="maintain_memory_indexes",
        )]

    def _from_learning(self) -> list[Improvement]:
        """Notice when the learning loop itself is the thing that needs fixing.

        Asked what would make it better, HAYAT answered "Nothing stands out"
        while its background learner had been unable to start a single cycle
        for over a day, its knowledge store held 223 recorded route failures
        and 15 stale entries, and its training corpus stood at 6 records
        against a gate of 1000. None of that was invisible -- it simply had no
        reviewer, because every existing source looked at tools, providers,
        failures and memory, and none looked at learning.
        """
        found: list[Improvement] = []

        learner = getattr(self.runtime, "learner", None)
        if learner is not None:
            try:
                allowed, reason = learner.may_run()
                paused = bool(getattr(learner, "paused", False))
            except Exception:
                allowed, reason, paused = True, "ok", False
            # A pause belongs to the turn in flight and clears itself; only a
            # standing block is worth raising.
            if not allowed and not paused:
                found.append(Improvement(
                    title="background learning cannot start a cycle",
                    reason=f"may_run is refusing every cycle: {reason}",
                    scale=LARGE, kind="learning",
                    evidence={"reason": reason, "running": bool(getattr(learner, "running", False))},
                    action="review the learning budget and the machine-busy thresholds",
                ))

        knowledge = getattr(getattr(self.runtime, "research", None), "knowledge", None)
        if knowledge is not None and hasattr(knowledge, "stats"):
            try:
                stats = knowledge.stats()
            except Exception:
                stats = {}
            unresolved = int(stats.get("unresolved_conflicts", 0))
            unsupported = int(stats.get("unsupported_claims", 0))
            if unsupported:
                found.append(Improvement(
                    title="withdraw verification from claims missing source evidence",
                    reason=f"{unsupported} claims are labeled supported without retained source evidence",
                    scale=SMALL, kind="knowledge_quality", evidence={"unsupported_claims": unsupported},
                    action="repair_evidence_labels",
                ))
            if unresolved:
                found.append(Improvement(
                    title="resolve contradictions between stored claims",
                    reason=f"{unresolved} conflicting claim pairs are stored without a resolution",
                    scale=SMALL, kind="knowledge_quality",
                    evidence={"unresolved_conflicts": unresolved, "claims": stats.get("claims", 0)},
                    action="resolve_claim_conflicts",
                ))
            stale = int(stats.get("stale", 0))
            if stale >= MINIMUM_ATTEMPTS:
                found.append(Improvement(
                    title="refresh knowledge that has passed its validity horizon",
                    reason=f"{stale} stored entries are past the date they were trusted until",
                    scale=SMALL, kind="knowledge_quality",
                    evidence={"stale": stale},
                    action="refresh_stale_knowledge",
                ))

        training = getattr(self.runtime, "training", None)
        if training is not None and getattr(training, "automatic_collection", False):
            try:
                status = training.status()
            except Exception:
                status = {}
            total = int(status.get("eligible", status.get("total", 0)))
            minimum = int((status.get("training_gate") or {}).get("minimum_validated_samples", 0))
            if minimum and total < minimum:
                found.append(Improvement(
                    title="the training corpus is far short of its own gate",
                    reason=(f"collection is on and has {total} eligible records against a gate of "
                            f"{minimum}; at this rate no adapter will ever be trainable"),
                    scale=LARGE, kind="learning",
                    evidence={"records": total, "required": minimum,
                              "states": status.get("states", {})},
                    action="review validated task coverage and dataset rejection reasons",
                ))
        return found

    # -------------------------------------------------------------- applying

    def apply(self, proposal_id: str, *, approved: bool = False) -> dict[str, Any]:
        """Serialize changes and retain a bounded record of observed outcomes."""
        if not self._apply_lock.acquire(blocking=False):
            return {"applied": False, "reason": "another improvement is being applied"}
        try:
            try:
                result = self._apply(proposal_id, approved=approved)
            except Exception as exc:
                result = {"applied": False, "reason": f"{type(exc).__name__}: {exc}"}
            from app.logging import redact

            record = redact({
                "at": datetime.now(UTC).isoformat(), "proposal_id": proposal_id, "result": result,
            })
            with self._lock:
                self._outcomes.append(record)
                self._outcomes = self._outcomes[-100:]
                if self.history_path is not None:
                    try:
                        self.history_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = self.history_path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(self._outcomes, indent=2, default=str), encoding="utf-8")
                        temporary.replace(self.history_path)
                    except OSError:
                        log_swallowed(_log, "saving improvement outcomes")
            return result
        finally:
            self._apply_lock.release()

    def _apply(self, proposal_id: str, *, approved: bool = False) -> dict[str, Any]:
        """Act on a proposal, if its size allows.

        A large change without approval is refused here rather than argued
        about later: this is the one place a self-improving system could widen
        its own behaviour, so the check is unconditional.
        """
        with self._lock:
            proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return {"applied": False, "reason": "no such proposal"}
        if proposal.needs_permission and not approved:
            return {"applied": False, "reason": "this one changes how I behave, so it needs your OK",
                    "proposal": proposal.to_dict()}

        if proposal.action == "learn_procedure":
            apprenticeship = getattr(self.runtime, "apprenticeship", None)
            if apprenticeship is None:
                return {"applied": False, "reason": "nothing to learn with"}
            signature = proposal.evidence.get("signature")
            lesson = next((item for item in apprenticeship._lessons.values()
                           if item.signature == signature), None)
            if lesson is None:
                return {"applied": False, "reason": "that pattern is no longer recorded"}
            return {"applied": True, **apprenticeship.learn(lesson)}

        if proposal.action == "maintain_memory_indexes":
            memory = getattr(self.runtime, "memory", None)
            if memory is None or not hasattr(memory, "maintain_indexes"):
                return {"applied": False, "reason": "memory index maintenance is unavailable"}
            result = memory.maintain_indexes()
            health = memory.health()
            return {**result, "applied": bool(health.get("healthy")), "verification": health}

        if proposal.action == "repair_evidence_labels":
            knowledge = getattr(getattr(self.runtime, "research", None), "knowledge", None)
            if knowledge is None or not hasattr(knowledge, "audit_claim_evidence"):
                return {"applied": False, "reason": "evidence audit is unavailable"}
            result = knowledge.audit_claim_evidence(repair=True)
            remaining = knowledge.audit_claim_evidence()
            return {**result, "applied": remaining["missing_evidence"] == 0,
                    "verification": remaining}

        if proposal.action == "refresh_stale_knowledge":
            # This was proposed as "I can just do this" and then fell through to
            # "I have no way to carry this one out myself" -- a dead-end
            # suggestion. The honest fix is to actually do it: hand the stale
            # topics to the background learner, which already re-researches
            # through the normal validated pipeline. Nothing is deleted; the old
            # entries stand until fresh ones supersede them.
            # The queue applies a public-topic filter, and stored queries can
            # still include whole pasted
            # prompts, remarks to HAYAT and fragments cut out of long
            # instructions. Those are retired -- kept, marked, never refreshed
            # -- rather than skipped, or they return every time they go stale.
            from app.learning.continuous import unresearchable_reason

            knowledge = getattr(getattr(self.runtime, "research", None), "knowledge", None)
            learner = getattr(self.runtime, "learner", None)
            if knowledge is None or learner is None or not hasattr(learner, "enqueue"):
                return {"applied": False, "reason": "stale-knowledge refresh is unavailable"}
            is_fragment = self._fragment_of_an_owner_message
            queued = retired = 0
            seen: set[str] = set()
            for entry in knowledge.stale_entries(limit=50):
                topic = " ".join(str(entry.get("query") or "").split())
                if not topic or topic.lower() in seen:
                    continue
                if reason := unresearchable_reason(topic, is_fragment):
                    if entry.get("id") is not None and hasattr(knowledge, "retire"):
                        knowledge.retire(int(entry["id"]), reason)
                        retired += 1
                    continue
                seen.add(topic.lower())
                try:
                    if learner.enqueue(topic, source="stale_refresh", priority=60, relearn=True):
                        queued += 1
                except Exception:
                    log_swallowed(_log, "queueing a stale-knowledge refresh")
                    continue
            return {"applied": True, "queued": queued, "retired": retired,
                    "detail": (f"handed {queued} stale topics to the background learner to refresh"
                               + (f" and retired {retired} that were never real topics" if retired else ""))}

        if proposal.action == "resolve_claim_conflicts":
            # Same dead end the stale refresh once had: proposed as something
            # HAYAT can just do, then "no way to carry this one out". Each
            # conflict becomes the research question that settles it, queued
            # for the learner's normal validated pipeline. Nothing is resolved
            # by fiat here; the evidence decides.
            from app.learning.continuous import conflict_resolution_topic

            knowledge = getattr(getattr(self.runtime, "research", None), "knowledge", None)
            learner = getattr(self.runtime, "learner", None)
            if knowledge is None or learner is None or not hasattr(learner, "enqueue"):
                return {"applied": False, "reason": "conflict resolution is unavailable"}
            queued = 0
            for conflict in knowledge.conflicts(unresolved_only=True, limit=50):
                topic = conflict_resolution_topic(conflict)
                try:
                    if topic and learner.enqueue(topic, source="reflection_conflict", priority=65):
                        queued += 1
                except Exception:
                    log_swallowed(_log, "queueing a conflict-resolution topic")
                    continue
            return {"applied": True, "queued": queued,
                    "detail": f"handed {queued} conflicting claim pairs to the background learner to settle"}

        if proposal.action.startswith("ask the repair specialist"):
            return self.change_own_code(proposal.action, proposal_id=proposal.proposal_id)

        # Anything with no way to carry it out stays a recommendation. Saying
        # so is better than pretending to act.
        return {"applied": False, "reason": "I have no way to carry this one out myself",
                "proposal": proposal.to_dict()}

    def _fragment_of_an_owner_message(self, text: str) -> bool:
        """Whether this text was cut out of something longer the owner wrote.

        "knowledge and maximally precise" was a research topic because it was
        a phrase inside a 2,000-character instruction. The whole message is
        the owner's; a slice of it is not a topic.
        """
        memory = getattr(self.runtime, "memory", None)
        if memory is None or not hasattr(memory, "connect") or len(text) < 8:
            return False
        with memory.connect() as db:
            row = db.execute(
                "SELECT 1 FROM conversations WHERE role='user' AND length(content) > ? * 2 "
                "AND instr(lower(content), lower(?)) > 0 LIMIT 1", (len(text) + 40, text),
            ).fetchone()
        return row is not None

    def change_own_code(self, goal: str, *, proposal_id: str = "") -> dict[str, Any]:
        """Change HAYAT's own source, and prove it before keeping it.

        Mohsin approved this. What makes it safe is not that he approved it --
        it is the three things the repair specialist already does and this
        route cannot bypass:

        the suite runs before and after, and a change that does not improve it
        is reverted rather than argued for; the working tree is snapshotted
        against a commit, so "reverted" means the files are actually back; and
        a change touching the files that decide what HAYAT may do -- the
        authority kernel, permissions, approvals -- is refused outright even
        when the tests would have gone green. An assistant that can widen its
        own permissions by editing the code that grants them has no boundary
        at all, only a suggestion.

        So HAYAT may rewrite how it works. It may not rewrite what it is
        allowed to do.
        """
        manager = getattr(self.runtime, "manager", None)
        repair = manager.specialists.get("repair") if manager else None
        if repair is None:
            return {"changed": False, "reason": "the repair specialist is not available"}
        try:
            report = repair.repair(goal, apply_changes=True)
        except Exception as exc:
            return {"changed": False, "reason": f"the attempt failed outright: {exc}"}

        detail = report.to_dict()
        kept = bool(getattr(report, "changed_files", None)) and not getattr(report, "reverted", False)
        return {
            "changed": kept, "reverted": bool(getattr(report, "reverted", False)),
            "proposal_id": proposal_id, "outcome": getattr(report, "outcome", ""),
            "files": list(getattr(report, "changed_files", []) or []), "report": detail,
        }

    # ------------------------------------------------------------- upgrading

    def healthy(self) -> bool:
        """Whether the system still works. The gate every self-change passes.

        Runs the project's own suite, because that is the only check that
        knows what HAYAT is supposed to do. Anything cheaper -- imports, a
        smoke test -- would pass while the thing that matters is broken.
        """
        repair = getattr(self.runtime, "manager", None)
        specialist = repair.specialists.get("repair") if repair else None
        if specialist is None:
            return False
        try:
            return specialist.run_tests().green
        except Exception:
            return False

    def upgrade_dependency(self, name: str) -> dict[str, Any]:
        """Upgrade one library, verified against the suite, reverted if worse.

        Mohsin approved self-upgrading. What makes that safe is not trust, it
        is that the change is checked and undone when it fails -- so the worst
        case is time spent rather than a runtime that stopped working.
        """
        packages = getattr(self.runtime, "packages", None)
        if packages is None:
            return {"upgraded": False, "reason": "no package installer is available"}
        result = packages.upgrade(name, verify=self.healthy)
        return {"upgraded": result.ok, **result.to_dict()}

    def upgradable(self, limit: int = 20) -> list[dict[str, Any]]:
        """What could be upgraded, newest-first, without upgrading anything."""
        packages = getattr(self.runtime, "packages", None)
        if packages is None:
            return []
        return packages.outdated()[:limit]

    # ------------------------------------------------------------- reporting

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in self._proposals.values()]

    def diagnostics(self) -> dict[str, Any]:
        """Evidence behind improvement decisions, suitable for the GUI."""
        memory = getattr(self.runtime, "memory", None)
        memory_health = None
        if memory is not None and hasattr(memory, "health"):
            try:
                memory_health = memory.health()
            except Exception as exc:
                memory_health = {"healthy": False, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "memory": memory_health,
            "pending": self.pending(),
            "recent_outcomes": list(self._outcomes[-10:]),
            "policy": "Small derived-index/procedure maintenance may run locally; behavior, reach, dependencies, and source changes remain approval-and-test gated.",
        }

    def report(self) -> str:
        proposals = self.review()
        memory = self.diagnostics().get("memory")
        memory_line = ""
        if memory:
            memory_line = (
                f" Memory recall: {memory.get('memories', 0)} records, "
                f"full-text index {'ready' if memory.get('fts_available') else 'unavailable'}, "
                f"integrity {memory.get('integrity', 'unknown')}."
            )
        with self._lock:
            recent = list(self._outcomes[-10:])
        if recent:
            succeeded = sum(bool(item.get("result", {}).get("applied") or item.get("result", {}).get("changed")) for item in recent)
            memory_line += f" Recent improvement attempts: {len(recent)}, verified/applied: {succeeded}."
        if not proposals:
            return "Nothing stands out. I'm not seeing a pattern worth changing yet." + memory_line
        lines = ["Here's what I think would make me better:"]
        lines.extend(f"  {item.describe()}" for item in proposals)
        if memory_line:
            lines.append(memory_line.strip())
        lines.append("\nSay 'improve <id>' to let me do one that needs your OK.")
        return "\n".join(lines)
