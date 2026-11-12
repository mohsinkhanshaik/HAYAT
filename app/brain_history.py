"""The brain mesh's fourth dimension: what it was doing, over time.

Rebuilt from the durable event log (``data/memory/events.db``), which already
records every turn, routing decision, tool start/finish/failure, research run
and authority decision with a timestamp. Nothing here is sampled or guessed:
a span is the interval between a recorded start and its recorded end. A
start whose end was never logged is ended by the first later event proving
the work was over; with no such evidence it is drawn to "now" and marked
open, never given a made-up duration.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

#: The flows a span can belong to; the same names the 3D brain animates.
FLOWS = ("hearing", "seeing", "model", "tool", "research", "learning")
#: Tools whose work is research rather than local action.
RESEARCH_TOOLS = {"research_topic", "web_search", "browser_navigate", "browser_extract",
                  "capture_web_page", "scrape_page", "fetch_url"}
#: Tools whose work is looking: the camera and the screen.
SEEING_TOOLS = {"camera_observe", "camera_capture", "describe_screen", "display_screenshot"}
#: How far before the window to read, so work begun just before it is seen.
LOOKBACK_SECONDS = 3600.0
EVENT_TYPES = ("TURN_RECEIVED", "TURN_ROUTED", "TURN_COMPLETED", "TURN_FAILED", "TURN_SUPERSEDED",
               "TOOL_STARTED", "TOOL_COMPLETED", "TOOL_FAILED", "RESEARCH_STARTED",
               "AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED",
               "CAMERA_CHANGED", "CAMERA_OBSERVATION")


@dataclass(frozen=True, slots=True)
class Span:
    start: float
    end: float
    flow: str
    label: str
    #: True when no end was ever recorded and nothing later ended it.
    open: bool = False
    #: The registered tool this span ran, when it was a tool call ("" otherwise).
    tool: str = ""


@dataclass(frozen=True, slots=True)
class Mark:
    at: float
    node: str
    kind: str      # "failure", "denied", "approval" or "look"
    label: str
    #: The tool a failure or authority decision was about, when one was logged.
    tool: str = ""


@dataclass(frozen=True, slots=True)
class History:
    start: float
    end: float
    spans: tuple[Span, ...]
    marks: tuple[Mark, ...]

    def flows_at(self, moment: float) -> list[str]:
        """Which flows were under way at one instant, in FLOWS order."""
        live = {span.flow for span in self.spans if span.start <= moment <= span.end}
        return [flow for flow in FLOWS if flow in live]

    def flows_between(self, begin: float, finish: float) -> list[str]:
        """Flows with any recorded activity in [begin, finish], in FLOWS order."""
        live = {span.flow for span in self.spans if span.start <= finish and span.end >= begin}
        return [flow for flow in FLOWS if flow in live]

    def span_at(self, flow: str, moment: float, tolerance: float = 0.0) -> Span | None:
        """The span of one flow covering ``moment`` (the shortest, if several)."""
        covering = [span for span in self.spans
                    if span.flow == flow and span.start - tolerance <= moment <= span.end + tolerance]
        return min(covering, key=lambda span: span.end - span.start) if covering else None

    def node_seconds(self, begin: float, finish: float,
                     flow_nodes: dict[str, tuple[str, ...]]) -> dict[str, float]:
        """How long each brain part was busy in [begin, finish], from its flows."""
        busy: dict[str, float] = {}
        for span in self.spans:
            if span.open:
                # An end that was never recorded is a guess; busy time is
                # recorded time only (one unended call once counted as an hour).
                continue
            overlap = min(span.end, finish) - max(span.start, begin)
            if overlap <= 0:
                continue
            for node in flow_nodes.get(span.flow, ()):
                busy[node] = busy.get(node, 0.0) + overlap
        for mark in self.marks:
            if begin <= mark.at <= finish:
                busy[mark.node] = busy.get(mark.node, 0.0) + 1.0
        return busy

    def labels_at(self, moment: float) -> list[str]:
        return [span.label for span in self.spans if span.start <= moment <= span.end and span.label]

    def tools_at(self, moment: float) -> list[str]:
        """The registered tools running at one instant, as recorded."""
        return list(dict.fromkeys(span.tool for span in self.spans
                                  if span.tool and span.start <= moment <= span.end))

    def tools_between(self, begin: float, finish: float) -> list[str]:
        return list(dict.fromkeys(span.tool for span in self.spans
                                  if span.tool and span.start <= finish and span.end >= begin))

    def tool_seconds(self, begin: float, finish: float) -> dict[str, float]:
        """Recorded busy seconds per tool in [begin, finish]; open-ended spans are not counted."""
        busy: dict[str, float] = {}
        for span in self.spans:
            if not span.tool or span.open:
                continue
            overlap = min(span.end, finish) - max(span.start, begin)
            if overlap > 0:
                busy[span.tool] = busy.get(span.tool, 0.0) + overlap
        for mark in self.marks:
            if mark.tool and begin <= mark.at <= finish:
                busy[mark.tool] = busy.get(mark.tool, 0.0) + 1.0
        return busy

    def marks_near(self, moment: float, seconds: float) -> list[Mark]:
        return [mark for mark in self.marks if abs(mark.at - moment) <= seconds]


def _epoch(stamp: str) -> float | None:
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def reconstruct(rows: Iterable[tuple[str, str, str, Any, str | None]], start: float, end: float) -> History:
    """Spans and marks from (timestamp, event_type, source, payload, turn_id) rows, in time order.

    No duration is invented. A start is ended by its own recorded end; failing
    that, by the first later event proving the work was over -- its turn
    finished, the learner started that tool again, or the next turn began.
    With no such evidence at all it runs to the end of the window and is
    marked open: its end was never recorded.
    """
    spans: list[Span] = []
    marks: list[Mark] = []
    turns: dict[str, dict[str, Any]] = {}
    tools: dict[str, list[tuple[float, str]]] = {}
    camera_on: float | None = None

    def close(begin: float, finish: float, flow: str, label: str, open_ended: bool = False,
              tool: str = "") -> None:
        finish = max(finish, begin + 0.25)          # an instant still shows
        if finish >= start and begin <= end:
            spans.append(Span(max(begin, start), min(finish, end), flow, label, open_ended, tool))

    def tool_flow(tool: str, owner: str) -> str:
        if tool in RESEARCH_TOOLS:
            return "research" if owner else "learning"
        if tool in SEEING_TOOLS:
            return "seeing"
        return "tool"

    def end_turn(turn_id: str, at: float, *, recorded: bool) -> None:
        turn = turns.pop(turn_id)
        begin = turn.get("routed", turn["received"])
        intent = turn.get("intent", "")
        flow = "research" if intent == "RESEARCH" else "model"
        label = f"{'answering' if flow == 'model' else 'researching'}: {intent.lower() or 'turn'}"
        close(begin, at, flow, label if recorded else f"{label} (end not logged)", not recorded)
        # Tools still pending in this turn were over when the turn was.
        for tool, pending in tools.items():
            for item in [entry for entry in pending if entry[1] == turn_id]:
                pending.remove(item)
                close(item[0], at, tool_flow(tool, turn_id), f"{tool.replace('_', ' ')} (end not logged)", True,
                      tool)

    for stamp, kind, source, raw, turn_id in rows:
        at = _epoch(stamp)
        if at is None:
            continue
        payload = _payload(raw)
        if kind == "TURN_RECEIVED" and turn_id:
            # A new turn means an older unfinished one is no longer being worked.
            for older in [key for key, turn in turns.items() if key != turn_id and "routed" in turn]:
                end_turn(older, at, recorded=False)
            turns[turn_id] = {"received": at, "voice": str(payload.get("source") or source) == "voice"}
        elif kind == "TURN_ROUTED" and turn_id:
            turn = turns.setdefault(turn_id, {"received": at, "voice": False})
            turn["routed"], turn["intent"] = at, str(payload.get("intent") or "")
            if turn.get("voice"):
                close(turn["received"], at, "hearing", "hearing you")
        elif kind in {"TURN_COMPLETED", "TURN_FAILED", "TURN_SUPERSEDED"} and turn_id in turns:
            end_turn(turn_id, at, recorded=True)
        elif kind == "TOOL_STARTED":
            tool = str(payload.get("tool") or "tool")
            owner = turn_id or ""
            pending = tools.setdefault(tool, [])
            if not owner:
                # The learner runs one call of a tool at a time: starting it
                # again means the previous call was over.
                for item in [entry for entry in pending if not entry[1]]:
                    pending.remove(item)
                    close(item[0], at, tool_flow(tool, ""), f"{tool.replace('_', ' ')} (end not logged)", True,
                          tool)
            pending.append((at, owner))
        elif kind in {"TOOL_COMPLETED", "TOOL_FAILED"}:
            tool = str(payload.get("tool") or "tool")
            pending = tools.get(tool) or []
            # Pair the end with a start from the same turn. Pairing with any
            # earlier start drew one call as two hours.
            same = [entry for entry in pending if entry[1] == (turn_id or "")]
            if payload.get("started") is False:
                same = []                           # failed before it began: not an end
            if same:
                pending.remove(same[0])
                close(same[0][0], at, tool_flow(tool, same[0][1]), tool.replace("_", " "), tool=tool)
            if kind == "TOOL_FAILED":
                error = str(payload.get("error") or "failed")
                marks.append(Mark(at, "tools", "failure", f"{tool.replace('_', ' ')}: {error[:80]}", tool))
        elif kind == "RESEARCH_STARTED" and not turn_id:
            # Research with no turn behind it is the background learner; its
            # own tool events carry the duration, this marks the moment.
            close(at, at, "learning", f"learning: {str(payload.get('query') or '')[:60]}")
        elif kind == "CAMERA_CHANGED":
            state = str(payload.get("state") or "").upper()
            if state == "ACTIVE" and camera_on is None:
                camera_on = at
            elif state != "ACTIVE" and camera_on is not None:
                close(camera_on, at, "seeing", "camera on")
                camera_on = None
        elif kind == "CAMERA_OBSERVATION":
            # Only that a look happened is logged, never what was seen.
            what = ("watch mode noticed movement" if payload.get("source") == "watch"
                    else "looked through the camera")
            state = str(payload.get("visibility") or "clear")
            marks.append(Mark(at, "perception", "look", what if state == "clear" else f"{what} (view {state})"))
        elif kind in {"AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED"}:
            tool = str(payload.get("tool") or payload.get("category") or "an action")
            denied = kind == "AUTHORITY_DENIED"
            marks.append(Mark(at, "authority", "denied" if denied else "approval",
                              f"{'denied' if denied else 'approval asked'}: {tool.replace('_', ' ')}",
                              str(payload.get("tool") or "")))

    # The camera still on at the end of the window: on until now, as far as
    # anything recorded says.
    if camera_on is not None:
        close(camera_on, end, "seeing", "camera on (still on)", True)
    # No later evidence at all: shown to the end of the window, marked open.
    for turn_id in list(turns):
        turn = turns.pop(turn_id)
        close(turn.get("routed", turn["received"]), end, "model", "turn (no end recorded)", True)
    for tool, pending in tools.items():
        for begin, owner in pending:
            close(begin, end, tool_flow(tool, owner), f"{tool.replace('_', ' ')} (no end recorded)", True, tool)
    spans.sort(key=lambda span: (span.start, span.flow))
    marks = [mark for mark in marks if start <= mark.at <= end]
    return History(start, end, tuple(spans), tuple(marks))


def load(path: Path, start: float, end: float) -> History:
    """Read one window of the event log, read-only, and rebuild it."""
    lower = datetime.fromtimestamp(start - LOOKBACK_SECONDS, UTC).isoformat()
    upper = datetime.fromtimestamp(end, UTC).isoformat()
    placeholders = ",".join("?" for _ in EVENT_TYPES)
    try:
        with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                f"SELECT timestamp,event_type,source,payload_json,turn_id FROM events "
                f"WHERE event_type IN ({placeholders}) AND timestamp>=? AND timestamp<=? "
                "ORDER BY timestamp, id LIMIT 50000",
                [*EVENT_TYPES, lower, upper],
            ).fetchall()
    except sqlite3.Error:
        rows = []
    return reconstruct(rows, start, end)


# ----------------------------------------------------------------- usage
#
# The component neurons of the 3D brain carry live numbers: how often each
# tool, route and policy tier was used, how often it failed, and what happened
# last. They come from the same event log, read-only, as recorded counts --
# never sampled, never estimated.

#: Event types the usage summary reads.
USAGE_TYPES = ("TOOL_COMPLETED", "TOOL_FAILED", "TURN_ROUTED", "TURN_COMPLETED", "TURN_FAILED",
               "AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED", "SERVICE_DEGRADED", "TASK_CREATED")


@dataclass(frozen=True, slots=True)
class Count:
    calls: int = 0
    failures: int = 0
    last_at: float | None = None
    last_ok: bool | None = None
    last_note: str = ""


@dataclass(frozen=True, slots=True)
class Group:
    """The tools one turn (or one task without a turn) ran, and how the turn was routed."""
    key: str
    intent: str
    tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class Usage:
    since: float | None
    until: float | None
    rows: int
    tools: dict[str, Count]
    intents: dict[str, Count]
    #: Authority decisions per risk tier name: calls = decisions, failures = denials.
    tiers: dict[str, Count]
    #: Events per event source; failures = SERVICE_DEGRADED reported by or about it.
    sources: dict[str, Count]
    groups: tuple[Group, ...]


def summarize(rows: Iterable[tuple[str, str, str, Any, str | None, str | None]],
              source_rows: Iterable[tuple[str, int, str]] = ()) -> Usage:
    """Usage from (timestamp, event_type, source, payload, turn_id, task_id) rows, oldest first.

    ``source_rows`` are (source, events, latest timestamp) totals for the whole log.
    """
    tools: dict[str, list[Any]] = {}
    intents: dict[str, list[Any]] = {}
    tiers: dict[str, list[Any]] = {}
    sources: dict[str, list[Any]] = {}
    turn_intent: dict[str, str] = {}
    task_turn: dict[str, str] = {}
    calls: list[tuple[str, str, str]] = []
    first = last = None
    count = 0

    def bump(table: dict[str, list[Any]], key: str, at: float, ok: bool, note: str = "",
             counted: bool = True) -> None:
        entry = table.setdefault(key, [0, 0, None, None, ""])
        entry[0] += 1 if counted else 0
        entry[1] += 0 if ok else 1
        if entry[2] is None or at >= entry[2]:
            entry[2], entry[3], entry[4] = at, ok, note[:120]

    for stamp, kind, source, raw, turn_id, task_id in rows:
        at = _epoch(stamp)
        if at is None:
            continue
        count += 1
        first = at if first is None else min(first, at)
        last = at if last is None else max(last, at)
        payload = _payload(raw)
        turn_id, task_id = turn_id or "", task_id or ""
        if turn_id and task_id:
            task_turn.setdefault(task_id, turn_id)
        if kind in {"TOOL_COMPLETED", "TOOL_FAILED"}:
            tool = str(payload.get("tool") or "")
            if not tool:
                continue
            failed = kind == "TOOL_FAILED"
            bump(tools, tool, at, not failed, str(payload.get("error") or "")[:120] if failed else "")
            calls.append((tool, turn_id, task_id))
        elif kind == "TURN_ROUTED" and turn_id:
            intent = str(payload.get("intent") or "")
            if intent:
                turn_intent[turn_id] = intent
                bump(intents, intent, at, True)
        elif kind in {"TURN_FAILED", "TURN_COMPLETED"} and turn_id in turn_intent:
            # A route's last outcome is its last turn's; a failed turn counts as a failure.
            entry = intents[turn_intent[turn_id]]
            if kind == "TURN_FAILED":
                entry[1] += 1
            if entry[2] is None or at >= entry[2]:
                entry[2], entry[3] = at, kind == "TURN_COMPLETED"
        elif kind in {"AUTHORITY_DENIED", "AUTHORITY_APPROVAL_REQUIRED"}:
            tier = str(payload.get("tier_name") or "")
            if tier:
                denied = kind == "AUTHORITY_DENIED"
                bump(tiers, tier, at, not denied,
                     f"{'denied' if denied else 'approval asked'}: {payload.get('tool') or payload.get('category') or ''}")
        elif kind == "SERVICE_DEGRADED":
            # Shutdown reports name the component they were about; others are their own source.
            about = str(payload.get("component") or source or "")
            entry = sources.setdefault(about, [0, 0, None, None, ""])
            entry[1] += 1
            entry[2], entry[3], entry[4] = at, False, str(payload.get("error") or "degraded")[:120]
    for source, events, stamp in source_rows:
        entry = sources.setdefault(str(source), [0, 0, None, None, ""])
        entry[0] = int(events)
        latest = _epoch(stamp) if stamp else None
        if latest is not None and (entry[2] is None or latest > entry[2]):
            entry[2], entry[3], entry[4] = latest, True, ""
    groups: dict[str, set[str]] = {}
    for tool, turn_id, task_id in calls:
        key = turn_id or task_turn.get(task_id, "") or (f"task:{task_id}" if task_id else "")
        if key:
            groups.setdefault(key, set()).add(tool)

    def freeze(table: dict[str, list[Any]]) -> dict[str, Count]:
        return {key: Count(*value) for key, value in table.items()}

    return Usage(first, last, count, freeze(tools), freeze(intents), freeze(tiers), freeze(sources),
                 tuple(Group(key, turn_intent.get(key, ""), frozenset(names)) for key, names in groups.items()))


def load_usage(path: Path, *, limit: int = 250_000) -> Usage:
    """Read the most recent ``limit`` usage events from the event log, read-only."""
    placeholders = ",".join("?" for _ in USAGE_TYPES)
    try:
        with sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                f"SELECT timestamp,event_type,source,payload_json,turn_id,task_id FROM events "
                f"WHERE event_type IN ({placeholders}) ORDER BY id DESC LIMIT ?",
                [*USAGE_TYPES, int(limit)],
            ).fetchall()
            source_rows = db.execute(
                "SELECT source, COUNT(*), MAX(timestamp) FROM events GROUP BY source").fetchall()
    except sqlite3.Error:
        rows, source_rows = [], []
    rows.reverse()
    return summarize(rows, source_rows)
