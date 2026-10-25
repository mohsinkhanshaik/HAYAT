from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.agent.core import Agent, TaskOutcome
from app.agent.executor import Executor
from app.agent.planner import Planner
from app.agent.verifier import Verifier
from app.assistant import AssistantService
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.learning.guardian import ChildGuardian
from app.memory.database import MemoryDatabase
from app.safety.permissions import PermissionManager
from app.session import SessionState
from app.tools.registry import ToolRegistry
from app.modes import ModeManager


class Conversation:
    def reply(self, text):
        return "Conversational answer."


class Manager:
    def __init__(self):
        self.calls = []

    def route(self, goal, **kwargs):
        self.calls.append((goal, kwargs))
        return TaskOutcome("task-1", goal, True, "complete", [], [])


class Storage:
    def status(self): return {"used_bytes": 0, "maximum_total_bytes": 1, "remaining_budget_bytes": 1, "categories": {}}
    def clean(self): return {"cleaned": True, "bytes_reclaimed": 0, "files_removed": 0, "expired_cache_rows_removed": 0}
    def largest(self): return {"largest": []}
    def format_result(self, result, mode): return "Storage is healthy."


def runtime(tmp_path):
    memory = MemoryDatabase(tmp_path / "assistant.db"); memory.initialize()
    result = SimpleNamespace(
        memory=memory, session=SessionState("shared-session"), intent_router=IntentRouter(),
        conversation=Conversation(), manager=Manager(), storage=Storage(), voice=None,
        agent=SimpleNamespace(cancel_current=lambda: False),
        modes=ModeManager(tmp_path / "modes.json"),
        state=SimpleNamespace(set_activity=lambda value: None, answer=lambda value: "grounded state"),
        projects=SimpleNamespace(use=lambda name: SimpleNamespace(name=name, root=str(tmp_path))),
        learning=SimpleNamespace(handle=lambda text, session, **_kwargs: "learning response"),
        training=SimpleNamespace(add=lambda *args, **kwargs: None),
    )
    result.assistant = AssistantService(result)
    result.status_text = lambda: "AI Ready"
    return result


def test_project_review_runs_scan_and_reports_its_actual_scope(tmp_path):
    rt = runtime(tmp_path)
    intent = rt.intent_router.route("i made changes again in u check them", rt.session)
    plan = Planner(SimpleNamespace(names=lambda: ["code_scan"])).create_plan(intent.goal, intent=intent)
    assert [step.tool for step in plan] == ["code_scan"]
    outcome = SimpleNamespace(complete=True, needs_user_input=False, history=[{
        "tool": "code_scan", "result": {"success": True, "output": {
            "AI": {"project": "AI", "files": 12, "changed": 2, "issue_count": 1,
                   "issues": [{"path": "app/main.py", "line": 7, "message": "invalid syntax"}],
                   "error": ""},
        }},
    }])
    reply = rt.assistant._outcome_response(intent, outcome)
    assert "app/main.py:7" in reply
    assert "12 source files" in reply
    assert "does not verify behavior" in reply


def test_what_can_you_do_uses_real_tool_overview(tmp_path):
    """Answered in plain words from the capability catalog, counted from the live registry, never by a model."""
    from app import capabilities

    rt = runtime(tmp_path)
    rt.registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    reply = rt.assistant.handle("what can you do")
    assert reply.intent.intent == IntentKind.CONTROL
    assert "(0 tools behind it)" in reply.display_response
    for capability in capabilities.CATALOG:
        assert capability.title in reply.display_response


def test_greeting_and_feedback_never_reach_planner_or_tools(tmp_path):
    rt = runtime(tmp_path)
    greeting = rt.assistant.handle("HI")
    assert greeting.intent.intent == IntentKind.CONVERSATION
    # The exact words vary now -- a greeting that is identical every time
    # reads as a card rather than a person. What matters here is unchanged:
    # a hello is answered directly and never reaches the planner or a tool.
    assert greeting.display_response.strip()
    assert greeting.display_response != rt.assistant.handle("HI").display_response
    assert rt.manager.calls == []
    rt.session.last_task = TaskOutcome("old", "Write a note in Notepad", True, "wrong", [{"tool": "launch_application"}], [{"tool": "launch_application", "verification": {"passed": True}, "result": {"success": True, "output": {"running": True}}}])
    repair = rt.assistant.handle("That didn't work. You only opened Notepad.")
    assert repair.intent.intent == IntentKind.DIAGNOSTIC
    assert "only launched" in repair.display_response
    assert rt.manager.calls == []
    learned = rt.memory.retrieve("Notepad user correction", "reflection")
    assert learned and learned[0]["source"] == "user_correction"


