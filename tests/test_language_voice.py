"""English unless asked, Arabic spoken by an Arabic voice, and no copied paragraphs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.answers import drop_repeated_paragraphs
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState
from app.voice.tts import BilingualTTS, TextToSpeechProvider, split_scripts


class FakeVoice(TextToSpeechProvider):
    name = "fake"

    def __init__(self, log, label, available=True):
        self.log, self.label, self.available = log, label, available

    def speak(self, text, wait=False):
        self.log.append((self.label, text))

    def stop(self):
        pass

    @property
    def is_speaking(self):
        return False


# ------------------------------------------------------------------ speech
def test_a_mixed_reply_is_cut_into_english_and_arabic_runs():
    assert split_scripts("Shukran means thank you: شُكْرًا. Say it twice.") == [
        ("en", "Shukran means thank you:"), ("ar", "شُكْرًا."), ("en", "Say it twice.")]
    assert split_scripts("مرحباً وسلام عليك يا موهسين! كيف حالك؟") == [("ar", "مرحباً وسلام عليك يا موهسين! كيف حالك؟")]
    assert split_scripts("No Arabic here.") == [("en", "No Arabic here.")]


def test_arabic_goes_to_the_arabic_voice_and_english_to_the_main_one():
    log = []
    tts = BilingualTTS(FakeVoice(log, "english"), lambda: FakeVoice(log, "arabic"))
    tts.speak("Hello is مرحبا in Arabic.", wait=True)
    assert log == [("english", "Hello is"), ("arabic", "مرحبا"), ("english", "in Arabic.")]


def test_without_an_arabic_voice_the_script_is_left_out_not_spelled():
    log = []
    tts = BilingualTTS(FakeVoice(log, "english"), lambda: FakeVoice(log, "arabic", available=False))
    tts.speak("Hello is مرحبا in Arabic.", wait=True)
    assert log == [("english", "Hello is in Arabic.")]
    log.clear()
    tts.speak("مرحبا", wait=True)
    assert log == []                      # nothing English to say, and no letter-by-letter reading


def test_plain_english_is_unchanged():
    log = []
    BilingualTTS(FakeVoice(log, "english"), lambda: None).speak("Just English.", wait=True)
    assert log == [("english", "Just English.")]


# ------------------------------------------------------------------ reply language
@pytest.mark.parametrize("text,language", [
    ("talk to me in arabic", "ar"), ("work totally in arabic", "ar"), ("switch to arabic", "ar"),
    ("can you speak arabic with me", "ar"), ("arabic mode", "ar"),
    ("I want to talk in English, not another bit.", "en"), ("English please", "en"), ("speak english", "en"),
    ("go back to english", "en"), ("reply only in english", "en"),
])
def test_asking_for_a_language_switches_replies(text, language):
    intent = IntentRouter().route(text)
    assert intent.control == "REPLY_LANGUAGE" and intent.entities["language"] == language


@pytest.mark.parametrize("text", ["teach me arabic", "how do I say thank you in arabic", "do you speak arabic?",
                                  "translate hello to arabic", "learn arabic"])
def test_talking_about_arabic_does_not_switch_the_language(text):
    assert IntentRouter().route(text).control != "REPLY_LANGUAGE"


def test_the_switch_works_in_the_middle_of_a_lesson():
    session = SessionState()
    session.pending_learning = {"type": "language", "course": "arabic"}
    assert IntentRouter().route("English please", session).control == "REPLY_LANGUAGE"


def test_the_model_is_told_to_reply_in_english_unless_arabic_was_asked_for(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase

    seen = []

    class Model:
        name, model = "fake", "fake"

        def generate(self, messages, **kwargs):
            seen.append(messages[0].content)
            return SimpleNamespace(content="ok", done_reason="stop", finish_reason="stop", tokens=1)

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    service = ConversationService(Model(), memory)
    language = ["en"]
    service.language_supplier = lambda: language[0]
    try:
        service.reply("hi hello")
    except Exception:
        pass
    language[0] = "ar"
    try:
        service.reply("hi hello")
    except Exception:
        pass
    assert seen and "Reply in English" in seen[0]
    assert "entirely in clear Modern Standard Arabic" in seen[-1]


# ------------------------------------------------------------------ hearing Arabic
def test_the_arabic_model_hears_arabic_mode_and_speaking_exercises():
    from app.voice.manager import VoiceManager

    session = SessionState()
    tutor = SimpleNamespace(current=lambda course: {"skill": "speaking"})
    manager = SimpleNamespace(runtime=SimpleNamespace(session=session, learning=SimpleNamespace(language=tutor)),
                              config={"stt": {"detect_other_languages": False}})
    manager._tutor = lambda: tutor
    hearing = lambda: VoiceManager._arabic_hearing(manager)
    assert hearing() is None                               # English-only when detection is switched off
    manager.config = {"stt": {}}
    assert hearing() == "auto"                             # by default other languages are heard too
    manager.config = {"stt": {"detect_other_languages": False}}
    session.reply_language = "ar"
    assert hearing() == "auto"                             # Arabic mode still understands "English please"
    session.reply_language = "en"
    session.pending_learning = {"type": "language", "course": "arabic"}
    assert hearing() == "ar"                               # a speaking exercise listens for Arabic


# ------------------------------------------------------------------ copied paragraphs
def test_a_paragraph_copied_from_the_last_reply_is_dropped():
    copied = ("Mohsin, I hear you clearly now. You are saying that when I say \"Arabic\" or list letters like "
              "\"Alif,\" it feels to me as if the words themselves carry a foreign sound in your mind.")
    previous = "Understood. Plain English only. What would you like to talk about?\n\n" + copied
    answer = "Understood, Mohsin. English only from now on.\n\n" + copied
    assert drop_repeated_paragraphs(answer, previous) == "Understood, Mohsin. English only from now on."
    assert drop_repeated_paragraphs(copied, previous) == copied          # never leaves nothing
    assert drop_repeated_paragraphs("New answer.", previous) == "New answer."


# ------------------------------------------------------------------ the whole turn
def test_language_control_and_lessons_through_the_assistant():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    stored = []
    original = runtime.memory.store_memory
    runtime.memory.store_memory = lambda kind, *args, **kwargs: stored.append(kind) or original(kind, *args, **kwargs)
    try:
        reply = runtime.assistant.handle("talk to me in arabic")
        assert runtime.session.reply_language == "ar" and "English please" in reply.response.display_text
        runtime.assistant.handle("English please")
        assert runtime.session.reply_language == "en"
        runtime.assistant.handle("learn arabic")
        before = stored.count("learning")
        runtime.assistant.handle("a")
        runtime.assistant.handle("b")
        assert stored.count("learning") == before, "drill steps are not stored as memories"
        assert runtime.session.pending_learning and runtime.session.pending_learning["type"] == "language"
        assert IntentRouter().route("stop lesson", runtime.session).intent == IntentKind.LEARNING
    finally:
        runtime.assistant.handle("stop lesson")
        runtime.close()
