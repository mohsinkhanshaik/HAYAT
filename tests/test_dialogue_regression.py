"""Regression tests reproducing every failure of the 2026-11-11 conversation, and the fixes for each.

Failures, in the order they happened:
 1. "work on it" (after a failure) went back to a day-old topic on the one shared word "work".
 2. "ok can u fix it?" was then read as "fix it, about ... show you how this works with all tools": a tool demo.
 3. Four requests to investigate the ModelError produced invented ports, restarts and timings, then a question
    back to the owner. Nothing was read; the records held the answer.
 4. "ways to make money ... higher ways ... as AI rises" got the fixed list of small services.
 5. "this is same reply ... look again but deeper" was a new topic; the reply came from the ModelError thread.
 6. "i dint ask about model errpr i was asking u to look better ideas ... billionaire by 2030" was sent back to
    the tool whose list had just been refused: the same list came back.
 7. "again same thing dont treat this fact as i can change" got a reply "tailored to your fixed hardware".
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from app.agent.core import TaskOutcome
from app.ambition import ambition_request, evaluate, required_return, wants_small_start, years_left
from app.answer_feedback import read as read_feedback
from app.dialogue_state import CIRCUMSTANCE, PERMANENT, DialogueState, alike, read_facts, read_goal
from app.evaluation.dialogue_cases import BASELINE_REPLIES, MONEY_SUBJECT, TRANSCRIPT, relevance, repetition, score
from app.investigation import (HYPOTHESIS, NOT_RECORDED, VERIFIED, Investigator, follow_up_on_failure,
                               investigation_request, parse_window)
from app.topics import ConversationTopics

START = datetime(2026, 11, 11, 7, 10, tzinfo=UTC)
TODAY = date(2026, 11, 11)
MONEY = TRANSCRIPT[11][1]
SAME_REPLY = TRANSCRIPT[12][1]
NOT_MODEL_ERROR = TRANSCRIPT[13][1]
CAN_CHANGE = TRANSCRIPT[14][1]


# --------------------------------------------------------------------------------------- reading the complaints


def test_every_complaint_in_the_transcript_is_read_as_feedback_on_the_answer():
    same = read_feedback(SAME_REPLY)
    assert {"repeated", "deeper"} <= same.kinds and same.rejects and same.about_answer
    denied = read_feedback(NOT_MODEL_ERROR)
    assert "off_topic" in denied.kinds and denied.denied == "model errpr"
    assert denied.restated == "look better ideas to make money and become billionaire by 2030"
    change = read_feedback(CAN_CHANGE)
    assert {"repeated", "changeable"} <= change.kinds


@pytest.mark.parametrize("text", [
    MONEY, "play the same song again", "do the same for china", "same thing for 2028", "give me better ideas for a logo",
    "what is the same thing as a marginal cost", "ok can u fix it?", "i just did check again", "look again at me",
    "generate an image ufc blood shed fight",
])
def test_requests_of_their_own_are_not_feedback(text):
    assert read_feedback(text) is None


# ----------------------------------------------------------------------------------------- topics, replayed


def _replay(store: ConversationTopics, turns, start=START, session="s"):
    decisions = []
    for minutes, text in turns:
        moment = start + timedelta(minutes=minutes)
        decision = store.decide(session, text, now=moment, today=TODAY)
        store.observe_reply(decision, f"reply to {text[:40]}", session_id=session, now=moment + timedelta(seconds=20))
        decisions.append(decision)
    return decisions


def test_the_transcript_keeps_follow_ups_and_complaints_in_their_topic(tmp_path):
    store = ConversationTopics(tmp_path / "topics.db")
    day_before = START - timedelta(hours=23)
    store.decide("s", "i want you to show you how this works with a quick task which include all tools", now=day_before)
    store.decide("s", "check each tool works and fix what does not work", now=day_before + timedelta(minutes=2))
    decisions = _replay(store, TRANSCRIPT)
    first = decisions[0].topic_id
    # Failures 1 and 2: "work on it" and "can u fix it?" stay with the failure just reported.
    assert decisions[2].relation == "continue" and decisions[2].topic_id == first
    assert decisions[3].topic_id == first and "show you how this works" not in decisions[3].anchored
    # Failures 5-7: every complaint stays with the money question it complains about.
    money = decisions[11]
    assert money.relation == "new_topic"
    for decision in decisions[12:]:
        assert decision.relation == "correction" and decision.topic_id == money.topic_id
        assert decision.feedback["kinds"]
    assert "ModelError" not in decisions[13].note() and "refused answer" in decisions[13].note()


def test_a_complaint_is_never_a_topics_subject_but_a_restated_request_is(tmp_path):
    store = ConversationTopics(tmp_path / "topics.db")
    first = store.decide("s", "hello", now=START)
    complaint = store.decide("s", "this is same reply, look again but deeper", now=START + timedelta(minutes=1))
    assert complaint.topic_id == first.topic_id
    row = store.topics("s")[0]
    assert "same reply" not in row["subject"].lower()


def test_a_restated_request_moves_to_the_topic_it_belongs_to_across_conversations(tmp_path):
    store = ConversationTopics(tmp_path / "topics.db")
    _replay(store, [(0, "what business can i start to make money and become a billionaire by 2030")])
    # Next morning: a new conversation, and a new subject.
    later = START + timedelta(hours=9)
    new = _replay(store, [(0, "explain how vaccines train the immune system")], start=later)[0]
    assert new.relation == "new_conversation"
    fix = store.decide("s", "i didn't ask about vaccines, i was asking about money and becoming a billionaire by 2030",
                       now=later + timedelta(minutes=2), today=TODAY)
    assert fix.relation == "correction" and fix.topic_id != new.topic_id
    assert "make money" in fix.title.lower() and fix.restored  # its earlier exchange comes back with it
    # And a plain new subject afterwards still opens a new topic.
    after = store.decide("s", "what is the boiling point of nitrogen", now=later + timedelta(minutes=4), today=TODAY)
    assert after.relation == "new_topic"


# --------------------------------------------------------------------------------- goals, facts, refused replies


def test_goals_and_facts_are_read_from_the_owners_words_in_two_classes(tmp_path):
    goal = read_goal("ok for now i have 0$ with me where to start and want to become billionare by 2030")
    assert (goal["amount"], goal["horizon"]) == (1e9, 2030)
    assert read_goal("how did elon musk become a billionaire") is None
    facts = {row["attribute"]: row for row in read_facts("am in michigan now and after a month moving to san francisco")}
    assert facts["location"]["value"].lower() == "michigan" and facts["location"]["kind"] == CIRCUMSTANCE
    assert facts["planned_move"]["value"].lower() == "san francisco"
    assert read_facts("what if i have $1000 to start") == []
    assert read_facts("look at detailed and cheapest moving to california") == []
    assert read_facts("my name is Mohsin Khan Shaik")[0]["kind"] == PERMANENT

    state = DialogueState(tmp_path / "dialogue.db")
    state.observe("s", "i have rtx 5060 8gb and ram 48 gb", now=START)
    state.observe("s", "i have $0 with me", now=START)
    state.observe("s", "now i have $5000 saved", now=START + timedelta(days=3))
    current = {row["attribute"]: row for row in state.facts()[CIRCUMSTANCE]}
    assert current["capital"]["value"] == "$5000"  # the later statement replaces the earlier one
    assert len(state.fact_history("capital")) == 2  # and the earlier one is kept as history
    brief = state.brief("s", today=TODAY + timedelta(days=90))
    assert "can change" in brief and "Never present one as fixed" in brief and "may be out of date" in brief


def test_a_refused_reply_is_marked_and_its_copies_are_caught(tmp_path):
    state = DialogueState(tmp_path / "dialogue.db")
    canned = BASELINE_REPLIES[0]
    state.note_reply("s", MONEY, canned, source="tool:business_ideas", now=START)
    state.note_reply("s", SAME_REPLY, "an unrelated reply about logs", now=START + timedelta(minutes=1))
    state.note_reply("s", NOT_MODEL_ERROR, canned, source="tool:business_ideas", now=START + timedelta(minutes=5))
    refused = state.reject("s", {"repeated"}, CAN_CHANGE, now=START + timedelta(minutes=6))
    assert refused["source"] == "tool:business_ideas"
    assert state.first_request(refused) == "look better ideas to make money and become billionaire by 2030"
    assert state.refused_copy(canned + " ") is not None
    assert state.refused_copy(canned.replace("Graphic", "Logo")) is not None  # reworded slightly: still a copy
    assert state.refused_copy("A different answer about company value and revenue growth.") is None
    measured = state.measures(now=START + timedelta(minutes=7))
    assert measured["repeats_of_an_earlier_reply"] == 1 and measured["refused"] == 2


def test_goals_and_facts_backfill_once_from_earlier_conversations(tmp_path):
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    memory.record_message("old", "user", "am in michigan now and i want to make 1 billion till 2030")
    state = DialogueState(tmp_path / "dialogue.db")
    assert state.backfill(memory, since="2000-01-01") == {"goals": 1, "facts": 1}
    assert state.backfill(memory, since="2000-01-01") == {"goals": 0, "facts": 0}
    assert state.main_goal()["horizon"] == 2030


# ------------------------------------------------------------------------------------------------- ambition


def test_a_goal_sized_question_is_recognised_and_a_small_start_is_left_alone():
    assert ambition_request(MONEY).reason.startswith("asks for larger")
    assert ambition_request(NOT_MODEL_ERROR).amount == 1e9
    for text in ("how can i make money", "give me side hustle ideas to make money this week",
                 "how did elon musk become a billionaire", "how to earn $5000 a month"):
        assert ambition_request(text) is None
    assert wants_small_start("show the starter business list")


def test_the_evaluation_is_arithmetic_with_its_assumptions_and_no_remembered_statistics():
    assert round(years_left(2030, TODAY), 1) == 4.1
    assert round(required_return(1e6, 1e9, 4.1), 2) == 4.39
    result = evaluate(1e9, 2030, today=TODAY, circumstances=[{"label": "money in hand", "value": "0$", "at": "2026-10-01"}])
    text = result.to_dict()["text"]
    assert "0.17% of what is needed" in text and "cannot reach it" in text
    assert "Selling your own hours" in text and "Not the destination" in text  # freelancing is a first step at most
    assert "I am not stating a probability from memory" in text
    assert [stage["year"] for stage in result.milestones] == [2027, 2028, 2029, 2030]
    assert result.milestones[-1]["yearly_revenue"] == 500_000_000
    assert "where the first step begins, not a limit" in text and "tell me when it changes" in text
    assert len(result.questions) == 3


def test_asking_again_goes_further_and_never_repeats_itself():
    circumstances = [{"attribute": "hardware", "value": "rtx 5060 8gb", "label": "computer", "at": "2026-11-09"},
                     {"attribute": "capital", "value": "0$", "label": "money in hand", "at": "2026-10-01"}]
    asked: list[str] = []
    replies = []
    for number in range(1, 8):
        result = evaluate(1e9, 2030, today=TODAY, circumstances=circumstances, round_number=number, asked=asked,
                          evidence=[{"question": asked[0], "answer": "Sourced: about 1% reach it."}] if number == 2 else [])
        assert not set(result.questions) & set(asked)  # no question researched twice
        asked += result.questions
        replies.append(result.to_dict()["text"])
    # Every look with something new to say differs from all the others; once nothing is left, it says so plainly
    # (the same honest sentence each time) instead of rewording an old answer to look new.
    fresh = [reply for reply in replies if "I have nothing new to add" not in reply]
    assert len(fresh) == 3 and repetition(fresh)["copies"] == 0 and repetition(replies[:4])["copies"] == 0
    assert "challenged" in replies[1] and "rtx 5060" in replies[1] and "about 1% reach it" in replies[1]
    assert all("I have nothing new to add" in reply for reply in replies[3:])


# ------------------------------------------------------------------------------------------------ investigation


def _incident_store(path: Path) -> Path:
    """The event store of 2026-11-11 07:10-07:27 UTC, as it was recorded (ids and times from the real store)."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT,timestamp TEXT NOT NULL,event_type TEXT NOT NULL,"
               "source TEXT NOT NULL,payload_json TEXT NOT NULL,handled INTEGER NOT NULL DEFAULT 0, sequence INTEGER, "
               "runtime_id TEXT, session_id TEXT, turn_id TEXT, task_id TEXT, final INTEGER NOT NULL DEFAULT 0, supersedes TEXT)")
    traceback = ('Traceback (most recent call last):\n  File "D:\\\\AI\\\\app\\\\models\\\\streaming.py", line 119, in '
                 'generate_answer\n  File "D:\\\\AI\\\\app\\\\models\\\\intelligent_router.py", line 145, in generate_stream\n')
    rows = [
        (75835, "07:10:53", "TURN_RECEIVED", "turn-a", {}),
        (75839, "07:11:37", "RESOURCE_ALERT", "", {"ram_used_percent": 98.0, "disk_free_bytes": 3476352274432}),
        (75846, "07:15:15", "FAILURE_RECORDED", "turn-a", {
            "failure_id": "fail-96fe8a09ebf6", "exception_type": "ModelError", "provider": "ollama", "stage": "turn",
            "exception_message": "No bounded streaming provider completed the response", "retry_count": 0,
            "recovery_attempted": False, "user_request": "what are u learning and what are skill that u learned",
            "traceback": traceback}),
        (75847, "07:15:15", "TURN_FAILED", "turn-a", {"error": "ModelError: No bounded streaming provider completed the response"}),
        (75848, "07:15:30", "TURN_RECEIVED", "turn-b", {}),
        (75852, "07:15:30", "TURN_COMPLETED", "turn-b", {}),
        (75855, "07:15:44", "TURN_RECEIVED", "turn-c", {}),
        (75862, "07:17:55", "FAILURE_RECORDED", "turn-c", {
            "failure_id": "fail-9a5006073ba7", "exception_type": "ModelError", "provider": "ollama", "stage": "turn",
            "exception_message": "No bounded streaming provider completed the response", "retry_count": 0,
            "user_request": "work on it", "traceback": traceback}),
        (75873, "07:24:47", "TURN_RECEIVED", "turn-d", {}),
        (75879, "07:26:28", "TURN_COMPLETED", "turn-d", {}),
    ]
    for event_id, clock, kind, turn, payload in rows:
        db.execute("INSERT INTO events(id,timestamp,event_type,source,payload_json,turn_id) VALUES(?,?,?,?,?,?)",
                   (event_id, f"2026-11-11T{clock}.000000+00:00", kind, "gui", json.dumps(payload), turn))
    db.commit()
    db.close()
    return path


