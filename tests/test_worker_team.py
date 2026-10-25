"""Temporary worker teams (app/agents/team.py): designed per goal, read-only, verified, reported, dissolved."""
from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from app.agents.team import VERIFIER, WORKER_TOOLS, WorkerTeam, wants_team
from app.tools.registry import ToolResult


class Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def names(self):
        return [*WORKER_TOOLS, "send_message", "submit_form", "write_file", "delete_file"]

    def get(self, name):
        return SimpleNamespace(description=f"{name} tool", model_schema=lambda: {"type": "function",
                                                                                 "function": {"name": name}})

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        return ToolResult(name, True, {"results": [{"title": "Acme AI careers", "url": "https://acme.ai/jobs"}]})


class Brain:
    """Scripted replies: a team design, then each worker searches once and reports."""

    def __init__(self, design: dict | str | None = None) -> None:
        self.design = design
        self.prompts: list[str] = []

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        last = messages[-1]["content"]
        self.prompts.append(last)
        if response_schema is not None:
            content = self.design if isinstance(self.design, str) else json.dumps(self.design or {})
            return SimpleNamespace(content=content, tool_calls=[])
        if tools and not any("Result of" in str(item.get("content")) for item in messages):
            first = tools[0]["function"]["name"]
            return SimpleNamespace(content="", tool_calls=[{"function": {"name": first,
                                                                         "arguments": {"query": "acme jobs"}}}])
        if "worked on" in last:
            return SimpleNamespace(content="Acme AI is hiring a platform engineer (https://acme.ai/jobs).",
                                   tool_calls=[])
        return SimpleNamespace(content="- Acme AI: platform engineer opening (https://acme.ai/jobs)", tool_calls=[])


DESIGN = {"workers": [
    {"role": "Research Agent", "mission": "Find AI infrastructure companies.", "tools": ["web_search", "send_message"]},
    {"role": "Job Search Agent", "mission": "Find their openings.", "tools": ["web_search"],
     "depends_on": ["Research Agent", "Nobody Agent"]},
    {"role": "Verification Agent", "mission": "my own checker", "tools": ["web_search"]},
    {"role": "Resume Matching Agent", "mission": "Match openings to the owner.",
     "tools": ["knowledge_search", "delete_file"], "depends_on": ["Job Search Agent"]},
]}


def test_a_team_is_used_for_many_items_plus_a_second_task_or_when_asked():
    assert wants_team("Research 20 AI infrastructure companies and find jobs suitable for me.")
    assert wants_team("Use a team of agents to compare GPU cloud providers")
    assert wants_team("find 10 logo design niches and rank them by demand")
    assert not wants_team("what is the capital of france")
    assert not wants_team("research nvidia")
    assert not wants_team("create a folder called test")


def test_a_designed_team_keeps_only_read_tools_known_roles_and_ends_with_verification(tmp_path):
    specs, planned_by = WorkerTeam(Brain(DESIGN), Registry(), root=tmp_path).plan("goal")
    assert planned_by == "model"
    assert [spec.role for spec in specs] == ["Research Agent", "Job Search Agent", "Resume Matching Agent", VERIFIER]
    tools = {tool for spec in specs for tool in spec.tools}
    assert not tools & {"send_message", "submit_form", "write_file", "delete_file"}
    assert specs[1].depends_on == ["Research Agent"]          # unknown roles dropped
    assert specs[-1].depends_on == ["Research Agent", "Job Search Agent", "Resume Matching Agent"]


def test_a_broken_design_falls_back_to_a_fixed_team(tmp_path):
    specs, planned_by = WorkerTeam(Brain("not json"), Registry(), root=tmp_path).plan(
        "Research 20 providers and compare options for me")
    assert planned_by == "fallback"
    assert specs[0].role == "Research Agent" and specs[-1].role == VERIFIER
    assert any(spec.role == "Profile Matching Agent" for spec in specs)


def test_a_team_runs_each_worker_with_its_tools_verifies_and_saves_a_report(tmp_path):
    registry = Registry()
    report = WorkerTeam(Brain(DESIGN), registry, root=tmp_path).run("Use a team to compare providers for me")
    assert report.finished and [result.role for result in report.results][-1] == VERIFIER
    assert all(result.ok for result in report.results)
    assert {name for name, _ in registry.calls} <= set(WORKER_TOOLS)
    assert report.results[0].sources == ["https://acme.ai/jobs"]
    assert "platform engineer" in report.answer
    saved = (tmp_path / "data" / "artifacts" / "teams" / f"{report.team_id}.md").read_text(encoding="utf-8")
    assert "## The workers" in saved and "### Resume Matching Agent" in saved
    assert report.report_path.endswith(".md")


def test_a_worker_cannot_use_a_tool_it_was_not_given(tmp_path):
    class Pushy(Brain):
        def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
            if tools is not None and not any("Result of" in str(item.get("content")) for item in messages):
                return SimpleNamespace(content="", tool_calls=[{"function": {"name": "send_message",
                                                                             "arguments": {"to": "x"}}}])
            return super().generate(messages, tools, temperature, response_schema, max_tokens)

    registry = Registry()
    report = WorkerTeam(Pushy(DESIGN), registry, root=tmp_path).run("goal")
    assert all(name != "send_message" for name, _ in registry.calls)
    assert report.tool_calls == 0


