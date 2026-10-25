"""HAYAT needing the model less for what it has already worked out.

An assistant cannot learn its way out of needing a language model -- the
reasoning is the model. It can stop needing the model for the things it has
already solved, and that is most of what it does. The machinery for this
existed and nothing ever called it, so every repeat of a solved problem paid
full inference for an answer already found.
"""
from __future__ import annotations

from types import SimpleNamespace


from app.apprenticeship import (
    REPETITIONS_BEFORE_LEARNING,
    Apprenticeship,
    goal_signature,
    steps_signature,
)
from app.selfimprove import LARGE, SMALL, Improvement, SelfImprovement


def _outcome(goal: str, tools: list[str], complete: bool = True, task_id: str = "task-1"):
    return SimpleNamespace(
        goal=goal, complete=complete, task_id=task_id,
        history=[{"tool": name, "verification": {"passed": True}} for name in tools],
    )


class FakePromoter:
    def __init__(self) -> None:
        self.proposed: list[tuple[str, str]] = []

    def propose_from_task(self, task_id, name=None):
        from pathlib import Path

        self.proposed.append((task_id, name))
        return Path(f"data/skills/candidates/{name}.yaml")


# ------------------------------------------------------------- recognising


def test_the_same_job_asked_differently_is_the_same_job():
    """A signature that disagreed on word order would never accumulate enough
    repetitions to learn anything."""
    assert goal_signature("open notepad and write a note") == \
        goal_signature("write a note and open Notepad")
    assert goal_signature("please open the notepad for me") == goal_signature("open notepad")


def test_different_jobs_stay_different():
    assert goal_signature("open notepad") != goal_signature("open chrome")


def test_solving_it_a_different_way_is_recorded_separately():
    assert steps_signature(["a", "b"]) != steps_signature(["b", "a"])


# ---------------------------------------------------------------- watching


def test_repetition_is_what_makes_a_lesson_ready():
    """Two is coincidence; three is a habit."""
    apprentice = Apprenticeship()
    for index in range(REPETITIONS_BEFORE_LEARNING - 1):
        assert apprentice.observe(_outcome("write a note in notepad", ["notepad_edit"],
                                           task_id=f"t{index}")) is None
    lesson = apprentice.observe(_outcome("write a note in notepad", ["notepad_edit"], task_id="t9"))
    assert lesson is not None and lesson.ready


def test_failed_work_teaches_nothing():
    """Learning from a failure teaches the failure."""
    apprentice = Apprenticeship()
    for index in range(5):
        assert apprentice.observe(
            _outcome("do a thing badly", ["some_tool"], complete=False, task_id=f"t{index}")) is None
    assert apprentice.lessons() == []


def test_steps_that_did_not_verify_are_not_learned():
    apprentice = Apprenticeship()
    outcome = SimpleNamespace(goal="do a thing here", complete=True, task_id="t1",
                              history=[{"tool": "x", "verification": {"passed": False}}])
    assert apprentice.observe(outcome) is None


def test_trivial_requests_are_not_treated_as_procedures():
    apprentice = Apprenticeship()
    for index in range(5):
        apprentice.observe(_outcome("hi", ["chat"], task_id=f"t{index}"))
    assert apprentice.lessons() == []


def test_two_routes_to_the_same_goal_are_counted_apart():
    apprentice = Apprenticeship()
    for index in range(REPETITIONS_BEFORE_LEARNING):
        apprentice.observe(_outcome("open the report file", ["open_file"], task_id=f"a{index}"))
    for index in range(2):
        apprentice.observe(_outcome("open the report file", ["search_files", "open_file"],
                                    task_id=f"b{index}"))
    assert len(apprentice.lessons()) == 2


# ---------------------------------------------------------------- learning


def test_a_repeated_success_is_written_down():
    promoter = FakePromoter()
    apprentice = Apprenticeship(promoter=promoter)
    lesson = None
    for index in range(REPETITIONS_BEFORE_LEARNING):
        lesson = apprentice.observe(_outcome("write a note in notepad", ["notepad_edit"],
                                             task_id=f"t{index}")) or lesson

    result = apprentice.learn(lesson)

    assert result["learned"] is True
    assert promoter.proposed, "the existing promoter should have been used"
    assert result["skill"].startswith("learned_")


def test_a_learned_procedure_is_proposed_not_trusted():
    """A wrong step repeated without thinking is worse than a slow correct one."""
    apprentice = Apprenticeship(promoter=FakePromoter())
    lesson = None
    for index in range(REPETITIONS_BEFORE_LEARNING):
        lesson = apprentice.observe(_outcome("write a note in notepad", ["notepad_edit"],
                                             task_id=f"t{index}")) or lesson

    result = apprentice.learn(lesson)
    assert result["trusted"] is False
    assert "/skill promote" in result["note"]


def test_learning_without_a_promoter_says_so_rather_than_pretending():
    apprentice = Apprenticeship()
    lesson = None
    for index in range(REPETITIONS_BEFORE_LEARNING):
        lesson = apprentice.observe(_outcome("do the thing here", ["tool_a"],
                                             task_id=f"t{index}")) or lesson
    assert apprentice.learn(lesson)["learned"] is False


# --------------------------------------------------- needing the model less


