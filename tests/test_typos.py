"""Fast, misspelt requests still reach the real tools; ordinary sentences keep their own words."""

from __future__ import annotations

import pytest

from app.intent.router import IntentRouter
from app.intent.typos import fix
from app.session import SessionState


@pytest.mark.parametrize("typed, modality, goal", [
    ("chek my routr", "network", "check my router"),
    ("whos on my wifi", "network", "who's on my wifi"),
    ("who is conected to my wifi", "network", "who is connected to my wifi"),
    ("how is my interent", "network", "how is my internet"),
    ("is csi conected", "csi", "is csi connected"),
    ("conect csi", "csi", "connect csi"),
    ("list my devises", "device", "list my devices"),
    ("any erthquakes near tokyo", "public_intelligence", "any earthquakes near tokyo"),
    ("wats happening around me", "public_intelligence", "what's happening around me"),
    ("hazard radr", "public_intelligence", "hazard radar"),
])
def test_misspelt_tool_requests_reach_their_tools(typed, modality, goal):
    routed = IntentRouter(None).route(typed, SessionState())
    assert routed.entities.get("modality") == modality and routed.goal == goal


@pytest.mark.parametrize("text", ["I love my mother very much", "The senior partner signed the contract",
                                  "what is a mutex?", "Open notepad", "hi"])
def test_ordinary_messages_are_not_rewritten(text):
    routed = IntentRouter(None).route(text, SessionState())
    assert routed.goal.lower().strip(" ?") == text.lower().strip(" ?") or routed.goal == text


def test_the_fixer_expands_shorthand_and_corrects_only_tool_words():
    assert fix("is any1 moving") == "is anyone moving"
    assert fix("chek the routr pls") == "check the router please"
    assert fix("The senior partner signed") == "The senior partner signed"  # no tool word near "senior"


def test_radar_questions_route_to_the_radar_through_the_router():
    for text in ("am I safe?", "any hazards near me", "what's happening around me"):
        assert IntentRouter(None).route(text, SessionState()).entities == {"modality": "public_intelligence"}, text


def test_project_dependencies_are_code_packages_not_a_program():
    router = IntentRouter(None)
    assert router.install_request("Install the project dependency") is None
    assert router.install_request("install the requirements") is None
    assert router.install_request("install VLC") == ("software", "VLC")
