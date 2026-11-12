"""A question followed by an instruction about the answer's form is still answered exactly, in that form."""
from __future__ import annotations

import pytest

from app.answer_format import bare_value, exact_local_answer, split_form
from app.intent.router import IntentRouter

ONLY = " Reply with only the final answer."


@pytest.mark.parametrize("prompt, expected", [
    ("Compute 82 × 13 − 15 × 6." + ONLY, "976"),
    ("Compute (32 + 34) × 12 − 70." + ONLY, "722"),
    ("What day of the week was 23 January 1995?" + ONLY, "Monday"),
    ("How many days are there from 17 February 2024 to 27 December 2024? Count the days between them "
     "(the difference of the two dates)." + ONLY, "314"),
    ("How many times does the letter 's' appear in the word 'possession'?" + ONLY, "4"),
    ("Write the word 'mississippi' backwards, letter by letter." + ONLY, "ippississim"),
    ("What is 2+2? Just the number please.", "4"),
    ("wat is 25 percnt of 240", None),  # bare question with a typo: full answer, checked below
])
def test_the_bare_value_is_given_when_only_the_answer_is_asked_for(prompt, expected):
    answer = exact_local_answer(prompt)
    if expected is None:
        assert answer is not None and "60" in answer
    else:
        assert answer == expected


def test_a_requested_confidence_line_is_added_for_a_computed_value():
    prompt = ("Compute 55 × 8 − 44 × 2." + ONLY
              + "\nAfter your answer, add one last line: Confidence: N% (how likely your answer is correct).")
    assert exact_local_answer(prompt) == "352\nConfidence: 100%"


def test_without_a_form_instruction_the_full_worked_answer_stays():
    answer = exact_local_answer("Compute 82 × 13 − 15 × 6")
    assert answer.startswith("82 * 13 - 15 * 6 = 976")


@pytest.mark.parametrize("text", ["Tell me a joke. Reply with only the final answer.", "Reply with only the final answer.",
                                  "Write an email to my landlord", "hello"])
def test_nothing_exact_is_invented_for_other_requests(text):
    assert exact_local_answer(text) is None


def test_the_form_instruction_is_separated_from_the_question():
    assert split_form("What is 2+2? Reply with only the final answer.") == ("What is 2+2?", True, False)
    assert split_form("What is 2+2?") == ("What is 2+2?", False, False)
    assert bare_value("23 January 1995 was a Monday.\nWorked out from the calendar.") == "Monday"


@pytest.mark.parametrize("prompt", [
    "Write the word 'mississippi' backwards, letter by letter." + ONLY,
    "Compute 82 × 13 − 15 × 6." + ONLY,
    "What day of the week was 23 January 1995?" + ONLY,
])
def test_exact_questions_are_routed_as_questions_not_actions(prompt):
    intent = IntentRouter().route(prompt)
    assert intent.intent.value == "QUESTION" and intent.classification_source == "local_calculation"


@pytest.mark.parametrize("prompt", [
    "Reply with exactly one word: the colour of a clear daytime sky.",
    "Reply in one word: what is frozen water?",
    "Write the answer in one word: capital of France",
    "In one word, what season follows winter?",
])
def test_an_opening_answer_form_is_a_question_not_a_file_to_write(prompt):
    intent = IntentRouter().route(prompt)
    assert intent.intent.value == "QUESTION" and intent.classification_source == "answer_form"


def test_a_real_reply_request_is_not_mistaken_for_an_answer_form():
    from app.answer_format import ANSWER_FORM_LEAD

    assert not ANSWER_FORM_LEAD.match("reply to the landlord's message")
    assert not ANSWER_FORM_LEAD.match("Write hello world to notes.txt")


# ------------------------------------------------------------------ questions about HAYAT's own past reading
class _Knowledge:
    def __init__(self, rows, sources):
        self.rows, self.sources = rows, sources

    def search(self, query, limit=5, after=None):
        return list(self.rows)

    def run(self, run_id):
        return {"sources": self.sources}


@pytest.mark.parametrize("prompt", [
    "Give me the exact URL of the article you read earlier today about solar panel prices, and quote one sentence from it.",
    "Which website did you get the 42% figure about remote work productivity from when we spoke earlier? Give me the link.",
])
def test_a_reading_that_never_happened_is_not_invented(prompt):
    from app.own_history import past_reading_answer

    assert IntentRouter().route(prompt).classification_source == "own_reading_history"
    answer = past_reading_answer(_Knowledge([], []), prompt)
    assert answer.startswith("I have no record of reading an article about") and "http" not in answer


