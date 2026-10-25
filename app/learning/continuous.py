"""An always-on learner, built so that it cannot run away.

This is the one subsystem in the project that acts with nobody watching. Every
other path has a person at the end of it who sees the result and can say no.
A loop that runs for days does not, so the design assumption here is that any
single cycle might be wrong and nobody will notice for a while. Everything
below follows from that.

Four rails, in the order they matter.

Authority. The learner runs as `Principal.BACKGROUND_LEARNER`. It may read
public pages/search and perform one constrained write: a claim-validated,
provenance-bearing ResearchManager result into its own knowledge database. It
does not inherit owner mode, grants, or envelopes. It cannot write arbitrary
files, click, type, download, open an application, touch a sensor, or reach
another machine -- because the authority kernel refuses, not because this loop
promises to behave.

A budget it can actually hit. Cycles per hour, network requests per hour,
bytes written per day, and a wall-clock ceiling per cycle. Exceeding any of
them stops the loop rather than slowing it, because a limit that only throttles
is a limit that gets discovered when the disk is full.

A kill switch that works without this process cooperating. A file on disk
stops it, checked every cycle, so it can be halted from outside even if the
runtime is wedged.

A daily report. Everything it did, written where the owner reads it. An
unattended process that cannot be reviewed is indistinguishable from one that
is not working.
"""
from __future__ import annotations

import json
import ctypes
import hashlib
import logging
import re
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


LEARNING_ROOT = PROJECT_ROOT / "data" / "learning"

#: Touch this file to stop the loop from outside the process.
STOP_FILE = LEARNING_ROOT / "STOP"

#: Actions the learner may take. Deliberately a list of verbs rather than a
#: capability set: even within its read-only authority, it does one kind of
#: thing. Widening it is an edit here, reviewed, not an emergent behaviour.
#: Failures that are HAYAT's own limits, not the topic's: they never use up an attempt.
_NOT_THE_TOPICS_FAULT = ("budget exhausted", "rate limit", "privacy mode", "another learning cycle",
                       "connectionerror", "connecttimeout", "readtimeout", "connection refused",
                       "temporary failure in name resolution", "http 429", "http 503")

#: The soonest a learned topic may be researched again by a stale-knowledge refresh.
RELEARN_COOLDOWN_SECONDS = 6 * 3600.0

PERMITTED_ACTIONS = frozenset({"search", "read_page", "index", "summarise", "self_review", "reuse"})


@dataclass
class LearningBudget:
    """Ceilings the loop stops at. Chosen to be reachable, not theoretical."""

    max_cycles_per_hour: int = 12
    max_requests_per_hour: int = 60
    max_bytes_per_day: int = 2 * 1024**3
    max_seconds_per_cycle: float = 300.0
    #: Never run while the owner is working. Background means background.
    min_idle_seconds: float = 120.0
    #: Refuse to start if the machine is already busy with the owner's work.
    max_cpu_percent: float = 70.0
    #: Recheck soon after the owner becomes idle instead of sleeping for the
    #: whole learning interval after a question woke the queue.
    idle_retry_seconds: float = 15.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cycles_per_hour": self.max_cycles_per_hour,
            "max_requests_per_hour": self.max_requests_per_hour,
            "max_bytes_per_day": self.max_bytes_per_day,
            "max_seconds_per_cycle": self.max_seconds_per_cycle,
            "min_idle_seconds": self.min_idle_seconds,
            "max_cpu_percent": self.max_cpu_percent,
            "idle_retry_seconds": self.idle_retry_seconds,
        }


@dataclass
class LearningLedger:
    """What has been spent. Reset on the natural boundary, not on restart.

    Counters that reset when the process restarts would let a crash loop spend
    the daily budget many times over, so the day is keyed by date rather than
    by uptime.
    """

    day: str = field(default_factory=lambda: date.today().isoformat())
    cycles: int = 0
    requests: int = 0
    bytes_written: int = 0
    cycle_times: list[float] = field(default_factory=list)
    hour_started: float = field(default_factory=time.time)
    cycles_this_hour: int = 0
    requests_this_hour: int = 0

    def roll(self) -> None:
        today = date.today().isoformat()
        if today != self.day:
            self.day, self.cycles, self.requests, self.bytes_written = today, 0, 0, 0
            self.cycle_times.clear()
        if time.time() - self.hour_started >= 3600:
            self.hour_started = time.time()
            self.cycles_this_hour = self.requests_this_hour = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day, "cycles": self.cycles, "requests": self.requests,
            "bytes_written": self.bytes_written,
            "cycles_this_hour": self.cycles_this_hour,
            "requests_this_hour": self.requests_this_hour,
            "hour_started": self.hour_started,
        }


class StopReason(str):
    pass


@dataclass
class CycleResult:
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    action: str = ""
    topic: str = ""
    ok: bool = False
    detail: str = ""
    bytes_written: int = 0
    requests: int = 0
    seconds: float = 0.0
    depth: str = ""
    confidence: float = 0.0
    sources: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at, "action": self.action, "topic": self.topic, "ok": self.ok,
            "detail": self.detail[:500], "bytes_written": self.bytes_written,
            "requests": self.requests, "seconds": round(self.seconds, 2),
            "depth": self.depth, "confidence": round(self.confidence, 3),
            "sources": self.sources,
        }


