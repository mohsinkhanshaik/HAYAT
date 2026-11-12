"""Self-review of every reply: why it arose, what shaped it, how reliable it is, and whether to revise it.

The owner asked for a fast, independent metacognitive layer "inspired by self-awareness" that questions
HAYAT's own reasoning without claiming consciousness. This is it, and it is deliberately modest:

* It runs after every reply, in about a millisecond, with no model call: it reads only what was recorded
  about the turn (how the words were read and routed, the memories the reply was built from, the research
  run and its sources and claims, the claim check, the self-questioning and skeptical review, standing rules).
* It answers five questions: what triggered the reply; what was assumed; which memories and evidence
  influenced it; how reliable it is, and why; whether it should be revised, and how.
* It learns. Each review is kept (data/memory/self-review.db). When the owner says a reply was right or wrong,
  that verdict is attached to the reply's review, and the reliability HAYAT reports for that level is pulled
  toward how often the owner actually found such answers right ("calibrated on N of your verdicts").

What it is not: it does not watch the model's internal reasoning (the model's activations are not
available), it does not decide what is true, and it is not an experience. It is bookkeeping about evidence,
done every time, so that "how sure are you?" and "why did you say that?" have honest answers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Starting reliability by where the answer came from, before the evidence adjusts it.
BASE = {"exact": .95, "money_guard": .9, "standing_instruction": .9, "research_audit": .8, "research": .72, "brain": .55}
RULES_BASE = .82   # any other deterministic rule
LEVELS = ((.75, "high"), (.5, "medium"), (0.0, "low"))
#: How many of the owner's verdicts it takes for them to count as much as the evidence-based estimate.
PRIOR_WEIGHT = 5
STALE_DAYS = 180


def _probability(value: Any) -> float | None:
    """A recorded probability, or unknown. Invalid history is never scored as zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def level_of(score: float) -> str:
    return next(name for floor, name in LEVELS if score >= floor)


def _age_days(stamp: Any, now: datetime) -> float | None:
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return max(0.0, (now - moment).total_seconds() / 86400)
    except (TypeError, ValueError):
        return None


def research_signal(knowledge_path: Path | str | None, since: str) -> dict[str, Any] | None:
    """The research run this turn made (started at or after `since`): confidence, sources, independence, claims."""
    if not knowledge_path or not Path(knowledge_path).is_file():
        return None
    try:
        with sqlite3.connect(f"file:{Path(knowledge_path).as_posix()}?mode=ro", uri=True, timeout=2) as db:
            run = db.execute("SELECT id, query, confidence, complete FROM research_runs WHERE started_at >= ? "
                             "ORDER BY started_at DESC LIMIT 1", (since,)).fetchone()
            if not run:
                return None
            run_id = run[0]
            sources = db.execute("SELECT COUNT(*), COUNT(DISTINCT COALESCE(s.independence_group, s.domain)) FROM run_sources r "
                                 "JOIN sources s ON s.id = r.source_id WHERE r.run_id = ?", (run_id,)).fetchone()
            statuses = dict(db.execute("SELECT status, COUNT(*) FROM claims WHERE run_id = ? GROUP BY status", (run_id,)).fetchall())
    except sqlite3.Error:
        return None
    return {"run_id": run_id, "query": run[1], "confidence": float(run[2] or 0), "complete": bool(run[3]),
            "sources": int(sources[0] or 0), "independent": int(sources[1] or 0),
            "supported": int(statuses.get("SUPPORTED", 0)), "disputed": int(statuses.get("DISPUTED", 0)),
            "unverified": int(statuses.get("UNVERIFIED", 0))}


