from __future__ import annotations

import pytest

from app.memory.database import MemoryDatabase
from app.memory.state_policy import (
    StateClass,
    classify,
    excluded_from_conversation,
    is_current_state_eligible,
)


@pytest.fixture()
def memory(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    return database


# ------------------------------------------------------------- classification


@pytest.mark.parametrize(
    "memory_type, content, expected",
    [
        ("semantic", "the user's name is Mohsin Khan Shaik", StateClass.DURABLE),
        ("procedural", "how to restart the provider", StateClass.DURABLE),
        ("episodic", "Task succeeded. Goal: research NCCL.", StateClass.HISTORICAL),
        ("reflection", "what worked: research_topic", StateClass.HISTORICAL),
        ("working", "scratch", StateClass.TRANSIENT),
    ],
)
def test_types_classify_by_how_they_relate_to_now(memory_type, content, expected):
    assert classify(memory_type, content) is expected


def test_an_episodic_record_carrying_a_sensor_snapshot_is_transient_in_substance():
    """The actual leak: camera and screen observations are stored as episodic
    task records, so the declared type says "history" while the payload
    describes a world that has since moved on."""
    camera = classify("episodic", "Task succeeded. observed_at 2026-08-14. Frame: 1280x720")
    screen = classify("episodic", "Task succeeded. Goal: Take a screenshot and tell me what is on my screen.")
    assert camera is StateClass.TRANSIENT
    assert screen is StateClass.TRANSIENT


# ---------------------------------------------------------- current eligibility


def test_a_transient_observation_is_current_only_for_the_session_that_made_it():
    row = {"state_class": "TRANSIENT", "session_id": "session-a"}
    assert is_current_state_eligible(row, session_id="session-a") is True
    assert is_current_state_eligible(row, session_id="session-b") is False


def test_durable_and_historical_memories_stay_eligible_across_sessions():
    for state_class in ("DURABLE", "HISTORICAL"):
        row = {"state_class": state_class, "session_id": "session-a"}
        assert is_current_state_eligible(row, session_id="session-b") is True


def test_legacy_untyped_rows_are_never_treated_as_current():
    """Rows predating this policy carry no class. Assuming they are current
    would recreate the bug across the whole existing database."""
    row = {"state_class": "LEGACY_UNTYPED", "session_id": None}
    assert is_current_state_eligible(row, session_id="session-a") is False


def test_only_foreign_transient_rows_are_withheld_from_conversation():
    historical = {"state_class": "HISTORICAL", "session_id": "old"}
    foreign_transient = {"state_class": "TRANSIENT", "session_id": "old"}
    own_transient = {"state_class": "TRANSIENT", "session_id": "now"}

    assert excluded_from_conversation(historical, session_id="now") is False
    assert excluded_from_conversation(foreign_transient, session_id="now") is True
    assert excluded_from_conversation(own_transient, session_id="now") is False


# ------------------------------------------------------------ retrieval gating


def test_yesterdays_screenshot_cannot_reach_a_new_session(memory):
    """The regression this policy exists for. Previously the row was retrieved
    and merely labelled; the model still received it and could answer a
    question about the present with it."""
    memory.store_memory(
        "episodic",
        "Task succeeded. Goal: Take a screenshot and tell me what is on my screen. "
        "observed_at 2026-08-13. Frame: 1920x1200. Visual Studio Code was open.",
        0.9, "task", session_id="session-yesterday",
    )

    leaked = memory.retrieve("what is on my screen", limit=10, session_id="session-today")
    assert leaked == [], "a foreign session's screen snapshot must not be retrievable as context"


def test_the_session_that_observed_it_may_still_use_it(memory):
    memory.store_memory(
        "episodic", "Task succeeded. observed_at now. Frame: 1280x720. A person is visible.",
        0.9, "task", session_id="session-live",
    )
    rows = memory.retrieve("what can you see", limit=10, session_id="session-live")
    assert any("person is visible" in row["content"] for row in rows)


def test_historical_questions_can_opt_in_explicitly(memory):
    """"What was on my screen earlier?" is legitimate; it just has to be asked
    for, rather than leaking into every unrelated query."""
    memory.store_memory(
        "episodic", "Task succeeded. observed_at 2026-08-13. Frame: 1920x1200. A screenshot was taken.",
        0.9, "task", session_id="session-yesterday",
    )
    assert memory.retrieve("screenshot", limit=10, session_id="session-today") == []
    historical = memory.retrieve(
        "screenshot", limit=10, session_id="session-today", include_foreign_transient=True
    )
    assert any("screenshot was taken" in row["content"] for row in historical)


def test_durable_identity_still_crosses_sessions(memory):
    """The policy must not damage genuine long-term memory."""
    memory.store_memory("semantic", "The user's name is Mohsin Khan Shaik.", 0.95, "user", session_id="session-old")
    rows = memory.retrieve("what is the user's name", limit=10, session_id="session-new")
    assert any("Mohsin Khan Shaik" in row["content"] for row in rows)


def test_new_memories_record_their_state_class(memory):
    memory.store_memory("semantic", "A durable fact.", 0.8, "user", session_id="s1")
    rows = memory.retrieve("durable fact", limit=5, session_id="s1")
    assert rows and rows[0]["state_class"] == StateClass.DURABLE.value
