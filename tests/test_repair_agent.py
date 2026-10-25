"""The specialist that works on this system itself.

Self-modification is only safe if three things hold: it believes evidence
rather than the model, it cannot re-authorise itself, and it cannot claim a
repair it has not observed. Each is tested here directly.
"""
from __future__ import annotations


import pytest

from app.agents.repair import (
    Diagnosis,
    RepairAgent,
    SuiteResult,
    _is_self_protected,
)


class StubCore:
    """Stands in for the shared Agent core; records what it was asked to do."""

    def __init__(self) -> None:
        self.goals: list[str] = []

    def run(self, goal, intent=None, session=None):
        from app.agent.core import TaskOutcome

        self.goals.append(goal)
        return TaskOutcome(f"task-{len(self.goals)}", goal, True, "done", [])


# ------------------------------------------------------------ test parsing


def test_pytest_output_is_parsed_rather_than_assumed():
    output = (
        "FAILED tests/test_alpha.py::test_one - AssertionError\n"
        "ERROR tests/test_beta.py::test_two\n"
        "2 failed, 528 passed, 1 error in 61.2s\n"
    )
    outcome = RepairAgent._parse_pytest(output, 1, 61.2)

    assert outcome.passed == 528
    assert outcome.failed == 2
    assert outcome.errors == 1
    assert not outcome.green
    assert "tests/test_alpha.py::test_one" in outcome.failing_tests


def test_a_green_run_is_recognised():
    outcome = RepairAgent._parse_pytest("596 passed in 65.92s\n", 0, 65.9)
    assert outcome.green and outcome.passed == 596 and outcome.failed == 0


def test_improvement_requires_strictly_fewer_failures():
    before = SuiteResult(passed=500, failed=3, exit_code=1)
    assert SuiteResult(passed=503, failed=0, exit_code=0).better_than(before)
    assert SuiteResult(passed=501, failed=2, exit_code=1).better_than(before)
    assert not SuiteResult(passed=500, failed=3, exit_code=1).better_than(before)
    assert not SuiteResult(passed=498, failed=5, exit_code=1).better_than(before), "worse is not better"


def test_a_run_that_breaks_something_new_is_never_an_improvement():
    before = SuiteResult(passed=596, failed=0, exit_code=0)
    after = SuiteResult(passed=590, failed=6, exit_code=1)
    assert not after.better_than(before)


def test_fewer_failures_cannot_hide_new_failures_or_deleted_tests():
    before = SuiteResult(passed=8, failed=2, exit_code=1, failing_tests=["test_a", "test_b"])
    assert not SuiteResult(passed=9, failed=1, exit_code=1, failing_tests=["test_new"]).better_than(before)
    assert not SuiteResult(passed=8, exit_code=0).better_than(before)
    assert not SuiteResult(exit_code=0).green


def test_success_after_a_reverted_attempt_is_reported_as_kept(tmp_path, monkeypatch):
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    broken = Diagnosis(tests=SuiteResult(passed=8, failed=2, exit_code=1))
    states = iter([broken, broken, Diagnosis(tests=SuiteResult(passed=10, exit_code=0))])
    monkeypatch.setattr(agent, "diagnose", lambda **kw: next(states))
    monkeypatch.setattr(agent, "_changed_since", lambda commit: ["app/module.py"])
    reverts = []
    monkeypatch.setattr(agent, "_revert", lambda commit: reverts.append(True))
    report = agent.repair("fix the failure")
    assert report.attempts == 2 and report.improved and not report.reverted
    assert reverts == [True]