def review(*, request: str, intent: str = "", confidence: float | None = None, routed_by: str = "",
           answer_source: str = "", spelling: list | None = None, meaning_assumed: str = "",
           recalled: list[dict] | None = None, research: dict | None = None, inquiry: dict | None = None,
           dual_review: dict | None = None, claims_labelled: Any = None, standing: dict | None = None,
           now: datetime | None = None) -> dict[str, Any]:
    """The self-review of one reply, from recorded facts only."""
    now = now or datetime.now(UTC)
    source = answer_source or "brain"
    score = BASE.get(source, RULES_BASE if source not in ("", "brain", "conversation", "question") else BASE["brain"])
    if research:
        score = max(score, BASE["research"])
    reasons: list[str] = [f"Answer came from {'research' if research else source.replace('_', ' ')}."]
    assumptions: list[str] = []
    revise_how: list[str] = []
    influences: list[str] = []

    # What triggered it.
    trigger = f"Your message “{' '.join(str(request).split())[:160]}”"
    if intent:
        trigger += f", read as {intent.lower().replace('_', ' ')}"
        if confidence is not None:
            trigger += f" ({confidence:.0%} sure"
            trigger += f", by {routed_by.replace('_', ' ')})" if routed_by else ")"
    if confidence is not None and confidence < .7 and source not in ("exact",):
        score -= .1
        assumptions.append(f"What you wanted was a guess ({confidence:.0%} sure).")
        revise_how.append("Confirm what you asked for.")

    # What it assumed.
    for typed, meant in (spelling or [])[:6]:
        assumptions.append(f"Read “{typed}” as “{meant}”.")
    if spelling:
        score -= .05
    if meaning_assumed:
        score -= .08
        assumptions.append(f"Assumed the meaning: {meaning_assumed}")
        revise_how.append("Say which meaning you intended.")
    if standing and standing.get("rule"):
        assumptions.append(f"Followed your standing instruction: {standing['rule']}.")

    # Which memories shaped it.
    misled, stale = [], []
    for memory in recalled or []:
        meta = memory.get("metadata") or {}
        good, bad = float(meta.get("successful_uses") or 0), float(meta.get("failed_uses") or 0)
        age = _age_days(memory.get("timestamp"), now)
        text = " ".join(str(memory.get("content") or "").split())
        influences.append(f"memory #{memory.get('id')} ({memory.get('memory_type') or 'memory'}"
                          + (f", {age:.0f} days old" if age is not None else "")
                          + (f", used well {good:.0f}× / badly {bad:.0f}×" if good or bad else "")
                          + f"): {text[:110]}")
        if bad > good:
            misled.append(memory)
        if age is not None and age > STALE_DAYS:
            stale.append(memory)
    if misled:
        score -= .1
        reasons.append(f"{len(misled)} memory used has misled before (more failed than successful uses).")
        revise_how += [f"Check memory #{memory.get('id')}, which has been wrong before." for memory in misled[:2]]
    if stale:
        score -= .05
        reasons.append(f"{len(stale)} memory used is over {STALE_DAYS} days old.")
        revise_how += [f"Check memory #{memory.get('id')} is still true." for memory in stale[:2]]
    if recalled and not misled:
        score += .03
        reasons.append(f"Built on {len(recalled)} of your recorded memories.")

    # What evidence shaped it.
    if research:
        influences.append(f"research run {research['run_id']}: {research['sources']} sources "
                          f"({research['independent']} independent), {research['supported']} supported claims"
                          + (f", {research['disputed']} disputed" if research['disputed'] else ""))
        if research["independent"] >= 3:
            score += .12
            reasons.append(f"{research['independent']} independent sources agree on the basis.")
        elif research["independent"] == 2:
            score += .06
            reasons.append("Two independent sources.")
        elif research["sources"] <= 1:
            score -= .12
            reasons.append("Only one source was read.")
            revise_how.append("Find a second, independent source.")
        if research["disputed"]:
            score -= .15
            reasons.append(f"{research['disputed']} claim{'s' if research['disputed'] != 1 else ''} it rests on "
                           f"{'are' if research['disputed'] != 1 else 'is'} disputed between sources.")
            revise_how.append("Settle the disputed point against a primary source.")
        if not research["complete"]:
            score -= .05
            reasons.append("The research stopped before it was complete.")
    labelled = len(claims_labelled) if isinstance(claims_labelled, (list, tuple)) else int(bool(claims_labelled))
    if labelled:
        score -= min(.2, .07 * labelled)
        reasons.append(f"{labelled} statement{'s' if labelled != 1 else ''} had no backing and "
                       f"{'were' if labelled != 1 else 'was'} labelled unverified in the reply.")
        revise_how.append("Look up the unverified statements.")
    if source == "brain" and not research and not recalled:
        reasons.append("Nothing was looked up and no memory was used: it rests on the model's own knowledge.")
    if inquiry and inquiry.get("state") == "examined" and inquiry.get("open_points"):
        score -= min(.15, .05 * int(inquiry["open_points"]))
        reasons.append(f"Its self-questioning left {inquiry['open_points']} point(s) unresolved.")
    if dual_review and dual_review.get("state") == "reviewed" and dual_review.get("revised"):
        reasons.append("A skeptical check revised the draft before you saw it.")
    score = round(max(.05, min(.98, score)), 3)
    level = level_of(score)
    needed = level == "low" or bool(research and research.get("disputed")) or bool(meaning_assumed) or bool(misled)
    return {"at": now.isoformat(timespec="seconds"), "trigger": trigger, "assumptions": assumptions,
            "influences": influences, "score": round(score, 3), "level": level, "reasons": reasons,
            "revise": {"needed": needed, "how": list(dict.fromkeys(revise_how))[:5]},
            "basis": "Bookkeeping of recorded evidence, not a view of the model's internal reasoning."}


