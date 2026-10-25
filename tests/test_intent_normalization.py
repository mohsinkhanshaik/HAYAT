"""Understanding how Mohsin actually talks.

Every intent pattern expects a tidy sentence starting with a verb, and real
input is not that. The bug that prompted this: the recogniser transcribes
speech as "HAYAT. Open Notepad and search the web", punctuating the name as
its own sentence, so the verb is no longer first, every action pattern misses,
and HAYAT chats about the task instead of doing it.
"""
from __future__ import annotations

import pytest

from app.intent.models import IntentKind
from app.intent.normalize import normalize, normalized_text
from app.intent.router import IntentRouter
from app.session import SessionState


def _route(text: str):
    return IntentRouter().route(text, SessionState())


# ------------------------------------------------------------ the voice bug


def test_a_spoken_command_addressed_by_name_is_an_action_not_a_chat():
    """The exact transcription faster-whisper produced, which used to route to
    CONVERSATION at 0.55 confidence."""
    intent = _route("HAYAT.  Open Notepad and search the web for Python news.")
    assert intent.is_action
    assert intent.confidence > 0.9


@pytest.mark.parametrize(
    "spoken",
    ["HAYAT. Open notepad.", "Hayat, open notepad", "hey hayat open notepad",
     "Hyatt. Open notepad", "yo hayat open notepad", "open notepad hayat"],
)
def test_the_name_is_stripped_however_it_was_heard(spoken):
    """HAYAT is not an English word and comes back several ways."""
    assert _route(spoken).intent is IntentKind.COMPUTER_CONTROL


def test_being_addressed_is_recorded_rather_than_silently_dropped():
    result = normalize("hayat open notepad")
    assert result.was_addressed
    assert result.address_removed.lower().startswith("hayat")
    assert result.text == "Open notepad" or result.text == "open notepad"


# --------------------------------------------------------- how Mohsin writes


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("bro can u open chrome n check my mail", IntentKind.BROWSER),
        ("hey hayat pls opne notepad", IntentKind.COMPUTER_CONTROL),
        ("yo bro whats on my screen", IntentKind.COMPUTER_CONTROL),
        ("hayat search the web for rtx 5060 benchmarks bro", IntentKind.RESEARCH),
    ],
)
def test_real_phrasing_reaches_the_right_intent(typed, expected):
    assert _route(typed).intent is expected


@pytest.mark.parametrize(
    "typed",
    ["just quickly take a screenshoot", "pls take a screenshot",
     "bro take a photo with the camera"],
)
def test_taking_something_is_an_action_not_a_conversation(typed):
    """"take a screenshot" is among the most common spoken requests and used
    to fall through to conversation, which answered with talk about
    screenshots. The kind it lands on matters less than that it acts."""
    intent = _route(typed)
    assert intent.is_action and intent.requires_tools


@pytest.mark.parametrize(
    "shorthand,word",
    [("u", "you"), ("ur", "your"), ("n", "and"), ("pls", "please"),
     ("dont", "don't"), ("im", "I'm"), ("wat", "what"), ("cud", "could")],
)
def test_shorthand_is_expanded(shorthand, word):
    assert word in normalized_text(f"tell me {shorthand} think")


@pytest.mark.parametrize("typo,fixed", [("opne", "open"), ("serach", "search"),
                                        ("acess", "access"), ("camra", "camera"),
                                        ("downlaod", "download"), ("teh", "the")])
def test_habitual_typos_are_repaired(typo, fixed):
    assert fixed in normalized_text(f"please {typo} it")


def test_substitutions_are_whole_word_only():
    """The failure mode of naive replacement: "ubuntu" becoming "youbuntu"."""
    for word in ("ubuntu", "android", "understand", "runner", "nginx"):
        assert word in normalized_text(f"install {word}")


def test_stretched_words_are_calmed_without_being_invented():
    assert normalized_text("soooooo slow") == "Soo slow" or "soo" in normalized_text("soooooo slow").lower()


# ----------------------------------------------------- it must not paraphrase


def test_the_request_itself_is_never_rewritten():
    """A normaliser that paraphrases can change what was asked."""
    original = "delete the report from the archive folder"
    assert normalized_text(f"bro {original}") == original[0].upper() + original[1:] or \
        normalized_text(f"bro {original}") == original


def test_dictated_content_is_never_edited():
    """A bug that shipped, not a hypothetical.

    "Write a note in Notepad saying: Hello from my AI." lost its final word,
    because "AI" is a way of addressing the assistant and the sentence
    happened to end with it. The note would have been written wrong.
    """
    text = "Write a note in Notepad saying: Hello from my AI."
    assert normalized_text(text).endswith("Hello from my AI.")


@pytest.mark.parametrize(
    "request_text,content",
    [
        ("type this: u r the best bro", "u r the best bro"),
        ("write a file named ur-notes.txt", "ur-notes.txt"),
        ("send a message saying pls dont", "pls dont"),
        ("create a note titled hayat", "hayat"),
    ],
)
def test_shorthand_inside_dictated_content_is_left_alone(request_text, content):
    """Expanding "u" to "you" inside a note changes what gets written."""
    assert content in normalized_text(request_text)


def test_the_instruction_before_dictation_is_still_cleaned():
    """Only the payload is protected; the request around it is not."""
    result = normalize("bro pls type this: hello")
    assert result.was_addressed
    assert result.text.lower().endswith("type this: hello")


def test_an_address_only_message_is_not_emptied():
    """"hayat" alone is being called, not an empty command."""
    result = normalize("hayat")
    assert result.text.lower() == "hayat"


def test_filler_only_message_survives():
    assert normalize("please").text.lower() == "please"


def test_what_was_removed_is_reported():
    result = normalize("hey hayat please open notepad")
    assert result.was_addressed
    assert "please" in result.filler_removed.lower()
    assert result.changed


def test_an_untouched_message_reports_no_change():
    result = normalize("open notepad")
    assert not result.changed
    assert not result.was_addressed


# ------------------------------------------------- ordinary input still works


@pytest.mark.parametrize(
    "text,expected",
    [
        ("hi", IntentKind.CONVERSATION),
        ("what is the capital of France?", IntentKind.QUESTION),
        ("open notepad", IntentKind.COMPUTER_CONTROL),
    ],
)
def test_normalisation_does_not_disturb_what_already_worked(text, expected):
    assert _route(text).intent is expected


def test_a_question_containing_an_address_word_is_still_a_question():
    """"man" and "bro" appear inside real sentences; only a leading or
    trailing address is an address."""
    assert "man" in normalized_text("what does the man in the video say")


def test_empty_input_is_still_rejected_cleanly():
    assert _route("   ").intent is IntentKind.UNKNOWN


def test_numbers_are_never_spell_corrected():
    """'1,000 seconds' became '1,00 seconds' when digits were treated as stretched letters."""
    from app.intent.normalize import normalize

    assert normalize("set a timer for 1,000 seconds").text == "set a timer for 1,000 seconds"
    assert normalize("transfer 5,000 to him").text == "transfer 5,000 to him"
    assert normalize("open file report,000.txt").text == "open file report,000.txt"
    assert normalize("sooooo cool").text == "soo cool"