def test_interrupted_repair_restores_existing_untracked_content(tmp_path, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / "draft.py"
    path.write_text("owner's unfinished work", encoding="utf-8")

    class FailingCore:
        def run(self, *args, **kw):
            path.write_text("broken repair", encoding="utf-8")
            raise RuntimeError("interrupted")

    agent = RepairAgent(FailingCore(), project_root=tmp_path)
    monkeypatch.setattr(agent, "diagnose", lambda **kw: Diagnosis(tests=SuiteResult(passed=4, failed=1, exit_code=1)))
    report = agent.repair("repair my code")
    assert report.reverted
    assert "interrupted" in report.outcome.lower()
    assert path.read_text(encoding="utf-8") == "owner's unfinished work"


# -------------------------------------------------------- porcelain parsing


def test_git_status_paths_survive_a_stripped_first_line():
    """A real bug: the helper strips stdout, removing the leading space of the
    first porcelain line only, so a fixed line[3:] ate one character and
    "app/..." became "pp/...". This list decides whether a repair touched a
    file governing the agent's own authority, so one lost character is a
    security failure rather than a cosmetic one."""
    from app.agents.repair import _parse_porcelain

    stripped_first_line = "M app/agents/manager.py\n M app/runtime.py\n?? notes.md"
    assert _parse_porcelain(stripped_first_line) == [
        "app/agents/manager.py", "app/runtime.py", "notes.md",
    ]


@pytest.mark.parametrize(
    "line,expected",
    [
        (" M app/runtime.py", "app/runtime.py"),
        ("MM app/runtime.py", "app/runtime.py"),
        ("?? AUDIT-2026-08-13.md", "AUDIT-2026-08-13.md"),
        ("A  config/permissions.yaml", "config/permissions.yaml"),
        ("R  app/old.py -> app/new.py", "app/new.py"),
        ('?? "path with spaces.txt"', "path with spaces.txt"),
    ],
)
def test_every_porcelain_shape_yields_the_real_path(line, expected):
    from app.agents.repair import _parse_porcelain

    assert _parse_porcelain(line) == [expected]


def test_a_protected_file_is_still_recognised_after_parsing():
    """The two halves joined: parse the status line, then check the boundary."""
    from app.agents.repair import _is_self_protected, _parse_porcelain

    # First line, stripped -- the exact case that used to lose a character.
    paths = _parse_porcelain("M config/permissions.yaml\n M app/runtime.py")
    assert paths[0] == "config/permissions.yaml"
    assert _is_self_protected(paths[0])
    assert not _is_self_protected(paths[1])


# --------------------------------------------------- the authority boundary


@pytest.mark.parametrize(
    "path",
    ["config/permissions.yaml", "app/safety/authority.py", "app/safety/permissions.py",
     "data/memory/authority.db", "app\\safety\\authority.py", "./config/permissions.yaml"],
)
def test_files_that_decide_its_own_authority_are_recognised(path):
    assert _is_self_protected(path)


@pytest.mark.parametrize("path", ["app/runtime.py", "app/tools/registry.py", "tests/test_alpha.py"])
def test_ordinary_project_files_are_repairable(path):
    assert not _is_self_protected(path)


def test_a_repair_touching_its_own_permissions_is_reverted(tmp_path, monkeypatch):
    """The one boundary that protects all the others.

    Even if the model decides the fix is to widen a permission, the change is
    put back and the report says plainly that this is the owner's decision.
    """
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    reverted: list[str] = []

    broken = Diagnosis(tests=SuiteResult(passed=10, failed=1, exit_code=1))
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: broken)
    monkeypatch.setattr(agent, "_changed_since", lambda commit: ["config/permissions.yaml", "app/runtime.py"])
    monkeypatch.setattr(agent, "_revert", lambda commit: reverted.append("reverted"))

    report = agent.repair("fix the failing test")

    assert report.reverted
    assert reverted == ["reverted"]
    assert "allowed to do" in report.outcome
    assert "config/permissions.yaml" in report.outcome
    assert not report.improved


def test_a_repair_that_does_not_improve_anything_is_reverted(tmp_path, monkeypatch):
    """No claiming success that was not observed."""
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    reverts: list[str] = []
    still_broken = Diagnosis(tests=SuiteResult(passed=10, failed=1, exit_code=1))

    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: still_broken)
    monkeypatch.setattr(agent, "_changed_since", lambda commit: ["app/runtime.py"])
    monkeypatch.setattr(agent, "_revert", lambda commit: reverts.append("r"))

    report = agent.repair("fix it")

    assert report.attempts == RepairAgent.MAX_ATTEMPTS, "attempts must be bounded"
    assert report.reverted
    assert len(reverts) == RepairAgent.MAX_ATTEMPTS
    assert not report.improved
    assert "back where it started" in report.outcome


def test_a_real_improvement_is_kept_and_reported(tmp_path, monkeypatch):
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    states = [
        Diagnosis(tests=SuiteResult(passed=10, failed=2, exit_code=1)),
        Diagnosis(tests=SuiteResult(passed=12, failed=0, exit_code=0)),
    ]
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: states.pop(0) if states else states)
    monkeypatch.setattr(agent, "_changed_since", lambda commit: ["app/runtime.py"])
    monkeypatch.setattr(agent, "_revert", lambda commit: pytest.fail("a successful repair must not be reverted"))

    report = agent.repair("fix the failing tests")

    assert report.improved
    assert not report.reverted
    assert report.attempts == 1
    assert "app/runtime.py" in report.changed_files


def test_failed_repair_restores_its_changes_without_erasing_owner_work(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    tracked = tmp_path / "app.py"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)

    # This edit and untracked note belong to the owner before repair begins.
    tracked.write_text("owner edit\n", encoding="utf-8")
    note = tmp_path / "notes.txt"
    note.write_text("owner note\n", encoding="utf-8")
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    agent._workspace_snapshot = agent._capture_snapshot()

    tracked.write_text("bad repair\n", encoding="utf-8")
    generated = tmp_path / "repair-created.py"
    generated.write_text("broken\n", encoding="utf-8")
    agent._revert(None)

    assert tracked.read_text(encoding="utf-8") == "owner edit\n"
    assert note.read_text(encoding="utf-8") == "owner note\n"
    assert not generated.exists()


