"""Making the exchange feel like a conversation rather than a form.

Mohsin said talking to HAYAT felt one-sided. The transcript agreed: every
greeting produced the same seven words, and "ok" was answered with "You're
welcome" -- a reply to something he never said.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import presence
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState


@pytest.fixture(autouse=True)
def _fresh_rotation():
    presence.ROTATION.reset()
    yield
    presence.ROTATION.reset()


def _route(text: str, session=None):
    return IntentRouter().route(text, session or SessionState())


# ------------------------------------------------- acknowledgement vs thanks


@pytest.mark.parametrize("text", ["ok", "okay", "got it", "sure", "cool", "yep", "noted"])
def test_acknowledging_is_not_answered_as_gratitude(text):
    """"You're welcome" in reply to "ok" is a reply to a sentence that was
    never said, and it is most of why the exchange felt dead."""
    reply = _route(text).response or ""
    assert "welcome" not in reply.lower(), f"{text!r} was treated as thanks"
    assert reply, "it should still say something"


@pytest.mark.parametrize("text", ["thanks", "thank you", "cheers", "appreciate it"])
def test_thanks_is_answered_as_thanks(text):
    assert _route(text).response


def test_goodbye_is_recognised_as_leaving():
    reply = _route("good night").response or ""
    assert reply and "welcome" not in reply.lower()


# ------------------------------------------------------------- not a card


def test_a_greeting_does_not_repeat_itself():
    """Someone who says the same sentence every morning is reading from a
    card, and after the second time you stop reading back."""
    replies = [presence.greeting() for _ in range(3)]
    assert len(set(replies)) > 1, f"same greeting every time: {replies}"


def test_thanks_and_acknowledgement_vary_too():
    assert len({presence.gratitude() for _ in range(4)}) > 1
    assert len({presence.acknowledgement() for _ in range(4)}) > 1


def test_greetings_do_not_all_demand_a_task():
    """"What do you need?" makes every hello a service counter."""
    openings = [presence.greeting() for _ in range(8)]
    plain = [item for item in openings if "?" not in item]
    assert plain, f"every greeting asked for work: {openings}"


def test_the_greeting_uses_his_name_at_least_sometimes():
    assert any("Mohsin" in presence.greeting() for _ in range(8))


# --------------------------------------------------------------- continuity


def test_it_picks_up_unfinished_work_instead_of_starting_cold():
    session = SessionState()
    session.last_task = SimpleNamespace(goal="fix the printer driver", complete=False)

    opening = presence.greeting(session)

    assert "printer driver" in opening


def test_finished_work_is_not_reopened():
    """Reopening something already done is not continuity, it is forgetting
    that it finished."""
    session = SessionState()
    session.last_task = SimpleNamespace(goal="fix the printer driver", complete=True)
    assert "printer driver" not in presence.greeting(session)


def test_how_are_you_is_answered_and_handed_back():
    reply = presence.how_are_you()
    assert "?" in reply, "small talk should go both ways"
    assert "what do you need" not in reply.lower()


def test_how_are_you_mentions_work_in_flight():
    session = SessionState()
    session.last_task = SimpleNamespace(goal="index the photo archive", complete=False)
    assert "photo archive" in presence.how_are_you(session)


# ------------------------------------------------------- through the router


def test_the_router_uses_presence_for_greetings():
    first = _route("hi").response
    second = _route("hi").response
    assert first and second and first != second


def test_greeting_still_routes_as_conversation_not_a_task():
    intent = _route("hello")
    assert intent.intent is IntentKind.CONVERSATION
    assert not intent.is_action


def test_a_real_request_is_untouched_by_any_of_this():
    assert _route("open notepad").is_action
