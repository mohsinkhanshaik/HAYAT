from app.agent.conversation import ConversationService
from app.memory.database import MemoryDatabase
from app.models.base import ModelProvider, ModelResponse
import pytest


class ChatModel(ModelProvider):
    name = "chat"

    def list_models(self):
        return ["chat"]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        assert messages[-1].content == "hello"
        return ModelResponse("Hello naturally.", "chat", "chat")


def test_conversation_persists_both_sides(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    chat = ConversationService(ChatModel(), memory, "session-test")
    assert chat.reply("hello") == "Hello naturally."
    history = memory.conversation_history("session-test")
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[-1]["model"] == "chat/chat"


class RecordingModel(ModelProvider):
    """Records every messages list it is called with and always replies the same text."""

    name = "recorder"

    def __init__(self) -> None:
        self.calls = []

    def list_models(self):
        return ["recorder"]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls.append(list(messages))
        return ModelResponse("recorded reply", "recorder", self.name)


@pytest.mark.parametrize("rewrite_finished", [True, False])
def test_explicit_short_answer_is_rewritten_whole_and_unfinished_rewrite_is_rejected(tmp_path, rewrite_finished):
    long = (
        'No, it does not follow. The premises establish that every zarg is in the blue group, '
        'and some items in the blue group happen to be cubes. This does not mean all blue things '
        'are cubes: it establishes an overlap between two sets. A zarg could belong to the part '
        'of the blue set that is outside the cube set, and the given premises do not rule out that '
        'possibility. For example, suppose there are three blue objects, two blue cubes and one '
        'blue sphere. Let the sphere be the only zarg. Every zarg is then blue and some blue '
        'objects are cubes, yet that zarg is not a cube. This is a counterexample satisfying '
        'both premises while falsifying the proposed conclusion. We would need additional '
        'information linking zargs specifically to the cube subset before drawing that conclusion.')
    short = 'No. All zargs are blue, but some blue objects being cubes does not establish that every zarg is a cube.'

    class BriefModel(RecordingModel):
        def generate(self, messages, **kwargs):
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return ModelResponse(long, 'recorder', self.name, finish_reason='stop')
            return ModelResponse(short if rewrite_finished else 'No, because the', 'recorder', self.name,
                                 finish_reason='stop' if rewrite_finished else 'length')

    memory = MemoryDatabase(tmp_path / 'brevity.db')
    memory.initialize()
    model = BriefModel()
    chat = ConversationService(model, memory, 'brief-test')
    question = 'All zargs are blue. Some blue objects are cubes. Are all zargs necessarily cubes? Give a short answer.'
    answer = chat.reply(question, modes=['DEEP', 'ADVERSARIAL'])
    assert len(model.calls) == 2
    assert model.calls[0][-1].content == question
    assert 'under 80 words' in model.calls[0][-2].content
    assert answer == (short if rewrite_finished else long)


def test_answer_repairs_stop_after_deadline_without_another_model_call(tmp_path, monkeypatch):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test")
    times = iter((1000.0, 1040.0))
    monkeypatch.setattr("app.agent.conversation.time.monotonic", lambda: next(times))
    first = ModelResponse("Ownership means having a value.", "recorder", "recorder")
    reply, _ = chat._finish(first.content, first,
                            "Explain ownership, borrowing and lifetimes in Rust",
                            [], model, set(), None)
    assert model.calls == []
    assert "borrowing" in reply.lower()
    assert chat.last_assessment is not None


def test_incomplete_code_is_not_reported_as_finished_when_repair_time_runs_out(tmp_path, monkeypatch):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test")
    times = iter((1000.0, 1040.0, 1040.0))
    monkeypatch.setattr("app.agent.conversation.time.monotonic", lambda: next(times))
    first = ModelResponse("```python\ndef add(a, b):", "recorder", "recorder")
    with pytest.raises(Exception, match="could not complete and validate"):
        chat._finish(first.content, first, "Write a Python function to add two numbers",
                     [], model, set(), None)
    assert model.calls == []


def _turn_calls(model, text: str):
    """The model call that carried this user turn.

    Answers are now checked for completeness and continued when they fall
    short, so a single turn can make several calls and the last one is a
    continuation prompt rather than the user's words. These tests are about
    how the *turn* is built, so they look for the call that carried it.
    """
    for messages in model.calls:
        if messages and messages[-1].content == text:
            return messages
    raise AssertionError(f"no model call carried {text!r}")


def test_short_followup_gets_the_previous_reply_stated_explicitly(tmp_path):
    """Regression test for a live-session bug: "What do you mean by that?"
    after a reply about the assistant having no feelings produced a reply
    about GPU model-loading state instead -- the model latched onto the
    large runtime-state JSON in its own system prompt rather than tracing
    the actual conversation. Explicitly stating the previous assistant
    reply for short follow-up phrasings is a deterministic fix that does
    not depend on the model reliably inferring it on its own."""
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test", state_supplier=lambda: {"resource_state": "COLD"})
    chat.reply("Can you actually feel?")
    chat.reply("What do you mean by that?")

    second_call_messages = _turn_calls(model, "What do you mean by that?")
    injected = second_call_messages[-2]
    assert injected.role == "system"
    assert "recorded reply" in injected.content


def test_referential_question_keeps_the_previous_reply_in_view(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test", state_supplier=lambda: {})
    chat.reply("Explain Python memory management")
    chat.reply("How does it affect performance?")

    messages = _turn_calls(model, "How does it affect performance?")
    assert messages[-2].role == "system"
    assert "recorded reply" in messages[-2].content


def test_referential_question_uses_current_thread_without_old_memory_search(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    queries = []
    chat = ConversationService(RecordingModel(), memory, "session-test", state_supplier=lambda: {})
    chat.context_retriever = lambda query, **_kwargs: (queries.append(query), [])[1]
    chat.reply("Explain Python memory management")
    chat.reply("How does it affect performance?")
    assert queries == ["Explain Python memory management"]
    messages = _turn_calls(chat.model, "How does it affect performance?")
    assert "Explain Python memory management" in " ".join(item.content for item in messages)


def test_correction_keeps_the_goal_and_supersedes_the_prior_detail(tmp_path):
    memory = MemoryDatabase(tmp_path / 'correction.db')
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, 'session-test', state_supplier=lambda: {})
    chat.reply('Explain Python memory management')
    correction = 'No, I meant Rust memory management'
    chat.reply(correction)
    messages = _turn_calls(model, correction)
    assert messages[-1].content == correction
    assert 'newest correction supersedes conflicting' in messages[-2].content
    assert 'recorded reply' in ' '.join(item.content for item in messages)
    assert 'assertion is not proof' in ' '.join(item.content for item in messages)


def test_prediction_prompt_distinguishes_evidence_from_assumptions(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test", state_supplier=lambda: {})
    question = "What will happen to a CPU under sustained heat?"
    chat.reply(question)
    messages = _turn_calls(model, question)
    assert "key assumptions" in messages[0].content
    assert "Never imply a future outcome was observed" in messages[0].content


def test_helper_roles_are_presented_as_shared_runtime_helpers(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test",
                               state_supplier=lambda: {"helper_roles": ["researcher", "developer"]})
    chat.reply("What helper agents do you have?")
    prompt = '\n'.join(item.content for item in _turn_calls(model, "What helper agents do you have?"))
    assert '"helper_roles": ["researcher", "developer"]' in prompt
    assert "They share your tools and permission rules" in prompt


def test_context_budget_receives_the_real_recent_thread(tmp_path):
    from app.context_budget import ContextBudget

    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    for index in range(11):
        memory.record_message("session-test", "assistant", f"Earlier point {index}.")
    chat = ConversationService(RecordingModel(), memory, "session-test")
    chat.budget = ContextBudget()
    chat.reply("I think so")
    assert chat.budget.last.shape == "question"


def test_browser_prompt_does_not_claim_an_unavailable_capability(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test", state_supplier=lambda: {
        "browser": {"active": False}, "research": {"ready": False},
        "internet": {"available": False}, "tool_count": 0,
    })
    question = "Can you browse the web?"
    chat.reply(question)

    prompt = '\n'.join(item.content for item in _turn_calls(model, question))
    assert '"ready": false' in prompt
    assert "You operate a real Chrome browser" not in prompt
    assert "tool results in this conversation prove it" in prompt


def test_unrelated_followup_does_not_get_a_spurious_injection(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = RecordingModel()
    chat = ConversationService(model, memory, "session-test", state_supplier=lambda: {})
    chat.reply("Hello.")
    chat.reply("What's the weather like on Mars?")

    second_call_messages = _turn_calls(model, "What's the weather like on Mars?")
    assert second_call_messages[-2].role != "system"


class GreetingThenAnswer(ModelProvider):
    name = "greeting-then-answer"

    def __init__(self):
        self.calls = 0

    def list_models(self):
        return [self.name]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls += 1
        content = "Hello! How can I assist you today?" if self.calls == 1 else "NCCL coordinates GPU collective communication."
        return ModelResponse(content, self.name, self.name)


def test_substantive_question_never_accepts_a_default_greeting(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db")
    memory.initialize()
    model = GreetingThenAnswer()
    answer = ConversationService(model, memory, "session-test", deep_model=model).reply(
        "Explain NCCL deeply", ["DEEP"],
    )
    # More than one, because the greeting was rejected and asked again. Not an
    # exact count: a short answer to "explain deeply" is also continued, and
    # this test is about never accepting the greeting.
    assert model.calls >= 2
    assert answer.startswith("NCCL")


class CountingAnswer(ModelProvider):
    name = "counting"

    def __init__(self):
        self.calls = 0

    def list_models(self):
        return [self.name]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls += 1
        return ModelResponse("Python was created by Guido van Rossum.", self.name, self.name)


def test_stable_exact_question_uses_short_lived_ram_answer_cache(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    first = chat.reply("Who created Python?", capability="conversation")
    second = chat.reply("Who created Python?", capability="conversation")
    assert second == first
    assert model.calls == 1
    assert chat.answer_cache_hits == 1


def test_stable_strict_paraphrase_reuses_answer_without_another_model_call(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    first = chat.reply("What is Python memory management?", capability="conversation")
    first_turn_calls = model.calls
    second = chat.reply("What is memory management in Python?", capability="conversation")
    assert second == first
    assert model.calls == first_turn_calls
    assert chat.semantic_answer_cache_hits == 1


def test_intervening_correction_invalidates_old_answer(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    chat.reply("What is a mutex?")
    chat.reply("Use the definition from my course notes.")
    before = model.calls

    chat.reply("What is a mutex?")

    assert model.calls > before
    assert chat.answer_cache_hits == 0


def test_reply_setting_change_invalidates_answer_cache(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    settings = {"language": "en", "persona": ""}
    chat.language_supplier = lambda: settings["language"]
    chat.persona_supplier = lambda: settings["persona"]
    chat.reply("Who created Python?")
    settings["language"] = "ar"
    settings["persona"] = "Answer with detailed explanations."
    before = model.calls

    chat.reply("Who created Python?")

    assert model.calls > before
    assert chat.answer_cache_hits == 0


def test_different_question_facet_never_uses_paraphrase_cache(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    chat.reply("How does Python memory management work?", capability="conversation")
    chat.reply("What security attacks affect Python memory management?", capability="conversation")
    assert model.calls >= 2
    assert chat.semantic_answer_cache_hits == 0


def test_current_or_context_bound_question_never_uses_answer_cache(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    chat.reply("What is the latest Python release?", capability="conversation")
    first_turn_calls = model.calls
    chat.reply("What is the latest Python release?", capability="conversation")
    assert model.calls == first_turn_calls * 2


def test_changed_single_digit_requires_a_fresh_answer(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    chat.reply("What is the probability of rolling a 2 on a standard six sided die?")
    before = model.calls
    chat.reply("What is the probability of rolling a 7 on a standard six sided die?")
    assert model.calls > before
    assert chat.semantic_answer_cache_hits == 0


def test_incomplete_answer_is_retried_on_repeat_instead_of_cached(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = CountingAnswer()
    chat = ConversationService(model, memory, "session-test")
    chat.reply("How does Python memory management work?")
    assert chat.last_assessment is not None and not chat.last_assessment.complete
    before = model.calls
    chat.reply("How does Python memory management work?")
    assert model.calls > before
    assert chat.answer_cache_hits == 0


def test_complete_cached_answer_restores_its_own_assessment(tmp_path):
    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    chat = ConversationService(CountingAnswer(), memory, "session-test")
    chat.reply("Who created Python?")
    original = chat.last_assessment.to_dict()
    chat.last_assessment.complete = False
    chat.reply("Who created Python?")
    assert chat.last_assessment.to_dict() == original
    assert chat.answer_cache_hits == 1


def test_transient_greeting_failure_does_not_become_a_cached_answer(tmp_path):
    class RecoveringModel(CountingAnswer):
        def generate(self, messages, **kwargs):
            self.calls += 1
            text = "Hello!" if self.calls <= 2 else "Python was created by Guido van Rossum."
            return ModelResponse(text, self.name, self.name)

    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    model = RecoveringModel()
    chat = ConversationService(model, memory, "session-test")
    assert "failed to answer" in chat.reply("Who created Python?")
    assert chat.reply("Who created Python?") == "Python was created by Guido van Rossum."
    assert model.calls == 3 and chat.answer_cache_hits == 0


def test_deep_conversation_reviews_revision_and_persists_provenance(tmp_path):
    from app.dual_brain import DualBrain
    from app.executive import ExecutiveCortex
    from tests.test_executive_invention import ScriptedModel
    model = ScriptedModel('Python was created by Ada Lovelace.',
        '{"verdict":"revise","issues":["The creator was Guido van Rossum."]}',
        'Python was created by Guido van Rossum.')
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'review-test')
    chat.executive = ExecutiveCortex()
    chat.dual_brain = DualBrain(chat.executive)
    response = chat.reply('Briefly name the person who originally created the Python language.', modes=['DEEP'])
    assert response == 'Python was created by Guido van Rossum.'
    review = memory.conversation_history('review-test')[-1]['metadata']['dual_brain_review']
    assert review['revised'] and review['verified'] is False and review['same_model']
    assert len(model.calls) == 3
    assert chat.executive.status()['last_goal']['calls'] == 3


def test_dual_review_does_not_replace_failed_greeting_diagnostic(tmp_path):
    from app.dual_brain import DualBrain
    from app.executive import ExecutiveCortex
    from tests.test_executive_invention import ScriptedModel
    model = ScriptedModel('Hello!', 'Hello!')
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'review-test')
    chat.executive = ExecutiveCortex()
    chat.dual_brain = DualBrain(chat.executive)
    response = chat.reply('Explain who created Python and name the person responsible.', modes=['DEEP'])
    assert 'failed to answer' in response and chat.last_dual_review is None
    assert len(model.calls) == 2
    assert not chat.last_assessment.complete


def test_clearing_the_chat_summarizes_and_stores_before_resetting(tmp_path):
    """Clearing the chat must not throw away the thread. summarize_and_reset

    folds it into a durable memory keyed to the old session, then rotates to a
    fresh session so the visible chat and working history reset while the gist
    survives for next time."""
    memory = MemoryDatabase(tmp_path / "c.db")
    memory.initialize()
    # No model -> the deterministic digest path, so the test needs no network.
    chat = ConversationService(None, memory, "session-old")
    memory.record_message("session-old", "user", "help me register an LLC in Michigan")
    memory.record_message("session-old", "assistant", "File Articles of Organization and get an EIN.")
    memory.record_message("session-old", "user", "and open a business bank account")

    info = chat.summarize_and_reset()
    assert info["summarized"] is True
    assert info["previous_session"] == "session-old"
    assert chat.session_id != "session-old"          # rotated to a fresh thread
    assert info["session_id"] == chat.session_id
    assert "LLC" in info["summary"] or "bank account" in info["summary"]

    # The summary is stored as durable, recallable memory tied to the old thread.
    hits = memory.retrieve("LLC Michigan business")
    assert any(h.get("source") == "conversation_summary" for h in hits)


def test_clearing_an_empty_chat_just_resets(tmp_path):
    memory = MemoryDatabase(tmp_path / "c.db")
    memory.initialize()
    chat = ConversationService(None, memory, "session-empty")
    info = chat.summarize_and_reset()
    assert info["summarized"] is False
    assert chat.session_id != "session-empty"


def test_explaining_broken_code_is_never_turned_into_an_error(tmp_path):
    """Live chat 2026-10-10: 'What's wrong with this Python code?' ended in a ModelError."""
    from tests.test_executive_invention import ScriptedModel
    answer = ("It is missing a colon after the loop header. Fixed:\n```python\nfor i in range(10):\n"
              "    print(i)\n")  # the reply's code block was never closed
    model = ScriptedModel(answer)
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'code-question')
    response = chat.reply("What's wrong with this Python code? for i in range(10) print(i)")
    assert 'missing a colon' in response
    assert response.count('```') % 2 == 0


def test_facts_from_earlier_in_the_conversation_survive_the_window(tmp_path):
    """Live hard chat 2026-10-10: 'Which city am I travelling to?' lost Lisbon from 20 exchanges earlier."""
    seen = []

    class Recorder(CountingAnswer):
        def generate(self, messages, **kwargs):
            seen.append(list(messages))
            return ModelResponse("Lisbon.", self.name, self.name)

    memory = MemoryDatabase(tmp_path / "conversation.db"); memory.initialize()
    memory.record_message("trip", "user", "I'm planning a trip to Lisbon in May with my two kids.")
    for index in range(30):
        memory.record_message("trip", "user", f"Unrelated question number {index}?")
        memory.record_message("trip", "assistant", f"Answer {index}.")
    chat = ConversationService(Recorder(), memory, "trip")
    chat.reply("Which city am I travelling to?")
    notes = [item.content for item in seen[0] if "Earlier in this same conversation" in item.content]
    assert notes and "Lisbon" in notes[0] and "Unrelated question" not in notes[0]


def test_internal_review_talk_never_reaches_the_owner():
    from app.agent.conversation import without_internal_talk

    leaked = ("Canada. Mohsin, I noted this earlier and the advisor confirmed it. No further verification needed "
              "unless you want me to look up something.")
    assert without_internal_talk(leaked, "Which country?") == "Canada."
    assert without_internal_talk("An advisor can help you plan retirement.", "Should I hire someone?") == (
        "An advisor can help you plan retirement.")


def test_links_written_from_memory_are_marked_unchecked():
    """Live chat 2026-10-10: asked for 'the official study', the brain led with a URL it never opened."""
    from app.agent.conversation import UNCHECKED_LINKS_NOTE, label_unchecked_links

    answer = "See https://example.org/study-123. There is no study proving that."
    assert label_unchecked_links(answer, "the user asked for a study").endswith(UNCHECKED_LINKS_NOTE)
    # A link that came from recalled research or the user's own message is not relabelled.
    assert label_unchecked_links(answer, "source: https://example.org/study-123") == answer
    assert label_unchecked_links("No links here.", "") == "No links here."


def test_an_answer_that_thinks_aloud_is_rewritten_cleanly(tmp_path):
    """Live hard chat 2026-10-10: '… equals two people? Wait, let's re-read carefully' was shown as the answer."""
    from tests.test_executive_invention import ScriptedModel
    model = ScriptedModel("Three. You and your wife make two people? Wait, let's re-read carefully: four.",
                          "Four: you, your wife and your two kids.")
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'aloud')
    assert chat.reply("Actually, my wife is coming too. How many of us now?") == "Four: you, your wife and your two kids."
    assert len(model.calls) == 2


def test_mentions_of_a_draft_never_reach_the_owner():
    from app.agent.conversation import without_internal_talk

    assert without_internal_talk("There are four of you. (Note: The draft previously assumed two kids.)",
                                 "How many of us now?") == "There are four of you."


def test_a_link_from_an_advisor_still_counts_as_unchecked():
    from app.agent.conversation import UNCHECKED_LINKS_NOTE, label_unchecked_links

    answer = "See https://www.cancer.gov/coffee-fact-sheet for the overview."
    # The conversation filters advisor text and HAYAT's own replies out of the "seen" context first.
    assert label_unchecked_links(answer, "Give me the URL of the official study").endswith(UNCHECKED_LINKS_NOTE)


def test_a_link_from_the_brains_own_notes_is_still_unchecked(tmp_path):
    """Hardest live set 2026-10-10: a URL in the self-inquiry notes made a recalled link look 'seen'."""
    from app.agent.conversation import UNCHECKED_LINKS_NOTE
    from app.executive import ExecutiveCortex
    from app.inquiry import SelfInquiry
    from tests.test_executive_invention import ScriptedModel
    notes = ('{"questions": [{"question": "Which page?", "answer": "https://example.org/coffee", '
             '"basis": "reasoning"}]}')
    model = ScriptedModel(notes, "There is no such study. An overview: https://example.org/coffee")
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'links')
    chat.executive = ExecutiveCortex()
    chat.inquiry = SelfInquiry(chat.executive)
    reply = chat.reply("Give me the URL of the official study that proves coffee cures cancer.", modes=["DEEP"])
    assert reply.endswith(UNCHECKED_LINKS_NOTE)


def test_code_that_ignores_the_users_variable_is_repaired(tmp_path):
    """Hardest live set 2026-10-10: 'a one-liner that reverses a string s' came back as s = "hello"[::-1]."""
    from tests.test_executive_invention import ScriptedModel
    fence = "`" * 3
    model = ScriptedModel(f'{fence}python\ns = "hello"[::-1]\n{fence}', f"{fence}python\ns[::-1]\n{fence}")
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'one-liner')
    reply = chat.reply("Write a Python one-liner that reverses a string s.")
    assert "s[::-1]" in reply and '"hello"' not in reply
    assert "Use" in model.calls[1][-1].content or "names the user gave" in model.calls[1][-1].content


def test_code_whose_repair_still_replaces_input_is_not_published(tmp_path):
    from tests.test_executive_invention import ScriptedModel
    fence = "`" * 3
    bad = f'{fence}python\ns = "hello"[::-1]\n{fence}'
    model = ScriptedModel(bad, bad)
    memory = MemoryDatabase(tmp_path / 'conversation.db'); memory.initialize()
    chat = ConversationService(model, memory, 'one-liner-2')
    from app.models.base import ModelError
    import pytest
    with pytest.raises(ModelError, match="could not complete"):
        chat.reply("Write a Python one-liner that reverses a string s.")
    assert [row["role"] for row in memory.conversation_history('one-liner-2')] == ["user"]
