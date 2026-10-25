"""HAYAT noticing what it has learned, and needing the model less for it.

Mohsin asked for something that sounds impossible and is not, once it is said
precisely. An assistant cannot learn its way out of needing a language model:
the reasoning *is* the model. But it can stop needing the model *for the
things it has already worked out*, and that is most of what it does.

The machinery for this was already here and nothing ever used it.
`SkillPromoter.propose_from_task` turns a completed task's real actions into a
replayable procedure, and `SkillRegistry` loads procedures as tools. Both
waited for someone to type `/skill propose`. So HAYAT solved the same problem
with a fresh planning round every single time, paying full inference for an
answer it had already found.

This closes that loop. When the same goal has been achieved the same way more
than once, the sequence is written down. Next time the goal arrives, the
recorded steps run directly -- no planner, no model, no tokens. What is left
for the model is the genuinely new.

Two rules keep it honest. A procedure is only proposed from tasks that
actually succeeded and whose steps agreed, because learning a lucky accident
teaches the accident. And a learned procedure is *proposed*, not trusted: it
becomes automatic only when Mohsin promotes it, because a wrong step repeated
without thinking is worse than a slow correct one.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: How many times the same goal must be solved the same way before it is
#: worth writing down. Two is coincidence; three is a habit.
REPETITIONS_BEFORE_LEARNING = 3

#: Goals shorter than this carry no reusable shape ("hi", "thanks").
MINIMUM_GOAL_WORDS = 3

_NOISE = re.compile(r"[^a-z0-9 ]+")
_FILLER = {"the", "a", "an", "my", "please", "for", "me", "to", "of", "in", "on", "and"}


def goal_signature(goal: str) -> str:
    """A stable key for "the same kind of request".

    Deliberately crude: lowercase, strip punctuation and filler, sort the
    remaining words. "open notepad and write a note" and "write a note and
    open Notepad" are the same job, and a signature that disagreed would never
    accumulate enough repetitions to learn anything.
    """
    words = _NOISE.sub(" ", str(goal).lower()).split()
    meaningful = sorted({word for word in words if word not in _FILLER and len(word) > 1})
    return " ".join(meaningful)


def steps_signature(tools: list[str]) -> str:
    """A key for "solved the same way", so a different route is not merged."""
    return hashlib.sha256("|".join(tools).encode()).hexdigest()[:12]


@dataclass
class Lesson:
    """A repeated success that is ready to be written down."""

    signature: str
    goal: str
    tools: list[str] = field(default_factory=list)
    task_ids: list[str] = field(default_factory=list)
    occurrences: int = 0
    learned_at: str | None = None
    skill_name: str | None = None

    @property
    def ready(self) -> bool:
        return self.occurrences >= REPETITIONS_BEFORE_LEARNING and not self.learned_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature, "goal": self.goal[:200], "tools": self.tools,
            "occurrences": self.occurrences, "task_ids": self.task_ids[-5:],
            "learned_at": self.learned_at, "skill_name": self.skill_name,
        }


class Apprenticeship:
    """Watches successful work and turns repetition into procedure."""

    def __init__(self, memory: Any = None, promoter: Any = None, skills: Any = None,
                 events: Any = None) -> None:
        self.memory = memory
        self.promoter = promoter
        self.skills = skills
        self.events = events
        self._lessons: dict[str, Lesson] = {}
        self._lock = threading.RLock()

    # -------------------------------------------------------------- watching

    def observe(self, outcome: Any) -> Lesson | None:
        """Record one finished task. Returns the lesson if it is now ready.

        Only complete tasks count. A failed run teaches the wrong thing, and a
        partially-verified one teaches something that looked right.
        """
        if not getattr(outcome, "complete", False):
            return None
        goal = str(getattr(outcome, "goal", "")).strip()
        if len(goal.split()) < MINIMUM_GOAL_WORDS:
            return None
        tools = [str(event.get("tool")) for event in getattr(outcome, "history", [])
                 if event.get("tool") and event.get("verification", {}).get("passed")]
        if not tools:
            return None

        key = f"{goal_signature(goal)}::{steps_signature(tools)}"
        with self._lock:
            lesson = self._lessons.get(key)
            if lesson is None:
                lesson = Lesson(signature=key, goal=goal, tools=tools)
                self._lessons[key] = lesson
            lesson.occurrences += 1
            lesson.task_ids.append(str(getattr(outcome, "task_id", "")))
            ready = lesson.ready
        if ready:
            self._publish("KNOWLEDGE_STALE", lesson)  # nearest existing event
        return lesson if ready else None

    # -------------------------------------------------------------- learning

    def learn(self, lesson: Lesson) -> dict[str, Any]:
        """Write a repeated success down as a replayable procedure.

        Proposed rather than trusted. A learned procedure that runs without
        thinking is only an improvement while it is right, and nobody has
        checked this one yet.
        """
        if self.promoter is None:
            return {"learned": False, "reason": "no skill promoter is available"}
        if not lesson.task_ids:
            return {"learned": False, "reason": "no task to learn from"}
        name = self._name_for(lesson)
        try:
            path = self.promoter.propose_from_task(lesson.task_ids[-1], name)
        except Exception as exc:
            return {"learned": False, "reason": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            lesson.learned_at = datetime.now(UTC).isoformat()
            lesson.skill_name = name
        return {
            "learned": True, "skill": name, "path": str(path), "trusted": False,
            "occurrences": lesson.occurrences,
            "note": (f"I've done this {lesson.occurrences} times the same way, so I wrote it "
                     f"down as '{name}'. Promote it with /skill promote {name} and I'll stop "
                     "planning it from scratch."),
        }

    def _name_for(self, lesson: Lesson) -> str:
        words = [word for word in goal_signature(lesson.goal).split()][:4]
        return "learned_" + ("_".join(words) or "procedure")

    # ------------------------------------------------------------- replaying

    def known_procedure(self, goal: str) -> dict[str, Any] | None:
        """A trusted procedure that already solves this, if one exists.

        This is the part that reduces dependence on the model: a hit here
        means the answer is already known and no inference is needed.
        """
        if self.skills is None:
            return None
        wanted = goal_signature(goal)
        if not wanted:
            return None
        for name, skill in getattr(self.skills, "skills", {}).items():
            if not skill.get("trusted"):
                continue
            described = goal_signature(f"{name} {skill.get('description', '')}")
            if described and (described == wanted or _covers(described, wanted)):
                return {"skill": name, "definition": skill}
        return None

    # -------------------------------------------------------------- reporting

    def lessons(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in
                    sorted(self._lessons.values(), key=lambda x: -x.occurrences)]

    def status(self) -> dict[str, Any]:
        with self._lock:
            watching = len(self._lessons)
            ready = [item.to_dict() for item in self._lessons.values() if item.ready]
            learned = [item.to_dict() for item in self._lessons.values() if item.learned_at]
        trusted = len([s for s in getattr(self.skills, "skills", {}).values() if s.get("trusted")]) \
            if self.skills else 0
        return {
            "patterns_watched": watching, "ready_to_learn": ready, "learned": learned,
            "trusted_procedures": trusted,
            "repetitions_needed": REPETITIONS_BEFORE_LEARNING,
            "note": ("Each trusted procedure is one thing I no longer need the model for."),
        }

    def report(self) -> str:
        status = self.status()
        lines = [f"I'm watching {status['patterns_watched']} pattern(s) in what you ask me.",
                 f"{status['trusted_procedures']} are trusted procedures I can run without thinking."]
        for item in status["ready_to_learn"]:
            lines.append(f"  ready to write down: {item['goal'][:60]} (done {item['occurrences']}x)")
        for item in status["learned"]:
            lines.append(f"  learned: {item['skill_name']} -- promote it to make it automatic")
        return "\n".join(lines)

    def _publish(self, event_type: str, lesson: Lesson) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(event_type, "apprenticeship", lesson.to_dict())
        except Exception:
            log_swallowed(_log, "publishing an apprenticeship event")


def _covers(described: str, wanted: str) -> bool:
    """Whether a procedure's own words contain everything the goal asks for."""
    described_words, wanted_words = set(described.split()), set(wanted.split())
    return bool(wanted_words) and wanted_words.issubset(described_words)
