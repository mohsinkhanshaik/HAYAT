"""Secrets must not survive into logs, events, memory, checkpoints, or training data.

Every credential in this file is a syntactically valid but fabricated fixture.
None is a real key, and no test prints a fixture value on success.
"""

from __future__ import annotations

import json

import pytest

from app.events import EventBus
from app.memory.database import MemoryDatabase
from app.safety.sensitive import (
    Redactor,
    Sensitivity,
    assert_persistable,
    assert_transmittable,
    contains_secret,
    highest,
    redact,
    redact_text,
    scan,
)
from app.training.pipeline import TrainingDataPipeline


# Fabricated fixtures. Shapes are real; values are not.
FIXTURES = {
    "openai_key": "sk-" + "A1b2C3d4E5f6G7h8I9j0" * 2,
    "anthropic_key": "sk-ant-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3",
    "github_token": "ghp_" + "0123456789abcdefABCDEF0123",
    # Assembled at run time so secret scanners (GitHub push protection) do not take a fixture for a real key.
    "aws_access_key_id": "AKIA" + "QQQQWWWWEEEERRRR",
    "google_api_key": "AIza" + "Sy" + "A" * 33,
    "slack_token": "xox" + "b-" + "0" * 12 + "-" + "1" * 12 + "-" + "a" * 24,
    "stripe_key": "sk_live_" + "abcdefghijklmnop1234",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r5wW1g",
    "authorization_header": "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
    "connection_string_password": "Server=db;User Id=sa;Password=Sup3rSecretValue;",
    "url_credentials": "postgres://admin:Hunter2Hunter2@db.internal:5432/app",
    "assigned_secret": 'api_key = "kdo39ffnDKE93jfnEKD93jfn"',
    "private_key_block": "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAx\n-----END " + "RSA PRIVATE KEY-----",
}

SAFE_TEXT = [
    "The build finished in 12 seconds with no errors.",
    "See docs/security.md for the permission model.",
    "OPENAI_API_KEY=",
    "api_key = ${OPENAI_API_KEY}",
    "password = changeme",
    "token: <redacted>",
    "Run python -m pytest -q to check the suite.",
]


@pytest.mark.parametrize("kind", sorted(FIXTURES))
def test_every_credential_shape_is_detected(kind):
    findings = scan(FIXTURES[kind])
    assert findings, f"{kind} was not detected"


@pytest.mark.parametrize("kind", sorted(FIXTURES))
def test_redaction_removes_the_material_but_keeps_diagnostics(kind):
    original = FIXTURES[kind]
    redacted, findings = redact_text(original)
    assert findings
    assert "[REDACTED:" in redacted
    assert "sha256=" in redacted and "len=" in redacted
    secret = findings[0]
    assert original[secret.start : secret.end] not in redacted


@pytest.mark.parametrize("text", SAFE_TEXT)
def test_ordinary_text_and_placeholders_are_untouched(text):
    assert scan(text) == [], f"false positive on {text!r}"
    assert redact_text(text)[0] == text


def test_value_level_redaction_not_only_keys():
    """Regression: the previous implementation matched dictionary keys only."""
    payload = {"stdout": f"exporting {FIXTURES['openai_key']} now", "exit_code": 0}
    cleaned = redact(payload)
    assert FIXTURES["openai_key"] not in json.dumps(cleaned)
    assert cleaned["exit_code"] == 0


def test_key_named_secrets_are_still_redacted():
    cleaned = redact({"api_key": "anything-at-all-here", "note": "fine"})
    assert cleaned["api_key"].startswith("[REDACTED:key:")
    assert cleaned["note"] == "fine"


def test_redaction_is_recursive_through_lists_and_nesting():
    payload = {"a": [{"b": {"c": [FIXTURES["github_token"]]}}]}
    assert FIXTURES["github_token"] not in json.dumps(redact(payload))


def test_environment_secrets_are_redacted_even_without_a_pattern(monkeypatch):
    monkeypatch.setenv("SOME_SERVICE_TOKEN", "plainlookingbutsecret12345")
    redactor = Redactor()
    cleaned = redactor.text("the value is plainlookingbutsecret12345 here")
    assert "plainlookingbutsecret12345" not in cleaned
    assert "[REDACTED:environment_secret" in cleaned


def test_contains_secret_helper():
    assert contains_secret({"x": FIXTURES["jwt"]}) is True
    assert contains_secret({"x": "ordinary text"}) is False


# ----------------------------------------------------------------- labels


def test_sensitivity_ordering():
    assert highest([Sensitivity.PUBLIC, Sensitivity.SECRET]) is Sensitivity.SECRET
    assert highest([Sensitivity.PUBLIC, Sensitivity.PROJECT]) is Sensitivity.PROJECT
    assert highest([]) is Sensitivity.PUBLIC


def test_secret_and_ephemeral_must_not_be_persisted():
    with pytest.raises(PermissionError):
        assert_persistable({"x": 1}, Sensitivity.SECRET)
    with pytest.raises(PermissionError):
        assert_persistable({"x": 1}, Sensitivity.EPHEMERAL)
    with pytest.raises(PermissionError):
        assert_persistable({"x": FIXTURES["openai_key"]})
    assert_persistable({"x": "safe"}, Sensitivity.PROJECT)


