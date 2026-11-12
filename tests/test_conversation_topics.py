"""Conversation topics: relations, anchored follow-ups, topic-scoped history, the owner's controls, the benchmark."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.agent.conversation import ConversationService
from app.evaluation.topic_cases import CASES, HOLDOUT_CASES, run_benchmark
from app.intent.models import IntentKind, RequestIntent
from app.memory.database import MemoryDatabase
from app.models.base import ModelProvider, ModelResponse
from app.topics import ConversationTopics, anchor, expand, leans_on_topic, resolve_relative_years, topics_request

TODAY = date(2026, 11, 9)
START = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


def _talk(store, messages, session="s", start=START, step=2):
    decisions = []
    for index, text in enumerate(messages):
        moment = start + timedelta(minutes=step * index)
        decision = store.decide(session, text, now=moment, today=TODAY)
        store.observe_reply(decision, f"reply about {decision.title}", session_id=session,
                            now=moment + timedelta(seconds=30))
        decisions.append(decision)
    return decisions


def test_shorthand_and_relative_years_are_arithmetic_not_names():
    assert expand("predict for nxt yr") == "predict for next year"
    assert resolve_relative_years("predict for next year", TODAY) == "predict for 2027"
    assert resolve_relative_years("compare with last year", TODAY) == "compare with 2025"


def test_the_nxt_failure_is_read_as_next_years_forecast_in_its_topic(tmp_path):
    first, follow = _talk(ConversationTopics(tmp_path / "t.db"),
                          ["What is the GDP growth forecast for India in 2026?",
                           "Look at the latest and predict for nxt year"])
    assert first.relation == "new_conversation"
    assert follow.relation == "continue" and follow.topic_id == first.topic_id
    assert "nxt" not in follow.query.lower()
    assert all(word in follow.query for word in ("GDP", "India", "2027", "latest"))
    assert follow.anchored.startswith("Look at the latest and predict for 2027")
    assert "nxt = next" in follow.note()


def test_what_about_swaps_the_named_place_and_keeps_the_subject(tmp_path):
    _, swapped = _talk(ConversationTopics(tmp_path / "t.db"),
                       ["What is the GDP growth forecast for India in 2026?", "what about china"])
    assert swapped.query == "GDP growth forecast for china in 2026"


def test_an_unrelated_question_is_a_new_topic_and_going_back_restores_only_that_topic(tmp_path):
    store = ConversationTopics(tmp_path / "t.db")
    gpu, enough, pasta, back = _talk(store, [
        "Which GPU should I buy for running local LLMs with 8GB VRAM?", "is the RTX 5060 enough?",
        "btw give me a quick pasta recipe for dinner", "back to the gpu question from earlier"])
    assert enough.relation == "continue" and enough.topic_id == gpu.topic_id
    assert pasta.relation == "new_topic" and pasta.topic_id != gpu.topic_id
    assert back.relation == "return" and back.topic_id == gpu.topic_id
    restored = [item["content"] for item in back.restored]
    assert "is the RTX 5060 enough?" in restored and not any("pasta" in item for item in restored)


def test_reply_history_keeps_its_topic_and_names_what_was_set_aside(tmp_path):
    store = ConversationTopics(tmp_path / "t.db")
    texts = ["What is the GDP growth forecast for India in 2026?", "Which GPU should I buy for local LLMs?"]
    decisions = _talk(store, texts)
    history = []
    for text, decision in zip(texts, decisions):
        history += [{"role": "user", "content": text}, {"role": "assistant", "content": f"reply about {decision.title}"}]
    history.append({"role": "user", "content": "unseen notice text"})
    kept, aside = store.scope_history("s", history, decisions[1])
    contents = [item["content"] for item in kept]
    assert texts[1] in contents and texts[0] not in contents
    assert "unseen notice text" in contents  # never-seen messages are kept, not dropped on a guess
    assert aside == [decisions[0].title]


def test_corrections_status_people_and_small_talk(tmp_path):
    store = ConversationTopics(tmp_path / "t.db")
    news, fix, done = _talk(store, ["search the latest news on NXT", "no I meant the ticker, not wrestling", "is it done?"])
    assert fix.relation == "correction" and fix.topic_id == news.topic_id
    assert done.relation == "task_status"
    other = ConversationTopics(tmp_path / "o.db")
    brother, shifts, thanks = _talk(other, ["my brother Omar works as a nurse in Dubai",
                                            "what shifts does he usually work?", "thanks"])
    assert brother.person == "brother Omar"
    assert shifts.relation == "continue" and not shifts.anchored  # a person is never turned into a web query
    assert thanks.relation == "small_talk" and thanks.topic_id == brother.topic_id


def test_commands_and_answers_about_the_last_reply_are_never_reread(tmp_path):
    assert leans_on_topic("open it") == ""
    assert leans_on_topic("how did you get that?") == ""
    assert leans_on_topic("do it") == ""
    assert leans_on_topic("and for 2028?")


def test_a_long_gap_starts_a_new_conversation(tmp_path):
    store = ConversationTopics(tmp_path / "t.db")
    first = store.decide("s", "What is the capital of Australia?", now=START)
    later = store.decide("s", "What is the capital of Canada?", now=START + timedelta(hours=9))
    assert later.relation == "new_conversation" and later.conversation_id != first.conversation_id


def test_owner_controls_are_kept_as_measured_routing_errors(tmp_path):
    store = ConversationTopics(tmp_path / "t.db")
    gdp, gpu = _talk(store, ["What is the GDP growth forecast for India in 2026?", "Which GPU should I buy for local LLMs?"])
    joined = store.same_topic(gpu.message_id)
    assert joined["merged"] and joined["topic_id"] == gdp.topic_id
    assert {item["role"] for item in store.messages(gdp.topic_id)} == {"user", "assistant"}
    apart = store.separate(gpu.message_id)
    assert apart["separated"] and apart["topic_id"] != gdp.topic_id
    assert store.reactivate("s", gdp.topic_id)["reactivated"]
    stats = store.stats()
    assert stats["flagged_wrong"] == 2 and stats["owner_checked_accuracy"] == 0.0
    assert stats["owner_fixes"] == {"same_topic": 1, "separate": 1, "reactivate": 1}


def test_benchmark_beats_the_one_thread_baseline_in_and_out_of_sample():
    tuned, held_out = run_benchmark(CASES), run_benchmark(HOLDOUT_CASES)
    for result in (tuned, held_out):
        assert result["relation_accuracy"] >= 0.9, result["failures"]
        assert result["relation_accuracy"] > result["baseline"]["relation_accuracy"] + 0.25
        assert result["reference_resolution"] >= 0.9 > result["baseline"]["reference_resolution"]
        assert result["contamination_rate"] <= 0.1 < result["baseline"]["contamination_rate"]


def test_anchor_reads_compare_with_a_year():
    reading, query = anchor("compare it with last year", "GDP growth forecast for India in 2026", today=TODAY)
    assert "2025" in query and "comparison" in query and "2025" in reading


def test_topic_questions_route_to_the_record():
    assert topics_request("what topics have we discussed?")
    assert topics_request("what were we talking about")
    assert not topics_request("what is a topic sentence")


class _Recording(ModelProvider):
    name = "recorder"

    def __init__(self):
        self.calls = []

    def list_models(self):
        return ["recorder"]

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        self.calls.append(list(messages))
        return ModelResponse("recorded reply", "recorder", self.name)


def test_the_reply_writer_sees_only_its_topic_and_the_owners_words(tmp_path):
    memory = MemoryDatabase(tmp_path / "m.db")
    memory.initialize()
    store = ConversationTopics(tmp_path / "t.db")
    model = _Recording()
    chat = ConversationService(model, memory, "s")
    chat.topic_scope = store.scope_history
    for text in ("Explain how a heat pump works in winter", "give me a pasta recipe for dinner"):
        chat.topic_decision = store.decide("s", text, today=TODAY)
        chat.reply(text)
    sent = [str(message.content) for message in model.calls[-1]]
    assert "give me a pasta recipe for dinner" in sent
    assert "Explain how a heat pump works in winter" not in sent  # the exchange is out; only its title is named
    assert any("were set aside" in content and "heat pump" in content.lower() for content in sent)
    chat.topic_decision = decision = store.decide("s", "and what about lasagne", today=TODAY)
    chat.reply(decision.anchored or "and what about lasagne")
    users = [row["content"] for row in memory.conversation_history("s", 20) if row["role"] == "user"]
    assert users[-1] == "and what about lasagne"  # recorded as typed; the reading is a note


@pytest.mark.parametrize("message", ["Look at the latest and predict for nxt year"])
def test_a_real_turn_routes_the_topic_reading_and_records_the_owners_words(tmp_path, monkeypatch, message):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.topics = ConversationTopics(tmp_path / "topics.db")
    # The live-web invariant (a forecast question goes to research) is covered elsewhere; here the reading is checked.
    monkeypatch.setattr("app.assistant.needs_live_web", lambda text: None)
    routed = []

    def route(text, session=None):
        routed.append(text)
        if "about the GDP" in text:
            return RequestIntent(IntentKind.CONVERSATION, 1.0, text, response="read in its topic")
        if text.startswith("What is the GDP"):
            return RequestIntent(IntentKind.CONVERSATION, 1.0, text, response="first answer")
        return RequestIntent(IntentKind.ACTION, 0.7, text, True, False, True, classification_source="deterministic")

    monkeypatch.setattr(rt.intent_router, "route", route)
    rt.assistant.handle("What is the GDP growth forecast for India in 2026?")
    reply = rt.assistant.handle(message)
    year = str(date.today().year + 1)
    assert routed[-2] == message and routed[-1].startswith(f"Look at the latest and predict for {year}")
    assert reply.display_response == "read in its topic"
    assert rt.session.last_user_input == message
    topic = reply.response.metadata["topic"]
    assert topic["relation"] == "continue" and year in topic["query"] and topic["title"].startswith("GDP growth")
    assert [item["role"] for item in rt.topics.messages(topic["topic_id"])] == ["user", "assistant"] * 2


def test_someone_joining_the_plan_stays_in_the_plans_topic(tmp_path):
    """Live chat 2026-11-11: "my wife is coming too. How many of us now?" became a topic about the wife, the trip's
    history was set aside, and the answer was two instead of four."""
    store = ConversationTopics(tmp_path / "t.db")
    trip, count, joined = _talk(store, ["I'm planning a trip to Lisbon in May with my two kids.",
                                        "How many people are going on this trip, counting me?",
                                        "Actually, my wife is coming too. How many of us now?"])
    assert joined.relation == "continue" and joined.topic_id == trip.topic_id and not joined.set_aside
    other = ConversationTopics(tmp_path / "o.db")
    _plan, sister = _talk(other, ["Plan a dinner party for six on Saturday.", "My sister is joining us as well."])
    assert sister.relation == "continue"
    # A person named without joining anything is still their own subject.
    third = ConversationTopics(tmp_path / "p.db")
    _first, nurse = _talk(third, ["Plan a dinner party for six on Saturday.", "my brother Omar works as a nurse in Dubai"])
    assert nurse.relation == "other_person"


def test_calendar_and_arithmetic_questions_never_lean_on_the_previous_topic(tmp_path):
    """Live chat 2026-11-11: both questions were re-read as "…, about the exactly five words describing the ocean",
    and the first was sent to a web search for that text."""
    for message in ("What date will it be 45 days from today?", "What day of the week was 1 January 2000?",
                    "How many days are there from March 3, 2026 to July 19, 2026?", "What's 15% of 240?",
                    "what is 17*23 now?"):
        assert leans_on_topic(message) == "", message
    store = ConversationTopics(tmp_path / "t.db")
    decisions = _talk(store, ["Give me exactly five words describing the ocean, separated by commas, nothing else.",
                              "What date will it be 45 days from today?", "What day of the week was 1 January 2000?"])
    assert not any(decision.anchored for decision in decisions)
    # A message that really leans on its topic is still read through it.
    assert leans_on_topic("and for 2028?") and leans_on_topic("what about china?")


def test_a_refinement_stays_with_the_answer_it_refines(tmp_path):
    """Live 2026-11-12: "keep it under 50 dollars" after gift ideas became a new topic and the reply asked "What are
    we buying?"."""
    store = ConversationTopics(tmp_path / "t.db")
    first, ask, refine = _talk(store, ["my sister's birthday is on Saturday and she loves painting",
                                       "what should I get her?", "keep it under 50 dollars"])
    assert refine.relation == "continue" and refine.topic_id == first.topic_id and not refine.set_aside
    other = ConversationTopics(tmp_path / "o.db")
    _plan, cheaper = _talk(other, ["Find me a laptop for coding", "something cheaper"])
    assert cheaper.relation == "continue"
