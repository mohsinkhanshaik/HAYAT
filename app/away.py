"""What happened while the owner was away, said in a sentence or two when he comes back.

An assistant that only ever answers feels like a tool; one that tells you, unprompted, what it got done while you were
gone feels like someone working with you. The difference has to be real, though: every item here comes from a
record (a research job that finished, a failure in the failure log, a proactive rule that fired), with its count. If
nothing happened, nothing is said, and no "while you were away" is invented to fill the silence.

Used when he greets HAYAT, calls it by name ("Jarvis?"), or says he is back, after at least ``AWAY_GAP`` since his
last message.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Shorter than this since his last message is a pause, not being away.
AWAY_GAP = timedelta(hours=2)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def last_seen(memory: Any, session_id: str, current: str = "") -> datetime | None:
    """When the owner last wrote, before this message (None when there is no earlier message)."""
    try:
        rows = memory.conversation_history(session_id, 6)
    except Exception:
        log_swallowed(_log, "reading when the owner last wrote")
        return None
    owner = [row for row in rows if row.get("role") == "user"]
    if owner and current and " ".join(str(owner[-1].get("content") or "").split()) == " ".join(current.split()):
        owner = owner[:-1]
    if owner:
        return _when(owner[-1].get("timestamp"))
    # A new launch is a new session: his last message is in an earlier one.
    connect = getattr(memory, "connect", None)
    if connect is None:
        return None
    try:
        with connect() as db:
            rows = db.execute("SELECT content, timestamp FROM conversations WHERE role='user' "
                              "ORDER BY id DESC LIMIT 2").fetchall()
    except Exception:
        log_swallowed(_log, "reading the owner's last message across sessions")
        return None
    rows = [row for row in rows if not (current and " ".join(str(row[0] or "").split()) == " ".join(current.split()))]
    return _when(rows[0][1]) if rows else None


def _quoted(items: list[str], limit: int = 2) -> str:
    shown = [f"“{item[:60].rstrip()}{'…' if len(item) > 60 else ''}”" for item in items[:limit]]
    extra = len(items) - len(shown)
    return ", ".join(shown) + (f" and {extra} more" if extra > 0 else "")


def happenings(runtime: Any, since: datetime) -> list[str]:
    """Plain sentences, one per kind of thing that happened since ``since``; empty when nothing did."""
    found: list[str] = []
    jobs = getattr(runtime, "research_jobs", None)
    try:
        done = [job for job in (jobs.recent(40) if jobs is not None else [])
                if getattr(job, "status", "") == "done" and (_when(getattr(job, "finished_at", "")) or since) > since]
    except Exception:
        log_swallowed(_log, "reading finished research for the away note")
        done = []
    if done:
        asked = [str(getattr(job, "asked", "") or getattr(job, "query", "")) for job in done]
        found.append(f"{len(done)} search{'es' if len(done) != 1 else ''} finished ({_quoted(asked)}); "
                     "the answers are in the chat")
    log = getattr(runtime, "failures", None)
    try:
        failed = [item for item in (log.recent(40) if log is not None else [])
                  if (_when(item.get("timestamp")) or since) > since]
    except Exception:
        log_swallowed(_log, "reading failures for the away note")
        failed = []
    if failed:
        latest = failed[-1]
        what = str(latest.get("user_request") or latest.get("stage") or "something").strip()[:60]
        found.append(f"{len(failed)} thing{'s' if len(failed) != 1 else ''} failed, the latest while working on "
                     f"“{what}” (say “what failed?” and I'll show you the record)")
    triggers = getattr(runtime, "event_triggers", None)
    try:
        fired = [rule for rule in (triggers.list() if triggers is not None else [])
                 if (_when(rule.get("last_run_at") or rule.get("last_fired_at")) or since) > since]
    except Exception:
        log_swallowed(_log, "reading fired rules for the away note")
        fired = []
    if fired:
        names = [str(rule.get("name") or rule.get("goal") or "a rule") for rule in fired]
        found.append(f"{len(fired)} of your watch rules fired ({_quoted(names)})")
    return found


def note(runtime: Any, since: datetime | None, *, now: datetime | None = None) -> str:
    """'While you were away: ...' or '' when he was not away or nothing happened."""
    now = now or datetime.now(UTC)
    if since is None or now - since < AWAY_GAP:
        return ""
    items = happenings(runtime, since)
    if not items:
        return ""
    hours = int((now - since).total_seconds() // 3600)
    gap = f"{hours} hour{'s' if hours != 1 else ''}" if hours < 48 else f"{hours // 24} days"
    return f"While you were away ({gap}): " + "; ".join(items) + "."
