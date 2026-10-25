from __future__ import annotations

from app.cli import describe_turn_failure
from app.models.base import ModelError


def test_a_stopped_ollama_is_explained_with_a_recovery_step():
    """The real failure seen in this project: Ollama stopped mid-session, the
    router still held live providers, generate() raised, and the unguarded
    conversation path took the whole CLI down with it. The message has to name
    the cause and the fix, not print a class name."""
    exc = ModelError(
        "All bounded model fallbacks failed: qwen3.5:latest: ModelError: Ollama request failed: "
        "[WinError 10061] No connection could be made because the target machine actively refused it"
    )
    message = describe_turn_failure(exc)
    assert "Ollama" in message
    assert "isn't running" in message
    assert "/doctor" in message
    assert "Traceback" not in message


def test_timeouts_are_described_as_timeouts():
    assert "timed out" in describe_turn_failure(TimeoutError("read timed out")).lower()


def test_unknown_failures_still_name_the_exception_and_keep_the_session():
    message = describe_turn_failure(ValueError("something specific broke"))
    assert "ValueError" in message
    assert "something specific broke" in message
    assert "still running" in message
    # The one thing that is never acceptable when a real diagnostic exists.
    assert "unknown error" not in message.lower()
