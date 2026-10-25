"""A specialist that works on this system itself.

The distinguishing rule is not that it edits code -- the coder specialist does
that. It is where its beliefs come from. A repair agent that asks a model
"what is broken?" produces confident fiction, because the model cannot see the
test run, the failure record, or the working tree. So diagnosis here is
code: pytest is executed and its result parsed, FailureRecords are read,
doctor is run, git is asked what changed. The model is used for the one thing
it is actually good at -- proposing a change given evidence it has been shown.

Three boundaries make self-modification safe enough to run at all.

It cannot widen its own authority. The permission config and the authority
store are outside every allowed root, so ordinary file tools cannot reach
them, and `modify_own_permissions` is PROHIBITED. A repair that needs more
authority is a conversation with the owner, not a step in a plan.

It cannot claim success it has not observed. Every repair is bracketed by a
real test run; if the suite is not strictly better afterwards the change is
reverted, and the report says so.

It cannot loop. Attempts are bounded and each one must improve on the last.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.core import TaskOutcome
from app.agents.manager import SpecialistAgent


#: Files whose contents decide what this agent is allowed to do. Editing them
#: is a permission change, and it belongs to the owner. Enforced here as well
#: as by the path sandbox, because two independent refusals are the right
#: number for the one boundary that protects all the others.
SELF_PROTECTED = (
    "config/permissions.yaml",
    "app/safety/authority.py",
    "app/safety/permissions.py",
    "app/safety/approval_records.py",
    "app/safety/elevation.py",
    "data/memory/authority.db",
)


@dataclass
class SuiteResult:
    """What the suite actually did. Parsed from output, never assumed."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    exit_code: int = -1
    failing_tests: list[str] = field(default_factory=list)
    raw_tail: str = ""

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and self.passed > 0 and self.failed == 0 and self.errors == 0

    def better_than(self, other: SuiteResult) -> bool:
        """Strictly better: nothing newly broken, and something newly fixed."""
        if not self.preserves_coverage(other):
            return False
        if set(self.failing_tests) - set(other.failing_tests):
            return False
        if self.failed + self.errors > other.failed + other.errors:
            return False
        if self.passed < other.passed and not self.green:
            return False
        return (self.failed + self.errors) < (other.failed + other.errors) or (
            self.green and not other.green
        )

    def preserves_coverage(self, other: SuiteResult) -> bool:
        return (self.exit_code in {0, 1}
                and self.passed + self.failed + self.errors >= other.passed + other.failed + other.errors)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed, "failed": self.failed, "errors": self.errors,
            "green": self.green, "duration_s": round(self.duration_s, 1),
            "exit_code": self.exit_code, "failing_tests": self.failing_tests[:20],
        }


@dataclass
class Diagnosis:
    """Evidence, gathered by running things rather than by asking."""

    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tests: SuiteResult | None = None
    recorded_failures: list[dict[str, Any]] = field(default_factory=list)
    dirty_files: list[str] = field(default_factory=list)
    head: str | None = None
    doctor_ok: bool | None = None
    provider: dict[str, Any] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return bool(self.tests and self.tests.green) and self.doctor_ok is not False

    def problems(self) -> list[str]:
        """Everything wrong, stated as facts with their evidence attached."""
        found: list[str] = []
        if self.tests and not self.tests.green:
            found.append(
                f"{self.tests.failed + self.tests.errors} test(s) failing: "
                + ", ".join(self.tests.failing_tests[:5])
            )
        if self.doctor_ok is False:
            found.append("doctor reports a subsystem problem")
        for failure in self.recorded_failures[:3]:
            found.append(
                f"recorded failure {failure.get('failure_class')} in "
                f"{failure.get('service') or failure.get('stage')}: "
                f"{str(failure.get('exception_message'))[:120]}"
            )
        state = self.provider.get("state")
        if state and state not in {"HEALTHY", "UNKNOWN"}:
            found.append(f"local inference service is {state}")
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at, "healthy": self.healthy, "problems": self.problems(),
            "tests": self.tests.to_dict() if self.tests else None,
            "recorded_failures": len(self.recorded_failures),
            "dirty_files": self.dirty_files[:20], "head": self.head,
            "doctor_ok": self.doctor_ok, "provider_state": self.provider.get("state"),
        }


@dataclass
class RepairReport:
    goal: str
    before: Diagnosis | None = None
    after: Diagnosis | None = None
    attempts: int = 0
    changed_files: list[str] = field(default_factory=list)
    reverted: bool = False
    outcome: str = ""
    task_ids: list[str] = field(default_factory=list)

    @property
    def improved(self) -> bool:
        if not (self.before and self.after and self.before.tests and self.after.tests):
            return False
        return self.after.tests.better_than(self.before.tests)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal, "improved": self.improved, "reverted": self.reverted,
            "attempts": self.attempts, "changed_files": self.changed_files,
            "outcome": self.outcome, "task_ids": self.task_ids,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
        }


