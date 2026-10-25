"""HAYAT's running understanding of the situation, kept across restarts.

The live state snapshot (app/state.py) says what is true this second: the
active window, the browser page, the camera, the models. What it could not say
is what the owner is *doing*: what has occupied the last hour, what was going
on last session, what keeps failing, what HAYAT cannot do right now, and what
is likely to be asked next. So every new conversation started cold, and the
owner explained the same context again.

This keeps that understanding as data:

* focus -- the areas the owner's recent requests were about, and the latest one;
* habits -- which areas come up at this time of day, and what usually follows what;
* recent outcomes -- the last tasks and whether they worked;
* gaps -- areas that need setup, recent failures, tools that often fail;
* predictions -- the likely next areas with the evidence behind each.

Each prediction is checked against the request that actually follows, so its
accuracy is a measured number rather than a claim, and it shows in every report.
Predictions are offered as context and suggestions; HAYAT never acts on one by
itself. Nothing here rewrites code or changes permissions.

The turns table holds an area label and the first 200 characters of a request,
in the same local data folder as the conversation history it is drawn from.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Areas too generic to predict or report as a focus.
_GENERIC = {"chat"}


def part_of_day(moment: datetime) -> str:
    hour = moment.hour
    return "night" if hour < 5 else "morning" if hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 22 else "night"


#: Words too common to say what a request is about ("check", "working").
_GENERIC_WORDS = re.compile(r"(?i)\b(?:check(?:ed|ing)?|work(?:ing|s)?|tools?|status|know|next|now|today|again)\b")
#: "ok", "yes", "explain", "go on": a reply to the last answer, not a new subject.
FOLLOW_UP_WINDOW = timedelta(minutes=30)


def classify(text: str, previous: str | None = None) -> str:
    """The capability area a request is about (app/capabilities.py).

    A short follow-up with no subject of its own ("ok great", "yes", "explain")
    belongs to the area of the request before it, when that was recent.
    """
    from app.agent.conversation import refers_to_previous_reply
    from app.answers import is_small_talk
    from app.capabilities import match

    value = " ".join(str(text or "").split())
    found = match(_GENERIC_WORDS.sub(" ", value), 1)
    if found:
        return found[0].key
    if previous and not is_small_talk(value) and (refers_to_previous_reply(value) or len(value.split()) <= 4):
        return previous
    return "chat"


def area_title(key: str) -> str:
    from app.capabilities import BY_KEY

    capability = BY_KEY.get(key)
    return capability.title if capability else "Conversation"


class SituationModel:
    def __init__(self, path: Path, runtime: Callable[[], Any] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.path = Path(path)
        self.runtime = runtime or (lambda: None)
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._lock = threading.Lock()
        self._ready = False

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS situation_turns (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, part TEXT NOT NULL, area TEXT NOT NULL,
                    text TEXT NOT NULL, ok INTEGER);
                CREATE TABLE IF NOT EXISTS situation_predictions (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, areas TEXT NOT NULL, basis TEXT NOT NULL,
                    resolved INTEGER NOT NULL DEFAULT 0, hit INTEGER, actual TEXT);
            """)
            self._ready = True
        return db

    # -------------------------------------------------------------- observe
    def observe_turn(self, text: str, ok: bool | None = None) -> dict[str, Any]:
        """Record one owner request, score the prediction made before it, and predict the next."""
        value = " ".join(str(text or "").split())
        if not value:
            return {}
        moment = self.clock()
        with self._lock, self._db() as db:
            last = db.execute("SELECT at, area FROM situation_turns ORDER BY id DESC LIMIT 1").fetchone()
        inherited = (last["area"] if last is not None
                     and moment - datetime.fromisoformat(last["at"]) <= FOLLOW_UP_WINDOW else None)
        area = classify(value, inherited)
        with self._lock, self._db() as db:
            open_prediction = db.execute(
                "SELECT id, areas FROM situation_predictions WHERE resolved=0 ORDER BY id DESC LIMIT 1").fetchone()
            hit = None
            if open_prediction is not None:
                hit = area in json.loads(open_prediction["areas"])
                db.execute("UPDATE situation_predictions SET resolved=1, hit=?, actual=? WHERE id=?",
                           (int(hit), area, open_prediction["id"]))
            db.execute("INSERT INTO situation_turns (at, part, area, text, ok) VALUES (?,?,?,?,?)",
                       (moment.isoformat(), part_of_day(moment), area, value[:200], None if ok is None else int(ok)))
        predictions = self.predict()
        likely = [item["area"] for item in predictions if item.get("area")]
        if likely:
            with self._lock, self._db() as db:
                db.execute("INSERT INTO situation_predictions (at, areas, basis) VALUES (?,?,?)",
                           (moment.isoformat(), json.dumps(likely), "; ".join(item["basis"] for item in predictions)))
        return {"area": area, "previous_prediction_hit": hit, "predicted_next": likely}

    def backfill(self, memory: Any, limit: int = 5000, since: str = "2026-09-30") -> int:
        """Learn the owner's history once, from the conversation record, when this model is new.

        Without it every habit and pattern would start from zero on the day
        this shipped, though months of requests were already on disk.
        """
        from app.agent.conversation import owner_words

        with self._lock, self._db() as db:
            if db.execute("SELECT COUNT(*) FROM situation_turns").fetchone()[0]:
                return 0
        try:
            with memory.connect() as source:
                # Before 2026-09-30 the test suite also wrote into this table.
                rows = source.execute("SELECT timestamp, content FROM conversations WHERE role='user' "
                                      "AND timestamp >= ? ORDER BY id DESC LIMIT ?", (since, limit)).fetchall()
        except Exception:
            log_swallowed(_log, "reading conversation history for the situation backfill", level=logging.WARNING)
            return 0
        records: list[tuple[str, str, str, str]] = []
        for timestamp, content in reversed(rows):
            text = " ".join(owner_words(str(content or "")).split())
            if not text:
                continue
            try:
                moment = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone()
            except ValueError:
                continue
            previous = records[-1] if records else None
            inherited = (previous[2] if previous and moment - datetime.fromisoformat(previous[0]) <= FOLLOW_UP_WINDOW
                         else None)
            records.append((moment.isoformat(), part_of_day(moment), classify(text, inherited), text[:200]))
        with self._lock, self._db() as db:
            db.executemany("INSERT INTO situation_turns (at, part, area, text) VALUES (?,?,?,?)", records)
        return len(records)

    # ----------------------------------------------------------- understand
    def _turns(self, limit: int = 2000) -> list[sqlite3.Row]:
        with self._lock, self._db() as db:
            return db.execute("SELECT * FROM situation_turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def focus(self, hours: float = 3.0) -> dict[str, Any]:
        rows = self._turns(60)
        if not rows:
            return {"areas": [], "latest": None, "since": None, "last_session": None}
        now = self.clock()
        cutoff = now - timedelta(hours=hours)
        recent = [row for row in rows if datetime.fromisoformat(row["at"]) >= cutoff]
        counted = Counter(row["area"] for row in recent if row["area"] not in _GENERIC)
        latest = rows[0]
        focus = {"areas": [{"area": area, "title": area_title(area), "requests": n} for area, n in counted.most_common(3)],
                 "latest": {"text": latest["text"], "area": latest["area"], "at": latest["at"]},
                 "since": recent[-1]["at"] if recent else None, "last_session": None}
        if not recent:
            earlier = Counter(row["area"] for row in rows[:20] if row["area"] not in _GENERIC)
            focus["last_session"] = {"at": latest["at"],
                                     "areas": [area_title(area) for area, _n in earlier.most_common(3)]}
        return focus

    def habits(self) -> dict[str, Any]:
        rows = list(reversed(self._turns()))
        if not rows:
            return {"requests": 0}
        part = part_of_day(self.clock())
        overall = Counter(row["area"] for row in rows if row["area"] not in _GENERIC)
        now_part = Counter(row["area"] for row in rows if row["part"] == part and row["area"] not in _GENERIC)
        return {"requests": len(rows), "part_of_day": part,
                "most_asked": [area_title(area) for area, _n in overall.most_common(4)],
                "usual_now": [area_title(area) for area, _n in now_part.most_common(3)]}

    def _transitions(self) -> dict[str, Counter]:
        rows = list(reversed(self._turns()))
        table: dict[str, Counter] = {}
        for before, after in zip(rows, rows[1:]):
            # A gap of more than two hours is a new session, not a follow-up.
            if datetime.fromisoformat(after["at"]) - datetime.fromisoformat(before["at"]) > timedelta(hours=2):
                continue
            table.setdefault(before["area"], Counter())[after["area"]] += 1
        return table

    # -------------------------------------------------------------- predict
    def predict(self, limit: int = 3) -> list[dict[str, Any]]:
        rows = self._turns(1)
        found: dict[str, dict[str, Any]] = {}
        if rows:
            last = rows[0]["area"]
            following = self._transitions().get(last, Counter())
            total = sum(following.values())
            for area, count in following.most_common(3):
                if count >= 2 and area not in _GENERIC:
                    found[area] = {"area": area, "title": area_title(area), "probability": round(count / total, 2),
                                   "basis": f"after {area_title(last)}, {area_title(area)} followed {count} of {total} times"}
        part = part_of_day(self.clock())
        by_time = Counter(row["area"] for row in self._turns() if row["part"] == part and row["area"] not in _GENERIC)
        total_time = sum(by_time.values())
        for area, count in by_time.most_common(2):
            if count >= 3 and area not in found:
                found[area] = {"area": area, "title": area_title(area), "probability": round(count / total_time, 2),
                               "basis": f"{area_title(area)} is {count} of {total_time} requests in the {part}"}
        return sorted(found.values(), key=lambda item: -item["probability"])[:limit]

    def accuracy(self, last: int = 100) -> dict[str, Any]:
        with self._lock, self._db() as db:
            rows = db.execute("SELECT hit FROM situation_predictions WHERE resolved=1 ORDER BY id DESC LIMIT ?",
                              (last,)).fetchall()
        hits = sum(int(row["hit"] or 0) for row in rows)
        return {"checked": len(rows), "right": hits, "rate": round(hits / len(rows), 2) if rows else None}

    def pending(self) -> list[str]:
        """Things already waiting, read from the records that hold them."""
        runtime = self.runtime()
        items: list[str] = []
        business = getattr(runtime, "business", None)
        if business is not None:
            try:
                due = [lead["title"] for lead in business.leads("open", due_only=True)["leads"]]
                if due:
                    items.append(f"{len(due)} lead follow-up(s) due: {', '.join(due[:3])}")
            except Exception:
                log_swallowed(_log, "reading due lead follow-ups for the situation")
        jobs = getattr(runtime, "research_jobs", None)
        if jobs is not None and hasattr(jobs, "running"):
            try:
                running = jobs.running()
                if running:
                    items.append(f"{len(running)} search(es) still running")
            except Exception:
                log_swallowed(_log, "reading running research jobs for the situation")
        return items

    # ------------------------------------------------------- know the limits
    def gaps(self) -> list[str]:
        """What HAYAT cannot do right now, or keeps failing at, from its own records."""
        runtime = self.runtime()
        found: list[str] = []
        if runtime is None:
            return found
        try:
            from app.capabilities import CATALOG, readiness

            for capability in CATALOG:
                state, note = readiness(capability, runtime)
                if state != "ready":
                    found.append(f"{capability.title}: {note}")
        except Exception:
            log_swallowed(_log, "reading capability readiness for the situation")
        failures = getattr(runtime, "failures", None)
        if failures is not None:
            try:
                cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
                recent = [item for item in failures.recent(5) if str(item.get("timestamp") or "") >= cutoff]
                for item in recent[:2]:
                    found.append(f"Recent failure in {item.get('service') or item.get('tool') or item.get('stage')}: "
                                 f"{item.get('exception_type') or 'failure'} -- "
                                 f"{str(item.get('exception_message') or item.get('verification_failure') or '')[:100]}")
            except Exception:
                log_swallowed(_log, "reading recent failures for the situation")
        understanding = getattr(runtime, "tool_understanding", None)
        if understanding is not None:
            try:
                for tool in understanding.unreliable(0.5)[:3]:
                    found.append(f"Tool {tool.get('tool') or tool.get('name')} has failed more often than it worked")
            except Exception:
                log_swallowed(_log, "reading unreliable tools for the situation")
        return found

    # ---------------------------------------------------------------- share
    def brief(self) -> dict[str, Any]:
        """Compact, for the conversation prompt and the planner's observation."""
        focus = self.focus()
        runtime = self.runtime()
        value: dict[str, Any] = {}
        if focus["areas"]:
            value["owner_focus"] = ", ".join(f"{item['title']} ({item['requests']})" for item in focus["areas"])
        if focus.get("latest"):
            value["latest_request"] = focus["latest"]["text"][:120]
        if focus.get("last_session"):
            value["last_session"] = {"ended": focus["last_session"]["at"], "was_about": focus["last_session"]["areas"]}
        memory = getattr(runtime, "memory", None)
        if memory is not None:
            try:
                # The task answering this very question is still running; it is not history.
                value["recent_tasks"] = [f"{row['status']}: {str(row['goal'])[:70]}"
                                         for row in memory.recent_tasks(4) if row["status"] != "running"][:3]
            except Exception:
                log_swallowed(_log, "reading recent tasks for the situation")
        predictions = self.predict()
        if predictions:
            accuracy = self.accuracy()
            value["likely_next"] = [f"{item['title']} ({item['basis']})" for item in predictions]
            if accuracy["checked"]:
                value["prediction_accuracy"] = f"{accuracy['right']} of last {accuracy['checked']} right"
        pending = self.pending()
        if pending:
            value["waiting"] = pending
        gaps = self.gaps()
        if gaps:
            value["cannot_do_now"] = gaps[:4]
        experience = getattr(runtime, "experience", None)
        if experience is not None:
            try:
                stats = experience.stats()
                if stats["experiences"]:
                    value["learned_from"] = (f"{stats['experiences']} past tasks, {stats['succeeded']} worked, "
                                             f"{stats['recovered_with_a_fix']} recovered with a recorded fix")
            except Exception:
                log_swallowed(_log, "reading experience stats for the situation")
        return value

    def suggestions(self, limit: int = 3) -> list[tuple[str, str]]:
        """Starting points for the empty chat, from the record: (chip label, request to fill in)."""
        from app.capabilities import BY_KEY

        rows = self._turns(30)
        if not rows:
            return []
        found: list[tuple[str, str]] = [("Where we left off", "Where did we leave off?")]
        if any(row["area"] == "languages" and "quran" in row["text"].lower() for row in rows):
            found.append(("Continue Quran", "continue quran"))
        for prediction in self.predict():
            capability = BY_KEY.get(prediction["area"])
            if capability is None or not capability.examples:
                continue
            # "See through the camera and screen" is too long for a chip; its first two words are not.
            label = capability.title if len(capability.title) <= 22 else " ".join(capability.title.split()[:2])
            if all(label != existing for existing, _request in found):
                found.append((label, capability.examples[0]))
        return found[:limit]

    def report(self) -> dict[str, Any]:
        """Everything, for "what's going on?" and the situation_report tool."""
        return {"situation": True, "observed_at": self.clock().isoformat(timespec="seconds"),
                "focus": self.focus(), "habits": self.habits(), "predictions": self.predict(),
                "prediction_accuracy": self.accuracy(), "waiting": self.pending(), "gaps": self.gaps(),
                "brief": self.brief(),
                "basis": ("Built from HAYAT's own records of your requests, its tasks and its setup. Predictions are "
                          "patterns with their evidence and measured accuracy; HAYAT does not act on them by itself.")}


# ------------------------------------------------------------------ routing
_SITUATION_QUESTION = re.compile(
    r"(?i)^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:so\s+|ok\s+|okay\s+)?(?:"
    # "What am I doing right now?" asks the camera to look (tests/test_camera_seeing.py),
    # so only the forms about work and the recent past come here.
    r"what\s+am\s+i\s+working\s+on(?:\s+(?:right\s+)?now|\s+today|\s+lately)?"
    r"|what\s+(?:have\s+i\s+been|was\s+i)\s+(?:doing|working\s+on)(?:\s+(?:today|lately|before|earlier))?"
    r"|what\s+(?:were|are)\s+we\s+(?:doing|working\s+on)(?:\s+(?:before|earlier|last\s+time|yesterday|today))?"
    r"|what(?:'s|\s+is)\s+(?:going\s+on|happening|the\s+situation)(?:\s+(?:now|right\s+now|today))?"
    r"|where\s+did\s+we\s+(?:leave\s+off|stop)"
    r"|what\s+(?:will|might|may)\s+(?:i|happen)\s+(?:do|ask|need\s+)?\s*next"
    r"|what\s+do\s+you\s+(?:think|predict|expect)\s+(?:i(?:'ll|\s+will)\s+(?:do|ask|need)|will\s+happen)\s+next"
    r"|what\s+do\s+you\s+know\s+about\s+(?:my\s+situation|what\s+i(?:'m|\s+am)\s+doing|my\s+work)"
    r"|(?:show|give\s+me)\s+(?:my\s+|the\s+)?(?:situation|situation\s+report|context\s+report)"
    r"|what\s+have\s+you\s+learned\s+from\s+(?:mistakes|failures|experience)"
    r"|what\s+(?:can't|cannot)\s+you\s+do\s+right\s+now"
    r")[\s?.!]*$"
)


def situation_request(text: str) -> bool:
    return bool(_SITUATION_QUESTION.match(" ".join(str(text or "").split())))


def direct_situation_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if not situation_request(text) or "situation_report" not in set(names):
        return None
    return [{"description": "Read HAYAT's own understanding of the situation from its records.",
             "tool": "situation_report", "arguments": {}, "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_situation_tools(registry: Any, model: SituationModel) -> None:
    registry.register(
        "situation_report",
        "HAYAT's own understanding of the situation from its records: what the owner has been doing, the last "
        "session, habits, recent task outcomes, what is waiting, what it cannot do right now, what it learned from "
        "past tasks, and what is likely next with the evidence and measured prediction accuracy.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "filesystem_read", model.report)
