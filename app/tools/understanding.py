"""What HAYAT knows about its own tools, from having used them.

Every tool arrives with a description written by whoever registered it, and
that description is a claim about what the tool is for. It is not knowledge of
how the tool actually behaves on this machine -- whether it works, how long it
takes, what it fails with when it fails. HAYAT had the claims and nothing
else, so asked "how do your tools work" it could only read the labels back.

Worse, the absence was load-bearing. `SelfImprovement` declares
FAILURE_RATE_THRESHOLD = 0.34 and has never once been able to use it: failures
were counted, attempts were not, and a rate needs a denominator. A tool that
failed four times out of four and a tool that failed four times out of two
hundred were the same fact. This supplies the denominator.

The record is deliberately thin. Tool name, whether the call succeeded, how
long it took, and the exception type when it did not -- never the arguments
and never the output, because tool arguments are where the private things are:
the file paths, the search text, the command lines. Knowing that
`read_file` succeeds in 4ms is useful. Knowing which file was read is
surveillance, and this database is not the place for it.

A failure also keeps a scrubbed form of its message. The type alone could not
explain seven `browser_navigate` RuntimeErrors, so the self-review flagged the
tool and nobody could say why. Paths, URLs, email addresses, quoted text and
anything secret-shaped are replaced before it is written, which keeps
"Target page has been closed" and drops whose page it was.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


#: Beneath this many calls, a failure rate is noise rather than a pattern.
#: Two failures out of two is not evidence that a tool is broken; it is
#: evidence that it has barely been used.
MINIMUM_CALLS_FOR_A_VERDICT = 5
RECENT_ATTEMPTS = 20

#: A tool slower than this is worth mentioning when explaining it, so a plan
#: that chains six of them is not a surprise when it takes a minute.
SLOW_MILLISECONDS = 3000


class ToolUnderstanding:
    """A durable record of how each tool has actually behaved.

    Writes are best-effort and never raise into a tool call: a tool must not
    fail because bookkeeping about tools failed. Reads are honest about
    knowing nothing, which is the correct answer for a tool never used.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else PROJECT_ROOT / "data" / "tool_understanding.db"
        self._lock = threading.RLock()
        self._broken = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._db() as db:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS tool_calls (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tool TEXT NOT NULL,
                        ok INTEGER NOT NULL,
                        ms INTEGER NOT NULL DEFAULT 0,
                        error_kind TEXT NOT NULL DEFAULT '',
                        refused INTEGER NOT NULL DEFAULT 0,
                        at TEXT NOT NULL,
                        error_detail TEXT NOT NULL DEFAULT ''
                    )
                """)
                columns = {row[1] for row in db.execute("PRAGMA table_info(tool_calls)")}
                if "error_detail" not in columns:
                    db.execute("ALTER TABLE tool_calls ADD COLUMN error_detail TEXT NOT NULL DEFAULT ''")
                db.execute("CREATE INDEX IF NOT EXISTS idx_tool ON tool_calls(tool)")
        except sqlite3.DatabaseError:
            # A missing record is a missing opinion, not a broken assistant.
            self._broken = True

    # ------------------------------------------------------------- recording

    def observe(self, result: Any) -> None:
        """Record one tool call. Never raises."""
        if self._broken:
            return
        try:
            name = str(getattr(result, "tool", "") or "")
            if not name:
                return
            ok = bool(getattr(result, "success", False))
            error = str(getattr(result, "error", "") or "")
            # "PolicyRefusal" is not a fault in the tool. A tool refused by the
            # authority kernel worked exactly as designed, and counting that
            # against its reliability would teach HAYAT that its own safety
            # boundary is a defect to be engineered around.
            refused = (not ok) and str(getattr(result, "permission", "")) in {"DENY", "ASK", "BUDGET"}
            with self._lock, self._db() as db:
                db.execute(
                    "INSERT INTO tool_calls (tool, ok, ms, error_kind, refused, at, error_detail) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, int(ok), int(getattr(result, "duration_ms", 0) or 0),
                     _error_kind(error), int(refused), datetime.now(UTC).isoformat(),
                     "" if ok or refused else _error_detail(error)),
                )
        except Exception:
            log_swallowed(_log, "storing a tool call statistic", level=logging.WARNING)

    # -------------------------------------------------------------- reading

    def record_for(self, name: str) -> dict[str, Any]:
        """What using this tool has been like. Empty dict if never used."""
        if self._broken:
            return {}
        try:
            with self._lock, self._db() as db:
                row = db.execute(
                    "SELECT COUNT(*) AS calls, "
                    "       SUM(ok) AS succeeded, "
                    "       SUM(CASE WHEN refused THEN 1 ELSE 0 END) AS refused, "
                    "       AVG(ms) AS avg_ms, MAX(ms) AS max_ms "
                    "FROM tool_calls WHERE tool = ?", (name,),
                ).fetchone()
                kinds = db.execute(
                    "SELECT error_kind, COUNT(*) AS n FROM tool_calls "
                    "WHERE tool = ? AND ok = 0 AND refused = 0 AND error_kind <> '' "
                    "GROUP BY error_kind ORDER BY n DESC LIMIT 3", (name,),
                ).fetchall()
                recent = db.execute(
                    'SELECT ok, at, error_kind, error_detail FROM tool_calls WHERE tool = ? AND refused = 0 ORDER BY id DESC LIMIT ?',
                    (name, RECENT_ATTEMPTS),
                ).fetchall()
                # The most recent scrubbed message for each kind: recent,
                # because a cause that has since been fixed should not be the
                # one quoted back.
                examples = {
                    kind: db.execute(
                        "SELECT error_detail FROM tool_calls WHERE tool = ? AND error_kind = ? "
                        "AND ok = 0 AND refused = 0 AND error_detail <> '' ORDER BY id DESC LIMIT 1",
                        (name, kind),
                    ).fetchone()
                    for kind, _ in kinds
                }
        except sqlite3.DatabaseError:
            return {}
        calls = int(row[0] or 0)
        if not calls:
            return {}
        succeeded, refused = int(row[1] or 0), int(row[2] or 0)
        # Refusals are excluded from both sides of the ratio: they say
        # something about the boundary, not about whether the tool works.
        attempted = calls - refused
        failed = attempted - succeeded
        recent_failures = {}
        for ok, _at, kind, detail in recent:
            if not ok and kind:
                entry = recent_failures.setdefault(kind, {'kind': kind, 'count': 0, 'example': detail})
                entry['count'] += 1
        return {
            "tool": name, "calls": calls, "succeeded": succeeded, "failed": failed,
            "refused": refused,
            "failure_rate": round(failed / attempted, 3) if attempted else 0.0,
            "avg_ms": int(row[3] or 0), "max_ms": int(row[4] or 0),
            "typical_failures": [
                {"kind": kind, "count": n, "example": (examples.get(kind) or ("",))[0]}
                for kind, n in kinds
            ],
            "enough_evidence": attempted >= MINIMUM_CALLS_FOR_A_VERDICT,
            "recent_attempts": len(recent), "recent_failed": sum(not item[0] for item in recent),
            "recent_failure_rate": round(sum(not item[0] for item in recent) / len(recent), 3) if recent else 0.0,
            "recent_enough_evidence": len(recent) >= MINIMUM_CALLS_FOR_A_VERDICT,
            "last_attempt_at": recent[0][1] if recent else None,
            "recent_typical_failures": sorted(recent_failures.values(), key=lambda item: item['count'], reverse=True)[:3],
        }

    def totals(self) -> list[dict[str, Any]]:
        """Every tool that has ever been used, busiest first."""
        if self._broken:
            return []
        try:
            with self._lock, self._db() as db:
                names = [row[0] for row in db.execute(
                    "SELECT tool, COUNT(*) AS n FROM tool_calls GROUP BY tool ORDER BY n DESC",
                ).fetchall()]
        except sqlite3.DatabaseError:
            return []
        return [record for record in (self.record_for(name) for name in names) if record]

    def unreliable(self, threshold: float) -> list[dict[str, Any]]:
        """Tools failing often enough, and used often enough, to mean it."""
        return [record for record in self.totals()
                if record["recent_enough_evidence"] and record["recent_failure_rate"] >= threshold]

    # -------------------------------------------------------------- speaking

    def explain(self, name: str, spec: Any = None) -> str:
        """What this tool is for, and what using it has actually been like."""
        purpose = str(getattr(spec, "description", "") or "").strip()
        risk = str(getattr(spec, "risk", "") or "")
        permission = str(getattr(spec, "permission", "") or "")
        lines = [f"{name} -- {purpose}" if purpose else name]
        if risk or permission:
            lines.append(f"  treated as {risk or 'unclassified'}"
                         + (f", gated by {permission}" if permission else ""))

        record = self.record_for(name)
        if not record:
            lines.append("  I have never actually used this one, so I only know what it claims to do.")
            return "\n".join(lines)

        attempted = record["calls"] - record["refused"]
        if attempted:
            lines.append(
                f"  used {record['calls']} times: {record['succeeded']} worked, "
                f"{record['failed']} failed"
                + (f", {record['refused']} stopped by permission" if record["refused"] else "")
            )
        else:
            lines.append(f"  asked for {record['calls']} times and stopped by permission every time")
        if record["avg_ms"]:
            slow = " (slow enough to be worth planning around)" if record["avg_ms"] >= SLOW_MILLISECONDS else ""
            lines.append(f"  usually takes about {record['avg_ms']}ms{slow}")
        if record["typical_failures"]:
            worst = record["typical_failures"][0]
            lines.append(f"  when it fails it is usually {worst['kind']} ({worst['count']} times)"
                         + (f", most recently: {worst['example']}" if worst.get("example") else ""))
        if record['recent_attempts']:
            lines.append(f"  last {record['recent_attempts']} attempts: {record['recent_failed']} failed; "
                         f"last observed {record['last_attempt_at']}")
        if record["recent_enough_evidence"] and record["recent_failure_rate"] >= 0.34:
            lines.append("  I do not trust this one; it fails often enough that I plan around it.")
        return "\n".join(lines)

    def overview(self, registry: Any = None, limit: int = 12) -> str:
        """A plain answer to 'what tools do you have and do they work'."""
        specs = {}
        if registry is not None:
            try:
                specs = {item["name"]: item for item in registry.describe()}
            except Exception:
                specs = {}
        used = self.totals()
        if not used and not specs:
            return "I have no tools registered right now."

        lines = []
        if specs:
            lines.append(f"I have {len(specs)} tools registered.")
        if not used:
            lines.append("I have not used any of them yet, so all I know is what they say they do.")
            lines.extend(f"  {name} -- {str(item.get('description', ''))[:80]}"
                         for name, item in list(specs.items())[:limit])
            return "\n".join(lines)

        lines.append(f"{len(used)} of them I have actually used. Busiest first:")
        for record in used[:limit]:
            verdict = ("reliable recently" if record["recent_failure_rate"] < 0.1 else
                       "patchy recently" if record["recent_failure_rate"] < 0.34 else "unreliable recently")
            if not record["recent_enough_evidence"]:
                verdict = "too early to say"
            lines.append(
                f"  {record['tool']:<24} {record['calls']:>4} calls  "
                f"{record['avg_ms']:>5}ms  {verdict}"
                + (f"  ({record['refused']} refused)" if record["refused"] else "")
            )
        never = [name for name in specs if name not in {r["tool"] for r in used}]
        if never:
            lines.append(f"Never used: {', '.join(sorted(never)[:10])}"
                         + (f" and {len(never) - 10} more" if len(never) > 10 else ""))
        return "\n".join(lines)

    # -------------------------------------------------------------- plumbing

    @contextmanager
    def _db(self) -> Any:
        """Open, commit, and close -- see the note in learning/continuous.py."""
        db = sqlite3.connect(self.path, timeout=15.0)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=15000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def _error_kind(error: str) -> str:
    """The exception type, without the message.

    The message is where the private detail lives -- a path, a hostname, a
    line of the user's text quoted back inside a ValueError. The type alone is
    enough to say "this usually fails with a timeout" and carries none of it.
    """
    head = str(error or "").split(":", 1)[0].strip()
    return head[:60] if head and head.replace("_", "").isalnum() else ""


#: Replaced before a failure message is stored: whatever names a place, a
#: person or the user's own words. Order matters -- URLs before paths.
_SCRUB = (
    (re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+"), "<url>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # Windows paths may contain spaces ("C:\Users\mohsin khan\..."), so a
    # path runs until a quote, comma, semicolon or line end. Over-scrubbing a
    # trailing word is the safe direction.
    (re.compile(r"""(?i)\b[a-z]:[\\/][^"'<>|,;\r\n]*"""), "<path>"),
    (re.compile(r"""\\\\[^"'<>|,;\r\n]+"""), "<path>"),
    (re.compile(r"(?<![\w<])/(?:[\w.-]+/)+[\w.-]*"), "<path>"),
    (re.compile("'[^']*'|\"[^\"]*\"|\u201c[^\u201d]*\u201d"), "<text>"),
)


def _error_detail(error: str, limit: int = 160) -> str:
    """The failure message with everything private taken out."""
    from app.safety.sensitive import redact_text

    text = str(error or "")
    kind, _, message = text.partition(":")
    message = message if message and kind.strip().replace("_", "").isalnum() else text
    message, _ = redact_text(" ".join(message.split()), include_digest=False)
    for pattern, placeholder in _SCRUB:
        message = pattern.sub(placeholder, message)
    return message.strip()[:limit]
