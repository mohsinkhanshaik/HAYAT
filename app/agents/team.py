"""Temporary worker teams: HAYAT builds a small crew of specialists for one goal, then dissolves it.

Asked "Research 20 AI infrastructure companies and find jobs suitable for me",
one assistant doing everything in one pass either stops early or blends
company facts, job listings and guesses into one answer. A team splits the work
the way a person would: a Research Agent finds the companies, a Job Search Agent
finds openings, a Company Intelligence Agent profiles them, a Resume Matching
Agent compares them with what HAYAT knows about the owner, and a Verification
Agent checks every claim against the sources the others cite. HAYAT then writes
the answer itself.

The rules that keep this safe and honest:

* the team is designed for the goal (by HAYAT's own model, with a fixed
  fallback) and always ends with a Verification Agent. How many workers it
  may have, and its time and tool-call budgets, are worked out from this
  machine (:mod:`app.agents.capacity`): the brain's context window, measured
  worker times and the CPU count -- not a fixed number;
* each worker may use only the tools named for it, and only from
  :data:`WORKER_TOOLS` -- reading, searching and researching. No worker can
  send, submit, post, log in, buy, delete or change anything; that stays with
  the owner's confirmed actions;
* every tool call still goes through the tool registry, so the authority
  kernel, permissions and audit see it like any other call;
* each worker, and the whole team, has a step and time budget;
* each worker appears in the running-activity list while it works, and the
  full report is saved, so nothing about the team is hidden;
* workers are temporary: they exist for one goal and are gone when it ends;
* a team bigger than the flat limit is organised in groups
  (:mod:`app.agents.team_groups`): each group's lead condenses its members'
  findings into a cited group report, code checks every claim's link against
  what the members' tools returned (anything else is marked unverified, never
  dropped), the Verification Agent reads and re-opens the group reports' links,
  and covered items are counted once across groups. Teams that fit stay flat.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.agents.team_groups import LEAD_SCHEMA, TeamGroup, condense, distinct_coverage, lead_prompt, norm_url, parse_lead
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

MAX_STEPS_PER_WORKER = 5
#: What a worker may be given. Reading and researching only.
WORKER_TOOLS = (
    "web_search", "research_topic", "public_web_fetch", "public_api_get", "bounded_crawl",
    "knowledge_search", "explain_knowledge", "recall_memory_page", "github_research", "paper_research",
    "read_file", "list_folder", "search_files", "get_file_info",
    "find_freelance_jobs", "score_job_opportunity", "lead_finder", "lead_score", "deal_probability",
    "content_idea_miner", "affiliate_opportunity_finder", "local_business_finder", "lead_list_builder",
)
VERIFIER = "Verification Agent"
#: What every member of a grouped team returns, so its covered items can be counted across groups.
GROUP_DELIVERABLE = ("Cited findings for your part, and each distinct item you covered with the exact source link "
                     "you observed.")


def _elapsed(started_at: str) -> float:
    try:
        return round((datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds(), 1)
    except (TypeError, ValueError):
        return 0.0


class TeamsBusy(RuntimeError):
    """The machine is already running as many teams as it should at once."""
WORKER_REPORT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'findings': {'type': 'string'},
        'completion': {'type': 'string', 'enum': ['complete', 'partial', 'blocked']},
        'coverage': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'company': {'type': 'string'}, 'source': {'type': 'string'}},
            'required': ['company', 'source']}},
        'gaps': {'type': 'array', 'items': {'type': 'string'}},
    }, 'required': ['findings', 'completion', 'coverage', 'gaps'],
}

#: An explicit request for a team.
_EXPLICIT = re.compile(
    r"(?i)\b(?:use|create|spawn|build|assemble|make|start|run)\s+(?:a\s+|some\s+)?"
    r"(?:team|crew|squad|workers?|sub-?agents?|agents?|specialists?)\b"
    r"|\bmulti-?agent\b|\bteam\s+of\s+(?:agents|workers|specialists)\b"
    r"|\b(?:team|crew|squad)\s+of\s+\d{1,4}\s+(?:[a-z-]+\s+)?(?:agents|workers|specialists)\b"
    r"|\b(?:use|create|spawn|build|assemble|start|run|deploy|launch|with)\s+\d{1,4}\s+(?:[a-z-]+\s+)?"
    r"(?:agents|workers|sub-?agents|specialists)\b"
    r"|\b(?:biggest|largest|maximum|max|full[- ]size)\s+(?:possible\s+)?team\b"
)
#: How many workers the owner asked for: "a team of 60 agents", "use 40 workers".
_REQUESTED = re.compile(
    r"(?i)\b(?:team|crew|squad)\s+of\s+(\d{1,4})\b"
    r"|\b(?:use|create|spawn|build|assemble|start|run|deploy|launch|with)\s+(\d{1,4})\s+(?:[a-z-]+\s+)?"
    r"(?:agents|workers|sub-?agents|specialists)\b")
_LARGEST = re.compile(r"(?i)\b(?:biggest|largest|maximum|max|full[- ]size)\s+(?:possible\s+)?team\b")
#: "The largest team you can": as many as this machine allows.
AS_MANY_AS_FIT = 10**6
_ITEM_COUNT = re.compile(
    r"(?i)\b(\d{1,4})\s+(?:(?!agents?\b|workers?\b|specialists?\b)[a-z-]+\s+){0,3}?"
    r"(?:companies|startups|firms|businesses|products|tools|universities|colleges|schools|jobs|roles|positions|"
    r"suppliers|vendors|competitors|apps|platforms|frameworks|libraries|countries|cities|markets|niches|courses|"
    r"investors|funds|stocks|papers|items|sources|options)\b")
#: Many items of one kind ("20 AI infrastructure companies")...
_MANY_ITEMS = re.compile(
    r"(?i)\b(?:\d{1,3}|five|six|seven|eight|nine|ten|twelve|fifteen|twenty|thirty|fifty|dozens?\s+of)\s+"
    r"(?:[a-z-]+\s+){0,3}?(?:companies|startups|firms|businesses|products|tools|universities|colleges|schools|"
    r"jobs|roles|positions|suppliers|vendors|competitors|apps|platforms|frameworks|libraries|countries|cities|"
    r"markets|niches|courses|investors|funds|stocks|options)\b"
)
#: ...plus a second piece of work about them.
_SECOND_TASK = re.compile(
    r"(?i)\band\s+(?:then\s+)?(?:find|match|compare|rank|recommend|shortlist|evaluate|identify|assess|"
    r"pick|choose|list|map|analy[sz]e|score|summari[sz]e|check)\b"
)


_CAPACITY = re.compile(
    r"(?i)\b(?:how many|max(?:imum)?(?: number of)?|limit(?: on| of)?|capacity)\b.{0,40}?"
    r"\b(?:agents?|workers?|teams?|sub-?agents?)\b"
    r"|\b(?:agents?|workers?|teams?)\b.{0,20}?\b(?:limit|capacity|cap|maximum)\b")


_QUESTION = re.compile(r"(?i)^(?:how|what|whats|what's|can|could|do|does|is|are|tell me|give me|show me)\b|\?\s*$")


def capacity_question(text: str) -> bool:
    """"How many agents can you run?" -- answered from the measured capacity, not by the model.

    Only a question: "use the maximum number of agents to research X" is a request for a team.
    """
    value = " ".join(str(text or "").split())
    return len(value) <= 200 and bool(_CAPACITY.search(value)) and bool(_QUESTION.search(value))


def requested_workers(text: str) -> int | None:
    """How many workers the owner asked for, AS_MANY_AS_FIT for "the largest team", else None."""
    value = " ".join(str(text or "").split())
    match = _REQUESTED.search(value)
    if match:
        number = int(match.group(1) or match.group(2))
        return number if number > 0 else None
    return AS_MANY_AS_FIT if _LARGEST.search(value) else None


def _item_count(goal: str) -> int | None:
    match = _ITEM_COUNT.search(goal)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def wants_team(text: str) -> bool:
    """Whether a request is worth a team: asked for by name, or many items plus a second task about them."""
    value = " ".join(str(text or "").split())
    if _EXPLICIT.search(value):
        return True
    return bool(_MANY_ITEMS.search(value) and _SECOND_TASK.search(value))


@dataclass
class WorkerSpec:
    role: str
    mission: str
    tools: list[str]
    depends_on: list[str] = field(default_factory=list)
    deliverable: str = ""
    target_count: int | None = None
    #: The group this worker belongs to in a grouped team ("" in a flat team).
    group: str = ""
    #: What a coverage row names: a company in the career workflow, any item in a grouped team.
    item_label: str = "company"


@dataclass
class WorkerResult:
    role: str
    mission: str
    tools: list[str]
    ok: bool
    findings: str
    tools_used: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    seconds: float = 0.0
    note: str = ""
    successful_tools: list[str] = field(default_factory=list)
    complete: bool = False
    coverage: list[dict[str, str]] = field(default_factory=list)
    group: str = ""
    #: "worker", or "group" for a group report handed to the verifier and the final answer.
    kind: str = "worker"
    #: Links this worker passed to a tool call that succeeded (how verification of links is measured).
    opened: list[str] = field(default_factory=list)


@dataclass
class TeamReport:
    team_id: str
    goal: str
    started_at: str
    workers: list[WorkerSpec]
    results: list[WorkerResult] = field(default_factory=list)
    answer: str = ""
    planned_by: str = "model"
    finished: bool = False
    seconds: float = 0.0
    tool_calls: int = 0
    report_path: str = ""
    complete: bool = False
    current_worker: str = ""
    cancelled: bool = False
    note: str = ""
    #: Budgets worked out for this team from the machine (app.agents.capacity).
    max_seconds: float = 1200.0
    max_tool_calls: int = 40
    limits: dict = field(default_factory=dict)
    #: A grouped team's groups and their condensed reports; empty for a flat team.
    groups: list[TeamGroup] = field(default_factory=list)
    #: Said to the owner when the team differs from what was asked ("you asked for 500...").
    plan_note: str = ""
    #: Distinct items covered across all groups (an item two groups covered counts once).
    coverage_total: int = 0
    #: Groups whose cited links the Verification Agent opened, and those it did not.
    verified_groups: list[str] = field(default_factory=list)
    unchecked_groups: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def job_id(self) -> str:
        """The name background-job notices file this report under."""
        return self.team_id

    def roster(self) -> str:
        return ", ".join(worker.role for worker in self.workers)


def _owner() -> str:
    try:
        from app.identity import IDENTITY

        return IDENTITY.owner_short or "the owner"
    except Exception:
        return "the owner"


_URL = re.compile(r"https?://[^\s)\]\"'<>]+")


def _source_urls(value: Any) -> list[str]:
    """Read original strings, avoiding JSON escapes being mistaken for part of a URL."""
    if isinstance(value, str):
        return [url.rstrip('.,;') for url in _URL.findall(value)]
    if isinstance(value, dict):
        return [url for item in value.values() for url in _source_urls(item)]
    if isinstance(value, (list, tuple)):
        return [url for item in value for url in _source_urls(item)]
    return []


def _completion_data(content: str) -> dict[str, Any] | None:
    value = content.strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:json)?\s*|\s*```$', '', value, flags=re.IGNORECASE)
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('findings'), str) else None


class WorkerTeam:
    """Designs, runs and dissolves one temporary team per goal."""

    def __init__(self, model: Any, registry: Any, *, root: Path,
                 deliver: Callable[["TeamReport", str], None] | None = None) -> None:
        self.model = model
        self.registry = registry
        self.root = Path(root)
        self.deliver = deliver
        self._lock = threading.Lock()
        self._running: dict[str, TeamReport] = {}
        self._recent: list[TeamReport] = []
        self._capacity = None
        # What plan() decided beyond the worker list (groups, a note), per planning thread.
        self._planned = threading.local()

    # ------------------------------------------------------------- capacity

    def capacity(self, *, refresh: bool = False):
        """How big a team this machine can run, measured now (see app.agents.capacity)."""
        from app.agents.capacity import measure

        if refresh or self._capacity is None:
            self._capacity = measure(self.model, reports=self.root / "data" / "artifacts" / "teams")
        return self._capacity

    def measured_capacity(self):
        """The last measurement, or None: for displays that must not wait on nvidia-smi."""
        return self._capacity

    def _new_report(self, goal: str) -> TeamReport:
        capacity = self.capacity(refresh=True)
        self._planned.groups, self._planned.note = [], ""
        workers, planned_by = self.plan(goal)
        report = TeamReport(f"team-{uuid.uuid4().hex[:8]}", goal, datetime.now(UTC).isoformat(), workers,
                            planned_by=planned_by)
        groups = list(getattr(self._planned, "groups", None) or [])
        roles = {spec.role for spec in workers}
        if groups and all(set(group.members) <= roles for group in groups):
            report.groups = groups
        report.plan_note = str(getattr(self._planned, "note", "") or "")
        report.max_seconds = capacity.team_seconds(len(workers), len(report.groups))
        report.max_tool_calls = capacity.team_tool_calls(len(workers))
        report.limits = {"max_workers": capacity.max_workers, "context_tokens": capacity.context_tokens,
                         "seconds_per_worker": capacity.seconds_per_worker,
                         "concurrent_teams": capacity.concurrent_teams,
                         "grouped_workers": capacity.grouped_workers, "groups": len(report.groups),
                         "expected_seconds": (len(workers) + len(report.groups)) * capacity.seconds_per_worker}
        return report

    # ------------------------------------------------------------- planning

    def available_tools(self) -> list[str]:
        names = set(self.registry.names()) if self.registry is not None else set()
        return [name for name in WORKER_TOOLS if name in names]

    def plan(self, goal: str) -> tuple[list[WorkerSpec], str]:
        """The team for this goal, designed by HAYAT's model, or a fixed fallback when it cannot."""
        tools = self.available_tools()
        self._planned.groups, self._planned.note = [], ""
        career = self._career_plan(goal, tools)
        if career:
            return career, "career workflow"
        requested = requested_workers(goal)
        if requested:
            capacity = self.capacity()
            limit = capacity.team_limit
            total = min(requested, limit)
            if requested > limit and requested != AS_MANY_AS_FIT:
                self._planned.note = (f"You asked for {requested} workers; this machine can run at most {limit} in one "
                                      f"team (its {capacity.context_tokens:,}-token brain window must hold every group "
                                      f"report), so this team has {total}.")
            sizes = capacity.layout(total)
            if sizes:
                specs, groups, planned_by = self._grouped_plan(goal, tools, sizes)
                self._planned.groups = groups
                return specs, planned_by
        designed = self._model_plan(goal, tools, requested=requested) if self.model is not None else None
        if designed:
            return designed, "model"
        return self._fallback_plan(goal, tools), "fallback"

    # ------------------------------------------------------------- grouped teams

    def _grouped_plan(self, goal: str, tools: list[str], sizes: list[int]) -> tuple[list[WorkerSpec], list[TeamGroup], str]:
        """A team bigger than the flat limit: groups (designed by the model, else numbered parts) of members.

        Each member starts as a working generic researcher for its part of the
        group's focus; when its group begins, the model may give the members
        distinct missions (``_design_members``). Every member returns a
        structured report so covered items can be counted across groups.
        """
        groups = self._model_groups(goal, len(sizes)) if self.model is not None else None
        planned_by = "grouped: model" if groups else "grouped: fallback"
        if not groups:
            groups = [TeamGroup(f"Group {index}", f"Part {index} of {len(sizes)} of the goal: {goal[:200]}")
                      for index in range(1, len(sizes) + 1)]
        items = _item_count(goal)
        target = -(-items // sum(sizes)) if items else None
        search = [name for name in ("research_topic", "web_search", "public_web_fetch", "public_api_get") if name in tools]
        specs: list[WorkerSpec] = []
        used = {VERIFIER.casefold()}
        for group, size in zip(groups, sizes):
            group.members, group.designed_by = [], "placeholder"
            for index in range(1, size + 1):
                role = self._unique_role(f"{group.name} Agent {index}", used)
                specs.append(WorkerSpec(
                    role, f"Part {index} of {size} of the {group.name} group's focus: {group.focus} Cover items the "
                          "rest of the team has not covered yet (you are given that list) and cite the source link "
                          "for every claim.",
                    list(search), deliverable=GROUP_DELIVERABLE, target_count=target, group=group.name,
                    item_label="item"))
                group.members.append(role)
        verifier = self._with_verifier(specs, tools)[-1]
        verifier.depends_on = [group.name for group in groups]
        verifier.mission = ("Each group report lists cited claims with the link a worker's tool returned. Re-open the "
                            "most important cited links with your tools -- at least one from every group -- and say for "
                            "each checked claim whether the page supports it. Reject unsupported claims, keep "
                            "unverified ones labelled unverified, and name the groups you could not check.")
        return specs + [verifier], groups, planned_by

    @staticmethod
    def _unique_role(role: str, used: set[str]) -> str:
        role = " ".join(role.split())[:60] or "Worker Agent"
        candidate, number = role, 2
        while candidate.casefold() in used:
            candidate, number = f"{role} {number}", number + 1
        used.add(candidate.casefold())
        return candidate

    def _model_groups(self, goal: str, count: int) -> list[TeamGroup] | None:
        prompt = (
            f"Split the goal below into exactly {count} groups of temporary specialist workers. Each group takes one "
            "distinct part of the work (a sub-topic, a region, a category or a slice of the items) so groups do not "
            "repeat each other. Give each group a short name (two or three words, no 'verification'), a one-sentence "
            "focus, and the earlier groups whose results it needs first (depends_on, earlier names only).\n"
            f"Goal: {goal}\n"
            'Reply with JSON only: {"groups": [{"name": "...", "focus": "...", "depends_on": []}]}')
        schema = {"type": "object", "properties": {"groups": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}, "focus": {"type": "string"},
                                             "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "focus"]}}}, "required": ["groups"]}
        try:
            reply = self.model.generate([{"role": "user", "content": prompt}], temperature=0.1,
                                        response_schema=schema, max_tokens=120 * count + 200)
            rows = json.loads(str(getattr(reply, "content", "") or "{}")).get("groups")
        except Exception:
            log_swallowed(_log, "designing the groups of a large worker team")
            return None
        if not isinstance(rows, list):
            return None
        groups: list[TeamGroup] = []
        names: dict[str, str] = {VERIFIER.casefold(): VERIFIER}
        for row in rows:
            if not isinstance(row, dict) or len(groups) >= count:
                continue
            name = " ".join(str(row.get("name") or "").split())[:40]
            focus = " ".join(str(row.get("focus") or "").split())[:300]
            if not name or not focus or name.casefold() in names or "verif" in name.casefold():
                continue
            depends = list(dict.fromkeys(names[str(item).casefold()] for item in row.get("depends_on") or []
                                         if str(item).casefold() in names))
            names[name.casefold()] = name
            groups.append(TeamGroup(name, focus, depends_on=depends))
        if not groups:
            return None
        # Fewer usable groups than the layout needs: number the missing parts rather than drop workers.
        for index in range(len(groups) + 1, count + 1):
            name = self._unique_role(f"Group {index}", set(names))
            names[name.casefold()] = name
            groups.append(TeamGroup(name, f"Part {index} of {count} of the goal not covered by the other groups: "
                                          f"{goal[:200]}"))
        return groups

    def _design_members(self, report: TeamReport, group: TeamGroup) -> None:
        """Give a group's members distinct missions from the model when the group starts; else keep the parts."""
        if self.model is None or not group.members:
            return
        tools = self.available_tools()
        catalog = "\n".join(f"- {name}: {self._describe_tool(name)}" for name in tools)
        size = len(group.members)
        prompt = (
            f"You organise the '{group.name}' group of a temporary team working on: {report.goal}\n"
            f"Group focus: {group.focus}\nSplit this focus between exactly {size} workers, each covering a different "
            "part so they do not repeat each other. Give each a short role name ending in 'Agent', a one-sentence "
            "mission and the tools it needs from the catalog, and the earlier workers of this group whose findings it "
            "needs (depends_on). Do not add a verification worker.\n"
            f"Tool catalog:\n{catalog}\n"
            'Reply with JSON only: {"workers": [{"role": "...", "mission": "...", "tools": ["..."], "depends_on": []}]}')
        schema = {"type": "object", "properties": {"workers": {"type": "array", "items": {
            "type": "object", "properties": {"role": {"type": "string"}, "mission": {"type": "string"},
                                             "tools": {"type": "array", "items": {"type": "string"}},
                                             "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["role", "mission", "tools"]}}}, "required": ["workers"]}
        try:
            reply = self.model.generate([{"role": "user", "content": prompt}], temperature=0.1,
                                        response_schema=schema, max_tokens=110 * size + 200)
            rows = json.loads(str(getattr(reply, "content", "") or "{}")).get("workers")
        except Exception:
            log_swallowed(_log, f"designing the members of team group {group.name}")
            group.designed_by = "placeholder (the model could not design the members)"
            return
        if not isinstance(rows, list):
            group.designed_by = "placeholder (the model could not design the members)"
            return
        by_role = {spec.role: spec for spec in report.workers}
        members = [by_role[role] for role in group.members if role in by_role]
        used = {spec.role.casefold() for spec in report.workers if spec.group != group.name} | {VERIFIER.casefold()}
        designed: dict[str, str] = {}
        applied = 0
        for row in rows:
            if applied >= len(members) or not isinstance(row, dict):
                continue
            role = " ".join(str(row.get("role") or "").split())[:40]
            mission = " ".join(str(row.get("mission") or "").split())[:300]
            if not role or not mission or "verif" in role.casefold():
                continue
            spec = members[applied]
            spec.role = self._unique_role(role, used)
            spec.mission = mission
            allowed = [name for name in row.get("tools") or [] if name in tools][:5]
            spec.tools = allowed or spec.tools
            spec.depends_on = list(dict.fromkeys(designed[str(name).casefold()] for name in row.get("depends_on") or []
                                                 if str(name).casefold() in designed))
            designed[role.casefold()] = spec.role
            applied += 1
        for spec in members[applied:]:           # placeholders keep their working generic mission
            spec.role = self._unique_role(spec.role, used)
        group.members = [spec.role for spec in members]
        group.designed_by = f"model ({applied} of {len(members)} members)" if applied else "placeholder"

    def _career_plan(self, goal: str, tools: list[str]) -> list[WorkerSpec] | None:
        """A predictable five-role workflow, without an extra model round trip to plan it."""
        if not (re.search(r"(?i)\b(?:companies|startups|firms|businesses)\b", goal)
                and re.search(r"(?i)\b(?:jobs?|openings?|vacancies|positions|careers)\b", goal)):
            return None
        quantity = re.search(r"(?i)\b(\d{1,3}|five|ten|twelve|fifteen|twenty|thirty|fifty)\s+"
                             r"(?:[a-z-]+\s+){0,3}?(?:companies|startups|firms|businesses)\b", goal)
        numbers = dict(five=5, ten=10, twelve=12, fifteen=15, twenty=20, thirty=30, fifty=50)
        raw = quantity.group(1).lower() if quantity else ''
        count = int(raw) if raw.isdigit() else numbers.get(raw)
        count = count if count and count > 0 else None
        target = f"{count} distinct companies" if count else "the requested companies"
        select = lambda names: [name for name in names if name in tools]
        web = select(('web_search', 'public_web_fetch', 'public_api_get', 'research_topic'))
        memory = select(('knowledge_search', 'recall_memory_page', 'search_files', 'read_file', 'list_folder'))
        specs = [
            WorkerSpec('Research Agent',
                       f'Identify {target} relevant to this request: {goal}. Deduplicate companies and cite official sources.',
                       web, deliverable='Company roster: name, official website, infrastructure category and source.', target_count=count),
            WorkerSpec('Job Search Agent',
                       'Search the Research Agent company roster for current jobs. Prefer official careers pages and job listings. '
                       'Record title, location/remote rules, requirements, listing URL and check date. For each company distinguish '
                       'an observed opening, no relevant opening found, or not checked; a search snippet alone cannot prove a job is open.',
                       web, ['Research Agent'], 'Company-by-company job search results, including explicit unknowns.', count),
            WorkerSpec('Company Intelligence Agent',
                       'Use the Research Agent roster to establish each company product, infrastructure focus and technical stack '
                       'from sources. Separate facts from inference; do not invent funding, hiring or technology claims.',
                       web, ['Research Agent'], 'Company profiles with source links and uncertainties.', count),
            WorkerSpec('Resume Matching Agent',
                       'Look up the owner skills, experience and preferences in memory; read a provided resume or locate a CV '
                       'within the project if needed. Match sourced openings against actual profile evidence. Explain strengths, '
                       'gaps and location/work eligibility uncertainty. If profile evidence is missing, return partial or blocked, '
                       'give a provisional shortlist and state exactly what profile information is needed; never invent qualifications.',
                       memory, ['Job Search Agent', 'Company Intelligence Agent'],
                       'Ranked roles with job URLs, profile evidence, reasons, gaps and questions needed to confirm suitability.'),
        ]
        verifier = self._with_verifier(specs, tools)[-1]
        verifier.mission = ('Recheck the company roster and recommended job listings against sources using your tools. '
                            'Check duplicates, requested company coverage, current opening status, cited requirements and '
                            'profile-based match claims. Reject unsupported claims. Report unchecked companies and missing '
                            'profile evidence explicitly; inherited citations alone do not establish verification.')
        verifier.deliverable = 'Checked company coverage, verified or rejected job claims, and unresolved gaps.'
        verifier.target_count = count
        return specs + [verifier]

    def _model_plan(self, goal: str, tools: list[str], *, requested: int | None = None) -> list[WorkerSpec] | None:
        catalog = "\n".join(f"- {name}: {self._describe_tool(name)}" for name in tools)
        limit = self.capacity().max_workers - 1
        asked = (f"The owner asked for {requested} workers in all, so design {min(limit, max(2, requested - 1))} "
                 "plus the verifier. " if requested and requested != AS_MANY_AS_FIT else "")
        prompt = (
            "Design a small team of temporary specialist workers for the goal below. Each worker does one part "
            "and passes its findings on. Use as many workers as the goal genuinely splits into: at least 2 and at "
            f"most {limit} on this machine; fewer when the work does not divide. {asked}Give each "
            "a short role name ending in 'Agent', a "
            "one-sentence mission, the tools it needs from the catalog, and the roles whose findings it needs "
            "first (depends_on, earlier roles only). Do not add a verification worker; one is always added. "
            "What HAYAT knows about the owner (skills, experience, preferences) is in its memory: a worker that "
            "matches anything to the owner needs knowledge_search and recall_memory_page, plus search_files and "
            "read_file to find a CV or resume.\n"
            f"Tool catalog:\n{catalog}\n\nGoal: {goal}\n"
            'Reply with JSON only: {"workers": [{"role": "...", "mission": "...", "tools": ["..."], '
            '"depends_on": ["..."]}]}'
        )
        schema = {"type": "object", "properties": {"workers": {"type": "array", "items": {
            "type": "object", "properties": {"role": {"type": "string"}, "mission": {"type": "string"},
                                             "tools": {"type": "array", "items": {"type": "string"}},
                                             "depends_on": {"type": "array", "items": {"type": "string"}}},
            "required": ["role", "mission", "tools"]}}}, "required": ["workers"]}
        try:
            reply = self.model.generate([{"role": "user", "content": prompt}], temperature=0.1,
                                        response_schema=schema, max_tokens=900)
            data = json.loads(str(getattr(reply, "content", "") or "{}"))
        except Exception:
            log_swallowed(_log, "designing a worker team")
            return None
        return self._validated(data.get("workers") if isinstance(data, dict) else None, tools)

    def _validated(self, rows: Any, tools: list[str]) -> list[WorkerSpec] | None:
        limit = self.capacity().max_workers
        if not isinstance(rows, list) or len(rows) > limit:
            return None
        specs: list[WorkerSpec] = []
        seen: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = " ".join(str(row.get("role") or "").split())[:40]
            mission = " ".join(str(row.get("mission") or "").split())[:300]
            if not role or not mission or role.casefold() in seen or "verif" in role.casefold():
                continue
            if len(specs) >= limit - 1:
                return None
            allowed = [name for name in row.get("tools") or [] if name in tools][:5]
            if not allowed:
                allowed = [name for name in ("research_topic", "web_search") if name in tools][:1]
            depends = list(dict.fromkeys(seen[str(name).casefold()] for name in row.get("depends_on") or []
                                        if str(name).casefold() in seen))
            seen[role.casefold()] = role
            specs.append(WorkerSpec(role, mission, allowed, depends))
        if len(specs) < 2:
            return None
        return self._with_verifier(specs, tools)

    def _fallback_plan(self, goal: str, tools: list[str]) -> list[WorkerSpec]:
        search = [name for name in ("research_topic", "web_search", "public_web_fetch") if name in tools]
        memory = [name for name in ("knowledge_search", "recall_memory_page", "read_file", "list_folder") if name in tools]
        specs = [
            WorkerSpec("Research Agent", f"Find the main facts, items and sources needed for: {goal}", search),
            WorkerSpec("Analysis Agent", f"Compare and organise the research findings so they answer: {goal}",
                       memory or search, ["Research Agent"]),
        ]
        if re.search(r"(?i)\b(?:me|my|i)\b", goal) and memory:
            specs.append(WorkerSpec("Profile Matching Agent",
                                    "Use what HAYAT knows about the owner (memory, files) to judge which findings "
                                    "suit the owner, and say what is still unknown about them.", memory,
                                    ["Research Agent", "Analysis Agent"]))
        return self._with_verifier(specs, tools)

    @staticmethod
    def _with_verifier(specs: list[WorkerSpec], tools: list[str]) -> list[WorkerSpec]:
        checking = [name for name in ("public_web_fetch", "web_search") if name in tools]
        if not checking:
            checking = [name for name in ('read_file', 'get_file_info', 'knowledge_search') if name in tools][:2]
        return specs + [WorkerSpec(VERIFIER, "Check every claim the other workers made against the sources they "
                                             "cite; keep what is supported, mark what is not, and note gaps.",
                                   checking, [spec.role for spec in specs])]

    def _describe_tool(self, name: str) -> str:
        try:
            return str(self.registry.get(name).description)[:140]
        except Exception:
            return ""

    # ------------------------------------------------------------- running

    def start(self, goal: str) -> TeamReport:
        """Design the team now and run it in the background; the result is delivered when it is done.

        Raises TeamsBusy when this machine is already running as many teams as it can share its brain with.
        """
        limit = self.capacity(refresh=True).concurrent_teams
        with self._lock:
            running = len(self._running)
        if running >= limit:
            raise TeamsBusy(f"{running} team{'s are' if running != 1 else ' is'} already running, and this machine "
                            f"runs at most {limit} at once: they share one brain and would slow each other down. "
                            "Wait for one to finish, or cancel one, then ask again.")
        report = self._new_report(goal)
        with self._lock:
            self._running[report.team_id] = report
        from app.execution import current_context, execution_scope
        context = current_context()
        def run_scoped():
            # Carry causal identity, not the foreground reply's stream/cancel state.
            with execution_scope(context=context):
                self._run_and_deliver(report)
        threading.Thread(target=run_scoped, name=f"hayat-{report.team_id}",
                         daemon=True).start()
        return report

    def run(self, goal: str) -> TeamReport:
        """Design and run a team in this thread (used by tests and the /team command when waiting)."""
        report = self._new_report(goal)
        with self._lock:
            self._running[report.team_id] = report
        self._execute(report)
        return report

    def _run_and_deliver(self, report: TeamReport) -> None:
        try:
            self._execute(report)
        except Exception as exc:
            report.answer = f"The team stopped with an error: {type(exc).__name__}: {exc}"
            report.complete = False
            self._save(report)
            log_swallowed(_log, "running a worker team", level=logging.WARNING)
        if self.deliver is not None:
            try:
                self.deliver(report, self.summary_text(report))
            except Exception:
                log_swallowed(_log, "delivering a team report", level=logging.WARNING)

    def _execute(self, report: TeamReport) -> None:
        from app.activity import ACTIVITY

        started = time.monotonic()
        try:
            label = (f"{len(report.workers)} workers in {len(report.groups)} groups" if report.groups
                     else f"{len(report.workers)} workers")
            with ACTIVITY.track("task", "Worker team", f"{label}: {report.goal[:120]}"):
                final_inputs = (self._run_grouped(report, started) if report.groups
                                else self._run_flat(report, started))
                if report.cancelled or time.monotonic() >= started + report.max_seconds:
                    report.answer = 'Team stopped before the final answer. Review the recorded findings and gaps in its report.'
                else:
                    report.answer = self._final_answer(report, final_inputs)
        finally:
            report.current_worker = ''
            report.finished = True
            report.complete = (not report.cancelled and not report.note and time.monotonic() < started + report.max_seconds
                               and len(report.results) == len(report.workers) and all(item.complete for item in report.results)
                               and not report.unchecked_groups)
            report.seconds = round(time.monotonic() - started, 1)
            saved = self._save(report)
            report.report_path = str(saved) if saved is not None else ''
            with self._lock:
                self._running.pop(report.team_id, None)
                self._recent = ([report] + self._recent)[:10]

    @staticmethod
    def _skipped(spec: WorkerSpec, report: TeamReport) -> WorkerResult:
        return WorkerResult(spec.role, spec.mission, spec.tools, False, "", group=spec.group,
                            note="skipped: team stopped" if report.cancelled else "skipped: the team's time budget ran out")

    def _run_flat(self, report: TeamReport, started: float) -> list[WorkerResult]:
        """Every worker in order; the verifier and the final answer read every worker's findings."""
        from app.activity import ACTIVITY

        for spec in report.workers:
            if report.cancelled or time.monotonic() - started >= report.max_seconds:
                report.results.append(self._skipped(spec, report))
                continue
            report.current_worker = spec.role
            inputs = [result for result in report.results if result.role in spec.depends_on] or (
                list(report.results) if spec.role == VERIFIER else [])
            with ACTIVITY.track("task", spec.role, spec.mission[:160]):
                report.results.append(self._run_worker(spec, report, inputs, deadline=started + report.max_seconds))
        return list(report.results)

    def _run_grouped(self, report: TeamReport, started: float) -> list[WorkerResult]:
        """Group by group: members, then the group's lead condenses; the verifier reads the group reports."""
        from app.activity import ACTIVITY

        deadline = started + report.max_seconds
        stopped = lambda: report.cancelled or time.monotonic() >= deadline
        group_results: dict[str, WorkerResult] = {}
        for group in report.groups:
            if not stopped():
                report.current_worker = f"{group.name} (planning)"
                self._design_members(report, group)
            by_role = {spec.role: spec for spec in report.workers}
            members: list[WorkerResult] = []
            for role in group.members:
                spec = by_role[role]
                if stopped():
                    result = self._skipped(spec, report)
                else:
                    report.current_worker = spec.role
                    inputs = [item for item in members if item.role in spec.depends_on]
                    inputs += [group_results[name] for name in group.depends_on if name in group_results]
                    covered = [row["company"] for row in distinct_coverage(
                        row for item in report.results for row in item.coverage)]
                    with ACTIVITY.track("task", spec.role, spec.mission[:160]):
                        result = self._run_worker(spec, report, inputs, deadline=deadline, covered=covered)
                    result.group = group.name
                report.results.append(result)
                members.append(result)
            self._condense(report, group, members, deadline)
            group_results[group.name] = self._group_result(group, members)
        verifier = report.workers[-1]
        if stopped():
            checked = self._skipped(verifier, report)
        else:
            report.current_worker = verifier.role
            with ACTIVITY.track("task", verifier.role, verifier.mission[:160]):
                checked = self._run_worker(verifier, report, list(group_results.values()), deadline=deadline)
        report.results.append(checked)
        self._record_verification(report, checked)
        report.coverage_total = len(distinct_coverage(row for group in report.groups for row in group.coverage))
        return [*group_results.values(), checked]

    def _condense(self, report: TeamReport, group: TeamGroup, members: list[WorkerResult], deadline: float) -> None:
        """The group lead's turn: the model proposes condensed claims; code checks every link (team_groups)."""
        from app.activity import ACTIVITY

        started = time.monotonic()
        capacity = self.capacity()
        budget = capacity.group_report_chars(len(report.groups))
        found = [item for item in members if item.findings]
        data, why = None, ""
        if not found:
            why = "no member returned findings"
        elif report.cancelled or time.monotonic() >= deadline:
            why = "team stopped" if report.cancelled else "the team's time budget ran out"
        elif self.model is None:
            why = "no model is available for the group lead"
        else:
            report.current_worker = f"{group.name} lead"
            prompt = lead_prompt(report.goal, group, members, capacity.lead_share_chars(len(found)), budget)
            from app.agent.context import UNTRUSTED_DATA_NOTICE
            from app.agents.capacity import LEAD_ANSWER_TOKENS
            try:
                with ACTIVITY.track("task", f"{group.name} lead", f"Condensing {len(found)} workers' findings"):
                    reply = self.model.generate([{"role": "user", "content": prompt + "\n\n" + UNTRUSTED_DATA_NOTICE}],
                                                temperature=0.1, response_schema=LEAD_SCHEMA,
                                                max_tokens=LEAD_ANSWER_TOKENS)
                data = parse_lead(str(getattr(reply, "content", "") or ""))
                why = "" if data is not None else "the lead's reply was not a valid condensed report"
            except Exception as exc:
                why = f"the lead's model call failed: {type(exc).__name__}"
                log_swallowed(_log, f"condensing team group {group.name}")
        condense(group, members, data, budget=budget, why=why)
        group.seconds = round(time.monotonic() - started, 1)

    @staticmethod
    def _group_result(group: TeamGroup, members: list[WorkerResult]) -> WorkerResult:
        """A group report in the shape the verifier and the final answer read."""
        sources = list(dict.fromkeys([*group.cited_sources(), *(row["source"] for row in group.coverage)]))
        tools = list(dict.fromkeys(name for item in members for name in item.successful_tools))
        return WorkerResult(f"{group.name} group", group.focus, [], any(item.ok for item in members), group.report,
                            successful_tools=tools, sources=sources, complete=bool(members) and all(
                                item.complete for item in members), coverage=list(group.coverage),
                            note=f"{len(group.gaps)} gaps, listed in the group report" if group.gaps else "",
                            group=group.name, kind="group")

    @staticmethod
    def _record_verification(report: TeamReport, verifier: WorkerResult) -> None:
        """Which groups had a cited link actually re-opened by the Verification Agent's tools."""
        opened = {norm_url(url) for url in verifier.opened} if verifier.successful_tools else set()
        report.verified_groups, report.unchecked_groups = [], []
        for group in report.groups:
            cited = {norm_url(url) for url in group.cited_sources()}
            (report.verified_groups if cited & opened else report.unchecked_groups).append(group.name)

    def _run_worker(self, spec: WorkerSpec, report: TeamReport, inputs: list[WorkerResult], *, deadline=float('inf'),
                    covered: list[str] | None = None) -> WorkerResult:
        started = time.monotonic()
        result = WorkerResult(spec.role, spec.mission, spec.tools, False, "", group=spec.group)
        if self.model is None:
            result.note = "no model is available to run this worker"
            return result
        schemas = []
        for name in spec.tools:
            try:
                schemas.append(self.registry.get(name).model_schema())
            except Exception:
                log_swallowed(_log, f"describing tool {name} to a worker")
        # Every input gets an equal share of the window, so none is cut off the end.
        share = self.capacity().share_chars(len(inputs), for_verifier=spec.role == VERIFIER)

        def hand(item: WorkerResult) -> str:
            if item.kind == "group":
                # A group report already carries its cited links, items and gaps, sized to fit.
                return (f"Group report from the {item.role} ({'evidence gathered' if item.ok else 'UNVERIFIED'}):\n"
                        f"{item.findings[:share]}")
            return (f"Findings from {item.role} ({'evidence gathered' if item.ok else 'UNVERIFIED'}):\n"
                    f"{item.findings[:share]}\nSources: {', '.join(item.sources)}\n"
                    f"Coverage: {json.dumps(item.coverage)}\n"
                    f"Task complete: {item.complete}\nGaps: {item.note or 'none recorded'}")

        handed = "\n\n".join(hand(item) for item in inputs)
        if covered:
            names, shown = [], 0
            for name in covered:
                if shown + len(name) + 2 > 1500:
                    break
                names.append(name)
                shown += len(name) + 2
            more = f" (and {len(covered) - len(names)} more)" if len(names) < len(covered) else ""
            handed = (handed + "\n\n" if handed else "") + (
                f"Items the rest of the team has already covered -- do not repeat them, find others: "
                f"{'; '.join(names)}{more}")
        contract = ''
        if spec.deliverable:
            label = spec.item_label or 'company'
            plural = 'companies' if label == 'company' else f'{label}s'
            field_note = '' if label == 'company' else ' ("company" is the field name for any item)'
            contract = (f' Your required deliverable: {spec.deliverable} When finished, return JSON only with '
                        '"findings" (a readable summary with citations), "completion" ("complete", "partial" or "blocked"), '
                        '"coverage" (a list of {"company": "name", "source": "exact observed URL"})' + field_note + ', and "gaps" '
                        '(a list of unresolved issues). Each coverage row means that you completed your own mission for '
                        f'that {label}, not merely that its name was mentioned. List each {label} once. Unsupported or '
                        f'unchecked {plural} do not count. Mark partial if any requested work or profile evidence is missing. '
                        + (f'The coverage target is {spec.target_count} distinct {plural}.' if spec.target_count else ''))
        from app.agent.context import UNTRUSTED_DATA_NOTICE
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": (
                f"You are the {spec.role}, a temporary worker HAYAT created for one goal. Your mission: "
                f"{spec.mission} The team's goal: {report.goal} Use your tools to gather real evidence. Never "
                "invent a fact, number, company, job or link; if you cannot find something, say so. When you are "
                "done, reply without calling a tool: give your findings as a clear list, each with the source "
                "URL it came from. " + contract + ' ' + UNTRUSTED_DATA_NOTICE)},
            {"role": "user", "content": (handed + "\n\n" if handed else "") + f"Do your part now: {spec.mission}"},
        ]
        # A worker with tools must use them. In the first live run three of four
        # workers answered from the model's own memory without one search, and
        # the final answer still called their job titles and links "verified".
        needs_evidence = bool(schemas) or spec.role == VERIFIER or bool(spec.deliverable)
        nudges = 0
        tool_turns = 0
        for _step in range(MAX_STEPS_PER_WORKER + 2):
            if report.cancelled or time.monotonic() >= deadline:
                result.note = 'team stopped' if report.cancelled else "the team's time budget ran out"
                break
            if report.tool_calls >= report.max_tool_calls:
                result.note = "the team's tool-call budget ran out"
                break
            if tool_turns >= MAX_STEPS_PER_WORKER:
                break
            try:
                reply = self.model.generate(messages, tools=schemas or None, temperature=0.2,
                                            max_tokens=3000 if spec.deliverable else 1400)
            except Exception as exc:
                result.note = f"model error: {type(exc).__name__}"
                break
            calls = list(getattr(reply, "tool_calls", None) or [])
            if not calls:
                content = str(getattr(reply, "content", "") or "").strip()
                if schemas and needs_evidence and not result.successful_tools and nudges < 2:
                    nudges += 1
                    messages.append({"role": "assistant", "content": content or "(no answer)"})
                    messages.append({"role": "user", "content": (
                        "You have not used any tool yet, so nothing above is backed by evidence. Call one of your "
                        f"tools now ({', '.join(spec.tools)}) and base your findings on what it returns.")})
                    continue
                result.findings = content
                break
            messages.append({"role": "assistant", "content": str(getattr(reply, "content", "") or "")
                             or f"Calling {', '.join(self._call_name(call) for call in calls)}."})
            tool_turns += 1
            for call in calls[:3]:
                if report.cancelled or time.monotonic() >= deadline or report.tool_calls >= report.max_tool_calls:
                    result.note = 'team stopped' if report.cancelled else "the team's execution budget ran out"
                    break
                name, arguments = self._call_name(call), self._call_arguments(call)
                if name not in spec.tools:
                    observed = f"{name} is not one of your tools; you may use: {', '.join(spec.tools)}."
                else:
                    report.tool_calls += 1
                    result.tools_used.append(name)
                    try:
                        outcome = self.registry.execute(name, arguments)
                        from app.agent.verifier import Verifier
                        checked = Verifier().check({'type': 'tool_success'}, outcome)
                        if checked.passed:
                            result.successful_tools.append(name)
                            result.opened = list(dict.fromkeys([*result.opened, *_source_urls(arguments)]))[:100]
                            raw_output = json.dumps(outcome.output, default=str)
                            observed = raw_output[:8000 if spec.deliverable else 4000]
                            result.sources = list(dict.fromkeys([*result.sources, *_source_urls(outcome.output)]))[:100]
                        else:
                            observed = f"failed: {outcome.error or checked.reason}"[:600]
                            result.note = f'At least one tool failed: {name}'
                    except Exception as exc:
                        observed = f"failed: {type(exc).__name__}"
                        result.note = f'Tool error: {name} ({type(exc).__name__})'
                        log_swallowed(_log, f"worker tool {name}")
                messages.append({"role": "user", "content": f"Result of {name}({json.dumps(arguments)[:300]}):\n"
                                                            f"{observed}"})
        if ((not result.findings or (spec.deliverable and _completion_data(result.findings) is None))
                and not report.cancelled and time.monotonic() < deadline):
            try:
                if result.findings:
                    messages.append({'role': 'assistant', 'content': result.findings})
                messages.append({"role": "user", "content": "Stop using tools. Give your findings now, with sources." + contract})
                kwargs = {'response_schema': WORKER_REPORT_SCHEMA} if spec.deliverable else {}
                reply = self.model.generate(messages, temperature=0.2, max_tokens=3000 if spec.deliverable else 1400, **kwargs)
                result.findings = str(getattr(reply, "content", "") or "").strip()
            except Exception as exc:
                result.note = result.note or f"model error: {type(exc).__name__}"
        result.sources = result.sources[:100]
        result.ok = bool(result.findings)
        if needs_evidence and not result.successful_tools and result.findings:
            # Kept for the record, but not counted as evidence.
            result.ok = False
            result.note = (result.note + "; " if result.note else "") + \
                "answered without using any tool, so its findings are unverified"
            if result.tools_used:
                result.note = 'no tool call returned successful evidence; findings are unverified'
        if report.cancelled or time.monotonic() >= deadline:
            result.ok = False
        if spec.deliverable:
            self._check_deliverable(spec, result, inputs)
        else:
            result.complete = result.ok
        result.seconds = round(time.monotonic() - started, 1)
        return result

    @staticmethod
    def _check_deliverable(spec: WorkerSpec, result: WorkerResult, inputs: list[WorkerResult]) -> None:
        """Keep evidence collection separate from a worker claiming its task is complete."""
        data = _completion_data(result.findings)
        if data is None:
            result.note = (result.note + '; ' if result.note else '') + 'worker did not return its required completion report'
            return
        result.findings = data['findings'].strip()
        result.ok = result.ok and bool(result.findings)
        valid_urls = set(result.sources)
        if spec.role != VERIFIER:
            valid_urls.update(url for item in inputs if item.ok for url in item.sources)
        seen = set()
        coverage = data.get('coverage')
        if isinstance(coverage, list):
            for row in coverage:
                if not isinstance(row, dict):
                    continue
                company, source = row.get('company'), row.get('source')
                if not isinstance(company, str) or not isinstance(source, str):
                    continue
                company, source = ' '.join(company.split()), source.strip()
                key = company.casefold()
                if company and key not in seen and source in valid_urls:
                    seen.add(key)
                    result.coverage.append({'company': company, 'source': source})
        gaps = data.get('gaps')
        valid_gaps = isinstance(gaps, list) and all(isinstance(gap, str) for gap in gaps)
        notes = [gap.strip() for gap in gaps if gap.strip()] if valid_gaps else ['missing or invalid gap report']
        if spec.target_count and len(result.coverage) < spec.target_count:
            plural = 'companies' if (spec.item_label or 'company') == 'company' else f'{spec.item_label}s'
            notes.append(f'Coverage: {len(result.coverage)} of {spec.target_count} requested {plural}; remaining work is incomplete')
        if data.get('completion') != 'complete':
            notes.append('worker reported partial or blocked work')
        if notes:
            result.note = '; '.join(filter(None, [result.note, *notes]))
        result.complete = result.ok and data.get('completion') == 'complete' and valid_gaps and not notes

    def _final_answer(self, report: TeamReport, items: list[WorkerResult] | None = None) -> str:
        """HAYAT's answer from the workers' findings (flat) or the group reports plus the verifier (grouped)."""
        items = list(report.results) if items is None else items
        verified = next((item for item in items if item.role == VERIFIER and item.ok and item.findings), None)

        def evidence(item: WorkerResult) -> str:
            if item.ok and item.successful_tools:
                return (f"(gathered with {', '.join(dict.fromkeys(item.successful_tools))}; "
                        f"{len(item.sources)} source links seen)")
            return "(NO EVIDENCE: no successful evidence-backed result; treat everything it says as unverified)"

        capacity = self.capacity()
        share = capacity.share_chars(sum(bool(item.findings) for item in items))
        material = "\n\n".join(f"{item.role} {evidence(item)}:\n{item.findings[:share]}\n"
                               f"Task complete: {item.complete}; {'item' if item.kind == 'group' else 'company'} "
                               f"coverage: {len(item.coverage)}\n"
                               f"Gaps: {item.note or 'none recorded'}"
                               for item in items if item.findings)
        if not material:
            return "The team could not gather any findings."
        grouped = self._grouped_instructions(report) if report.groups else ""
        prompt = (
            f"You are HAYAT. Your temporary team worked on: {report.goal}\nWrite the final answer for {_owner()} from "
            "their findings. Lead with the answer, use short sections and lists, and keep each fact's source link. "
            "Present something as fact only when it comes from a worker that gathered evidence with a tool and "
            "the Verification Agent did not reject it. Label everything else 'unverified'. Never call a finding "
            "verified unless the Verification Agent checked it with a tool. Never invent a link, job, number or "
            "company. Say plainly what could not be found or verified. Tool evidence alone does not mean a worker "
            "completed its mission. Preserve partial/blocked status and coverage counts; never present a provisional "
            "job match as confirmed suitability when profile evidence is missing.\n\n" + grouped
            + material[:capacity.material_chars()]
            + (f"\n\nVerification Agent's verdict:\n{verified.findings[:3000]}" if verified else "")
        )
        try:
            from app.agent.context import UNTRUSTED_DATA_NOTICE
            prompt += '\n\n' + UNTRUSTED_DATA_NOTICE
            reply = self.model.generate([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=2200)
            return str(getattr(reply, "content", "") or "").strip() or material[:4000]
        except Exception:
            log_swallowed(_log, "writing a team's final answer")
            report.note = 'The final model answer failed; recorded worker findings are shown instead.'
            return material[:4000]

    @staticmethod
    def _grouped_instructions(report: TeamReport) -> str:
        """What the final answer must keep straight about a grouped team (counted by code, not the model)."""
        checked = (f"The Verification Agent re-opened no cited link from: {', '.join(report.unchecked_groups)}; say "
                   "that those groups' claims are sourced but not re-checked. " if report.unchecked_groups
                   else "The Verification Agent re-opened cited links from every group. ")
        omitted = ("Some claims or items were left out of the group reports to fit the window; say the full lists are "
                   "in the saved team report. " if any(group.omitted for group in report.groups) else "")
        return (f"The team worked in {len(report.groups)} groups. Each group report was condensed by its lead and "
                "checked by code: a cited claim's link was returned by a worker's own tool; an 'UNVERIFIED' claim has "
                "no such link and must stay labelled unverified; an '(excerpt)' line was not condensed by the lead. "
                f"Distinct items covered across all groups: {report.coverage_total} (an item two groups covered counts "
                "once; use this number, not a sum of the groups). " + checked + omitted + "\n\n")

    @staticmethod
    def _call_name(call: Any) -> str:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        return str(function.get("name") or call.get("name") or "") if isinstance(call, dict) else ""

    @staticmethod
    def _call_arguments(call: Any) -> dict[str, Any]:
        function = call.get("function", {}) if isinstance(call, dict) else {}
        arguments = function.get("arguments") if function else call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        return arguments if isinstance(arguments, dict) else {}

    # ------------------------------------------------------------- reporting

    def _save(self, report: TeamReport) -> Path | None:
        folder = self.root / "data" / "artifacts" / "teams"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{report.team_id}.json").write_text(json.dumps(report.to_dict(), indent=2, default=str),
                                                           encoding="utf-8")
            path = folder / f"{report.team_id}.md"
            path.write_text(self.markdown(report), encoding="utf-8")
            return path
        except OSError:
            log_swallowed(_log, "saving a team report", level=logging.WARNING)
            return None

    @staticmethod
    def markdown(report: TeamReport) -> str:
        lines = [f"# Team report: {report.goal}", "",
                 f"Team {report.team_id} · {len(report.workers)} temporary workers · {report.seconds:.0f}s · "
                 f"{report.tool_calls} tool calls · plan: {report.planned_by}",
                 "", "## Answer", "", report.answer or "(none)", "", "## The workers", ""]
        for spec in report.workers:
            lines += [f"- {spec.role} · after: {', '.join(spec.depends_on) or 'none'}"
                      + (f" · deliverable: {spec.deliverable}" if spec.deliverable else '')]
        lines += ['']
        for result in report.results:
            lines += [f"### {result.role}", "", f"Mission: {result.mission}",
                      f"Tools allowed: {', '.join(result.tools) or 'none'} · used: "
                      f"{', '.join(dict.fromkeys(result.tools_used)) or 'none'} · {result.seconds:.0f}s"
                      + (f" · {result.note}" if result.note else ""),
                      f"Mission complete: {result.complete} · Company coverage: {len(result.coverage)}",
                      "", result.findings or "(no findings)", ""]
            lines += [f"- {item['company']}: {item['source']}" for item in result.coverage]
            lines += ['']
        if report.groups:
            lines += ["## Groups", "",
                      f"{len(report.groups)} groups · {report.coverage_total} distinct items covered across groups · "
                      f"Verification Agent re-opened cited links from {len(report.verified_groups)} of "
                      f"{len(report.groups)} groups"
                      + (f" (not: {', '.join(report.unchecked_groups)})" if report.unchecked_groups else ""), ""]
            for group in report.groups:
                lines += [f"### Group: {group.name}", "", f"Focus: {group.focus}",
                          f"Members: {', '.join(group.members)} · designed by: {group.designed_by} · condensed by: "
                          f"{group.condensed_by} · {group.seconds:.0f}s", "", "Group report as the verifier read it:",
                          "", group.report or "(none)", "", "Every claim (nothing left out):", ""]
                lines += [f"- [{claim['status']}] {claim['claim']} -- {claim['source'] or 'no link'} "
                          f"({claim['member'] or 'unattributed'})" + (f" · {claim['reason']}" if claim['reason'] else "")
                          for claim in group.claims] or ["- (no claims)"]
                lines += ["", f"Items covered ({len(group.coverage)} distinct):", ""]
                lines += [f"- {row['company']}: {row['source']}" for row in group.coverage] or ["- (none)"]
                lines += [""] + ([f"Gaps: {' | '.join(group.gaps)}", ""] if group.gaps else [])
        return "\n".join(lines)

    @staticmethod
    def summary_text(report: TeamReport) -> str:
        done = sum(1 for result in report.results if result.ok)
        roster = (f"{len(report.groups)} groups: {', '.join(group.name for group in report.groups)}"
                  if report.groups else report.roster())
        header = (f"Team finished: {done} of {len(report.workers)} workers delivered "
                  f"({roster}) in {report.seconds:.0f}s with {report.tool_calls} tool calls.")
        if report.groups:
            header += (f" {report.coverage_total} distinct items covered across the groups (each counted once); the "
                       f"Verification Agent re-opened cited links from {len(report.verified_groups)} of "
                       f"{len(report.groups)} groups.")
        if not report.complete:
            header += ' The goal is incomplete; review the recorded gaps.'
        footer = f"\n\nFull team report: {report.report_path}" if report.report_path else ""
        return f"{header}\n\n{report.answer}" + (f'\n\n{report.note}' if report.note else '') + footer

    def started_text(self, report: TeamReport) -> str:
        if report.groups:
            return self._started_grouped(report)
        lines = [f"I've put together a temporary team of {len(report.workers)} workers for this:"]
        for spec in report.workers:
            after = f" (after {', '.join(spec.depends_on)})" if spec.depends_on and spec.role != VERIFIER else ""
            lines.append(f"- **{spec.role}**: {spec.mission}{after}")
        lines.append("")
        lines.append("They only read and research; none of them can send, submit or change anything. You can "
                     "watch each one under Running now or Team workspace. I'll post their answer and evidence gaps here when they finish.")
        if report.limits:
            minutes = len(report.workers) * report.limits["seconds_per_worker"] / 60
            lines.append(f"They share one brain and work one after another: expect about {minutes:.0f} minutes "
                         f"(budget {report.max_seconds / 60:.0f} min, {report.max_tool_calls} tool calls). This machine "
                         f"can run teams of up to {report.limits['max_workers']} workers"
                         + (f" ({report.limits['grouped_workers']} when organised in groups)."
                            if report.limits.get('grouped_workers', 0) > report.limits['max_workers'] else "."))
        if report.plan_note:
            lines.append(report.plan_note)
        return "\n".join(lines)

    def _started_grouped(self, report: TeamReport) -> str:
        from app.agents.capacity import duration

        members = len(report.workers) - 1
        lines = [f"I've put together a temporary team of {len(report.workers)} workers for this, organised in "
                 f"{len(report.groups)} groups ({members} researchers and the Verification Agent):"]
        for group in report.groups:
            after = f" (after {', '.join(group.depends_on)})" if group.depends_on else ""
            lines.append(f"- **{group.name}** ({len(group.members)} workers): {group.focus}{after}")
        lines.append(f"- **{VERIFIER}**: reads the group reports and re-opens their cited links, at least one per group.")
        lines += ["", "Each group's lead condenses its members' findings into a short report that keeps every "
                      "claim's source link; a claim without a link a worker actually observed is marked unverified, "
                      "never dropped. Items are counted once across groups. They only read and research; none of "
                      "them can send, submit or change anything. Watch them under Running now or Team workspace."]
        if report.limits:
            expected = report.limits.get("expected_seconds") or (
                (len(report.workers) + len(report.groups)) * report.limits["seconds_per_worker"])
            lines.append(f"They share one brain and work one after another (each group lead adds one turn): expect "
                         f"about {duration(expected)} (budget {duration(report.max_seconds)}, {report.max_tool_calls} "
                         f"tool calls). You can cancel it from Team workspace at any time; findings so far are kept.")
        if report.plan_note:
            lines.append(report.plan_note)
        return "\n".join(lines)

    def status(self) -> dict[str, Any]:
        def budgets(item: TeamReport) -> dict[str, Any]:
            # What the GUI needs to show progress against this team's own machine-derived limits.
            return {'started_at': item.started_at, 'max_seconds': item.max_seconds,
                    'max_tool_calls': item.max_tool_calls, 'limits': dict(item.limits),
                    'tool_calls': item.tool_calls, 'note': item.note,
                    'groups': [{'name': group.name, 'members': len(group.members), 'condensed_by': group.condensed_by,
                                'claims': len(group.claims), 'items': len(group.coverage)} for group in item.groups],
                    'coverage_total': item.coverage_total}

        with self._lock:
            running = [{"team_id": item.team_id, "goal": item.goal, "workers": item.roster(),
                        "done": len(item.results), 'total': len(item.workers), 'current_worker': item.current_worker,
                        'tool_calls': item.tool_calls, 'cancelled': item.cancelled,
                        'seconds': _elapsed(item.started_at),
                        'worker_specs': [asdict(spec) for spec in item.workers], 'planned_by': item.planned_by,
                        'results': [asdict(result) for result in item.results], **budgets(item)}
                       for item in self._running.values()]
            recent = [{"team_id": item.team_id, "goal": item.goal, "seconds": item.seconds,
                       "report": item.report_path, 'complete': item.complete,
                       'done': sum(result.ok for result in item.results), 'total': len(item.workers),
                       'worker_specs': [asdict(spec) for spec in item.workers], 'planned_by': item.planned_by,
                       'results': [asdict(result) for result in item.results], **budgets(item)}
                      for item in self._recent]
        return {"running": running, "recent": recent}

    def cancel(self, team_id: str) -> bool:
        """Stop at the next model/tool boundary; an in-flight call may finish first."""
        with self._lock:
            report = self._running.get(team_id)
            if report is None:
                return False
            report.cancelled = True
            return True

    def cancel_all(self) -> None:
        with self._lock:
            for report in self._running.values():
                report.cancelled = True
