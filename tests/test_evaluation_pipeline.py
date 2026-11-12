"""The pipeline transport asks each case in a session of its own and returns the answer the owner would read."""
from __future__ import annotations

from types import SimpleNamespace

from app.evaluation.pipeline import pipeline_generate


class _Jobs:
    def __init__(self):
        self.jobs, self.done = [], set()

    def recent(self, limit):
        return list(self.jobs)

    def running(self):
        return [job for job in self.jobs if job.job_id not in self.done]

    def get(self, job_id):
        return next(job for job in self.jobs if job.job_id == job_id)

    def result_text(self, job):
        return f"researched answer for {job.job_id}" if job.job_id in self.done else ""


def _runtime(jobs, seen):
    runtime = SimpleNamespace(session=SimpleNamespace(session_id="owner"), research_jobs=jobs,
                              conversation=SimpleNamespace(session_id="owner-chat"))

    def handle(prompt):
        seen.append((runtime.session.session_id, runtime.conversation.session_id))
        if "research" in prompt:
            job = SimpleNamespace(job_id=f"job-{len(jobs.jobs)}")
            jobs.jobs.append(job)
            return SimpleNamespace(response=SimpleNamespace(display_text="I'm searching now."))
        return SimpleNamespace(response=SimpleNamespace(display_text=f"direct: {prompt}"))

    runtime.assistant = SimpleNamespace(handle=handle)
    return runtime


def test_each_case_gets_its_own_session_and_the_owners_is_restored():
    jobs, seen = _Jobs(), []
    runtime = _runtime(jobs, seen)
    generate = pipeline_generate(runtime)
    assert generate("What is 2+2?") == "direct: What is 2+2?"
    generate("And the capital of France?")
    assert len({session for session, _chat in seen}) == 2 and all(s == c for s, c in seen)
    assert "owner" not in {session for session, _chat in seen}
    assert (runtime.session.session_id, runtime.conversation.session_id) == ("owner", "owner-chat")


def test_research_handed_to_the_background_is_waited_for():
    jobs, seen = _Jobs(), []
    runtime = _runtime(jobs, seen)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        jobs.done.add("job-0")  # the job finishes while the transport waits

    answer = pipeline_generate(runtime, sleep=sleep)("please research solar prices")
    assert answer == "researched answer for job-0" and waits


def test_an_unfinished_job_leaves_the_first_reply(monkeypatch):
    jobs, seen = _Jobs(), []
    runtime = _runtime(jobs, seen)
    answer = pipeline_generate(runtime, wait_seconds=0.0, sleep=lambda s: None)("research something slow")
    assert answer == "I'm searching now."


def test_a_conversation_keeps_one_session_across_its_turns():
    from app.evaluation.pipeline import pipeline_converse

    jobs, seen = _Jobs(), []
    runtime = _runtime(jobs, seen)
    converse = pipeline_converse(runtime)
    assert converse(["first", "second"]) == ["direct: first", "direct: second"]
    converse(["third"])
    assert seen[0][0] == seen[1][0] != seen[2][0]


def test_bounce_checks_name_exactly_which_constraint_broke():
    from app.evaluation.bounce import CASES, check, is_bounce_prompt, run

    budget = next(case for case in CASES if case["id"] == "budget-kept")
    good = ["Day 1..Day 3", "ok, 2 days", "2-day plan, total $600: Day 1 ..., Day 2 ..."]
    bad = ["", "", "Day 1 ..., Day 2 ..., Day 3 ... total $750"]
    assert all(check(spec, good)["passed"] for spec in budget["checks"])
    broken = [check(spec, bad) for spec in budget["checks"]]
    assert [c["constraint"] for c in broken if not c["passed"]] == ["keeps the $600 budget", "uses 2 days, not 3"]
    one = next(case for case in CASES if case["id"] == "change-one-item")
    assert check(one["checks"][0], ["1. umbrella\n2. raincoat\n3. boots", "1. umbrella\n2. poncho\n3. boots"])["passed"]
    assert not check(one["checks"][0], ["1. umbrella\n2. raincoat\n3. boots", "1. hat\n2. poncho\n3. boots"])["passed"]
    rev = next(case for case in CASES if case["id"] == "reverse-list")
    assert check(rev["checks"][0], ["1. apple\n2. pear\n3. fig", "1. fig\n2. pear\n3. apple"])["passed"]
    assert is_bounce_prompt("What is 17% of 340?") and not is_bounce_prompt("What is 18% of 340?")
    assert is_bounce_prompt('{"prompt": "What is 17% of 340?", "answer": "57.8"}')  # inside a training record
    report = run(lambda turns: ["x"] * len(turns))
    assert report["summary"]["conversations_passed"] == 0 and report["summary"]["metamorphic_groups"] == 3


def test_reversal_is_judged_on_the_items_not_their_emoji():
    from app.evaluation.bounce import CASES, check

    spec = next(case for case in CASES if case["id"] == "reverse-list")["checks"][0]
    before = "1. Apple\n2. Banana\n3. Orange 🍎🍌🍊"
    assert check(spec, [before, "1. Orange\n2. Banana\n3. Apple 🍊🍌🍎"])["passed"]
    assert not check(spec, [before, "1. Banana\n2. Orange\n3. Apple"])["passed"]


def test_new_bounce_relations_check_versions_counts_and_menus():
    from app.evaluation.bounce import CASES, check

    first = next(case for case in CASES if case["id"] == "first-version")["checks"][0]
    assert check(first, ["Fresh bread, warm hearts.", "Knead we say more?", "“Fresh bread, warm hearts!”"])["passed"]
    assert not check(first, ["Fresh bread, warm hearts.", "Knead we say more?", "Fresh bread, warmer hearts."])["passed"]
    count = next(case for case in CASES if case["id"] == "count-keeps-limit")["checks"][0]
    five = "1. Keep a schedule\n2. Dim the lights\n3. No screens late\n4. Cool bedroom\n5. Skip late coffee"
    assert check(count, ["", five])["passed"]
    assert not check(count, ["", five + "\n6. Extra tip"])["passed"]
    menu = next(case for case in CASES if case["id"] == "party-long")["checks"][0]
    assert check(menu, [""] * 6 + ["Veggie lasagna, salad, fruit tart", ""])["passed"]
    assert not check(menu, [""] * 6 + ["Grilled chicken skewers and salad", ""])["passed"]


def test_confined_file_tools_cannot_write_into_the_project(tmp_path):
    import pytest
    from app.evaluation.pipeline import confine_files
    from app.safety.permissions import PermissionManager

    permissions = PermissionManager({"default": "ALLOW"}, tmp_path / "project")
    runtime = SimpleNamespace(registry=SimpleNamespace(permissions=permissions))
    root = confine_files(runtime, tmp_path / "scratch")
    assert permissions.validate_project_path("answer.txt", write=True) == root / "answer.txt"
    with pytest.raises(PermissionError):
        permissions.validate_project_path(tmp_path / "project" / "answer.txt", write=True)
