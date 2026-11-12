"""The owner can mark a reply right or wrong; HAYAT keeps the verdict and applies it to similar questions."""
import pytest

from app.intent.router import IntentRouter
from app.memory.database import MemoryDatabase
from app.owner_feedback import OwnerFeedback, record_from_message, verdict


@pytest.mark.parametrize("text, rating, correction", [
    ("that's wrong, the capital of Australia is Canberra", -1, "the capital of Australia is Canberra"),
    ("No, that is not correct. The right answer is 12", -1, "12"),
    ("you're wrong", -1, ""),
    ("wrong answer", -1, ""),
    ("That's right, thanks", 1, ""),
    ("good answer", 1, ""),
    ("perfect answer!", 1, ""),
    ("👍", 1, ""),
    ("thumbs up", 1, ""),
    ("thumbs down, it was Canberra", -1, "it was Canberra"),
])
def test_verdicts_on_the_previous_reply_are_recognised(text, rating, correction):
    assert verdict(text) == (rating, correction)


@pytest.mark.parametrize("text", [
    "is that wrong?", "it is wrong to steal", "that's right where I left it, now open the file",
    "the weather is good", "explain why stealing is wrong", "you're right about one thing but what about tax?",
])
def test_other_messages_are_not_taken_as_verdicts(text):
    assert verdict(text) is None