class ContinuousLearner:
    """Runs bounded research-and-index cycles while the machine is idle."""

    def __init__(self, runtime: Any = None, budget: LearningBudget | None = None,
                 root: Path | None = None,
                 cycle: Callable[[str], CycleResult] | None = None,
                 *, research_level: str = "fast", anticipate_followups: bool = False,
                 anticipated_per_request: int = 2, idle_reflection: bool = False,
                 reflection_candidates: int = 2, standby_after_budget: bool = False,
                 retry_delay_seconds: float = 60.0, maintenance_interval_seconds: float = 900.0) -> None:
        self.runtime = runtime
        self.budget = budget or LearningBudget()
        self.root = root or LEARNING_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.root / "ledger.json"
        self.queue_path = self.root / "learning.db"
        self._initialize_queue()
        self.ledger = self._load_ledger()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._paused_because = ""
        self._lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self.standby_after_budget = bool(standby_after_budget)
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.maintenance_interval_seconds = max(60.0, float(maintenance_interval_seconds))
        self._next_maintenance = 0.0
        self.last_maintenance: dict[str, Any] = {}
        self.worker_state = "stopped"
        self.waiting_because = ""
        self.last_heartbeat = 0.0
        # Learned predictions are a foreground read path. Keep their compact
        # index in RAM so checking a new question does not reopen and scan
        # SQLite on every turn; the durable database remains authoritative.
        self._anticipated_cache: list[tuple[str, str | None]] | None = None
        self._history: list[CycleResult] = []
        self.stopped_because: str | None = None
        # Injected so the loop can be exercised without touching the network.
        self._cycle_impl = cycle
        allowed_levels = {"fast", "normal", "deep", "adversarial", "primary_only"}
        self.research_level = research_level if research_level in allowed_levels else "normal"
        self.anticipate_followups = bool(anticipate_followups)
        self.anticipated_per_request = max(0, min(int(anticipated_per_request), 3))
        self.idle_reflection = bool(idle_reflection)
        self.reflection_candidates = max(0, min(int(reflection_candidates), 5))

    # ------------------------------------------------------------ kill switch

    @property
    def stop_file(self) -> Path:
        return self.root / "STOP"

    def request_stop(self, reason: str = "requested by the owner") -> None:
        """Stop after the current cycle, and record why.

        Terminal by design: nothing clears `_stop`. Use `pause()` when the
        learner should stand aside for a while and come back on its own.
        """
        self.stopped_because = reason
        self._stop.set()
        self._wake.set()

    # ---------------------------------------------------------------- pausing

    def pause(self, reason: str = "the owner's work comes first") -> None:
        """Stand aside without ending the run.

        Stopping was the only lever there was, and it is terminal -- once set,
        nothing clears it, so a learner that stepped aside for a question never
        came back. The rest of the time it was held off by a 120-second idle
        requirement, which is never satisfied while Mohsin is actually at the
        keyboard, so "background learning" ran mainly when nobody was there.

        A pause is the honest shape for "not right now": the loop stays alive,
        reports why it is idle, and resumes the moment `resume()` is called.
        """
        self._paused_because = reason
        self._paused.set()
        self._wake.set()

    def resume(self) -> None:
        """Come back, and start the next cycle without waiting out the idle timer.

        Waking the loop is the point. Without it the learner would sit through
        `idle_retry_seconds` before noticing it was allowed to work again, and
        the pause would cost more time than the answer it stood aside for.
        """
        self._paused_because = ""
        self._paused.clear()
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def halt_file_present(self) -> bool:
        return self.stop_file.is_file()

    def write_stop_file(self, reason: str = "manual halt") -> Path:
        self.stop_file.write_text(reason, encoding="utf-8")
        return self.stop_file

    def clear_stop_file(self) -> None:
        self.stop_file.unlink(missing_ok=True)

    # ---------------------------------------------------------------- budget

    def budget_breach(self) -> str | None:
        """The first ceiling that has been hit, or None. Checked before work."""
        self.ledger.roll()
        if self.ledger.cycles_this_hour >= self.budget.max_cycles_per_hour:
            return f"cycle ceiling reached ({self.budget.max_cycles_per_hour}/hour)"
        if self.ledger.requests_this_hour >= self.budget.max_requests_per_hour:
            return f"request ceiling reached ({self.budget.max_requests_per_hour}/hour)"
        if self.ledger.bytes_written >= self.budget.max_bytes_per_day:
            return f"daily write ceiling reached ({self.budget.max_bytes_per_day} bytes)"
        return None

    def machine_is_busy(self) -> bool:
        """Refuse to compete with the owner for their own machine.

        Keyboard idle time used to be the first and strongest test, and it is
        the wrong instrument: typing is not resource contention. Requiring two
        minutes of stillness meant the learner sat out every session Mohsin was
        present for, which is most of them -- reported honestly as "the machine
        is busy with your work" while the CPU was idle.

        `pause()` now carries that concern precisely, so what is left here is
        the thing the name actually claims: real contention for RAM and CPU.
        Idle time still counts, but only as a tiebreaker once the machine is
        already under load.
        """
        try:
            from app.hardware import profile

            hardware = profile(refresh=True)
            if hardware.available_ram_bytes is not None and hardware.available_ram_bytes < 2 * 1024**3:
                return True
        except Exception:
            log_swallowed(_log, "reading the hardware profile for learning budget")
        cpu = _windows_cpu_percent()
        if cpu is not None and cpu > self.budget.max_cpu_percent:
            return True
        # Under load and actively being typed at: yield. Under load with the
        # keyboard quiet, the load is not Mohsin's typing and a cycle is fine.
        if cpu is not None and cpu > self.budget.max_cpu_percent * 0.75:
            return _windows_idle_seconds() < self.budget.min_idle_seconds
        return False

    def may_run(self) -> tuple[bool, str]:
        """Every reason not to start a cycle, checked in one place."""
        if self._stop.is_set():
            return False, self.stopped_because or "stopped"
        if self._paused.is_set():
            return False, self._paused_because or "paused"
        if self.halt_file_present():
            return False, f"halted by {self.stop_file.name}"
        if bool(getattr(getattr(self.runtime, "authority", None), "privacy_mode", False)):
            return False, "privacy mode is on"
        breach = self.budget_breach()
        if breach:
            return False, breach
        if self.runtime is not None and self.machine_is_busy():
            return False, "the machine is busy with your work"
        storage = getattr(self.runtime, "storage", None) if self.runtime is not None else None
        if storage is not None:
            capacity = storage.category_capacity("learning")
            if capacity.get("quota_bytes") is not None and int(capacity.get("remaining_bytes") or 0) <= 0:
                return False, "the governed learning allocation is full"
        return True, "ok"

    # ----------------------------------------------------------------- cycle

    def run_cycle(self, topic: str = "") -> CycleResult:
        """One bounded unit of learning. Never raises into the loop."""
        if not self._cycle_lock.acquire(blocking=False):
            return CycleResult(action="skipped", topic=topic, ok=False, detail="another learning cycle is running")
        try:
            return self._run_cycle(topic)
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, topic: str) -> CycleResult:
        started = time.perf_counter()
        allowed, reason = self.may_run()
        if not allowed:
            # next_topic() claims before this second admission check. Return
            # that claim to the queue without spending an attempt when paused.
            self._release_topic(topic)
            return CycleResult(action="skipped", topic=topic, ok=False, detail=reason)

        try:
            from app.activity import ACTIVITY

            with ACTIVITY.track("learning", f"{self.research_level} learning", topic[:180]):
                result = (self._cycle_impl or self._default_cycle)(topic)
        except Exception as exc:
            result = CycleResult(action="error", topic=topic, ok=False,
                                 detail=f"{type(exc).__name__}: {exc}")
        # The scheduler owns the claim; an implementation cannot strand it
        # by omitting (or returning a different) topic in its result.
        result.topic = topic
        result.seconds = time.perf_counter() - started
        if result.seconds > self.budget.max_seconds_per_cycle:
            result.ok = False
            result.detail = (
                f"cycle exceeded its {self.budget.max_seconds_per_cycle:.0f}s wall-clock budget; "
                f"it took {result.seconds:.1f}s"
            )
            self.stopped_because = result.detail
            self._stop.set()

        if result.action and result.action not in PERMITTED_ACTIONS and result.ok:
            # A cycle that did something outside the permitted verbs is a bug,
            # and it is recorded as a refusal rather than quietly accepted.
            result = CycleResult(
                action="refused", topic=topic, ok=False,
                detail=f"{result.action} is not a permitted background action",
                seconds=result.seconds,
            )

        with self._lock:
            self.ledger.roll()
            self.ledger.cycles += 1
            self.ledger.cycles_this_hour += 1
            self.ledger.requests += result.requests
            self.ledger.requests_this_hour += result.requests
            self.ledger.bytes_written += result.bytes_written
            self.ledger.cycle_times.append(result.seconds)
            self._history.append(result)
            del self._history[:-500]
            self._save_ledger()
        self._finish_topic(result)
        self._journal(result)
        return result

    def _default_cycle(self, topic: str) -> CycleResult:
        """Verified research through the runtime, as the learner principal.

        Goes through the normal tool registry so the authority kernel sees the
        request and can refuse it. Nothing here bypasses the dispatch path.
        """
        if topic and not public_learning_topic(topic):
            return CycleResult(action="refused", topic=topic, ok=False,
                               detail="topic is not suitable for unattended public research")
        registry = getattr(self.runtime, "registry", None)
        if registry is None or not topic:
            return CycleResult(action="self_review", topic=topic, ok=True,
                               detail="no runtime attached; nothing to research")
        question = self._research_question(topic)
        if question is None:
            return CycleResult(action="refused", topic=topic, ok=False,
                               detail="read as conversation, not a question about the world; not researched")
        research = getattr(self.runtime, "research", None)
        if research is not None:
            try:
                recalled = research.recall_verified(question)
            except Exception:
                recalled = None
            if recalled is not None and recalled.complete:
                return CycleResult(
                    action="reuse", topic=topic, ok=True,
                    detail=(
                        f"fresh validated knowledge already covers this topic "
                        f"({recalled.stop_reason}); skipped duplicate web work"
                    ),
                    requests=0, bytes_written=0, depth="local_recall",
                    confidence=max(0.0, min(float(recalled.confidence or 0.0), 1.0)),
                    sources=len(recalled.sources),
                )
        from app.execution import execution_scope

        knowledge_path = Path(getattr(getattr(research, 'knowledge', None), 'path',
                                      PROJECT_ROOT / 'data' / 'knowledge' / 'knowledge.db'))
        before_bytes = _sqlite_footprint(knowledge_path)
        with self._queue() as db:
            row = db.execute('SELECT attempts FROM learning_queue WHERE topic=?', (topic,)).fetchone()
        attempt = int(row[0]) if row else 0
        # Repeating the same failed search produces the same unsupported result.
        # Try primary sources next, then a wider normal pass, without extra calls
        # inside a cycle or relaxing the evidence gate.
        level = self.research_level if attempt == 0 else 'primary_only' if attempt == 1 else 'normal'
        budget_scope = getattr(registry, "budget_scope", nullcontext)
        with execution_scope(principal="BACKGROUND_LEARNER", task_id=None), budget_scope():
            result = registry.execute("research_topic", {
                "query": question, "level": level,
                "official_only": level == 'primary_only', "remember": True,
            })
        after_bytes = _sqlite_footprint(knowledge_path)
        output = result.output if result.success and isinstance(result.output, dict) else {}
        metrics = output.get("metrics", {}) if isinstance(output, dict) else {}
        sources = output.get("sources", []) if isinstance(output, dict) else []
        citations = output.get("citations", []) if isinstance(output, dict) else []
        supported = int(metrics.get("claims_supported", 0) or 0)
        validated = bool(result.success and output.get("complete") and sources and citations and supported > 0)
        answer = " ".join(str(output.get("answer", "")).split())[:350]
        failure = ''
        if not validated and result.success:
            stop = str(output.get('stop_reason') or 'insufficient_evidence')
            errors = '; '.join(str(value)[:120] for value in output.get('errors', [])[:2])
            failure = (f'No supported cited claim was retained; topic remains unlearned. '
                       f'Strategy: {level}; reason: {stop}; sources: {len(sources)}; supported: {supported}.'
                       + (f' Retrieval: {errors}' if errors else ''))
        return CycleResult(
            action="index", topic=topic, ok=validated,
            detail=(result.error or failure
                    or ((f"Researched as: {question} | " if question != topic else "") + answer)
                    or output.get("stop_reason") or "research completed")[:400],
            requests=max(0, int(metrics.get("searches", 0)) + int(metrics.get("pages_retrieved", len(sources)))),
            bytes_written=max(0, after_bytes - before_bytes),
            depth=level,
            confidence=max(0.0, min(float(output.get("confidence", 0.0) or 0.0), 1.0)),
            sources=len(sources),
        )

    #: Queue sources that hold someone's own words rather than a clean topic.
    _RAW_SOURCES = ("user_", "anticipated_follow_up")
    _QUESTION_PROMPT = (
        "You prepare topics for a background web-research queue for an assistant named HAYAT. Read the message "
        "and reply with exactly one line: a clear, standalone question about the world that a search engine can "
        "answer, in English, under 20 words, about the subject the person actually meant. Reply NONE if the "
        "subject is ambiguous or requires inventing an acronym or named entity. Correct obvious spelling only "
        "when the intended ordinary word is clear; never turn a typo into a new technical entity. Reply NONE if the "
        "message is small talk, names or addresses HAYAT, asks what the assistant did or learned, is about the "
        "person's private life, files, accounts or computer, or its words do not form a meaningful subject.\n"
        "Examples:\n"
        "nice, so what did you find out this morning -> NONE\n"
        "hayat are you there -> NONE\n"
        "alright now find me ways to earn some cash -> What are realistic ways for one person to earn money online?\n"
        "tell me everything important about investing and stuff i need to know -> What are the basic principles "
        "every beginner investor should know?\n"
        "What are the limitations and trade-offs for cheap required quickly? -> NONE\n"
        "why does my laptop fan get loud -> Why do laptop fans get loud under load?"
    )

    def _research_question(self, topic: str) -> str | None:
        """The clean research question behind a queued message, or None when there is none.

        Raw requests were researched word for word: "great what have learn about
        today" and "ok good now look for ways to make money" went to the search
        engine as written, and templates built "failure modes ... for money
        required elite" from them, which stored claims about a video game. HAYAT's
        own model now reads each such message first. Seed, reflection and
        refresh topics are already clean and pass unchanged, and so does every
        topic when no model is reachable, so learning never stops for this.
        """
        try:
            with self._queue() as db:
                row = db.execute("SELECT source FROM learning_queue WHERE topic_hash=?", (
                    hashlib.sha256(" ".join(topic.split()).casefold().encode("utf-8")).hexdigest(),)).fetchone()
        except Exception:
            row = None
        source = str(row[0]) if row else ""
        if not source.startswith(self._RAW_SOURCES):
            return topic
        model = getattr(self.runtime, "model", None)
        if model is None:
            return topic
        try:
            reply = model.generate([
                {"role": "system", "content": self._QUESTION_PROMPT},
                {"role": "user", "content": topic},
            ], temperature=0.0, max_tokens=60)
        except Exception:
            log_swallowed(_log, "turning a queued message into a research question")
            return topic
        line = next((part.strip() for part in str(getattr(reply, "content", "") or "").splitlines() if part.strip()), "")
        line = re.sub(r"(?i)^(?:question|research question)\s*:\s*", "", line).strip(" \"'*")
        if not line or line.upper().startswith("NONE"):
            return None
        if len(line) > 200 or not public_learning_topic(line):
            return None
        return line

    def next_topic(self) -> str:
        """Something worth reading about, drawn from what actually happened.

        Topics come from the system's own record -- what failed recently, what
        Mohsin asked about -- rather than from a fixed list or from the model
        inventing an interest. A learner that studies its own failures is
        useful; one that wanders is just noise on the disk.
        """
        queued = self._claim_next_topic()
        if queued:
            return queued
        # The adaptive control plane folds conflicts, staleness, verified
        # failures and measured weaknesses into one ranked curriculum. It is
        # attached after the learner during runtime assembly, so startup and
        # isolated tests legitimately reach this branch without it.
        control = getattr(self.runtime, "metacognition", None)
        if control is not None:
            try:
                result = control.queue_curriculum(1)
                if result.get("queued"):
                    queued = self._claim_next_topic()
                    if queued:
                        return queued
            except Exception:
                log_swallowed(_log, "queueing a curriculum topic")
        failures = getattr(self.runtime, "failures", None)
        if failures is not None:
            try:
                for record in reversed(failures.recent(5)):
                    dependency = record.get("dependency") or record.get("service")
                    if dependency:
                        topic = f"{dependency} {record.get('failure_class', '')} troubleshooting".strip()
                        if self.enqueue(topic, source="runtime_failure", priority=90):
                            queued = self._claim_next_topic()
                            if queued:
                                return queued
            except Exception:
                log_swallowed(_log, "choosing a topic from recent failures")
        memory = getattr(self.runtime, "memory", None)
        if memory is not None:
            try:
                for task in memory.recent_tasks(3):
                    goal = str(task.get("goal", "")).strip()
                    if goal and task.get("status") != "complete":
                        if self.enqueue(goal, source="unfinished_task", priority=80):
                            queued = self._claim_next_topic()
                            if queued:
                                return queued
            except Exception:
                log_swallowed(_log, "choosing a topic from unfinished tasks")

        # Slow self-directed thought is an evidence-gap audit, not free-form
        # model daydreaming. Deterministically surface unresolved
        # contradictions and stale knowledge, queue a bounded verification
        # question, and let the normal restricted research cycle handle it.
        # Direct user questions and actual runtime failures always rank first.
        if self.idle_reflection:
            try:
                for topic, source, priority in reflection_topics(
                    self.runtime, limit=self.reflection_candidates,
                ):
                    if self.enqueue(topic, source=source, priority=priority):
                        queued = self._claim_next_topic()
                        if queued:
                            return queued
            except Exception:
                log_swallowed(_log, "choosing a topic from reflection")

        # Nothing queued, nothing broken, nothing unfinished. Rather than
        # sitting idle or wandering, go deeper on what Mohsin keeps asking
        # about -- how it works, where it goes wrong, what the trade-offs are.
        # Before this the learner only ever pursued his literal questions, so
        # it answered what was asked and then had nothing of its own to study.
        if memory is not None:
            try:
                for topic in follow_up_topics(memory, limit=3):
                    if self.enqueue(topic, source="follow_up", priority=40):
                        queued = self._claim_next_topic()
                        if queued:
                            return queued
            except Exception:
                log_swallowed(_log, "choosing a follow-up topic")
        return ""

    def observe_request(self, text: str, intent: str = "") -> bool:
        """Queue a meaningful knowledge gap without storing every utterance.

        Greetings, control commands and short computer actions are deliberately
        ignored. Questions, teaching requests, research goals and diagnostics
        become durable candidates that survive a restart until learned.
        """
        value = " ".join(str(text).split()).strip()
        kind = str(intent).upper()
        from app.chat_feedback import CORRECTION
        from app.agent.conversation import refers_to_previous_reply
        if CORRECTION.match(value) or refers_to_previous_reply(value):
            return False  # Local feedback/pronouns are not stand-alone public research topics.
        if len(value) < 12 or value.startswith("/"):
            return False
        if kind in {"CONTROL", "SYSTEM_STATE", "MEMORY", "COMPUTER_CONTROL", "BROWSER", "AUTOMATION"}:
            return False
        # Diagnostics and coding requests commonly contain private source,
        # paths, hostnames or customer data. They can research explicitly in
        # the foreground, but are never copied wholesale into an unattended
        # public-web queue.
        knowledge_intents = {"QUESTION", "RESEARCH", "LEARNING"}
        if kind in {"DIAGNOSTIC", "CODING"}:
            return False
        conversational_gap = bool(re.search(r"(?i)\b(?:how|why|what|explain|learn|understand|compare|diagnose)\b", value))
        if kind not in knowledge_intents and not conversational_gap:
            return False
        from app.safety.sensitive import redact_text

        _, findings = redact_text(value)
        private_context = re.search(
            r"(?i)(?:\b[A-Z]:\\|\\\\|file://|(?:^|\s)/(?:users|home|etc|var)/|"
            r"\b(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b|"
            r"\bmy\s+(?:account|email|file|folder|project|repository|code|document|company|client|customer)\b|"
            r"\bthis\s+(?:private|internal|local|customer|client)\b)",
            value,
        )
        if findings or private_context or not _is_about_the_world(value):
            return False
        priority = 95 if kind == "RESEARCH" else 70
        queued = self.enqueue(
            value[:500], source=f"user_{kind.lower() or 'conversation'}", priority=priority,
        )
        if self.anticipate_followups and _WELL_FORMED_QUESTION.match(value):
            for predicted in self._ranked_anticipated_questions(
                value, limit=self.anticipated_per_request,
            ):
                queued = self.enqueue(
                    predicted, source="anticipated_follow_up", priority=max(35, priority - 25),
                ) or queued
        return queued

    def _ranked_anticipated_questions(self, text: str, limit: int = 2) -> list[str]:
        """Prefer follow-up facets whose earlier predictions were actually used.

        This stays deterministic and local. Match/mismatch feedback comes only
        from the strict foreground matcher, so a model cannot reward its own
        guess and a prediction about a different question does not count.
        """
        candidates = anticipated_questions(text, limit=3)
        if len(candidates) <= 1:
            return candidates[:limit]
        try:
            from app.question_match import question_facets, subject_tokens

            subject = subject_tokens(text)

            with self._queue() as db:
                rows = db.execute(
                    "SELECT topic,match_count,mismatch_count FROM learning_queue "
                    "WHERE source='anticipated_follow_up' AND status='learned' "
                    "AND (match_count > 0 OR mismatch_count > 0)"
                ).fetchall()
            feedback: dict[str, list[int]] = {}
            for topic, matches, mismatches in rows:
                previous_subject = subject_tokens(str(topic))
                shared = subject & previous_subject
                if len(shared) < 2 or len(shared) / max(1, min(len(subject), len(previous_subject))) < .6:
                    continue
                for facet in question_facets(str(topic)) or {"general"}:
                    totals = feedback.setdefault(facet, [0, 0])
                    totals[0] += int(matches or 0)
                    totals[1] += int(mismatches or 0)

            def score(item: tuple[int, str]) -> tuple[float, int]:
                index, question = item
                facets = question_facets(question) or {"general"}
                rates = []
                for facet in facets:
                    matches, mismatches = feedback.get(facet, [0, 0])
                    rates.append((matches + 1) / (matches + mismatches + 2))
                return (sum(rates) / len(rates), -index)

            ranked = sorted(enumerate(candidates), key=score, reverse=True)
            return [question for _index, question in ranked[:max(0, min(int(limit), 3))]]
        except Exception:
            return candidates[:max(0, min(int(limit), 3))]

    def enqueue(self, topic: str, *, source: str = "owner", priority: int = 50,
                retry_failed: bool = False, relearn: bool = False) -> bool:
        """Queue a topic. A topic already learned is refused unless ``relearn``:
        the stale-knowledge refresh needs exactly that, and without it every
        stale topic had been learned once and could never be refreshed."""
        cleaned = " ".join(str(topic).split()).strip()
        if not public_learning_topic(cleaned):
            return False
        digest = hashlib.sha256(cleaned.casefold().encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self._queue() as db:
            existing = db.execute("SELECT status,learned_at FROM learning_queue WHERE topic_hash=?", (digest,)).fetchone()
            if existing and existing[0] == "learned" and relearn and existing[1]:
                # A refresh within hours of learning is a loop, not a refresh.
                try:
                    age = (datetime.now(UTC) - datetime.fromisoformat(existing[1])).total_seconds()
                except ValueError:
                    age = RELEARN_COOLDOWN_SECONDS
                if age < RELEARN_COOLDOWN_SECONDS:
                    return False
            if existing and existing[0] in {"learned", "failed"}:
                if (existing[0] == "failed" and retry_failed) or (existing[0] == "learned" and relearn):
                    db.execute(
                        "UPDATE learning_queue SET status='pending',attempts=0,last_error=NULL,ready_after=0,source=?,priority=?,updated_at=? WHERE topic_hash=?",
                        (source, max(0, min(100, int(priority))), now, digest),
                    )
                    self._wake.set()
                    return True
                return False
            db.execute(
                "INSERT INTO learning_queue(topic,topic_hash,source,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(topic_hash) DO UPDATE SET priority=MAX(priority,excluded.priority),source=excluded.source,"
                "updated_at=excluded.updated_at",
                (cleaned, digest, source, max(0, min(100, int(priority))), "pending", now, now),
            )
        self._wake.set()
        return True

    def anticipated_match(self, question: str) -> dict[str, Any] | None:
        """Best learned prediction for this question, with strict match evidence."""
        from app.question_match import compare_questions

        with self._lock:
            rows = self._anticipated_cache
        if rows is None:
            with self._queue() as db:
                loaded = db.execute(
                    "SELECT topic,learned_at FROM learning_queue "
                    "WHERE source='anticipated_follow_up' AND status='learned' "
                    "ORDER BY learned_at DESC LIMIT 200"
                ).fetchall()
            rows = [(str(row[0]), row[1]) for row in loaded]
            with self._lock:
                self._anticipated_cache = rows
        best: tuple[float, dict[str, Any]] | None = None
        for row in rows:
            comparison = compare_questions(row[0], question)
            related = comparison.subject_overlap >= 0.45 and comparison.containment >= 0.60
            if not comparison.same and not related:
                continue
            payload = {
                "expected_question": row[0], "learned_at": row[1],
                "matched": comparison.same, "related_but_different": related and not comparison.same,
                "score": comparison.score, "reason": comparison.reason,
                "expected_facets": list(comparison.expected_facets),
                "actual_facets": list(comparison.actual_facets),
            }
            rank = comparison.score + (1.0 if comparison.same else 0.0)
            if best is None or rank > best[0]:
                best = (rank, payload)
        if best is None:
            return None
        payload = best[1]
        now = datetime.now(UTC).isoformat()
        column = "match_count" if payload["matched"] else "mismatch_count"
        with self._queue() as db:
            db.execute(
                f"UPDATE learning_queue SET {column}={column}+1,last_matched_at=? WHERE topic=?",
                (now, payload["expected_question"]),
            )
        return payload

    def retry(self, topic: str) -> bool:
        """Explicitly reset one exhausted topic after the owner reviews it."""
        return self.enqueue(topic, source="owner_retry", priority=90, retry_failed=True)

    def retry_evidence_failures(self, limit: int = 20) -> dict[str, Any]:
        """Explicit recovery of evidence failures; retain conversational refusals."""
        with self._queue() as db:
            rows = db.execute("SELECT topic,source FROM learning_queue WHERE status='failed' AND "
                              "(last_error LIKE '%could not validate%' OR last_error LIKE '%No supported cited claim%') "
                              "ORDER BY priority DESC LIMIT ?", (max(1, min(int(limit), 50)),)).fetchall()
            recovered = 0
            for topic, source in rows:
                if not public_learning_topic(topic):
                    continue
                # Begin with a different source strategy. Preserve source so raw
                # owner messages still go through question normalization.
                db.execute("UPDATE learning_queue SET status='pending',attempts=1,ready_after=0,updated_at=? "
                           "WHERE topic=?", (datetime.now(UTC).isoformat(), topic))
                recovered += 1
        self._wake.set()
        return {'requeued': recovered, 'strategy': 'primary_only', 'learned': False}

    def queue_status(self, limit: int = 20) -> dict[str, Any]:
        with self._queue() as db:
            db.row_factory = sqlite3.Row
            counts = {row[0]: int(row[1]) for row in db.execute("SELECT status,COUNT(*) FROM learning_queue GROUP BY status")}
            prediction = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(match_count),0),COALESCE(SUM(mismatch_count),0) "
                "FROM learning_queue WHERE source='anticipated_follow_up'"
            ).fetchone()
            rows = db.execute(
                "SELECT topic,source,priority,status,attempts,last_error,updated_at,"
                "match_count,mismatch_count,last_matched_at,ready_after FROM learning_queue "
                "ORDER BY CASE status WHEN 'learning' THEN 0 WHEN 'pending' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,priority DESC,updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        matches, mismatches = int(prediction[1]), int(prediction[2])
        return {
            "database": str(self.queue_path), "counts": counts,
            "topics": [dict(row) for row in rows],
            "retry_delay_seconds": self.retry_delay_seconds,
            "predictions": {
                "prepared": int(prediction[0]), "matched": matches,
                "different_question": mismatches,
                "precision": round(matches / max(1, matches + mismatches), 4),
                "ranking": "verified_match_feedback",
            },
        }

    @contextmanager
    def _queue(self) -> Any:
        """Open, commit, and actually close.

        `with sqlite3.connect(...) as db:` manages the transaction and leaves
        the connection open -- a leak on every call, and on Windows an open
        handle is also why a corrupt file could not be renamed out of the way.
        Every access goes through here so the handle is released.
        """
        db = self._connect()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        """Open the queue in a way that survives two writers and a hard exit.

        The queue is written by the background learner and by the turn that
        enqueues a topic, and it was opened with default settings: no WAL, no
        busy timeout. On Windows that is how a file ends up reporting
        "database disk image is malformed", which is exactly what happened
        here -- twice, judging by the quarantined copy already sitting beside
        it.

        WAL lets a reader and a writer coexist instead of colliding, and the
        busy timeout makes a concurrent write wait rather than fail.
        """
        db = sqlite3.connect(self.queue_path, timeout=15.0)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            # FULL, not NORMAL.
            #
            # Under WAL, NORMAL does not fsync the write-ahead log on commit:
            # the database survives a process crash but not the machine losing
            # power or the process being killed at the wrong instant. This
            # queue has now been quarantined as malformed twice -- 2026-08-17
            # and again at 02:42 on 2026-08-19 -- and HAYAT is a program that
            # gets closed abruptly by design, which is precisely the exposure.
            #
            # The cost is one fsync per commit on a queue that writes a
            # handful of rows an hour. That is nothing next to losing
            # everything it meant to read about.
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=15000")
        except sqlite3.DatabaseError:
            db.close()
            raise
        return db

    def _quarantine_corrupt_queue(self, error: Exception) -> bool:
        """Move a corrupt queue aside so a new one can be built.

        The queue holds topics HAYAT meant to read about later. Losing it
        costs nothing that cannot be relearned, and keeping a malformed file
        in place costs the learner entirely. The old file is renamed rather
        than deleted so the corruption can still be looked at.
        """
        # "unsupported file format" belongs here too. That is what SQLite says
        # when page one has been overwritten with something that is not a
        # database page -- the state registry.db was found in -- and checking
        # only for the other two would have let exactly that case through.
        markers = ("malformed", "not a database", "unsupported file format",
                   "file is encrypted", "database disk image")
        if not any(marker in str(error).lower() for marker in markers):
            return False
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        try:
            self.queue_path.rename(self.queue_path.with_name(f"learning.corrupt.{stamp}.db"))
        except OSError:
            return False
        for suffix in ("-wal", "-shm"):
            side = self.queue_path.with_name(self.queue_path.name + suffix)
            side.unlink(missing_ok=True)
        return True

    def _initialize_queue(self) -> None:
        try:
            self._create_queue()
        except sqlite3.DatabaseError as error:
            # A corrupt queue must not take the learner down permanently.
            if not self._quarantine_corrupt_queue(error):
                raise
            self._create_queue()

    def _create_queue(self) -> None:
        with self._queue() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS learning_queue(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    topic_hash TEXT NOT NULL UNIQUE,
                    source TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 50,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    learned_at TEXT,
                    match_count INTEGER NOT NULL DEFAULT 0,
                    mismatch_count INTEGER NOT NULL DEFAULT 0,
                    last_matched_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_learning_queue_state ON learning_queue(status,priority,updated_at);
                CREATE INDEX IF NOT EXISTS idx_learning_queue_predictions ON learning_queue(source,status,learned_at);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(learning_queue)").fetchall()}
            for name, declaration in {
                "match_count": "INTEGER NOT NULL DEFAULT 0",
                "mismatch_count": "INTEGER NOT NULL DEFAULT 0",
                "last_matched_at": "TEXT",
                "ready_after": "REAL NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE learning_queue ADD COLUMN {name} {declaration}")
            # A process killed mid-cycle must not strand a topic forever.
            db.execute("UPDATE learning_queue SET status='pending' WHERE status='learning'")
            # Older/interrupted builds could leave exhausted rows labelled
            # pending even though the claim query will never select them.
            db.execute("UPDATE learning_queue SET status='failed' WHERE status='pending' AND attempts>=3")
            # Topics that "failed" only because the shared tool budget ran out
            # (before background work had its own) get their attempts back.
            db.execute("UPDATE learning_queue SET status='pending',attempts=0,last_error=NULL,ready_after=0 "
                       "WHERE status='failed' AND last_error LIKE '%budget exhausted%'")

    def _claim_next_topic(self) -> str:
        now = datetime.now(UTC).isoformat()
        with self._queue() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id,topic FROM learning_queue WHERE status='pending' AND attempts<3 AND ready_after<=? "
                "ORDER BY priority DESC,created_at ASC LIMIT 1", (time.time(),)
            ).fetchone()
            if not row:
                return ""
            db.execute(
                # Claiming is not a failed attempt. A shutdown may interrupt a
                # page fetch; only a completed failed cycle consumes retry
                # budget in _finish_topic().
                "UPDATE learning_queue SET status='learning',updated_at=? WHERE id=?",
                (now, row[0]),
            )
            return str(row[1])

    def _finish_topic(self, result: CycleResult) -> None:
        if not result.topic:
            return
        digest = hashlib.sha256(" ".join(result.topic.split()).casefold().encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        with self._queue() as db:
            row = db.execute("SELECT attempts,status FROM learning_queue WHERE topic_hash=?", (digest,)).fetchone()
            if not row:
                return
            if result.ok:
                db.execute(
                    "UPDATE learning_queue SET status='learned',last_error=NULL,ready_after=0,learned_at=?,updated_at=? WHERE topic_hash=?",
                    (now, now, digest),
                )
                with self._lock:
                    self._anticipated_cache = None
            elif any(marker in result.detail.lower() for marker in _NOT_THE_TOPICS_FAULT):
                # HAYAT ran out of its own allowance; the topic did nothing wrong.
                # These used to burn all three attempts and sit in "failed" forever.
                db.execute(
                    "UPDATE learning_queue SET status='pending',last_error=?,updated_at=?,ready_after=? WHERE topic_hash=?",
                    (result.detail[:500], now, time.time() + max(300.0, self.retry_delay_seconds), digest),
                )
            else:
                attempts = int(row[0]) + 1
                status = "failed" if attempts >= 3 or result.action == "refused" else "pending"
                ready_after = time.time() + min(3600.0, self.retry_delay_seconds * 2 ** (attempts - 1))
                db.execute(
                    "UPDATE learning_queue SET status=?,attempts=?,last_error=?,updated_at=?,ready_after=? WHERE topic_hash=?",
                    (status, attempts, result.detail[:500], now, ready_after, digest),
                )

    def _release_topic(self, topic: str) -> None:
        if not topic:
            return
        digest = hashlib.sha256(" ".join(topic.split()).casefold().encode("utf-8")).hexdigest()
        with self._queue() as db:
            db.execute("UPDATE learning_queue SET status='pending',updated_at=? "
                       "WHERE topic_hash=? AND status='learning'",
                       (datetime.now(UTC).isoformat(), digest))

    # --------------------------------------------------------------- the loop

    def start(self, topics: Callable[[], str] | None = None, interval: float = 300.0) -> bool:
        """Begin the background loop. Returns False if it is already running."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._wake.clear()
            self.stopped_because = None
            self._thread = threading.Thread(
                target=self._loop, args=(topics, interval), name="ai-continuous-learner", daemon=True,
            )
            self._thread.start()
        return True

    def _loop(self, topics: Callable[[], str] | None, interval: float) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                self.last_heartbeat = time.time()
                try:
                    allowed, reason = self.may_run()
                    if not allowed:
                        breach = self.budget_breach()
                        if self.halt_file_present() or (breach and not self.standby_after_budget):
                            self.stopped_because = reason
                            self._journal(CycleResult(action="stopped", ok=False, detail=reason))
                            return
                        self.waiting_because = reason
                        self.worker_state = "waiting_budget" if breach else "paused" if self.paused else "waiting_resources"
                        # 24/7 standby never resets spending or overrides a
                        # stop. It waits for the existing ledger's natural
                        # hour/day rollover, then checks the same gates again.
                        self._wait(60.0 if breach else min(max(.05, interval), max(1.0, self.budget.idle_retry_seconds)))
                        continue
                    self.waiting_because = ""
                    self._maintain_if_due()
                    self.worker_state = "checking_queue"
                    topic = (topics() if topics else self.next_topic()) or ""
                    if not topic:
                        self.worker_state = "idle"
                        self._wait(max(.05, interval))
                        continue
                    self.worker_state = "running"
                    self.run_cycle(topic)
                    failures = 0
                    self.worker_state = "idle"
                    self._wait(max(.05, interval))
                except Exception as exc:
                    # Queue/status/storage failures previously killed the
                    # daemon outside run_cycle's exception boundary.
                    from app.logging import redact
                    failures += 1
                    self.worker_state = "error_backoff"
                    self.waiting_because = redact(f"{type(exc).__name__}: {exc}")
                    self._journal(CycleResult(action="error", ok=False, detail=self.waiting_because))
                    self._wait(min(300.0, 5.0 * (2 ** min(failures - 1, 6))))
        finally:
            self.worker_state = "stopped"

    def _maintain_if_due(self) -> None:
        """One local maintenance review per interval, after admission gates."""
        control = getattr(self.runtime, "metacognition", None)
        if control is None or time.monotonic() < self._next_maintenance:
            return
        self._next_maintenance = time.monotonic() + self.maintenance_interval_seconds
        self.worker_state = "maintenance"
        try:
            result = control.review_upgrades(apply_small=True)
            applied = result.get("applied", [])
            self.last_maintenance = {
                "at": datetime.now(UTC).isoformat(),
                "proposals": len(result.get("proposals", [])),
                "applied": sum(bool(item.get("result", {}).get("applied")) for item in applied),
                "needs_review": len(result.get("needs_owner_review", [])),
            }
        except Exception as exc:
            self.last_maintenance = {"at": datetime.now(UTC).isoformat(), "error": type(exc).__name__}
            log_swallowed(_log, "continuous learning maintenance")

    def _wait(self, seconds: float) -> None:
        """Interruptible wait: a new safe topic wakes the idle learner."""
        self._wake.wait(max(0.0, float(seconds)))
        self._wake.clear()

    def stop(self, timeout: float = 30.0) -> bool:
        self.request_stop("stop() called")
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -------------------------------------------------------------- reporting

    def _journal(self, result: CycleResult) -> None:
        """Append-only record. Written per cycle so a crash loses at most one."""
        try:
            path = self.root / f"journal-{date.today().isoformat()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                from app.logging import redact
                handle.write(json.dumps(redact(result.to_dict()), default=str) + "\n")
        except Exception:
            log_swallowed(_log, "writing the learning journal", level=logging.WARNING)

    def daily_report(self, day: str | None = None) -> dict[str, Any]:
        """What it did, for a person to read. The point of an unattended loop
        is that it can be reviewed afterwards."""
        target = day or date.today().isoformat()
        entries: list[dict[str, Any]] = []
        path = self.root / f"journal-{target}.jsonl"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
        actions: dict[str, int] = {}
        for entry in entries:
            actions[entry.get("action", "?")] = actions.get(entry.get("action", "?"), 0) + 1
        return {
            "day": target,
            "cycles": len(entries),
            "succeeded": sum(1 for item in entries if item.get("ok")),
            "failed": sum(1 for item in entries if not item.get("ok")),
            "actions": actions,
            "requests": sum(int(item.get("requests", 0)) for item in entries),
            "bytes_written": sum(int(item.get("bytes_written", 0)) for item in entries),
            "topics": sorted({item.get("topic", "") for item in entries if item.get("topic")})[:25],
            "running": self.running,
            "stopped_because": self.stopped_because,
            "budget": self.budget.to_dict(),
            "spent": self.ledger.to_dict(),
        }

    def status(self) -> dict[str, Any]:
        allowed, reason = self.may_run()
        return {
            "running": self.running, "may_run": allowed, "reason": reason,
            "paused": self.paused, "paused_because": self._paused_because,
            "halted_by_file": self.halt_file_present(),
            "stop_file": str(self.stop_file),
            "stopped_because": self.stopped_because,
            "budget": self.budget.to_dict(), "spent": self.ledger.to_dict(),
            "permitted_actions": sorted(PERMITTED_ACTIONS),
            "principal": "BACKGROUND_LEARNER",
            "worker_state": self.worker_state, "waiting_because": self.waiting_because,
            "standby_after_budget": self.standby_after_budget,
            "last_heartbeat": self.last_heartbeat,
            "maintenance": dict(self.last_maintenance),
            "research_level": self.research_level,
            "anticipate_followups": self.anticipate_followups,
            "anticipated_per_request": self.anticipated_per_request,
            "idle_reflection": self.idle_reflection,
            "reflection_candidates": self.reflection_candidates,
            "queue": self.queue_status(10),
        }

    def _load_ledger(self) -> LearningLedger:
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            fields = LearningLedger.__dataclass_fields__
            ledger = LearningLedger(**{key: value for key, value in data.items() if key in fields and key != "cycle_times"})
            ledger.cycle_times = [float(value) for value in data.get("cycle_times", [])[-100:]]
            ledger.roll()
            return ledger
        except (OSError, ValueError, TypeError):
            return LearningLedger()

    def _save_ledger(self) -> None:
        temporary = self.ledger_path.with_suffix(".tmp")
        data = self.ledger.to_dict() | {"cycle_times": self.ledger.cycle_times[-100:]}
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.ledger_path)


def _sqlite_footprint(path: Path) -> int:
    """Bytes SQLite currently occupies, including live WAL/SHM sidecars."""
    total = 0
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            total += candidate.stat().st_size
        except OSError:
            pass
    return total


def _windows_idle_seconds() -> float:
    """Seconds since physical keyboard/mouse input; infinite off Windows."""
    if not hasattr(ctypes, "windll"):
        return float("inf")

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    try:
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception:
        return 0.0


def _windows_cpu_percent() -> float | None:
    """Read current CPU load without adding a resident monitoring package."""
    if not hasattr(ctypes, "windll"):
        return None
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Processor | Measure-Object LoadPercentage -Average).Average"],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return float(completed.stdout.strip()) if completed.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


#: Talking *to* HAYAT rather than asking about the world. "are u hayat or
#: qwen?", "great but why wont u?", "work on your tone" -- none of these is a
#: research topic, and all of them were sitting in the queue waiting to be
#: typed into a public search engine.
_ADDRESSED_TO_HAYAT = re.compile(
    r"(?i)\b(?:you|your|yours|u|ur|hayat|yourself)\b|\bare\s+u\b|\bwont\s+u\b"
    # "how do feel about it": the "you" dropped in typing, still a remark to HAYAT.
    r"|\bhow\s+do\s+feel\b"
)

#: The wrappers the learner and the adaptive queue put around a topic. A
#: topic is judged by what is inside them.
_TOPIC_WRAPPER = re.compile(
    r"(?i)^(?:refresh and verify with current primary evidence|refresh with current primary sources|"
    r"resolve with current primary evidence which claim is supported|resolve conflicting evidence|"
    r"look this up)\s*:\s*"
)


def unresearchable_reason(query: str, is_fragment: Callable[[str], bool] | None = None) -> str | None:
    """Why a stored query is not a topic worth researching again, or None.

    Stored research queries include whole pasted prompts, remarks made to
    HAYAT, and fragments cut out of long instructions ("knowledge and
    maximally precise"). Refreshing those sends them to a public search
    engine and brings them back every time they go stale.
    """
    core = " ".join(str(query or "").split())
    while (stripped := _TOPIC_WRAPPER.sub("", core)) != core:
        core = stripped
    if len(core) > 360:
        return "a pasted prompt, not a topic"
    template = re.match(
        r"(?i)^what are the (?:common failure modes and diagnostic signals|best practices, limitations, "
        r"and trade-offs) for (?P<subject>.+?)\??$", core)
    if template and _NO_DEEPER_ANGLE.search(template.group("subject")):
        return "a follow-up template wrapped around a lookup, not a topic"
    angle = _FOLLOW_UP_SUBJECT.match(core)
    if angle and _NO_DEEPER_ANGLE.search(next(group for group in angle.groups() if group)):
        return "a follow-up angle on a command or a changing fact, not a topic"
    if not _is_about_the_world(core):
        return "not a question about the world"
    if not public_learning_topic(core):
        return "private, personal or a local command; never researched unattended"
    if is_fragment is not None:
        try:
            if is_fragment(core):
                return "a fragment cut out of a longer message"
        except Exception:
            log_swallowed(_log, "fragment check for a learning topic")
    return None

#: About Mohsin himself. The queue held "how do my face look rate me score me"
#: and "how can style myself to groom and look better" -- his own appearance,
#: bound for DuckDuckGo. Nothing about his body, health, money, relationships
#: or documents belongs in an unattended public search.
_ABOUT_HIM = re.compile(
    r"(?i)\b(?:my|mine|myself|me|i'?m|i\s+am|i\s+look|i\s+feel|i\s+have)\b.{0,40}"
    r"\b(?:face|body|hair|skin|weight|look|looks|health|salary|money|bank|"
    r"family|wife|girlfriend|sister|brother|mother|father|friend|photo|"
    r"picture|selfie|age|name|address|phone|resume|cv)\b"
    r"|\b(?:rate|score|judge)\s+(?:me|my)\b"
    r"|\bhow\s+do\s+i\s+look\b"
    r"|\b(?:groom|style)\s+myself\b"
    # His own self-improvement is his life, not a public research topic.
    # "what would make me better" went to a search engine and came back with
    # life-coaching articles -- "striving for something can make you a happier
    # and better person" -- which then entered the knowledge store as claims.
    r"|\b(?:make|makes|making)\s+(?:me|myself)\s+(?:a\s+)?(?:better|happier|smarter|"
    r"stronger|healthier|more\b)"
    r"|\b(?:improve|better|fix|develop|change|work\s+on)\s+(?:me|myself)\b"
    r"|\b(?:about|with)\s+myself\b"
    # Only when the thing being improved is him. "how can i improve query
    # performance in postgres" is a technical question and must still be
    # researched, so the object matters, not the "how can i" opening.
    r"|\bhow\s+(?:can|do|should|could)\s+i\s+(?:be|become|get|feel)\s+"
    r"(?:a\s+|more\s+)?(?:better|happier|smarter|stronger|healthier|fitter|confident)\b"
)

#: Enough substance to be worth reading about. A three-word fragment is not a
#: topic, it is the middle of a conversation.
_MINIMUM_TOPIC_WORDS = 4

#: Words that carry no subject. "how is everything going" is four words and
#: every one of them is on this list -- a pleasantry, not something to search
#: for. A topic needs at least two words that are actually about something.
_EMPTY_WORDS = frozenset("""
a an the is are was were be been being am do does did doing done have has had
this that these those it its there here what which who whom whose when where
why how all any both each few more most other some such no nor not only own
same so than too very can will just should now and or but if then else of to
in on at by for with about into over after before again once ok okay okey
everything something anything nothing going go goes went get got getting
thing things stuff really quite pretty much lot lots well good bad fine great
yeah yes yep nope hmm please thanks thank hello hey
""".split())
_MINIMUM_CONTENT_WORDS = 2


#: Angles worth taking on a subject Mohsin keeps returning to. Deliberately
#: about depth rather than breadth: he asked about a thing, so the useful
#: follow-ups are how it works and where it goes wrong, not adjacent trivia.
_FOLLOW_UP_ANGLES = (
    "how {subject} actually works, mechanism and internals",
    "common mistakes and failure modes with {subject}",
    "{subject} best practices and trade-offs",
)


#: The subject inside one of the angles above, so stored ones can be judged again.
_FOLLOW_UP_SUBJECT = re.compile(
    r"(?i)^(?:how (.+) actually works, mechanism and internals"
    r"|common mistakes and failure modes with (.+)"
    r"|(.+) best practices and trade-offs)$"
)


def interests(memory: Any, limit: int = 6, lookback: int = 200) -> list[tuple[str, int]]:
    """Subjects Mohsin keeps coming back to, most-asked first.

    Read from what he actually typed rather than from anything he was asked to
    configure. A subject he has raised four times is one worth knowing more
    about than whatever the learner would otherwise have picked on its own.
    """
    try:
        with memory.connect() as db:
            rows = db.execute(
                "SELECT content FROM conversations WHERE role = 'user' "
                "ORDER BY id DESC LIMIT ?", (max(1, lookback),)).fetchall()
    except Exception:
        return []

    counts: dict[str, int] = {}
    for row in rows:
        value = " ".join(str(row[0] or "").split())
        if not _is_about_the_world(value):
            continue
        words = [word for word in re.findall(r"[a-z][a-z0-9+#.-]{2,}", value.lower())
                 if word not in _EMPTY_WORDS and word not in _ASKING_NOISE]
        # Pairs, because a subject is almost never one word. "tax return" and
        # "rust ownership" are things to study; "return" and "people" are not,
        # and single words produced follow-ups like "how people actually
        # works" -- which is neither a subject nor a sentence.
        for first, second in zip(words, words[1:]):
            if len(first) > 2 and len(second) > 2:
                counts[f"{first} {second}"] = counts.get(f"{first} {second}", 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(phrase, count) for phrase, count in ranked if count >= 2][:limit]


#: Words that show up in questions of every kind and identify no subject.
_ASKING_NOISE = frozenset({
    "explain", "detail", "detailed", "understand", "learn", "tell", "show",
    "make", "want", "need", "know", "think", "work", "working", "works",
    "thing", "things", "something", "anything", "everything", "please",
    "should", "could", "would", "about", "because", "which", "there",
    "where", "when", "what", "why", "how", "does", "doing", "done", "with",
    "from", "into", "that", "this", "then", "than", "they", "them", "your",
    "hayat", "mohsin",
})


def follow_up_topics(memory: Any, limit: int = 3) -> list[str]:
    """Research topics that follow from what he has been asking about.

    The learner used to queue only his literal questions, so it never went
    deeper on anything -- it answered what was asked and then had nothing of
    its own to pursue. These are the next questions about subjects he has
    raised more than once, which is what "relatable to what I have asked"
    means in practice.
    """
    # "brief today" is a command HAYAT runs, and "today" is a changing fact: the
    # learner studied "how brief today actually works" from articles about
    # Google's Daily Brief, again and again. Such subjects have no deeper angle.
    found = [(subject, count) for subject, count in interests(memory, limit=limit * 2)
             if not _NO_DEEPER_ANGLE.search(subject)][:limit]
    topics: list[str] = []
    for index, (subject, _count) in enumerate(found):
        angle = _FOLLOW_UP_ANGLES[index % len(_FOLLOW_UP_ANGLES)]
        topics.append(angle.format(subject=subject))
    return topics


#: Requests the follow-up templates do not fit. The queue held "What are the
#: common failure modes and diagnostic signals for latest stable version
#: python?", "... for find tutorials rust async?" and "... for MONEY REACH
#: BILLIONS DOLLAR STARTING?" -- each researched on the web and stored.
_NO_DEEPER_ANGLE = re.compile(
    r"(?i)"
    # A changing fact: the answer is the fact, there is no mechanism to study.
    r"\b(?:latest|newest|current(?:ly)?|today'?s?|tonight|this\s+week|price|prices|cost\s+of|"
    r"version|release(?:d|\s+date)?|news|weather|forecast|score|stock)\b"
    # A request to fetch material, not a subject.
    r"|^(?:please\s+)?(?:find|get|show|give|fetch|search(?:\s+for)?|look\s+up|download|list|recommend)"
    r"(?:\s+me)?\b"
    # A personal goal. "How can I improve query performance" is still a
    # question about postgres; "how can I make money" is about his life.
    r"|^(?:so\s+)?how\s+(?:can|do|should|could|would)\s+i\s+"
    r"(?:make|earn|get|become|reach|start|grow|save|afford|find)\b"
)


#: Follow-up predictions are built from a request's own words, so only a
#: well-formed question gives them a real subject. "great and all about money
#: and stuff that are required and all elite things to learn" produced "What
#: are the common failure modes and diagnostic signals for money required
#: elite?", which was researched and stored claims about a video game.
_WELL_FORMED_QUESTION = re.compile(
    r"(?i)^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?:how|why|what|which|when|where|who|explain|describe|compare|is|are|does|do)\b"
)


def anticipated_questions(text: str, limit: int = 2) -> list[str]:
    """Deterministic likely next questions for one safe public-world topic.

    Predictions are queued as research candidates, never presented as the
    user's words. They deliberately cover a different depth angle than the
    literal request so background time prepares useful follow-up knowledge.
    """
    value = " ".join(str(text or "").split()).strip()
    if limit <= 0 or not _is_about_the_world(value):
        return []
    if _NO_DEEPER_ANGLE.search(value):
        return []
    if re.search(
        r"(?i)(?:\b[A-Z]:\\|\\\\|file://|(?:^|\s)/(?:users|home|etc|var)/|"
        r"\bmy\s+(?:account|email|file|folder|project|repository|code|document|company|client|customer)\b|"
        r"\bthis\s+(?:private|internal|local|customer|client)\b)", value,
    ):
        return []
    words: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", value):
        folded = word.casefold()
        if folded in _EMPTY_WORDS or folded in _ASKING_NOISE:
            continue
        if folded in {
            "common", "failure", "failures", "mode", "modes", "best", "practices",
            "tradeoff", "tradeoffs", "internals", "mechanism", "diagnostic", "signals",
            # Pronouns are not a subject. Leaving them in built the topic
            # "What are the best practices, limitations, and trade-offs for me
            # better?" out of "what would make me better", and queued it.
            "me", "my", "myself", "mine", "you", "your", "yours", "yourself",
            "i", "we", "us", "our", "it", "its", "they", "them", "their",
        }:
            continue
        if folded not in {item.casefold() for item in words}:
            words.append(word)
        if len(words) >= 7:
            break
    if len(words) < 2:
        return []
    subject = " ".join(words)
    candidates = [
        f"What are the common failure modes and diagnostic signals for {subject}?",
        f"What are the best practices, limitations, and trade-offs for {subject}?",
        f"How does {subject} work internally and what mechanisms matter most?",
    ]
    return candidates[:max(0, min(int(limit), 3))]


def conflict_resolution_topic(conflict: dict[str, Any]) -> str | None:
    """The research question that settles one stored claim conflict."""
    left = " ".join(str(conflict.get("claim_text") or "").split())[:180]
    right = " ".join(str(conflict.get("conflicting_text") or "").split())[:180]
    if not (left and right):
        return None
    return f"Resolve with current primary evidence which claim is supported: {left} versus {right}"


def reflection_topics(runtime: Any, limit: int = 2) -> list[tuple[str, str, int]]:
    """Bounded, model-free questions derived from real evidence gaps."""
    if runtime is None or limit <= 0:
        return []
    knowledge = getattr(getattr(runtime, "research", None), "knowledge", None)
    if knowledge is None:
        return []
    candidates: list[tuple[str, str, int]] = []
    try:
        for conflict in knowledge.conflicts(unresolved_only=True, limit=limit):
            if topic := conflict_resolution_topic(conflict):
                candidates.append((topic, "reflection_conflict", 65))
    except Exception:
        log_swallowed(_log, "reading knowledge conflicts for reflection")
    if len(candidates) < limit:
        try:
            for entry in knowledge.stale_entries(limit=limit - len(candidates)):
                query = " ".join(str(entry.get("query") or "").split())[:360]
                if query:
                    candidates.append((
                        f"Refresh and verify with current primary evidence: {query}",
                        "reflection_stale", 30,
                    ))
        except Exception:
            log_swallowed(_log, "reading stale knowledge for reflection")
    return candidates[:max(0, min(int(limit), 5))]


#: An instruction to this computer. A failed "read config/settings.yaml" was
#: queued as an unfinished task and researched on the web, which filed
#: nineteen claims about Kubernetes configuration under that "topic".
_LOCAL_COMMAND = re.compile(
    r"(?i)^(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?:open|launch|start|close|quit|exit|type|click|press|scroll|list|read|write|create|make|delete|remove|"
    r"move|copy|rename|run|execute|install|uninstall|save|take\s+a\s+screenshot|screenshot|show\s+me\s+the\s+files)\b"
)


def _is_local_command(value: str) -> bool:
    """Whether this is something to do on this machine, not a topic to study."""
    from app.intent.local_files import LOCAL_FILE, local_file_request

    return bool(_LOCAL_COMMAND.match(value) or LOCAL_FILE.search(value) or local_file_request(value))


def public_learning_topic(text: str) -> bool:
    """Validate every queue producer, including stored failures and refreshes."""
    from app.safety.sensitive import redact_text

    value = " ".join(str(text or "").split())
    _, findings = redact_text(value)
    if findings or not 8 <= len(value) <= 500:
        return False
    if re.search(
        r"(?i)(?:\b[A-Z]:\\|\\\\|file://|(?:^|\s)/(?:users|home|etc|var)/|"
        r"\b(?:localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|"
        r"172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b|"
        r"\bmy\s+(?:account|email|file|folder|project|repository|code|document|company|client|customer)\b|"
        r"\bthis\s+(?:private|internal|local|customer|client)\b)", value,
    ):
        return False
    return not (_ADDRESSED_TO_HAYAT.search(value) or _ABOUT_HIM.search(value) or _PERSON_LOOKUP.search(value)
                or _is_local_command(value))


#: Finding a person, not learning about the world. The queue held "find current
#: location of <an email address>", researched on the web three times in an hour.
#: Locating or profiling someone is never unattended background work.
_PERSON_LOOKUP = re.compile(
    r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}"
    r"|(?<!\d)\+?\d[\d\s().-]{8,}\d(?!\d)"
    r"|\b(?:current\s+)?(?:location|whereabouts|home\s+address|address)\s+of\b"
    r"|\bwhere\s+(?:does|did)\s+(?!the\b|a\b|an\b)\w+(?:\s+\w+){0,2}\s+(?:live|stay|sleep)\b"
    r"|\b(?:track|trace|locate|find)\s+(?:a\s+|this\s+|that\s+)?(?:person|someone|somebody|people|him|her)\b"
)


def _is_about_the_world(text: str) -> bool:
    """Whether this is a question about the world rather than about us.

    The background learner types its topics into a public search engine, so
    the only things that belong in its queue are things Mohsin would not mind
    a stranger reading. That rules out three kinds of message that were all
    sitting in it: remarks addressed to HAYAT, questions about his own body
    and life, and fragments that carry no question at all.
    """
    value = " ".join(str(text or "").split())
    # These are follow-ups to the current chat, not standalone public search
    # topics. Enqueueing them made the learner spend network/model time on a
    # query with no subject (observed: "explain me about that").
    if re.fullmatch(
        r"(?i)(?:(?:ok|so|and)\s+)?(?:explain\s+(?:me\s+)?(?:about\s+)?(?:that|this|it)|"
        r"tell\s+me\s+more\s+about\s+(?:that|this|it)|what\s+about\s+(?:that|this|it))\s*[.?!]*",
        value,
    ):
        return False
    if len(value.split()) < _MINIMUM_TOPIC_WORDS:
        return False
    if _ADDRESSED_TO_HAYAT.search(value):
        return False
    if _is_local_command(value):
        return False
    if _ABOUT_HIM.search(value):
        return False
    content = [word for word in re.findall(r"[a-z0-9][a-z0-9'-]*", value.lower())
               if word not in _EMPTY_WORDS]
    if len(content) < _MINIMUM_CONTENT_WORDS:
        return False
    return _worth_researching(value)


def _worth_researching(value: str) -> bool:
    """Whether this is a topic to study or a question already answerable.

    Learning and research are not the same job. Research goes to the web
    because the answer is not already known; learning is for subjects worth
    getting deeper on. Nothing enforced the difference, so the learner spent
    a real network round trip and a knowledge entry on "what is 2 plus 2" --
    which it found at 0.58 confidence, from sources, having known the answer
    the whole time.

    Two things disqualify a topic: it is arithmetic, and it is the kind of
    quick factual question the model answers instantly from what it already
    has.
    """
    if re.search(r"^\s*[\d\s+\-*/×÷=().]+\s*$", value):
        return False
    if re.search(r"(?i)\b\d+\s*(?:plus|minus|times|divided by|[+\-*/×÷])\s*\d+", value):
        return False
    try:
        from app.answers import Depth, detect_depth

        return detect_depth(value) is not Depth.QUICK
    except Exception:
        return True
