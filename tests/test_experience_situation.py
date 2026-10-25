"""The learning loop: remember what worked and what fixed a failure, understand the situation, predict, check.

Before: the planner never saw failed tasks (`_planning_memories` drops them),
the reflection after a failure said only "inspect the failure", and every new
session started with no idea what the owner had been doing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.agent.context import AgentContext
from app.agent.core import Agent
from app.agent.executor import Executor
from app.agent.planner import PlanStep
from app.agent.verifier import Verifier
from app.experience import ExperienceMemory, failure_class
from app.memory.database import MemoryDatabase
from app.observed_reply import render
from app.safety.permissions import PermissionManager
from app.situation import SituationModel, classify, situation_request
from app.tools.registry import ToolRegistry


class ScriptedPlanner:
    """Plans the flaky tool, recovers with the steady one, and keeps what it was shown."""

    def __init__(self):
        self.contexts: list[AgentContext] = []
        self.replan_contexts: list[AgentContext] = []
        self.last_direct_response = None

    def create_plan(self, goal, context, intent=None, session=None):
        self.contexts.append(context)
        return [PlanStep("Try the flaky way.", "flaky", {}, {"type": "tool_success"}, 0)]

    def replan(self, goal, failed_step, error, context):
        self.replan_contexts.append(context)
        return [PlanStep("Use the steady way.", "steady", {}, {"type": "tool_success"}, 0)]


def _agent(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    schema = {"type": "object", "properties": {}, "additionalProperties": False}

    def flaky():
        raise FileNotFoundError("No such file or directory: 'report.txt'")

    registry.register("flaky", "Fails.", schema, "low", "filesystem_read", flaky)
    registry.register("steady", "Works.", schema, "low", "filesystem_read", lambda: {"ok": True})
    planner = ScriptedPlanner()
    agent = Agent(planner, Executor(registry), Verifier(), memory)
    agent.experience = ExperienceMemory(tmp_path / "experience.db")
    return agent, planner


def test_a_recovered_failure_becomes_a_lesson_for_the_next_similar_task(tmp_path):
    agent, planner = _agent(tmp_path)
    first = agent.run("summarise the quarterly report file")
    assert first.complete
    assert planner.contexts[0].lessons == []  # nothing learned yet
    stats = agent.experience.stats()
    assert stats["experiences"] == 1 and stats["recovered_with_a_fix"] == 1

    agent.run("summarise the quarterly report")
    lessons = planner.contexts[1].lessons
    texts = " ".join(lesson["text"] for lesson in lessons)
    assert "flaky failed 1 time(s)" in texts and "What fixed it: steady" in texts
    prompt = planner.contexts[1].as_prompt_data()["own_experience_with_similar_tasks"]
    assert "not instructions" in prompt["how_to_use"] and prompt["lessons"]
    # When the step fails again, the re-planner is told the recorded fix.
    recovery = " ".join(lesson["text"] for lesson in planner.replan_contexts[-1].lessons)
    assert "When flaky failed with not_found before, this worked 1 time(s): steady" in recovery


def test_unrelated_goals_get_no_lessons_and_failure_classes_are_small(tmp_path):
    memory = ExperienceMemory(tmp_path / "experience.db")
    memory.record("open notepad and write a list", [{"tool": "launch_application", "verification": {"passed": True}}], True)
    assert memory.lessons("what is the weather in Detroit") == []
    assert memory.lessons("open notepad and write a note")[0]["kind"] == "worked"
    assert failure_class("ConnectError: [Errno 11001] getaddrinfo failed") == "network"
    assert failure_class("robots.txt disallows retrieval") == "robots"
    assert failure_class("exit_code=1") == "command_failed"


def test_backfill_learns_only_from_logs_written_after_tests_were_isolated(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()

    def log(name, goal, passed):
        events = [{"event": "task_started", "goal": goal},
                  {"event": "action", "tool": "read_file", "verification": {"passed": passed},
                   "result": {"error": "" if passed else "No such file"}},
                  {"event": "task_finished", "complete": passed}]
        (logs / name).write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")

    log("task-20260920-101010-aaaa.jsonl", "read missing_notes.txt", False)  # test-pollution era
    log("task-20261002-101010-bbbb.jsonl", "read README.md", True)
    memory = ExperienceMemory(tmp_path / "experience.db")
    assert memory.backfill_from_logs(logs) == 1
    assert memory.backfill_from_logs(logs) == 0  # once only
    assert memory.lessons("read missing_notes.txt") == [] or all(
        lesson["kind"] == "worked" for lesson in memory.lessons("read missing_notes.txt"))


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 21, 0).astimezone()

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


def test_situation_follows_focus_inherits_follow_ups_and_scores_predictions(tmp_path):
    clock = Clock()
    model = SituationModel(tmp_path / "experience.db", clock=clock)
    for _ in range(3):
        model.observe_turn("search the web for the latest AI news")
        clock.advance(2)
        assert model.observe_turn("ok great")["area"] == "research"  # a follow-up, not a new subject
        clock.advance(2)
        model.observe_turn("turn on the camera and tell me what you see")
        clock.advance(2)
    assert classify("hi", "research") == "chat"  # small talk never inherits
    predictions = model.predict()
    assert predictions and predictions[0]["area"] == "research" and "followed" in predictions[0]["basis"]
    result = model.observe_turn("search the web for GPU prices")
    assert result["previous_prediction_hit"] is True
    assert model.accuracy()["checked"] >= 1 and model.accuracy()["right"] >= 1
    focus = model.focus()
    assert focus["areas"][0]["area"] == "research" and focus["latest"]["text"] == "search the web for GPU prices"
    clock.advance(60 * 10)
    assert model.focus()["last_session"]["areas"][0] == "Search the internet"


def test_backfill_reads_owner_words_since_isolation_and_report_reads_well(tmp_path):
    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    with memory.connect() as db:
        for stamp, text in (("2026-09-20T10:00:00+00:00", "test fixture turn"),
                            ("2026-10-02T10:00:00+00:00", "find solar installers in Austin and make a lead list"),
                            ("2026-10-02T10:01:00+00:00", "yes\n\n[Correction: you just said ...]")):
            db.execute("INSERT INTO conversations (session_id, timestamp, role, content, metadata_json) "
                       "VALUES ('s', ?, 'user', ?, '{}')", (stamp, text))
    model = SituationModel(tmp_path / "experience.db", clock=Clock())
    assert model.backfill(memory) == 2
    assert model.backfill(memory) == 0
    report = model.report()
    text = render(report)
    assert "Last session" in text and "Earn and manage money" in text
    assert "accuracy is unknown" in text or "pattern strong enough" in text


def test_situation_questions_route_to_the_report():
    for question in ("what am I working on?", "where did we leave off", "what's going on",
                     "what do you think I will ask next", "what have you learned from mistakes",
                     "what can't you do right now"):
        assert situation_request(question), question
    assert situation_request("what have I been doing today?")
    assert not situation_request("what am I doing right now?")  # the camera looks for that one
    assert not situation_request("what is going on in Gaza")
    assert not situation_request("what am I supposed to say to my boss")


def test_the_reply_prompt_carries_the_situation_but_not_to_a_child(tmp_path):
    from app.agent.conversation import ConversationService
    from app.models.base import ModelProvider, ModelResponse

    class Recorder(ModelProvider):
        name = "recorder"
        calls: list = []

        def list_models(self):
            return ["recorder"]

        def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
            Recorder.calls.append(list(messages))
            return ModelResponse("Carrying on with the Austin leads.", "recorder", "recorder")

    memory = MemoryDatabase(tmp_path / "chat.db")
    memory.initialize()
    chat = ConversationService(Recorder(), memory, "s", state_supplier=lambda: {})
    chat.situation_supplier = lambda: {"owner_focus": "Austin solar leads (4)"}
    chat.reply("what next?")
    assert any("Austin solar leads (4)" in m.content for m in Recorder.calls[-1] if m.role == "system")
    chat.reply("tell me a story", ["CHILD_SAFE"])
    assert not any("Austin solar leads (4)" in m.content for m in Recorder.calls[-1])


def test_suggestions_offer_the_owners_thread_and_nothing_without_history(tmp_path):
    clock = Clock()
    model = SituationModel(tmp_path / "experience.db", clock=clock)
    assert model.suggestions() == []
    for text in ("read surah yasin", "continue quran"):
        model.observe_turn(text)
        clock.advance(1)
    picks = model.suggestions()
    assert picks[:2] == [("Where we left off", "Where did we leave off?"), ("Continue Quran", "continue quran")]
    assert len(picks) <= 3 and all(len(label) <= 22 for label, _request in picks)