def test_a_trusted_procedure_is_found_without_the_model():
    """The point: a hit here means no inference is needed at all."""
    skills = SimpleNamespace(skills={
        "write_note_notepad": {"trusted": True, "description": "write a note in notepad"},
    })
    apprentice = Apprenticeship(skills=skills)
    assert apprentice.known_procedure("write a note in notepad") is not None


def test_an_untrusted_procedure_is_not_used_automatically():
    skills = SimpleNamespace(skills={
        "write_note_notepad": {"trusted": False, "description": "write a note in notepad"},
    })
    assert Apprenticeship(skills=skills).known_procedure("write a note in notepad") is None


def test_an_unrelated_request_finds_nothing():
    skills = SimpleNamespace(skills={
        "write_note_notepad": {"trusted": True, "description": "write a note in notepad"},
    })
    assert Apprenticeship(skills=skills).known_procedure("book a flight to Tokyo") is None


def test_the_status_says_what_it_no_longer_needs_the_model_for():
    skills = SimpleNamespace(skills={"a": {"trusted": True, "description": "x"}})
    status = Apprenticeship(skills=skills).status()
    assert status["trusted_procedures"] == 1
    assert "no longer need the model" in status["note"]


# -------------------------------------------------- deciding to improve


def test_a_repeatedly_failing_tool_is_noticed():
    failures = SimpleNamespace(recent=lambda n=50: [
        {"tool": "notepad_edit", "stage": "tool"} for _ in range(6)
    ])
    proposals = SelfImprovement(SimpleNamespace(failures=failures)).review()
    assert any("notepad_edit" in item.title for item in proposals)


def test_a_single_failure_is_not_a_pattern():
    failures = SimpleNamespace(recent=lambda n=50: [{"tool": "notepad_edit"}])
    assert SelfImprovement(SimpleNamespace(failures=failures)).review() == []


def test_every_proposal_cites_evidence():
    """A suggestion without evidence is an opinion, and HAYAT has no business
    having those about its own design."""
    failures = SimpleNamespace(recent=lambda n=50: [{"tool": "x"} for _ in range(6)])
    for item in SelfImprovement(SimpleNamespace(failures=failures)).review():
        assert item.evidence and item.reason


def test_a_provider_that_keeps_restarting_is_raised():
    supervisor = SimpleNamespace(status=lambda: {"restart_count": 4, "last_error": "died"})
    proposals = SelfImprovement(SimpleNamespace(supervisor=supervisor)).review()
    assert any("restart" in item.title for item in proposals)


# ------------------------------------------------------ the permission line


def test_writing_a_procedure_down_is_something_it_may_just_do():
    assert not Improvement(title="x", reason="y", scale=SMALL).needs_permission


def test_anything_that_changes_behaviour_waits_for_mohsin():
    assert Improvement(title="x", reason="y", scale=LARGE).needs_permission


def test_a_large_change_is_refused_without_approval():
    """The one place a self-improving system could widen its own behaviour."""
    improvement = SelfImprovement(SimpleNamespace())
    proposal = Improvement(title="rewrite my permissions", reason="faster", scale=LARGE)
    improvement._proposals[proposal.proposal_id] = proposal

    result = improvement.apply(proposal.proposal_id)

    assert result["applied"] is False
    assert "needs your OK" in result["reason"]


def test_it_never_pretends_to_have_done_something_it_cannot_do():
    """A proposal with no way to carry it out stays undone, and says so.

    Mohsin has since approved HAYAT changing its own code, so a proposal
    routed to the repair specialist is now carried out -- verified against the
    suite and reverted when it does not help. That does not make every
    proposal actionable. One with no route stays a recommendation, and the
    honest answer is that it was not applied rather than a success message
    covering nothing.
    """
    improvement = SelfImprovement(SimpleNamespace())
    proposal = Improvement(title="investigate the provider", reason="restarts",
                           scale=LARGE, action="investigate")
    improvement._proposals[proposal.proposal_id] = proposal

    result = improvement.apply(proposal.proposal_id, approved=True)
    assert result["applied"] is False
    assert "no way to carry this one out" in result["reason"]


def test_self_modification_still_cannot_touch_what_it_is_allowed_to_do():
    """The line that survived the approval.

    HAYAT may now rewrite how it works. It may not rewrite what it is
    permitted to do: a change reaching the authority kernel, the permission
    config, or the approval store is reverted even when the suite goes green.
    An assistant that can widen its own permissions by editing the code that
    grants them has no boundary, only a suggestion.
    """
    from app.agents.repair import _is_self_protected

    for path in ("app/safety/authority.py", "config/permissions.yaml",
                 "data/memory/authority.db"):
        assert _is_self_protected(path)


def test_an_unknown_proposal_is_refused():
    assert SelfImprovement(SimpleNamespace()).apply("nope")["applied"] is False


# ------------------------------------------------------------- integration


def test_the_runtime_watches_and_reviews_without_being_asked():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, interactive_approvals=False)
    try:
        assert runtime.apprenticeship is not None
        assert runtime.improvement is not None
        assert isinstance(runtime.improvement.review(), list)
    finally:
        runtime.learner.request_stop("test")
        runtime.learner.stop()
        runtime.close()
