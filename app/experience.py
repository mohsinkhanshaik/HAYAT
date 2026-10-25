"""What HAYAT learned from doing things: what worked, what failed, and what fixed it.

Before this, the planner was told about past tasks only when they had
succeeded -- `_planning_memories` dropped every failed episode -- and the
reflection written after a failure said only "inspect the failure before
retrying". So a mistake was recorded and then never consulted: the same request
failed the same way the next day, and the owner had to explain it again.

Each finished task now leaves one structured experience: the kind of goal, the
tools in order, whether it worked, the first failure (tool, class, reason), and
-- when the task recovered -- the tools that worked after it, which is the fix.
Before a task is planned, the experiences of similar goals become short lessons
in the planner's context. When a step fails mid-task, the re-planner is told
what fixed that tool's failure before.

Lessons are evidence, not rules. They inform the planner; they never change
code, permissions or behaviour on their own, and a lesson stops appearing once
newer experience contradicts it (it is ranked by recency and outcome counts).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: The test suite stopped writing into the owner's data on 2026-09-29 (commit
#: 98fece5); history from before then is mixed with fake test records.
BACKFILL_SINCE = "2026-09-30"

_WORD = re.compile(r"[a-z0-9]+")
_FILLER = frozenset({
    "a", "an", "the", "and", "or", "to", "for", "of", "in", "on", "at", "with", "me", "my", "please", "can", "could",
    "would", "you", "u", "i", "it", "this", "that", "is", "are", "be", "do", "make", "now", "then", "just", "some",
    "hayat", "from", "into", "about", "what", "how", "want", "need", "will", "your",
})


def words(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(str(text or "").lower()) if word not in _FILLER and len(word) > 1)


def similarity(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def failure_class(reason: str) -> str:
    """A small vocabulary for why a step failed, so different wordings of one cause meet."""
    text = str(reason or "").lower()
    for name, pattern in (
        ("not_found", r"not found|no such file|does not exist|cannot find|winerror 2\b|404"),
        ("permission", r"permission|denied|not allowed|forbidden|403|deny"),
        ("network", r"connect|timed? ?out|getaddrinfo|unreachable|dns|network|10060|10061|ssl"),
        ("robots", r"robots\.txt"),
        ("invalid_input", r"invalid|must be|validation|schema|unsupported|expected"),
        ("unavailable", r"unavailable|not running|not configured|not set up|missing|no model"),
        ("verification", r"verification|check failed|did not appear|not visible|mismatch"),
        ("command_failed", r"exit_code=[1-9]|exited with code|non-zero exit"),
    ):
        if re.search(pattern, text):
            return name
    return "other"


class ExperienceMemory:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._ready = False

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        if not self._ready:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, task_id TEXT, goal TEXT NOT NULL, words TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '', tools TEXT NOT NULL, success INTEGER NOT NULL,
                    failed_tool TEXT NOT NULL DEFAULT '', failure_class TEXT NOT NULL DEFAULT '',
                    failure_reason TEXT NOT NULL DEFAULT '', fix_tools TEXT NOT NULL DEFAULT '[]');
                CREATE INDEX IF NOT EXISTS experiences_failed_tool ON experiences(failed_tool, failure_class);
            """)
            self._ready = True
        return db

    # ------------------------------------------------------------- remember
    def record(self, goal: str, history: list[dict[str, Any]], complete: bool, *, task_id: str = "",
               kind: str = "") -> dict[str, Any] | None:
        """One experience from a finished task's verified history."""
        if not history:
            return None
        tools = [str(event.get("tool")) for event in history if event.get("tool")]
        first_failure = next((event for event in history if not (event.get("verification") or {}).get("passed")), None)
        failed_tool = reason = klass = ""
        fix: list[str] = []
        if first_failure is not None:
            result = first_failure.get("result") or {}
            failed_tool = str(first_failure.get("tool") or "")
            reason = str(result.get("error") or (first_failure.get("verification") or {}).get("reason") or "")[:300]
            klass = failure_class(reason)
            after = history[history.index(first_failure) + 1:]
            # The fix is what passed after the failure, and only if the task then succeeded.
            fix = [str(event.get("tool")) for event in after
                   if (event.get("verification") or {}).get("passed")] if complete else []
        row = (datetime.now(UTC).isoformat(), task_id, str(goal)[:500], " ".join(sorted(words(goal))), kind,
               json.dumps(tools), int(bool(complete)), failed_tool, klass, reason, json.dumps(fix))
        with self._lock, self._db() as db:
            db.execute("INSERT INTO experiences (at, task_id, goal, words, kind, tools, success, failed_tool, "
                       "failure_class, failure_reason, fix_tools) VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
        return {"tools": tools, "success": bool(complete), "failed_tool": failed_tool, "failure_class": klass,
                "fix_tools": fix}

    def backfill_from_logs(self, folder: Path, limit: int = 3000, since: str = BACKFILL_SINCE) -> int:
        """Learn once from the task logs already on disk (data/logs/task-*.jsonl).

        Only logs from `since` on: before 2026-09-30 the test suite wrote its
        fake tasks into the same folder, and learning from those would teach
        HAYAT failures the owner never had.
        """
        with self._lock, self._db() as db:
            if db.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]:
                return 0
        learned = 0
        stamp = since.replace("-", "")
        for path in [item for item in sorted(Path(folder).glob("task-*.jsonl")) if item.stem[5:13] >= stamp][-limit:]:
            goal, history, complete = "", [], None
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    event = json.loads(line)
                    if event.get("event") == "task_started":
                        goal = str(event.get("goal") or "")
                    elif event.get("event") == "action":
                        history.append(event)
                    elif event.get("event") == "task_finished":
                        complete = bool(event.get("complete"))
            except (OSError, ValueError):
                continue
            if goal and history and complete is not None:
                self.record(goal, history, complete, task_id=path.stem)
                learned += 1
        return learned

    # ---------------------------------------------------------------- recall
    def _similar(self, goal: str, limit: int = 200, threshold: float = 0.34) -> list[tuple[float, sqlite3.Row]]:
        wanted = words(goal)
        if not wanted:
            return []
        with self._lock, self._db() as db:
            rows = db.execute("SELECT * FROM experiences ORDER BY id DESC LIMIT 2000").fetchall()
        scored = [(similarity(wanted, frozenset(row["words"].split())), row) for row in rows]
        return sorted((item for item in scored if item[0] >= threshold), key=lambda item: (-item[0], -item[1]["id"]))[:limit]

    def lessons(self, goal: str, limit: int = 4) -> list[dict[str, Any]]:
        """Short, evidence-counted lessons from similar past goals, best first."""
        similar = self._similar(goal)
        if not similar:
            return []
        routes: dict[str, dict[str, Any]] = {}
        failures: dict[tuple[str, str], dict[str, Any]] = {}
        for score, row in similar:
            tools = json.loads(row["tools"])
            key = " -> ".join(tools)
            route = routes.setdefault(key, {"tools": tools, "worked": 0, "failed": 0, "score": score, "last": row["at"],
                                            "example": row["goal"]})
            route["worked" if row["success"] else "failed"] += 1
            if row["failed_tool"]:
                failure = failures.setdefault((row["failed_tool"], row["failure_class"]), {
                    "tool": row["failed_tool"], "class": row["failure_class"], "reason": row["failure_reason"],
                    "count": 0, "fixes": {}, "last": row["at"]})
                failure["count"] += 1
                fix = " -> ".join(json.loads(row["fix_tools"]))
                if fix:
                    failure["fixes"][fix] = failure["fixes"].get(fix, 0) + 1
        found: list[dict[str, Any]] = []
        for route in sorted(routes.values(), key=lambda item: (-(item["worked"] - item["failed"]), -item["score"])):
            if route["worked"] and route["worked"] >= route["failed"]:
                found.append({"kind": "worked", "tools": route["tools"], "times": route["worked"],
                              "failed_times": route["failed"], "last": route["last"],
                              "text": f"Worked {route['worked']} time(s) for a similar request "
                                      f"(\"{route['example'][:80]}\"): {' -> '.join(route['tools'])}."})
                break
        for failure in sorted(failures.values(), key=lambda item: -item["count"]):
            fix = max(failure["fixes"].items(), key=lambda item: item[1])[0] if failure["fixes"] else ""
            text = (f"{failure['tool']} failed {failure['count']} time(s) here ({failure['class']}: "
                    f"{failure['reason'][:120]})." + (f" What fixed it: {fix}." if fix else " No fix recorded yet."))
            found.append({"kind": "failed", "tool": failure["tool"], "class": failure["class"],
                          "times": failure["count"], "fix": fix.split(" -> ") if fix else [], "text": text})
        return found[:limit]

    def fixes_for(self, tool: str, error: str, limit: int = 3) -> list[dict[str, Any]]:
        """What worked after this tool failed this way before, on any goal."""
        klass = failure_class(error)
        with self._lock, self._db() as db:
            rows = db.execute("SELECT fix_tools, goal, at FROM experiences WHERE failed_tool=? AND failure_class=? "
                              "AND success=1 AND fix_tools != '[]' ORDER BY id DESC LIMIT 50", (tool, klass)).fetchall()
        counted: dict[str, dict[str, Any]] = {}
        for row in rows:
            fix = " -> ".join(json.loads(row["fix_tools"]))
            entry = counted.setdefault(fix, {"fix": json.loads(row["fix_tools"]), "times": 0, "example": row["goal"]})
            entry["times"] += 1
        return [{**entry, "text": f"When {tool} failed with {klass} before, this worked {entry['times']} time(s): "
                                  f"{' -> '.join(entry['fix'])} (e.g. \"{entry['example'][:70]}\")."}
                for entry in sorted(counted.values(), key=lambda item: -item["times"])[:limit]]

    def stats(self) -> dict[str, Any]:
        with self._lock, self._db() as db:
            total, worked = db.execute("SELECT COUNT(*), COALESCE(SUM(success),0) FROM experiences").fetchone()
            fixed = db.execute("SELECT COUNT(*) FROM experiences WHERE fix_tools != '[]'").fetchone()[0]
            common = db.execute("SELECT failure_class, COUNT(*) n FROM experiences WHERE failure_class != '' "
                                "GROUP BY failure_class ORDER BY n DESC LIMIT 5").fetchall()
        return {"experiences": total, "succeeded": worked, "recovered_with_a_fix": fixed,
                "common_failures": {row["failure_class"]: row["n"] for row in common}}
