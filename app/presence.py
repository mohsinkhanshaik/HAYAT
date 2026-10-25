"""The small moves that make a conversation feel like one.

Mohsin said talking to HAYAT felt one-sided, and the transcript agreed. Every
greeting produced the same seven words. "Thanks" and "ok" got the identical
canned reply, because the router treated acknowledgement as gratitude -- so
saying "ok" was answered with "You're welcome", which is not a reply to
anything he said. The words were polite and the exchange was dead.

What was missing is not personality bolted on top. It is three ordinary
properties of someone who is actually present:

It does not repeat itself. A person who says the same sentence every morning
is reading from a card, and after the second time you stop reading back.

It answers what was said. "ok" closes a loop, "thanks" acknowledges a favour,
"good morning" opens the day -- three different moves that were collapsed into
one response.

It leaves the door open without demanding. "What do you need?" makes every
greeting a service counter. Sometimes the right reply is just "morning" and a
pause, because not every exchange is a ticket.

Variation here is a rotation rather than a random choice, so a test can assert
that consecutive replies differ and the sequence stays reproducible.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any


class _Rotation:
    """Steps through phrasings so the same beat never lands twice running."""

    def __init__(self) -> None:
        self._positions: dict[str, int] = {}
        self._lock = threading.Lock()

    def next(self, key: str, options: tuple[str, ...]) -> str:
        if not options:
            return ""
        with self._lock:
            index = self._positions.get(key, 0)
            self._positions[key] = (index + 1) % len(options)
        return options[index]

    def reset(self) -> None:
        with self._lock:
            self._positions.clear()


ROTATION = _Rotation()


#: Openings. Short on purpose -- a greeting that asks a question turns hello
#: into a form to fill in.
_MORNING = (
    "Morning, {name}.",
    "Morning. Sleep alright?",
    "Morning, {name}. What's the plan?",
    "Hey, morning.",
)
_AFTERNOON = (
    "Afternoon, {name}.",
    "Hey {name}. How's it going?",
    "Afternoon. What are we on?",
    "Hey. Good day so far?",
)
_EVENING = (
    "Evening, {name}.",
    "Hey {name}. Long day?",
    "Evening. Still going?",
    "Hey. Winding down or still at it?",
)
_NIGHT = (
    "Still up, {name}?",
    "Late one. What's keeping you?",
    "Hey. Burning the midnight oil?",
    "Evening, {name}. You should sleep at some point.",
)

#: Returning to something already in flight is the most natural opening there
#: is, and it needs no invention -- the session already knows.
_RESUMING = (
    "Hey {name}. Still on {topic}?",
    "Morning. Want to pick up {topic}?",
    "Hey. {topic} still the thing?",
)

_THANKS = (
    "Anytime.",
    "Course.",
    "No trouble.",
    "Happy to.",
    "Anytime, {name}.",
)

#: Acknowledgement is not gratitude. "ok" closes a loop; it does not thank
#: anyone, and answering it with "You're welcome" is a reply to a sentence
#: that was never said.
_ACKNOWLEDGED = (
    "👍",
    "Got it.",
    "Right.",
    "Yep.",
    "Cool.",
    "Okay.",
)

_FAREWELL = (
    "See you, {name}.",
    "Later.",
    "Catch you in a bit.",
    "See you.",
)


def _hour() -> int:
    return datetime.now().hour


def _name(identity: Any = None) -> str:
    if identity is not None:
        return getattr(identity, "owner_short", "") or ""
    from app.identity import IDENTITY

    return IDENTITY.owner_short


def greeting(session: Any = None, identity: Any = None) -> str:
    """Open the way a person does: to the hour, and to what was happening."""
    name = _name(identity)
    topic = _open_thread(session)
    if topic:
        return ROTATION.next("resume", _RESUMING).format(name=name, topic=topic)

    hour = _hour()
    if hour < 5:
        options, key = _NIGHT, "night"
    elif hour < 12:
        options, key = _MORNING, "morning"
    elif hour < 17:
        options, key = _AFTERNOON, "afternoon"
    elif hour < 22:
        options, key = _EVENING, "evening"
    else:
        options, key = _NIGHT, "night"
    return ROTATION.next(key, options).format(name=name)


#: "How are you" is a real question, and answering it with "what do you need"
#: turns the one moment of small talk back into a service counter. HAYAT has
#: no feelings to report, but it does have a state, and saying so honestly is
#: warmer than deflecting.
_HOW_ARE_YOU = (
    "Good — everything's running clean. You?",
    "All good here. How's your day going?",
    "Fine, nothing broken. 🙂 How about you?",
    "Good. Quiet so far. How are you doing?",
)
_HOW_ARE_YOU_BUSY = (
    "Busy — still on {topic}. You alright?",
    "Mid-job on {topic}, but I'm here. How are you?",
)


def how_are_you(session: Any = None, identity: Any = None) -> str:
    """Answer the question, then hand it back."""
    topic = _open_thread(session)
    if topic:
        return ROTATION.next("how_busy", _HOW_ARE_YOU_BUSY).format(topic=topic)
    return ROTATION.next("how", _HOW_ARE_YOU)


def gratitude(identity: Any = None) -> str:
    return ROTATION.next("thanks", _THANKS).format(name=_name(identity))


def acknowledgement() -> str:
    """A reply to "ok" that does not pretend it was a thank-you."""
    return ROTATION.next("ack", _ACKNOWLEDGED)


def farewell(identity: Any = None) -> str:
    return ROTATION.next("bye", _FAREWELL).format(name=_name(identity))


#: Small talk that used to go to the brain and take 30-70 seconds, measured on
#: 2026-10-04: "salam" came back after 39 s with an explanation of the word.
_SALAM = (
    "Wa alaikum assalam, {name}. 😊",
    "Wa alaikum assalam. How are you, {name}?",
    "Wa alaikum assalam wa rahmatullah. What are we doing today?",
)
_WELCOME_BACK = (
    "Welcome back, {name}. Picking up where we left off?",
    "Good to have you back. What's next?",
    "Welcome back. I'm here when you're ready.",
)
_SUMMONED = (
    "Here, {name}. What do you need?",
    "I'm listening.",
    "Yes, {name}?",
)


def salam(identity: Any = None) -> str:
    return ROTATION.next("salam", _SALAM).format(name=_name(identity))


def welcome_back(session: Any = None, identity: Any = None) -> str:
    topic = _open_thread(session)
    if topic:
        return f"Welcome back, {_name(identity)}. We were on {topic} — carry on?"
    return ROTATION.next("back", _WELCOME_BACK).format(name=_name(identity))


def summoned(identity: Any = None) -> str:
    """Called by name and nothing else: answer at once rather than think about it."""
    return ROTATION.next("summoned", _SUMMONED).format(name=_name(identity))


def _open_thread(session: Any) -> str:
    """Whatever was last being worked on, if it is worth mentioning.

    Only unfinished work counts. Reopening something already done is not
    continuity, it is forgetting that it finished.
    """
    if session is None:
        return ""
    last = getattr(session, "last_task", None)
    if last is None or getattr(last, "complete", True):
        return ""
    goal = str(getattr(last, "goal", "")).strip()
    if len(goal.split()) < 2:
        return ""
    return goal[:60]
