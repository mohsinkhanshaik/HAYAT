"""Instructions that last beyond one message: "for the next two answers, reply with one word".

Asked exactly that, HAYAT answered the previous question again and then
ignored the instruction. A standing instruction is now recognised, confirmed
without a model call, kept in the session, applied to each following answer
and counted down; "back to normal" ends it.
"""
from __future__ import annotations

import re

_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "a couple of": 2, "a few": 3}
_COUNTED = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?[,\s]+|please\s+|now[,\s]+)*for\s+the\s+next\s+(?P<count>\d{1,2}|"
    + "|".join(sorted(_NUMBERS, key=len, reverse=True))
    + r")\s+(?:answers?|replies|responses?|questions?|messages?)\s*[,:-]?\s*(?P<rule>\S.{2,200}?)[.!]*\s*$")
_UNTIL_CHANGED = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?[,\s]+|please\s+)*(?:from\s+now\s+on|until\s+i\s+say\s+otherwise|for\s+the\s+rest\s+of\s+"
    r"(?:this|our)\s+(?:chat|conversation))\s*[,:-]?\s*(?P<rule>\S.{2,200}?)[.!]*\s*$")
#: The rule must be about how to answer, so "from now on, I live in Toronto" is a fact, not an instruction.
_ABOUT_ANSWERS = re.compile(
    r"(?i)\b(?:reply|answer|respond|write|speak|talk|use|keep|be|give|only|always|never|don't|do not|no)\b")
CANCEL = re.compile(
    r"(?i)^\s*(?:ok(?:ay)?[,\s]+)?(?:(?:go\s+)?back\s+to\s+normal(?:\s+answers)?|stop\s+(?:doing\s+)?that|"
    r"you\s+can\s+stop(?:\s+(?:doing\s+)?that)?|forget\s+(?:that|the)\s+(?:instruction|rule)|normal\s+answers\s+again)"
    r"[.!]*\s*$")


def parse(text: str) -> tuple[str, int | None] | None:
    """(rule, how many answers or None for until changed), or None."""
    value = " ".join(str(text or "").split())
    match = _COUNTED.match(value)
    if match:
        raw = match.group("count").lower()
        count = int(raw) if raw.isdigit() else _NUMBERS[raw]
        rule = match.group("rule").strip()
        return (rule, max(1, min(count, 50))) if _ABOUT_ANSWERS.search(rule) else None
    match = _UNTIL_CHANGED.match(value)
    if match and _ABOUT_ANSWERS.search(match.group("rule")):
        return match.group("rule").strip(), None
    return None


def confirmation(rule: str, count: int | None) -> str:
    """"Okay — for the next 2 answers, I'll reply with exactly one word." """
    mine = re.sub(r"(?i)\byour\b", "my", rule.strip())
    mine = mine[0].lower() + mine[1:]
    if count:
        return f"Okay — for the next {count} answer{'s' if count != 1 else ''}, I'll {mine}."
    return f"Okay — from now on I'll {mine}. Say “back to normal” to stop."