def _memory(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    return memory


def test_a_correction_is_stored_with_what_it_judged_and_applied_to_a_similar_question(tmp_path):
    memory = _memory(tmp_path)
    memory.record_message("s", "user", "What is the capital city of Australia?")
    memory.record_message("s", "assistant", "The capital of Australia is Sydney.")
    memory.record_message("s", "user", "that's wrong, it's Canberra")
    assert record_from_message(memory, "s", "that's wrong, it's Canberra") is not None
    store = OwnerFeedback.beside(memory)
    row = store.rows()[0]
    assert (row["rating"], row["kind"], row["correction"]) == (-1, "wrong", "it's Canberra")
    assert row["request"] == "What is the capital city of Australia?" and "Sydney" in row["reply"]
    note = store.prompt_note("which city is the capital of Australia")
    assert "Canberra" in note and "Sydney" in note and "not verified" in note
    assert store.prompt_note("what is the population of Brazil") == ""


def test_a_misunderstanding_is_counted_and_the_report_lists_right_and_wrong(tmp_path):
    from app.chat_feedback import remember_correction

    memory = _memory(tmp_path)
    memory.record_message("s", "user", "Explain Python memory management")
    memory.record_message("s", "assistant", "Python uses reference counting.")
    remember_correction(memory, "s", "u got me wrong. I meant Rust memory management")
    memory.record_message("s", "user", "Explain Rust ownership")
    memory.record_message("s", "assistant", "Each value has one owner.")
    remember_correction(memory, "s", "good answer")
    rows = OwnerFeedback.beside(memory).rows()
    assert [row["kind"] for row in rows] == ["right", "misunderstood"]
    report = OwnerFeedback.beside(memory).report()
    assert report.startswith("From your feedback: 1 reply marked right, 1 marked wrong or misunderstood")
    assert "Explain Python memory management" in report


def test_no_verdict_without_a_reply_to_judge_and_an_empty_report_explains_how(tmp_path):
    memory = _memory(tmp_path)
    assert record_from_message(memory, "s", "that's wrong") is None
    assert "Tell me \"that's wrong" in OwnerFeedback.beside(memory).report()


@pytest.mark.parametrize("text", ["what did you get wrong?", "show me my feedback", "what have I corrected you on?",
                                  "what did you get right and what wrong"])
def test_feedback_questions_are_answered_from_the_record(text):
    assert IntentRouter().route(text).control == "SELF_FEEDBACK"


def test_the_conversation_prompt_carries_the_owner_correction(tmp_path):
    from app.agent.conversation import ConversationService

    memory = _memory(tmp_path)
    OwnerFeedback.beside(memory).record(-1, "wrong", "it is Canberra", "What is the capital of Australia?",
                                        "Sydney.", "s")
    engine = ConversationService.__new__(ConversationService)
    engine.memory = memory
    assert "it is Canberra" in engine._owner_feedback_note("capital of Australia, which city?")


@pytest.mark.parametrize("reply, correction, evidence, status", [
    ("The capital of Australia is Sydney.", "it's Canberra",
     "Canberra is the capital city of Australia; Sydney is its largest city.", "agrees"),
    ("The capital of Australia is Canberra.", "no, it is Sydney",
     "Canberra is the capital city of Australia.", "disagrees"),
    ("The capital of Australia is Canberra.", "it is Melbourne", "Australia has six states.", "unclear"),
])
def test_a_correction_is_compared_with_what_the_evidence_says(reply, correction, evidence, status):
    from app.owner_feedback import compare_with_evidence

    assert compare_with_evidence("What is the capital of Australia?", reply, correction, evidence) == status


def test_pending_corrections_are_researched_once_and_the_result_is_shown(tmp_path):
    from types import SimpleNamespace

    store = OwnerFeedback(tmp_path / "owner-feedback.db")
    row = store.record(-1, "wrong", "it's Canberra", "What is the capital of Australia?", "Sydney.", "s")
    store.record(-1, "wrong", "", "What is the tallest mountain?", "K2.", "s")  # no correction: nothing to check
    store.record(-1, "misunderstood", "I meant Rust", "Explain memory", "Python...", "s")
    asked = []

    def research(query, level="normal"):
        asked.append((query, level))
        return SimpleNamespace(answer="Canberra is the capital of Australia.",
                               sources=[SimpleNamespace(final_url="https://example.org/canberra")])

    checked = store.check_corrections(SimpleNamespace(research=research), limit=5)
    assert asked == [("What is the capital of Australia?", "fast")]
    assert checked == [{"id": row, "status": "agrees", "sources": ["https://example.org/canberra"]}]
    assert store.check_corrections(SimpleNamespace(research=research), limit=5) == []
    assert "they support his correction" in store.prompt_note("capital of Australia which city")
    assert "(sources: they support his correction)" in store.report()
    assert "1 were supported" in store.summary_line()


def test_private_questions_and_failed_research_are_not_reported_as_checked(tmp_path):
    from types import SimpleNamespace

    store = OwnerFeedback(tmp_path / "owner-feedback.db")
    store.record(-1, "wrong", "it is 42", r"What is in C:\Users\me\secret.txt?", "Nothing.", "s")
    store.record(-1, "wrong", "it's Canberra", "What is the capital of Australia?", "Sydney.", "s")

    def broken(query, level="normal"):
        raise OSError("offline")

    assert store.check_corrections(SimpleNamespace(research=broken), limit=5) == []
    statuses = {row["request"][:12]: (row["check_status"], row["check_note"]) for row in store.rows()}
    assert statuses["What is in C"][0] == "private"
    assert statuses["What is the "][0] is None and "will retry" in statuses["What is the "][1]
    # A failed attempt waits a day before it is tried again.
    assert store.pending_checks(limit=5) == []


@pytest.mark.parametrize("text, expected", [
    ("that's wrong, it's Canberra", "kept your correction (\"it's Canberra\")"),
    ("wrong answer", "Tell me the right answer"),
    ("good answer", "Thanks, noted"),
])
def test_a_verdict_on_a_plain_reply_is_acknowledged(text, expected):
    from app.owner_feedback import acknowledgement

    assert expected in acknowledgement(text)


def test_a_verdict_that_asks_for_a_redo_is_left_to_the_normal_path():
    from app.owner_feedback import acknowledgement

    assert acknowledgement("that's wrong, check again") is None
    assert acknowledgement("open the file") is None
