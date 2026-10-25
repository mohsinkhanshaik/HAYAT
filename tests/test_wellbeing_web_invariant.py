"""Conversation and the final live-web check must agree across real contexts."""
from __future__ import annotations

import pytest

from app.intent.live_web import is_wellbeing_question, needs_live_web
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState


@pytest.mark.parametrize("context", ["empty", "research", "browser"])
@pytest.mark.parametrize("message", [
    "how are you today ?",
    "how are you today?",
    "How are you today???",
    "how are you doing today ! ?",
    "hey, how are you today?",
    "Hello. How are you feeling tonight?",
    "Good morning, how are you?",
    "HAYAT, how are you today ?",
    "hw r u today ?",
    "how’s it going today?",
    "how's your day going?",
    "how was your day?",
    "you good today?",
    "how are things today?",
    "how is everything going tonight?",
    "how have you been today?",
    "how have u been feeling lately?",
    "how do you feel today?",
    "are you okay today?",
    "HAYAT, are you alright now?",
    "how's your day going today?",
])
def test_wellbeing_cannot_be_reclassified_as_live_research(message, context):
    session = SessionState()
    if context != "empty":
        session.conversation_topic = "worldwide remote Python jobs"
        session.research_audit = {"query": session.conversation_topic, "status": "running"}
        session.last_user_input = "Find remote Python jobs worldwide"
        session.last_response = "I am searching public job sources."
    if context == "browser":
        session.last_browser_tab = {"url": "https://example.org/jobs", "title": "Jobs"}

    before = session.snapshot()
    intent = IntentRouter().route(message, session)

    assert intent.intent == IntentKind.CONVERSATION
    assert intent.response
    assert not intent.requires_tools
    assert is_wellbeing_question(message)
    assert needs_live_web(message) is None
    assert session.snapshot() == before  # Does not replace the ongoing research subject.


@pytest.mark.parametrize("message", [
    'Search for the phrase "how are you today"',
    "look up how are you today in Spanish",
    "Search the web for how are you today?",
    "hi, search for worldwide Python jobs",
    "hello, look up today's news",
    "I'm tired today, search for sleep research",
    "how are you going to find today's news?",
    "how are you today? Search for Python jobs",
    "how are you today and what is the weather in Mumbai?",
    "how are you finding the latest jobs today?",
])
def test_greeting_words_do_not_hide_real_lookups(message):
    assert not is_wellbeing_question(message)
    assert needs_live_web(message) is not None
    intent = IntentRouter().route(message, SessionState())
    assert intent.intent != IntentKind.CONVERSATION or intent.response is None


@pytest.mark.parametrize("message", [
    "how are you going to fix this",
    "how are you learning from mistakes?",
    "how are you connected to the camera?",
    "what's up with the database?",
    "how's your day planner implemented?",
    "how are you today? Actually, cancel the search.",
])
def test_work_questions_are_not_answered_with_canned_wellbeing(message):
    assert not is_wellbeing_question(message)
    intent = IntentRouter().route(message, SessionState())
    assert intent.intent != IntentKind.CONVERSATION or intent.response is None


@pytest.mark.parametrize("message", [
    "I'm online today",
    "I'm working on the web today",
    "I'm tired today",
    "hi again today",
])
def test_personal_remarks_are_not_explicit_search_commands(message):
    assert needs_live_web(message) is None