def test_the_incident_is_investigated_from_the_records_with_every_claim_labelled(tmp_path):
    store = _incident_store(tmp_path / "events.db")
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "ollama-hayat.log"
    old.write_text("time=2026-11-07T20:49:10Z level=INFO msg=old\n", encoding="utf-8")
    stamp = datetime(2026, 11, 7, 20, 49, tzinfo=UTC).timestamp()
    import os

    os.utime(old, (stamp, stamp))
    question = TRANSCRIPT[7][1]
    assert investigation_request(question)
    result = Investigator(store, logs).investigate(text=question, now=datetime(2026, 11, 11, 7, 42, tzinfo=UTC))
    assert (result.start, result.end) == (datetime(2026, 11, 11, 7, 10, tzinfo=UTC), datetime(2026, 11, 11, 7, 25, tzinfo=UTC))
    assert [failure["failure_id"] for failure in result.failures] == ["fail-96fe8a09ebf6", "fail-9a5006073ba7"]
    assert [failure["ran_seconds"] for failure in result.failures] == [262.0, 131.0]
    text = result.to_dict()["text"]
    assert "memory was 98% full" in text and "events.db #75839" in text
    assert "Nothing was repaired: the next request simply succeeded" in text
    assert result.recovery["mechanism"] == "none_recorded" and result.recovery["next_model_reply"].startswith("2026-11-11T07:26:28")
    assert "was last written before this window began" in text or "were last written before" in text
    statuses = {finding.status for finding in result.findings}
    assert {VERIFIED, HYPOTHESIS, NOT_RECORDED} <= statuses
    cause = next(item for item in result.findings if item.status == HYPOTHESIS)
    assert "is an inference" in cause.claim and cause.confirm  # a likely cause is never presented as proven
    assert all(item.evidence for item in result.findings if item.status == VERIFIED)
    assert result.to_dict()["changed_anything"] is False and old.stat().st_mtime == stamp


