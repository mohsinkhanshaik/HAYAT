"""The memory network changes with use: binding, strengthening, fading, consolidation (app/memory/plasticity.py).

Before (owner's memory on 2026-11-08): 863 memories, 339 links, all ``related``, every ``use_count`` 0; no task
memory had a link; a task's reflection was not connected to the task; nothing learned from being useful.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent.core import Agent
from app.agent.executor import Executor
from app.agent.planner import PlanStep
from app.agent.verifier import Verifier
from app.memory import plasticity
from app.memory.database import MemoryDatabase
from app.networking_privacy import PRIVATE_TEXT
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


@pytest.fixture
def memory(tmp_path):
    database = MemoryDatabase(tmp_path / "plastic.db")
    database.initialize()
    return database


def _links(memory, relation=None):
    with memory.connect() as db:
        rows = db.execute("SELECT * FROM memory_links" + (" WHERE relation=?" if relation else ""),
                          (relation,) if relation else ()).fetchall()
    return [dict(row) for row in rows]


def _metadata(memory, memory_id):
    with memory.connect() as db:
        return json.loads(db.execute("SELECT metadata_json FROM memories WHERE id=?", (memory_id,)).fetchone()[0])


def _episode(memory, goal, task_id, session="s1", complete=True, when=None):
    memory_id = memory.store_memory("episodic", f"Task {'succeeded' if complete else 'failed'}. Verified 1 step.\n"
                                    f"Goal: {goal}.", 0.8, "task", task_id, {"goal": goal, "complete": complete},
                                    session_id=session)
    if when is not None:
        with memory.connect() as db:
            db.execute("UPDATE memories SET timestamp=? WHERE id=?", (when.isoformat(), memory_id))
    return memory_id


def _reflection(memory, task_id, failed=""):
    body = {"what_worked": [] if failed else ["steady"], "what_failed": [failed] if failed else [],
            "next_time": f"Avoid {failed}" if failed else "Reuse the verified steps."}
    return memory.store_memory("reflection", json.dumps(body), 0.8, "reflection", task_id, {"succeeded": not failed})


# --------------------------------------------------------------------------- binding


def test_a_finished_task_is_bound_to_its_lesson_and_to_the_task_before_it(memory):
    start = datetime.now(UTC) - timedelta(minutes=30)
    first = _episode(memory, "open notepad", "t1", when=start)
    second = _episode(memory, "write a note in notepad", "t2", when=start + timedelta(minutes=2))
    lesson = _reflection(memory, "t2", failed="the window was not focused")
    made = plasticity.bind_episode(memory, "t2")
    assert made == {"reflects_on": 1, "followed_by": 1}
    assert {(row["source_id"], row["target_id"]) for row in _links(memory, "reflects_on")} == {(lesson, second)}
    assert {(row["source_id"], row["target_id"]) for row in _links(memory, "followed_by")} == {(first, second)}
    # Idempotent: binding again adds nothing.
    assert plasticity.bind_episode(memory, "t2") == {"reflects_on": 0, "followed_by": 0}


def test_tasks_in_other_sessions_or_hours_apart_are_not_a_sequence(memory):
    start = datetime.now(UTC) - timedelta(hours=10)
    _episode(memory, "open notepad", "a", session="s1", when=start)
    _episode(memory, "open notepad", "b", session="s2", when=start + timedelta(minutes=1))
    _episode(memory, "close notepad", "c", session="s2", when=start + timedelta(hours=5))
    for task in ("a", "b", "c"):
        plasticity.bind_episode(memory, task)
    assert _links(memory, "followed_by") == []


def test_private_and_untrusted_memories_are_never_linked_automatically(memory):
    task = memory.store_memory("episodic", PRIVATE_TEXT, 0.8, "task", "p1", {"goal": "x"}, session_id="s")
    _reflection(memory, "p1", failed="boom")
    assert plasticity.bind_episode(memory, "p1")["reflects_on"] == 0
    outside = memory.store_memory("semantic", "Close notepad page text", 0.9, "untrusted_external_content")
    mine = memory.store_memory("semantic", "Close notepad steps", 0.9, "user")
    plasticity.reinforce(memory, [outside, mine], True)
    assert _links(memory, "co_recalled") == []
    assert task  # stored, only not linked


# --------------------------------------------------------------------------- strengthening


def test_memories_recalled_together_for_work_that_succeeded_wire_together(memory):
    left = memory.store_memory("semantic", "The quarterly report lives in D:/Reports", 0.6, "user")
    right = memory.store_memory("semantic", "Quarterly numbers are due on Fridays", 0.6, "user")
    first = plasticity.reinforce(memory, [left, right], True, reason="task succeeded")
    assert first["created"] == 1 and first["memories"] == 2
    [link] = _links(memory, "co_recalled")
    assert link["weight"] == pytest.approx(plasticity.NEW_LINK_WEIGHT) and link["use_count"] == 1
    plasticity.reinforce(memory, [right, left], True)
    [link] = _links(memory, "co_recalled")
    assert link["weight"] == pytest.approx(0.3 + 0.2 * 0.7) and link["use_count"] == 2
    assert _metadata(memory, left)["successful_uses"] == 2


def test_failure_weakens_only_what_was_learned_from_use_and_counts_against_the_memory(memory):
    left = memory.store_memory("semantic", "Old printer address 10.0.0.9", 0.6, "user")
    right = memory.store_memory("semantic", "Printer queue is called office", 0.6, "user")
    memory.link_memories(left, right, "related", 0.8)
    plasticity.reinforce(memory, [left, right], True)  # strengthens related; no co_recalled since one existed
    strengthened = _links(memory, "related")[0]["weight"]
    assert strengthened == pytest.approx(0.8 + 0.2 * 0.2)
    with memory.connect() as db:
        db.execute("INSERT INTO memory_links VALUES(?,?,'co_recalled',0.5,'{}',?,?,1)",
                   (left, right, datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()))
    result = plasticity.reinforce(memory, [left, right], False, reason="owner said the reply was wrong")
    assert result["weakened"] == 1
    assert _links(memory, "co_recalled")[0]["weight"] == pytest.approx(0.4)
    assert _links(memory, "related")[0]["weight"] == pytest.approx(strengthened)  # a fact link is not unlearned
    assert _metadata(memory, left)["failed_uses"] == 1


def test_recall_ranks_a_memory_that_kept_helping_above_one_that_kept_failing(memory):
    helped = memory.store_memory("semantic", "Backup drive letter is E for nightly backup", 0.6, "user")
    misled = memory.store_memory("semantic", "Backup drive letter is F for nightly backup", 0.6, "user")
    for _ in range(4):
        plasticity.reinforce(memory, [helped], True)
        plasticity.reinforce(memory, [misled], False)
    ranked = [row["id"] for row in memory.retrieve("nightly backup drive letter", limit=2)]
    assert ranked == [helped, misled]


# --------------------------------------------------------------------------- fading


def _age_links(memory, days):
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with memory.connect() as db:
        db.execute("UPDATE memory_links SET last_reinforced_at=?", (stamp,))


def test_unused_associations_fade_to_half_after_the_half_life_but_lessons_do_not(memory):
    a = memory.store_memory("semantic", "alpha fact", 0.6, "user")
    b = memory.store_memory("semantic", "beta fact", 0.6, "user")
    c = memory.store_memory("reflection", "lesson about alpha", 0.6, "reflection")
    memory.link_memories(a, b, "related", 0.8)
    memory.link_memories(c, a, "reflects_on", 0.9)
    fresh = {item["memory_id"]: item["path_weight"] for item in memory.associations(a, max_depth=1)}
    assert fresh[b] == pytest.approx(0.8, abs=1e-6) and fresh[c] == pytest.approx(0.9)
    _age_links(memory, plasticity.HALF_LIFE_DAYS)
    aged = {item["memory_id"]: item["path_weight"] for item in memory.associations(a, max_depth=1)}
    assert aged[b] == pytest.approx(0.4, rel=1e-3)
    assert aged[c] == pytest.approx(0.9)  # structural: never fades
    # Using it again brings it back to full strength (and a little more).
    plasticity.reinforce(memory, [a, b], True)
    back = {item["memory_id"]: item["path_weight"] for item in memory.associations(a, max_depth=1)}
    assert back[b] == pytest.approx(0.84, abs=1e-4)


# --------------------------------------------------------------------------- consolidation


def test_consolidation_links_tasks_with_the_same_purpose_not_shared_boilerplate(memory):
    start = datetime.now(UTC) - timedelta(days=3)
    ids = {goal: _episode(memory, goal, f"t{index}", session=f"s{index}", when=start + timedelta(hours=index * 3))
           for index, goal in enumerate((
               "Close notepad", "close notepad without saving", "look and understand how industries are interlinked",
               "look again at me", "see through the camera", "see me right now with the camera",
               "create an image of an alien"))}
    report = plasticity.consolidate(memory)
    assert report["complete"] and report["related"] >= 2
    pairs = {frozenset((row["source_id"], row["target_id"])) for row in _links(memory, "related")}
    assert frozenset((ids["Close notepad"], ids["close notepad without saving"])) in pairs
    assert frozenset((ids["see through the camera"], ids["see me right now with the camera"])) in pairs
    assert frozenset((ids["look and understand how industries are interlinked"], ids["look again at me"])) not in pairs
    evidence = json.loads(_links(memory, "related")[0]["evidence_json"])
    assert evidence["method"] == "goal_consolidation"
    # A second pass finds nothing new and examines nothing again.
    again = plasticity.consolidate(memory)
    assert again["related"] == 0 and again["examined"] == 0


def test_consolidation_binds_past_tasks_to_their_lessons(memory):
    start = datetime.now(UTC) - timedelta(days=1)
    episode = _episode(memory, "print the invoice", "old-task", when=start)
    lesson = _reflection(memory, "old-task", failed="printer offline")
    report = plasticity.consolidate(memory)
    assert report["reflects_on"] == 1
    assert {(row["source_id"], row["target_id"]) for row in _links(memory, "reflects_on")} == {(lesson, episode)}


def test_recalling_a_past_task_now_brings_back_what_went_wrong_with_it(memory):
    for index in range(30):  # unrelated history, so the lesson is not found merely because little else exists
        _episode(memory, f"routine chore number {index}", f"r{index}", session=f"x{index}")
    episode = _episode(memory, "export the payroll spreadsheet to PDF", "payroll")
    lesson = _reflection(memory, "payroll", failed="write_pdf refused a path outside the workspace")
    before = [row["id"] for row in memory.retrieve("export the payroll spreadsheet to PDF", limit=5)]
    assert episode in before
    plasticity.consolidate(memory)
    after = {row["id"]: row for row in memory.retrieve("export the payroll spreadsheet to PDF", limit=5)}
    assert episode in after and lesson in after
    assert after[lesson]["retrieval"].get("relation") in (None, "reflects_on")


def test_consolidation_respects_its_budget(memory):
    for index in range(12):
        _episode(memory, f"rename holiday photo {index}", f"h{index}", session=f"h{index}")
    report = plasticity.consolidate(memory, max_memories=5)
    assert not report["complete"] and report["examined"] == 5
    rest = plasticity.consolidate(memory, max_memories=50)
    assert rest["complete"] and rest["examined"] == 7


# --------------------------------------------------------------------------- the real loops


class _Planner:
    last_direct_response = None

    def __init__(self, tool):
        self.tool = tool

    def create_plan(self, goal, context, intent=None, session=None):
        return [PlanStep("Do it.", self.tool, {}, {"type": "tool_success"}, 0)]

    def replan(self, goal, failed_step, error, context):
        return []


def _agent(tmp_path, memory, tool):
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    registry.register("steady", "Works.", schema, "low", "filesystem_read", lambda: {"ok": True})

    def broken():
        raise RuntimeError("disk not ready")

    registry.register("broken", "Fails.", schema, "low", "filesystem_read", broken)
    return Agent(_Planner(tool), Executor(registry), Verifier(), memory)


def test_a_real_task_binds_its_lesson_and_teaches_the_memories_it_was_planned_from(tmp_path, memory):
    note = memory.store_memory("semantic", "Weekly backup report goes to the shared drive", 0.9, "user")
    hint = memory.store_memory("semantic", "Weekly backup report must include the error count", 0.9, "user")
    session = SimpleNamespace(session_id="live-session")
    outcome = _agent(tmp_path, memory, "steady").run("write the weekly backup report", session=session)
    assert outcome.complete
    assert len(_links(memory, "reflects_on")) == 1
    assert _metadata(memory, note).get("successful_uses") == 1 and _metadata(memory, hint).get("successful_uses") == 1
    failed = _agent(tmp_path, memory, "broken").run("write the weekly backup report again", session=session)
    assert not failed.complete
    assert _metadata(memory, note).get("failed_uses") == 1
    assert len(_links(memory, "followed_by")) == 1  # the second task followed the first in this session


def test_the_owners_verdict_on_a_reply_teaches_the_memories_behind_it(memory):
    from app.owner_feedback import record_from_message

    left = memory.store_memory("semantic", "Mohsin's sister's birthday is 12 May", 0.9, "user")
    right = memory.store_memory("semantic", "Mohsin wants birthday reminders a week early", 0.9, "user")
    memory.record_message("s", "user", "when should you remind me about my sister's birthday?")
    memory.record_message("s", "assistant", "On 5 May, a week before 12 May.", metadata={"recalled_memory_ids": [left, right]})
    memory.record_message("s", "user", "that's right, thanks")
    assert record_from_message(memory, "s", "that's right, thanks") is not None
    assert _metadata(memory, left)["successful_uses"] == 1
    assert len(_links(memory, "co_recalled")) == 1


def test_a_reply_keeps_the_ids_of_the_memories_it_was_built_from(tmp_path, memory):
    from app.agent.conversation import ConversationService
    from app.models.base import ModelProvider, ModelResponse

    class Model(ModelProvider):
        name = "m"

        def list_models(self):
            return ["m"]

        def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
            return ModelResponse("Your favourite tea is jasmine.", "m", "m")

    tea = memory.store_memory("semantic", "Mohsin's favourite tea is jasmine tea", 0.9, "user")
    chat = ConversationService(Model(), memory, "tea-session")
    chat.reply("what is my favourite tea?")
    last = memory.conversation_history("tea-session")[-1]
    assert last["role"] == "assistant" and tea in last["metadata"].get("recalled_memory_ids", [])


def test_recalled_ids_come_only_from_the_memory_store():
    items = [{"id": 4, "memory_type": "semantic"}, {"object_type": "memory:episodic", "source_id": "9"},
             {"object_type": "document", "id": 77, "source_id": "a.txt"}, {"id": 4, "memory_type": "semantic"}]
    assert plasticity.recalled_memory_ids(items) == [4, 9]


def test_the_network_report_counts_what_is_really_there(tmp_path, memory):
    from app.tools.memory import register_memory_tools

    episode = _episode(memory, "print the invoice", "inv")
    _reflection(memory, "inv", failed="printer offline")
    plasticity.bind_episode(memory, "inv")
    alone = memory.store_memory("semantic", "unrelated fact", 0.5, "user")
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    register_memory_tools(registry, memory)
    result = registry.execute("memory_network", {})
    assert result.success
    report = result.output
    assert report["memories"] == 3 and report["links"] == 1
    assert report["links_by_kind"]["reflects_on"]["links"] == 1
    assert report["connected_memories"] == 2 and report["unconnected_memories"] == 1
    assert "lesson to its task 1" in report["summary"] and "3 memories, 1 links" in report["summary"]
    assert episode and alone


def test_the_planner_sees_what_failed_last_time_on_a_task_like_this(tmp_path, memory):
    episode = _episode(memory, "export the payroll spreadsheet to PDF", "pay1", complete=False)
    _reflection(memory, "pay1", failed="write_pdf refused a path outside the workspace")
    plasticity.bind_episode(memory, "pay1")
    agent = _agent(tmp_path, memory, "steady")
    recalled = agent._planning_memories("export the payroll spreadsheet to PDF")
    # An incomplete episode is left out of planning, so record a finished one for the same purpose too.
    assert all(item["id"] != episode for item in recalled)
    done = _episode(memory, "export the payroll spreadsheet to PDF", "pay2")
    _reflection(memory, "pay2", failed="the first attempt used a network drive")
    plasticity.bind_episode(memory, "pay2")
    recalled = {item["id"]: item for item in agent._planning_memories("export the payroll spreadsheet to PDF")}
    assert done in recalled
    assert recalled[done]["lesson"].startswith("Failed before: the first attempt used a network drive")
    assert "Next time: Avoid the first attempt used a network drive" in recalled[done]["lesson"]


def test_the_memory_network_is_a_brain_part_with_its_own_check(memory):
    from app import brain_components as bc

    network = plasticity.MemoryNetwork(memory)
    _episode(memory, "print the invoice", "inv")
    _reflection(memory, "inv", failed="printer offline")
    done = network.consolidate()
    assert done["reflects_on"] == 1
    runtime = SimpleNamespace(memory_network=network)
    [part] = [item for item in bc.service_components(["memory_network"]) if item.component_id == "svc:memory_network"]
    text = bc.check(part, runtime)
    assert "memories: 2" in text and "links: 1" in text and "reflects_on 1" in text and "1 lessons bound" in text
