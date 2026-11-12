"""What a conversation has established, kept so it is not lost at the next message.

The topic record (app/topics.py) says *where* a message belongs. This says what the owner is trying to reach and
what he has already been given:

* **Goals** -- what he says he wants ("become a billionaire by 2030"), with the amount and the year read from his
  words. A goal stays active across topics, days and restarts, so "ways to make money" is read against it instead
  of as a first-time question.
* **Facts about him**, in two classes that must never be mixed:

  - *permanent* facts (his name, where he is from, what he studied) do not change;
  - *circumstances* (money in hand, where he lives, his job, his computer) are true **as of the day he said
    them**. They describe where he starts. They are never a limit on what he can do, and a later statement
    replaces an earlier one for the same thing. On 2026-11-11 he had to say it himself: "dont treat this fact as
    i can change", and the reply was "tailored to your fixed hardware".

* **Replies given and replies rejected** -- every reply's fingerprint, the question it answered and what
  produced it. When he says "same reply", "wrong topic" or "too generic" (app/answer_feedback.py), that reply is
  marked rejected. A rejected reply is never sent again, however the next message is routed: the same canned
  list came back twice in three messages because nothing remembered that it had been refused.

Nothing here is inferred by a model. Goals and facts are read from the owner's own first-person sentences with
narrow patterns, and each record keeps the sentence it came from. Private-mode and child-mode turns are not
recorded (the caller decides that, as for topics).
"""
from __future__ import annotations

import hashlib
import json
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

#: The test suite stopped writing into the owner's data on 2026-09-29; earlier history holds fake test turns.
BACKFILL_SINCE = "2026-09-30"
#: How long a rejected reply stays refused for the same goal or question.
REJECTION_HORIZON = timedelta(days=45)
#: Two replies this alike are the same reply (word 3-shingles, Jaccard).
REPEAT_THRESHOLD = 0.8

PERMANENT = "permanent"
CIRCUMSTANCE = "circumstance"

# ------------------------------------------------------------------------------------------- reply fingerprints

_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def digest(text: str) -> str:
    """A fingerprint of a reply that ignores spacing, case and punctuation."""
    return hashlib.sha1(" ".join(_WORD.findall(str(text or "").lower())).encode("utf-8"),
                        usedforsecurity=False).hexdigest()


def shingles(text: str, size: int = 3, limit: int = 4000) -> frozenset[str]:
    words = _WORD.findall(str(text or "").lower())[:limit]
    if len(words) < size:
        return frozenset({" ".join(words)}) if words else frozenset()
    return frozenset(" ".join(words[index:index + size]) for index in range(len(words) - size + 1))


def alike(left: str, right: str) -> float:
    """How much two replies say the same thing in the same words (0..1)."""
    if not left or not right:
        return 0.0
    if digest(left) == digest(right):
        return 1.0
    a, b = shingles(left), shingles(right)
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


# ------------------------------------------------------------------------------------------------------- goals

_AMOUNT_WORDS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "mm": 1e6, "mil": 1e6, "million": 1e6, "millions": 1e6,
                 "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9, "t": 1e12, "trillion": 1e12, "crore": 1e7,
                 "crores": 1e7, "lakh": 1e5, "lakhs": 1e5}
_AMOUNT = re.compile(
    r"(?i)(?<![\w.])\$?\s?(\d[\d,]*(?:\.\d+)?)\s?(k|thousand|mn|mm|mil|millions?|m|bn|billions?|b|trillion|t|crores?|lakhs?)?"
    r"(?:\s?(?:dollars?|usd|\$))?(?![\w.])")
_YEAR = re.compile(r"(?i)\b(?:by|till|until|untill|before|in|within\s+the\s+year)\s+(?:the\s+(?:end|start)\s+of\s+)?(20[2-9]\d)\b")
_IN_YEARS = re.compile(r"(?i)\b(?:in|within)\s+(?:the\s+next\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\b")
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
#: Wealth targets named as a kind of person or company.
_WEALTH_NAMES = (
    (re.compile(r"(?i)\b(?:multi[\s-]?)?billion(?:n)?a(?:i)?re?s?\b|\bbillionare\b|\bbillionaire\b"), 1e9),
    (re.compile(r"(?i)\b(?:multi[\s-]?)?million(?:n)?a(?:i)?re?s?\b|\bmillionare\b"), 1e6),
    (re.compile(r"(?i)\bunicorn\b"), 1e9),
    (re.compile(r"(?i)\bbillions?\s+of\s+dollars?\b|\b(?:a|one)\s+billion\b"), 1e9),
    (re.compile(r"(?i)\bmillions?\s+of\s+dollars?\b|\b(?:a|one)\s+million\b"), 1e6),
)
_WANT = re.compile(
    r"(?i)\b(?:i\s+(?:want|wanna|need|plan|aim|intend|hope|am\s+going|['’]?m\s+going|would\s+like|['’]?d\s+like|have|got)\s+to"
    r"|i\s+(?:will|['’]ll|must|should)|my\s+(?:goal|aim|target|dream|plan|ambition|mission)\s+(?:is|was)(?:\s+to)?"
    r"|(?:help|make|let)\s+me|how\s+(?:can|do|could|should|to)\s+(?:i\s+)?|so\s+i\s+can|in\s+order\s+to|trying\s+to"
    r"|(?:want|need)\s+to|ways?\s+to|ideas?\s+to|and\s+(?:become|reach|make|earn)|to\s+(?:become|reach))\b")