def test_windows_and_requests_are_read_without_hijacking_other_research():
    now = datetime(2026, 11, 11, 7, 42, tzinfo=UTC)
    start, end = parse_window("what happened at 11:15 pm pacific", now=now)
    assert start == datetime(2026, 11, 11, 7, 0, tzinfo=UTC) and end - start == timedelta(minutes=30)
    start, end = parse_window("check the logs for the last 2 hours", now=now)
    assert end == now and end - start == timedelta(hours=2)
    for text in ("investigate the failures of Lehman Brothers in 2008", "research why the Titanic sank",
                 "investigate the problem of climate change"):
        assert not investigation_request(text)
    assert follow_up_on_failure("ok can u fix it?") and follow_up_on_failure("work on it")
    assert not follow_up_on_failure("can you fix my resume")


def test_a_recorded_failure_also_reaches_the_process_log(caplog):
    from app.failures import FailureLog

    with caplog.at_level(logging.WARNING, logger="app.failures"):
        FailureLog().record(stage="turn", exception_type="ModelError", exception_message="No bounded streaming provider")
    assert any("ModelError" in record.getMessage() for record in caplog.records)


# ----------------------------------------------------------------------------- the real assistant, end to end


class _Job:
    def __init__(self, query: str):
        self.job_id, self.query, self.status = f"job-{abs(hash(query)) % 9999}", query, "done"
        self.answer = f"Sourced answer about {query}"
        self._done = threading.Event()
        self._done.set()