def lines(item: dict[str, Any] | None, calibration: dict[str, Any] | None = None) -> list[str]:
    """The review in plain lines, for the conversation details panel and "why did you say that?"."""
    if not item:
        return []
    shown = item.get("calibrated_score", item["score"])
    out = [f"Self-review: reliability {item.get('calibrated_level', item['level'])} ({shown:.0%})"
           + (f", calibrated on {item['calibrated_on']} of your verdicts" if item.get("calibrated_on") else "") + "."]
    out.append("Why it arose: " + item["trigger"] + ".")
    if item["assumptions"]:
        out.append("Assumed: " + " ".join(item["assumptions"]))
    if item["influences"]:
        out.append("Shaped by: " + "; ".join(item["influences"][:4]) + ("…" if len(item["influences"]) > 4 else ""))
    out.append("Reliability, because: " + " ".join(item["reasons"]))
    revise = item["revise"]
    out.append(("Would revise it: " + " ".join(revise["how"])) if revise["needed"] and revise["how"]
               else "Would revise it: yes, it is weak." if revise["needed"] else "No revision needed on this evidence.")
    return out


# --------------------------------------------------------------------------------------- learning

class SelfReviewLog:
    """Every review, with the owner's verdict once given: the record calibration learns from."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def beside(cls, memory: Any) -> SelfReviewLog:
        return cls(Path(memory.path).parent / "self-review.db")

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, at TEXT NOT NULL, session_id TEXT, "
                   "reply_hash TEXT, score REAL, level TEXT, review_json TEXT, verdict INTEGER)")
        db.execute("CREATE INDEX IF NOT EXISTS reviews_reply ON reviews(session_id, reply_hash)")
        return db

    @staticmethod
    def digest(reply: str) -> str:
        return hashlib.sha256(" ".join(str(reply or "").split()).encode("utf-8")).hexdigest()[:24]

    def record(self, item: dict[str, Any], session_id: str, reply: str) -> None:
        if not str(reply or "").strip() or not str(session_id or "").strip():
            raise ValueError("A review needs a nonempty reply and session for feedback attribution.")
        if _probability(item.get("score")) is None or item.get("level") != level_of(item["score"]):
            raise ValueError("A review needs a finite probability and its matching reliability level.")
        if "calibrated_score" in item and _probability(item["calibrated_score"]) is None:
            raise ValueError("A calibrated review needs a finite probability.")
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO reviews (at, session_id, reply_hash, score, level, review_json) VALUES (?,?,?,?,?,?)",
                       (item["at"], session_id, self.digest(reply), item["score"], item["level"], json.dumps(item)))

    def judge(self, session_id: str, reply: str, rating: int) -> bool:
        """Attach an explicit verdict to the matching reply, never to a nearby turn.

        Repeated feedback replaces the verdict on that reply instead of creating
        extra training examples. If rendering changed its text, attribution is
        unknown; guessing the latest review would silently poison calibration.
        """
        if type(rating) is not int or rating not in (-1, 1):
            raise ValueError("A verdict must be +1 (right) or -1 (wrong).")
        if not str(reply or "").strip() or not str(session_id or "").strip():
            return False
        with self._lock, self._connect() as db:
            row = db.execute("SELECT id FROM reviews WHERE session_id=? AND reply_hash=? ORDER BY id DESC LIMIT 1",
                             (session_id, self.digest(reply))).fetchone()
            if not row:
                return False
            db.execute("UPDATE reviews SET verdict=? WHERE id=?", (rating, row[0]))
            return True

    def _statistics(self) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Score predictions saved before the verdict, without retroactive refitting."""
        table = {name: {"judged": 0, "right": 0, "predicted": None, "raw_predicted": None,
                        "observed": None, "observed_unsmoothed": None, "scored": 0}
                 for _floor, name in LEVELS}
        totals = {name: {"shown": 0.0, "raw": 0.0} for _floor, name in LEVELS}
        result: dict[str, Any] = {
            "total_reviews": 0, "judged_reviews": 0, "unjudged_reviews": 0,
            "scored_reviews": 0, "unscorable_reviews": 0, "invalid_verdicts": 0,
            "unattributed_reviews": 0,
            "verdict_coverage": None, "brier_score": None, "raw_brier_score": None,
            "calibrated_reviews": 0, "calibrated_brier_score": None,
            "calibrated_raw_brier_score": None, "calibration_brier_gain": None,
            "calibration_evidence": "not_measured",
            "prediction_basis": "Scores saved with each reply before its verdict; no retrospective refitting.",
            "limitation": "Owner-selected verdicts are feedback, not an independent accuracy benchmark.",
        }
        if not self.path.is_file():
            return table, result
        brier, raw_brier, adjusted_brier, adjusted_raw_brier = 0.0, 0.0, 0.0, 0.0
        with self._lock, closing(sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=5)) as db:
            # Coverage and scores must refer to the same database snapshot even
            # when a new reply or verdict is being recorded on another thread.
            db.execute("BEGIN")
            result["total_reviews"], result["unjudged_reviews"] = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(verdict IS NULL), 0) FROM reviews").fetchone()
            for level, verdict, score, payload, reply_hash in db.execute(
                    "SELECT level, verdict, score, review_json, reply_hash FROM reviews WHERE verdict IS NOT NULL"):
                if verdict not in (-1, 1):
                    result["invalid_verdicts"] += 1
                    continue
                result["judged_reviews"] += 1
                # An older adapter read response.content instead of display_text,
                # storing the empty reply's hash. Its time-based fallback verdicts
                # cannot establish which answer was judged; retain, but do not fit.
                if not reply_hash or reply_hash == self.digest(""):
                    result["unattributed_reviews"] += 1
                    result["unscorable_reviews"] += 1
                    continue
                raw = _probability(score)
                if raw is None or level not in table or level != level_of(raw):
                    result["unscorable_reviews"] += 1
                    continue
                group = table[level]
                group["judged"] += 1
                group["right"] += int(verdict == 1)
                totals[level]["raw"] += raw
                try:
                    saved = json.loads(payload)
                except (ValueError, TypeError):
                    saved = None
                # Older rows already kept the shown score in review_json. A
                # damaged snapshot cannot be reconstructed from today's fit.
                shown = (_probability(saved.get("calibrated_score", saved.get("score")))
                         if isinstance(saved, dict) else None)
                if shown is None or _probability(saved.get("score")) != raw:
                    result["unscorable_reviews"] += 1
                    continue
                target = int(verdict == 1)
                shown_error, raw_error = (shown - target) ** 2, (raw - target) ** 2
                group["scored"] += 1
                totals[level]["shown"] += shown
                result["scored_reviews"] += 1
                brier += shown_error
                raw_brier += raw_error
                if "calibrated_score" in saved:
                    result["calibrated_reviews"] += 1
                    adjusted_brier += shown_error
                    adjusted_raw_brier += raw_error
        for name, value in table.items():
            judged, scored = value["judged"], value["scored"]
            if judged:
                value["observed"] = (value["right"] + 1) / (judged + 2)
                value["observed_unsmoothed"] = value["right"] / judged
                value["raw_predicted"] = totals[name]["raw"] / judged
            if scored:
                value["predicted"] = totals[name]["shown"] / scored
        total, scored, adjusted = result["total_reviews"], result["scored_reviews"], result["calibrated_reviews"]
        if total:
            result["verdict_coverage"] = result["judged_reviews"] / total
        if scored:
            result["brier_score"], result["raw_brier_score"] = brier / scored, raw_brier / scored
        if adjusted:
            result["calibrated_brier_score"] = adjusted_brier / adjusted
            result["calibrated_raw_brier_score"] = adjusted_raw_brier / adjusted
            gain = (adjusted_raw_brier - adjusted_brier) / adjusted
            result["calibration_brier_gain"] = gain
            result["calibration_evidence"] = ("lower_error_on_judged_replies" if gain > 1e-12 else
                                              "higher_error_on_judged_replies" if gain < -1e-12 else "same_error")
        return table, result

    def calibration(self) -> dict[str, dict[str, Any]]:
        """Raw-score buckets for fitting, with the prediction actually shown and both observed rates."""
        return self._statistics()[0]

    def assessment(self) -> dict[str, Any]:
        """Coverage and squared prediction error against feedback (Brier, lower is better)."""
        return self._statistics()[1]

    def calibrate(self, item: dict[str, Any]) -> dict[str, Any]:
        """Pull the estimate toward how often the owner found answers at this level right."""
        level = self.calibration().get(item["level"]) or {}
        judged = int(level.get("judged") or 0)
        if not judged:
            return item
        adjusted = round((item["score"] * PRIOR_WEIGHT + level["observed"] * judged) / (PRIOR_WEIGHT + judged), 3)
        calibrated = {**item, "calibrated_score": adjusted, "calibrated_level": level_of(adjusted),
                      "calibrated_on": judged}
        if calibrated["calibrated_level"] == "low":
            guidance = "Check this answer against independent evidence; owner feedback lowered its reliability."
            revise = item.get("revise") or {}
            calibrated["revise"] = {**revise, "needed": True,
                                    "how": list(dict.fromkeys([guidance, *revise.get("how", [])]))[:5]}
        return calibrated

    def report(self) -> str:
        table, assessment = self._statistics()
        judged = sum(value["judged"] for value in table.values())
        if not judged:
            excluded = (f" {assessment['unscorable_reviews']} historical verdict(s) lack a usable prediction or reply "
                        "attribution and were excluded." if assessment["unscorable_reviews"] else "")
            return ("No usable verdicts yet. Say “that's right” or “that's wrong” after a reply and HAYAT will compare "
                    "how sure it said it was with how often you found it right." + excluded)
        parts = []
        for name, value in table.items():
            if value["judged"]:
                said = (f"said {value['predicted']:.0%}" if value["scored"] == value["judged"] else
                        f"shown scores incomplete ({value['scored']} of {value['judged']} usable)")
                parts.append(f"{name} initial rating: {value['right']} of {value['judged']} right ({said}, "
                             f"observed {value['observed_unsmoothed']:.0%})")
        coverage = assessment["verdict_coverage"] or 0
        summary = ("How well HAYAT's self-review matches your verdicts — " + "; ".join(parts)
                   + f". Verdict coverage: {assessment['judged_reviews']}/{assessment['total_reviews']} ({coverage:.0%}).")
        if assessment["brier_score"] is not None:
            summary += (f" Prediction error (Brier, lower is better): shown {assessment['brier_score']:.3f}, "
                        f"raw {assessment['raw_brier_score']:.3f}, on {assessment['scored_reviews']} judged replies.")
        if assessment["unscorable_reviews"]:
            summary += f" {assessment['unscorable_reviews']} historical verdict(s) could not be scored."
        return summary + " " + assessment["limitation"]


