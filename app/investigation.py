"""Finding out why something happened, from what was recorded when it happened.

On 2026-11-10 two replies failed with "ModelError: No bounded streaming provider completed the response". Asked
four times to investigate, HAYAT wrote four accounts and read nothing: it named ports, restarts and timings that
no record held, and finally asked the owner which command to run. The evidence was on disk the whole time. The
event store had both failures with their tracebacks, how long each turn ran before failing, a memory alarm at 98%
three and a half minutes before the first one, and the next reply succeeding with no restart in between.

An investigation here is a fixed procedure over records, with no model in it:

1. **Window** -- the time span is read from the request ("between 11:10 PM and 11:24 PM on November 10, 2026
   Pacific Time", "last night", "the last hour") or taken around the last recorded failure.
2. **What failed** -- every recorded failure in the window, with the turn it belonged to, when that turn started
   and how long it ran.
3. **What was going on** -- alarms, failed tools, degraded services and work still running in the minutes before
   each failure.
4. **How it recovered** -- the next reply that completed, and whether any restart, retry or recovery was
   recorded before it. When none was, it says so: nothing was repaired, the next request simply worked.
5. **Compared with normal** -- how often replies failed with and without the same signal beforehand, over the
   surrounding days, so a cause is supported by a difference and not by a story.
6. **Log files** -- which log files exist, when each was last written, and their lines inside the window. A log
   that holds nothing for the window is reported as a gap, never filled in.

Every statement in the result carries its status: ``verified`` (a record says so, and the record is cited),
``hypothesis`` (consistent with the records, with what would confirm it), or ``not_recorded``. It reads only.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

VERIFIED, HYPOTHESIS, NOT_RECORDED = "verified", "hypothesis", "not_recorded"

FAILURE_EVENTS = ("FAILURE_RECORDED", "TURN_FAILED", "TOOL_FAILED", "TASK_FAILED")
RECOVERY_EVENTS = ("SERVICE_RESTARTING", "SERVICE_RECOVERED", "SERVICE_STARTING", "SERVICE_HEALTHY", "MODEL_STARTED",
                   "MODEL_RECOVERED", "MODEL_RESTARTED")
SIGNAL_EVENTS = ("RESOURCE_ALERT", "SERVICE_DEGRADED", "TOOL_FAILED")
#: How far before a failure a signal still counts as "just before it".
LOOKBACK = timedelta(minutes=10)
#: A reply that takes at least this long was written by the model, not by a rule.
MODEL_TURN_SECONDS = 5.0
MAX_WINDOW = timedelta(days=3)

# ------------------------------------------------------------------------------------------ reading the request

_ASK = re.compile(
    r"(?i)\b(?:investigat\w+|audit|diagnos\w+|root[\s-]cause|find\s+out\s+why|figure\s+out\s+why|look\s+into|"
    r"what\s+(?:caused|went\s+wrong|happened)|why\s+did\s+(?:it|that|this|the|you|u|hayat|my)\b|why\s+(?:was|were|is)\s+(?:it|that|there)|"
    r"check\s+(?:the\s+)?(?:relevant\s+)?logs?|read\s+(?:the\s+)?(?:actual\s+)?logs?|explain\s+(?:the|that|this)\s+(?:error|failure|crash))")
_ABOUT = re.compile(
    r"(?i)\b(?:errors?|fail(?:ed|ure|ures|ing)?|crash(?:ed|es)?|exceptions?|modelerror|timeouts?|timed\s+out|broke(?:n)?|"
    r"stopp?ed\s+working|not\s+working|logs?|incident|outage|freeze|froze|hang|hung|stuck|issue|problem|recover(?:y|ed)?)\b")
#: A short message that points at the failure just reported ("fix it", "work on it", "why?", "look into it").
_FOLLOW_UP = re.compile(
    r"(?i)^(?:(?:ok(?:ay)?|so|and|then|please|pls|hayat|hey|now|yes|yeah)[,\s]+)*(?:"
    r"(?:can|could|will|would)\s+(?:you|u)\s+(?:please\s+)?(?:fix|solve|repair|resolve|investigate|check|look\s+into|work\s+on|"
    r"find\s+out|figure\s+out|explain)\s+(?:it|that|this|them|the\s+(?:problem|issue|error))(?:\s+out)?"
    r"|(?:fix|solve|repair|resolve|investigate|check|debug|look\s+into|work\s+on|find\s+out\s+why|figure\s+out)\s+"
    r"(?:it|that|this|them|the\s+(?:problem|issue|error))(?:\s+out)?"
    r"|why(?:\s+did\s+(?:it|that|this)\s+(?:happen|fail|break|crash))?|why\s+is\s+that|what\s+(?:caused|happened)(?:\s+(?:it|that|there))?"
    r"|how\s+(?:do|can|did)\s+(?:we|you|u|i)\s+fix\s+(?:it|that|this)|investigate|what\s+went\s+wrong"
    r")[\s.!?]*$")

_MONTHS = {name: index for index, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
     "december"), start=1)}
_MONTHS.update({name[:3]: index for name, index in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_TIME = r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?"
_BETWEEN = re.compile(rf"(?i)\b(?:between|from)\s+{_TIME}\s+(?:and|to|until|till|-|–)\s+{_TIME}")
_AT = re.compile(rf"(?i)\b(?:at|around|about|near)\s+{_TIME}(?=\s|[,.;?!]|$)")
_DATE_WORDS = re.compile(
    r"(?i)\b(?:on\s+)?(?:(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>20\d\d))?"
    r"|(?P<day2>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month2>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?:,?\s+(?P<year2>20\d\d))?"
    r"|(?P<iso>20\d\d-\d{2}-\d{2}))\b")
_LAST = re.compile(r"(?i)\b(?:last|past|previous)\s+(\d{1,3}|an?|one|two|three|few)?\s*(minutes?|mins?|hours?|hrs?|days?)\b")
_ZONES = (
    (re.compile(r"(?i)\b(?:pacific|pst|pdt|pt)\b"), "America/Los_Angeles", -8),
    (re.compile(r"(?i)\b(?:mountain|mst|mdt)\b"), "America/Denver", -7),
    (re.compile(r"(?i)\b(?:central|cst|cdt)\b"), "America/Chicago", -6),
    (re.compile(r"(?i)\b(?:eastern|est|edt|et)\b"), "America/New_York", -5),
    (re.compile(r"(?i)\b(?:utc|gmt|zulu)\b"), "UTC", 0),
    (re.compile(r"(?i)\b(?:ist|india(?:n)?\s+(?:standard\s+)?time)\b"), "Asia/Kolkata", 5.5),
)


#: The failure is HAYAT's own: "investigate the failures of Lehman Brothers" is research, not an incident.
_OWN = re.compile(
    r"(?i)\b(?:you|u|your|ur|yourself|hayat|turns?|repl(?:y|ies)|model\s*errors?|modelerror|ollama|logs?|orchestrator|"
    r"provider|streaming|inference|session|the\s+app|this\s+app|the\s+system|my\s+(?:instruction|request|message|question))\b")
_POINTING = re.compile(r"(?i)\b(?:it|that|this|these|those|the\s+(?:two|last|previous|same))\b")


def investigation_request(text: str) -> bool:
    """Whether the owner asks to find out why something of HAYAT's failed (not to research something new)."""
    value = " ".join(str(text or "").split())
    if not value or len(value) > 3000 or not _ASK.search(value) or not _ABOUT.search(value):
        return False
    return bool(_OWN.search(value) or (_POINTING.search(value) and len(value.split()) <= 12))