_WEALTH_VERB = re.compile(
    r"(?i)\b(?:become|be|make|earn|reach|get|build|grow|hit|have|own|worth|net\s*worth|rich|wealthy|wealth|income|money|"
    r"revenue|company|business|startup|valuation)\b")


def parse_amount(text: str) -> float | None:
    """The largest money amount named in a sentence, in dollars-as-typed ("1 billion", "$10k", "billionaire")."""
    best: float | None = None
    for pattern, value in _WEALTH_NAMES:
        if pattern.search(text):
            best = max(best or 0.0, value)
    for number, unit in _AMOUNT.findall(text):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        if not unit and not re.search(rf"(?i)\$\s?{re.escape(number)}|{re.escape(number)}\s?(?:\$|dollars?|usd)", text):
            continue  # a bare number is a year, a count, a version: not money
        value *= _AMOUNT_WORDS.get(unit.lower(), 1.0) if unit else 1.0
        best = max(best or 0.0, value)
    return best


def parse_horizon(text: str, today: date | None = None) -> int | None:
    """The year a goal is due, when he names one ("by 2030", "till 2030", "in 5 years")."""
    found = _YEAR.search(text)
    if found:
        return int(found.group(1))
    found = _IN_YEARS.search(text)
    if found:
        count = _NUMBER_WORDS.get(found.group(1).lower()) or int(found.group(1))
        return (today or date.today()).year + count
    return None