class _Jobs:
    def __init__(self):
        self.started: list[tuple[str, str]] = []

    def start(self, query, **kwargs):
        self.started.append((query, kwargs.get("level", "")))
        return _Job(query)

    def wait_inline(self, job, **kwargs):
        return True

    def release(self, job):
        pass

    def running(self):
        return []

    def recent(self, limit=10):
        return [_Job(query) for query, _ in self.started][-limit:]


class _IdeasManager:
    """The routed path as it was: the business-ideas tool, which gives the same list every time."""

    def __init__(self):
        from app.tools.launch import Launchpad

        self.calls: list[str] = []
        self.ideas = Launchpad.ideas(None)

    def route(self, goal, **kwargs):
        self.calls.append(goal)
        event = {"tool": "business_ideas", "verification": {"passed": True},
                 "result": {"success": True, "output": self.ideas}}
        return TaskOutcome("task-ideas", goal, True, "complete", [event], [event])


def _runtime(tmp_path):
    from tests.test_assistant_service import runtime

    rt = runtime(tmp_path)
    rt.topics = ConversationTopics(tmp_path / "topics.db")
    rt.dialogue = DialogueState(tmp_path / "dialogue.db")
    rt.research_jobs = _Jobs()
    rt.manager = _IdeasManager()
    return rt


