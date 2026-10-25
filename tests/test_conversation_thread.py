"""Holding a thread, which is what makes an exchange two-way.

Mohsin said it still felt one-way. Watching a real conversation showed why:
every short reply broke it. "yeah exactly" returned raw intent JSON, "hmm"
produced "It looks like you're just starting a new interaction!", and "so
should i?" answered a question about learning Rust with GPU statistics.

Being unable to say "hmm" is most of what makes talking to something feel
like dictation. A person who must write a full sentence every turn is not
having a conversation.
"""
from __future__ import annotations

import pytest

from app.agent.conversation import (
    _CONTINUATION_PHRASES,
    refers_to_previous_reply,
    _about_the_system,
    _state_summary,
)
from app.intent.router import _prose_only


def _thread(assistant_said: str, user_says: str):
    return [
        {"role": "user", "content": "im thinking about learning rust"},
        {"role": "assistant", "content": assistant_said},
        {"role": "user", "content": user_says},
    ]


# ------------------------------------------------ short replies keep the thread


@pytest.mark.parametrize(
    "reply",
    ["yeah", "yeah exactly", "exactly", "right", "i see", "makes sense", "agreed",
     "hmm", "hmmm", "oh", "ah", "interesting", "really?", "i guess",
     "so should i?", "should i?", "what would you do", "then what?",
     "not sure", "maybe", "tell me more", "like what", "and?"],
)
def test_a_short_reply_is_treated_as_continuing_the_thread(reply):
    """Each of these only means something relative to what was just said."""
    assert _CONTINUATION_PHRASES.match(reply), f"{reply!r} would start over"


@pytest.mark.parametrize(
    "text",
    ["open notepad", "what is the capital of France", "search the web for rust jobs",
     "write a note saying hello"],
)
def test_a_real_request_is_not_mistaken_for_a_continuation(text):
    assert not _CONTINUATION_PHRASES.match(text)


@pytest.mark.parametrize("text", [
    "How does it affect memory?", "What about its effect on memory?",
    "Does that mean I should change it?",
])
def test_referential_followup_uses_the_previous_answer(text):
    assert refers_to_previous_reply(text)


def test_explicit_new_subject_does_not_inherit_the_previous_answer():
    assert not refers_to_previous_reply("What is the capital of France?")
    assert not refers_to_previous_reply("What is this project?")


@pytest.mark.parametrize('text', ['do both', 'use the second option', 'pick the other one', 'finish it',
    'complete them all', 'make it better', 'same but under 500 words', 'the same without subscriptions',
    'please continue', 'go ahead', 'and improve it further', 'implement it', 'work on it', 'do it', 'build that'])
def test_proposal_followups_keep_the_existing_thread(text):
    assert refers_to_previous_reply(text)


@pytest.mark.parametrize('text', ['Explain the second law of thermodynamics', 'Both parents work full time',
    'What is the first amendment?', 'Continue reading D:\\notes.txt', 'Improve the GUI of my calendar'])
def test_standalone_requests_do_not_borrow_an_unrelated_proposal(text):
    assert not refers_to_previous_reply(text)


