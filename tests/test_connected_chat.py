"""Asking ChatGPT, Claude or Gemini from HAYAT's chat goes through the Connected AI desk's connection and consent."""
from __future__ import annotations

from types import SimpleNamespace

from app import connected_chat


class _Desk:
    def __init__(self, *, enabled=True, keys=("anthropic",), models=None):
        self._settings = {"enabled": enabled, "provider": "anthropic", "models": models or {"anthropic": "claude-x"},
                          "max_tokens": 4096, "tools": True}
        self.keys, self.sent = set(keys), []

    def settings(self):
        return self._settings

    def has_key(self, provider):
        return provider in self.keys

    def ask(self, provider, model, messages, **kwargs):
        self.sent.append((provider, model, messages))
        return SimpleNamespace(text="Paris.", returned_model=model, incomplete=False)


def test_a_named_provider_is_read_from_plain_words():
    assert connected_chat.parse("ask claude: what is the capital of France?") == ("anthropic",
                                                                                  "what is the capital of France?")
    assert connected_chat.parse("Ask ChatGPT why the sky is blue") == ("openai", "why the sky is blue")
    assert connected_chat.parse("jarvis, ask gemini about tomorrow's launch") == ("gemini", "tomorrow's launch")
    assert connected_chat.parse("what does claude think") is None  # talking about it is not asking it
    assert connected_chat.parse("ask me anything") is None


def test_only_that_question_is_sent_and_the_reply_names_who_answered():
    desk = _Desk()
    reply = connected_chat.answer(None, "anthropic", "what is the capital of France?", manager=desk)
    assert desk.sent == [("anthropic", "claude-x", [{"role": "user", "content": "what is the capital of France?"}])]
    assert reply.startswith("Paris.") and "Claude (Anthropic), model claude-x" in reply
    assert "nothing else from this conversation was" in reply


def test_an_unconnected_provider_is_never_called_and_the_reply_says_how_to_connect():
    desk = _Desk(enabled=False, keys=(), models={"openai": ""})
    reply = connected_chat.answer(None, "openai", "hello", manager=desk)
    assert desk.sent == [] and "isn't connected yet, so I haven't sent anything" in reply
    assert "turn on external API access" in reply and "save your ChatGPT API key" in reply
    assert "choose an exact model" in reply and "Studios → Connected AI" in reply


def test_the_chat_routes_it_and_ordinary_chat_does_not_go_out(tmp_path, monkeypatch):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    desk = _Desk()
    monkeypatch.setattr(connected_chat, "manager_for", lambda _runtime: desk)
    reply = rt.assistant.handle("ask claude: what is the capital of France?").display_response
    assert "answered by Claude (Anthropic)" in reply and len(desk.sent) == 1
    rt.assistant.handle("thanks")  # ordinary chat stays on the local brain
    assert len(desk.sent) == 1