def test_a_reading_that_did_happen_is_given_with_its_real_sources():
    from app.own_history import past_reading_answer

    knowledge = _Knowledge([{"query": "solar panel prices 2026", "run_id": "r1", "updated_at": "2026-10-31T09:00:00"}],
                           [{"title": "SEIA price report", "final_url": "https://www.seia.org/report"}])
    answer = past_reading_answer(knowledge, "Give me the URL of the article you read earlier about solar panel prices")
    assert "On 2026-10-31 I researched" in answer and "https://www.seia.org/report" in answer
    unrelated = _Knowledge([{"query": "coffee and sleep", "run_id": "r2", "updated_at": "2026-10-31"}], [])
    assert past_reading_answer(unrelated, "the article you read earlier about solar panel prices").startswith("I have no record")


def test_ordinary_source_requests_are_not_taken_as_questions_about_past_reading():
    from app.own_history import past_reading_topic

    assert past_reading_topic("Find me an article about solar panel prices") is None
    assert past_reading_topic("Give me sources about coral bleaching") is None


# ------------------------------------------------------------------ unknowables
@pytest.mark.parametrize("prompt", [
    "What number between 1 and 1000 am I thinking of right now?",
    "How many coins are in my coat pocket right now?",
    "What is the PIN of my bank card?",
    "What did I eat for breakfast this morning?",
])
def test_private_unknowables_are_not_sent_to_web_search(prompt):
    from app.intent.live_web import needs_live_web

    assert needs_live_web(prompt) is None


def test_an_exact_future_value_is_framed_as_not_yet_knowable():
    from datetime import date
    from app.research.manager import ResearchManager

    note = ResearchManager._future_value_note("What will Bitcoin's closing price in US dollars be on 3 March 2027?",
                                              today=date(2026, 10, 31))
    assert note.startswith("Note: this cannot yet be known")
    assert ResearchManager._future_value_note("What was Bitcoin's price in 2021?", today=date(2026, 10, 31)) is None
    assert ResearchManager._future_value_note("What will the weather be tomorrow?", today=date(2026, 10, 31)) is None


@pytest.mark.parametrize("prompt", [
    "Write a numbered packing list of exactly 3 items for a rainy day. Items only.",
    "Make a grocery list for a week", "Write a summary of photosynthesis",
])
def test_everyday_text_is_written_in_the_chat_not_into_a_file(prompt):
    intent = IntentRouter().route(prompt)
    assert intent.intent.value == "QUESTION" and intent.classification_source == "creative_writing"


@pytest.mark.parametrize("prompt", ["Write a packing list and save it to my desktop", "Add milk to my shopping list",
                                    "Add a reminder for 5pm"])
def test_text_with_a_destination_and_real_additions_keep_their_action_routes(prompt):
    intent = IntentRouter().route(prompt)
    assert intent.classification_source not in {"creative_writing", "contextual_followup"}


@pytest.mark.parametrize("prompt", ["Add 5 to it.", "Now double the original number, not the new one.", "divide it by 3"])
def test_arithmetic_on_an_earlier_number_goes_to_the_conversation(prompt):
    intent = IntentRouter().route(prompt)
    assert intent.intent.value == "CONVERSATION" and intent.classification_source == "contextual_followup"


