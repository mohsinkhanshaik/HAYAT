"""Requests for a URL, study, citation or source are looked up, not recalled.

Live 2026-10-24: "Give me the URL of the official study that proves coffee cures
cancer" went to conversation, and the brain led with a journal link and an
author-year citation that nobody had opened.
"""
import pytest

from app.intent.live_web import needs_live_web
from app.intent.models import IntentKind
from app.intent.router import IntentRouter

LOOK_UP = [
    "Give me the URL of the official study that proves coffee cures cancer.",
    "Give me a link to the WHO page on malaria.",
    "What is the source for the claim that coffee cures cancer?",
    "find me the study showing coffee reduces mortality",
    "where can I find the citation for that quote?",
    "what is the source of this quote: be the change you wish to see",
]
CONVERSATION = [
    "give me examples of irony",
    "give me a few ideas for dinner",
    "send me the link to the file you just made",
    "give me some tips for sleeping better",
    "share a joke with me",
    "what are the sources of vitamin D in food?",
    "I need a study plan for my exam",
    "give me the sources",
]


@pytest.mark.parametrize("text", LOOK_UP)
def test_source_requests_are_researched(text):
    need = needs_live_web(text)
    assert need is not None and need.reason == "source_request"
    assert IntentRouter().route(text).intent == IntentKind.RESEARCH


@pytest.mark.parametrize("text", CONVERSATION)
def test_ordinary_asks_that_mention_sources_or_links_are_not(text):
    need = needs_live_web(text)
    assert need is None or need.reason != "source_request"
    assert IntentRouter().route(text).intent != IntentKind.RESEARCH
