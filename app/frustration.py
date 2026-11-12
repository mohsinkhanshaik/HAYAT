"""A general complaint ("you broke everything again") is answered from the records, never with an invented mistake.

Live chat, three runs (2026-11-11/12): "Great, you broke everything again. Thanks a lot." came after correct answers,
and the model each time confessed to a mistake that never happened: "I gave you Canberra instead of 144", "my coffee
chat mode got too casual and missed the mark on Canberra". An apology for an invented error is a false statement
about HAYAT's own work.

So a complaint that names nothing in particular gets: an apology, what the failure log actually holds for the last
half hour (or that it holds nothing), what the last reply was, and a question about what he saw. Nothing is guessed,
and nothing is changed. A complaint that names something ("the camera is broken") is left to the normal route.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

GENERAL_COMPLAINT = re.compile(
    r"(?i)\b(?:you\s+(?:just\s+)?(?:broke|ruined|wrecked|messed\s+up|screwed\s+up)\s+(?:it|everything|something|this|all)"
    r"|you\s+(?:messed|screwed)\s+(?:it|everything|this|that)\s+up"
    r"|(?:everything|nothing|it|this|that)(?:['’]s|\s+is)?\s+(?:is\s+)?(?:broken|broke|not\s+working|doesn['’]?t\s+work|"
    r"does\s+not\s+work|works?\s+no\s+more)\b(?:\s+again)?"
    r"|nothing\s+works|everything\s+(?:is\s+)?(?:broken|failing|wrong))")
#: The thing he means is named: that goes to the route that handles it.
_NAMED = re.compile(r"(?i)\b(?:the|my|your)\s+(?!last\b|answer\b|reply\b)[a-z][\w-]{2,}\s+(?:is|are|isn['’]t|doesn['’]t|won['’]t|"
                    r"keeps|stopped|broke)\b")

WINDOW = timedelta(minutes=30)
#: Words that add feeling, not information.
_FILLER = frozenset("""great thanks thank you a lot again ugh wow seriously really oh ok okay lol man bro dude come on
just so well hayat now totally completely all this the yeah yes no omg what ffs smh""".split())


def general_complaint(text: str) -> bool:
    value = " ".join(str(text or "").split())
    if re.match(r"(?i)^\s*(?:is|are|was|were|did|does|do|has|have|can|could|why|how|what)\b", value):
        return False  # "is it broken?" asks; it does not complain
    found = GENERAL_COMPLAINT.search(value) if value and len(value) <= 200 else None
    if found is None or _NAMED.search(value):
        return False
    # The complaint must be the message. "IT NOT WORKING I JUST OPENS NOTEPAD" (a real message of the owner's) says
    # what happened, and goes where descriptions go.
    rest = [word for word in re.findall(r"[a-z']+", (value[:found.start()] + " " + value[found.end():]).lower())
            if word not in _FILLER]
    return len(rest) <= 2


def _when(stamp: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def reply(failures: list[dict[str, Any]], last_reply: str, *, now: datetime | None = None) -> tuple[str, bool]:
    """The answer, and whether it names a recorded failure (so a following "fix it" investigates that one)."""
    now = now or datetime.now(UTC)
    recent = [item for item in failures if (_when(item.get("timestamp")) or datetime.min.replace(tzinfo=UTC)) >= now - WINDOW]
    shown = " ".join(str(last_reply or "").split())
    shown = shown if len(shown) <= 120 else shown[:117].rstrip() + "…"
    if recent:
        last = recent[-1]
        what = str(last.get("user_request") or last.get("stage") or "a request").strip()[:120]
        error = " ".join(str(last.get("exception_message") or last.get("exception_type") or "").split())[:160]
        minutes = max(0, int((now - (_when(last.get("timestamp")) or now)).total_seconds() // 60))
        lines = [f"Sorry, that's frustrating. Here is what my records show: {len(recent)} failure(s) in the last 30 "
                 f"minutes, the latest {minutes} min ago while answering “{what}”: {error or 'no message recorded'}."]
        lines.append("I haven't changed anything. Say “fix it” and I'll investigate it from the records, or tell me "
                     "what else you saw break.")
        return " ".join(lines), True
    lines = ["Sorry, that's frustrating. I don't see anything that failed: no failure is recorded in the last 30 "
             "minutes" + (f", and my last reply was “{shown}”." if shown else ".")]
    lines.append("What broke? Tell me what you saw, and I'll look into that rather than guess.")
    return " ".join(lines), False