# --------------------------------------------------------------------------------------- per turn

def for_turn(runtime: Any, request: str, reply: Any, turn_started: str) -> dict[str, Any] | None:
    """Build, calibrate, record and keep the review of the reply just made. Never raises into the turn."""
    try:
        session = getattr(runtime, "session", None)
        conversation = getattr(runtime, "conversation", None)
        intent = getattr(reply, "intent", None)
        response = getattr(reply, "response", None)
        text = str(getattr(response, "display_text", "") or getattr(response, "content", "")
                   or getattr(reply, "display_response", "") or getattr(reply, "text", "") or "")
        knowledge = getattr(getattr(getattr(runtime, "research", None), "knowledge", None), "path", None)
        assumed = ""
        marker = "say if you meant"
        if marker in text.lower():
            start = text.lower().index(marker)
            assumed = text[max(0, start - 120):start + 80].split("\n")[-1].strip()
        item = review(
            request=request, intent=str(getattr(getattr(intent, "intent", None), "value", "") or ""),
            confidence=getattr(intent, "confidence", None), routed_by=str(getattr(intent, "classification_source", "") or ""),
            answer_source=str(getattr(session, "last_answer_source", "") or ""),
            spelling=list(getattr(session, "last_spelling", None) or []), meaning_assumed=assumed,
            recalled=list(getattr(conversation, "last_recalled", None) or []),
            research=research_signal(knowledge, turn_started), inquiry=getattr(conversation, "last_inquiry", None),
            dual_review=getattr(conversation, "last_dual_review", None),
            claims_labelled=None, standing=getattr(session, "standing_instruction", None))
        memory = getattr(runtime, "memory", None)
        private = bool(getattr(getattr(runtime, "authority", None), "privacy_mode", False))
        if memory is not None and getattr(memory, "path", None) and not private:  # privacy mode keeps no record
            log = SelfReviewLog.beside(memory)
            item = log.calibrate(item)
            log.record(item, str(getattr(session, "session_id", "") or getattr(conversation, "session_id", "") or ""), text)
        if session is not None:
            session.last_self_review = item
        return item
    except Exception:
        log_swallowed(_log, "reviewing the reply")
        return None