@dataclass
class WorkspaceSnapshot:
    """Exact pre-repair bytes, used to restore only what the repair changed.

    Git checkout is intentionally not used: it would erase unrelated edits the
    owner had in progress before self-repair started. Untracked files that
    already existed are recorded and left alone as well.
    """

    tracked: dict[str, bytes | None] = field(default_factory=dict)
    preexisting_untracked: frozenset[str] = frozenset()


_SUMMARY = re.compile(r"(?:(\d+) failed)?,?\s*(?:(\d+) passed)?,?\s*(?:(\d+) error)?", re.I)
_FAILED_LINE = re.compile(r"^FAILED (\S+)", re.M)
_ERROR_LINE = re.compile(r"^ERROR (\S+)", re.M)


class RepairAgent(SpecialistAgent):
    """Diagnoses this system from evidence, then repairs it under verification."""

    MAX_ATTEMPTS = 3

    def __init__(self, core: Any, runtime: Any = None, project_root: Path | None = None) -> None:
        super().__init__("repair", core)
        self.runtime = runtime
        self.project_root = project_root or Path(__file__).resolve().parents[2]
        self._workspace_snapshot: WorkspaceSnapshot | None = None

    #: Asking how it is differs from asking it to change itself. "Check
    #: yourself" should not quietly start editing source.
    _DIAGNOSTIC_ONLY = re.compile(
        r"(?i)\b(?:check|diagnose|inspect|status|what.s broken|are your tests|"
        r"how are you|health|report)\b"
    )
    _FIX_INTENT = re.compile(r"(?i)\b(?:fix|repair|heal|correct|resolve|make it work)\b")

    def run(self, goal: str, intent: Any = None, session: Any = None) -> TaskOutcome:
        """Entry point used by ManagerAgent. Returns a normal TaskOutcome.

        A request to look is answered by looking. A request to fix runs the
        verified repair loop. The difference is decided here rather than by the
        model, because "check yourself" turning into unattended self-editing is
        exactly the surprise this agent must never produce.
        """
        wants_fix = bool(self._FIX_INTENT.search(goal)) and not (
            self._DIAGNOSTIC_ONLY.search(goal) and not self._FIX_INTENT.search(goal)
        )
        report = self.repair(goal, session=session, apply_changes=wants_fix)
        task_id = report.task_ids[-1] if report.task_ids else f"repair-{int(time.time())}"
        return TaskOutcome(
            task_id=task_id, goal=goal,
            complete=report.improved or (report.before is not None and report.before.healthy),
            summary=self.explain(report),
            plan=[], history=[],
            reflection={"repair_report": report.to_dict()},
        )

    def explain(self, report: RepairReport) -> str:
        """State what was observed and what was done, without dressing it up."""
        lines: list[str] = []
        before = report.before
        if before is not None:
            problems = before.problems()
            if not problems:
                lines.append("I checked myself: tests are green and nothing is recorded as failing.")
            else:
                lines.append("I checked myself and found:")
                lines.extend(f"  - {item}" for item in problems)
            if before.tests:
                lines.append(
                    f"Test suite: {before.tests.passed} passed, "
                    f"{before.tests.failed} failed, {before.tests.errors} errors "
                    f"({before.tests.duration_s:.0f}s)."
                )
        if report.outcome:
            lines.append(report.outcome)
        if report.changed_files and not report.reverted:
            lines.append("Changed: " + ", ".join(report.changed_files[:10]))
        if report.reverted:
            lines.append("The working tree was restored, so nothing was left half-applied.")
        return "\n".join(lines)

    # ---------------------------------------------------------------- verbs

    def run_tests(self, target: str = "", timeout: float = 900.0) -> SuiteResult:
        """Run the suite and read what happened out of its output.

        Refuses to start a suite from inside one. A misrouted request that
        reaches here during a test run would otherwise spawn the whole project
        suite as a child of the test that triggered it -- which is exactly how
        this was found. Refusing is honest: the caller gets an outcome marked
        unavailable rather than a fabricated pass.
        """
        import os

        if os.environ.get("PYTEST_CURRENT_TEST"):
            return SuiteResult(
                exit_code=-1,
                raw_tail="declined to run the test suite from inside a test run",
            )
        command = [sys.executable, "-m", "pytest", "-q"]
        if target:
            command.append(target)
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command, cwd=self.project_root, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SuiteResult(exit_code=-1, duration_s=time.perf_counter() - started,
                               raw_tail="the test run exceeded its time limit")
        output = f"{completed.stdout}\n{completed.stderr}"
        return self._parse_pytest(output, completed.returncode, time.perf_counter() - started)

    @staticmethod
    def _parse_pytest(output: str, exit_code: int, duration: float) -> SuiteResult:
        failing = sorted({
            *(match.group(1) for match in _FAILED_LINE.finditer(output)),
            *(match.group(1) for match in _ERROR_LINE.finditer(output)),
        })
        passed = failed = errors = 0
        for line in reversed(output.strip().splitlines()):
            if " passed" in line or " failed" in line or " error" in line:
                for count, label in re.findall(r"(\d+) (passed|failed|error[s]?)", line):
                    if label == "passed":
                        passed = int(count)
                    elif label == "failed":
                        failed = int(count)
                    else:
                        errors = int(count)
                if passed or failed or errors:
                    break
        return SuiteResult(
            passed=passed, failed=failed, errors=errors, duration_s=duration,
            exit_code=exit_code, failing_tests=failing, raw_tail=output[-4000:],
        )

    def diagnose(self, *, run_tests: bool = True) -> Diagnosis:
        """Establish what is actually wrong, by looking rather than reasoning."""
        diagnosis = Diagnosis()
        if run_tests:
            diagnosis.tests = self.run_tests()

        failures = getattr(self.runtime, "failures", None)
        if failures is not None:
            try:
                diagnosis.recorded_failures = failures.recent(5)
            except Exception:
                diagnosis.recorded_failures = []

        diagnosis.head = self._git("rev-parse", "HEAD")
        diagnosis.dirty_files = _parse_porcelain(self._git("status", "--porcelain"))

        supervisor = getattr(self.runtime, "supervisor", None)
        if supervisor is not None:
            try:
                diagnosis.provider = supervisor.status()
            except Exception:
                diagnosis.provider = {}

        if self.runtime is not None:
            try:
                from app.doctor import inspect_runtime

                report = inspect_runtime(self.runtime)
                diagnosis.doctor_ok = not _has_failure(report)
            except Exception:
                diagnosis.doctor_ok = None
        return diagnosis

    # --------------------------------------------------------------- repair

    def repair(self, goal: str = "", *, session: Any = None, apply_changes: bool = True) -> RepairReport:
        """Fix what is broken, and prove it before believing it.

        Returns an honest report in every case, including "nothing was wrong"
        and "I made it worse and put it back".
        """
        before = self.diagnose()
        report = RepairReport(goal=goal or "repair whatever is broken", before=before)

        if before.healthy and not goal:
            report.after = before
            report.outcome = "Nothing was broken. Tests are green and no failures are recorded."
            return report

        if not apply_changes:
            report.after = before
            report.outcome = "Diagnosis only; no changes were attempted."
            return report

        self._workspace_snapshot = self._capture_snapshot()
        baseline_commit = before.head
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            report.attempts = attempt
            try:
                outcome = self.core.run(self._repair_goal(goal, before), session=session)
            except Exception as exc:
                self._revert(baseline_commit)
                report.reverted = not bool(self._changed_since(baseline_commit))
                report.outcome = (f"Repair interrupted: {type(exc).__name__}. "
                                  + ("Workspace restored." if report.reverted else "Workspace recovery is incomplete."))
                return report
            report.task_ids.append(outcome.task_id)

            changed = self._changed_since(baseline_commit)
            forbidden = [name for name in changed if _is_self_protected(name)]
            if forbidden:
                self._revert(baseline_commit)
                report.reverted = True
                report.after = self.diagnose()
                report.outcome = (
                    "Reverted: the repair touched files that decide what I am allowed to do "
                    f"({', '.join(forbidden)}). Changing those is your decision, not mine."
                )
                return report

            report.changed_files = changed
            try:
                after = self.diagnose()
            except Exception as exc:
                self._revert(baseline_commit)
                report.reverted = not bool(self._changed_since(baseline_commit))
                report.outcome = (f"Repair verification failed: {type(exc).__name__}. "
                                  + ("Workspace restored." if report.reverted else "Workspace recovery is incomplete."))
                return report
            report.after = after

            verified = bool(after.tests and before.tests and after.tests.preserves_coverage(before.tests))
            if verified and (after.healthy or after.tests.better_than(before.tests)):
                report.reverted = False
                report.outcome = (
                    f"Repaired on attempt {attempt}. "
                    f"Tests went from {before.tests.failed + before.tests.errors if before.tests else '?'} "
                    f"failing to {after.tests.failed + after.tests.errors if after.tests else '?'}."
                )
                return report

            if changed:
                self._revert(baseline_commit)
                report.reverted = True

        report.after = self.diagnose()
        report.outcome = (
            f"Could not repair this in {self.MAX_ATTEMPTS} attempts without making it worse. "
            "The working tree is back where it started. Diagnosis is in the report."
        )
        return report

    def _repair_goal(self, goal: str, diagnosis: Diagnosis) -> str:
        """Hand the model evidence, not a request to imagine the problem."""
        problems = "\n".join(f"- {item}" for item in diagnosis.problems()) or "- no specific problem detected"
        failing = "\n".join(f"- {name}" for name in (diagnosis.tests.failing_tests[:10] if diagnosis.tests else []))
        return (
            f"{goal or 'Repair this project.'}\n\n"
            "Observed evidence (gathered by running the checks, not by inference):\n"
            f"{problems}\n"
            + (f"\nFailing tests:\n{failing}\n" if failing else "")
            + "\nFix the underlying cause in the project source. Do not edit tests to make them "
            "pass unless the test itself encodes a wrong expectation. Do not modify permission, "
            "authority, or approval files. Make the smallest change that addresses the evidence."
        )

    # ----------------------------------------------------------------- git

    def _git(self, *args: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=self.project_root, capture_output=True, text=True, timeout=30,
            )
            return completed.stdout.strip() if completed.returncode == 0 else None
        except Exception:
            return None

    def _changed_since(self, commit: str | None) -> list[str]:
        snapshot = self._workspace_snapshot
        if snapshot is None:
            return _parse_porcelain(self._git("status", "--porcelain"))
        changed: set[str] = set()
        for relative, original in snapshot.tracked.items():
            path = self.project_root / relative
            current = path.read_bytes() if path.is_file() else None
            if current != original:
                changed.add(relative)
        current_untracked = set(_split_nul(self._git("ls-files", "--others", "--exclude-standard", "-z")))
        changed.update(current_untracked - set(snapshot.preexisting_untracked))
        return sorted(changed)

    def _revert(self, commit: str | None) -> None:
        """Restore only files changed since this repair began.

        Pre-existing tracked edits and untracked files are byte-for-byte part
        of the baseline. A failed repair therefore cannot destroy the owner's
        work merely because the repository was already dirty.
        """
        snapshot = self._workspace_snapshot
        if snapshot is None:
            return
        for relative in self._changed_since(commit):
            path = (self.project_root / relative).resolve()
            try:
                path.relative_to(self.project_root.resolve())
            except ValueError:
                continue
            if relative in snapshot.tracked:
                original = snapshot.tracked[relative]
                if original is None:
                    if path.is_file():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
            elif relative not in snapshot.preexisting_untracked and path.is_file():
                path.unlink()

    def _capture_snapshot(self) -> WorkspaceSnapshot:
        tracked: dict[str, bytes | None] = {}
        for relative in _split_nul(self._git("ls-files", "-z")):
            path = self.project_root / relative
            tracked[relative] = path.read_bytes() if path.is_file() else None
        untracked = frozenset(_split_nul(self._git("ls-files", "--others", "--exclude-standard", "-z")))
        for relative in untracked:
            path = self.project_root / relative
            if path.is_file():
                tracked[relative] = path.read_bytes()
        return WorkspaceSnapshot(tracked=tracked, preexisting_untracked=untracked)


