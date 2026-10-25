from app.memory.database import MemoryDatabase
from app.voice.session import ApprovalPhraseParser, VoiceSession


def test_voice_session_reuses_existing_conversation_store(tmp_path):
    memory = MemoryDatabase(tmp_path / "voice.db")
    memory.initialize()
    session = VoiceSession.create(memory, "voice-test")
    session.record("user", "Open GitHub")
    session.record("assistant", "GitHub is open")
    contextual = session.contextual_goal("Open its latest repository")
    assert "Prior voice context" in contextual
    assert "GitHub is open" in contextual
    assert len(memory.conversation_history("voice-test")) == 2


def test_voice_approval_requires_explicit_phrase():
    """A bare "yes" now approves, which is a deliberate policy change.

    The previous rule rejected it, which is what made a real voice session
    unusable: the user tried "Approved.", "Approved all of them.", and
    "Prove, Continue" and was told each time that the phrase "was not explicit
    enough". parse() is only ever reached while an approval is genuinely
    pending and actively being awaited, so an affirmative in that window is a
    response to that prompt.

    What must NOT change: an utterance that merely contains an affirmative
    without being one still returns None so the caller re-asks.
    """
    assert ApprovalPhraseParser.parse("yes") is True
    assert ApprovalPhraseParser.parse("yes, continue") is True
    assert ApprovalPhraseParser.parse("approve") is True
    assert ApprovalPhraseParser.parse("no") is False

    assert ApprovalPhraseParser.parse("maybe") is None
    assert ApprovalPhraseParser.parse("yes, but only the first one") is None
    assert ApprovalPhraseParser.parse("did you say yes?") is None
