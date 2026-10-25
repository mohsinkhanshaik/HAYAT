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
* workers are temporary: they exist for one goal and are gone when it ends.
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
)
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


def capacity_question(text: str) -> bool:
    """"How many agents can you run?" -- answered from the measured capacity, not by the model."""
    value = " ".join(str(text or "").split())
    return len(value) <= 200 and bool(_CAPACITY.search(value))


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

    # ------------------------------------------------------------- capacity

    def capacity(self, *, refresh: bool = False):
        """How big a team this machine can run, measured now (see app.agents.capacity)."""
        from app.agents.capacity import measure

        if refresh or self._capacity is None:
            self._capacity = measure(self.model, reports=self.root / "data" / "artifacts" / "teams")
        return self._capacity

    def _new_report(self, goal: str) -> TeamReport:
        capacity = self.capacity(refresh=True)
        workers, planned_by = self.plan(goal)
        report = TeamReport(f"team-{uuid.uuid4().hex[:8]}", goal, datetime.now(UTC).isoformat(), workers,
                            planned_by=planned_by)
        report.max_seconds = capacity.team_seconds(len(workers))
        report.max_tool_calls = capacity.team_tool_calls(len(workers))
        report.limits = {"max_workers": capacity.max_workers, "context_tokens": capacity.context_tokens,
                         "seconds_per_worker": capacity.seconds_per_worker,
                         "concurrent_teams": capacity.concurrent_teams}
        return report

    # ------------------------------------------------------------- planning

    def available_tools(self) -> list[str]:
        names = set(self.registry.names()) if self.registry is not None else set()
        return [name for name in WORKER_TOOLS if name in names]

    def plan(self, goal: str) -> tuple[list[WorkerSpec], str]:
        """The team for this goal, designed by HAYAT's model, or a fixed fallback when it cannot."""
        tools = self.available_tools()
        career = self._career_plan(goal, tools)
        if career:
            return career, "career workflow"
        designed = self._model_plan(goal, tools) if self.model is not None else None
        if designed:
            return designed, "model"
        return self._fallback_plan(goal, tools), "fallback"

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

    def _model_plan(self, goal: str, tools: list[str]) -> list[WorkerSpec] | None:
        catalog = "\n".join(f"- {name}: {self._describe_tool(name)}" for name in tools)
        prompt = (
            "Design a small team of temporary specialist workers for the goal below. Each worker does one part "
            "and passes its findings on. Use as many workers as the goal genuinely splits into: at least 2 and at "
            f"most {self.capacity().max_workers - 1} on this machine; fewer when the work does not divide. Give each "
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
            with ACTIVITY.track("task", "Worker team", f"{len(report.workers)} workers: {report.goal[:120]}"):
                for spec in report.workers:
                    if report.cancelled or time.monotonic() - started >= report.max_seconds:
                        report.results.append(WorkerResult(spec.role, spec.mission, spec.tools, False, "",
                                                           note="skipped: team stopped" if report.cancelled else "skipped: the team's time budget ran out"))
                        continue
                    report.current_worker = spec.role
                    inputs = [result for result in report.results if result.role in spec.depends_on] or (
                        list(report.results) if spec.role == VERIFIER else [])
                    with ACTIVITY.track("task", spec.role, spec.mission[:160]):
                        report.results.append(self._run_worker(spec, report, inputs, deadline=started + report.max_seconds))
                if report.cancelled or time.monotonic() >= started + report.max_seconds:
                    report.answer = 'Team stopped before the final answer. Review the recorded findings and gaps in its report.'
                else:
                    report.answer = self._final_answer(report)
        finally:
            report.current_worker = ''
            report.finished = True
            report.complete = (not report.cancelled and not report.note and time.monotonic() < started + report.max_seconds
                               and len(report.results) == len(report.workers) and all(item.complete for item in report.results))
            report.seconds = round(time.monotonic() - started, 1)
            saved = self._save(report)
            report.report_path = str(saved) if saved is not None else ''
            with self._lock:
                self._running.pop(report.team_id, None)
                self._recent = ([report] + self._recent)[:10]

    def _run_worker(self, spec: WorkerSpec, report: TeamReport, inputs: list[WorkerResult], *, deadline=float('inf')) -> WorkerResult:
        started = time.monotonic()
        result = WorkerResult(spec.role, spec.mission, spec.tools, False, "")
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
        handed ="\n\n".join(f"Findings from {item.role} ({'evidence gathered' if item.ok else 'UNVERIFIED'}):\n"
                              f"{item.findings[:share]}\nSources: {', '.join(item.sources)}\n"
                              f"Coverage: {json.dumps(item.coverage)}\n"
                              f"Task complete: {item.complete}\nGaps: {item.note or 'none recorded'}"
                              for item in inputs)
        contract = ''
        if spec.deliverable:
            contract = (f' Your required deliverable: {spec.deliverable} When finished, return JSON only with '
                        '"findings" (a readable summary with citations), "completion" ("complete", "partial" or "blocked"), '
                        '"coverage" (a list of {"company": "name", "source": "exact observed URL"}), and "gaps" '
                        '(a list of unresolved issues). Each coverage row means that you completed your own mission for '
                        'that company, not merely that its name was mentioned. List each company once. Unsupported or '
                        'unchecked companies do not count. Mark partial if any requested work or profile evidence is missing. '
                        + (f'The coverage target is {spec.target_count} distinct companies.' if spec.target_count else ''))
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
            notes.append(f'Coverage: {len(result.coverage)} of {spec.target_count} requested companies; remaining work is incomplete')
        if data.get('completion') != 'complete':
            notes.append('worker reported partial or blocked work')
        if notes:
            result.note = '; '.join(filter(None, [result.note, *notes]))
        result.complete = result.ok and data.get('completion') == 'complete' and valid_gaps and not notes

    def _final_answer(self, report: TeamReport) -> str:
        verified = next((item for item in report.results if item.role == VERIFIER and item.ok and item.findings), None)

        def evidence(item: WorkerResult) -> str:
            if item.ok and item.successful_tools:
                return (f"(gathered with {', '.join(dict.fromkeys(item.successful_tools))}; "
                        f"{len(item.sources)} source links seen)")
            return "(NO EVIDENCE: no successful evidence-backed result; treat everything it says as unverified)"

        capacity = self.capacity()
        share = capacity.share_chars(sum(bool(item.findings) for item in report.results))
        material = "\n\n".join(f"{item.role} {evidence(item)}:\n{item.findings[:share]}\n"
                               f"Task complete: {item.complete}; company coverage: {len(item.coverage)}\n"
                               f"Gaps: {item.note or 'none recorded'}"
                               for item in report.results if item.findings)
        if not material:
            return "The team could not gather any findings."
        prompt = (
            f"You are HAYAT. Your temporary team worked on: {report.goal}\nWrite the final answer for {_owner()} from "
            "their findings. Lead with the answer, use short sections and lists, and keep each fact's source link. "
            "Present something as fact only when it comes from a worker that gathered evidence with a tool and "
            "the Verification Agent did not reject it. Label everything else 'unverified'. Never call a finding "
            "verified unless the Verification Agent checked it with a tool. Never invent a link, job, number or "
            "company. Say plainly what could not be found or verified. Tool evidence alone does not mean a worker "
            "completed its mission. Preserve partial/blocked status and coverage counts; never present a provisional "
            "job match as confirmed suitability when profile evidence is missing.\n\n" + material[:capacity.material_chars()]
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
        return "\n".join(lines)

    @staticmethod
    def summary_text(report: TeamReport) -> str:
        done = sum(1 for result in report.results if result.ok)
        header = (f"Team finished: {done} of {len(report.workers)} workers delivered "
                  f"({report.roster()}) in {report.seconds:.0f}s with {report.tool_calls} tool calls.")
        if not report.complete:
            header += ' The goal is incomplete; review the recorded gaps.'
        footer = f"\n\nFull team report: {report.report_path}" if report.report_path else ""
        return f"{header}\n\n{report.answer}" + (f'\n\n{report.note}' if report.note else '') + footer

    def started_text(self, report: TeamReport) -> str:
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
                         f"can run teams of up to {report.limits['max_workers']} workers.")
        return "\n".join(lines)

    def status(self) -> dict[str, Any]:
        def budgets(item: TeamReport) -> dict[str, Any]:
            # What the GUI needs to show progress against this team's own machine-derived limits.
            return {'started_at': item.started_at, 'max_seconds': item.max_seconds,
                    'max_tool_calls': item.max_tool_calls, 'limits': dict(item.limits),
                    'tool_calls': item.tool_calls, 'note': item.note}

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