def test_try_again_uses_the_previous_image_prompt_and_settings(tmp_path):
    rt = runtime(tmp_path)
    rt.session.last_task = TaskOutcome(
        "old", "a portrait with red light", True, "image made", [],
        [{"tool": "generate_image", "arguments": {"prompt": "a portrait with red light",
                                                  "quality": "ultra", "style": "cinematic"}}],
    )
    rt.assistant.handle("try again")
    assert len(rt.manager.calls) == 1
    goal, details = rt.manager.calls[0]
    assert goal.startswith("Generate an image of a portrait with red light")
    assert details["intent"].entities["modality"] == "image"
    assert details["intent"].entities["quality"] == "ultra"
    assert details["intent"].entities["style"] == "cinematic"


def test_try_again_uses_the_previous_video_prompt(tmp_path):
    rt = runtime(tmp_path)
    rt.session.last_task = TaskOutcome(
        "old", "a red balloon rising", True, "clip made", [],
        [{"tool": "generate_video", "arguments": {"prompt": "a red balloon rising",
                                                  "quality": "fast"}}],
    )
    rt.assistant.handle("try again")
    goal, details = rt.manager.calls[0]
    assert goal.startswith("Generate a video of a red balloon rising")
    assert details["intent"].entities["modality"] == "video"
    assert details["intent"].entities["quality"] == "fast"


def test_try_again_without_a_previous_task_asks_for_the_action(tmp_path):
    reply = runtime(tmp_path).assistant.handle("try again")
    assert "earlier task" in reply.display_response
    assert "what you'd like me to do" in reply.display_response


def test_voice_control_routes_without_system_information_tool(tmp_path):
    rt = runtime(tmp_path)
    fake_voice = SimpleNamespace(
        status=lambda: {"enabled": True}, start_background=lambda: (_ for _ in ()).throw(AssertionError("already active")),
        muted=False, mode="push_to_talk", set_mode=lambda value: None,
        tts=SimpleNamespace(stop=lambda: None), approvals=SimpleNamespace(cancel=lambda: None),
    )
    rt.voice = fake_voice
    reply = rt.assistant.handle("Voice mode activate")
    assert reply.intent.control == "VOICE_ON"
    assert reply.display_response == "Voice mode is active."
    assert rt.manager.calls == []


def test_question_effort_escalates_only_when_reasoning_is_requested():
    service = AssistantService.__new__(AssistantService)
    assert service._question_model_capability("Who created Python?") == "conversation"
    assert service._question_model_capability(
        "Explain in detail how Python memory management works and why reference cycles matter."
    ) == "reasoning"


def test_child_and_privacy_modes_do_not_feed_background_web_learning(tmp_path):
    rt = runtime(tmp_path)
    observed = []
    rt.learner = SimpleNamespace(observe_request=lambda *args: observed.append(args))
    rt.authority = SimpleNamespace(privacy_mode=False)
    rt.learning.guardian = ChildGuardian(tmp_path / "guardian")
    rt.learning.guardian.enable("child")
    rt.assistant.handle("Teach me why fractions work")
    assert observed == []

    rt.learning.guardian.disable()
    rt.authority.privacy_mode = True
    rt.assistant.handle("Teach me how GPU topology affects NCCL")
    assert observed == []