def read_goal(text: str, today: date | None = None) -> dict[str, Any] | None:
    """A goal the owner states about himself, or None. Only money goals with a named size are read for now."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 500:
        return None
    amount = parse_amount(value)
    if amount is None or amount < 1e5:
        return None
    if not _WANT.search(value) or not _WEALTH_VERB.search(value):
        return None
    # "how did Bezos become a billionaire" is about someone else.
    if re.search(r"(?i)\bhow\s+(?:did|does|do|have|has)\s+(?!i\b)\w+", value) and not re.search(r"(?i)\b(?:i|me|my)\b", value):
        return None
    horizon = parse_horizon(value, today)
    return {"kind": "wealth", "amount": amount, "horizon": horizon, "statement": value[:400],
            "key": f"wealth:{int(amount)}"}


# ----------------------------------------------------------------------------------------------- facts about him

_NOW_WORDS = r"(?:now|right\s+now|currently|at\s+the\s+moment|for\s+now|atm|today|these\s+days|presently)"
#: attribute -> (class, patterns). Each pattern captures the value in group "value".
_FACT_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("name", PERMANENT, re.compile(r"(?i)\bmy\s+(?:full\s+|real\s+)?name\s+is\s+(?P<value>[A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,3})")),
    ("origin", PERMANENT, re.compile(r"(?i)\bi\s+(?:am|['’]?m|was)\s+(?:originally\s+)?(?:from|born\s+in|born\s+and\s+raised\s+in)\s+"
                                    r"(?P<value>[A-Z][\w'’-]+(?:[\s,]+[A-Z][\w'’-]+){0,2})")),
    ("education", PERMANENT, re.compile(r"(?i)\bi\s+(?:studied|graduated\s+(?:in|from|with)|have\s+a\s+(?:degree|masters?|bachelors?|phd)\s+in|"
                                       r"did\s+my\s+(?:degree|masters?|bachelors?|phd)\s+in)\s+(?P<value>[\w&+.,' -]{3,80}?)(?=[.;!?]|\s+(?:and|but|so)\s+i\b|$)")),
    ("capital", CIRCUMSTANCE, re.compile(
        r"(?i)\bi\s*(?:am|['’]?m)?\s*(?:have|got|only\s+have|just\s+have|am\s+at|['’]?m\s+at|am\s+with|start(?:ing)?\s+(?:with|from)|"
        r"at)\s+(?:only\s+|just\s+|about\s+|around\s+)?(?P<value>\$\s?\d[\d,]*(?:\.\d+)?\s?(?:k|m|million|thousand)?|\d[\d,]*(?:\.\d+)?\s?"
        r"(?:\$|dollars?|usd|k\s?(?:\$|dollars?|usd)))(?:\s+(?:with\s+me|in\s+(?:hand|cash|savings|my\s+account|the\s+bank)|to\s+(?:start|invest)|cash|saved))?")),
    ("capital", CIRCUMSTANCE, re.compile(
        r"(?i)\b(?:have|with|at|from)\s+(?P<value>0\s?\$|\$\s?0|zero\s+(?:dollars?|money)|no\s+money|no\s+capital|no\s+savings)\b")),
    ("capital", CIRCUMSTANCE, re.compile(r"(?i)\bi\s*(?:am|['’]?m)\s+(?P<value>broke|bankrupt|in\s+debt)\b")),
    ("location", CIRCUMSTANCE, re.compile(
        rf"(?i)\b(?:i\s+)?(?:am|['’]?m|live|stay|am\s+living|am\s+staying|am\s+based|['’]?m\s+based)\s+in\s+(?P<value>[A-Za-z][\w'’-]+"
        rf"(?:[\s,]+[A-Za-z][\w'’-]+){{0,2}}?)(?=\s+{_NOW_WORDS}\b|\s+and\b|\s+but\b|[.,;!?]|$)")),
    ("planned_move", CIRCUMSTANCE, re.compile(
        # First person only: "cheapest moving to california" asks about moving, it does not announce one.
        r"(?i)\b(?:(?:i\s+am|i['’]?m|am|we\s+are|after\s+[\w\s]{1,16}?)\s+(?:moving|relocating|shifting)|i\s+(?:will|['’]ll)\s+move|"
        r"(?:i\s+am|i['’]?m|am)\s+going\s+to\s+move)\s+to\s+(?P<value>[A-Za-z][\w'’-]+"
        r"(?:[\s,]+[A-Za-z][\w'’-]+){0,2}?)(?=\s+(?:and|so|in|next|after|by|this)\b|[.,;!?]|$)")),
    ("work", CIRCUMSTANCE, re.compile(
        r"(?i)\bi\s+(?:am|['’]?m|work\s+as|am\s+working\s+as)\s+(?:an?\s+|the\s+)?(?P<value>(?:[\w+#./-]+\s+){0,4}(?:engineer|developer|"
        r"programmer|student|designer|manager|analyst|scientist|researcher|consultant|founder|freelancer|teacher|doctor|nurse|driver|"
        r"architect|administrator|admin|technician|intern|trader|writer|accountant|lawyer|unemployed|jobless))\b")),
    ("work", CIRCUMSTANCE, re.compile(r"(?i)\bi\s+(?:got|was|have\s+been|['’]?ve\s+been)\s+(?P<value>laid\s+off|fired|let\s+go)\b")),
    ("visa", CIRCUMSTANCE, re.compile(r"(?i)\b(?:i\s+)?(?:am|['’]?m)\s+on\s+(?:an?\s+)?(?P<value>(?:stem\s+)?opt|cpt|h-?1b|f-?1|l-?1|o-?1|"
                                     r"green\s+card|[a-z]-?\d\s+visa|(?:student|work|tourist|visitor)\s+visa)\b")),
    ("hardware", CIRCUMSTANCE, re.compile(
        r"(?i)\bi\s+(?:have|got|own|use|am\s+using|run|am\s+running)\s+(?:an?\s+)?(?P<value>(?:nvidia\s+|geforce\s+|amd\s+|intel\s+)?"
        r"(?:rtx|gtx|rx|arc)\s?\d{3,4}(?:\s?ti|\s?super)?(?:\s+\d+\s?gb)?(?:\s+(?:and|with|,)\s+(?:ram\s+)?\d+\s?gb(?:\s+(?:of\s+)?ram)?)?)")),
    ("age", CIRCUMSTANCE, re.compile(r"(?i)\bi\s+(?:am|['’]?m)\s+(?P<value>\d{2})(?:\s+years?\s+old|\s+yo\b|\s+now\b)")),
)
#: What each circumstance is called when it is shown.
LABELS = {"name": "name", "origin": "from", "education": "studied", "capital": "money in hand", "location": "lives in",
          "planned_move": "plans to move to", "work": "work", "visa": "visa status", "hardware": "computer",
          "age": "age"}
#: Places the pattern "am in X" must not read as a location ("i am in trouble").
_NOT_A_PLACE = frozenset("""
trouble love pain debt doubt shock charge control need danger luck hurry mood progress touch line bed class school
college office home hospital jail prison town a an the my this that here there business tech it finance sales
marketing crypto stocks general fact short total favor favour agreement mode
""".split())


def read_facts(text: str) -> list[dict[str, str]]:
    """First-person facts in a message, each with its class and the words it came from."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 1200 or not re.search(r"(?i)\b(?:i|i['’]?m|my|am|have|moving)\b", value):
        return []
    found: dict[str, dict[str, str]] = {}
    for attribute, kind, pattern in _FACT_PATTERNS:
        if attribute in found:
            continue
        match = pattern.search(value)
        if not match:
            continue
        fact = " ".join(match.group("value").split()).strip(" ,.;")
        if not fact:
            continue
        if attribute in {"location", "planned_move"}:
            first = fact.split()[0].lower().strip(",")
            if first in _NOT_A_PLACE or len(fact) < 3:
                continue
        # "what if i have $1000" asks; it does not state.
        before = value[:match.start()].lower()
        if re.search(r"(?:\bif\b|\bwhat\s+if\b|\bsuppose\b|\bimagine\b|\bwhen\b|\bonce\b)[^.?!]{0,25}$", before):
            continue
        start = max(0, value.rfind(".", 0, match.start()) + 1)
        end = value.find(".", match.end())
        sentence = value[start:end if end != -1 else len(value)].strip()[:300]
        found[attribute] = {"attribute": attribute, "value": fact[:120], "kind": kind, "statement": sentence}
    return list(found.values())


# ------------------------------------------------------------------------------------------------------- store


def _now() -> datetime:
    return datetime.now(UTC)


class DialogueState:
    """Goals, facts about the owner, and the replies he was given and refused (data/memory/dialogue.db)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._ready = False
        #: Publishes ids and counts only (Brain 5D); set by the runtime.
        self.events: Any = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS goals(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    amount REAL, horizon INTEGER, statement TEXT NOT NULL, first_at TEXT NOT NULL, last_at TEXT NOT NULL,
                    times INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'active');
                CREATE TABLE IF NOT EXISTS facts(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, attribute TEXT NOT NULL, value TEXT NOT NULL, kind TEXT NOT NULL,
                    statement TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', at TEXT NOT NULL,
                    current INTEGER NOT NULL DEFAULT 1);
                CREATE INDEX IF NOT EXISTS facts_current ON facts(attribute, current);
                CREATE TABLE IF NOT EXISTS replies(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, topic_id TEXT NOT NULL DEFAULT '',
                    request TEXT NOT NULL, reply TEXT NOT NULL, digest TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',
                    goal_key TEXT NOT NULL DEFAULT '', at TEXT NOT NULL, rejected INTEGER NOT NULL DEFAULT 0,
                    rejected_at TEXT NOT NULL DEFAULT '', rejected_as TEXT NOT NULL DEFAULT '', complaint TEXT NOT NULL DEFAULT '',
                    repeat_of INTEGER);
                CREATE INDEX IF NOT EXISTS replies_session ON replies(session_id, id);
                CREATE INDEX IF NOT EXISTS replies_rejected ON replies(rejected, digest);
                CREATE TABLE IF NOT EXISTS notes(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            self._ready = True
        return db

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(event_type, "dialogue", payload)
        except Exception:
            log_swallowed(_log, "publishing a dialogue-state event")

    # ---- what he says about himself

    def observe(self, session_id: str, text: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Read one owner message for a goal and for facts about him; returns what was kept."""
        now = now or _now()
        kept: dict[str, Any] = {"goal": None, "facts": []}
        from app.answer_feedback import read as read_feedback

        # "i didn't ask about X, i was asking you to <goal>": the goal is the restated part, not the complaint.
        feedback = read_feedback(text)
        goal = read_goal(feedback.restated if feedback is not None and feedback.restated else text, now.date())
        facts = read_facts(text)
        if goal is None and not facts:
            return kept
        with self._lock, closing(self._db()) as db, db:
            if goal is not None:
                row = db.execute("SELECT * FROM goals WHERE key=?", (goal["key"],)).fetchone()
                if row is None:
                    db.execute("INSERT INTO goals(key,kind,amount,horizon,statement,first_at,last_at) VALUES(?,?,?,?,?,?,?)",
                               (goal["key"], goal["kind"], goal["amount"], goal["horizon"], goal["statement"],
                                now.isoformat(), now.isoformat()))
                else:
                    # A later statement with a year replaces a missing or older year; the wording is the latest.
                    db.execute("UPDATE goals SET times=times+1, last_at=?, statement=?, horizon=COALESCE(?, horizon), "
                               "status='active' WHERE key=?", (now.isoformat(), goal["statement"], goal["horizon"], goal["key"]))
                kept["goal"] = goal
            for fact in facts:
                current = db.execute("SELECT * FROM facts WHERE attribute=? AND current=1 ORDER BY id DESC LIMIT 1",
                                     (fact["attribute"],)).fetchone()
                if current is not None and current["value"].lower() == fact["value"].lower():
                    db.execute("UPDATE facts SET at=?, statement=? WHERE id=?", (now.isoformat(), fact["statement"], current["id"]))
                    continue
                if current is not None and current["kind"] == PERMANENT and _parse(current["at"]) and now < _parse(current["at"]):
                    continue
                db.execute("UPDATE facts SET current=0 WHERE attribute=? AND current=1", (fact["attribute"],))
                db.execute("INSERT INTO facts(attribute,value,kind,statement,session_id,at) VALUES(?,?,?,?,?,?)",
                           (fact["attribute"], fact["value"], fact["kind"], fact["statement"], session_id, now.isoformat()))
                kept["facts"].append(fact)
        if kept["goal"] or kept["facts"]:
            self._emit("DIALOGUE_OBSERVED", {"goal": bool(kept["goal"]), "facts": len(kept["facts"])})
        return kept

    def goals(self, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM goals WHERE status='active' ORDER BY last_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def main_goal(self) -> dict[str, Any] | None:
        """The goal that sets the scale: the largest active one."""
        found = self.goals(limit=10)
        return max(found, key=lambda item: float(item.get("amount") or 0)) if found else None

    def close_goal(self, key: str, status: str = "dropped") -> bool:
        with self._lock, closing(self._db()) as db, db:
            return db.execute("UPDATE goals SET status=? WHERE key=? OR CAST(id AS TEXT)=?", (status, key, key)).rowcount > 0

    def facts(self) -> dict[str, list[dict[str, Any]]]:
        """Current facts about the owner, split into permanent facts and dated circumstances."""
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM facts WHERE current=1 ORDER BY attribute").fetchall()
        result: dict[str, list[dict[str, Any]]] = {PERMANENT: [], CIRCUMSTANCE: []}
        for row in rows:
            result[row["kind"] if row["kind"] in result else CIRCUMSTANCE].append(dict(row))
        return result

    def fact_history(self, attribute: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM facts WHERE attribute=? ORDER BY id DESC LIMIT ?", (attribute, limit)).fetchall()
        return [dict(row) for row in rows]

    def forget_fact(self, fact_id: int) -> bool:
        """The owner's control: a fact read wrongly is removed, not argued with."""
        with self._lock, closing(self._db()) as db, db:
            return db.execute("DELETE FROM facts WHERE id=?", (int(fact_id),)).rowcount > 0

    # ---- replies given, replies refused

    def note_reply(self, session_id: str, request: str, reply: str, *, topic_id: str = "", source: str = "",
                   now: datetime | None = None) -> int:
        """Keep a reply's fingerprint with the question it answered; returns its id (0 when nothing to keep)."""
        request, reply = " ".join(str(request or "").split()), str(reply or "").strip()
        if not reply or not session_id:
            return 0
        now = now or _now()
        mark = digest(reply)
        goal = self.main_goal()
        with self._lock, closing(self._db()) as db, db:
            earlier = db.execute("SELECT id FROM replies WHERE session_id=? AND digest=? AND at>=? ORDER BY id DESC LIMIT 1",
                                 (session_id, mark, (now - timedelta(days=1)).isoformat())).fetchone()
            cursor = db.execute(
                "INSERT INTO replies(session_id,topic_id,request,reply,digest,source,goal_key,at,repeat_of) VALUES(?,?,?,?,?,?,?,?,?)",
                (session_id, topic_id, request[:1500], reply[:6000], mark, source[:120],
                 goal["key"] if goal else "", now.isoformat(), earlier["id"] if earlier else None))
            return int(cursor.lastrowid or 0)

    def last_reply(self, session_id: str, *, topic_id: str = "", within: timedelta = timedelta(hours=6),
                   now: datetime | None = None) -> dict[str, Any] | None:
        """The latest reply in this session (in this topic when given) that is recent enough to be 'the last answer'."""
        now = now or _now()
        with self._lock, closing(self._db()) as db:
            row = None
            if topic_id:
                row = db.execute("SELECT * FROM replies WHERE session_id=? AND topic_id=? AND at>=? ORDER BY id DESC LIMIT 1",
                                 (session_id, topic_id, (now - within).isoformat())).fetchone()
            if row is None:
                row = db.execute("SELECT * FROM replies WHERE session_id=? AND at>=? ORDER BY id DESC LIMIT 1",
                                 (session_id, (now - within).isoformat())).fetchone()
        return dict(row) if row is not None else None

    def reject(self, session_id: str, kinds: Any, complaint: str, *, topic_id: str = "",
               now: datetime | None = None) -> dict[str, Any] | None:
        """Mark the last answer refused; returns that reply (with the question it answered), or None.

        Every earlier copy of the same reply in this session is marked too: it was the copy he complained about.
        """
        now = now or _now()
        last = self.last_reply(session_id, topic_id=topic_id, now=now)
        if last is None:
            return None
        label = ",".join(sorted(kinds)) if not isinstance(kinds, str) else kinds
        with self._lock, closing(self._db()) as db, db:
            db.execute("UPDATE replies SET rejected=1, rejected_at=?, rejected_as=?, complaint=? "
                       "WHERE session_id=? AND digest=?", (now.isoformat(), label, str(complaint)[:500], session_id, last["digest"]))
        self._emit("ANSWER_REJECTED", {"reply_id": last["id"], "kinds": label, "source": last["source"]})
        return {**last, "rejected": 1, "rejected_as": label}

    def reject_reply(self, session_id: str, reply: str, kinds: Any, complaint: str = "", *,
                     now: datetime | None = None) -> dict[str, Any] | None:
        """Mark one named reply refused (the 👎 under it in the chat), whether or not it was the last one."""
        mark = digest(reply)
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT * FROM replies WHERE session_id=? AND digest=? ORDER BY id DESC LIMIT 1",
                             (session_id, mark)).fetchone()
        if row is None:
            return None
        now = now or _now()
        label = ",".join(sorted(kinds)) if not isinstance(kinds, str) else kinds
        with self._lock, closing(self._db()) as db, db:
            db.execute("UPDATE replies SET rejected=1, rejected_at=?, rejected_as=?, complaint=? WHERE session_id=? "
                       "AND digest=?", (now.isoformat(), label, str(complaint)[:500], session_id, mark))
        self._emit("ANSWER_REJECTED", {"reply_id": row["id"], "kinds": label, "source": row["source"]})
        return {**dict(row), "rejected": 1, "rejected_as": label}

    def first_request(self, rejected: dict[str, Any]) -> str:
        """The question a refused reply first answered: the earliest request it was given for in this session.

        "this is same reply" complains about the second copy; the question is the one the first copy answered,
        unless the second request was itself a real request and not a complaint.
        """
        from app.answer_feedback import read as read_feedback

        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT request FROM replies WHERE session_id=? AND digest=? ORDER BY id DESC LIMIT 12",
                              (rejected["session_id"], rejected["digest"])).fetchall()
        for row in rows:
            request = row["request"]
            feedback = read_feedback(request)
            if feedback is not None and feedback.restated:
                return feedback.restated
            if request and (feedback is None or not feedback.about_answer):
                return request
        return str(rejected.get("request") or "")

    def rejected(self, session_id: str = "", *, topic_id: str = "", goal_key: str = "", limit: int = 6,
                 now: datetime | None = None) -> list[dict[str, Any]]:
        """Replies the owner refused that still apply here: same topic, same goal, or this session, newest first."""
        now = now or _now()
        since = (now - REJECTION_HORIZON).isoformat()
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM replies WHERE rejected=1 AND rejected_at>=? ORDER BY id DESC LIMIT 200",
                              (since,)).fetchall()
        seen: set[str] = set()
        found: list[dict[str, Any]] = []
        for row in rows:
            if row["digest"] in seen:
                continue
            applies = ((topic_id and row["topic_id"] == topic_id) or (goal_key and row["goal_key"] == goal_key)
                       or (session_id and row["session_id"] == session_id and not topic_id and not goal_key))
            if applies:
                seen.add(row["digest"])
                found.append(dict(row))
            if len(found) >= limit:
                break
        return found

    def refused_copy(self, reply: str, *, session_id: str = "", now: datetime | None = None) -> dict[str, Any] | None:
        """The refused reply this one repeats (same words, or nearly), or None. Any session: a list he refused
        yesterday is the same list today."""
        reply = str(reply or "").strip()
        if not reply:
            return None
        now = now or _now()
        since = (now - REJECTION_HORIZON).isoformat()
        mark = digest(reply)
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT * FROM replies WHERE rejected=1 AND rejected_at>=? AND digest=? ORDER BY id DESC LIMIT 1",
                             (since, mark)).fetchone()
            if row is not None:
                return {**dict(row), "alike": 1.0}
            candidates = db.execute("SELECT * FROM replies WHERE rejected=1 AND rejected_at>=? GROUP BY digest "
                                    "ORDER BY id DESC LIMIT 40", (since,)).fetchall()
        for row in candidates:
            score = alike(reply, row["reply"])
            if score >= REPEAT_THRESHOLD:
                return {**dict(row), "alike": score}
        return None

    def refused_sources(self, *, topic_id: str = "", goal_key: str = "", session_id: str = "",
                        now: datetime | None = None) -> set[str]:
        """What produced the refused replies (a tool name, 'brain', ...): not to be asked the same way again."""
        return {row["source"] for row in self.rejected(session_id, topic_id=topic_id, goal_key=goal_key, limit=12, now=now)
                if row["source"]}

    # ---- how far a goal's evaluation has gone

    #: After this long without an evaluation, the next one measures the goal again from the start.
    ROUND_RESET = timedelta(days=3)

    def note(self, key: str) -> Any:
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT value FROM notes WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["value"])
        except ValueError:
            return None

    def set_note(self, key: str, value: Any) -> None:
        with self._lock, closing(self._db()) as db, db:
            db.execute("INSERT OR REPLACE INTO notes(key,value) VALUES(?,?)", (key, json.dumps(value)))

    def next_round(self, goal_key: str, *, now: datetime | None = None) -> int:
        """Which look at this goal the next evaluation is (1 = measure it; later = go further), and count it."""
        now = now or _now()
        record = self.note(f"round:{goal_key}") or {}
        last = _parse(record.get("at"))
        number = 1 if last is None or now - last > self.ROUND_RESET else int(record.get("round", 0)) + 1
        self.set_note(f"round:{goal_key}", {"round": number, "at": now.isoformat()})
        return number

    def asked(self, goal_key: str) -> list[str]:
        """Questions already researched for this goal (never asked twice)."""
        return list(self.note(f"asked:{goal_key}") or [])

    def note_asked(self, goal_key: str, questions: list[str]) -> None:
        if questions:
            self.set_note(f"asked:{goal_key}", list(dict.fromkeys([*self.asked(goal_key), *questions]))[-200:])

    # ---- for the reply writer

    def brief(self, session_id: str, text: str = "", *, topic_id: str = "", today: date | None = None) -> str:
        """What the reply writer must know before answering: goals, facts in their two classes, refused answers."""
        today = today or _now().date()
        lines: list[str] = []
        goals = self.goals(limit=3)
        if goals:
            lines.append("Goals the owner has stated and not withdrawn (judge any advice against their size and date; "
                         "do not shrink the goal to fit what is easy to suggest):")
            for goal in goals:
                due = f", by {goal['horizon']}" if goal.get("horizon") else ""
                lines.append(f"- “{goal['statement'][:200]}” (amount {_money(goal.get('amount'))}{due}; said "
                             f"{goal['times']} time(s), last on {str(goal['last_at'])[:10]})")
        facts = self.facts()
        if facts[PERMANENT]:
            lines.append("Permanent facts about him (do not change):")
            lines += [f"- {LABELS.get(row['attribute'], row['attribute'])}: {row['value']}" for row in facts[PERMANENT]]
        if facts[CIRCUMSTANCE]:
            lines.append("His circumstances as he stated them, each with its date. These can change and he can change "
                         "them: they describe where he starts today. Never present one as fixed, and never limit an "
                         "answer to what fits them unless he asks for that:")
            for row in facts[CIRCUMSTANCE]:
                age = (today - _parse(row["at"]).date()).days if _parse(row["at"]) else None
                stale = " — may be out of date, ask before relying on it" if age is not None and age > 60 else ""
                lines.append(f"- {LABELS.get(row['attribute'], row['attribute'])}: {row['value']} "
                             f"(said {str(row['at'])[:10]}{stale})")
        goal = self.main_goal()
        refused = self.rejected(session_id, topic_id=topic_id, goal_key=goal["key"] if goal else "", limit=4)
        if refused:
            lines.append("Answers he refused. Do not give any of them again, reworded or not; say what is different "
                         "this time:")
            for row in refused:
                lines.append(f"- [{row['rejected_as']}] to “{row['request'][:120]}”: “{row['reply'][:160]}…”"
                             + (f" (from {row['source']})" if row["source"] else ""))
        return "\n".join(lines)

    # ---- measures

    def measures(self, days: int = 30, *, now: datetime | None = None) -> dict[str, Any]:
        """How often replies repeated an earlier one and how often they were refused, over a window."""
        now = now or _now()
        since = (now - timedelta(days=days)).isoformat()
        with self._lock, closing(self._db()) as db:
            total = db.execute("SELECT COUNT(*) FROM replies WHERE at>=?", (since,)).fetchone()[0]
            repeats = db.execute("SELECT COUNT(*) FROM replies WHERE at>=? AND repeat_of IS NOT NULL", (since,)).fetchone()[0]
            refused = db.execute("SELECT COUNT(*) FROM replies WHERE at>=? AND rejected=1", (since,)).fetchone()[0]
            resent = db.execute(
                "SELECT COUNT(*) FROM replies r WHERE r.at>=? AND EXISTS (SELECT 1 FROM replies e WHERE e.digest=r.digest "
                "AND e.rejected=1 AND e.rejected_at<>'' AND e.rejected_at<r.at)", (since,)).fetchone()[0]
            kinds = db.execute("SELECT rejected_as, COUNT(*) n FROM replies WHERE at>=? AND rejected=1 GROUP BY rejected_as "
                               "ORDER BY n DESC", (since,)).fetchall()
        return {"days": days, "replies": total, "repeats_of_an_earlier_reply": repeats,
                "repeat_rate": round(repeats / total, 4) if total else 0.0, "refused": refused,
                "refused_rate": round(refused / total, 4) if total else 0.0,
                "sent_again_after_being_refused": resent, "refused_as": {row["rejected_as"]: row["n"] for row in kinds}}

    def status(self) -> dict[str, Any]:
        """For the Brain view: a real check that the store answers."""
        with self._lock, closing(self._db()) as db:
            goals = db.execute("SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
            facts = db.execute("SELECT COUNT(*) FROM facts WHERE current=1").fetchone()[0]
            replies, refused = db.execute("SELECT COUNT(*), COALESCE(SUM(rejected),0) FROM replies").fetchone()
        return {"ok": True, "goals": goals, "facts": facts, "replies": replies, "refused": refused}

    def report(self, session_id: str = "") -> dict[str, Any]:
        return {"goals": self.goals(limit=10), "facts": self.facts(), "refused": self.rejected(session_id, limit=10),
                "measures": self.measures()}

    # ---- learning from the conversations already on disk

    def backfill(self, memory: Any, *, since: str = BACKFILL_SINCE, limit: int = 20000) -> dict[str, int]:
        """Read the owner's earlier messages once for goals and facts about him (never replies or rejections)."""
        with self._lock, closing(self._db()) as db, db:
            if db.execute("SELECT 1 FROM notes WHERE key='backfilled'").fetchone():
                return {"goals": 0, "facts": 0}
            db.execute("INSERT OR REPLACE INTO notes(key,value) VALUES('backfilled',?)", (_now().isoformat(),))
        counted = {"goals": 0, "facts": 0}
        try:
            with memory.connect() as source:
                rows = source.execute("SELECT session_id, timestamp, content FROM conversations WHERE role='user' AND "
                                      "timestamp>=? ORDER BY id LIMIT ?", (since, limit)).fetchall()
        except Exception:
            log_swallowed(_log, "reading earlier conversations for goals and facts")
            return counted
        for row in rows:
            moment = _parse(row[1]) or _now()
            kept = self.observe(str(row[0]), str(row[2]), now=moment)
            counted["goals"] += int(bool(kept["goal"]))
            counted["facts"] += len(kept["facts"])
        return counted


def _parse(moment: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(moment))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _money(amount: Any) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "not stated"
    for size, name in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if value >= size:
            scaled = value / size
            return f"${scaled:g} {name}"
    return f"${value:,.0f}"


# ---------------------------------------------------------------------------------------------------- the tool

_STATE_QUESTION = re.compile(
    r"(?i)\b(?:what\s+(?:are|is)\s+my\s+(?:goals?|circumstances|situation)\b|what\s+do\s+(?:you|u)\s+know\s+about\s+my\s+"
    r"(?:goals?|situation|circumstances)|which\s+answers?\s+(?:did|have)\s+i\s+(?:reject|refuse)|what\s+(?:did|have)\s+i\s+"
    r"(?:reject|refuse)(?:ed)?|show\s+(?:me\s+)?(?:my\s+)?(?:dialogue|conversation)\s+state|what\s+(?:answers?|replies)\s+"
    r"(?:did|have)\s+i\s+(?:reject|refuse)(?:ed)?|how\s+often\s+do\s+(?:you|u)\s+repeat\s+(?:yourself|answers?))\b")


def state_request(text: str) -> bool:
    return bool(_STATE_QUESTION.search(" ".join(str(text or "").split())))


def direct_state_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if "dialogue_state" not in names or not state_request(text):
        return None
    return [{"description": "Report the goals, facts and refused answers HAYAT keeps for this conversation.",
             "tool": "dialogue_state", "arguments": {}}]


def format_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    goals = report.get("goals") or []
    lines.append("Goals I'm holding for you:" if goals else "I'm not holding any goal for you yet. Tell me one "
                 "(“I want to …”) and I'll keep it across conversations.")
    for goal in goals:
        due = f", by {goal['horizon']}" if goal.get("horizon") else ""
        lines.append(f"- {goal['statement'][:160]} ({_money(goal.get('amount'))}{due}; said {goal['times']} time(s))")
    facts = report.get("facts") or {}
    if facts.get(PERMANENT):
        lines += ["", "Permanent facts:"] + [f"- {LABELS.get(row['attribute'], row['attribute'])}: {row['value']}"
                                            for row in facts[PERMANENT]]
    if facts.get(CIRCUMSTANCE):
        lines += ["", "Circumstances (true when you said them; they can change, so I treat them as a starting "
                  "point, not a limit):"]
        lines += [f"- {LABELS.get(row['attribute'], row['attribute'])}: {row['value']} (said {str(row['at'])[:10]})"
                  for row in facts[CIRCUMSTANCE]]
    refused = report.get("refused") or []
    if refused:
        lines += ["", "Answers you refused (I won't send these again):"]
        lines += [f"- {row['rejected_as']}: “{row['reply'][:110]}…” on {str(row['rejected_at'])[:10]}" for row in refused]
    measures = report.get("measures") or {}
    if measures.get("replies"):
        lines += ["", f"Last {measures['days']} days: {measures['replies']} replies, "
                  f"{measures['repeats_of_an_earlier_reply']} repeated an earlier reply word for word, "
                  f"{measures['refused']} were refused, {measures['sent_again_after_being_refused']} were sent again "
                  "after being refused."]
    return "\n".join(lines)


def register_dialogue_tools(registry: Any, state: DialogueState, session: Any) -> None:
    def run(action: str = "report", fact_id: int = 0, goal: str = "") -> dict[str, Any]:
        current = session() if callable(session) else session
        session_id = str(getattr(current, "session_id", "") or "")
        if action == "forget_fact":
            return {"removed": state.forget_fact(int(fact_id))}
        if action == "drop_goal":
            return {"dropped": state.close_goal(str(goal))}
        report = state.report(session_id)
        return {**report, "text": format_report(report)}

    registry.register(
        "dialogue_state",
        "Report what HAYAT keeps about the conversation itself: the goals the owner stated (with amount and year), "
        "facts about him split into permanent facts and dated circumstances that can change, the answers he "
        "refused (never sent again), and how often replies repeated or were refused. action 'forget_fact' with "
        "fact_id removes a fact read wrongly; 'drop_goal' with goal closes a goal. Reads and edits only this record.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["report", "forget_fact", "drop_goal"]},
            "fact_id": {"type": "integer"}, "goal": {"type": "string"}}, "additionalProperties": False},
        "low", "system_read", run,
    )


__all__ = ["DialogueState", "alike", "digest", "read_facts", "read_goal", "parse_amount", "parse_horizon",
           "register_dialogue_tools", "format_report", "state_request", "direct_state_plan", "PERMANENT",
           "CIRCUMSTANCE"]
