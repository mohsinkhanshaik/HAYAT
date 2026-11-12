"""How big a worker team this machine can actually run, worked out rather than fixed.

Teams used to be capped at six workers, 40 tool calls and 20 minutes, whatever
the machine. The cap also hid a real limit: HAYAT's final answer read only the
first 24,000 characters of all findings, so with more workers the last ones
were silently dropped.

What actually limits a team on this machine:

* **The brain's context window.** The Verification Agent and HAYAT's final
  answer must read every worker's findings at once. The window (num_ctx) is
  set from the GPU's VRAM and free RAM, so this is the hardware limit. Each
  worker needs a useful minimum share of it; the number of workers is how many
  such shares fit beside the instructions and the answer.
* **Time.** Workers share one local brain and think one after another, so
  more workers make a team take longer, not think in parallel. Each worker's
  time is measured from this machine's past team runs, and a team's time and
  tool-call budgets grow with its size.
* **CPU.** Teams that run at the same time slow each other down on the one
  brain; how many may run at once follows the CPU count.

Everything here is measured or derived, and explained in plain words, so HAYAT
can say how many workers it can run and why.

**Grouped teams.** A flat team is limited because the Verification Agent and the
final answer read every worker's findings at MIN_WORKER_CHARS or more each.
Beyond that limit workers are organised in groups (app/agents/team_groups.py):
each group's lead reads its members' full findings in its own window and
condenses them into a cited group report, and the Verification Agent and the
final answer read the group reports instead. A worker then needs only
MIN_DIGEST_CHARS of the top window (at least one cited claim with its link),
plus a fixed header per group, so more workers fit. The number is computed
here from the window (see grouped_workers); it is not a fixed multiple.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.context_budget import CHARS_PER_TOKEN

#: Tokens kept for the final answer, the instructions around the findings, and
#: the Verification Agent's verdict quoted into the final prompt.
FINAL_ANSWER_TOKENS, INSTRUCTION_TOKENS, VERDICT_TOKENS = 2200, 1500, 750
#: A verifier also holds its own tool results while it checks (5 steps).
VERIFIER_TOOL_TOKENS, VERIFIER_ANSWER_TOKENS = 3000, 1400
#: Below this a worker's findings stop meaning anything (about 300 tokens).
MIN_WORKER_CHARS = 1200
#: No single worker needs more of the window than this.
MAX_WORKER_CHARS = 6000
#: Until this machine has run teams, assume a worker takes this long.
DEFAULT_WORKER_SECONDS = 90.0
STEPS_PER_WORKER = 5

#: In a grouped team the Verification Agent and the final answer see each worker
#: only through its group report: at least one cited claim with its source link
#: (about 50 tokens). The worker's full findings are read by its group lead.
MIN_DIGEST_CHARS = 200
#: Each group report's fixed lines: name, counts and focus (up to ~250), the item
#: count and "left out to fit" lines (~210) and section titles.
GROUP_HEADER_CHARS = 500
#: The labels the verifier and the final answer put around each group report.
GROUP_WRAP_CHARS = 150
#: A group lead's condensed answer is at most one worker's largest share.
LEAD_ANSWER_TOKENS = MAX_WORKER_CHARS // CHARS_PER_TOKEN
#: The Verification Agent opens at most 3 links in each of its 5 steps, so a
#: team never has more groups than it can check at least one link from.
MAX_GROUPS = STEPS_PER_WORKER * 3


@dataclass
class TeamCapacity:
    context_tokens: int
    max_workers: int
    seconds_per_worker: float
    measured_from_runs: int
    concurrent_teams: int
    hardware: str
    reasons: list[str] = field(default_factory=list)

    @property
    def _final_chars(self) -> int:
        return max(0, (self.context_tokens - FINAL_ANSWER_TOKENS - INSTRUCTION_TOKENS - VERDICT_TOKENS)
                   * CHARS_PER_TOKEN)

    @property
    def _verifier_chars(self) -> int:
        return max(0, (self.context_tokens - VERIFIER_ANSWER_TOKENS - INSTRUCTION_TOKENS - VERIFIER_TOOL_TOKENS)
                   * CHARS_PER_TOKEN)

    def share_chars(self, workers: int, *, for_verifier: bool = False) -> int:
        """Each worker's share of the window when `workers` findings are read together."""
        budget = self._verifier_chars if for_verifier else self._final_chars
        return max(MIN_WORKER_CHARS, min(MAX_WORKER_CHARS, budget // max(1, workers)))

    def material_chars(self) -> int:
        """All findings together in the final answer's prompt."""
        return max(MIN_WORKER_CHARS, self._final_chars)

    # ------------------------------------------------------------ grouped teams

    @property
    def _lead_chars(self) -> int:
        """What a group lead can read: its window less its instructions and its condensed answer."""
        return max(0, (self.context_tokens - INSTRUCTION_TOKENS - LEAD_ANSWER_TOKENS) * CHARS_PER_TOKEN)

    @property
    def members_per_group(self) -> int:
        """Most members one lead can read at MIN_WORKER_CHARS each and still digest into one report."""
        by_window = self._lead_chars // MIN_WORKER_CHARS
        by_report = (MAX_WORKER_CHARS - GROUP_HEADER_CHARS - GROUP_WRAP_CHARS) // MIN_DIGEST_CHARS
        return max(0, min(by_window, by_report))

    def lead_share_chars(self, members: int) -> int:
        """Each member's share of its lead's window."""
        return max(MIN_WORKER_CHARS, min(MAX_WORKER_CHARS, self._lead_chars // max(1, members)))

    def group_share_chars(self, groups: int) -> int:
        """Room for one group report when the verifier reads `groups` reports and the final answer
        reads them plus the verifier's own findings."""
        groups = max(1, groups)
        return min(self._verifier_chars // groups, self._final_chars // (groups + 1), MAX_WORKER_CHARS)

    def group_report_chars(self, groups: int) -> int:
        """The text budget of one group report (its share less the labels put around it)."""
        return max(0, self.group_share_chars(groups) - GROUP_WRAP_CHARS)

    def group_sizes(self, members: int) -> list[int] | None:
        """The fewest groups that hold `members` workers within every window, as balanced sizes.

        None when they do not fit. Every group report must hold a header plus
        MIN_DIGEST_CHARS per member, inside the share the verifier and the final
        answer can give it; every lead must read its members at MIN_WORKER_CHARS.
        """
        per_lead = self.members_per_group
        if members < 2 or per_lead < 1:
            return None
        for groups in range(2, MAX_GROUPS + 1):
            largest = math.ceil(members / groups)
            if largest > per_lead or largest < 1:
                continue
            if largest * MIN_DIGEST_CHARS + GROUP_HEADER_CHARS <= self.group_report_chars(groups):
                base, extra = divmod(members, groups)
                return [base + (1 if index < extra else 0) for index in range(groups)]
        return None

    @property
    def grouped_workers(self) -> int:
        """Largest grouped team (the Verification Agent included); never below the flat limit."""
        for members in range(MAX_GROUPS * self.members_per_group, 1, -1):
            if self.group_sizes(members) is not None:
                return max(self.max_workers, members + 1)
        return self.max_workers

    @property
    def team_limit(self) -> int:
        return max(self.max_workers, self.grouped_workers)

    def layout(self, workers: int) -> list[int]:
        """Group sizes for a team of `workers` (Verification Agent included); [] for a flat team."""
        if workers <= self.max_workers:
            return []
        return self.group_sizes(workers - 1) or []

    def expected_seconds(self, workers: int) -> float:
        """Plain estimate for the owner: every worker and every group lead takes one brain turn."""
        return (workers + len(self.layout(workers))) * self.seconds_per_worker

    def team_seconds(self, workers: int, groups: int = 0) -> float:
        """Time budget: each worker's (and group lead's) measured time, doubled for slack, plus the final answer."""
        return max(1200.0, (workers + groups) * self.seconds_per_worker * 2 + 300)

    def team_tool_calls(self, workers: int) -> int:
        return max(40, workers * STEPS_PER_WORKER + 10)

    def explain(self, workers: int | None = None) -> str:
        grouped = self.grouped_workers
        sizes = self.layout(grouped)
        if grouped > self.max_workers:
            first = (f"I can run teams of up to {grouped} workers on this machine (the Verification Agent included): "
                     f"up to {self.max_workers} as one flat team, and up to {grouped} organised in {len(sizes)} groups "
                     f"whose leads condense their findings ({grouped / self.max_workers:.1f}x the flat limit). ")
        else:
            first = (f"I can run teams of up to {self.max_workers} workers on this machine (the Verification Agent "
                     "included); this window is too small for grouped teams to add more. ")
        lines = [first + f"{self.concurrent_teams} team{'s' if self.concurrent_teams != 1 else ''} can run at a time.",
                 *[f"- {reason}" for reason in self.reasons]]
        if workers:
            groups = len(self.layout(workers))
            lines.append(f"A team of {workers} would take about {duration(self.expected_seconds(workers))} "
                         f"(budget {duration(self.team_seconds(workers, groups))}, {self.team_tool_calls(workers)} tool calls).")
        if grouped > self.max_workers:
            lines.append(f"The largest team, {grouped} workers, would take about {duration(self.expected_seconds(grouped))}: "
                         "more workers cover more ground, but they still think one after another.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "worker_share_chars_at_max": self.share_chars(self.max_workers),
                "flat_workers": self.max_workers, "grouped_workers": self.grouped_workers,
                "grouped_layout": self.layout(self.grouped_workers),
                "members_per_group": self.members_per_group,
                "example_team_of_10_minutes": round(10 * self.seconds_per_worker / 60, 1)}


def duration(seconds: float) -> str:
    """"about 40 minutes" / "about 4.9 hours", for telling the owner how long a team takes."""
    minutes = max(0.0, seconds) / 60
    return f"{minutes:.0f} minutes" if minutes < 90 else f"{minutes / 60:.1f} hours"


def measured_worker_seconds(reports: Path, limit: int = 20) -> tuple[float, int]:
    """Median seconds per worker over this machine's recent saved team runs."""
    rates = []
    files = sorted(Path(reports).glob("team-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            workers, seconds = len(data.get("workers") or []), float(data.get("seconds") or 0)
        except (OSError, ValueError, TypeError):
            continue
        if workers and seconds > 0 and data.get("finished"):
            rates.append(seconds / workers)
    return (round(statistics.median(rates), 1), len(rates)) if rates else (DEFAULT_WORKER_SECONDS, 0)


def measure(model: Any = None, *, reports: Path | None = None, hardware_profile: Any = None) -> TeamCapacity:
    from app import hardware
    from app.models.base import model_context_limit

    machine = hardware_profile or hardware.profile()
    window = model_context_limit(model) if model is not None else None
    reasons = []
    if window:
        reasons.append(f"My brain's context window is {window:,} tokens, chosen for this machine's "
                       f"{_gb(machine.gpu.total_bytes)} GB of VRAM and {_gb(machine.available_ram_bytes)} GB of free RAM. "
                       "The Verification Agent and my final answer must read every worker's findings within it.")
    else:
        window = 8192
        reasons.append("No model window was reported, so I assume 8,192 tokens.")
    slots = brain_parallel_slots()
    cpu_share = max(1, (machine.cpu_count or 4) // 4)
    capacity = TeamCapacity(window, 0, DEFAULT_WORKER_SECONDS, 0,
                            max(1, min(slots, cpu_share)), machine.summary().splitlines()[0])
    fit = min(capacity._final_chars, capacity._verifier_chars) // MIN_WORKER_CHARS
    capacity.max_workers = max(3, int(fit))
    reasons.append(f"Each worker needs at least {MIN_WORKER_CHARS:,} characters of that window to report "
                   f"anything useful, so {capacity.max_workers} fit in one flat team.")
    grouped = capacity.grouped_workers
    if grouped > capacity.max_workers:
        sizes = capacity.layout(grouped)
        reasons.append(
            f"Beyond that I organise workers in groups. Each group lead reads its members' full findings in its own "
            f"window (up to {capacity.members_per_group} members at {MIN_WORKER_CHARS:,} characters each) and condenses "
            "them into a cited group report; the Verification Agent and my final answer then read the group reports, "
            f"which need only {MIN_DIGEST_CHARS} characters per worker (at least one claim with its source link) plus "
            f"{GROUP_HEADER_CHARS + GROUP_WRAP_CHARS} per group. So {grouped - 1} workers fit in {len(sizes)} groups of "
            f"up to {max(sizes)}, plus the Verification Agent: {grouped} in all. Each group lead adds one more brain "
            "turn, and every claim keeps its link or is marked unverified.")
    else:
        reasons.append("Grouping would not add workers at this window size, so teams stay flat.")
    seconds, runs = measured_worker_seconds(reports) if reports is not None else (DEFAULT_WORKER_SECONDS, 0)
    capacity.seconds_per_worker, capacity.measured_from_runs = seconds, runs
    reasons.append(
        f"Workers share one brain and think one after another: about {seconds:.0f} s each "
        + (f"(median of my last {runs} team runs here)." if runs else "(an estimate until I have run teams here).")
        + " More workers make a team slower, not smarter at once.")
    reasons.append(f"My brain server answers {slots} request{'s' if slots != 1 else ''} at once and "
                   f"{machine.cpu_count or 'an unknown number of'} logical CPUs leave room for {cpu_share} busy "
                   f"team{'s' if cpu_share != 1 else ''}, so {capacity.concurrent_teams} can run together; "
                   "a team beyond that would only wait for the brain.")
    capacity.reasons = reasons
    return capacity


def brain_parallel_slots() -> int:
    """Requests the local brain server serves at once: OLLAMA_NUM_PARALLEL, else Ollama's default of 4."""
    import os

    try:
        return max(1, int(os.environ.get("OLLAMA_NUM_PARALLEL", "4")))
    except ValueError:
        return 4


def _gb(value: int | None) -> str:
    return "unknown" if value is None else f"{value / 1024**3:.1f}"