def test_agent_completes_notepad_goal_only_with_read_back_evidence(tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    registry = ToolRegistry(permissions)
    obj = {"type": "object", "additionalProperties": True}
    registry.register("launch_application", "launch", obj, "low", "x", lambda application: {"application": application, "running": True, "window": {"visible": True, "handle": 11}})
    registry.register("notepad_edit", "edit", obj, "low", "x", lambda text, append_line=False, handle=None: {"application": "notepad", "focused": True, "text": text, "text_present": True, "window": {"visible": True, "handle": 11}})
    memory = MemoryDatabase(tmp_path / "agent.db"); memory.initialize()
    planner = Planner(registry)
    agent = Agent(planner, Executor(registry), Verifier(), memory)
    intent = IntentRouter().route("Write a note in Notepad saying: Hello from my AI.")
    outcome = agent.run(intent.goal, intent, SessionState())
    assert outcome.complete
    assert outcome.goal_verification["passed"]
    assert [event["tool"] for event in outcome.history] == ["launch_application", "notepad_edit"]


def test_agent_rejects_tool_success_when_observed_text_is_wrong(tmp_path):
    permissions = PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path)
    registry = ToolRegistry(permissions)
    obj = {"type": "object", "additionalProperties": True}
    registry.register("launch_application", "launch", obj, "low", "x", lambda application: {"application": application, "running": True, "window": {"visible": True, "handle": 11}})
    registry.register("notepad_edit", "edit", obj, "low", "x", lambda **kwargs: {"application": "notepad", "focused": True, "text": "wrong", "text_present": False, "window": {"visible": True, "handle": 11}})
    memory = MemoryDatabase(tmp_path / "agent-fail.db"); memory.initialize()
    agent = Agent(Planner(registry), Executor(registry), Verifier(), memory)
    intent = IntentRouter().route("Write a note in Notepad saying: Hello from my AI.")
    outcome = agent.run(intent.goal, intent, SessionState())
    assert not outcome.complete
    assert not outcome.goal_verification["passed"]


def test_background_learning_pauses_for_a_turn_and_resumes_afterwards():
    """Learning stands aside for one answer, then comes back by itself.

    Before this the two competed, and the only arbitration was a 120-second
    keyboard-idle rule -- so learning was suppressed for the whole time Mohsin
    was present rather than just while an answer was being produced.
    """
    from app.assistant import AssistantService

    class _Learner:
        def __init__(self):
            self.paused = False
            self.events = []

        def pause(self, reason="x"):
            self.paused = True
            self.events.append(("pause", reason))

        def resume(self):
            self.paused = False
            self.events.append(("resume", None))

    learner = _Learner()
    service = AssistantService.__new__(AssistantService)
    service.runtime = type("R", (), {"learner": learner})()

    with service._learner_stands_aside():
        assert learner.paused is True
    assert learner.paused is False
    assert [name for name, _ in learner.events] == ["pause", "resume"]

    # A turn that raises must still give the learner back. A learner left
    # paused by a failed turn would never return, which is the failure this
    # replaces rather than repeats.
    learner.events.clear()
    with pytest.raises(RuntimeError):
        with service._learner_stands_aside():
            raise RuntimeError("turn blew up")
    assert learner.paused is False
    assert [name for name, _ in learner.events] == ["pause", "resume"]


def test_an_already_paused_learner_is_not_resumed_by_a_turn_ending():
    """A turn returns the learner to how it found it, not to running."""
    from app.assistant import AssistantService

    class _Learner:
        def __init__(self):
            self.paused = True
        def pause(self, reason="x"):
            self.paused = True
        def resume(self):
            self.paused = False

    learner = _Learner()
    service = AssistantService.__new__(AssistantService)
    service.runtime = type("R", (), {"learner": learner})()
    with service._learner_stands_aside():
        pass
    assert learner.paused is True


