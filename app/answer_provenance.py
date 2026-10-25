"""How the last answer was made, in plain words, for the conversation details panel.

An answer can come from code (the calculator, the calendar, letter counting),
from a fixed rule (the money stop, a standing instruction, the clock), or
from HAYAT's brain -- and a brain answer may have questioned itself first and
been reviewed. Showing which, beside the chat, lets the owner judge how much
to trust it without asking.
"""
from __future__ import annotations

from typing import Any

_FIXED = {
    "money_guard": "Fixed rule: payments are stopped in code (no model).",
    "standing_instruction": "Fixed rule: your standing instruction was recorded (no model).",
    "research_audit": "From the recorded research audit (no model).",
}


def describe(runtime: Any) -> list[str]:
    session = getattr(runtime, "session", None)
    source = str(getattr(session, "last_answer_source", "") or "")
    if not source:
        return ["No answer yet."]
    if source == "exact":
        return ["Worked out by code: exact arithmetic, the calendar or counting (no model, no guessing)."]
    if source in _FIXED:
        return [_FIXED[source]]
    if source != "brain":
        return [f"Answered directly by HAYAT's own rules ({source.replace('_', ' ')})."]
    conversation = getattr(runtime, "conversation", None)
    model = getattr(getattr(runtime, "model", None), "model", None) or "the local model"
    lines = [f"Written by HAYAT's brain ({model})."]
    assessment = getattr(conversation, "last_assessment", None)
    depth = getattr(getattr(assessment, "depth", None), "label", "")
    if depth:
        words = getattr(assessment, "word_count", 0)
        lines.append(f"Answer depth: {depth}" + (f", {words} words." if words else "."))
    inquiry = getattr(conversation, "last_inquiry", None) or {}
    if inquiry.get("state") == "examined":
        open_points = inquiry.get("open_points", 0)
        lines.append(f"Questioned itself first: {inquiry.get('questions', 0)} question"
                     f"{'s' if inquiry.get('questions', 0) != 1 else ''}"
                     + (f", {open_points} left open and said so." if open_points else ", all settled."))
    review = getattr(conversation, "last_dual_review", None) or {}
    if review.get("state") == "reviewed":
        lines.append("Reviewed by a skeptical check: " + ("revised before you saw it." if review.get("revised")
                                                          else "kept as written."))
    standing = (getattr(session, "standing_instruction", None) or {})
    if standing.get("rule"):
        left = standing.get("remaining")
        lines.append(f"Standing instruction in force: {standing['rule']}"
                     + (f" ({left} more answer{'s' if left != 1 else ''})." if left else "."))
    return lines
