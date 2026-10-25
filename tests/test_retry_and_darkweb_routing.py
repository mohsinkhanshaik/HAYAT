"""'Search again' re-runs the last search, dark-web searches reach the onion worker, and an ambiguous
'look again' is a camera look or a re-search by context.

Live 2026-10-24: "look all ways to make money on dark web" became the business-ideas list, "look again"
(meaning re-run the web search) opened the camera, and a frustrated "actually search the web" was taken
as a literal query.
"""
import pytest

from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState


def route(text, **state):
    session = SessionState()
    for key, value in state.items():
        setattr(session, key, value)
    return IntentRouter().route(text, session)


@pytest.mark.parametrize("text", [
    "look all ways to make money on dark web and look what suspious going in there",
    "look what is suspicious on the dark web",
    "find ransomware groups on the dark web",
    "monitor the darknet for mentions of my company",
    "search the dark web",
])
def test_dark_web_searches_go_to_the_onion_worker(text):
    intent = route(text)
    assert intent.intent is IntentKind.RESEARCH and intent.classification_source == "onion_research"
    assert intent.entities.get("onion") is True


@pytest.mark.parametrize("text", [
    "look up again", "search again", "look it up again on the web",
    "actually search the web properly", "search deeper", "do it properly",
])
def test_re_search_phrasings_repeat_the_last_search(text):
    intent = route(text, research_audit={"query": "ai"})
    assert intent.intent is IntentKind.CONTROL and intent.control == "RESEARCH_AGAIN"


def test_a_frustrated_actually_search_repeats_the_search_only_after_one_ran():
    complaint = "u are just talking around same things without actually look on web and stuff and dark web"
    assert route(complaint, research_audit={"query": "ai"}).control == "RESEARCH_AGAIN"
    # With no earlier search, it is not swallowed as a retry control.
    assert route(complaint).control != "RESEARCH_AGAIN"


def test_look_again_never_defaults_to_a_camera_activation():
    assert route("look again", last_camera_observation={"at": "x"}).control == 'RESEARCH_AGAIN'
    assert route("look again", research_audit={"query": "ai"}).control == "RESEARCH_AGAIN"
    assert route("look again").control == 'RESEARCH_AGAIN'
    # Naming the owner is always the camera, even mid-search.
    assert route("look at me again", research_audit={"query": "ai"}).intent is IntentKind.COMPUTER_CONTROL
