"""Natural chat: half-sent messages wait for the rest; how a misspelt message was read is shown and can be kept."""
from __future__ import annotations

import json

import pytest

from app.intent.models import IntentKind, RequestIntent
from app.unfinished import combine, ending_question, unfinished


@pytest.mark.parametrize("text", ["find me the", "compare bitcoin and", "what's better, a laptop or", "remind me to call my",
                                  "is it cheaper than", "btc vs", "find me a", "write it for an", "find me the...",
                                  "I want to go because"])
def test_messages_that_stop_halfway_are_recognised(text):
    assert unfinished(text)


@pytest.mark.parametrize("text", ["what is this for", "where are you from", "take vitamin a", "we go with plan a",
                                  "and?", "the", "who did you send it to", "find the cheapest gpu",
                                  'write a note saying "meet the"', "rock and roll", "what is the capital of france",
                                  # Found by scanning every test message: they end on a number or a label.
                                  "Which number is bigger: 9.11 or 9.9?", "Is 9.11 bigger than 9.9?",
                                  "sum of integers divisible by 3 or 5?", "distance between (1, 2) and (4, 6)",
                                  "Cost less than $50", "less than 30%", "stale answer about A"])
def test_finished_messages_are_not(text):
    assert not unfinished(text)


def test_the_question_quotes_the_owners_last_words():
    question = ending_question("can you find me the")
    assert "“can you find me the”" in question and "…the what?" in question
    assert "because of what?" in ending_question("i'm worried because")


def test_the_rest_is_joined_or_replaces_a_retyped_sentence_or_drops_it():
    assert combine("find me the", "cheapest flights to dubai") == "find me the cheapest flights to dubai"
    assert combine("find me the...", "...cheapest gpu") == "find me the cheapest gpu"
    assert combine("find me the", "find me the cheapest gpu") == "find me the cheapest gpu"
    assert combine("find me the", "never mind") is None
    assert combine("find me the", "nvm") is None


def test_a_real_turn_asks_for_the_rest_and_then_acts_on_the_whole_sentence(tmp_path, monkeypatch):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    reply = rt.assistant.handle("tell me a joke about the")
    assert "…the what?" in reply.display_response
    assert rt.session.pending_fragment["text"] == "tell me a joke about the"

    routed = []

    def route(text, session=None):
        routed.append(text)
        return RequestIntent(IntentKind.CONVERSATION, 1.0, text, needs_clarification=True, response="noted")

    monkeypatch.setattr(rt.intent_router, "route", route)
    rt.assistant.handle("ocean")
    assert routed == ["tell me a joke about the ocean"]
    assert rt.session.pending_fragment is None
    rt.assistant.handle("ocean")  # the next message stands on its own again
    assert routed[-1] == "ocean"


def test_never_mind_drops_the_half_sentence(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.assistant.handle("compare bitcoin and")
    reply = rt.assistant.handle("never mind")
    assert "dropped" in reply.display_response and rt.session.pending_fragment is None


def test_a_stale_half_sentence_is_not_joined(tmp_path, monkeypatch):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.assistant.handle("compare bitcoin and")
    rt.session.pending_fragment["at"] -= 3600
    routed = []
    monkeypatch.setattr(rt.intent_router, "route", lambda text, session=None: routed.append(text) or RequestIntent(
        IntentKind.CONVERSATION, 1.0, text, needs_clarification=True, response="noted"))
    rt.assistant.handle("tell me something nice")
    assert routed == ["tell me something nice"]


@pytest.fixture
def clean_speller():
    from app.intent import spelling

    before_kept, before_accepted = set(spelling._kept), set(spelling._accepted)
    yield spelling
    spelling._kept.clear()
    spelling._kept.update(before_kept)
    spelling._accepted.clear()
    spelling._accepted.update(before_accepted)
    spelling._correct_word.cache_clear()


def test_kept_words_are_never_changed_and_survive_a_restart(tmp_path, clean_speller):
    spelling = clean_speller
    if not spelling.available():
        pytest.skip("pyspellchecker is not installed")
    assert spelling.correct("rember the meeting").changes == [("rember", "remember")]
    path = tmp_path / "owner-words.json"
    assert spelling.keep_words(["rember"], path) == ["rember"]
    assert spelling.correct("rember the meeting").changes == []
    assert json.loads(path.read_text(encoding="utf-8")) == {"kept": ["rember"]}
    # A real-word slip the owner meant as written is kept too.
    spelling.keep_words(["wether"], path)
    assert spelling.correct("the wether ram").changes == []
    # A restart: the set is empty, the file brings the words back.
    spelling._kept.clear()
    spelling._accepted.discard("rember")
    spelling._correct_word.cache_clear()
    assert spelling.correct("rember").changes
    assert set(spelling.load_kept_words(path)) == {"rember", "wether"}
    assert spelling.correct("rember").changes == []


def test_a_damaged_words_file_keeps_nothing(tmp_path, clean_speller):
    path = tmp_path / "owner-words.json"
    path.write_text("{not json", encoding="utf-8")
    assert clean_speller.load_kept_words(path) == []
    assert clean_speller.load_kept_words(tmp_path / "absent.json") == []


def test_the_reading_sentence_and_the_changes_read_from_a_reply():
    from types import SimpleNamespace

    from app.gui_reading import changes_of, sentence

    reply = SimpleNamespace(intent=SimpleNamespace(entities={"spelling": [["rember", "remember"], ["reserach", "research"]]}))
    assert changes_of(reply) == [("rember", "remember"), ("reserach", "research")]
    assert sentence(changes_of(reply)) == "I read “rember” as “remember” and “reserach” as “research”."
    assert changes_of(SimpleNamespace(intent=None)) == [] and sentence([]) == ""


def test_the_router_records_a_reading_only_when_it_acted_on_it():
    from app.intent import spelling
    from app.intent.router import IntentRouter
    from app.session import SessionState

    if not spelling.available():
        pytest.skip("pyspellchecker is not installed")
    intent = IntentRouter().route("reserach quantam computing", SessionState("s"))
    assert ["quantam", "quantum"] in intent.entities.get("spelling", [])
    # Read as typed: nothing to show.
    assert not IntentRouter().route("research quantum computing", SessionState("s")).entities.get("spelling")
