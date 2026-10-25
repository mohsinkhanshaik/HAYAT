"""HAYAT changing itself, and putting it back when the change was wrong.

Mohsin approved self-upgrading. What makes that safe is not trust -- it is
that every change is checked against the suite and undone when it fails, so
the worst outcome is time spent rather than a runtime that stopped working.

The boundary he set himself still holds underneath all of this: HAYAT may
improve what it does, never what it is allowed to do.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.packages import PackageInstaller


class FakePip:
    """Stands in for pip, recording what would have been run."""

    def __init__(self, versions: dict[str, str]) -> None:
        self.versions = versions
        self.commands: list[list[str]] = []

    def run(self, command, **kwargs):
        self.commands.append(command)
        if "install" in command and "--upgrade" in command:
            name = command[-1]
            self.versions[name] = "2.0.0"
        elif "install" in command and "==" in command[-1]:
            name, _, version = command[-1].partition("==")
            self.versions[name] = version
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")


def _installer(tmp_path, pip: FakePip) -> PackageInstaller:
    installer = PackageInstaller(journal=tmp_path / "j.jsonl")
    installer._installed_version = lambda name: pip.versions.get(name)
    return installer


@pytest.fixture
def pip(monkeypatch) -> FakePip:
    import subprocess

    fake = FakePip({"somelib": "1.0.0"})
    monkeypatch.setattr(subprocess, "run", fake.run)
    return fake


# ----------------------------------------------------- it must check itself


def test_an_upgrade_without_a_way_to_verify_is_refused(tmp_path, pip):
    """An unverified self-upgrade is the failure this exists to prevent."""
    result = _installer(tmp_path, pip).upgrade("somelib")
    assert not result.ok
    assert "without a way to check" in result.refused_because
    assert pip.commands == [], "nothing should have been run"


def test_it_will_not_start_from_an_already_broken_state(tmp_path, pip):
    """If something is failing beforehand, an upgrade cannot be judged."""
    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: False)
    assert not result.ok
    assert "already failing" in result.refused_because
    assert not any("--upgrade" in " ".join(c) for c in pip.commands)


# ------------------------------------------------ verified, or put back


def test_a_good_upgrade_is_kept(tmp_path, pip):
    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: True)
    assert result.ok
    assert result.version == "2.0.0"
    assert "verified" in result.detail


def test_an_upgrade_that_breaks_the_suite_is_reverted(tmp_path, pip):
    """The property that makes this safe to allow at all."""
    checks = iter([True, False, True])  # healthy, broken after, healthy again

    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: next(checks))

    assert not result.ok
    assert result.version == "1.0.0", "it should report the version now installed"
    assert "reinstalled" in result.detail
    assert pip.versions["somelib"] == "1.0.0", "the old version must be back"


def test_a_revert_that_does_not_help_says_so_plainly(tmp_path, pip):
    """Healthy, then broken by the upgrade, and still broken after putting the
    old version back. Reporting that as a clean revert would be worse than the
    original failure -- it needs a person."""
    checks = iter([True, False, False])

    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: next(checks))

    assert not result.ok
    assert "reinstalled" in result.detail
    assert "needs you" in result.detail


def test_an_already_current_package_is_not_churned(tmp_path, monkeypatch):
    import subprocess

    pip = FakePip({"somelib": "1.0.0"})
    pip.run = lambda command, **kwargs: (pip.commands.append(command),
                                         SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
    monkeypatch.setattr(subprocess, "run", pip.run)
    installer = _installer(tmp_path, pip)

    result = installer.upgrade("somelib", verify=lambda: True)
    assert result.ok and "newest version" in result.detail


def test_verifier_exception_after_upgrade_restores_previous_version(tmp_path, pip):
    checks = iter([True, RuntimeError("verification crashed"), True])
    def verify():
        value = next(checks)
        if isinstance(value, Exception):
            raise value
        return value
    result = _installer(tmp_path, pip).upgrade("somelib", verify=verify)
    assert not result.ok
    assert result.version == pip.versions["somelib"] == "1.0.0"
    assert "reinstalled" in result.detail


def test_rollback_failure_reports_actual_version_and_unverified_recovery(tmp_path, pip, monkeypatch):
    import subprocess
    original = pip.run
    def run(command, **kwargs):
        if command[-1] == "somelib==1.0.0":
            return SimpleNamespace(returncode=1, stdout="", stderr="could not restore")
        return original(command, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    checks = iter([True, False, False])
    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: next(checks))
    assert not result.ok and result.version == "2.0.0"
    assert "not verified" in result.detail
    assert "was reinstalled" not in result.detail


def test_unchanged_package_still_verifies_dependency_health(tmp_path, pip, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    checks = iter([True, False, False])
    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: next(checks))
    assert not result.ok and "not verified" in result.detail


def test_upgrade_timeout_after_mutation_attempts_verified_recovery(tmp_path, pip, monkeypatch):
    import subprocess
    original = pip.run
    def run(command, **kwargs):
        result = original(command, **kwargs)
        if "--upgrade" in command:
            raise subprocess.TimeoutExpired(command, 2)
        return result
    monkeypatch.setattr(subprocess, "run", run)
    result = _installer(tmp_path, pip).upgrade("somelib", verify=lambda: True)
    assert not result.ok and result.version == "1.0.0"
    assert "interrupted" in result.detail and "reinstalled" in result.detail


def test_a_hostile_name_never_reaches_pip_on_the_upgrade_path_either(tmp_path, pip):
    result = _installer(tmp_path, pip).upgrade("git+https://evil/x.git", verify=lambda: True)
    assert not result.ok and result.refused_because
    assert pip.commands == []


def test_upgrading_something_not_installed_is_refused(tmp_path, pip):
    result = _installer(tmp_path, pip).upgrade("neverinstalled", verify=lambda: True)
    assert not result.ok
    assert "not installed" in result.refused_because


# --------------------------------------------------- the line that holds


def test_hayat_still_cannot_change_what_it_is_allowed_to_do():
    """Self-improvement widens what HAYAT does, never its own authority.
    Everything else Mohsin approved depends on this staying true."""
    from app.safety.authority import ActionRequest, AuthorityManager, Decision

    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(ActionRequest(
        tool="x", category="modify_own_permissions")).decision is Decision.DENY
    with pytest.raises(PermissionError):
        authority.grant("modify_own_permissions")


def test_a_self_change_touching_its_own_authority_is_still_reverted():
    from app.agents.repair import _is_self_protected

    for path in ("config/permissions.yaml", "app/safety/authority.py",
                 "data/memory/authority.db"):
        assert _is_self_protected(path)


def test_the_learner_cannot_upgrade_anything():
    """An unattended loop upgrading libraries is the dangerous version."""
    from app.safety.authority import ActionRequest, AuthorityManager, Decision, Principal

    decision = AuthorityManager(owner_mode=True).evaluate(ActionRequest(
        tool="install_package", category="install_package_project",
        principal=Principal.BACKGROUND_LEARNER))
    assert decision.decision is Decision.DENY


# ------------------------------------------------------------- reporting


def test_upgradable_lists_without_upgrading(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='[{"name":"somelib","version":"1.0","latest_version":"2.0"}]', stderr=""))
    rows = PackageInstaller(journal=tmp_path / "j.jsonl").outdated()
    assert rows[0]["name"] == "somelib"


# ------------------------------------------- changing its own code, verified


class FakeRepair:
    """Stands in for the repair specialist, which really runs pytest."""

    def __init__(self, report):
        self.report = report
        self.goals = []

    def repair(self, goal="", *, session=None, apply_changes=True):
        self.goals.append((goal, apply_changes))
        return self.report


def _runtime_with(repair):
    return SimpleNamespace(manager=SimpleNamespace(specialists={"repair": repair}))


def test_a_kept_change_reports_what_it_touched():
    from app.selfimprove import SelfImprovement

    repair = FakeRepair(SimpleNamespace(
        changed_files=["app/tools/browser.py"], reverted=False,
        outcome="Repaired on attempt 1.", to_dict=lambda: {"attempts": 1}))
    result = SelfImprovement(_runtime_with(repair)).change_own_code("fix the crawler")

    assert result["changed"] is True
    assert result["files"] == ["app/tools/browser.py"]
    assert repair.goals == [("fix the crawler", True)]


def test_a_change_that_made_things_worse_is_reported_as_put_back():
    """Mohsin approved HAYAT changing its own code. What makes that safe is
    not the approval -- it is that a change which does not improve the suite
    is reverted, and reported as reverted rather than as success."""
    from app.selfimprove import SelfImprovement

    repair = FakeRepair(SimpleNamespace(
        changed_files=["app/models/local.py"], reverted=True,
        outcome="Could not repair this without making it worse.",
        to_dict=lambda: {"attempts": 3}))
    result = SelfImprovement(_runtime_with(repair)).change_own_code("make it faster")

    assert result["changed"] is False
    assert result["reverted"] is True
    assert "worse" in result["outcome"]


def test_a_reliability_proposal_is_carried_out_only_once_approved():
    """The proposal comes from evidence, but acting on it still asks."""
    from app.selfimprove import SelfImprovement

    repair = FakeRepair(SimpleNamespace(
        changed_files=[], reverted=False, outcome="Nothing was broken.",
        to_dict=lambda: {}))
    runtime = _runtime_with(repair)
    runtime.failures = SimpleNamespace(recent=lambda n: [{"tool": "flaky"}] * 6)
    improvement = SelfImprovement(runtime)

    proposal = improvement.review()[0]
    assert improvement.apply(proposal.proposal_id) == {
        "applied": False,
        "reason": "this one changes how I behave, so it needs your OK",
        "proposal": proposal.to_dict(),
    }
    assert repair.goals == []  # nothing ran

    improvement.apply(proposal.proposal_id, approved=True)
    assert repair.goals and "flaky" in repair.goals[0][0]


def test_a_proposal_it_cannot_carry_out_says_so_instead_of_pretending():
    from app.selfimprove import Improvement, SelfImprovement

    improvement = SelfImprovement(_runtime_with(FakeRepair(None)))
    proposal = Improvement(title="think harder", reason="vibes", action="")
    improvement._proposals[proposal.proposal_id] = proposal

    result = improvement.apply(proposal.proposal_id, approved=True)
    assert result["applied"] is False
    assert "no way to carry this one out" in result["reason"]


def test_it_cannot_change_its_own_code_without_the_specialist():
    from app.selfimprove import SelfImprovement

    runtime = SimpleNamespace(manager=SimpleNamespace(specialists={}))
    result = SelfImprovement(runtime).change_own_code("rewrite everything")
    assert result["changed"] is False and "not available" in result["reason"]


def test_a_stalled_learning_loop_is_something_that_stands_out():
    """Asked what would make it better, HAYAT said "Nothing stands out".

    At that moment its background learner had been unable to start a cycle
    for over a day and its training corpus stood at single digits against a
    gate of a thousand. None of it was hidden; it simply had no reviewer,
    because every source looked at tools, providers, failures and memory, and
    none looked at learning itself.
    """
    from app.selfimprove import SelfImprovement

    class _Learner:
        paused = False
        running = True
        def may_run(self):
            return False, "the machine is busy with your work"

    class _Knowledge:
        def stats(self):
            return {"unresolved_conflicts": 3, "stale": 15, "claims": 1340}

    class _Training:
        automatic_collection = True
        def status(self):
            return {"total": 7, "states": {"RAW": 7},
                    "training_gate": {"minimum_validated_samples": 1000}}

    runtime = type("R", (), {
        "learner": _Learner(),
        "research": type("Res", (), {"knowledge": _Knowledge()})(),
        "training": _Training(),
    })()
    found = SelfImprovement(runtime)._from_learning()
    titles = " | ".join(item.title for item in found)
    assert "cannot start a cycle" in titles
    assert "contradictions" in titles
    assert "validity horizon" in titles
    assert "short of its own gate" in titles

    # Every proposal cites the evidence it came from.
    assert all(item.reason and item.evidence for item in found)


def test_a_healthy_learning_loop_proposes_nothing():
    """The check must stay quiet when there is nothing wrong, or it is noise."""
    from app.selfimprove import SelfImprovement

    class _Learner:
        paused = False
        running = True
        def may_run(self):
            return True, "ok"

    class _Knowledge:
        def stats(self):
            return {"unresolved_conflicts": 0, "stale": 0, "claims": 1340}

    runtime = type("R", (), {
        "learner": _Learner(),
        "research": type("Res", (), {"knowledge": _Knowledge()})(),
        "training": type("T", (), {"automatic_collection": False})(),
    })()
    assert SelfImprovement(runtime)._from_learning() == []


def test_a_learner_paused_for_the_current_turn_is_not_reported_as_broken():
    """The turn asking the question holds the learner; that is not a defect."""
    from app.selfimprove import SelfImprovement

    class _Learner:
        paused = True
        running = True
        def may_run(self):
            return False, "answering your question"

    runtime = type("R", (), {
        "learner": _Learner(),
        "research": type("Res", (), {"knowledge": None})(),
        "training": None,
    })()
    assert SelfImprovement(runtime)._from_learning() == []


def test_refresh_stale_knowledge_actually_refreshes_instead_of_dead_ending():
    """It was proposed as "I can just do this" and then couldn't.

    The action had no handler in apply(), so it fell through to "I have no way
    to carry this one out myself" -- a suggestion the system made about itself
    and then refused to act on. Now it hands the stale topics to the background
    learner, which re-researches them through the normal pipeline.
    """
    from app.selfimprove import SelfImprovement, Improvement, SMALL

    class _Knowledge:
        def stale_entries(self, limit=20):
            return [
                {"query": "what is the latest python version", "stale": True},
                {"query": "what is the latest python version", "stale": True},  # dup
                {"query": "current uk corporation tax rate", "stale": True},
            ]

    class _Learner:
        def __init__(self):
            self.queued = []
        def enqueue(self, topic, *, source="owner", priority=50, **k):
            self.queued.append((topic, source, priority))
            return True

    learner = _Learner()
    runtime = type("R", (), {
        "research": type("Res", (), {"knowledge": _Knowledge()})(),
        "learner": learner,
    })()
    si = SelfImprovement(runtime)
    proposal = Improvement(
        title="refresh knowledge that has passed its validity horizon",
        reason="stale entries", scale=SMALL, kind="knowledge_quality",
        evidence={"stale": 3}, action="refresh_stale_knowledge",
    )
    with si._lock:
        si._proposals[proposal.proposal_id] = proposal

    result = si.apply(proposal.proposal_id)
    assert result["applied"] is True
    assert result["queued"] == 2, "duplicate topics collapse to one enqueue"
    assert all(source == "stale_refresh" for _, source, _ in learner.queued)


def test_resolving_claim_conflicts_actually_queues_them_instead_of_dead_ending():
    """The same dead end the stale refresh had: proposed as something HAYAT
    could just do, then "no way to carry this one out myself". Each conflict
    now becomes the research question that settles it."""
    from app.selfimprove import SelfImprovement, Improvement, SMALL

    class _Knowledge:
        def conflicts(self, query="", *, unresolved_only=True, limit=50):
            return [
                {"claim_text": "The LLC is taxed as an S-Corp", "conflicting_text": "The LLC is a disregarded entity"},
                {"claim_text": "RFC 9110 obsoletes RFC 7231", "conflicting_text": "RFC 7231 is current"},
                {"claim_text": "", "conflicting_text": "half a conflict is not a question"},
            ]

    class _Learner:
        def __init__(self):
            self.queued = []
        def enqueue(self, topic, *, source="owner", priority=50, **k):
            self.queued.append((topic, source, priority))
            return True

    learner = _Learner()
    runtime = type("R", (), {
        "research": type("Res", (), {"knowledge": _Knowledge()})(),
        "learner": learner,
    })()
    si = SelfImprovement(runtime)
    proposal = Improvement(
        title="resolve contradictions between stored claims", reason="conflicts",
        scale=SMALL, kind="knowledge_quality",
        evidence={"unresolved_conflicts": 3}, action="resolve_claim_conflicts",
    )
    with si._lock:
        si._proposals[proposal.proposal_id] = proposal

    result = si.apply(proposal.proposal_id)
    assert result["applied"] is True
    assert result["queued"] == 2, "a conflict missing one side is not a question to research"
    assert all(source == "reflection_conflict" for _, source, _ in learner.queued)
    assert learner.queued[0][0] == (
        "Resolve with current primary evidence which claim is supported: "
        "The LLC is taxed as an S-Corp versus The LLC is a disregarded entity"
    )


def test_stale_refresh_never_sends_a_pasted_prompt_to_the_search_engine():
    """Stored queries include whole prompts the owner pasted. enqueue()
    filters nothing, and the learner searches the public web with each topic."""
    from app.selfimprove import SelfImprovement, Improvement, SMALL

    prompt = ("Use DEEP + RESEARCH + ADVERSARIAL mode. I want you to act as a senior engineer and "
              "diagnose this hypothetical production problem. " * 5)

    class _Knowledge:
        def stale_entries(self, limit=20):
            return [{"query": prompt}, {"query": "python 3.14 release notes"}]

    queued = []
    learner = type("L", (), {"enqueue": lambda self, topic, **k: queued.append(topic) or True})()
    runtime = type("R", (), {"research": type("Res", (), {"knowledge": _Knowledge()})(), "learner": learner})()
    si = SelfImprovement(runtime)
    proposal = Improvement(title="refresh", reason="stale", scale=SMALL, kind="knowledge_quality",
                           evidence={"stale": 2}, action="refresh_stale_knowledge")
    with si._lock:
        si._proposals[proposal.proposal_id] = proposal

    assert si.apply(proposal.proposal_id)["queued"] == 1
    assert queued == ["python 3.14 release notes"]


def test_junk_stale_entries_are_retired_so_they_stop_coming_back(tmp_path):
    """Skipping them was not enough: they were proposed again every time
    they went stale. Retiring keeps the entry and marks why."""
    from app.selfimprove import SelfImprovement, Improvement, SMALL

    retired, queued = {}, []

    class _Knowledge:
        def stale_entries(self, limit=20):
            return [
                {"id": 1, "query": "Use DEEP mode. I want you to act as a senior engineer. " * 12},
                {"id": 2, "query": "there is a lot of chnage now how do feel about it"},
                {"id": 3, "query": "knowledge and maximally precise"},
                {"id": 4, "query": "Refresh with current primary sources: Python 3.13 release date"},
            ]

        def retire(self, knowledge_id, reason):
            retired[knowledge_id] = reason

    learner = type("L", (), {"enqueue": lambda self, topic, **k: queued.append(topic) or True})()
    runtime = type("R", (), {"research": type("Res", (), {"knowledge": _Knowledge()})(), "learner": learner})()
    si = SelfImprovement(runtime)
    si._fragment_of_an_owner_message = lambda text: text == "knowledge and maximally precise"
    proposal = Improvement(title="refresh", reason="stale", scale=SMALL, kind="knowledge_quality",
                           evidence={"stale": 4}, action="refresh_stale_knowledge")
    with si._lock:
        si._proposals[proposal.proposal_id] = proposal

    result = si.apply(proposal.proposal_id)
    assert result["retired"] == 3 and result["queued"] == 1
    assert retired == {1: "a pasted prompt, not a topic", 2: "not a question about the world",
                       3: "a fragment cut out of a longer message"}
    assert queued == ["Refresh with current primary sources: Python 3.13 release date"]


def test_a_fragment_is_found_inside_a_longer_owner_message(tmp_path):
    from app.memory.database import MemoryDatabase
    from app.selfimprove import SelfImprovement

    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    with memory.connect() as db:
        db.execute("INSERT INTO conversations (session_id, timestamp, role, content) VALUES ('s', 't', 'user', ?)",
                   ("Be careful. " * 20 + "Keep answers grounded in knowledge and maximally precise. " + "Cite. " * 20,))
    si = SelfImprovement(type("R", (), {"memory": memory})())
    assert si._fragment_of_an_owner_message("knowledge and maximally precise")
    assert not si._fragment_of_an_owner_message("python 3.13 release date")


def test_retired_and_superseded_entries_do_not_count_as_stale(tmp_path):
    """The count included rows that could never be refreshed, so the review
    asked for a refresh forever."""
    from app.research.knowledge import KnowledgeStore

    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    with store.connect() as db:
        db.execute("INSERT INTO research_runs (id, query, started_at, completed_at, level, complete, stop_reason, "
                   "confidence, answer, metrics_json, errors_json) VALUES ('r','q','t','t','normal',1,'done',.5,'a','{}','[]')")
        for status in ("active", "superseded", "stale"):
            db.execute("INSERT INTO knowledge (created_at, updated_at, query, content, confidence, freshness_class, "
                       "valid_until, status, run_id, vector_json) VALUES ('t','t',?, 'c', .5, 'fast', '2000-01-01', ?, 'r', '[]')",
                       (status, status))
    assert store.stats()["stale"] == 1
    active_id = store.stale_entries()[0]["id"]
    store.retire(active_id, "not a topic")
    assert store.stats()["stale"] == 0
    with store.connect() as db:
        assert db.execute("SELECT resolution FROM knowledge WHERE id=?", (active_id,)).fetchone()[0] == "retired: not a topic"