def test_a_background_team_reports_back_when_it_finishes(tmp_path):
    delivered: list[str] = []
    done = threading.Event()
    team = WorkerTeam(Brain(DESIGN), Registry(), root=tmp_path,
                      deliver=lambda report, text: (delivered.append(text), done.set()))
    report = team.start("Use a team to compare providers for me")
    started = team.started_text(report)
    assert "temporary team of 4 workers" in started and "**Job Search Agent**" in started
    assert done.wait(10)
    assert delivered[0].startswith("Team finished: 4 of 4 workers delivered")
    assert "Full team report:" in delivered[0]
    assert team.status()["recent"][0]["team_id"] == report.team_id


def test_a_worker_that_will_not_gather_evidence_is_nudged_then_marked_unverified(tmp_path):
    """The first live run: three workers answered from memory and the answer still said "verified"."""
    class Lazy(Brain):
        def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
            if response_schema is None and tools is not None:
                self.prompts.append(messages[-1]["content"])
                return SimpleNamespace(content="CoreWeave is hiring SREs (https://coreweave.com/careers)",
                                       tool_calls=[])
            return super().generate(messages, tools, temperature, response_schema, max_tokens)

    brain = Lazy(DESIGN)
    report = WorkerTeam(brain, Registry(), root=tmp_path).run("goal")
    assert sum("You have not used any tool yet" in prompt for prompt in brain.prompts) >= 2
    assert not any(result.ok for result in report.results)
    assert all("unverified" in result.note for result in report.results)
    final_prompt = next(prompt for prompt in brain.prompts if "worked on" in prompt)
    assert "NO EVIDENCE" in final_prompt and "Label everything else 'unverified'" in final_prompt


def test_failed_tool_attempts_cannot_count_as_team_evidence(tmp_path):
    class FailedRegistry(Registry):
        def execute(self, name, arguments):
            self.calls.append((name, arguments))
            return ToolResult(name, False, error='Lookup failed https://not-evidence.test')
    registry, brain = FailedRegistry(), Brain(DESIGN)
    report = WorkerTeam(brain, registry, root=tmp_path).run('goal')
    assert report.finished and not report.complete
    assert not any(result.ok for result in report.results)
    assert not any(result.successful_tools or result.sources for result in report.results)
    assert 'NO EVIDENCE' in next(prompt for prompt in brain.prompts if 'worked on' in prompt)


def test_tool_batch_respects_remaining_team_budget(tmp_path, monkeypatch):
    from app.agents.capacity import TeamCapacity
    monkeypatch.setattr(TeamCapacity, 'team_tool_calls', lambda self, workers: 1)
    class BatchBrain(Brain):
        def generate(self, messages, tools=None, **kwargs):
            if tools and not any('Result of' in str(item.get('content')) for item in messages):
                return SimpleNamespace(content='', tool_calls=[{'function': {'name': 'web_search', 'arguments': {'query': 'test'}}}] * 3)
            return super().generate(messages, tools=tools, **kwargs)
    registry = Registry()
    report = WorkerTeam(BatchBrain(DESIGN), registry, root=tmp_path).run('goal')
    assert report.tool_calls == len(registry.calls) == 1
    assert not report.complete


def test_dependency_case_is_normalised_to_actual_worker_role(tmp_path):
    design = {'workers': [
        {'role': 'Research Agent', 'mission': 'Find facts', 'tools': ['web_search']},
        {'role': 'Analysis Agent', 'mission': 'Compare findings', 'tools': ['web_search'],
         'depends_on': ['research agent', 'RESEARCH AGENT']}]}
    workers, _ = WorkerTeam(Brain(design), Registry(), root=tmp_path).plan('goal')
    assert workers[1].depends_on == ['Research Agent']


def test_local_verifier_receives_read_tools_and_empty_verifier_cannot_pass(tmp_path):
    from app.agents.team import WorkerSpec, TeamReport
    verifier = WorkerTeam._with_verifier([], ['read_file'])[-1]
    assert verifier.tools == ['read_file']
    spec = WorkerSpec(VERIFIER, 'Check claims', [])
    team = WorkerTeam(Brain(), Registry(), root=tmp_path)
    result = team._run_worker(spec, TeamReport('team-local', 'Check claims', '', [spec]), [])
    assert not result.ok and not result.successful_tools


def test_background_team_keeps_original_execution_context(tmp_path):
    from app.execution import current_context, execution_scope, ExecutionContext
    sessions, done = [], threading.Event()
    class ContextRegistry(Registry):
        def execute(self, name, arguments):
            sessions.append(current_context().session_id)
            return super().execute(name, arguments)
    team = WorkerTeam(Brain(DESIGN), ContextRegistry(), root=tmp_path,
                      deliver=lambda *_args: done.set())
    with execution_scope(context=ExecutionContext(session_id='original-team-session', turn_id='original-turn')):
        team.start('goal')
    assert done.wait(10)
    assert sessions and set(sessions) == {'original-team-session'}


def test_cancellation_stops_new_tool_calls_and_marks_team_incomplete(tmp_path):
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    class SlowBrain(Brain):
        def generate(self, messages, tools=None, **kwargs):
            if tools:
                entered.set()
                assert release.wait(5)
            return super().generate(messages, tools=tools, **kwargs)
    registry = Registry()
    team = WorkerTeam(SlowBrain(DESIGN), registry, root=tmp_path, deliver=lambda *_args: done.set())
    report = team.start('goal')
    try:
        assert entered.wait(5)
        assert team.cancel(report.team_id)
    finally:
        release.set()
    assert done.wait(5)
    assert report.cancelled and report.finished and not report.complete
    assert not registry.calls
