"""Emoji as instructions, and hands-free conversation.

Emoji are two problems wearing one hat: on the way in they carry meaning that
a verb-first parser throws away, and on the way out they are fine to read and
impossible to speak -- or to print, on a console that reports cp1252.
"""
from __future__ import annotations

import io

import pytest

from app.intent.emoji import contains_emoji, describe, for_speech, translate
from app.intent.models import IntentKind
from app.intent.normalize import normalized_text
from app.intent.router import IntentRouter
from app.session import SessionState

CAMERA, SCREENSHOT, SEARCH = "\U0001F4F7", "\U0001F4F8", "\U0001F50D"
THUMBS_UP, STOP_SIGN, PARTY = "\U0001F44D", "\U0001F6D1", "\U0001F389"


def _route(text: str):
    return IntentRouter().route(text, SessionState())


# ------------------------------------------------------- emoji as instruction


def test_a_camera_emoji_asks_to_look_through_the_camera():
    assert _route(f"{CAMERA} whats this").is_action


def test_an_emoji_prefixed_request_is_an_action_not_a_chat():
    """Leading emoji push the verb out of first position, the same way a
    leading "HAYAT." did."""
    assert _route(f"{SEARCH} search for rtx 5060 reviews").is_action


def test_a_bare_approval_emoji_is_an_answer_not_a_task():
    assert not _route(THUMBS_UP).is_action


def test_a_stop_emoji_is_understood_as_stopping():
    assert _route(f"bro {STOP_SIGN}").intent in {IntentKind.CONTROL, IntentKind.CONVERSATION}


@pytest.mark.parametrize(
    "symbol,word",
    [(THUMBS_UP, "yes"), ("\U0001F44E", "no"), ("\U0001F64F", "please"),
     (SEARCH, "search"), (SCREENSHOT, "screenshot"), (CAMERA, "camera")],
)
def test_instruction_emoji_become_the_words_they_stand_for(symbol, word):
    assert word in translate(f"{symbol} now").lower()


def test_decorative_emoji_are_dropped_not_described():
    """"great work party popper" is worse than "great work"."""
    result = translate(f"great work {PARTY}")
    assert "great work" in result
    assert "popper" not in result.lower()


def test_text_without_emoji_is_untouched():
    assert normalized_text("open notepad") == "open notepad"
    assert not contains_emoji("open notepad")


# ---------------------------------------------------------------- for speech


def test_spoken_text_carries_no_emoji():
    """A synthesiser either says nothing for an emoji or reads its name."""
    spoken = for_speech(f"Done ✅ - opened Notepad {PARTY} for you!")
    assert not contains_emoji(spoken)
    assert "opened Notepad" in spoken


def test_stripping_does_not_leave_broken_spacing():
    assert "  " not in for_speech(f"Done {PARTY} , really {THUMBS_UP} .")


def test_emoji_can_still_be_named_for_the_record():
    names = describe(THUMBS_UP)
    assert names and "thumbs up" in names[0]


# ------------------------------------------------- a console that cannot cope


class _NarrowStream(io.StringIO):
    """A console like the real one here: cp1252, no emoji."""

    encoding = "cp1252"

    def write(self, text: str) -> int:
        text.encode(self.encoding)  # raises exactly as the real console does
        return super().write(text)


def test_an_emoji_reply_does_not_take_down_the_output_channel():
    """The console here reports cp1252. Writing an emoji raises from inside
    the write, and output is what reports every other failure -- so a reply
    containing one would break the channel used to explain the break. HAYAT
    is meant to use emoji, so this is a certainty, not an edge case."""
    from app.output import OutputCoordinator

    stream = _NarrowStream()
    coordinator = OutputCoordinator(stream=stream)
    coordinator.emit(f"Opened Notepad {PARTY} and searched the web {SEARCH}")

    written = stream.getvalue()
    assert "Opened Notepad" in written
    assert "searched the web" in written
    assert not contains_emoji(written)


class _WideStream(io.StringIO):
    """A console that can render anything, as a UTF-8 terminal does."""

    encoding = "utf-8"


def test_a_capable_console_keeps_the_emoji():
    """Stripping is a concession to a narrow console, not a policy."""
    from app.output import OutputCoordinator

    stream = _WideStream()
    OutputCoordinator(stream=stream).emit(f"All done {PARTY}")
    assert PARTY in stream.getvalue()


# -------------------------------------------------------- continuous voice


def test_continuous_is_a_selectable_mode():
    from app.voice.manager import VoiceManager

    resolved = {
        "push": "push_to_talk", "wake": "wake_word",
        "continuous": "continuous", "hands_free": "continuous", "talk": "continuous",
    }
    for given, expected in resolved.items():
        manager = VoiceManager.__new__(VoiceManager)
        VoiceManager.set_mode(manager, given)
        assert manager.mode == expected


def test_an_unknown_mode_is_refused():
    from app.voice.manager import VoiceManager

    manager = VoiceManager.__new__(VoiceManager)
    with pytest.raises(ValueError):
        VoiceManager.set_mode(manager, "telepathy")


@pytest.mark.parametrize(
    "spoken",
    ["stop", "Stop.", "stop listening", "that's enough", "hayat stop",
     "bro stop", "never mind", "shut up"],
)
def test_spoken_stop_ends_the_conversation(spoken):
    """Ending an always-listening mode must not require the keyboard, and
    "stop" must not become a task called stop."""
    from app.voice.manager import VoiceManager

    manager = VoiceManager.__new__(VoiceManager)
    assert VoiceManager._is_stop_phrase(manager, spoken)


@pytest.mark.parametrize(
    "spoken",
    ["stop the download", "open notepad", "stop watch comparison", "what happened"],
)
def test_a_real_request_is_not_mistaken_for_stopping(spoken):
    from app.voice.manager import VoiceManager

    manager = VoiceManager.__new__(VoiceManager)
    assert not VoiceManager._is_stop_phrase(manager, spoken)
