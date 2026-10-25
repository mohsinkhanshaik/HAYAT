"""Deterministic policy for what a memory may be used to claim.

Some memories describe durable truths ("the user's name is X"). Some describe
things that merely happened once ("a task ran and succeeded"). And some are
snapshots of a world that has since moved on: what the camera saw, what was on
screen, which window was focused, how much VRAM was free.

The third kind is the dangerous one. Labelling it "from an earlier session" in
the prompt and asking the model not to misuse it is a request, not a
guarantee -- the model still receives it as apparently relevant context and
can still answer "what is on my screen?" from a screenshot taken yesterday.
This module makes that structurally impossible instead: transient observations
from another session are filtered out of retrieval before the model ever sees
them.

Historical questions ("what was I wearing earlier?") are a separate, legitimate
case and are served by asking for them explicitly.
"""
from __future__ import annotations

import re
from enum import StrEnum


class StateClass(StrEnum):
    """How a memory relates to the present moment."""

    DURABLE = "DURABLE"          # true until changed: profile, preferences, facts, procedures
    HISTORICAL = "HISTORICAL"    # happened once, stays true as history: episodes, research, failures
    TRANSIENT = "TRANSIENT"      # a snapshot of a world that moves: camera, screen, window, resources
    LEGACY_UNTYPED = "LEGACY_UNTYPED"  # written before classification existed; treated conservatively


# Transient observations only describe "now" for the session that made them.
_TRANSIENT_CLASSES = {StateClass.TRANSIENT}

# Rows predating this policy carry no class. They are not assumed durable and
# are not assumed current: they may inform history, never the present.
_NEVER_CURRENT = {StateClass.TRANSIENT, StateClass.LEGACY_UNTYPED}

_DURABLE_TYPES = {"semantic", "procedural", "learning", "profile", "preference"}
_HISTORICAL_TYPES = {"episodic", "reflection", "project"}

# An episodic task record whose payload is a sensor snapshot is transient in
# substance even though its type says "episodic" -- that mismatch is exactly
# how yesterday's screenshot reached a new session.
_TRANSIENT_MARKERS = re.compile(
    r"(?i)\b(?:observed_at|camera[_ ]observation|screen[_ ]?shot|screenshot|"
    r"vram|window_title|active_window|foreground window|frame:\s*\d+x\d+)\b"
)


def classify(memory_type: str, content: str = "", metadata: dict | None = None) -> StateClass:
    """Decide a memory's state class from its type and substance."""
    normalized = (memory_type or "").strip().lower()
    blob = f"{content} {metadata or ''}"
    if normalized in {"camera_observation", "screen_observation", "window_state", "system_state"}:
        return StateClass.TRANSIENT
    if _TRANSIENT_MARKERS.search(blob):
        return StateClass.TRANSIENT
    if normalized in _DURABLE_TYPES:
        return StateClass.DURABLE
    if normalized in _HISTORICAL_TYPES:
        return StateClass.HISTORICAL
    if normalized == "working":
        return StateClass.TRANSIENT
    return StateClass.LEGACY_UNTYPED


def is_current_state_eligible(row: dict, *, session_id: str | None) -> bool:
    """Whether a memory may be used to describe the present.

    Durable and historical material is always eligible to be *cited*; what this
    gates is the claim that something is true *now*. A transient observation
    qualifies only when the current session is the one that observed it.
    """
    state_class = _row_class(row)
    if state_class not in _NEVER_CURRENT:
        return True
    if state_class is StateClass.LEGACY_UNTYPED:
        return False
    row_session = row.get("session_id")
    return bool(session_id) and row_session == session_id


def _row_class(row: dict) -> StateClass:
    raw = row.get("state_class")
    if raw:
        try:
            return StateClass(raw)
        except ValueError:
            pass
    return classify(str(row.get("memory_type") or ""), str(row.get("content") or ""))


def excluded_from_conversation(row: dict, *, session_id: str | None) -> bool:
    """Whether a row must be withheld from ordinary conversational context.

    Only cross-session transient snapshots are withheld. History stays
    available -- it is labelled, not hidden -- because "what happened earlier"
    is a reasonable thing to ask.
    """
    return _row_class(row) in _TRANSIENT_CLASSES and not is_current_state_eligible(row, session_id=session_id)


def eligible_conversation_context(row: dict, *, session_id: str | None) -> bool:
    """Keep indexed data from bypassing the memory and research evidence gates."""
    kind = str(row.get("object_type") or "")
    if kind in {"research_claim", "knowledge_summary"}:
        # The research manager supplies these with citations, freshness and
        # conflict checks. An index hit alone cannot make them a settled fact.
        return False
    if kind.startswith("memory:"):
        memory_type = kind.partition(":")[2]
        if classify(memory_type, str(row.get("content") or "")) is StateClass.TRANSIENT:
            return False
        return True
    return not excluded_from_conversation(row, session_id=session_id)
