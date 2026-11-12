"""How far HAYAT has developed, stage by stage, from what it has measurably done -- never from a neuron count.

The owner asked (twice, 2026-11-09) for development "like a baby's brain": observe, recognise, associate, wonder,
experiment, understand, reason, predict, abstract, judge itself, learn on its own and solve unfamiliar problems. Each of those stages is assessed here
from records HAYAT already keeps, with a stated threshold:

==  ====================  ==========================================================================================
 1  Observation           owner messages it has taken in (conversation memory)
 2  Recognition           topic-routing accuracy on the labelled conversation benchmark (app/evaluation/topic_cases.py)
 3  Association           share of memories linked into the memory network (app/memory/plasticity.py)
 4  Curiosity             open questions it queued for itself and then learned (background learner queue)
 5  Experimentation       tasks where a first approach failed and a different one was found to work (experience.db)
 6  Understanding         graded accuracy on held-out questions through the whole assistant (data/benchmarks/holdout)
 7  Reasoning             accuracy on the held-out reasoning families: arithmetic, units, dates, letters, reading
 8  Prediction            its predictions of the owner's next request, against a no-hindsight habit guess
 9  Abstraction           a recovery that worked across unrelated tasks, or a reviewed procedure reused successfully
10  Metacognition         whether its own confidence levels match how often the owner found its replies right
11  Independent learning  answers it learned before being asked that the owner then did ask
12  Problem-solving       success on tasks unlike any it had done before
==  ====================  ==========================================================================================

A stage counts as reached only when it and every stage before it pass. A store that does not exist yet makes its
stage "not measured" (and not passed), so the report can never claim more than the records show. Snapshots are
kept (``development.db``), so a change in a stage is visible over time instead of asserted.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

REASONING_FAMILIES = ("arithmetic", "units", "dates", "letters", "reading")
SNAPSHOT_INTERVAL = timedelta(hours=1)


@dataclass
class Stage:
    number: int
    name: str
    measure: str
    value: float | None
    threshold: float
    passed: bool
    detail: str
    source: str
    next_step: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rate(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _stage(number: int, name: str, measure: str, value: float | None, threshold: float, detail: str, source: str,
           next_step: str, *, extra_ok: bool = True) -> Stage:
    passed = value is not None and value >= threshold and extra_ok
    return Stage(number, name, measure, value, threshold, passed, detail, source, next_step)


def _unmeasured(number: int, name: str, measure: str, threshold: float, source: str, why: str, next_step: str) -> Stage:
    return Stage(number, name, measure, None, threshold, False, f"Not measured yet: {why}", source, next_step)


def _latest_holdout(data_root: Path) -> dict[str, Any] | None:
    folder = data_root / "benchmarks" / "holdout"
    files = sorted(folder.glob("pipeline-*.json"), key=lambda path: path.stat().st_mtime) if folder.is_dir() else []
    for path in reversed(files):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        summary = data.get("summary") or {}
        if int(summary.get("completed_cases") or 0) > 0:
            return {"file": path.name, "finished_at": data.get("finished_at"), **summary}
    return None


class CognitiveDevelopment:
    """Assess the ten stages from real stores; keep snapshots so progress is a measured history."""

    def __init__(self, runtime: Callable[[], Any], path: Path | str) -> None:
        self.runtime = runtime
        self.path = Path(path)
        self._lock = threading.Lock()

    # ---- the stages

    def _observation(self, rt: Any) -> Stage:
        memory = getattr(rt, "memory", None)
        if memory is None:
            return _unmeasured(1, "Observation", "owner messages taken in", 100, "conversation memory",
                               "no memory database", "Talk to HAYAT.")
        with memory.connect() as db:
            count = int(db.execute("SELECT COUNT(*) FROM conversations WHERE role='user'").fetchone()[0])
        return _stage(1, "Observation", "owner messages taken in", count, 100, f"{count} messages from you are in "
                      "its conversation memory.", "agent.db conversations", "More conversation.")

    def _recognition(self, rt: Any) -> Stage:
        from app.evaluation.topic_cases import run_benchmark

        result = run_benchmark()
        topics = getattr(rt, "topics", None)
        flagged = topics.stats() if topics is not None else {}
        detail = (f"{result['relation_correct']} of {result['relation_cases']} labelled messages placed in the right "
                  f"relation (continue / new topic / return / correction ...), against {result['baseline']['relation_accuracy']:.0%} "
                  "for reading every message as one thread.")
        if flagged.get("decisions"):
            detail += (f" In your own chats: {flagged['decisions']} messages placed, {flagged['flagged_wrong']} that "
                       "you marked as wrongly separated or joined.")
        owner_ok = flagged.get("owner_checked_accuracy")
        return _stage(2, "Recognition", "topic-routing accuracy (labelled benchmark)", result["relation_accuracy"], 0.85,
                      detail, "app/evaluation/topic_cases.py + topics.db",
                      "Mark wrongly split or joined messages in the chat; each becomes a new benchmark case.",
                      extra_ok=owner_ok is None or flagged.get("decisions", 0) < 20 or owner_ok >= 0.85)

    def _association(self, rt: Any) -> Stage:
        network = getattr(rt, "memory_network", None)
        if network is None:
            return _unmeasured(3, "Association", "memories linked into the network", 0.5, "memory_links",
                               "no memory network", "Start HAYAT normally so memories are consolidated.")
        status = network.status()
        memories = int(status.get("memories") or 0)
        alone = int(status.get("standing_alone") or 0)
        share = _rate(memories - alone, memories)
        return _stage(3, "Association", "memories linked into the network", share, 0.5,
                      f"{memories - alone} of {memories} memories are linked; {status.get('links', 0)} links "
                      f"({status.get('kinds', 'none')}). Recalls that helped: {status.get('helped', 0)}, "
                      f"did not: {status.get('did_not_help', 0)}.", "agent.db memory_links",
                      "Consolidation links episodes by purpose at start-up; owner verdicts strengthen links.")

    def _learner_counts(self, rt: Any) -> dict[str, Any] | None:
        learner = getattr(rt, "learner", None)
        path = getattr(learner, "queue_path", None)
        if path is None or not Path(path).is_file():
            return None
        with closing(sqlite3.connect(path, timeout=5)) as db:
            by_status = {row[0]: int(row[1]) for row in db.execute("SELECT status, COUNT(*) FROM learning_queue GROUP BY status")}
            own = {row[0]: int(row[1]) for row in db.execute(
                "SELECT status, COUNT(*) FROM learning_queue WHERE source IN ('open_question','anticipated_follow_up',"
                "'idle_reflection','knowledge_gap','curiosity') GROUP BY status")}
            matched, mismatched = db.execute("SELECT COALESCE(SUM(match_count),0), COALESCE(SUM(mismatch_count),0) "
                                             "FROM learning_queue WHERE source='anticipated_follow_up'").fetchone()
        return {"by_status": by_status, "own": own, "matched": int(matched), "mismatched": int(mismatched)}

    def _curiosity(self, counts: dict[str, Any] | None) -> Stage:
        if counts is None:
            return _unmeasured(4, "Curiosity", "own questions learned", 5, "learning.db", "the background learner "
                               "has no queue yet", "Let the background learner run while HAYAT is idle.")
        asked = sum(counts["own"].values())
        learned = counts["own"].get("learned", 0)
        return _stage(4, "Curiosity", "own questions learned", learned, 5,
                      f"{asked} questions it raised itself (open questions, likely follow-ups, gaps); {learned} learned, "
                      f"{counts['own'].get('failed', 0)} failed.", "learning.db learning_queue",
                      "Unanswered research questions are queued automatically; idle time lets them be learned.",
                      extra_ok=asked >= 10)

    def _experimentation(self, rt: Any) -> Stage:
        experience = getattr(rt, "experience", None)
        if experience is None:
            return _unmeasured(5, "Experimentation", "tasks recovered by a different approach", 3, "experience.db",
                               "no experience memory", "Give HAYAT tasks that use tools.")
        stats = experience.stats()
        total, fixed = int(stats.get("experiences") or 0), int(stats.get("recovered_with_a_fix") or 0)
        return _stage(5, "Experimentation", "tasks recovered by a different approach", fixed, 3,
                      f"{total} tool tasks recorded, {stats.get('succeeded', 0)} succeeded; in {fixed} a first approach "
                      "failed and a different one was found that worked.", "experience.db experiences",
                      "Each recovered failure becomes a lesson the planner reads before the next similar task.",
                      extra_ok=total >= 30)

    def _understanding(self, holdout: dict[str, Any] | None) -> Stage:
        if holdout is None:
            return _unmeasured(6, "Understanding", "held-out accuracy (whole assistant)", 0.85,
                               "data/benchmarks/holdout", "no held-out run on record",
                               "Run the held-out evaluation (docs/evaluation.md).")
        completed = int(holdout.get("completed_cases") or 0)
        # Unanswered cases count as wrong: the denominator is every case in the run, not only the answered ones.
        cases = max(completed, int(holdout.get("cases") or 0))
        accuracy = _rate(int(holdout.get("correct") or 0), cases)
        interval = holdout.get("interval_95") or []
        return _stage(6, "Understanding", "held-out accuracy (whole assistant)", accuracy, 0.85,
                      f"{holdout.get('correct')} of {cases} held-out questions right ({cases - completed} unanswered)"
                      + (f" (95% interval {interval[0]:.0%}–{interval[1]:.0%})" if len(interval) == 2 else "")
                      + f", graded by code, run {str(holdout.get('finished_at') or '')[:10]} ({holdout['file']}).",
                      "data/benchmarks/holdout", "A complete (not partial) run gives a firmer figure.",
                      extra_ok=completed >= 20)

    def _reasoning(self, holdout: dict[str, Any] | None) -> Stage:
        families = (holdout or {}).get("families") or {}
        rows = [families[name] for name in REASONING_FAMILIES if name in families]
        if not rows:
            return _unmeasured(7, "Reasoning", "accuracy on reasoning families", 0.85, "data/benchmarks/holdout",
                               "no reasoning families on record", "Run the held-out evaluation.")
        cases = sum(int(row.get("cases") or 0) for row in rows)
        correct = sum(int(row.get("correct") or 0) for row in rows)
        weak = [name for name, row in families.items() if float(row.get("accuracy") or 0) < 0.67]
        bias = (holdout or {}).get("bias_pairs") or {}
        detail = f"{correct} of {cases} right across {', '.join(n for n in REASONING_FAMILIES if n in families)}."
        if weak:
            detail += f" Weak families: {', '.join(weak)}."
        if bias.get("pairs"):
            detail += f" Same question, reworded to lean one way: consistent in {bias.get('consistent', 0)} of {bias['pairs']} pairs."
        return _stage(7, "Reasoning", "accuracy on reasoning families", _rate(correct, cases), 0.85, detail,
                      "data/benchmarks/holdout", "Fix the weak families first; they are listed.")

    @staticmethod
    def _prediction_record(path: Path) -> dict[str, Any] | None:
        """Hit rate of checked predictions, beside the no-skill guess made the same way at the same moments.

        The baseline guesses, before each prediction was checked, the owner's most common kinds of request *so
        far* -- as many as the prediction named, and from the same choices (plain chat is never a guess, for
        either). A first version counted frequencies over the very rows it scored and let the guess include
        "chat": that hindsight made a real +7-point lift look like -19 points (2026-11-09 replay of 419 turns).
        """
        if not path.is_file():
            return None
        from app.situation import _GENERIC

        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT areas, hit, actual FROM situation_predictions WHERE resolved=1 "
                              "ORDER BY id").fetchall()[-500:]
        if not rows:
            return {"checked": 0, "right": 0, "rate": None, "baseline": None}
        seen: dict[str, int] = {}
        guessed = right = width_total = 0
        for areas, hit, actual in rows:
            width = len(json.loads(areas or "[]")) or 1
            width_total += width
            habit = [area for area, _n in sorted(seen.items(), key=lambda kv: -kv[1])[:width]]
            guessed += str(actual) in habit
            right += int(hit or 0)
            if str(actual) not in _GENERIC:
                seen[str(actual)] = seen.get(str(actual), 0) + 1
        return {"checked": len(rows), "right": right, "rate": right / len(rows), "baseline": guessed / len(rows),
                "width": round(width_total / len(rows), 1)}

    def _prediction(self, rt: Any, holdout: dict[str, Any] | None) -> Stage:
        situation = getattr(rt, "situation", None)
        record = self._prediction_record(Path(situation.path)) if situation is not None else None
        if record is None or not record["checked"]:
            return _unmeasured(8, "Prediction", "lift over guessing your usual requests", 0.05, "experience.db",
                               "no checked predictions", "Use HAYAT for a while; predictions are checked automatically.")
        lift = round(record["rate"] - record["baseline"], 3)
        calibration = (holdout or {}).get("calibration") or {}
        detail = (f"{record['right']} of {record['checked']} predictions of your next kind of request were right "
                  f"({record['rate']:.0%}); guessing, at the same moments, your {record['width']:g} most common kinds "
                  f"so far would have been right {record['baseline']:.0%} of the time, so the lift is {lift:+.0%}.")
        if calibration.get("rated"):
            detail += (f" Stated confidence vs results on {calibration['rated']} rated answers: expected calibration "
                       f"error {calibration.get('ece')}, Brier {calibration.get('brier')}.")
        ece = calibration.get("ece")
        return _stage(8, "Prediction", "lift over guessing your usual requests", lift, 0.05, detail,
                      "experience.db situation_predictions",
                      "A prediction that does no better than habit is not foresight; the transitions model must beat it.",
                      extra_ok=record["checked"] >= 30 and (ece is None or float(ece) <= 0.1))

    @staticmethod
    def _experience_rows(rt: Any) -> list[sqlite3.Row] | None:
        experience = getattr(rt, "experience", None)
        path = getattr(experience, "path", None)
        if path is None or not Path(path).is_file():
            return None
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            return db.execute("SELECT goal, words, success, failed_tool, failure_class, fix_tools FROM experiences "
                              "ORDER BY id").fetchall()

    @staticmethod
    def _overlap(first: set[str], second: set[str]) -> float:
        return len(first & second) / len(first | second) if first and second else 0.0

    def _abstraction(self, rt: Any, rows: list[sqlite3.Row] | None) -> Stage:
        if rows is None:
            return _unmeasured(9, "Abstraction", "strategies that carried across unrelated tasks", 2, "experience.db",
                               "no experience memory", "Give HAYAT tasks that use tools.")
        by_strategy: dict[tuple[str, str, str], list[set[str]]] = {}
        for row in rows:
            if row["success"] and row["fix_tools"] not in ("", "[]"):
                key = (row["failed_tool"], row["failure_class"], row["fix_tools"])
                by_strategy.setdefault(key, []).append(set(str(row["words"]).split()))
        general = [key for key, goals in by_strategy.items()
                   if any(self._overlap(a, b) < 0.3 for index, a in enumerate(goals) for b in goals[index + 1:])]
        reused = 0
        memory = getattr(rt, "memory", None)
        if memory is not None:
            try:
                with memory.connect() as db:
                    reused = int(db.execute("SELECT COUNT(*) FROM procedures WHERE trusted=1 AND success_count>=2")
                                 .fetchone()[0])
            except sqlite3.Error:
                reused = 0
        names = "; ".join(f"{tool or 'a step'} {kind or 'failure'} -> {fix}" for tool, kind, fix in general[:3])
        return _stage(9, "Abstraction", "strategies that carried across unrelated tasks", len(general) + reused, 2,
                      f"{len(general)} recoveries worked on tasks with different goals"
                      + (f" ({names})" if names else "") + f"; {reused} reviewed procedures reused successfully.",
                      "experience.db experiences + agent.db procedures",
                      "A fix that works once is an anecdote; the same fix working on an unrelated task is a strategy.")

    def _problem_solving(self, rows: list[sqlite3.Row] | None) -> Stage:
        if rows is None:
            return _unmeasured(12, "Problem-solving", "success on unfamiliar tasks", 0.7, "experience.db",
                               "no experience memory", "Give HAYAT tasks that use tools.")
        seen: list[set[str]] = []
        novel = solved = 0
        for row in rows:
            words = set(str(row["words"]).split())
            if words and all(self._overlap(words, earlier) < 0.2 for earlier in seen):
                novel += 1
                solved += int(row["success"])
            seen.append(words)
        return _stage(12, "Problem-solving", "success on unfamiliar tasks", _rate(solved, novel), 0.7,
                      f"{novel} tasks shared under 20% of their goal words with every earlier task; {solved} of them "
                      "were completed.", "experience.db experiences",
                      "Unfamiliar tasks have no lesson to lean on; there reasoning, not memory, does the work.",
                      extra_ok=novel >= 10)

    def _metacognition(self, rt: Any) -> Stage:
        memory = getattr(rt, "memory", None)
        if memory is None:
            return _unmeasured(10, "Metacognition", "self-review matches your verdicts", 1, "self-review.db",
                               "no memory database", "Tell HAYAT when a reply was right or wrong.")
        from app.self_review import LEVELS, SelfReviewLog

        calibration = SelfReviewLog.beside(memory).calibration()
        judged = sum(int(item["judged"]) for item in calibration.values())
        ordered = [calibration[name] for _floor, name in sorted(LEVELS) if calibration.get(name, {}).get("judged")]
        rates = [item["observed"] for item in ordered]
        monotonic = len(rates) >= 2 and all(later >= earlier - 0.05 for earlier, later in zip(rates, rates[1:]))
        detail = (f"You judged {judged} reviewed replies. "
                  + ("; ".join(f"{name}: {item['right']}/{item['judged']} right" for _f, name in sorted(LEVELS)
                               if (item := calibration.get(name, {})).get("judged")) or "No verdicts yet.")
                  + (" Higher self-confidence went with more replies you found right." if monotonic else
                     " Its confidence does not yet track how often you found it right." if len(rates) >= 2 else ""))
        return _stage(10, "Metacognition", "self-review matches your verdicts", 1.0 if monotonic else 0.0, 1, detail,
                      "self-review.db reviews", "Say \"that's right\" or \"that's wrong\" after replies.",
                      extra_ok=judged >= 20)

    def _independent(self, counts: dict[str, Any] | None) -> Stage:
        if counts is None:
            return _unmeasured(11, "Independent learning", "learned ahead, then asked", 5, "learning.db",
                               "no learner queue", "Let the background learner run while HAYAT is idle.")
        learned = counts["by_status"].get("learned", 0)
        matched = counts["matched"]
        return _stage(11, "Independent learning", "learned ahead, then asked", matched, 5,
                      f"{learned} topics learned in the background; {matched} answers prepared ahead were then asked "
                      f"for, {counts['mismatched']} were not.", "learning.db learning_queue",
                      "Likely follow-ups are prepared after a question; only ones you then ask count.",
                      extra_ok=learned >= 20)

    # ---- the report

    def assess(self) -> dict[str, Any]:
        rt = self.runtime()
        data_root = Path(getattr(getattr(rt, "memory", None), "path", Path("data/memory/agent.db"))).parent.parent
        holdout = None
        try:
            holdout = _latest_holdout(data_root)
        except OSError:
            log_swallowed(_log, "reading the latest held-out evaluation")
        counts = None
        try:
            counts = self._learner_counts(rt)
        except sqlite3.Error:
            log_swallowed(_log, "reading the learner queue for development stages")
        rows = None
        try:
            rows = self._experience_rows(rt)
        except sqlite3.Error:
            log_swallowed(_log, "reading task experience for development stages")
        builders = [lambda: self._observation(rt), lambda: self._recognition(rt), lambda: self._association(rt),
                    lambda: self._curiosity(counts), lambda: self._experimentation(rt), lambda: self._understanding(holdout),
                    lambda: self._reasoning(holdout), lambda: self._prediction(rt, holdout),
                    lambda: self._abstraction(rt, rows), lambda: self._metacognition(rt),
                    lambda: self._independent(counts), lambda: self._problem_solving(rows)]
        names = ["Observation", "Recognition", "Association", "Curiosity", "Experimentation", "Understanding",
                 "Reasoning", "Prediction", "Abstraction", "Metacognition", "Independent learning", "Problem-solving"]
        stages: list[Stage] = []
        for number, (build, name) in enumerate(zip(builders, names), start=1):
            try:
                stages.append(build())
            except Exception as error:  # one broken store must not hide the other nine
                log_swallowed(_log, f"assessing development stage {name}")
                stages.append(Stage(number, name, "", None, 0, False, f"Could not be read ({type(error).__name__}).",
                                    "", "See data/logs/hayat.log."))
        reached = 0
        for stage in stages:
            if not stage.passed:
                break
            reached = stage.number
        passed_anywhere = [stage.number for stage in stages if stage.passed]
        report = {"stage_reached": reached, "stage_name": stages[reached - 1].name if reached else "None yet",
                  "working_on": stages[reached].name if reached < len(stages) else "",
                  "passed": passed_anywhere, "stages": [stage.to_dict() for stage in stages],
                  "assessed_at": datetime.now(UTC).isoformat(timespec="seconds"),
                  "rule": "A stage counts only when it and every earlier stage pass their measured threshold."}
        try:
            report["previous"] = self._snapshot(report)
        except sqlite3.Error:
            log_swallowed(_log, "keeping a development snapshot")
        return report

    def _snapshot(self, report: dict[str, Any]) -> dict[str, Any] | None:
        """Keep one snapshot an hour; return the previous different one, so a change can be shown."""
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                       "stage INTEGER NOT NULL, values_json TEXT NOT NULL)")
            values = {str(stage["number"]): stage["value"] for stage in report["stages"]}
            last = db.execute("SELECT at, stage, values_json FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
            earlier = db.execute("SELECT at, stage, values_json FROM snapshots WHERE values_json != ? ORDER BY id DESC "
                                 "LIMIT 1", (json.dumps(values),)).fetchone()
            due = last is None or datetime.now(UTC) - datetime.fromisoformat(last[0]) >= SNAPSHOT_INTERVAL
            if due:
                db.execute("INSERT INTO snapshots(at, stage, values_json) VALUES(?,?,?)",
                           (report["assessed_at"], report["stage_reached"], json.dumps(values)))
        if earlier is None:
            return None
        return {"at": earlier[0], "stage_reached": earlier[1], "values": json.loads(earlier[2])}

    def status(self) -> dict[str, Any]:
        """The last snapshot kept (no new assessment is run)."""
        if not self.path.is_file():
            return {"assessed": "never"}
        with self._lock, closing(sqlite3.connect(self.path, timeout=5)) as db:
            row = db.execute("SELECT at, stage, values_json FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return {"assessed": "never"}
        return {"assessed_at": row[0], "stage_reached": row[1], "stage_values": json.loads(row[2])}

    def summary(self) -> str:
        report = self.assess()
        lines = [f"Development stage reached: {report['stage_reached']} of {len(report['stages'])} ({report['stage_name']})."
                 + (f" Working on: {report['working_on']}." if report["working_on"] else "")]
        for stage in report["stages"]:
            mark = "✓" if stage["passed"] else "·"
            value = "not measured" if stage["value"] is None else f"{stage['value']:g}"
            lines.append(f"{mark} {stage['number']}. {stage['name']} — {stage['measure']}: {value} "
                         f"(needs {stage['threshold']:g}). {stage['detail']}")
        lines.append(report["rule"])
        return "\n".join(lines)


def register_development_tools(registry: Any, development: CognitiveDevelopment) -> None:
    registry.register(
        "cognitive_development",
        "HAYAT's developmental stage (observation, recognition, association, curiosity, experimentation, "
        "understanding, reasoning, prediction, abstraction, metacognition, independent learning, problem-solving), each assessed from its own "
        "measured records with a stated threshold, and what would move it forward. Never a neuron count.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "system_read", development.assess)


_DEVELOPMENT_QUESTION = (
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:so\s+|ok(?:ay)?\s+)?(?:"
    r"(?:what|which)\s+(?:developmental\s+|development\s+|cognitive\s+|learning\s+)?stage\s+(?:of\s+(?:development|growth)\s+)?"
    r"(?:are\s+you|have\s+you\s+reached|is\s+hayat)"
    r"|how\s+(?:developed|mature|far\s+(?:along|developed))\s+(?:are\s+you|is\s+your\s+(?:brain|mind|intelligence))"
    r"|(?:show|what\s+is|what's)\s+(?:me\s+)?(?:your|hayat'?s)\s+(?:cognitive\s+)?development"
    r"|how\s+(?:much\s+)?(?:have\s+you|has\s+your\s+brain)\s+(?:grown|developed|matured))\b")


def development_request(text: str) -> bool:
    import re

    return bool(re.match(_DEVELOPMENT_QUESTION, " ".join(str(text or "").split())))


def direct_development_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if not development_request(text) or "cognitive_development" not in set(names):
        return None
    return [{"description": "Assess HAYAT's development stages from its measured records.",
             "tool": "cognitive_development", "arguments": {}, "expected": {"type": "tool_success"}, "max_retries": 0}]