# ------------------------------------------------------- evidence, not vibes


def test_the_goal_handed_to_the_model_carries_observed_evidence(tmp_path, monkeypatch):
    """The model is asked to fix a described problem, not to imagine one."""
    core = StubCore()
    agent = RepairAgent(core, project_root=tmp_path)
    broken = Diagnosis(tests=SuiteResult(passed=5, failed=1, exit_code=1,
                                         failing_tests=["tests/test_x.py::test_y"]))
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: broken)
    monkeypatch.setattr(agent, "_changed_since", lambda commit: [])
    monkeypatch.setattr(agent, "_revert", lambda commit: None)

    agent.repair("fix it")

    prompt = core.goals[0]
    assert "tests/test_x.py::test_y" in prompt
    assert "1 test(s) failing" in prompt
    assert "Do not modify permission" in prompt


def test_diagnosis_reports_problems_from_recorded_failures():
    diagnosis = Diagnosis(
        tests=SuiteResult(passed=10, failed=0, exit_code=0),
        recorded_failures=[{"failure_class": "DEPENDENCY_DOWN", "service": "local_inference",
                            "exception_message": "connection refused"}],
        provider={"state": "FAILED"},
    )
    problems = diagnosis.problems()
    assert any("DEPENDENCY_DOWN" in item for item in problems)
    assert any("local inference service is FAILED" in item for item in problems)


def test_a_healthy_system_reports_that_nothing_was_wrong(tmp_path, monkeypatch):
    agent = RepairAgent(StubCore(), project_root=tmp_path)
    healthy = Diagnosis(tests=SuiteResult(passed=596, failed=0, exit_code=0), doctor_ok=True)
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: healthy)

    report = agent.repair()

    assert report.attempts == 0
    assert "Nothing was broken" in report.outcome


# ------------------------------------------------------------------ routing


def test_asking_it_to_check_itself_does_not_start_editing(tmp_path, monkeypatch):
    """"Check yourself" turning into unattended self-editing is the surprise
    this agent must never produce."""
    core = StubCore()
    agent = RepairAgent(core, project_root=tmp_path)
    broken = Diagnosis(tests=SuiteResult(passed=5, failed=1, exit_code=1))
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: broken)

    outcome = agent.run("check yourself and tell me what's broken")

    assert core.goals == [], "no repair task should have been started"
    assert "Diagnosis only" in outcome.summary or "found" in outcome.summary


def test_asking_it_to_fix_itself_runs_the_repair_loop(tmp_path, monkeypatch):
    core = StubCore()
    agent = RepairAgent(core, project_root=tmp_path)
    states = [
        Diagnosis(tests=SuiteResult(passed=5, failed=1, exit_code=1)),
        Diagnosis(tests=SuiteResult(passed=6, failed=0, exit_code=0)),
    ]
    monkeypatch.setattr(agent, "diagnose", lambda **kwargs: states.pop(0) if states else
                        Diagnosis(tests=SuiteResult(passed=6, failed=0, exit_code=0)))
    monkeypatch.setattr(agent, "_changed_since", lambda commit: ["app/x.py"])
    monkeypatch.setattr(agent, "_revert", lambda commit: None)

    outcome = agent.run("fix yourself")

    assert core.goals, "the repair loop should have run a task"
    assert outcome.complete


@pytest.mark.parametrize(
    "goal",
    ["fix yourself", "repair yourself", "self-repair", "diagnose your own code",
     "what's broken in you", "are your tests passing"],
)
def test_self_directed_requests_route_to_the_repair_specialist(goal):
    from app.agents.manager import ManagerAgent

    assert ManagerAgent.SELF_REPAIR.search(goal), f"{goal!r} should reach the repair specialist"


@pytest.mark.parametrize("goal", ["fix the bug in my python script", "research quantum computing",
                                  "open notepad"])
def test_ordinary_work_does_not_route_to_self_repair(goal):
    from app.agents.manager import ManagerAgent

    core = StubCore()
    manager = ManagerAgent(core, {"repair": RepairAgent(core)})
    manager.route(goal)
    assert core.goals == [goal], "ordinary work must go to the normal path"


# ------------------------------------------------------------- integration


def test_the_repair_specialist_is_registered_in_the_real_runtime():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, interactive_approvals=False)
    try:
        assert "repair" in runtime.manager.roles()
        specialist = runtime.manager.specialists["repair"]
        assert specialist.runtime is runtime, "it must see the runtime's own evidence"
        assert specialist.core is runtime.agent, "it must share the one agent core"
    finally:
        runtime.events.close()