def test_proposal_followup_uses_this_session_reply_and_avoids_unrelated_recall(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    from tests.test_conversation import RecordingModel
    memory = MemoryDatabase(tmp_path / 'proposal.db')
    memory.initialize()
    memory.record_message('proposal', 'user', 'I need local notes with no subscription.')
    memory.record_message('proposal', 'assistant', '1. Plain files. 2. Local SQLite notes.')
    model = RecordingModel()
    service = ConversationService(model, memory, 'proposal')
    lookups = []
    service.context_retriever = lambda *args, **kwargs: lookups.append(args) or []
    service.reply('use the second option')
    assert not lookups
    anchors = [message.content for message in model.calls[-1] if message.content.startswith("The user's message is a short follow-up.")]
    assert len(anchors) == 1 and 'Local SQLite notes' in anchors[0]
    assert any('no subscription' in message.content for message in model.calls[-1])


# ------------------------------------------- the machine is not always the topic


def test_discussing_memory_safety_does_not_summon_the_gpu_report():
    """The bug: "memory" in a reply about Rust pulled the whole runtime state
    back into the prompt, so "so should i?" was answered with VRAM usage."""
    history = _thread(
        "Rust forces you to think carefully about memory and safety without garbage collection.",
        "so should i?",
    )
    assert not _about_the_system("so should i?", history)


@pytest.mark.parametrize(
    "text",
    ["hmm", "yeah exactly", "i also know python", "whats the hardest part",
     "im tired today", "what should i do now"],
)
def test_ordinary_talk_does_not_carry_the_runtime_state(text):
    assert not _about_the_system(text, [])


@pytest.mark.parametrize(
    "text",
    ["what model are you running on", "how much vram do you have",
     "what are your tools", "which backend is this", "is ollama running",
     "what can you do"],
)
def test_asking_about_the_machine_still_gets_the_machine(text):
    assert _about_the_system(text, [])


def test_a_follow_up_to_a_system_question_still_counts():
    """"and how much is left?" carries no keyword of its own."""
    history = [
        {"role": "user", "content": "how much vram do you have"},
        {"role": "assistant", "content": "8GB total, and about 3GB is free right now."},
        {"role": "user", "content": "and how much is left?"},
    ]
    assert _about_the_system("and how much is left?", history)


def test_the_summary_keeps_only_what_grounds_a_self_claim():
    summary = _state_summary({
        "provider": "ollama", "model": "qwen", "mode": "BALANCED", "activity": "idle",
        "tools": list(range(80)), "memory": {"rows": 5000}, "storage": {"used": 1},
    })
    assert set(summary) <= {"provider", "model", "mode", "activity", "active_project", "active_task"}
    assert "tools" not in summary


# --------------------------------------------- a classification is not a reply


def test_a_classifier_that_emits_json_is_not_spoken_aloud():
    """"yeah exactly" came back as {'intent': 'CONVERSATION', 'confidence':
    0.95, ...} rendered straight into the conversation."""
    leaked = "{'intent': 'CONVERSATION', 'confidence': 0.9546, 'goal': '', 'requires_tools': true}"
    assert _prose_only(leaked) is None


@pytest.mark.parametrize(
    "leak",
    ['{"intent": "ACTION", "confidence": 0.9}',
     "[{'goal': 'x'}]",
     '  {"entities": {}}  ',
     'something "requires_tools": true something'],
)
def test_structured_output_is_never_used_as_speech(leak):
    assert _prose_only(leak) is None


@pytest.mark.parametrize(
    "reply",
    ["Sure, that makes sense.", "I opened Notepad for you.",
     "Yes -- I'd go with Rust for that."],
)
def test_a_real_sentence_is_kept(reply):
    assert _prose_only(reply) == reply


def test_non_text_and_empty_are_dropped():
    assert _prose_only(None) is None
    assert _prose_only({"intent": "x"}) is None
    assert _prose_only("   ") is None


# ------------------------------------------- a greeting is not an answer


@pytest.mark.parametrize(
    "reply",
    [
        "Hello! How can I assist you today?",
        # The one that actually reached Mohsin. Since HAYAT was asked to use
        # his name, the model started greeting him by it -- and the detector
        # only allowed punctuation after "hello", so the retry never fired.
        "Hello Mohsin! How can I assist you today?",
        "Hi Mohsin, how may I help you?",
        "Hey Mohsin! What can I help you with today?",
        "Good morning Mohsin!",
    ],
)
def test_a_greeting_instead_of_an_answer_is_caught(reply):
    from app.agent.conversation import ConversationService

    assert ConversationService._is_empty_greeting(reply, "i finished the report finally")


@pytest.mark.parametrize(
    "reply",
    [
        "That's great news! I'd love to see what you wrote.",
        "Hello Mohsin - the report looks solid, nice work.",
        "Hi, I opened Notepad for you.",
    ],
)
def test_a_real_reply_that_opens_warmly_is_not_discarded(reply):
    """Catching greetings must not eat answers that begin with one."""
    from app.agent.conversation import ConversationService

    assert not ConversationService._is_empty_greeting(reply, "i finished the report finally")


def test_greeting_him_back_when_he_said_hello_is_correct():
    from app.agent.conversation import ConversationService

    assert not ConversationService._is_empty_greeting("Hello Mohsin!", "hi")


# --------------------------------- a greeting is not an answer, on any path


@pytest.mark.parametrize(
    "hollow",
    [
        "Hello! How can I assist you today?",
        "Hello Mohsin! How can I assist you today?",
        "Hi there, how may I help?",
        "How can I help you today?",
        "It looks like you're just starting a new interaction!",
        "Good morning Mohsin!",
    ],
)
def test_the_router_never_speaks_a_hollow_greeting(hollow):
    """The conversation service already caught these and retried, but the
    router's own `response` field bypasses that path and goes straight to
    Mohsin -- which is why the greeting kept appearing after the detector was
    fixed. It was fixed in the wrong place. Dropping it here hands the turn
    to the service that has the history and can actually reply."""
    assert _prose_only(hollow) is None


@pytest.mark.parametrize(
    "real",
    [
        "Nice - what is she like?",
        "Hello Mohsin - your sister will love the pasta.",
        "I opened Notepad.",
        "How can I make this faster for you? I'd start by trimming the prompt.",
    ],
)
def test_a_genuine_reply_is_not_mistaken_for_a_greeting(real):
    """Rejecting hollow openers must not eat answers that begin with one, or
    questions that happen to start with "how can I"."""
    assert _prose_only(real) == real
