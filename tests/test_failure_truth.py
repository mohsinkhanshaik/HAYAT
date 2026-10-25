"""Failures are recorded facts, not something the model reconstructs later.

The incident this subsystem exists for: a real session hit WinError 10061 --
the local Ollama endpoint refusing a connection -- and when the user asked
what was wrong moments later, the assistant suggested a possible internet
problem. Nothing had recorded the error, so the model filled the gap with
something plausible and false. These tests hold the line at both ends: the
failure is classified from evidence where it happens, and the answer to "what
happened?" is read back from that record.
"""
from __future__ import annotations

import socket

import pytest

from app.execution import execution_scope
from app.failures import (
    FailureClass,
    FailureLog,
    FailureRecord,
    classify_exception,
    classify_http_status,
)


# ------------------------------------------------------------ classification


def test_a_refused_local_connection_is_a_downed_dependency_not_a_network_guess():
    assert classify_exception(ConnectionRefusedError(61, "Connection refused")) is FailureClass.DEPENDENCY_DOWN


def test_the_actual_windows_error_text_classifies_correctly():
    """WinError 10061, verbatim from the real incident."""
    exc = OSError(
        "[WinError 10061] No connection could be made because the target machine actively refused it"
    )
    assert classify_exception(exc) is FailureClass.DEPENDENCY_DOWN


@pytest.mark.parametrize(
    "exc,expected",
    [
        (TimeoutError("timed out"), FailureClass.TIMEOUT),
        (PermissionError("access is denied"), FailureClass.PERMISSION_DENIED),
        (FileNotFoundError("no such file"), FailureClass.NOT_FOUND),
        (ValueError("bad argument"), FailureClass.INVALID_INPUT),
        (MemoryError(), FailureClass.RESOURCE_EXHAUSTED),
        (RuntimeError("something else"), FailureClass.UNKNOWN),
    ],
)
def test_exception_classification_is_deterministic(exc, expected):
    assert classify_exception(exc) is expected


@pytest.mark.parametrize(
    "status,expected",
    [
        (404, FailureClass.NOT_FOUND), (429, FailureClass.RATE_LIMITED),
        (401, FailureClass.PERMISSION_DENIED), (503, FailureClass.DEPENDENCY_DOWN),
        (400, FailureClass.INVALID_INPUT),
    ],
)
def test_http_status_classification(status, expected):
    assert classify_http_status(status) is expected


def test_only_transient_classes_are_marked_retryable():
    assert FailureRecord(failure_class=FailureClass.DEPENDENCY_DOWN).retryable
    assert FailureRecord(failure_class=FailureClass.TIMEOUT).retryable
    assert not FailureRecord(failure_class=FailureClass.POLICY_DENIED).retryable
    assert not FailureRecord(failure_class=FailureClass.INVALID_INPUT).retryable


# ------------------------------------- the real provider failure, reproduced


