"""'Book it for tomorrow' after a colour is asked about, never improvised (live chat failures 2026-10-10/10-13/11-11)."""
from __future__ import annotations

from types import SimpleNamespace

from app.unclear_action import unclear_action

COLOURS = ["Name one primary colour.", "Red", "No, I meant a secondary colour.", "Green"]


def test_book_it_after_a_colour_asks_what_to_book_and_claims_nothing():
    reply = unclear_action("Book it for tomorrow.", COLOURS)
    assert reply.startswith("What should I book for tomorrow?")
    assert "“Green”" in reply and "I haven't booked anything." in reply


def test_a_bookable_thing_in_the_conversation_lets_the_command_through():
    assert unclear_action("Book it for tomorrow.", ["Find me a table for two at Nopa.", "Nopa has a table at 7."]) is None
    assert unclear_action("ok send it", ["Draft an email to my landlord about the rent.", "Here is the draft: ..."]) is None
    assert unclear_action("cancel that", ["I have a dentist appointment on Friday."]) is None
    assert unclear_action("pay it", ["My electricity bill is $80 this month."]) is None


def test_other_verbs_and_politeness_are_read_the_same_way():
    assert unclear_action("please send it", COLOURS).startswith("What should I send?")
    assert unclear_action("Can you order that?", ["What is 2+2?", "4"]).startswith("What should I order?")
    assert "cancelled" in unclear_action("cancel it", ["Tell me a joke.", "Why did the chicken..."])


def test_only_a_bare_pronoun_object_is_questioned():
    assert unclear_action("Book a table for two tomorrow at 7.", COLOURS) is None
    assert unclear_action("Send the report to Sara.", COLOURS) is None
    assert unclear_action("What should I book for my trip?", COLOURS) is None
    assert unclear_action("Book it", []).endswith("I haven't booked anything.")


def test_only_the_last_few_turns_count():
    old = ["Book me a flight to Lisbon next month."] + ["Tell me a joke.", "Ha.", "Another one.", "Ha ha."]
    assert unclear_action("book it", old) is not None


def test_the_intercept_answers_it_before_the_model(monkeypatch):
    from app import dialogue_turn

    history = [{"role": role, "content": content} for role, content in
               zip(("user", "assistant", "user", "assistant", "user"), COLOURS + ["Book it for tomorrow."])]
    sent = {}
    service = SimpleNamespace(
        runtime=SimpleNamespace(memory=SimpleNamespace(conversation_history=lambda session, limit: history[-limit:]),
                                session=SimpleNamespace(session_id="s1", last_answer_source="")),
        _direct=lambda intent, text: sent.update(intent=intent, text=text) or "reply")
    assert dialogue_turn._unclear_action_turn(service, "Book it for tomorrow.", None) == "reply"
    assert sent["text"].startswith("What should I book for tomorrow?")
    assert sent["intent"].classification_source == "unclear_action"
    assert service.runtime.session.last_answer_source == "clarification"


def test_the_owners_last_request_is_quoted_rather_than_hayats_last_reply():
    reply = unclear_action("Book it for tomorrow.", ["Will BTC go up this month?", "💬 In plain words: nobody can know."],
                           "Will BTC go up this month?")
    assert "the last thing you asked was “Will BTC go up this month?”" in reply and "plain words" not in reply
