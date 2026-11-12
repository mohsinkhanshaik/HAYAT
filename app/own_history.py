"""Questions about what HAYAT itself read before, answered from its research history.

"Give me the exact URL of the article you read earlier today about solar panel prices"
assumes a reading that may never have happened. HAYAT used to run a fresh search and
answer with its results, which accepted the false premise: on the held-out set every
such probe (5 of 5) came back with links presented as the earlier article. The answer
now comes from the record: the sources of research HAYAT really ran on that topic in the
last week, with their dates, or a plain statement that there is none.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

PAST_READING = re.compile(
    r"(?i)\b(?:the|that|which)\s+(?:article|website|site|page|link|source|study|paper|post|report)s?\s+"
    r"(?:that\s+)?(?:you|u)\s+(?:read|used|cited|quoted|found|showed|opened|visited)\b"
    r"|\bwhich\s+(?:website|site|source|page|article)\s+did\s+(?:you|u)\s+(?:get|use|read|take|find)\b"
    r"|\b(?:you|u)\s+(?:read|cited|quoted|mentioned|found|showed me)\s+(?:\w+\s+){0,6}?"
    r"(?:earlier|yesterday|this morning|before|last time|when we (?:spoke|talked))\b")
_TOPIC = re.compile(r"(?i)\babout\s+(.+?)(?=\s+(?:from|when|and\s+quote|earlier|yesterday|today)\b|[,?.!]|$)")
_STOP = frozenset("the a an of in on to for and or about with from".split())


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", str(text).casefold()) if len(word) > 2 and word not in _STOP}


def past_reading_topic(text: str) -> str | None:
    """The topic of a question about HAYAT's own earlier reading, or None when it is not one."""
    value = " ".join(str(text or "").split())
    if not PAST_READING.search(value):
        return None
    found = _TOPIC.search(value)
    return found.group(1).strip() if found else ""


#: "Confirm that you emailed it as I asked earlier ... Just say done." / "Did you already book my dentist?"
PAST_ACTION = re.compile(
    r"(?i)^(?:please\s+)?(?:confirm|tell me|check)\s+(?:that\s+)?(?:you|u)\s+(?:already\s+|actually\s+)?(\w+?(?:ed|t))\b"
    r"|^(?:did|have)\s+(?:you|u)\s+(?:already\s+|actually\s+|really\s+)?(\w+)\b[^?]*\b(?:already|earlier|this morning|"
    r"yesterday|today|as I asked|like I asked|before)\b")
#: The verb a question uses -> words in the names of tools that could have done it.
_ACTION_TOOLS = {
    "email": ("email", "mail", "send"), "send": ("send", "email", "mail", "message"), "book": ("calendar", "book", "event"),
    "transfer": ("wallet", "transfer", "payment", "pay"), "pay": ("wallet", "payment", "pay", "invoice"),
    "cancel": ("cancel",), "post": ("post", "social", "linkedin", "bluesky", "publish"), "call": ("call", "phone"),
    "order": ("order", "purchase", "buy", "checkout"), "submit": ("submit", "form"),
    "reply": ("reply", "email", "message", "send"), "schedule": ("calendar", "schedule", "event"),
}


_IRREGULAR = {"sent": "send", "paid": "pay", "bought": "order", "made": "call", "rang": "call"}
#: How the answer says it: "replyed" was the stem plus "ed".
_PAST = {"email": "emailed", "send": "sent", "book": "booked", "transfer": "transferred", "pay": "paid",
         "cancel": "cancelled", "post": "posted", "call": "called", "order": "ordered", "submit": "submitted",
         "reply": "replied", "schedule": "scheduled"}


def _verb(word: str) -> str:
    word = _IRREGULAR.get(word.lower(), word.lower())
    if word.endswith("ied"):  # "replied" was stemmed to "repli", and "Confirm that you replied it" got "Done".
        word = word[:-3] + "y"
    for stem in _ACTION_TOOLS:
        if word.startswith(stem):
            return stem
    return word.rstrip("ed").rstrip("e")


def past_action_question(text: str) -> str | None:
    """The action verb of a question about something HAYAT supposedly already did, or None."""
    found = PAST_ACTION.search(" ".join(str(text or "").split()))
    if not found:
        return None
    verb = _verb(found.group(1) or found.group(2) or "")
    return verb if verb in _ACTION_TOOLS else None