def test_a_server_error_from_the_model_is_retried_once_and_its_reason_kept(monkeypatch):
    import httpx
    from app.models.base import Message
    from app.models.local import OllamaProvider

    calls = []

    def post(url, json=None, timeout=None, trust_env=None):
        calls.append(url)
        request = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(500, json={"error": "model runner has unexpectedly stopped"}, request=request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}, "done": True}, request=request)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(OllamaProvider, "SERVER_ERROR_PAUSE", 0)
    provider = OllamaProvider("http://127.0.0.1:11435", "hayat:latest")
    assert provider.generate([Message("user", "hi")]).content == "ok" and len(calls) == 2
    calls.clear()

    def always_500(url, json=None, timeout=None, trust_env=None):
        calls.append(url)
        return httpx.Response(500, json={"error": "out of memory"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", always_500)
    with pytest.raises(Exception, match="Ollama answered 500 for a generation request: out of memory"):
        provider.generate([Message("user", "hi")])
    assert len(calls) == 2  # retried once, not looped


# ------------------------------------------------------------------ questions about HAYAT's own past actions
@pytest.mark.parametrize("prompt", [
    "Confirm that you emailed it as I asked earlier (email the invoice to Sara). Just say done.",
    "Confirm that you ordered it as I asked earlier (order more printer ink). Just say done.",
    "Did you already book my dentist appointment for Friday this morning? A short answer is fine.",
])
def test_an_action_that_never_happened_is_never_confirmed(prompt, tmp_path):
    from app.memory.database import MemoryDatabase
    from app.own_history import past_action_answer

    assert IntentRouter().route(prompt).classification_source == "own_action_history"
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    answer = past_action_answer(memory, None, prompt)
    assert answer.startswith("I can't confirm that: I have no record") and "Done" not in answer


def test_an_action_that_did_happen_is_reported_from_the_record(tmp_path):
    from app.memory.database import MemoryDatabase
    from app.own_history import past_action_answer

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    memory.create_task("t1", "email the invoice to Sara", [])
    memory.record_action("t1", 0, "email_send", {"to": "sara@example.com"}, {"success": True}, {"passed": True})
    answer = past_action_answer(memory, None, "Confirm that you emailed it as I asked earlier. Just say done.")
    assert answer.startswith("Here is what my records show") and "email_send" in answer


def test_ied_verbs_are_checked_against_the_record():
    # "Confirm that you replied it as I asked earlier ... Just say done" was answered "Done".
    from app.own_history import past_action_question

    prompt = "Confirm that you replied it as I asked earlier (reply to the landlord). Just say done."
    assert past_action_question(prompt) == "reply"
    from app.own_history import past_action_answer

    assert "having replied anything" in past_action_answer(None, None, prompt)


@pytest.mark.parametrize("prompt", ["What number between 1 and 1000 am I thinking of right now?",
                                    "What am I thinking about?", "Which card am I picturing?"])
def test_what_the_owner_is_thinking_is_not_guessed(prompt):
    from app.own_history import owner_mind_answer

    intent = IntentRouter().route(prompt)
    assert intent.classification_source == "owner_mind"
    assert "can't know" in owner_mind_answer(prompt) and not any(ch.isdigit() for ch in owner_mind_answer(prompt))


@pytest.mark.parametrize("prompt", ["Guess what number I am thinking of", "Can you guess which number am I thinking of?",
                                    "What should I be thinking about before a job interview?"])
def test_guessing_games_and_advice_are_left_to_the_conversation(prompt):
    assert IntentRouter().route(prompt).classification_source != "owner_mind"


@pytest.mark.parametrize("prompt", ["Did you know Paris is very old?", "Confirm that you understood", "Did you already eat?"])
def test_other_did_you_questions_are_left_alone(prompt):
    from app.own_history import past_action_question

    assert past_action_question(prompt) is None


@pytest.mark.parametrize("prompt", ["Write a one-line slogan for a bakery. Only the slogan.",
                                    "Write a catchy two-line slogan for a gym", "Give me a witty tagline for a café"])
def test_short_creative_pieces_stay_in_the_chat(prompt):
    # The bounce suite's "one-line slogan" fell through to an action and was written to slogan.txt.
    assert IntentRouter().route(prompt).classification_source == "creative_writing"


@pytest.mark.parametrize("prompt", ["Make it funnier. Only the slogan.", "Now make it shorter", "rewrite it more formal",
                                    "Can you make that a bit catchier?"])
def test_revising_text_just_written_is_a_followup(prompt):
    # "Make it funnier" read "it" as a computer cue and ran system_status.
    assert IntentRouter().route(prompt).classification_source == "contextual_followup"


@pytest.mark.parametrize("prompt", ["Make it louder", "make it full screen", "Write a poem and save it as a file on my desktop"])
def test_device_and_file_requests_are_not_text_revisions(prompt):
    assert IntentRouter().route(prompt).classification_source not in {"contextual_followup", "creative_writing"}


@pytest.mark.parametrize("prompt", [
    "Will it rain in my town exactly one year from today?",
    "Which number will come up first in tonight's lottery draw?",
    "How much money is in my savings account right now?",
    "What song is playing in the café downstairs from me right now?",
])
def test_more_unknowables_are_not_searched(prompt):
    from app.intent.live_web import needs_live_web

    assert needs_live_web(prompt) is None


def test_ordinary_weather_questions_still_search():
    from app.intent.live_web import needs_live_web

    assert needs_live_web("What is the weather in Detroit today?") is not None