def _parse_porcelain(output: str | None) -> list[str]:
    """Read paths out of `git status --porcelain`, tolerating a stripped first line.

    Fixed rather than tidied: the previous `line[3:]` assumed every line began
    with the full two-character status plus a space, but the surrounding helper
    strips stdout, which removes the leading space of the *first* line only.
    That silently shortened one path per run -- "app/..." became "pp/..." --
    and this list is what decides whether a repair touched a file governing the
    agent's own authority. A path mangled by one character would not match, so
    a parsing slip here is a security failure, not a cosmetic one.

    The rule: the path begins after the first space at index 1 or later. That
    holds for every porcelain shape -- " M path", "MM path", "?? path", the
    stripped "M path", and "R  old -> new" -- without needing to know how many
    status columns survived.
    """
    paths: list[str] = []
    for line in (output or "").splitlines():
        if not line.strip():
            continue
        separator = line.find(" ", 1)
        if separator == -1:
            continue
        candidate = line[separator + 1:].strip()
        # Renames report "old -> new"; the new path is the one that exists now.
        if " -> " in candidate:
            candidate = candidate.split(" -> ", 1)[1].strip()
        if candidate:
            paths.append(candidate.strip('"'))
    return paths


def _split_nul(output: str | None) -> list[str]:
    return [item.replace("\\", "/") for item in (output or "").split("\0") if item]


def _is_self_protected(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").lower().lstrip("./")
    return any(normalized.endswith(name.lower()) or normalized == name.lower() for name in SELF_PROTECTED)


def _has_failure(report: Any) -> bool:
    """True when any nested doctor entry reports a non-ready status."""
    if isinstance(report, dict):
        status = str(report.get("status", "")).lower()
        if status in {"fail", "failed", "error", "unavailable"}:
            return True
        return any(_has_failure(value) for value in report.values())
    if isinstance(report, list):
        return any(_has_failure(item) for item in report)
    return False