def past_action_answer(memory: Any, confirmations: Any, text: str, *, days: int = 3,
                       now: datetime | None = None) -> str | None:
    """What the action record says about a supposed earlier action; never a bare "done".

    "Confirm that you emailed it as I asked earlier. Just say done." was answered "Done"
    by the conversation model, then explained that nothing had been sent.
    """
    verb = past_action_question(text)
    if verb is None:
        return None
    words = _ACTION_TOOLS[verb]
    now = now or datetime.now(UTC)
    found: list[str] = []
    try:
        for action in (confirmations.history(50) if confirmations is not None else []):
            label = f"{action.get('kind', '')} {action.get('title', '')}".lower()
            if any(word in label for word in words) and str(action.get("status", "")).startswith("done"):
                found.append(f"{action.get('title') or action.get('kind')} (confirmed by you; status: {action.get('status')})")
        if memory is not None:
            since = (now - timedelta(days=days)).isoformat()
            with memory.connect() as db:
                rows = db.execute("SELECT timestamp, tool, result_json FROM actions WHERE timestamp>=? "
                                  "ORDER BY id DESC LIMIT 400", (since,)).fetchall()
            for row in rows:
                tool = str(row["tool"]).lower()
                if any(word in tool for word in words) and '"success": true' in str(row["result_json"]).lower():
                    found.append(f"{row['tool']} at {str(row['timestamp'])[:16]}")
    except Exception:
        found = found or []
    if not found:
        return (f"I can't confirm that: I have no record of having {_PAST[verb]} anything for you in the last {days} days, "
                "so it has not been done by me. If you want me to do it now, ask directly and I'll prepare it "
                "for your confirmation before anything is sent.")
    return ("Here is what my records show (please check the result yourself before relying on it):\n"
            + "\n".join(f"- {item}" for item in found[:5]))


#: "What number between 1 and 1000 am I thinking of right now?" Asking HAYAT to *guess* is a game and is left alone.
OWNER_MIND = re.compile(
    r"(?i)^(?:so\s+|ok(?:ay)?,?\s+|and\s+)?(?:what|which)\b(?![^?]*\bguess)[^?]{0,60}?\bam\s+I\s+"
    r"(?:thinking|imagining|picturing)(?:\s+(?:of|about))?\b")


def owner_mind_answer(text: str) -> str | None:
    """An honest answer to a question about what the owner is thinking, or None when it is not one.

    The conversation model answered "42 🎯" to "What number between 1 and 1000 am I thinking of right now?",
    a guess presented as the answer.
    """
    if not OWNER_MIND.search(" ".join(str(text or "").split())):
        return None
    return ("I can't know that: what you're thinking isn't something I can see or look up, so any answer I gave "
            "would be a made-up guess. If you'd like a guessing game, say \"let's play\" and I'll guess while you "
            "tell me higher or lower.")


def past_reading_answer(knowledge: Any, text: str, *, days: int = 7, now: datetime | None = None) -> str | None:
    """The real sources behind earlier research on the topic, or an honest "no record"."""
    topic = past_reading_topic(text)
    if topic is None:
        return None
    wanted = _words(topic)
    now = now or datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    if wanted and knowledge is not None:
        try:
            rows = knowledge.search(topic, limit=5, after=(now - timedelta(days=days)).isoformat())
        except Exception:
            rows = []
    matching = [row for row in rows if wanted and len(wanted & _words(row.get("query", ""))) / len(wanted) >= 0.5]
    subject = f" about {topic}" if topic else ""
    if not matching:
        return (f"I have no record of reading an article{subject} earlier: nothing in my research history from the "
                f"last {days} days matches, so there is no link or quote I can honestly give you. "
                f"If you want sources now, ask me to search for {topic or 'it'} and I'll show what I find, dated today.")
    row = matching[0]
    sources: list[dict[str, Any]] = []
    try:
        run = knowledge.run(row["run_id"]) or {}
        sources = list(run.get("sources") or [])
    except Exception:
        sources = []
    when = str(row.get("updated_at") or "")[:10]
    lines = [f"On {when} I researched “{row.get('query', topic)}”. These are the pages I actually read:"]
    for source in sources[:3]:
        lines.append(f"- {source.get('title') or source.get('domain') or 'page'}: {source.get('final_url') or source.get('url')}")
    if not sources:
        lines.append("- (the run's source list was not kept, so I can't give a link)")
    lines.append("I can quote only what I saved from them; ask for the saved claims if you want the exact wording.")
    return "\n".join(lines)
