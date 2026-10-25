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
"""
from __future__ import annotations

import json
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

    def team_seconds(self, workers: int) -> float:
        """Time budget: each worker's measured time, doubled for slack, plus the final answer."""
        return max(1200.0, workers * self.seconds_per_worker * 2 + 300)

    def team_tool_calls(self, workers: int) -> int:
        return max(40, workers * STEPS_PER_WORKER + 10)

    def explain(self, workers: int | None = None) -> str:
        lines = [
            f"I can run teams of up to {self.max_workers} workers on this machine (the Verification Agent included), "
            f"and {self.concurrent_teams} team{'s' if self.concurrent_teams != 1 else ''} at a time.",
            *[f"- {reason}" for reason in self.reasons],
        ]
        if workers:
            minutes = workers * self.seconds_per_worker / 60
            lines.append(f"A team of {workers} would take about {minutes:.0f} minutes "
                         f"(budget {self.team_seconds(workers) / 60:.0f} min, {self.team_tool_calls(workers)} tool calls).")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "worker_share_chars_at_max": self.share_chars(self.max_workers),
                "example_team_of_10_minutes": round(10 * self.seconds_per_worker / 60, 1)}


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
                   f"anything useful, so {capacity.max_workers} fit.")
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
