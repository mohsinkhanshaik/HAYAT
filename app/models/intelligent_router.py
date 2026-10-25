from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.models.base import ModelError, ModelProvider
from app.supervisor import CircuitBreaker
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


CAPABILITY_METRICS = {
    "conversation": "conversation_accuracy", "classification": "classification_accuracy",
    "reasoning": "reasoning_accuracy", "planning": "planning_accuracy", "tool_use": "planning_accuracy",
    "coding": "coding_accuracy", "research": "research_synthesis_accuracy",
    "teaching": "reasoning_accuracy", "summarization": "conversation_accuracy", "json": "instruction_json_reliability",
}


@dataclass(slots=True)
class RouteRequest:
    capability: str
    complexity: str = "simple"
    modality: str = "text"
    modes: tuple[str, ...] = ("AUTO",)
    privacy: str = "local"
    latency_budget_ms: int | None = None
    output_format: str = "text"


@dataclass(slots=True)
class RouteDecision:
    capability: str
    primary: str | None
    fallbacks: list[str]
    deterministic: bool
    scores: list[dict[str, Any]]
    reason: str


class BoundedFallbackProvider(ModelProvider):
    name = "adaptive"

    def __init__(self, providers: list[ModelProvider], failure_callback=None,
                 success_callback=None, admission=None) -> None:
        self.providers = providers[:3]
        self.model = self.providers[0].model if self.providers else ""
        self.failure_callback = failure_callback
        self.success_callback = success_callback
        self.admission = admission

    @staticmethod
    def _notify(callback, *args):
        # Observability failure must not turn successful inference into a
        # retry, nor suppress an otherwise viable fallback.
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                log_swallowed(_log, "routing observer callback")

    def list_models(self) -> list[str]:
        return [str(provider.model) for provider in self.providers]

    @property
    def context_length(self):
        from app.models.base import model_context_limit
        limits = [model_context_limit(provider) for provider in self.providers]
        return min(value for value in limits if value is not None) if any(value is not None for value in limits) else None

    @staticmethod
    def _usable(result):
        if not str(getattr(result, "content", "") or "").strip() and not getattr(result, "tool_calls", None):
            raise ModelError("Model returned neither public text nor a tool call")

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        from app.models.streaming import GenerationCancelled, check_cancelled
        errors = []
        for provider in self.providers:
            check_cancelled()
            if self.admission is not None and not self.admission(str(provider.model)):
                errors.append(f"{provider.model}: temporarily unavailable or insufficient resources")
                continue
            started = time.perf_counter()
            try:
                result = provider.generate(messages, tools, temperature, response_schema, max_tokens=max_tokens)
                check_cancelled()
                self._usable(result)
            except GenerationCancelled:
                raise
            except Exception as exc:
                errors.append(f"{provider.model}: {type(exc).__name__}: {exc}")
                self._notify(self.failure_callback, str(provider.model),
                             (time.perf_counter() - started) * 1000, str(exc))
                continue
            self._notify(self.success_callback, str(provider.model))
            return result
        raise ModelError("All bounded model fallbacks failed: " + " | ".join(errors))

    def generate_stream(self, messages, *, on_chunk, cancel=None, **kwargs):
        from app.models.streaming import GenerationCancelled, check_cancelled
        emitted = False

        def emit(text):
            nonlocal emitted
            if text:
                emitted = True
                on_chunk(text)

        for provider in self.providers:
            check_cancelled(cancel)
            if self.admission is not None and not self.admission(str(provider.model)):
                continue
            started = time.perf_counter()
            try:
                if callable(getattr(provider, "generate_stream", None)):
                    result = provider.generate_stream(messages, on_chunk=emit, cancel=cancel, **kwargs)
                else:
                    result = provider.generate(messages, **kwargs)
                check_cancelled(cancel)
                self._usable(result)
            except GenerationCancelled:
                raise
            except Exception as exc:
                self._notify(self.failure_callback, str(provider.model),
                             (time.perf_counter() - started) * 1000, str(exc))
                if emitted:
                    raise ModelError("Response stream interrupted; incomplete output was not saved") from exc
                continue
            check_cancelled(cancel)
            self._notify(self.success_callback, str(provider.model))
            return result
        raise ModelError("No bounded streaming provider completed the response")