def _closed_port() -> int:
    """A port nothing is listening on, obtained without occupying it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_ollama_refusing_a_connection_produces_classified_provider_evidence():
    """§19's safe equivalent of the historical incident.

    A real HTTP attempt against a local port with nothing behind it. No
    mocking of the failure mode: the OS refuses it exactly as it refused the
    user's 11434.
    """
    from app.models.base import ModelError
    from app.models.local import OllamaProvider

    provider = OllamaProvider(f"http://127.0.0.1:{_closed_port()}", model="qwen3.5:latest", timeout=5.0)

    with pytest.raises(ModelError) as caught:
        provider.generate([{"role": "user", "content": "hello"}])

    error = caught.value
    fields = error.failure_fields()
    assert fields["dependency"] == "Ollama"
    assert fields["failure_class"] is FailureClass.DEPENDENCY_DOWN
    assert "127.0.0.1" in fields["endpoint"]


def test_the_recorded_failure_names_the_local_dependency_and_rules_out_the_internet():
    """The sentence the user should have seen the first time."""
    log = FailureLog()
    failure = log.record(
        stage="model", provider="ollama", dependency="Ollama",
        endpoint="http://localhost:11434/api/chat",
        exception_type="ConnectError",
        exception_message="[WinError 10061] the target machine actively refused it",
        failure_class=FailureClass.DEPENDENCY_DOWN,
        user_request="summarise this file",
    )
    summary = failure.summary()

    assert "DEPENDENCY_DOWN" in summary
    assert "Ollama" in summary
    assert "localhost:11434" in summary
    assert "not an internet problem" in summary


def test_a_provider_failure_is_recorded_with_the_provider_s_own_evidence():
    """The generic classifier must not overrule what the provider knew."""
    from app.models.base import ModelError

    log = FailureLog()
    error = ModelError(
        "Ollama request failed", provider="ollama", endpoint="http://localhost:11434/api/chat",
        dependency="Ollama", failure_class=FailureClass.DEPENDENCY_DOWN,
    )
    failure = log.from_exception(error, stage="model", **error.failure_fields())

    assert failure.failure_class is FailureClass.DEPENDENCY_DOWN
    assert failure.dependency == "Ollama"
    assert failure.retryable


# ------------------------------------------------------------- correlation


def test_a_failure_inherits_the_turn_and_task_in_flight():
    """A failure and the approval that preceded it line up on the same task."""
    log = FailureLog()
    with execution_scope(session_id="s1", turn_id="t1", task_id="task-7"):
        failure = log.from_exception(RuntimeError("boom"), stage="tool", tool="write_file")

    assert (failure.session_id, failure.turn_id, failure.task_id) == ("s1", "t1", "task-7")


def test_explicit_correlation_beats_the_ambient_scope():
    log = FailureLog()
    with execution_scope(session_id="s1", turn_id="t1", task_id="task-7"):
        failure = log.record(stage="tool", task_id="task-explicit")
    assert failure.task_id == "task-explicit"


# --------------------------------------------------------------- redaction


def test_credentials_never_reach_a_failure_record():
    """§21. A diagnostic that leaks a token is worse than the bug it reports."""
    log = FailureLog()
    failure = log.record(
        stage="research",
        exception_message="401 for https://api.example.com with Authorization: Bearer sk-example-abcd1234efgh5678",
        user_request="log in with password hunter2correcthorse",
    )
    blob = str(failure.to_dict())

    assert "sk-example-abcd1234efgh5678" not in blob
    assert "hunter2correcthorse" not in blob


def test_a_credential_spoken_in_prose_is_redacted_like_one_that_is_assigned():
    """Voice produces "my password is X", never "password=X"."""
    from app.safety.sensitive import redact

    assert "swordfish99tuna" not in redact("my password is swordfish99tuna")
    assert "abcd1234efgh5678" not in redact("use api key abcd1234efgh5678 for that")


def test_prose_about_a_credential_stays_readable():
    """Over-redaction that mangles ordinary sentences is its own failure."""
    from app.safety.sensitive import redact

    assert redact("the password is incorrect") == "the password is incorrect"
    assert redact("password authentication failed") == "password authentication failed"


def test_tracebacks_are_bounded():
    log = FailureLog()
    failure = log.record(stage="tool", traceback="x" * 50_000)
    assert len(failure.traceback) <= FailureRecord.MAX_TRACEBACK_CHARS


def test_from_exception_captures_a_traceback_when_one_exists():
    log = FailureLog()
    try:
        raise ValueError("deliberate")
    except ValueError as exc:
        failure = log.from_exception(exc, stage="tool")
    assert failure.traceback and "ValueError" in failure.traceback


# --------------------------------------------------- the tool boundary


def _registry_with_failures():
    from app.safety.permissions import PermissionManager
    from app.tools.registry import ToolRegistry

    log = FailureLog()
    registry = ToolRegistry(PermissionManager(), lambda *a, **k: False)
    registry.failures = log
    return registry, log


def test_a_tool_that_raises_becomes_a_recorded_failure():
    registry, log = _registry_with_failures()

    def explode(**kwargs):
        raise FileNotFoundError("D:/AI/missing.txt")

    registry.register("read_thing", "read", {"type": "object", "properties": {}}, "low", "filesystem_read", explode)

    with execution_scope(session_id="s1", turn_id="t1", task_id="task-3"):
        result = registry.execute("read_thing", {})

    assert not result.success
    failure = log.last
    assert failure is not None
    assert failure.stage == "tool" and failure.tool == "read_thing"
    assert failure.failure_class is FailureClass.NOT_FOUND
    assert failure.task_id == "task-3", "the failure names the task that was running"


def test_a_refused_tool_is_recorded_as_a_policy_denial_not_a_crash():
    from app.safety.authority import AuthorityManager

    registry, log = _registry_with_failures()
    registry.authority = AuthorityManager(owner_mode=True)
    registry.register(
        "post_externally", "post", {"type": "object", "properties": {}}, "high", "external_post",
        lambda **kw: {"ran": True},
    )

    result = registry.execute("post_externally", {})

    assert not result.success
    assert log.last is not None
    assert log.last.failure_class is FailureClass.POLICY_DENIED


def test_a_successful_tool_records_nothing():
    registry, log = _registry_with_failures()
    registry.register(
        "fine", "ok", {"type": "object", "properties": {}}, "low", "filesystem_read",
        lambda **kw: {"ok": True},
    )
    assert registry.execute("fine", {}).success
    assert log.last is None


# ---------------------------------------------------- asking what happened


@pytest.mark.parametrize(
    "question",
    [
        "What happened?", "what's wrong?", "What failed?", "what connection failed?",
        "What went wrong?", "why did it fail?", "what was the last error?",
    ],
)
def test_natural_failure_questions_route_to_the_diagnostic_path(question):
    from app.intent.models import IntentKind
    from app.intent.router import IntentRouter

    intent = IntentRouter().route(question)
    assert intent.intent is IntentKind.DIAGNOSTIC
    assert intent.entities.get("failure_inquiry") is True


def test_asking_what_happened_reads_the_record_not_the_model(tmp_path):
    """The whole point, end to end.

    The assistant is built with no model at all. If the answer still names the
    endpoint, the dependency and the classification, then it came from the
    record -- there is nothing else it could have come from.
    """
    from tests.test_assistant_service import runtime as assistant_runtime

    rt = assistant_runtime(tmp_path)
    rt.failures = FailureLog()
    rt.failures.record(
        stage="model", provider="ollama", dependency="Ollama",
        endpoint="http://localhost:11434/api/chat",
        exception_type="ConnectError",
        exception_message="[WinError 10061] the target machine actively refused it",
        failure_class=FailureClass.DEPENDENCY_DOWN,
        user_request="summarise this file",
    )

    reply = rt.assistant.handle("What happened?")
    answer = reply.display_response

    assert "DEPENDENCY_DOWN" in answer
    assert "localhost:11434" in answer
    assert "not an internet problem" in answer
    assert rt.manager.calls == [], "a failure question must not start a task"


def test_with_nothing_recorded_the_assistant_does_not_invent_a_failure(tmp_path):
    from tests.test_assistant_service import runtime as assistant_runtime

    rt = assistant_runtime(tmp_path)
    rt.failures = FailureLog()

    answer = rt.assistant.handle("What happened?").display_response

    assert "DEPENDENCY_DOWN" not in answer
    assert "Ollama" not in answer


def test_ordinary_questions_are_not_swallowed_by_the_failure_path():
    """The pattern must not capture normal conversation."""
    from app.intent.router import IntentRouter

    router = IntentRouter()
    for text in ("what is the capital of France?", "what happened in 1969?", "open notepad"):
        intent = router.route(text)
        assert not intent.entities.get("failure_inquiry"), f"{text!r} is not a failure inquiry"
