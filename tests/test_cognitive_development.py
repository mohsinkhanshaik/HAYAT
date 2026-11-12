"""Development stages come only from measured records; a missing store is 'not measured', never passed."""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.cognitive_development import CognitiveDevelopment, development_request, direct_development_plan
from app.memory.database import MemoryDatabase
from app.topics import ConversationTopics


def _runtime(tmp_path, **extra):
    memory = MemoryDatabase(tmp_path / "memory" / "agent.db")
    memory.initialize()
    return SimpleNamespace(memory=memory, topics=ConversationTopics(tmp_path / "memory" / "topics.db"), **extra)


def test_an_empty_install_reaches_no_stage_and_says_what_is_unmeasured(tmp_path):
    rt = _runtime(tmp_path)
    report = CognitiveDevelopment(lambda: rt, tmp_path / "memory" / "development.db").assess()
    assert report["stage_reached"] == 0 and report["stage_name"] == "None yet"
    stages = {stage["name"]: stage for stage in report["stages"]}
    assert len(stages) == 12
    assert stages["Observation"]["value"] == 0 and not stages["Observation"]["passed"]
    # Recognition is measured even here: the labelled benchmark needs no history.
    assert stages["Recognition"]["value"] >= 0.9 and stages["Recognition"]["passed"]
    assert stages["Understanding"]["value"] is None and stages["Understanding"]["detail"].startswith("Not measured")
    assert stages["Prediction"]["value"] is None and not stages["Prediction"]["passed"]


def test_stages_count_only_in_order_and_come_from_the_records(tmp_path):
    rt = _runtime(tmp_path)
    for index in range(120):
        rt.memory.record_message("s", "user", f"message {index}")
    holdout = tmp_path / "benchmarks" / "holdout"
    holdout.mkdir(parents=True)
    (holdout / "pipeline-20261107-000000.json").write_text(json.dumps({"finished_at": "2026-11-07", "summary": {
        "completed_cases": 30, "correct": 27, "interval_95": [0.74, 0.96],
        "families": {"arithmetic": {"cases": 3, "correct": 3, "accuracy": 1.0},
                     "bias_pairs": {"cases": 3, "correct": 1, "accuracy": 0.33}}}}), encoding="utf-8")
    report = CognitiveDevelopment(lambda: rt, tmp_path / "memory" / "development.db").assess()
    stages = {stage["number"]: stage for stage in report["stages"]}
    assert stages[1]["passed"] and stages[2]["passed"]
    assert stages[6]["value"] == 0.9 and stages[6]["passed"]  # 27 of 30, from the file
    assert "Weak families: bias_pairs" in stages[7]["detail"]
    # Association has no memory network here, so the reached stage stops at 2 although 6 passed.
    assert not stages[3]["passed"] and report["stage_reached"] == 2 and 6 in report["passed"]


def test_prediction_is_judged_against_a_guess_made_without_hindsight(tmp_path):
    """2026-11-09: an in-sample baseline that could guess "chat" turned a real +7-point lift into -19 points."""
    import sqlite3

    path = tmp_path / "experience.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE situation_predictions (id INTEGER PRIMARY KEY, at TEXT, areas TEXT, basis TEXT, "
                   "resolved INTEGER, hit INTEGER, actual TEXT)")
        # Mostly chat (never guessable), then specific requests the predictor gets right after a while.
        rows = [('["research"]', 0, "chat")] * 20 + [('["research"]', 1, "research")] * 6 + [('["money"]', 1, "money")] * 4
        db.executemany("INSERT INTO situation_predictions (at, areas, basis, resolved, hit, actual) VALUES ('t', ?, '', 1, ?, ?)",
                       rows)
    record = CognitiveDevelopment._prediction_record(path)
    assert record["checked"] == 30 and record["right"] == 10
    # Habit can only start guessing "research" after it has been seen once, and never guesses chat.
    assert record["baseline"] == 5 / 30
    rt = _runtime(tmp_path, situation=SimpleNamespace(path=path))
    stage = CognitiveDevelopment(lambda: rt, tmp_path / "dev.db")._prediction(rt, None)
    assert stage.value == round(10 / 30 - 5 / 30, 3) and stage.passed


def test_abstraction_and_problem_solving_come_from_task_records(tmp_path):
    from app.experience import ExperienceMemory

    path = tmp_path / "experience.db"
    ExperienceMemory(path).stats()  # creates the table
    import sqlite3

    rows = [("open the sales report", "open report sales", 1, "browser_open", "timeout", '["web_fetch"]'),
            ("book a dentist slot", "book dentist slot", 1, "browser_open", "timeout", '["web_fetch"]'),
            ("open the sales report again", "again open report sales", 1, "", "", "[]")]
    rows += [(f"novel task {index}", f"word{index} thing{index} other{index}", int(index % 5 != 0), "", "", "[]")
             for index in range(12)]
    with sqlite3.connect(path) as db:
        db.executemany("INSERT INTO experiences (at, goal, words, tools, success, failed_tool, failure_class, fix_tools) "
                       "VALUES ('t', ?, ?, '[]', ?, ?, ?, ?)", rows)
    rt = _runtime(tmp_path, experience=ExperienceMemory(path))
    development = CognitiveDevelopment(lambda: rt, tmp_path / "dev.db")
    records = development._experience_rows(rt)
    abstraction = development._abstraction(rt, records)
    assert abstraction.value == 1 and not abstraction.passed  # one fix carried across unrelated goals; needs 2
    assert "browser_open timeout -> " in abstraction.detail
    solving = development._problem_solving(records)
    # "open the sales report again" overlaps an earlier goal, so it is not unfamiliar; 14 others are.
    assert solving.detail.startswith("14 tasks") and solving.value == round(11 / 14, 3) and solving.passed


def test_development_questions_reach_the_tool():
    assert development_request("what stage of development are you at?")
    assert development_request("how developed are you")
    assert not development_request("what stage is the project in")
    assert direct_development_plan("how developed are you", ["cognitive_development"])[0]["tool"] == "cognitive_development"