def follow_up_on_failure(text: str) -> bool:
    """Whether a short message points at the failure HAYAT just reported ("ok can u fix it?", "work on it")."""
    from app.topics import expand

    return bool(_FOLLOW_UP.match(expand(" ".join(str(text or "").split()))))


def _zone(text: str, local: tzinfo) -> tzinfo:
    for pattern, name, offset in _ZONES:
        if pattern.search(text):
            try:
                from zoneinfo import ZoneInfo

                return ZoneInfo(name)
            except Exception:  # noqa: BLE001 - no tz database on this machine: use the fixed offset
                from datetime import timezone

                return timezone(timedelta(hours=offset))
    return local


def _clock(hour: str, minute: str | None, half: str | None) -> time | None:
    value = int(hour)
    marker = (half or "").replace(".", "").lower()
    if marker == "pm" and value < 12:
        value += 12
    if marker == "am" and value == 12:
        value = 0
    if not 0 <= value <= 23:
        return None
    return time(value, int(minute or 0))


def _local_zone() -> tzinfo:
    return datetime.now().astimezone().tzinfo or UTC


def parse_window(text: str, *, now: datetime | None = None, local: tzinfo | None = None) -> tuple[datetime, datetime] | None:
    """The time span a request names, in UTC, or None when it names none."""
    now = now or datetime.now(UTC)
    value = " ".join(str(text or "").split())
    zone = _zone(value, local or _local_zone())
    here = now.astimezone(zone)
    day: date | None = None
    found = _DATE_WORDS.search(value)
    if found:
        if found.group("iso"):
            try:
                day = date.fromisoformat(found.group("iso"))
            except ValueError:
                day = None
        else:
            month = _MONTHS.get((found.group("month") or found.group("month2") or "").lower().rstrip("."))
            number = found.group("day") or found.group("day2")
            year = found.group("year") or found.group("year2")
            if month and number:
                try:
                    day = date(int(year) if year else here.year, month, int(number))
                    if not year and day > here.date():
                        day = date(here.year - 1, month, int(number))
                except ValueError:
                    day = None
    if day is None:
        if re.search(r"(?i)\byesterday\b|\blast\s+night\b", value):
            day = here.date() - timedelta(days=1)
        elif re.search(r"(?i)\btoday\b|\bthis\s+(?:morning|afternoon|evening)\b|\btonight\b", value):
            day = here.date()
    between = _BETWEEN.search(value)
    if between:
        first = _clock(between.group(1), between.group(2), between.group(3) or between.group(6))
        second = _clock(between.group(4), between.group(5), between.group(6) or between.group(3))
        if first and second:
            base = day or here.date()
            start = datetime.combine(base, first, zone)
            end = datetime.combine(base, second, zone)
            if end <= start:
                end += timedelta(days=1)
            if day is None and start > here:
                start, end = start - timedelta(days=1), end - timedelta(days=1)
            return start.astimezone(UTC), end.astimezone(UTC) + timedelta(minutes=1)
    at = _AT.search(value)
    if at and (at.group(2) or at.group(3)):
        moment = _clock(at.group(1), at.group(2), at.group(3))
        if moment:
            centre = datetime.combine(day or here.date(), moment, zone)
            if day is None and centre > here:
                centre -= timedelta(days=1)
            return (centre - timedelta(minutes=15)).astimezone(UTC), (centre + timedelta(minutes=15)).astimezone(UTC)
    last = _LAST.search(value)
    if last:
        count = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "few": 3, None: 1}.get(
            (last.group(1) or "").lower() or None) or int(last.group(1))
        unit = last.group(2).lower()
        span = timedelta(minutes=count) if unit.startswith("m") else timedelta(hours=count) if unit.startswith("h") else timedelta(days=count)
        return now - min(span, MAX_WINDOW), now
    if re.search(r"(?i)\blast\s+night\b", value) and day is not None:
        start = datetime.combine(day, time(18, 0), zone)
        return start.astimezone(UTC), (start + timedelta(hours=12)).astimezone(UTC)
    if day is not None:
        start = datetime.combine(day, time(0, 0), zone)
        return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)
    return None