def test_the_money_turns_replayed_through_the_assistant_are_relevant_and_never_repeat(tmp_path):
    rt = _runtime(tmp_path)
    first = rt.assistant.handle("what business can i start")
    assert "Freelance writing" in first.display_response  # with no goal stated, the starter list is still there
    rt.assistant.handle("ok for now i have 0$ with me where to start and want to become billionare by 2030")
    replies = [rt.assistant.handle(text).display_response for _, text in TRANSCRIPT[11:]]
    canned = first.display_response
    assert all(alike(reply, canned) < 0.5 for reply in replies)  # the canned list never comes back
    assert rt.manager.calls == ["what business can i start"]  # the refused tool is not asked again
    measured = score(replies)
    assert measured["copies"] == 0 and measured["max_alike"] < 0.8
    assert min(measured["relevance"]) > max(score(BASELINE_REPLIES)["relevance"])
    assert all("ModelError" not in reply and "model error" not in reply.lower() for reply in replies)
    assert "you did not ask about model errpr" in replies[2]
    assert "where you start, not a limit" in replies[3] and "fixed hardware" not in replies[3]
    assert rt.dialogue.measures()["sent_again_after_being_refused"] == 0
    asked = [query for query, _ in rt.research_jobs.started]
    assert len(asked) == len(set(asked))  # every search is a new question
    assert all(level == "deep" for _, level in rt.research_jobs.started)


def test_the_baseline_shows_what_the_measures_catch():
    measured = score(BASELINE_REPLIES)
    assert measured["copies"] == 1 and measured["max_alike"] == 1.0
    assert measured["relevance"][1] == 0.0  # the ModelError reply to a money question
    assert relevance(MONEY_SUBJECT, "Company value and yearly revenue growth to become a billionaire by 2030") > 0.5


