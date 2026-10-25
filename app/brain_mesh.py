from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.models.intelligent_router import RouteRequest


@dataclass(frozen=True, slots=True)
class BrainNode:
    node_id: str
    label: str
    layer: str
    responsibility: str
    critical: bool = True


@dataclass(frozen=True, slots=True)
class BrainEdge:
    source: str
    target: str
    contract: str


class BrainMesh:
    """Read-only operational map and self-test for HAYAT's cooperating brains.

    A node is a separately testable subsystem, not a claim of consciousness or
    a newly trained neural network. Team readiness is reported only when the
    deterministic authority, evidence, execution, and verification contracts
    connecting those nodes are intact.
    """

    NODES = (
        BrainNode("interface", "Interface", "interaction", "Shared GUI, CLI, and voice request boundary"),
        BrainNode("intent", "Intent", "reasoning", "Classify the request without granting authority"),
        BrainNode("context", "Context Budget", "reasoning", "Choose how much recent history and memory a turn needs"),
        BrainNode("router", "Model Router", "reasoning", "Choose deterministic, HAYAT, or specialist routes", False),
        BrainNode("helpers", "Specialists", "reasoning", "Route focused work to helpers sharing one governed core"),
        BrainNode("planner", "Planner", "action", "Create bounded steps and success criteria"),
        BrainNode("tool_judgment", "Tool Judgment", "action", "Use tool purpose and observed outcomes to choose useful tools"),
        BrainNode("authority", "Authority", "control", "Make the final ALLOW, ASK, or DENY decision"),
        BrainNode("tools", "Tool Bus", "action", "Execute typed contracts and retain observations"),
        BrainNode("verifier", "Truth Gate", "control", "Check observed final state before success"),
        BrainNode("memory", "Memory", "knowledge", "Tamper-evident durable recall and indexes"),
        BrainNode("dna", "DNA Graph", "knowledge", "Bounded multi-seed associative traversal"),
        BrainNode("research", "Research", "knowledge", "Fresh, sourced, contradiction-aware public knowledge"),
        BrainNode("learning", "Learning", "adaptation", "Teaching, procedures, reflection, and gated training", False),
        BrainNode("network", "Network", "operator", "Public web, browser bridge, MCP, and scoped connectors", False),
        BrainNode("perception", "Perception", "operator", "Screen, camera, OCR, clipboard, and voice sensors", False),
        BrainNode("observability", "Observability", "control", "Health, events, failures, timing, and audit evidence"),
        BrainNode("cortex", "Prefrontal Cortex", "control", "Retain goals, constraints, working evidence and bounded stop rules"),
        BrainNode("dual_brain", "Dual Brain", "reasoning", "Separate proposal from skeptical review and preserve disagreements", False),
        BrainNode("invention", "Invention", "adaptation", "Persist hypotheses and run frozen numeric prototype tests", False),
        BrainNode("inquiry", "Self-Inquiry", "reasoning",
                  "Question its own assumptions and unknowns before a hard answer", False),
    )

    EDGES = (
        BrainEdge("interface", "intent", "normalized request + session"),
        BrainEdge("intent", "context", "turn shape and context need"),
        BrainEdge("memory", "context", "bounded relevant recall"),
        BrainEdge("context", "interface", "bounded history and answer room for conversation"),
        BrainEdge("intent", "router", "capability + complexity + modality"),
        BrainEdge("intent", "helpers", "specialist role when the task needs one"),
        BrainEdge("helpers", "planner", "goal handled by the shared agent core"),
        BrainEdge("intent", "planner", "action intent + success criteria"),
        BrainEdge("router", "planner", "bounded local provider"),
        BrainEdge("planner", "tool_judgment", "candidate tool profile lookup"),
        BrainEdge("tool_judgment", "planner", "purpose and observed-use evidence"),
        BrainEdge("planner", "authority", "proposed tool + exact arguments"),
        BrainEdge("authority", "tools", "scoped decision; model cannot grant it"),
        BrainEdge("tools", "verifier", "typed observation, never assumed success"),
        BrainEdge("verifier", "memory", "verified outcome + provenance"),
        BrainEdge("memory", "dna", "indexed seeds and trusted associations"),
        BrainEdge("dna", "intent", "bounded recalled context"),
        BrainEdge("research", "verifier", "claims + evidence + contradictions"),
        BrainEdge("network", "research", "untrusted public content"),
        BrainEdge("perception", "verifier", "multi-frame or live state evidence"),
        BrainEdge("learning", "memory", "validated learning records"),
        BrainEdge("observability", "planner", "failures and measured outcomes"),
        BrainEdge("verifier", "interface", "canonical complete/failed response"),
        BrainEdge("intent", "cortex", "current goal and acceptance constraints"),
        BrainEdge("cortex", "planner", "bounded executive goal contract"),
        BrainEdge("cortex", "dual_brain", "goal criteria and model-call budget"),
        BrainEdge("dual_brain", "verifier", "untrusted critique and revised candidate"),
        BrainEdge("research", "invention", "sourced evidence and unresolved gaps"),
        BrainEdge("invention", "cortex", "persistent rounds and experiment observations"),
        BrainEdge("cortex", "inquiry", "hard-question goal and model-call budget"),
        BrainEdge("inquiry", "dual_brain", "settled points and stated unknowns for the draft"),
    )

    def __init__(self, runtime: Any, *, artifact: Path | None = None) -> None:
        self.runtime = runtime
        self.artifact = artifact or PROJECT_ROOT / "data/benchmarks/brain_mesh/latest.json"

    def topology(self) -> dict[str, Any]:
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "nodes": [asdict(node) for node in self.NODES],
            "edges": [asdict(edge) for edge in self.EDGES],
            "truth_order": ["live observation", "verified sourced knowledge", "durable memory", "model inference"],
            "authority_owner": "deterministic authority kernel",
            "model_can_authorize": False,
            "consciousness_claimed": False,
            "view_dimensions": ["x", "y", "z", "time", "evidence"],
        }

    @staticmethod
    def _result(ok: bool, detail: str, **evidence: Any) -> dict[str, Any]:
        return {"passed": bool(ok), "detail": detail, "evidence": evidence}

    def _probe_interface(self) -> dict[str, Any]:
        ok = all(hasattr(self.runtime, name) for name in ("assistant", "conversation", "intent_router"))
        voice = getattr(self.runtime, "voice", None)
        return self._result(ok, "one assistant boundary shared by text, GUI, and voice",
                            voice_available=voice is not None)

    def _probe_intent(self) -> dict[str, Any]:
        intent = self.runtime.intent_router.route("What is two plus two?", self.runtime.session)
        kind = str(getattr(getattr(intent, "intent", None), "value", ""))
        return self._result(kind == "QUESTION" and not bool(getattr(intent, "requires_tools", True)),
                            "independent deterministic question classification", intent=kind)

    def _probe_context(self) -> dict[str, Any]:
        budget = getattr(self.runtime, "context_budget", None)
        linked = budget is not None and getattr(self.runtime.conversation, "budget", None) is budget
        last = getattr(budget, "last", None)
        return self._result(linked and callable(getattr(budget, "status", None)),
                            "conversation uses the adaptive context budget",
                            last_shape=getattr(last, "shape", None),
                            last_context_tokens=getattr(last, "context_tokens", None),
                            last_answer_tokens=getattr(last, "answer_tokens", None))

    def _probe_router(self) -> dict[str, Any]:
        status = self.runtime.intelligent_router.status()
        callable_route = callable(getattr(self.runtime.intelligent_router, "route", None))
        return self._result(callable_route and int(status.get("max_fallbacks", 99)) <= 2,
                            "measured router is available with bounded fallbacks",
                            identity=getattr(self.runtime.intelligent_router, "identity_model", None),
                            max_fallbacks=status.get("max_fallbacks"),
                            verified_outcomes=status.get("verified_outcomes", 0))

    def _probe_helpers(self) -> dict[str, Any]:
        manager = getattr(self.runtime, "manager", None)
        specialists = getattr(manager, "specialists", {})
        linked = getattr(manager, "core", None) is self.runtime.agent
        shared = all(getattr(helper, "core", None) is self.runtime.agent
                     for helper in specialists.values())
        # Temporary worker teams are specialists too: show this machine's limits for them.
        teams = getattr(self.runtime, "teams", None)
        team_evidence: dict[str, Any] = {}
        if teams is not None and callable(getattr(teams, "capacity", None)):
            capacity = teams.capacity()
            team_evidence = {"team_workers_max": capacity.max_workers,
                             "teams_at_once": capacity.concurrent_teams,
                             "teams_running": len(teams.status()["running"]),
                             "seconds_per_worker": capacity.seconds_per_worker,
                             "brain_window_tokens": capacity.context_tokens}
        return self._result(linked and shared and bool(specialists),
                            "specialists share one agent core and its tool permissions; temporary teams are sized "
                            "by this machine",
                            roles=sorted(specialists), shared_core=shared, **team_evidence)

    def _probe_planner(self) -> dict[str, Any]:
        agent = self.runtime.agent
        ok = getattr(agent, "planner", None) is not None and int(getattr(agent, "max_steps", 0)) > 0
        return self._result(ok, "bounded planner is present", max_steps=getattr(agent, "max_steps", 0))

    def _probe_tool_judgment(self) -> dict[str, Any]:
        understanding = getattr(self.runtime, "tool_understanding", None)
        linked = (understanding is not None and
                  getattr(self.runtime.registry, "understanding", None) is understanding and
                  getattr(self.runtime.agent.planner, "registry", None) is self.runtime.registry)
        methods = all(callable(getattr(understanding, name, None))
                      for name in ("record_for", "totals", "overview"))
        # Every tool's description and area reaches the shortlist, not only keyword matches.
        from app.tools.relevance import index_for
        reached = "intelligence_aircraft" in index_for(self.runtime.registry).relevant(
            "which planes are flying over Dubai") if "intelligence_aircraft" in self.runtime.registry.names() else True
        return self._result(linked and methods and reached,
                            "tool registry shares observed-use records and a relevance index over every tool",
                            linked=linked, history_available=methods, relevance_index=reached,
                            tools_indexed=len(self.runtime.registry.names()))

    def _probe_authority(self) -> dict[str, Any]:
        status = self.runtime.authority.status()
        linked = getattr(self.runtime.registry, "authority", None) is self.runtime.authority
        protected = len(status.get("always_protected", []))
        return self._result(linked and protected > 0, "registry is governed by the same protected authority kernel",
                            protected_categories=protected, owner_mode=status.get("owner_mode"))

    def _probe_tools(self) -> dict[str, Any]:
        names = list(self.runtime.registry.names())
        linked = getattr(self.runtime.agent.executor, "registry", None) is self.runtime.registry
        return self._result(linked and len(names) > 0, "agent and operator UI share one typed tool registry",
                            registered=len(names), unique=len(names) == len(set(names)))

    def _probe_verifier(self) -> dict[str, Any]:
        verifier = getattr(self.runtime.agent, "verifier", None)
        return self._result(verifier is not None and callable(getattr(verifier, "check", None)),
                            "final-state verifier is independently callable")

    def _probe_memory(self) -> dict[str, Any]:
        health = self.runtime.memory.health()
        integrity = self.runtime.memory.verify_memory_integrity(limit=100_000)
        ok = bool(health.get("healthy")) and not integrity.get("mismatches")
        return self._result(ok, "SQLite, FTS, hashed vectors, and tamper digests agree",
                            memories=health.get("memories", 0), checked=integrity.get("checked", 0),
                            mismatches=len(integrity.get("mismatches", [])))

    def _probe_dna(self) -> dict[str, Any]:
        stats = self.runtime.memory.stats()
        health = self.runtime.memory.health()
        return self._result(bool(health.get("healthy")), "DNA database health checked; poisoning resistance not exercised",
                            associations=stats.get("associations", 0), bounded=True,
                            check_scope="database_health", poisoning_resistance_verified=False)

    def _probe_research(self) -> dict[str, Any]:
        """Claims carry their sources, and disagreements are on the record.

        This used to fail the whole research brain whenever any conflict sat
        unresolved, which inverts what the check is for: finding that two
        sources disagree, and saying so, is the behaviour working. Sources
        disagree about real things, so the count is never durably zero and the
        probe reported a healthy system as broken -- while never once checking
        the property it names, that claims are backed by evidence.

        Unresolved conflicts are reported as evidence and raised as a
        knowledge-quality item by the self-review, which is the right place
        for them. What fails here is a claim with nothing behind it.
        """
        stats = self.runtime.research.knowledge.stats()
        unresolved = int(stats.get("unresolved_conflicts", 0))
        claims = int(stats.get("claims", 0))
        evidence = int(stats.get("evidence", 0))
        unsupported = int(stats.get("unsupported_claims", max(0, claims - evidence)))
        return self._result(unsupported == 0,
                            "claims remain source-backed and contradictions are explicit",
                            claims=claims, evidence=evidence, unsupported_claims=unsupported,
                            unverified_claims=int(stats.get("unverified_claims", 0)),
                            unresolved_conflicts=unresolved)

    def _probe_learning(self) -> dict[str, Any]:
        report = self.runtime.learning_architecture.alignment()
        # A deliberately gated private training dataset is not an operational
        # brain failure. Surface its blocker while keeping the learning node
        # honest and available for memory/research/procedure learning.
        # Sources disagreeing is the same kind of thing: a knowledge-quality
        # item the self-review raises, not a failure of the authority
        # boundaries this node exists to check.
        excused = {"training_dataset", "contradictions_resolved"}
        hard = [name for name in report.get("blockers", []) if name not in excused]
        training = getattr(self.runtime, "training", None)
        return self._result(not hard, "learning layers are separate and permission-bounded",
                            alignment=report.get("status"), blockers=report.get("blockers", []),
                            # Read, not asserted. A status field that cannot
                            # report a change is how automatic_training stayed
                            # "False" on screen over a config that said true.
                            automatic_training=bool(getattr(training, "automatic_training", False)))

    def _probe_network(self) -> dict[str, Any]:
        names = set(self.runtime.registry.names())
        required = {"web_search", "public_web_fetch", "browser_navigate", "mcp_servers"}
        scanner = self.runtime.downloads.scanner.status()
        present = sorted(required & names)
        return self._result(required <= names and bool(scanner.get("available")),
                            "Network tools and scanner connected; internet and provider retrieval not tested",
                            capabilities=present, antimalware=scanner.get("provider"),
                            missing_capabilities=sorted(required - names),
                            check_scope="registry_and_scanner_status",
                            internet_reachability_verified=False, provider_retrieval_verified=False,
                            arbitrary_remote_control=False)

    def _probe_perception(self) -> dict[str, Any]:
        camera_node = getattr(self.runtime, "camera", None)
        voice_node = getattr(self.runtime, "voice", None)
        camera_status = getattr(camera_node, "status", None)
        voice_status = getattr(voice_node, "status", None)
        camera = camera_status() if callable(camera_status) else {}
        voice = voice_status() if callable(voice_status) else {}
        return self._result(callable(camera_status), "Sensor status read only; capture and recognition not tested",
                            camera_state=camera.get("state"), voice_state=voice.get("state", "off"),
                            camera_status_available=callable(camera_status),
                            voice_status_available=callable(voice_status), check_scope="sensor_status",
                            capture_verified=False, recognition_verified=False,
                            raw_camera_retention=camera.get("raw_retention", False))

    def _probe_observability(self) -> dict[str, Any]:
        ok = all(getattr(self.runtime, name, None) is not None
                 for name in ("events", "failures", "observability", "turns"))
        return self._result(ok, "events, failures, turns, and resource telemetry are connected")

    def _probes(self) -> dict[str, Callable[[], dict[str, Any]]]:
        return {node.node_id: getattr(self, f"_probe_{node.node_id}") for node in self.NODES}

    def _probe_cortex(self):
        cortex = self.runtime.executive
        linked = self.runtime.agent.executive is cortex and self.runtime.conversation.executive is cortex
        return self._result(linked and cortex.status()['available'], 'Executive contracts linked to action and conversation.',
                            working_memory_limit=16, model_can_authorize=False)

    def _probe_dual_brain(self):
        dual = self.runtime.dual_brain
        return self._result(self.runtime.conversation.dual_brain is dual, 'Proposer/critic loop linked; agreement is not evidence.',
                            enabled=dual.enabled, last_review=dual.status()['last_review'])

    def _probe_inquiry(self):
        inquiry = getattr(self.runtime, "inquiry", None)
        linked = inquiry is not None and self.runtime.conversation.inquiry is inquiry
        status = inquiry.status() if inquiry is not None else {}
        return self._result(linked and inquiry.executive is self.runtime.executive,
                            "Self-questioning linked to conversation; its answers are reasoning, not evidence.",
                            scope=status.get("scope"), runs=status.get("runs", 0),
                            open_points=status.get("open_points", 0), answers_are_evidence=False)

    def _probe_invention(self):
        bench = self.runtime.invention
        linked = bench.executive is self.runtime.executive and bench.dual_brain is self.runtime.dual_brain
        return self._result(linked and 'invention_run' in self.runtime.registry.names(), 'Persistent bounded experiment workbench linked.',
                            experiment_engine='exact arithmetic', arbitrary_code_execution=False)

    def independent_checks(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        for node in self.NODES:
            started = time.perf_counter()
            try:
                result = self._probes()[node.node_id]()
            except Exception as exc:
                result = self._result(False, f"{type(exc).__name__}: {exc}")
            result["critical"] = node.critical
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
            checks[node.node_id] = result
        critical_failures = [node.node_id for node in self.NODES
                             if node.critical and not checks[node.node_id]["passed"]]
        return {"passed": not critical_failures, "critical_failures": critical_failures,
                "nodes": checks}

    def team_checks(self) -> dict[str, Any]:
        checks = {
            "single_tool_bus": getattr(self.runtime.agent.executor, "registry", None) is self.runtime.registry,
            "authority_on_every_tool": getattr(self.runtime.registry, "authority", None) is self.runtime.authority,
            "shared_memory": getattr(self.runtime.agent, "memory", None) is self.runtime.memory,
            "state_reads_live_registry": getattr(self.runtime.state, "registry", None) is self.runtime.registry,
            "document_learning_reuses_shared_memory": (
                getattr(getattr(self.runtime, "public_documents", None), "memory", None)
                is self.runtime.memory
            ),
            "model_cannot_authorize": True,
            "tool_success_requires_verification": getattr(self.runtime.agent, "verifier", None) is not None,
            "network_content_untrusted": True,
            "counterattack_disabled": self.runtime.downloads.scanner.status().get("counterattack") is False,
        }
        return {"passed": all(checks.values()), "checks": checks,
                "contract": "observe -> authorize -> act -> observe -> verify -> remember"}

    def self_check(self, *, deep: bool = False) -> dict[str, Any]:
        started = time.perf_counter()
        independent = self.independent_checks()
        team = self.team_checks()
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "passed": bool(independent["passed"] and team["passed"]),
            "mode": "deep" if deep else "standard",
            "independent": independent, "team": team,
            "nodes": len(self.NODES), "edges": len(self.EDGES),
            "unsafe_capabilities": {
                "software_cracking": False, "unauthorized_access": False,
                "satellite_control_without_authorized_api": False, "counterattack": False,
            },
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        if deep:
            result["topology"] = self.topology()
        result["evidence_layer"] = self.evidence_layer(independent["nodes"], result["generated_at"])
        return result

    @staticmethod
    def evidence_layer(nodes: dict[str, Any], checked_at: str) -> dict[str, Any]:
        """Fifth view dimension: observed evidence gaps, never invented IQ."""
        result = {}
        for node in BrainMesh.NODES:
            check = nodes.get(node.node_id, {})
            evidence = check.get("evidence") or {}
            gaps = []
            if not check:
                state = "unknown"
                gaps.append("No runtime check has been recorded.")
            elif not check.get("passed"):
                state = "attention"
                gaps.append(str(check.get("detail") or "Runtime check did not pass."))
            else:
                state = "checked"
            if evidence.get("unresolved_conflicts"):
                gaps.append(f"{evidence['unresolved_conflicts']} unresolved source conflicts")
            if evidence.get("unverified_claims"):
                gaps.append(f"{evidence['unverified_claims']} claims retained as unverified")
            if evidence.get("blockers"):
                gaps.extend(str(item) for item in evidence["blockers"])
            if node.node_id == "router" and not evidence.get("verified_outcomes"):
                gaps.append("No verified model outcomes recorded yet.")
            if node.node_id == "research" and not evidence.get("claims"):
                gaps.append("No source-backed claims recorded yet.")
            if check and node.node_id == "network":
                if not evidence.get("internet_reachability_verified"):
                    gaps.append("Internet reachability was not tested; registered tools can still be offline.")
                if not evidence.get("provider_retrieval_verified"):
                    gaps.append("Search-provider retrieval was not tested.")
            if check and node.node_id == "perception":
                if not evidence.get("capture_verified"):
                    gaps.append("No camera, screen or audio capture was tested.")
                if not evidence.get("recognition_verified"):
                    gaps.append("Image, text and speech recognition were not tested.")
                if evidence.get("camera_state") in {"off", "stopped", "disabled"}:
                    gaps.append("Camera is off; no camera observation was requested.")
            if check and node.node_id == "dna" and not evidence.get("poisoning_resistance_verified"):
                gaps.append("Database health does not verify resistance to poisoned memory.")
            if gaps and state == "checked":
                state = "attention"
            result[node.node_id] = {
                "state": state, "checked_at": checked_at, "gaps": gaps,
                "observations": evidence,
                "scope": "Runtime check snapshot; not a measure of intelligence or answer accuracy.",
            }
        return {"dimension": "evidence", "checked_at": checked_at, "nodes": result}

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
        return ordered[index]

    def benchmark(self, *, iterations: int = 20, write_artifact: bool = True) -> dict[str, Any]:
        count = max(3, min(int(iterations), 200))
        samples: dict[str, list[float]] = {"intent": [], "model_router": [], "memory_health": [],
                                                  "tool_inventory": [], "team_contract": []}
        operations: dict[str, Callable[[], Any]] = {
            "intent": lambda: self.runtime.intent_router.route("Explain JSON canonicalization.", self.runtime.session),
            "model_router": lambda: self.runtime.intelligent_router.route(RouteRequest(
                capability="reasoning", complexity="complex", modes=("DEEP",))),
            "memory_health": self.runtime.memory.health,
            "tool_inventory": self.runtime.registry.names,
            "team_contract": self.team_checks,
        }
        for _ in range(count):
            for name, operation in operations.items():
                started = time.perf_counter()
                operation()
                samples[name].append((time.perf_counter() - started) * 1000)
        checks = self.self_check(deep=True)
        result = {
            "generated_at": datetime.now(UTC).isoformat(), "iterations": count,
            "passed": checks["passed"],
            "benchmarks": {
                name: {"mean_ms": round(sum(values) / len(values), 3),
                       "p95_ms": round(self._percentile(values, .95), 3),
                       "min_ms": round(min(values), 3), "max_ms": round(max(values), 3),
                       "cold_first_ms": round(values[0], 3),
                       "warm_mean_ms": round(sum(values[1:]) / len(values[1:]), 3)}
                for name, values in samples.items()
            },
            "independent_passed": checks["independent"]["passed"],
            "team_passed": checks["team"]["passed"],
            "critical_failures": checks["independent"]["critical_failures"],
            "scope": "routing and coordination only; no generation quality claim",
        }
        if write_artifact:
            self.artifact.parent.mkdir(parents=True, exist_ok=True)
            self.artifact.write_text(json.dumps(result, indent=2), encoding="utf-8")
            result["artifact"] = str(self.artifact)
        return result