# ------------------------------------------------------------------------------------------------------ results


@dataclass
class Finding:
    claim: str
    status: str
    evidence: list[str] = field(default_factory=list)
    confirm: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"claim": self.claim, "status": self.status, "evidence": self.evidence, "confirm": self.confirm}


@dataclass
class Investigation:
    start: datetime
    end: datetime
    zone: tzinfo
    findings: list[Finding] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    recovery: dict[str, Any] = field(default_factory=dict)
    contrast: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
    reading: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    events_read: int = 0
    sources: list[str] = field(default_factory=list)

    def by_status(self, status: str) -> list[Finding]:
        return [item for item in self.findings if item.status == status]

    def to_dict(self) -> dict[str, Any]:
        return {"window": {"start": self.start.isoformat(), "end": self.end.isoformat()},
                "findings": [item.to_dict() for item in self.findings], "failures": self.failures,
                "recovery": self.recovery, "contrast": self.contrast, "logs": self.logs, "reading": self.reading,
                "proposals": self.proposals, "events_read": self.events_read, "sources": self.sources,
                "changed_anything": False, "text": format_report(self)}


def _parse(moment: Any) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(moment))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _span(seconds: float) -> str:
    seconds = abs(float(seconds))
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 90:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


class Investigator:
    """Reads the event store and the log folder for a window. Never writes to either."""

    def __init__(self, events_path: Path | str | None, logs_dir: Path | str | None = None, *, topics: Any = None,
                 extra_logs: list[Path] | None = None) -> None:
        self.events_path = Path(events_path) if events_path else None
        self.logs_dir = Path(logs_dir) if logs_dir else None
        self.topics = topics
        self.extra_logs = [Path(item) for item in (extra_logs or [])]

    # ---- records

    def _events(self, start: datetime, end: datetime, types: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        if self.events_path is None or not self.events_path.exists():
            return []
        query = "SELECT id, timestamp, event_type, source, payload_json, turn_id, task_id FROM events WHERE timestamp>=? AND timestamp<?"
        values: list[Any] = [start.isoformat(), end.isoformat()]
        if types:
            query += f" AND event_type IN ({','.join('?' for _ in types)})"
            values += list(types)
        try:
            with closing(sqlite3.connect(f"{self.events_path.as_uri()}?mode=ro", uri=True, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(query + " ORDER BY id LIMIT 20000", values).fetchall()
        except sqlite3.Error:
            log_swallowed(_log, "reading the event store for an investigation", level=logging.WARNING)
            return []
        found = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except ValueError:
                payload = {}
            found.append({"id": row["id"], "at": _parse(row["timestamp"]), "type": row["event_type"],
                          "source": row["source"], "payload": payload if isinstance(payload, dict) else {},
                          "turn_id": row["turn_id"] or "", "task_id": row["task_id"] or ""})
        return [item for item in found if item["at"] is not None]

    def last_failure(self, *, before: datetime | None = None, within: timedelta = timedelta(days=2)) -> dict[str, Any] | None:
        before = before or datetime.now(UTC)
        events = self._events(before - within, before + timedelta(seconds=1), ("FAILURE_RECORDED", "TURN_FAILED"))
        return events[-1] if events else None

    @staticmethod
    def _cite(event: dict[str, Any]) -> str:
        return f"events.db #{event['id']} {event['at'].strftime('%Y-%m-%d %H:%M:%S')} UTC {event['type']}"

    # ---- the investigation

    def investigate(self, *, start: datetime | None = None, end: datetime | None = None, text: str = "",
                    now: datetime | None = None, zone: tzinfo | None = None, session_id: str = "") -> Investigation:
        now = now or datetime.now(UTC)
        zone = zone or (_zone(text, _local_zone()) if text else _local_zone())
        named = parse_window(text, now=now, local=zone) if text and (start is None or end is None) else None
        if start is None or end is None:
            if named is not None:
                start, end = named
            else:
                last = self.last_failure(before=now)
                centre = last["at"] if last else now
                start, end = centre - timedelta(minutes=15), min(now, centre + timedelta(minutes=15)) + timedelta(seconds=1)
        if end - start > MAX_WINDOW:
            start = end - MAX_WINDOW
        result = Investigation(start, end, zone)
        result.sources.append(str(self.events_path) if self.events_path else "no event store")
        events = self._events(start - LOOKBACK, end + timedelta(minutes=30))
        result.events_read = len(events)
        inside = [item for item in events if start <= item["at"] < end]
        if self.events_path is None or not self.events_path.exists():
            result.findings.append(Finding("The event store was not found, so there is no record to investigate from.",
                                           NOT_RECORDED, [str(self.events_path)]))
        self._failures(result, events, inside)
        self._recovery(result, events)
        self._contrast(result)
        self._logs(result)
        self._causes(result)
        if text and self.topics is not None:
            self._reading(result, text, session_id)
        return result

    def _failures(self, result: Investigation, events: list[dict[str, Any]], inside: list[dict[str, Any]]) -> None:
        received = {item["turn_id"]: item for item in events if item["type"] == "TURN_RECEIVED" and item["turn_id"]}
        routed = {item["turn_id"]: item for item in events if item["type"] == "TURN_ROUTED" and item["turn_id"]}
        seen: set[str] = set()
        for event in inside:
            if event["type"] not in FAILURE_EVENTS:
                continue
            payload = event["payload"]
            key = event["turn_id"] or f"{event['type']}:{event['id']}"
            if event["type"] == "TURN_FAILED" and key in seen:
                continue  # the same failure, already taken from its fuller FAILURE_RECORDED record
            if event["type"] in {"FAILURE_RECORDED", "TURN_FAILED"}:
                seen.add(key)
            begun = received.get(event["turn_id"])
            ran = (event["at"] - begun["at"]).total_seconds() if begun else None
            kind = str(payload.get("exception_type") or "")
            message = str(payload.get("exception_message") or payload.get("error") or "")
            failure = {
                "event_id": event["id"], "at": event["at"].isoformat(), "type": event["type"], "turn_id": event["turn_id"],
                "failure_id": str(payload.get("failure_id") or ""), "exception": kind, "message": message[:300],
                "provider": str(payload.get("provider") or ""), "tool": str(payload.get("tool") or ""),
                "stage": str(payload.get("stage") or event["source"] or ""), "request": str(payload.get("user_request") or "")[:200],
                "retry_count": payload.get("retry_count"), "recovery_attempted": payload.get("recovery_attempted"),
                "ran_seconds": round(ran, 1) if ran is not None else None,
                "intent": str((routed.get(event["turn_id"]) or {}).get("payload", {}).get("intent") or ""),
                "where": _last_frames(str(payload.get("traceback") or "")), "signals": [],
            }
            for signal in events:
                if signal["type"] in SIGNAL_EVENTS and timedelta(0) <= event["at"] - signal["at"] <= LOOKBACK \
                        and signal["id"] != event["id"]:
                    failure["signals"].append({"event_id": signal["id"], "type": signal["type"],
                                               "seconds_before": round((event["at"] - signal["at"]).total_seconds()),
                                               "detail": _signal_detail(signal), "cite": self._cite(signal)})
            result.failures.append(failure)
            when = _both(event["at"], result.zone)
            what = f"{kind}: {message}" if kind else message or event["type"]
            claim = f"A failure was recorded at {when}: {what[:220].rstrip('. ')}."
            if failure["request"]:
                claim += f" It happened while answering “{failure['request']}”."
            if ran is not None:
                claim += f" That turn had been running for {_span(ran)} when it failed."
            if failure["where"]:
                claim += f" The error was raised in {failure['where']}."
            cites = [self._cite(event)] + ([self._cite(begun)] if begun else [])
            result.findings.append(Finding(claim, VERIFIED, cites))
            for signal in failure["signals"]:
                result.findings.append(Finding(
                    f"{_span(signal['seconds_before'])} before that failure: {signal['detail']}.", VERIFIED, [signal["cite"]]))
        if not result.failures:
            result.findings.append(Finding(
                f"No failure is recorded between {_both(result.start, result.zone)} and {_both(result.end, result.zone)}.",
                VERIFIED if result.events_read else NOT_RECORDED,
                [f"{result.events_read} events read from {self.events_path}"]))

    def _recovery(self, result: Investigation, events: list[dict[str, Any]]) -> None:
        if not result.failures:
            return
        last = max(_parse(item["at"]) for item in result.failures)
        after = [item for item in events if item["at"] > last]
        recovered = next((item for item in after if item["type"] == "TURN_COMPLETED"), None)
        model_ok = None
        received = {item["turn_id"]: item for item in events if item["type"] == "TURN_RECEIVED" and item["turn_id"]}
        for item in after:
            begun = received.get(item["turn_id"])
            if item["type"] == "TURN_COMPLETED" and begun and (item["at"] - begun["at"]).total_seconds() >= MODEL_TURN_SECONDS:
                model_ok = (item, begun)
                break
        until = model_ok[0]["at"] if model_ok else recovered["at"] if recovered else last + timedelta(minutes=30)
        between = [item for item in after if item["at"] <= until and item["type"] in RECOVERY_EVENTS]
        retried = [item for item in result.failures if item.get("retry_count") or item.get("recovery_attempted")]
        result.recovery = {
            "next_completed_reply": recovered["at"].isoformat() if recovered else "",
            "next_model_reply": model_ok[0]["at"].isoformat() if model_ok else "",
            "restart_or_recovery_events": [self._cite(item) for item in between],
            "retries_recorded": len(retried),
            "mechanism": "restart_recorded" if between else "retry_recorded" if retried else "none_recorded",
        }
        if model_ok:
            done, begun = model_ok
            result.findings.append(Finding(
                f"The next reply written by the model completed at {_both(done['at'], result.zone)}, "
                f"{_span((done['at'] - last).total_seconds())} after the last failure; it took "
                f"{_span((done['at'] - begun['at']).total_seconds())}.", VERIFIED, [self._cite(begun), self._cite(done)]))
        elif recovered:
            result.findings.append(Finding(
                f"The next reply that completed was at {_both(recovered['at'], result.zone)}, but it was answered by a "
                "rule in under a few seconds, so it does not show that the model had recovered.", VERIFIED,
                [self._cite(recovered)]))
        else:
            result.findings.append(Finding("No reply completed in the 30 minutes after the last failure in the records "
                                           "read, so recovery is not shown.", NOT_RECORDED))
        if between:
            result.findings.append(Finding(
                "A restart or recovery was recorded before the next successful reply: "
                + "; ".join(f"{item['type']} at {_both(item['at'], result.zone)}" for item in between) + ".",
                VERIFIED, [self._cite(item) for item in between]))
        else:
            claim = ("No restart, retry or recovery is recorded between the failure and the next successful reply"
                     + (" (the failure records show retry_count 0 and no recovery attempt)" if not retried else "")
                     + ". Nothing was repaired: the next request simply succeeded.")
            result.findings.append(Finding(claim, VERIFIED, [f"event store, {_both(last, result.zone)} to "
                                                             f"{_both(until, result.zone)}: no "
                                                             + "/".join(RECOVERY_EVENTS[:3]) + " event"]))

    def _contrast(self, result: Investigation, days: int = 7) -> None:
        """Did replies fail more often after the same signal than without it? Counted over the surrounding days."""
        signal_types = sorted({signal["type"] for failure in result.failures for signal in failure["signals"]})
        if not signal_types:
            return
        start, end = result.start - timedelta(days=days), result.end + timedelta(hours=1)
        events = self._events(start, end, ("TURN_RECEIVED", "TURN_COMPLETED", "TURN_FAILED", *signal_types))
        received = {item["turn_id"]: item for item in events if item["type"] == "TURN_RECEIVED" and item["turn_id"]}
        signals = [item["at"] for item in events if item["type"] in signal_types]
        counts = {"with_signal": [0, 0], "without_signal": [0, 0]}  # [turns, failed]
        index = 0
        for item in events:
            if item["type"] not in {"TURN_COMPLETED", "TURN_FAILED"} or item["turn_id"] not in received:
                continue
            begun = received[item["turn_id"]]["at"]
            ran = (item["at"] - begun).total_seconds()
            if ran < MODEL_TURN_SECONDS:
                continue  # answered by a rule: it never waited on the model
            while index < len(signals) and signals[index] < begun - LOOKBACK:
                index += 1
            near = any(begun - LOOKBACK <= moment <= item["at"] for moment in signals[index:index + 50])
            bucket = counts["with_signal" if near else "without_signal"]
            bucket[0] += 1
            bucket[1] += int(item["type"] == "TURN_FAILED")
        with_n, with_f = counts["with_signal"]
        without_n, without_f = counts["without_signal"]
        result.contrast = {"days": days, "signals": signal_types, "with_signal": {"replies": with_n, "failed": with_f},
                           "without_signal": {"replies": without_n, "failed": without_f}}
        if with_n and without_n:
            rate_with, rate_without = with_f / with_n, without_f / without_n
            result.contrast["rate_with"], result.contrast["rate_without"] = round(rate_with, 4), round(rate_without, 4)
            small = min(with_n, without_n) < 20 or (with_f + without_f) < 5
            result.findings.append(Finding(
                f"Over the {days} days up to this window, model-written replies with a {' / '.join(signal_types)} signal in the "
                f"{int(LOOKBACK.total_seconds() // 60)} minutes before them or while they ran failed {with_f} times out of "
                f"{with_n} ({rate_with:.1%}); "
                f"those without one failed {without_f} out of {without_n} ({rate_without:.1%})."
                + (" The counts are small, so this is an association and not proof." if small else ""),
                VERIFIED, [f"event store, {start.date().isoformat()} to {end.date().isoformat()}: "
                           f"{with_n + without_n} model-written replies counted"]))

    def _logs(self, result: Investigation) -> None:
        files: list[Path] = []
        if self.logs_dir is not None and self.logs_dir.exists():
            files += sorted(path for path in self.logs_dir.glob("*.log*") if path.is_file())
        files += [path for path in self.extra_logs if path.exists() and path.is_file()]
        if not files:
            result.findings.append(Finding("No log file was found to read" + (f" in {self.logs_dir}" if self.logs_dir else "")
                                           + ".", NOT_RECORDED))
            return
        local_start, local_end = result.start.astimezone(result.zone), result.end.astimezone(result.zone)
        stale: list[tuple[Path, datetime]] = []
        for path in files[:12]:
            try:
                stat = path.stat()
            except OSError:
                continue
            written = datetime.fromtimestamp(stat.st_mtime, UTC)
            entry: dict[str, Any] = {"path": str(path), "bytes": stat.st_size, "last_written": written.isoformat(), "lines": []}
            if written < result.start - timedelta(minutes=1):
                entry["covers_window"] = False
                stale.append((path, written))
            else:
                entry["lines"] = _lines_in_window(path, local_start, local_end, result.start, result.end)
                entry["covers_window"] = True
                if entry["lines"]:
                    worst = [line for line in entry["lines"] if re.search(r"(?i)\b(?:error|warn|fail|exception|traceback|timeout|refused)", line["text"])]
                    result.findings.append(Finding(
                        f"{path.name} has {len(entry['lines'])} line(s) inside the window"
                        + (f", {len(worst)} of them warnings or errors" if worst else ", none of them warnings or errors") + ".",
                        VERIFIED, [f"{path}:{line['line']}: {line['text'][:200]}" for line in (worst or entry["lines"])[:5]]))
                else:
                    result.findings.append(Finding(f"{path.name} was written during or after the window but has no line "
                                                   "stamped inside it.", VERIFIED, [str(path)]))
            result.logs.append(entry)
            result.sources.append(str(path))
        if stale:
            # One line for all of them: four "holds nothing" lines buried the findings that matter.
            result.findings.append(Finding(
                f"{len(stale)} log file(s) were last written before this window began, so they hold nothing about it: "
                + "; ".join(f"{path.name} (last written {written.astimezone(result.zone).strftime('%Y-%m-%d %I:%M %p')})"
                            for path, written in stale) + ".",
                VERIFIED, [f"{path} (modified {written.isoformat()})" for path, written in stale]))

    def _causes(self, result: Investigation) -> None:
        if not result.failures:
            return
        model_failures = [item for item in result.failures if "model" in (item["exception"] + item["message"]).lower()
                          or item["provider"]]
        memory = [signal for failure in result.failures for signal in failure["signals"]
                  if signal["type"] == "RESOURCE_ALERT" and "memory" in signal["detail"]]
        slow = [item for item in model_failures if (item["ran_seconds"] or 0) >= 60]
        log_covers = any(entry.get("covers_window") and entry["lines"] for entry in result.logs)
        if model_failures and memory:
            support = sorted({signal["cite"] for signal in memory})
            claim = ("Likely cause: the computer was almost out of memory, so the model ran too slowly to finish inside "
                     "its time limit"
                     + (f" (the failed turns ran {', '.join(_span(item['ran_seconds']) for item in slow)} before giving up)" if slow else "")
                     + ". The memory alarm and the long-running turns are recorded; that the slowness was caused by the "
                       "memory pressure is an inference.")
            result.findings.append(Finding(claim, HYPOTHESIS, support,
                                           "The model server's own log for those minutes (load time, memory, timeouts), or "
                                           "the same request repeated with memory free."))
            result.proposals += [
                "Before a long answer, check free memory and tell you when it is too low instead of failing after minutes.",
                "Close or pause what is filling memory at that hour (I can list the largest processes on request).",
            ]
        elif model_failures:
            result.findings.append(Finding(
                "The model did not complete a response, and no alarm, failed tool or degraded service is recorded in the "
                f"{int(LOOKBACK.total_seconds() // 60)} minutes before it. The records show that it failed, not why.",
                HYPOTHESIS, [], "The model server's own log for those minutes."))
        if model_failures and not log_covers:
            result.findings.append(Finding(
                "No log line from the model server or from HAYAT's own process covers these failures. Until now a failed "
                "turn was written to the event store only, never to data/logs/hayat.log.", NOT_RECORDED, [],
                "From this version on, every recorded failure is also written to the process log."))
            result.proposals.append("Keep the model server's log beside HAYAT's (data/logs) so the two can be read together.")
        if not result.recovery.get("restart_or_recovery_events"):
            result.proposals.append("Retry a failed answer once automatically when the failure is a timeout, and say so in the reply.")

    def _reading(self, result: Investigation, text: str, session_id: str) -> None:
        """Why a quoted message was read the way it was, from the topic record ("why was 'can you fix it?' ...")."""
        quoted = re.findall(r"[\"“']([^\"”']{3,120})[\"”']", text)
        if not quoted:
            return
        try:
            decisions = self.topics.recent_decisions(session_id or None, limit=200)
        except Exception:
            log_swallowed(_log, "reading the topic record for an investigation")
            return
        from app.topics import expand

        def plain(value: str) -> str:
            return re.sub(r"[^a-z0-9 ]", "", expand(value).lower()).strip()

        for phrase in quoted[:3]:
            wanted = plain(phrase)
            match = next((row for row in decisions if wanted and (plain(str(row.get("text", ""))) == wanted
                                                                  or wanted in plain(str(row.get("text", ""))))), None)
            if match is None:
                result.findings.append(Finding(f"The topic record holds no message matching “{phrase}”, so how it was "
                                               "read cannot be shown.", NOT_RECORDED))
                continue
            result.reading.append(match)
            claim = (f"“{match.get('text', '')[:120]}” was placed in the topic “{match.get('topic') or 'untitled'}” "
                     f"({match.get('relation', '')}: {match.get('reasons', '') or 'no reason recorded'}).")
            if match.get("anchored"):
                claim += f" It was then read as: “{match['anchored'][:200]}”."
            result.findings.append(Finding(claim, VERIFIED, [f"topics.db message #{match.get('id')} at {match.get('at')}"]))


def _both(moment: datetime, zone: tzinfo) -> str:
    local = moment.astimezone(zone)
    return f"{local.strftime('%Y-%m-%d %I:%M:%S %p')} {local.tzname() or ''} ({moment.astimezone(UTC).strftime('%H:%M:%S')} UTC)".replace("  ", " ")


def _last_frames(traceback: str, count: int = 2) -> str:
    frames = re.findall(r'File "([^"]+)", line (\d+), in (\S+)', traceback)
    if not frames:
        return ""
    return " → ".join(f"{Path(path.replace(chr(92) * 2, chr(92))).name}:{line} {name}" for path, line, name in frames[-count:])


def _signal_detail(event: dict[str, Any]) -> str:
    payload = event["payload"]
    if event["type"] == "RESOURCE_ALERT":
        parts = []
        if payload.get("ram_used_percent") is not None:
            parts.append(f"memory was {float(payload['ram_used_percent']):.0f}% full")
        if payload.get("vram_used_percent") is not None:
            parts.append(f"graphics memory was {float(payload['vram_used_percent']):.0f}% full")
        if payload.get("disk_free_bytes") is not None and float(payload["disk_free_bytes"]) < 20e9:
            parts.append(f"only {float(payload['disk_free_bytes']) / 1e9:.1f} GB of disk was free")
        return "a resource alarm: " + (", ".join(parts) or "no detail recorded")
    if event["type"] == "TOOL_FAILED":
        return f"the tool {payload.get('tool') or 'unknown'} failed ({str(payload.get('error') or '')[:120]})"
    if event["type"] == "SERVICE_DEGRADED":
        return f"the service {payload.get('service') or event['source']} was reported degraded"
    return event["type"]


_STAMPS = (
    (re.compile(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"), "local"),
    (re.compile(r"time=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?"), "stamped"),
    (re.compile(r"^\[?(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})"), "slash"),
)


def _lines_in_window(path: Path, local_start: datetime, local_end: datetime, start: datetime, end: datetime,
                     limit: int = 60, tail_bytes: int = 6_000_000) -> list[dict[str, Any]]:
    """Lines of a log whose own timestamp falls inside the window (the last few MB of the file are read)."""
    found: list[dict[str, Any]] = []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > tail_bytes:
                handle.seek(size - tail_bytes)
                handle.readline()
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return found
    first_line = 1 if size <= tail_bytes else None
    for number, line in enumerate(text.splitlines(), start=1):
        moment = None
        for pattern, kind in _STAMPS:
            match = pattern.search(line)
            if not match:
                continue
            try:
                if kind == "local":
                    moment = datetime.fromisoformat(match.group(1).replace(" ", "T")).replace(tzinfo=local_start.tzinfo)
                elif kind == "slash":
                    moment = datetime.strptime(match.group(1), "%Y/%m/%d %H:%M:%S").replace(tzinfo=local_start.tzinfo)
                else:
                    offset = match.group(2)
                    stamp = match.group(1) + ("+00:00" if offset == "Z" else offset or "")
                    moment = datetime.fromisoformat(stamp)
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=local_start.tzinfo)
            except ValueError:
                moment = None
            break
        if moment is not None and start <= moment.astimezone(UTC) < end:
            found.append({"line": number if first_line else f"~{number} (of the last {tail_bytes // 1_000_000} MB)",
                          "text": line.strip()[:400]})
            if len(found) >= limit:
                break
    return found


def format_report(result: Investigation) -> str:
    """The investigation in plain words: what the records show, what is only likely, what was not recorded."""
    lines = [f"Investigation of {_both(result.start, result.zone)} to {_both(result.end, result.zone)}.",
             f"I read {result.events_read} recorded events and {len(result.logs)} log file(s). I changed nothing."]
    verified = result.by_status(VERIFIED)
    if verified:
        lines += ["", "What the records show (verified):"]
        for item in verified:
            lines.append(f"- {item.claim}")
            lines += [f"    evidence: {cite}" for cite in item.evidence[:3]]
    likely = result.by_status(HYPOTHESIS)
    if likely:
        lines += ["", "What is likely but not proven (hypothesis):"]
        for item in likely:
            lines.append(f"- {item.claim}")
            lines += [f"    supported by: {cite}" for cite in item.evidence[:3]]
            if item.confirm:
                lines.append(f"    what would confirm it: {item.confirm}")
    missing = result.by_status(NOT_RECORDED)
    if missing:
        lines += ["", "What was not recorded (I will not guess it):"]
        for item in missing:
            lines.append(f"- {item.claim}" + (f" {item.confirm}" if item.confirm else ""))
    if result.proposals:
        lines += ["", "What I would change, only if you say so:"]
        lines += [f"- {proposal}" for proposal in dict.fromkeys(result.proposals)]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------ the tool


def register_investigation_tools(registry: Any, investigator: Investigator, session: Any = None) -> None:
    def run(question: str = "", start: str = "", end: str = "", hours: float = 0) -> dict[str, Any]:
        begin, finish = _parse(start) if start else None, _parse(end) if end else None
        now = datetime.now(UTC)
        if hours and begin is None:
            begin, finish = now - timedelta(hours=min(float(hours), MAX_WINDOW.total_seconds() / 3600)), now
        current = session() if callable(session) else session
        return investigator.investigate(start=begin, end=finish, text=str(question or ""), now=now,
                                        session_id=str(getattr(current, "session_id", "") or "")).to_dict()

    registry.register(
        "investigate_incident",
        "Find out why something failed, from HAYAT's own records: the failures in a time window with how long each "
        "turn ran, the alarms and failed tools just before them, how and whether it recovered, how often the same "
        "signal came before failures over the last week, and the log files with their lines for that window. "
        "Every statement is marked verified (with the record cited), hypothesis (with what would confirm it) or "
        "not recorded. Give the owner's question (it may name a time span), or start/end as ISO times, or hours "
        "back from now; with none of them it investigates the last recorded failure. Reads only; changes nothing.",
        {"type": "object", "properties": {
            "question": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "hours": {"type": "number"}}, "additionalProperties": False},
        "low", "system_read", run,
    )


def direct_investigation_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    if "investigate_incident" not in names or not investigation_request(text):
        return None
    return [{"description": "Read the recorded events and logs for the window and separate what is verified from "
                            "what is only likely.",
             "tool": "investigate_incident", "arguments": {"question": " ".join(str(text).split())[:2000]}}]
