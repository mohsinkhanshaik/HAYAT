"""Grouped worker teams: more workers than one flat team, with every claim's source kept and checked.

All offline: a scripted fake brain and a fake tool registry; the real brain is never started.
"""
from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

import pytest

from app.agents import capacity as capacity_module
from app.agents.capacity import (GROUP_HEADER_CHARS, MIN_DIGEST_CHARS, MIN_WORKER_CHARS, TeamCapacity, measure)
from app.agents.team import (AS_MANY_AS_FIT, GROUP_DELIVERABLE, VERIFIER, WORKER_TOOLS, WorkerResult, WorkerTeam,
                             capacity_question, requested_workers, wants_team)
from app.agents.team_groups import CITED, EXCERPT, UNVERIFIED, TeamGroup, condense
from app.tools.registry import ToolResult

WRITE_TOOLS = {"send_message", "submit_form", "write_file", "delete_file"}


def machine():
    return SimpleNamespace(gpu=SimpleNamespace(total_bytes=8 * 1024**3), available_ram_bytes=16 * 1024**3,
                           cpu_count=32, summary=lambda: "Running on a test machine.")


#: This machine's GUI default (Balanced) window: 7 flat workers, 37 grouped.
CAP = TeamCapacity(8192, 7, 1.0, 0, 4, "test machine", ["My brain's context window is 8,192 tokens."])


