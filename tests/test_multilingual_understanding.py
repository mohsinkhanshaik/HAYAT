"""HAYAT understands Hinglish, Hindi, Urdu, Arabic and Telugu, and routes them by what they mean."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.multilingual import detect, english_reading


@pytest.mark.parametrize("text,language", [
    ("kya haal hai bhai", "hi"), ("mujhe kal subah 7 baje uthana", "hi"),
    ("aaj mausam kaisa hai hyderabad mein", "hi"), ("nenu bagunnanu, meeru ela unnaru", "te"),
    ("ana bikhair, shukran", "ar"), ("हयात आज मौसम कैसा है", "hi"), ("آج موسم کیسا ہے", "ur"),
    ("كيف حالك", "ar"), ("నువ్వు ఎలా ఉన్నావు", "te"),
    ("I am in Austin", None), ("how are you", None), ("main road is closed", None), ("Mohsin hai", None),
])
def test_the_language_of_a_message_is_recognised(text, language):
    assert detect(text) == language


class _Model:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def generate(self, messages, **kwargs):
        self.calls += 1
        return SimpleNamespace(content=self.reply)


def test_a_reading_is_made_only_when_needed_and_only_once():
    model = _Model("What is the weather in Hyderabad today?")
    assert english_reading("aaj mausam kaisa hai hyderabad mein", model) == "What is the weather in Hyderabad today?"
    assert english_reading("aaj mausam kaisa hai hyderabad mein", model) == "What is the weather in Hyderabad today?"
    assert model.calls == 1
    assert english_reading("what is the weather in Hyderabad today?", model) is None  # English needs no reading
    assert english_reading("kya haal hai bhai", _Model("kya haal hai bhai")) is None  # no real reading came back
    assert english_reading("kya haal hai bhai", None) is None


def test_hinglish_that_chat_could_not_place_is_routed_by_its_meaning(tmp_path):
    """"aaj mausam kaisa hai hyderabad mein" fell through to plain chat, where weather is answered from memory."""
    from app import multilingual
    from tests.test_assistant_service import runtime

    multilingual._READINGS.clear()
    rt = runtime(tmp_path)
    rt.model = _Model("What is the weather in Hyderabad right now?")
    routed = []
    original = rt.assistant._handle_inner

    def spy_route(text, session=None, _route=rt.intent_router.route):
        result = _route(text, session)
        routed.append((text, result.classification_source))
        return result

    rt.intent_router.route = spy_route
    try:
        original("aaj mausam kaisa hai hyderabad mein")
    except Exception:
        pass  # what happens after routing (a live search) is not this test's concern
    assert routed[0] == ("aaj mausam kaisa hai hyderabad mein", "safe_fallback")
    assert routed[1][0] == "What is the weather in Hyderabad right now?" and routed[1][1] != "safe_fallback"
