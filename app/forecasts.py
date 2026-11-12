"""HAYAT's own forecasts, kept and checked: was what it predicted what then happened?

Market ranges already had a walk-forward record (app/market_periods.py) and the situation model scored its guesses
about the owner's next request. Forecasts about the world -- "predict India's growth for next year", "will the
storm reach Florida" -- were written and forgotten, so nobody could say how often they were right.

Every substantive reply to a prediction request is now kept here (``forecasts.db``) with the question, the
forecast as written, what it was about, and a check date worked out from the horizon it names (next year -> after
that year ends, next month -> after that month, tomorrow -> two days). When a forecast falls due:

* the background learner is asked to look up what happened (its usual public-source research), and
* the forecast is listed as *due*, with the outcome evidence once found.

HAYAT does not grade its own forecasts: whether "growth of about 6.5%" was right against "6.2%" is a judgement,
so the owner gives it (right / partly / wrong / can't tell). The hit rate is computed only from those verdicts.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

VERDICTS = ("right", "partly", "wrong", "unclear")
PREDICTION = re.compile(r"(?i)\b(?:predict(?:ion|ions)?|forecast(?:s|ing)?|outlook|projection|will\b.{0,60}\b(?:rise|fall|"
                        r"grow|drop|win|lose|happen|reach|increase|decrease|recover)|what\s+(?:will|would)\s+happen)\b")
#: Replies that are not the forecast: a refusal, a question back, or "I'm searching" (the answer comes later).
_NOT_A_FORECAST = re.compile(r"(?i)^(?:i (?:can't|cannot|won't)|i'm not able|which |what do you mean|could you clarify"
                             r"|i'?m (?:searching|looking)|i am (?:searching|looking)|looking (?:it|that) up)")


def check_date(text: str, asked: date) -> date:
    """When the forecast can be checked, from the horizon it names; 90 days when it names none."""
    value = str(text or "").lower()
    years = [int(year) for year in re.findall(r"\b(20\d\d)\b", value) if int(year) >= asked.year]
    if re.search(r"\bnext\s+year\b", value):
        years.append(asked.year + 1)
    if years:
        return date(max(years) + 1, 1, 31)  # after the year closes, when its figures start to be published
    if re.search(r"\bthis\s+year\b", value):
        return date(asked.year + 1, 1, 31)
    if re.search(r"\bnext\s+month\b", value):
        first = (asked.replace(day=1) + timedelta(days=32)).replace(day=1)
        return (first + timedelta(days=32)).replace(day=7)
    if re.search(r"\bnext\s+week\b", value):
        return asked + timedelta(days=14)
    if re.search(r"\b(?:tomorrow|tonight)\b", value):
        return asked + timedelta(days=2)
    if re.search(r"\bthis\s+(?:week|weekend)\b", value):
        return asked + timedelta(days=9)
    return asked + timedelta(days=90)


def is_forecast_request(text: str) -> bool:
    return bool(PREDICTION.search(" ".join(str(text or "").split())))


class ForecastLedger:
    SOURCE = "forecasts"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ready = False
        #: Set by the runtime: the event bus.
        self.events: Any = None

    def _emit(self, event_type: str, payload: dict[str, Any], **scope: Any) -> None:
        """Report activity on the event bus (ids and counts only, never text), so the Brain view shows it."""
        events = getattr(self, "events", None)
        if events is None:
            return
        try:
            events.publish(event_type, self.SOURCE, payload, **scope)
        except Exception:
            log_swallowed(_log, f"publishing {event_type}")

    def status(self) -> dict[str, Any]:
        return self.stats()

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS forecasts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, made_at TEXT NOT NULL, question TEXT NOT NULL,
                    forecast TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', topic_id TEXT NOT NULL DEFAULT '',
                    check_on TEXT NOT NULL, lookup_queued INTEGER NOT NULL DEFAULT 0,
                    verdict TEXT NOT NULL DEFAULT '', judged_at TEXT, note TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS forecasts_due ON forecasts(verdict, check_on);
            """)
            self._ready = True
        return db

    def record(self, question: str, forecast: str, *, subject: str = "", topic_id: str = "",
               now: datetime | None = None) -> int | None:
        """Keep a forecast HAYAT just gave; None when the reply was not one (a question back, a refusal)."""
        reply = " ".join(str(forecast or "").split())
        asked = " ".join(str(question or "").split())
        if not is_forecast_request(asked) or len(reply) < 60 or _NOT_A_FORECAST.match(reply):
            return None
        now = now or datetime.now(UTC)
        due = check_date(f"{asked} {subject}", now.date())
        with self._lock, closing(self._db()) as db, db:
            cursor = db.execute("INSERT INTO forecasts(made_at,question,forecast,subject,topic_id,check_on) VALUES(?,?,?,?,?,?)",
                                (now.isoformat(timespec="seconds"), asked[:600], reply[:2000], subject[:300], topic_id,
                                 due.isoformat()))
            forecast_id = int(cursor.lastrowid or 0)
        self._emit("FORECAST_KEPT", {"forecast_id": forecast_id, "check_on": due.isoformat(), "topic_id": topic_id})
        return forecast_id

    def due(self, today: date | None = None, limit: int = 20) -> list[dict[str, Any]]:
        today = today or date.today()
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM forecasts WHERE verdict='' AND check_on<=? ORDER BY check_on LIMIT ?",
                              (today.isoformat(), limit)).fetchall()
        return [dict(row) for row in rows]

    def queue_lookups(self, enqueue: Any, today: date | None = None) -> int:
        """Ask the learner to find out what happened, once per due forecast."""
        queued = 0
        for item in self.due(today, 10):
            if item["lookup_queued"]:
                continue
            topic = f"what actually happened: {item['subject'] or item['question']}"[:240]
            try:
                enqueue(topic)
            except Exception:
                log_swallowed(_log, "queuing a forecast check")
                continue
            with self._lock, closing(self._db()) as db, db:
                db.execute("UPDATE forecasts SET lookup_queued=1 WHERE id=?", (item["id"],))
            queued += 1
        return queued

    def judge(self, forecast_id: int, verdict: str, note: str = "") -> dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
        with self._lock, closing(self._db()) as db, db:
            changed = db.execute("UPDATE forecasts SET verdict=?, judged_at=?, note=? WHERE id=?",
                                 (verdict, datetime.now(UTC).isoformat(timespec="seconds"), note[:500], int(forecast_id))).rowcount
        if changed:
            self._emit("FORECAST_JUDGED", {"forecast_id": int(forecast_id), "verdict": verdict})
        return {"judged": bool(changed), "id": int(forecast_id), "verdict": verdict}

    def stats(self) -> dict[str, Any]:
        with self._lock, closing(self._db()) as db:
            counts = {row[0] or "waiting": int(row[1]) for row in db.execute("SELECT verdict, COUNT(*) FROM forecasts GROUP BY verdict")}
            due = int(db.execute("SELECT COUNT(*) FROM forecasts WHERE verdict='' AND check_on<=?",
                                 (date.today().isoformat(),)).fetchone()[0])
        judged = sum(counts.get(name, 0) for name in ("right", "partly", "wrong"))
        score = counts.get("right", 0) + 0.5 * counts.get("partly", 0)
        return {"kept": sum(counts.values()), "by_verdict": counts, "due_now": due, "judged": judged,
                "hit_rate": round(score / judged, 3) if judged else None,
                "rule": "Only your verdicts count; HAYAT never grades its own forecasts."}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            return [dict(row) for row in db.execute("SELECT * FROM forecasts ORDER BY id DESC LIMIT ?", (limit,))]


def register_forecast_tools(registry: Any, ledger: ForecastLedger) -> None:
    def run(action: str = "stats", forecast_id: int = 0, verdict: str = "", note: str = "") -> dict[str, Any]:
        if action == "due":
            return {"due": ledger.due(), "stats": ledger.stats()}
        if action == "recent":
            return {"forecasts": ledger.recent(), "stats": ledger.stats()}
        if action == "judge":
            return ledger.judge(int(forecast_id), verdict, note)
        return ledger.stats()

    registry.register(
        "forecast_ledger",
        "HAYAT's own past forecasts with the date each can be checked, which are due, and how often they proved "
        "right by the owner's verdicts (right / partly / wrong / unclear). action=judge records a verdict.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["stats", "due", "recent", "judge"]},
            "forecast_id": {"type": "integer"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
            "note": {"type": "string"}}, "additionalProperties": False},
        "low", "system_read", run)