@pytest.fixture(autouse=True)
def fixed_capacity(monkeypatch):
    monkeypatch.setattr(WorkerTeam, "capacity", lambda self, refresh=False: CAP)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Registry:
    def __init__(self, on_call=None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.on_call = on_call

    def names(self):
        return [*WORKER_TOOLS, *WRITE_TOOLS]

    def get(self, name):
        return SimpleNamespace(description=f"{name} tool",
                               model_schema=lambda: {"type": "function", "function": {"name": name}})

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        if name == "public_web_fetch":
            return ToolResult(name, True, {"url": arguments.get("url", ""), "text": "The page supports the claim."})
        query = str(arguments.get("query") or "x")
        return ToolResult(name, True, {"results": [{"title": query, "url": f"https://src.test/{slug(query)}"}]})


class GroupBrain:
    """Scripted brain: designs groups and members, works, condenses as a lead, verifies, answers."""

    def __init__(self, *, design_groups=True, design_members=True, lead="good", verifier_skips=0,
                 member_tools=None) -> None:
        self.design_groups, self.design_members, self.lead = design_groups, design_members, lead
        self.verifier_skips = verifier_skips      # how many groups' links the verifier leaves unopened
        self.member_tools = member_tools or ["web_search"]
        self.prompts: list[str] = []
        self.lead_prompts: list[str] = []
        self.final_prompt = ""
        self.lock = threading.Lock()

    def generate(self, messages, tools=None, temperature=None, response_schema=None, max_tokens=None):
        last = str(messages[-1]["content"])
        with self.lock:
            self.prompts.append(last)
        props = (response_schema or {}).get("properties", {})
        if "groups" in props:
            if not self.design_groups:
                return SimpleNamespace(content="not json", tool_calls=[])
            count = int(re.search(r"exactly (\d+) groups", last).group(1))
            rows = [{"name": f"Region {chr(65 + index)}", "focus": f"Chip startups in region {chr(65 + index)}.",
                     "depends_on": []} for index in range(count)]
            return SimpleNamespace(content=json.dumps({"groups": rows}), tool_calls=[])
        if "workers" in props and "You organise the" in last:
            if not self.design_members:
                raise RuntimeError("model unavailable")
            size = int(re.search(r"between exactly (\d+) workers", last).group(1))
            group = re.search(r"organise the '([^']+)' group", last).group(1)
            rows = [{"role": f"{group} Scout {index} Agent", "mission": f"Find chip startups, slice {index}.",
                     "tools": [*self.member_tools, "send_message"]} for index in range(1, size + 1)]
            return SimpleNamespace(content=json.dumps({"workers": rows}), tool_calls=[])
        if "claims" in props:
            with self.lock:
                self.lead_prompts.append(last)
            return SimpleNamespace(content=self._lead_reply(last), tool_calls=[])
        if "findings" in props:
            return SimpleNamespace(content=self._member_report(messages), tool_calls=[])
        if tools:
            role = re.match(r"You are the (.+?), a temporary worker", str(messages[0]["content"])).group(1)
            if role == VERIFIER:
                return self._verify(messages)
            if not any("Result of" in str(item.get("content")) for item in messages):
                return SimpleNamespace(content="", tool_calls=[{"function": {
                    "name": tools[0]["function"]["name"], "arguments": {"query": role}}}])
            return SimpleNamespace(content=self._member_report(messages), tool_calls=[])
        if "worked on" in last:
            self.final_prompt = last
            return SimpleNamespace(content="Final answer from the group reports.", tool_calls=[])
        return SimpleNamespace(content="(nothing)", tool_calls=[])

    @staticmethod
    def _member_report(messages) -> str:
        role = re.match(r"You are the (.+?), a temporary worker", str(messages[0]["content"])).group(1)
        url = f"https://src.test/{slug(role)}"
        shared = "Shared Corp" if len(role) % 2 else "  shared CORP "
        return json.dumps({
            "findings": f"{role} found Item-{slug(role)} designs inference chips ({url}). DETAIL-{slug(role)} "
                        "is a long secondary detail only the group lead needs to read.",
            "completion": "complete",
            "coverage": [{"company": f"Item {slug(role)}", "source": url}, {"company": shared, "source": url}],
            "gaps": []})

    def _lead_reply(self, prompt: str) -> str:
        if self.lead == "broken":
            return "I could not do it"
        blocks = re.findall(r"^\[(.+?)\] \(evidence gathered.*?\n.*?Observed source links: (\S+?)[,\s]", prompt,
                            flags=re.MULTILINE | re.DOTALL)
        claims = [{"claim": f"Item-{slug(role)} designs inference chips", "source": url, "member": role}
                  for role, url in blocks]
        if self.lead == "skip-first" and claims:
            claims = claims[1:]
        if self.lead == "tricky" and blocks:
            claims += [{"claim": "Acme raised $9B last week", "source": "https://invented.example/acme",
                        "member": blocks[0][0]},
                       {"claim": "Everyone is hiring", "source": "", "member": blocks[0][0]}]
        return json.dumps({"summary": "Chip startups found by this group.", "claims": claims})

    def _verify(self, messages):
        reports = str(messages[1]["content"])
        firsts = []
        for section in reports.split("Group report from the ")[1:]:
            urls = re.findall(r" -- (https://\S+)", section)
            if urls:
                firsts.append(urls[0])
        wanted = firsts[: max(0, len(firsts) - self.verifier_skips)]
        opened = [json.loads(re.search(r"Result of public_web_fetch\((.*)\):", str(item["content"])).group(1))["url"]
                  for item in messages if "Result of public_web_fetch" in str(item.get("content"))]
        todo = [url for url in wanted if url not in opened][:3]
        if todo:
            return SimpleNamespace(content="", tool_calls=[{"function": {"name": "public_web_fetch",
                                                                         "arguments": {"url": url}}} for url in todo])
        return SimpleNamespace(content="Checked one cited claim per group; the pages support them.", tool_calls=[])


# ------------------------------------------------------------------ capacity arithmetic


@pytest.mark.parametrize("window", [8192, 16384, 32768, 65536])
def test_the_grouped_limit_is_computed_and_every_part_fits_its_window(window):
    capacity = measure(SimpleNamespace(num_ctx=window), hardware_profile=machine())
    flat, grouped = capacity.max_workers, capacity.grouped_workers
    assert grouped > flat >= 3
    sizes = capacity.layout(grouped)
    assert sum(sizes) == grouped - 1 and len(sizes) >= 2
    for size in sizes:
        assert size <= capacity.members_per_group
        # The lead reads every member at a useful size...
        assert capacity.lead_share_chars(size) >= MIN_WORKER_CHARS
        assert size * MIN_WORKER_CHARS <= capacity._lead_chars
        # ...and the group report holds one cited claim per member inside its share of the top window.
        assert size * MIN_DIGEST_CHARS + GROUP_HEADER_CHARS <= capacity.group_report_chars(len(sizes))
    # Group reports fit what the verifier and the final answer can read.
    assert len(sizes) * capacity.group_share_chars(len(sizes)) <= capacity._verifier_chars
    assert (len(sizes) + 1) * capacity.group_share_chars(len(sizes)) <= capacity._final_chars
    # One more worker does not fit: the limit is the arithmetic, not a fixed multiple.
    assert capacity.group_sizes(grouped) is None


def test_this_machines_windows_give_the_documented_numbers():
    numbers = {}
    for window in (8192, 16384):
        capacity = measure(SimpleNamespace(num_ctx=window), hardware_profile=machine())
        numbers[window] = (capacity.max_workers, capacity.grouped_workers, capacity.layout(capacity.grouped_workers))
    assert numbers[8192] == (7, 37, [12, 12, 12])
    assert numbers[16384] == (34, 183, [26] * 7)


def test_a_tiny_window_stays_flat_and_says_so():
    capacity = measure(SimpleNamespace(num_ctx=4096), hardware_profile=machine())
    assert capacity.grouped_workers == capacity.max_workers == capacity.team_limit
    assert capacity.layout(capacity.max_workers + 5) == []
    assert "stay flat" in capacity.explain()


def test_small_teams_keep_the_flat_layout_and_budgets_scale_with_size():
    assert CAP.layout(CAP.max_workers) == []
    assert CAP.layout(CAP.max_workers + 1) != []
    paced = TeamCapacity(8192, 7, 90.0, 3, 4, "test machine", [])   # a measured 90 s per worker
    big = paced.grouped_workers
    groups = len(paced.layout(big))
    assert paced.team_seconds(big, groups) > paced.team_seconds(big) > paced.team_seconds(paced.max_workers)
    assert paced.team_seconds(big, groups) == (big + groups) * 90.0 * 2 + 300
    assert paced.team_tool_calls(big) == big * capacity_module.STEPS_PER_WORKER + 10
    assert paced.expected_seconds(big) == (big + groups) * 90.0
    assert "hours" in capacity_module.duration(paced.expected_seconds(big)) or big * 90 < 5400


def test_the_how_many_agents_answer_states_flat_and_grouped_limits_with_reasons():
    capacity = measure(SimpleNamespace(num_ctx=16384), hardware_profile=machine())
    text = capacity.explain(workers=10)
    assert text.startswith("I can run teams of up to 183 workers")
    assert "up to 34 as one flat team" in text and "7 groups" in text
    assert "condenses them into a cited group report" in text and "marked unverified" in text
    assert "The largest team, 183 workers, would take about" in text and "hours" in text
    assert capacity.to_dict()["grouped_layout"] == [26] * 7


def test_hayat_answers_how_many_agents_with_both_limits(tmp_path, monkeypatch):
    from tests.test_assistant_service import runtime

    monkeypatch.undo()                            # the real measurement for this question
    rt = runtime(tmp_path)
    rt.teams = WorkerTeam(SimpleNamespace(num_ctx=16384), SimpleNamespace(names=lambda: []), root=tmp_path)
    answer = rt.assistant.handle("how many agents can you run?").display_response
    assert answer.startswith("I can run teams of up to 183 workers")
    assert "34 as one flat team" in answer and "16,384 tokens" in answer
    assert rt.manager.calls == []


# ------------------------------------------------------------------ requests and planning


def test_requested_team_sizes_are_read_and_still_start_a_team():
    assert requested_workers("Use a team of 60 agents to research AI chip startups") == 60
    assert requested_workers("use 40 research workers to map EU battery suppliers") == 40
    assert requested_workers("use the biggest team you can to research chip startups") == AS_MANY_AS_FIT
    assert requested_workers("I have 3 workers in my shop") is None
    assert wants_team("use 40 research workers to map EU battery suppliers")
    assert wants_team("Use a team of 60 agents to research AI chip startups")
    assert not capacity_question("use the maximum number of agents to research AI chips")
    assert capacity_question("how many agents can you run") and capacity_question("what is your agent limit")


def test_a_team_that_fits_stays_flat(tmp_path):
    team = WorkerTeam(GroupBrain(), Registry(), root=tmp_path)
    report = team._new_report("use 5 agents to compare GPU cloud providers")
    assert report.groups == [] and not any(spec.group for spec in report.workers)
    assert report.max_seconds == CAP.team_seconds(len(report.workers))


def test_a_large_request_is_planned_as_groups_of_read_only_workers(tmp_path):
    team = WorkerTeam(GroupBrain(), Registry(), root=tmp_path)
    report = team._new_report("Use a team of 20 agents to research AI chip startups and compare them")
    assert report.planned_by == "grouped: model"
    assert [group.name for group in report.groups] == ["Region A", "Region B"]
    assert [len(group.members) for group in report.groups] == [10, 9]
    assert len(report.workers) == 20 and report.workers[-1].role == VERIFIER
    assert report.workers[-1].depends_on == ["Region A", "Region B"]
    members = report.workers[:-1]
    assert all(spec.deliverable == GROUP_DELIVERABLE and spec.item_label == "item" for spec in members)
    tools = {tool for spec in report.workers for tool in spec.tools}
    assert tools <= set(WORKER_TOOLS) and not tools & WRITE_TOOLS
    assert len({spec.role.casefold() for spec in report.workers}) == 20
    assert report.max_seconds == CAP.team_seconds(20, 2)
    assert report.max_tool_calls == CAP.team_tool_calls(20)
    started = team.started_text(report)
    assert "organised in 2 groups" in started and "**Region A** (10 workers)" in started
    assert "expect about" in started and "never dropped" in started


def test_asking_for_more_than_fits_is_clamped_and_explained(tmp_path):
    team = WorkerTeam(GroupBrain(), Registry(), root=tmp_path)
    report = team._new_report("Use a team of 500 agents to research AI chip startups")
    assert len(report.workers) == CAP.grouped_workers == 37
    assert "You asked for 500 workers" in report.plan_note and "at most 37" in report.plan_note
    assert "You asked for 500 workers" in team.started_text(report)


def test_when_the_model_cannot_design_groups_numbered_groups_still_work(tmp_path):
    team = WorkerTeam(GroupBrain(design_groups=False), Registry(), root=tmp_path)
    report = team._new_report("Use a team of 12 agents to research AI chip startups")
    assert report.planned_by == "grouped: fallback"
    assert [group.name for group in report.groups] == ["Group 1", "Group 2"]
    assert all(spec.tools for spec in report.workers)


# ------------------------------------------------------------------ condensation keeps sources


def member(role, url, *, ok=True, coverage=(), findings=None, note=""):
    return WorkerResult(role, "mission", ["web_search"], ok,
                        findings if findings is not None else f"{role} found something ({url})",
                        sources=[url] if ok else [], successful_tools=["web_search"] if ok else [],
                        coverage=list(coverage), complete=ok, note=note)


def test_condensing_keeps_every_link_and_marks_unsourced_claims_unverified():
    group = TeamGroup("Region A", "Chip startups")
    members = [member("A1 Agent", "https://a.test/1", coverage=[{"company": "Acme", "source": "https://a.test/1"}]),
               member("A2 Agent", "https://a.test/2", coverage=[{"company": " ACME ", "source": "https://a.test/2"},
                                                                {"company": "Beta", "source": "https://a.test/2"}]),
               member("A3 Agent", "https://a.test/3"),
               member("A4 Agent", "", ok=False, findings="Gamma is huge (no tool used)", note="answered without tools"),
               member("A5 Agent", "", ok=False, findings="", note="skipped: team stopped")]
    lead = {"summary": "Three startups.", "claims": [
        {"claim": "Acme designs chips", "source": "https://a.test/1/", "member": "a1 agent"},
        {"claim": "Beta ships in 2027", "source": "https://a.test/2", "member": "A2 Agent"},
        {"claim": "Acme raised $9B", "source": "https://invented.example/acme", "member": "A1 Agent"},
        {"claim": "Everyone is hiring", "source": "", "member": ""}]}
    condense(group, members, lead, budget=4000)
    status = {claim["claim"]: claim for claim in group.claims}
    assert status["Acme designs chips"]["status"] == CITED
    assert status["Acme designs chips"]["source"] == "https://a.test/1"     # the exact observed link
    assert status["Beta ships in 2027"]["status"] == CITED
    assert status["Acme raised $9B"]["status"] == UNVERIFIED and "not returned" in status["Acme raised $9B"]["reason"]
    assert status["Everyone is hiring"]["status"] == UNVERIFIED
    # The lead left A3 and A4 out: code adds them, A4 as unverified (it had no tool evidence).
    a3 = next(claim for claim in group.claims if claim["member"] == "A3 Agent")
    a4 = next(claim for claim in group.claims if claim["member"] == "A4 Agent")
    assert a3["status"] == EXCERPT and a3["source"] == "https://a.test/3"
    assert a4["status"] == UNVERIFIED and a4["source"] == ""
    assert not any(claim["member"] == "A5 Agent" for claim in group.claims)
    assert any("A5 Agent: no findings" in gap for gap in group.gaps)
    # Items merged by name: Acme once.
    assert [row["company"] for row in group.coverage] == ["Acme", "Beta"]
    text = group.report
    for needle in ("https://a.test/1", "https://a.test/2", "https://a.test/3", "UNVERIFIED: Acme raised $9B",
                   "UNVERIFIED: Everyone is hiring", "Items covered: 2 distinct", "condensed by lead"):
        assert needle in text
    assert group.condensed_by == "lead" and not group.omitted


def test_a_full_group_keeps_one_cited_line_per_member_at_its_real_budget():
    size = CAP.layout(CAP.grouped_workers)[0]
    budget = CAP.group_report_chars(len(CAP.layout(CAP.grouped_workers)))
    members = [member(f"Region A Scout {index} Agent", f"https://source-{index}.example.org/startups/profile-{index}",
                      coverage=[{"company": f"Startup {index}", "source":
                                 f"https://source-{index}.example.org/startups/profile-{index}"}])
               for index in range(size)]
    lead = {"summary": "x" * 400, "claims": [
        {"claim": f"Startup {index} designs low-power inference accelerators for edge devices " * 3,
         "source": item.sources[0], "member": item.role} for index, item in enumerate(members)]}
    group = TeamGroup("Region A", "Chip startups")
    condense(group, members, lead, budget=budget)
    assert len(group.report) <= budget
    for item in members:
        assert item.sources[0] in group.report
    # What did not fit is counted, not silently cut, and kept in full on the group.
    assert group.omitted and "Left out here to fit" in group.report
    assert len(group.claims) == size and len(group.coverage) == size


def test_a_failed_lead_is_condensed_by_code_without_losing_links():
    members = [member("A1 Agent", "https://a.test/1"), member("A2 Agent", "https://a.test/2")]
    group = TeamGroup("Region A", "Chip startups")
    condense(group, members, None, budget=3000, why="the lead's model call failed: RuntimeError")
    assert group.condensed_by.startswith("code (the lead's model call failed")
    assert "https://a.test/1" in group.report and "https://a.test/2" in group.report
    assert all(claim["status"] == EXCERPT for claim in group.claims)


# ------------------------------------------------------------------ whole teams with a fake brain


def test_the_largest_team_runs_end_to_end_reads_group_reports_and_verifies_every_group(tmp_path):
    brain, registry = GroupBrain(lead="tricky"), Registry()
    team = WorkerTeam(brain, registry, root=tmp_path)
    report = team.run("Use the biggest team you can to research AI chip startups and compare them")
    assert len(report.workers) == 37 and len(report.groups) == 3
    assert report.finished and report.complete, [r.note for r in report.results if not r.complete]
    assert len(report.results) == 37 and all(result.ok for result in report.results)
    # Read-only: nothing but worker tools was ever called; the model's send_message was filtered out.
    assert {name for name, _ in registry.calls} <= set(WORKER_TOOLS)
    assert not any(tool in WRITE_TOOLS for spec in report.workers for tool in spec.tools)
    assert report.tool_calls == len(registry.calls) <= report.max_tool_calls
    # Members were designed by the model when their group began.
    assert all(group.designed_by.startswith("model") for group in report.groups)
    assert all(" Scout " in role for group in report.groups for role in group.members)
    # Every group was condensed by its lead, keeps every member's link, and kept the bad claims as unverified.
    for group in report.groups:
        assert group.condensed_by == "lead"
        members = [result for result in report.results if result.group == group.name]
        assert len(members) == len(group.members) == 12
        for result in members:
            assert result.sources[0] in group.report
        assert "UNVERIFIED: Acme raised $9B" in group.report and "UNVERIFIED: Everyone is hiring" in group.report
    # The verifier read group reports (not raw findings) and re-opened a cited link from every group.
    verifier_prompt = next(prompt for prompt in brain.prompts if "Group report from the Region A group" in prompt)
    assert "Findings from" not in verifier_prompt
    assert report.verified_groups == ["Region A", "Region B", "Region C"] and not report.unchecked_groups
    # Distinct items across groups: one per member plus "Shared Corp", which every member covered, once.
    assert report.coverage_total == 36 + 1
    # The final answer read the group reports and the counted totals, not every raw finding.
    assert "Distinct items covered across all groups: 37" in brain.final_prompt
    assert "Region A group" in brain.final_prompt and "DETAIL-" not in brain.final_prompt
    assert report.answer == "Final answer from the group reports."
    summary = team.summary_text(report)
    assert "37 distinct items covered across the groups" in summary and "3 of 3 groups" in summary
    saved = (tmp_path / "data" / "artifacts" / "teams" / f"{report.team_id}.md").read_text(encoding="utf-8")
    assert "## Groups" in saved and "Every claim (nothing left out)" in saved and "[unverified] Acme raised $9B" in saved
    data = json.loads((tmp_path / "data" / "artifacts" / "teams" / f"{report.team_id}.json").read_text(encoding="utf-8"))
    assert len(data["groups"]) == 3 and data["coverage_total"] == 37


def test_a_group_the_verifier_did_not_check_keeps_the_team_incomplete(tmp_path):
    brain = GroupBrain(verifier_skips=1)
    report = WorkerTeam(brain, Registry(), root=tmp_path).run("Use a team of 20 agents to research AI chip startups")
    assert report.finished and not report.complete
    assert report.verified_groups == ["Region A"] and report.unchecked_groups == ["Region B"]
    assert "re-opened no cited link from: Region B" in brain.final_prompt


def test_a_failing_lead_and_member_design_fall_back_to_code_and_placeholders(tmp_path):
    brain = GroupBrain(lead="broken", design_members=False)
    report = WorkerTeam(brain, Registry(), root=tmp_path).run("Use a team of 12 agents to research AI chip startups")
    assert report.finished
    for group in report.groups:
        assert group.designed_by.startswith("placeholder")
        assert group.condensed_by.startswith("code (the lead's reply was not a valid condensed report")
        for result in report.results:
            if result.group == group.name:
                assert result.sources[0] in group.report


def test_a_lead_that_leaves_a_member_out_cannot_drop_it(tmp_path):
    brain = GroupBrain(lead="skip-first")
    report = WorkerTeam(brain, Registry(), root=tmp_path).run("Use a team of 12 agents to research AI chip startups")
    for group in report.groups:
        first = group.members[0]
        assert any(claim["member"] == first and claim["status"] == EXCERPT for claim in group.claims)


def test_cancelling_a_grouped_team_stops_new_calls_and_keeps_what_was_found(tmp_path):
    holder = {}
    registry = Registry(on_call=lambda count: count == 5 and holder["team"].cancel_all())
    brain = GroupBrain()
    team = WorkerTeam(brain, registry, root=tmp_path)
    holder["team"] = team
    report = team.run("Use a team of 20 agents to research AI chip startups")
    assert report.cancelled and report.finished and not report.complete
    assert len(registry.calls) == 5
    assert len(report.results) == len(report.workers)
    assert sum(result.ok for result in report.results) == 4
    assert all("skipped" in result.note for result in report.results[5:])
    first = report.groups[0]
    assert first.condensed_by == "code (team stopped)"
    for result in report.results[:4]:
        assert result.sources[0] in first.report
    assert not brain.lead_prompts and not brain.final_prompt
    assert "Team stopped before the final answer" in report.answer


def test_a_grouped_team_respects_its_time_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(TeamCapacity, "team_seconds", lambda self, workers, groups=0: 0.0)
    report = WorkerTeam(GroupBrain(), Registry(), root=tmp_path).run("Use a team of 12 agents to research chips")
    assert report.finished and not report.complete
    assert all(not result.ok for result in report.results)
    assert all(group.condensed_by.startswith("code") for group in report.groups)


def test_a_background_grouped_team_reports_back(tmp_path):
    delivered, done = [], threading.Event()
    team = WorkerTeam(GroupBrain(), Registry(), root=tmp_path,
                      deliver=lambda report, text: (delivered.append(text), done.set()))
    report = team.start("Use a team of 12 agents to research AI chip startups")
    assert done.wait(20)
    assert delivered[0].startswith("Team finished: 12 of 12 workers delivered (2 groups: Region A, Region B)")
    status = team.status()["recent"][0]
    assert status["team_id"] == report.team_id and [row["members"] for row in status["groups"]] == [6, 5]