def test_fix_it_after_a_reported_failure_is_investigated_not_demonstrated(tmp_path):
    rt = _runtime(tmp_path)
    rt.investigator = Investigator(_incident_store(tmp_path / "events.db"), tmp_path / "no-logs")
    from app.failures import FailureLog

    rt.failures = FailureLog()
    rt.failures.record(stage="turn", exception_type="ModelError", exception_message="No bounded streaming provider")
    rt.assistant.handle("whats wrong?")
    reply = rt.assistant.handle("ok can u fix it?")
    assert reply.intent.classification_source == "investigation"
    assert "I have not fixed or changed anything" in reply.display_response
    assert "What the records show (verified)" in reply.display_response
    assert rt.manager.calls == []  # no tool was run to "demonstrate" anything


def test_the_reply_writer_is_told_the_goal_and_that_circumstances_can_change(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    from tests.test_conversation_topics import _Recording

    memory = MemoryDatabase(tmp_path / "m.db")
    memory.initialize()
    state = DialogueState(tmp_path / "dialogue.db")
    state.observe("s", "i have $0 and i want to make 1 billion by 2030")
    model = _Recording()
    chat = ConversationService(model, memory, "s")
    chat.dialogue_supplier = state.brief
    chat.reply("what should I focus on this year?")
    sent = "\n".join(str(message.content) for message in model.calls[-1])
    assert "$1 billion, by 2030" in sent and "can change" in sent and "Never present one as fixed" in sent


def test_the_new_tools_are_registered_and_catalogued(tmp_path):
    from app import capabilities
    from app.ambition import register_ambition_tools
    from app.dialogue_state import register_dialogue_tools
    from app.investigation import register_investigation_tools
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    state = DialogueState(tmp_path / "dialogue.db")
    state.observe("s", "i want to make 1 billion by 2030")
    register_dialogue_tools(registry, state, None)
    register_ambition_tools(registry, state)
    register_investigation_tools(registry, Investigator(_incident_store(tmp_path / "events.db"), None))
    goal = registry.execute("ambition_evaluate", {})
    assert goal.success and goal.output["amount"] == 1e9 and goal.output["horizon"] == 2030
    report = registry.execute("dialogue_state", {})
    assert report.success and "1 billion" in report.output["text"]
    incident = registry.execute("investigate_incident", {"start": "2026-11-11T07:10:00+00:00",
                                                         "end": "2026-11-11T07:25:00+00:00"})
    assert incident.success and len(incident.output["failures"]) == 2
    catalogued = {tool for capability in capabilities.CATALOG for tool in capability.tools}
    assert {"ambition_evaluate", "dialogue_state", "investigate_incident"} <= catalogued


def test_a_complaint_recalls_memories_for_its_question_not_for_its_own_words(tmp_path):
    from app.agent.conversation import ConversationService
    from app.memory.database import MemoryDatabase
    from tests.test_conversation_topics import _Recording

    memory = MemoryDatabase(tmp_path / "m.db")
    memory.initialize()
    store = ConversationTopics(tmp_path / "topics.db")
    looked_up: list[str] = []
    chat = ConversationService(_Recording(), memory, "s")
    chat.topic_scope = store.scope_history
    chat.context_retriever = lambda query, **kwargs: looked_up.append(query) or []
    chat.topic_decision = store.decide("s", MONEY, now=START)
    chat.reply(MONEY)
    chat.topic_decision = store.decide("s", SAME_REPLY, now=START + timedelta(minutes=1))
    chat.reply(SAME_REPLY)
    assert "same reply" not in looked_up[-1] and "money" in looked_up[-1]


def test_a_different_tip_is_asked_of_the_model_with_the_refused_one_not_searched(tmp_path):
    """Live chat 2026-11-11: "you already said that, give me a different one" about a one-sentence sleep tip went to
    a deep web search and came back as study excerpts. A different answer is the model's job; it is told what was
    refused and to keep the format."""
    rt = _runtime(tmp_path)
    rt.research = None  # no live-facts search configured in this runtime
    seen: list[str] = []

    def reply(text, **kwargs):
        seen.append(text)
        return "Keep the bedroom cool and dark." if len(seen) > 1 else "Keep the same sleep and wake time every day."

    rt.assistant._conversation_reply = reply
    rt.assistant.handle("Give me one tip to sleep better. One sentence.")
    second = rt.assistant.handle("you already said that, give me a different one").display_response
    assert rt.research_jobs.started == []  # no web search for a different tip
    assert "Keep the bedroom cool and dark." in second
    assert "Keep the same sleep and wake time every day." in seen[-1] and "genuinely different" in seen[-1]
    assert "Keep any length or format" in seen[-1]