def test_private_data_needs_authorization_before_transmission():
    with pytest.raises(PermissionError):
        assert_transmittable("payload", Sensitivity.PRIVATE)
    with pytest.raises(PermissionError):
        assert_transmittable("payload", Sensitivity.EPHEMERAL)
    assert_transmittable("payload", Sensitivity.PRIVATE, authorized=True)
    assert_transmittable("payload", Sensitivity.PUBLIC)


def test_transmission_blocked_when_payload_carries_credentials():
    with pytest.raises(PermissionError):
        assert_transmittable(FIXTURES["aws_access_key_id"], Sensitivity.PUBLIC)


# ----------------------------------------------------------- persistence paths


def test_logs_do_not_receive_secret_values(tmp_path, monkeypatch):
    from app import logging as app_logging

    monkeypatch.setattr(app_logging, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app_logging, "load_settings", lambda: {"log_directory": "logs"})
    logger = app_logging.TaskLogger("task-secret-test")
    logger.log("action", result={"stdout": f"key={FIXTURES['openai_key']}"}, arguments={"command": "echo"})
    written = logger.path.read_text(encoding="utf-8")
    assert FIXTURES["openai_key"] not in written
    assert "[REDACTED:" in written


def test_events_do_not_receive_secret_values(tmp_path):
    bus = EventBus(tmp_path / "events.db")
    try:
        # Redaction happens synchronously inside publish(), before the record
        # is queued, so the secret never reaches the writer thread or disk.
        # Capture the in-flight record to prove that, rather than only
        # checking what was eventually persisted.
        delivered: list[dict] = []
        bus.subscribe("TASK_STARTED", delivered.append)
        bus.publish("TASK_STARTED", "test", {"goal": f"deploy using {FIXTURES['stripe_key']}"})
        assert FIXTURES["stripe_key"] not in json.dumps(delivered)

        # Persistence is asynchronous now; flush before reading it back.
        bus.flush()
        stored = json.dumps(bus.recent(1))
        assert FIXTURES["stripe_key"] not in stored
        assert "[REDACTED:" in stored
    finally:
        bus.close()


def test_memory_does_not_store_secret_values(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    database.store_memory("semantic", f"the deploy token is {FIXTURES['github_token']}", 0.9, "user")
    rows = database.retrieve("deploy token", limit=5)
    blob = json.dumps(rows)
    assert FIXTURES["github_token"] not in blob


def test_conversation_history_does_not_store_secret_values(tmp_path):
    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    database.record_message("session-1", "user", f"my key is {FIXTURES['anthropic_key']}")
    blob = json.dumps(database.conversation_history("session-1", 10))
    assert FIXTURES["anthropic_key"] not in blob


def test_task_history_and_checkpoints_are_redacted():
    from app.agent.core import redact_ephemeral_event

    event = {
        "step": 1,
        "tool": "terminal",
        "arguments": {"command": "echo deploy"},
        "result": {"success": True, "output": {"stdout": FIXTURES["url_credentials"]}},
        "verification": {"passed": True},
    }
    persistent = redact_ephemeral_event(event)
    assert "Hunter2Hunter2" not in json.dumps(persistent)
    assert persistent["verification"]["passed"] is True


def test_ephemeral_outputs_keep_metadata_only():
    from app.agent.core import redact_ephemeral_event

    event = {
        "result": {
            "success": True,
            "output": {"_ephemeral": True, "type": "text", "characters": 42, "sha256": "abc", "content": "clipboard text"},
        }
    }
    persistent = redact_ephemeral_event(event)
    output = persistent["result"]["output"]
    assert "content" not in output
    assert output["memory_policy"] == "not_persisted"
    assert output["characters"] == 42


def test_training_records_are_redacted_and_counted(tmp_path):
    pipeline = TrainingDataPipeline(tmp_path)
    record = pipeline.ingest(
        "conversation",
        {"input": f"use {FIXTURES['openai_key']}", "output": "ok", "api_key": "abcdefghijklmn"},
        provenance={"source": "test"},
    )
    assert record["redactions"] >= 2
    with pipeline._connect() as db:
        stored = db.execute("SELECT sample_json FROM training_records WHERE id=?", (record["id"],)).fetchone()[0]
    assert FIXTURES["openai_key"] not in stored
    assert "abcdefghijklmn" not in stored


def test_retrieval_index_refuses_credential_bearing_content(tmp_path):
    from app.memory.retrieval import RetrievalIndex

    database = MemoryDatabase(tmp_path / "memory.db")
    database.initialize()
    index = RetrievalIndex(database, None, project_root=tmp_path)
    assert index.upsert("note", "safe", "Safe", "This is a perfectly ordinary project note about testing.") is True
    assert index.upsert("note", "bad", "Bad", f"deployment guide {FIXTURES['jwt']} follow these steps carefully") is False