class IntelligentModelRouter:
    """Evidence-based local router; adaptive evidence only comes from verified outcomes."""

    OUTCOME_WINDOW = 100
    BENCHMARK_PRIOR_WEIGHT = 8

    def __init__(self, storage, model_router, path: Path | None = None) -> None:
        self.storage = storage
        self.model_router = model_router
        self.path = path or PROJECT_ROOT / "data/memory/model_routing.db"
        routing = load_settings().get("model_routing", {})
        self.prefer_non_qwen_within_score = max(
            0.0, min(float(routing.get("prefer_non_qwen_within_score", 0.03)), 0.20)
        )
        identity = model_router.config.get("identity_model", {}) if hasattr(model_router, "config") else {}
        self.identity_model = str(identity.get("model") or "hayat:latest")
        self.identity_base_model = str(identity.get("base_model") or "qwen3.6-35b-a3b-uncensored:q4_k_m")
        self.prefer_identity_within_score = max(
            0.0, min(float(routing.get("prefer_identity_within_score", 0.02)), 0.10)
        )
        #: How much better (verified quality, 0..1) a model must be before HAYAT consults it.
        self.consult_specialist_margin = max(
            0.01, min(float(routing.get("consult_specialist_margin", 0.05)), 0.50)
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._inventory_cache: tuple[float, list[dict[str, Any]]] | None = None
        self.resource_manager = None
        self._health: dict[str, CircuitBreaker] = {}
        self._health_lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_task_outcomes(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, task_type TEXT NOT NULL,
                    model TEXT NOT NULL, verified INTEGER NOT NULL, success INTEGER NOT NULL,
                    latency_ms REAL NOT NULL, corrected INTEGER NOT NULL DEFAULT 0,
                    verification_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_model_outcomes ON model_task_outcomes(task_type,model,verified);
                """
            )

    def route(self, request: RouteRequest) -> RouteDecision:
        if request.capability in {"windows", "control"}:
            return RouteDecision(request.capability, None, [], True, [], "A verified deterministic path exists; no model is needed.")
        rows = [row for row in self._inventory() if "completion" in row.get("capabilities", [])]
        scores = []
        mode_set = set(request.modes)
        cheap_turn = request.complexity in {"instant", "simple"} and request.capability in {
            "conversation", "classification", "summarization",
        }
        quality_weight, latency_weight, resource_weight = (
            (.40, .48, .12) if cheap_turn else
            (.45, .42, .13) if "FAST" in mode_set else
            (.72, .18, .10) if mode_set & {"DEEP", "ADVERSARIAL"} else
            (.60, .28, .12)
        )
        for row in rows:
            if not self._model_health(str(row["model"])).allows():
                continue
            if request.modality == "vision" and not row.get("vision_support"):
                continue
            if request.privacy in {"local", "private"} and row.get("provider") != "ollama":
                continue
            measured = row.get("benchmark_score") or {}
            metric = CAPABILITY_METRICS.get(request.capability, "reasoning_accuracy")
            quality = self._bounded_quality(measured.get(metric, 0))
            if request.output_format == "json":
                quality = quality * .7 + self._bounded_quality(measured.get("instruction_json_reliability", 0)) * .3
            outcomes = self._outcomes(request.capability, str(row["model"]))
            # A single lucky answer must not erase stronger benchmark evidence.
            # Recent verified outcomes gradually outweigh this explicit prior.
            weight = self.BENCHMARK_PRIOR_WEIGHT
            verified_quality = (quality * weight + outcomes["success_rate"] * outcomes["count"]) / (weight + outcomes["count"])
            failure_probability = 1 - verified_quality
            latency = self._nonnegative_number(outcomes["mean_latency_ms"] or
                                              self._nonnegative_number(measured.get("mean_latency_seconds"), 999) * 1000, 999000)
            latency_score = 1 / (1 + latency / max(1, request.latency_budget_ms or 3000))
            resource = min(1.0, self._nonnegative_number(row.get("loaded_vram_bytes") or row.get("size_bytes"), 0) / 8_151_000_000)
            warmth_bonus = .08 if row.get("loaded") else 0.0
            total = quality_weight * verified_quality + latency_weight * latency_score - resource_weight * resource - .12 * failure_probability + warmth_bonus
            evidence_kind = 'foundation_inheritance' if measured.get('inherited_from') else 'benchmark' if metric in measured else 'unmeasured'
            if outcomes['count']:
                evidence_kind += '_and_recent_verified_outcomes'
            scores.append({"model": row["model"], "score": round(total, 6), "quality": round(verified_quality, 4), "latency_ms": round(latency, 2), "failure_probability": round(failure_probability, 4), "warmth": row.get("warmth", "unknown"), "vram_bytes": row.get("loaded_vram_bytes", 0), "evidence_count": outcomes["count"], "benchmark_prior_weight": weight, "evidence_kind": evidence_kind})
        scores.sort(key=lambda item: (item["score"], item["quality"], -item["latency_ms"]), reverse=True)
        identity_preferred = False
        if scores:
            identity = next((item for item in scores if item["model"] == self.identity_model), None)
            identity_task = request.capability in {"conversation", "summarization", "teaching"}
            quality_gap = float(scores[0]["quality"]) - float(identity["quality"]) if identity is not None else 1.0
            # Warmth is a latency optimization, not a reason to silently turn
            # HAYAT back into whichever backend happens to be resident. For
            # identity-facing tasks, HAYAT wins when verified task quality is
            # equivalent; materially stronger evidence still selects a
            # specialist. Classification/coding/research keep pure routing.
            if identity_task and identity is not None and quality_gap <= self.prefer_identity_within_score:
                scores.remove(identity)
                scores.insert(0, identity)
                identity_preferred = True
        if scores and "qwen" in str(scores[0]["model"]).casefold():
            best_score = float(scores[0]["score"])
            alternative = next(
                (item for item in scores[1:]
                 if "qwen" not in str(item["model"]).casefold()
                 and best_score - float(item["score"]) <= self.prefer_non_qwen_within_score),
                None,
            )
            if alternative is not None:
                scores.remove(alternative)
                scores.insert(0, alternative)
        primary = scores[0]["model"] if scores else None
        return RouteDecision(
            request.capability, primary, [item["model"] for item in scores[1:3]], False, scores,
            ("Selected by measured quality, latency, resources, failure evidence, privacy, mode, "
             "modality, warmth, and bounded identity/non-Qwen tie preferences."
             + (" HAYAT's identity model was within the configured evidence margin." if identity_preferred else "")) if primary
            else "No eligible model is available.",
        )

    def hayat_first(self, decision: RouteDecision) -> RouteDecision:
        """Only HAYAT may lead a turn; every other model is an advisor."""
        if decision.deterministic:
            return decision
        eligible = any(item["model"] == self.identity_model for item in decision.scores)
        return RouteDecision(decision.capability, self.identity_model if eligible else None, [],
                             False, decision.scores,
                             "HAYAT is the only answer writer; other models may advise."
                             if eligible else "HAYAT is unavailable; an advisor cannot replace it.")

    def specialist(self, decision: RouteDecision, margin: float | None = None) -> str | None:
        """A model worth consulting: verified quality clearly above HAYAT's for this kind of task."""
        margin = self.consult_specialist_margin if margin is None else margin
        identity = next((item for item in decision.scores if item["model"] == self.identity_model), None)
        if identity is None:
            return None
        best = max((item for item in decision.scores if item["model"] != self.identity_model),
                   key=lambda item: float(item["quality"]), default=None)
        if best is None or float(best["quality"]) - float(identity["quality"]) < margin:
            return None
        return str(best["model"])

    def provider(self, decision: RouteDecision) -> ModelProvider | None:
        if decision.deterministic or not decision.primary:
            return None
        base = self.model_router.providers.get("ollama")
        if base is None:
            return None
        providers = []
        candidates = [decision.primary, *decision.fallbacks]
        if self.resource_manager:
            eligible = [model for model in candidates if self.resource_manager.can_load(model).get("allowed")]
            candidates = eligible
        for model in candidates:
            selected = copy(base); selected.model = model; providers.append(selected)
        if not providers:
            return None
        return BoundedFallbackProvider(providers, self._record_failure,
                                       self._record_success, self._admit)

    def _model_health(self, model: str) -> CircuitBreaker:
        with self._health_lock:
            if model not in self._health:
                self._health[model] = CircuitBreaker(threshold=3, cooldown_seconds=60)
            return self._health[model]

    def _admit(self, model: str) -> bool:
        return self._model_health(model).allows() and (
            self.resource_manager is None or bool(self.resource_manager.can_load(model).get("allowed")))

    def _record_success(self, model: str) -> None:
        # A valid provider response is health evidence, not proof of answer
        # correctness. Task-quality learning still requires verification.
        self._model_health(model).record_success()

    def record_outcome(self, task_type: str, model: str, success: bool, latency_ms: float, verification: dict[str, Any], *, corrected: bool = False, verified: bool = True) -> None:
        if verified is not True or type(success) is not bool or type(corrected) is not bool:
            return
        if not isinstance(verification, dict):
            return
        if task_type != 'provider_failure' and type(verification.get('passed')) is not bool:
            return
        if success and verification.get('passed') is not True:
            return
        latency_ms = self._nonnegative_number(latency_ms, -1)
        if latency_ms < 0:
            return
        with self._connect() as db:
            db.execute("INSERT INTO model_task_outcomes(timestamp,task_type,model,verified,success,latency_ms,corrected,verification_json) VALUES(?,?,?,?,?,?,?,?)", (datetime.now(UTC).isoformat(), task_type, model, 1, int(success), max(0, latency_ms), int(corrected), json.dumps(verification, default=str)))

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            count = int(db.execute("SELECT COUNT(*) FROM model_task_outcomes WHERE verified=1").fetchone()[0])
        with self._health_lock:
            health = {model: breaker.to_dict() for model, breaker in self._health.items()}
        return {"database": str(self.path), "verified_outcomes": count, "adaptive_learning": "verified_only", "max_fallbacks": 2, "cloud_private_tasks": False, "model_health": health}

    def _outcomes(self, task_type: str, model: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*),AVG(CASE WHEN success=1 AND corrected=0 THEN 1.0 ELSE 0.0 END),AVG(latency_ms) "
                             "FROM (SELECT success,corrected,latency_ms FROM model_task_outcomes "
                             "WHERE verified=1 AND task_type=? AND model=? ORDER BY id DESC LIMIT ?)",
                             (task_type, model, self.OUTCOME_WINDOW)).fetchone()
        return {"count": int(row[0]), "success_rate": float(row[1] or 0), "mean_latency_ms": float(row[2] or 0)}

    @staticmethod
    def _nonnegative_number(value, default: float) -> float:
        try:
            number = float(value)
            return number if math.isfinite(number) and number >= 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bounded_quality(value) -> float:
        try:
            number = float(value)
            return max(0.0, min(1.0, number)) if math.isfinite(number) else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _record_failure(self, model: str, latency_ms: float, error: str) -> None:
        self._model_health(model).record_failure()
        self.record_outcome("provider_failure", model, False, latency_ms, {"error_type": error[:200]}, verified=True)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _inventory(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._inventory_cache and now - self._inventory_cache[0] < 30:
            return self._inventory_cache[1]
        rows = [dict(row) for row in self.storage.model_inventory().get("models", [])]
        base = next((row for row in rows if row.get("model") == self.identity_base_model), None)
        identity = next((row for row in rows if row.get("model") == self.identity_model), None)
        if identity is not None and not identity.get("benchmark_score") and base and base.get("benchmark_score"):
            identity["benchmark_score"] = dict(base["benchmark_score"]) | {
                "inherited_from": self.identity_base_model,
                "evidence_kind": "foundation_inheritance_until_direct_benchmark",
            }
        self._inventory_cache = (now, rows)
        return rows

    @staticmethod
    def request_for_intent(intent, modes: list[str]) -> RouteRequest:
        name = getattr(getattr(intent, "intent", None), "value", "question").lower()
        mapping = {"conversation": "conversation", "question": "reasoning", "coding": "coding", "research": "research", "learning": "teaching", "computer_control": "windows", "browser": "tool_use", "action": "planning"}
        modality = "vision" if getattr(intent, "entities", {}).get("modality") == "vision" else "text"
        complexity = getattr(getattr(intent, "complexity", None), "value", "simple").lower()
        output = "json" if name in {"coding", "action", "computer_control"} else "text"
        privacy = "private" if "PRIVATE" in modes or "LOCAL_ONLY" in modes else "local"
        return RouteRequest(mapping.get(name, "reasoning"), complexity, modality, tuple(modes), privacy, 1200 if "FAST" in modes else 8000, output)