def test_created_files_are_reported_by_path_not_a_bare_done():
    """Mohsin asked for a file and got "2 steps completed and verified: system

    status, write file" -- true but naming no path he could open. The reply now
    names what was written and where, and ignores the noise step."""
    from app.assistant import AssistantService

    outcome = SimpleNamespace(history=[
        {"tool": "system_status", "result": {"output": {"ok": True}},
         "verification": {"passed": True}},
        {"tool": "write_file",
         "result": {"output": {"path": "D:/AI/data/notes.txt", "bytes": 42, "exists": True}},
         "verification": {"passed": True}},
    ])
    reply = AssistantService._created_files_reply(outcome)
    assert reply == "Wrote file: D:/AI/data/notes.txt (42 bytes)"

    # A PDF plus a folder are listed together, named, with the page count.
    multi = SimpleNamespace(history=[
        {"tool": "create_folder", "result": {"output": {"path": "D:/AI/data/out", "exists": True}}},
        {"tool": "write_pdf",
         "result": {"output": {"path": "D:/AI/data/out/plan.pdf", "pages": 3, "exists": True}}},
    ])
    reply = AssistantService._created_files_reply(multi)
    assert "Created folder: D:/AI/data/out" in reply
    assert "Wrote PDF: D:/AI/data/out/plan.pdf (3 pages)" in reply

    # Nothing written -> no claim of a file.
    assert AssistantService._created_files_reply(SimpleNamespace(history=[])) is None


def test_a_phone_web_turn_feeds_the_same_learning_as_the_keyboard(tmp_path):
    """Learning is a property of the turn, not the door it came through. A turn

    marked source='web' -- the phone -- observes into the learner exactly like a
    desktop turn, and privacy mode still blocks it, whatever the door. (The
    minimal runtime cannot produce a full answer, but the learning hook fires
    before that, which is the property under test.)"""
    rt = runtime(tmp_path)
    rt.research = None  # no anticipation branch in this minimal runtime
    observed = []
    rt.learner = SimpleNamespace(observe_request=lambda *args: observed.append(args))
    rt.authority = SimpleNamespace(privacy_mode=False)

    try:
        rt.assistant.handle("what is a b-tree", source="web")
    except Exception:
        pass  # answering needs a fuller runtime; learning already happened
    assert observed, "a mobile/web turn must feed the learner like a desktop one"

    observed.clear()
    rt.authority.privacy_mode = True
    try:
        rt.assistant.handle("what is a b-plus tree", source="web")
    except Exception:
        pass
    assert observed == [], "privacy mode must block learning from any door"


def test_memory_recall_reads_in_plain_words_and_answers_the_question_about_memory(tmp_path):
    import json

    rt = runtime(tmp_path)
    rt.memory.store_memory("semantic", "My sister Aisha lives in Toronto", .9, "user")
    rt.memory.store_memory("reflection", json.dumps({"previous_request": "Name one primary colour.",
                                                     "owner_correction": "No, I meant a secondary colour.",
                                                     "scope": "local_conversation"}), .9, "chat_correction")
    from app.assistant import _memory_line
    lines = [_memory_line(row) for row in rt.memory.retrieve("colour sister", limit=5)]
    assert all(not line.startswith("{") for line in lines)
    assert any("You corrected me: “No, I meant a secondary colour.”" in line for line in lines)
    reply = rt.assistant.handle("Do you remember things I tell you between sessions?").display_response
    if "remember" in reply.lower():
        assert "{" not in reply


def test_a_standing_instruction_is_confirmed_applied_and_counted_down(tmp_path):
    """Live chat 2026-10-10: 'For the next two answers, reply with exactly one word.' was ignored."""
    rt = runtime(tmp_path)
    rt.research = SimpleNamespace(recall_verified=lambda *args, **kwargs: None)
    seen = []

    def reply(text, modes=None, capability="conversation"):
        seen.append(rt.conversation.standing_instruction)
        return "Yellow"

    rt.conversation.reply = reply
    confirmed = rt.assistant.handle("For the next two answers, reply with exactly one word.").display_response
    assert confirmed == "Okay — for the next 2 answers, I'll reply with exactly one word."
    assert seen == []  # confirmed without asking the model
    rt.assistant.handle("What colour is a ripe banana?")
    rt.assistant.handle("What is the opposite of hot?")
    rt.assistant.handle("What is a mutex?")
    assert seen == ["reply with exactly one word", "reply with exactly one word", ""]
    assert rt.session.standing_instruction == {}

    rt.assistant.handle("From now on, keep your answers short.")
    assert rt.session.standing_instruction["remaining"] is None
    assert rt.assistant.handle("back to normal").display_response == "Okay, back to normal answers."
    assert rt.session.standing_instruction == {}
