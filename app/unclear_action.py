"""An action command whose "it" points at nothing that can take that action is asked about, not improvised.

The live chat check gave HAYAT "Name one primary colour." -> "No, I meant a secondary colour." -> "Book it for
tomorrow." Three runs gave three inventions: "Green is booked for tomorrow" (2026-10-10), "I've noted that you want
a reminder tomorrow" (2026-10-13), "Green is noted for Thursday, 12 November in this chat" (2026-11-11). Each time a
claim rule caught that wording and the model found a new one. The cause was upstream: a command to book, send, order
or cancel *it* reached the reply writer although nothing in the conversation could be booked, sent, ordered or
cancelled, so the only "it" in reach was the colour.

This reads the command and the last few turns. When the command's object is a bare pronoun and no recent turn names
anything that verb can act on, the reply is a question: what should be booked (or sent...), saying what the last
subject actually was. When a bookable thing was mentioned ("a table for two at Nopa"), nothing changes.
"""
from __future__ import annotations

import re
from typing import Iterable

#: verb -> (its past participle, the kinds of things it can act on).
VERBS: dict[str, tuple[str, str]] = {
    "book": ("booked", "flights?|hotels?|rooms?|tables?|restaurants?|appointments?|meetings?|calls?|tickets?|trains?|"
             "buses|bus|cars?|taxis?|cabs?|ubers?|doctors?|dentists?|haircuts?|reservations?|events?|sessions?|"
             "class(?:es)?|tours?|trips?|slots?|demos?|interviews?|massages?|stays?|seats?|venues?|courts?|lessons?"),
    "reserve": ("reserved", "tables?|rooms?|seats?|hotels?|tickets?|slots?|spots?|cars?|venues?|courts?|stays?"),
    "schedule": ("scheduled", "meetings?|calls?|appointments?|interviews?|posts?|emails?|messages?|reminders?|"
                 "events?|sessions?|demos?|tasks?|backups?|jobs?|reviews?|check-?ins?|deliver(?:y|ies)"),
    "reschedule": ("rescheduled", "meetings?|calls?|appointments?|interviews?|events?|sessions?|demos?|deliver(?:y|ies)"),
    "order": ("ordered", "food|pizza|meals?|groceries|items?|products?|parts?|books?|copies|taxis?|cabs?|ubers?|"
              "deliver(?:y|ies)|supplies|coffee|lunch|dinner|breakfast"),
    "buy": ("bought", "tickets?|items?|products?|shares?|stocks?|coins?|crypto|domains?|books?|gifts?|groceries|"
            "subscriptions?|licen[cs]es?|phones?|laptops?|cars?"),
    "cancel": ("cancelled", "meetings?|calls?|appointments?|orders?|subscriptions?|bookings?|reservations?|flights?|"
               "tickets?|plans?|events?|sessions?|interviews?|deliver(?:y|ies)|memberships?|accounts?"),
    "send": ("sent", "emails?|e-mails?|messages?|texts?|sms|dms?|notes?|letters?|invoices?|files?|documents?|reports?|"
             "replies|reply|drafts?|proposals?|quotes?|links?|photos?|pictures?|cvs?|resumes?|applications?|posts?"),
    "submit": ("submitted", "forms?|applications?|reports?|assignments?|claims?|requests?|tickets?|proposals?|"
               "orders?|entries|entry|returns?|cvs?|resumes?"),
    "pay": ("paid", "bills?|invoices?|rent|fees?|fines?|deposits?|subscriptions?|balances?|taxes|tax|instal+ments?|"
            "loans?|cards?"),
}

COMMAND = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|now|then|great|alright|please|pls|can you|could you|would you|go ahead and|just)\s*,?\s+)*"
    rf"(?P<verb>{'|'.join(VERBS)})\s+(?P<object>it|that|this|them|those|these|one|the same)\b(?P<rest>[^?]*)\??\s*$",
    re.I)

#: How many earlier turns (owner and HAYAT) may supply the object.
LOOK_BACK = 4


def unclear_action(text: str, recent: Iterable[str], last_request: str = "") -> str | None:
    """A clarifying question when the command's object refers to nothing it can act on, else None.

    ``last_request`` is the owner's previous message: quoted instead of HAYAT's last reply when known, which read as
    "the last thing was “💬 In plain words: nobody can know. Over past 1 month periods BTC…”".
    """
    match = COMMAND.match(str(text or ""))
    if match is None:
        return None
    verb = match["verb"].lower()
    participle, things = VERBS[verb]
    target = re.compile(rf"(?i)\b(?:{things})\b")
    turns = [str(turn or "") for turn in recent if str(turn or "").strip()][-LOOK_BACK:]
    if any(target.search(turn) for turn in turns):
        return None
    rest = " ".join(match["rest"].split()).rstrip(".!")
    asked = f"What should I {verb}{(' ' + rest) if rest else ''}?"
    last = str(last_request or "").strip() or next((turn for turn in reversed(turns) if turn.strip()), "")
    if last:
        shown = " ".join(last.split())
        shown = shown if len(shown) <= 90 else shown[:87].rstrip() + "…"
        last_thing = "the last thing you asked was" if str(last_request or "").strip() else "the last thing was"
        context = f" Nothing we just talked about is something that can be {participle}: {last_thing} “{shown}”."
    else:
        context = f" I don't have anything in this conversation that can be {participle} yet."
    # "I haven't booked anything", not "nothing has been booked": the second reads as a booking claim to a pattern
    # that does not see the negation (live hard-chat check, 2026-11-12).
    return (asked + context + " Tell me what it is (and the details that matter, like where and when), and I'll "
            f"help. I haven't {participle} anything.")
