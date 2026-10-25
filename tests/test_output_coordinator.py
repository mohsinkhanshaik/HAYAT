from __future__ import annotations

import io
import threading

from app.output import OutputCoordinator, get_output, set_output


def test_message_is_written_whole_on_its_own_line():
    stream = io.StringIO()
    OutputCoordinator(stream).emit("You: hello there", source="voice_transcript")
    assert stream.getvalue() == "You: hello there\n"


def test_background_emit_redraws_an_active_prompt_instead_of_overwriting_it():
    """The corruption: a background write landed at the cursor while the main
    thread sat in input("> "), merging into the prompt and the user's typed
    text. The prompt must be erased, the message written cleanly, and the
    prompt restored."""
    stream = io.StringIO()
    coordinator = OutputCoordinator(stream, width=20)
    with coordinator.prompt("> "):
        coordinator.emit("AI: Notepad is open.", source="assistant")
    value = stream.getvalue()
    assert "AI: Notepad is open.\n" in value
    assert value.startswith("\r" + " " * 20 + "\r"), "prompt line must be erased first"
    assert value.endswith("> "), "prompt must be redrawn after the message"


def test_no_prompt_means_no_erase_sequence():
    stream = io.StringIO()
    OutputCoordinator(stream, width=20).emit("plain")
    assert stream.getvalue() == "plain\n"


def test_concurrent_writers_never_interleave_within_a_line():
    """100+ interleaved events from many threads, as the spec's stress case
    requires: every line must arrive whole, none lost, none merged."""
    stream = io.StringIO()
    coordinator = OutputCoordinator(stream, width=40)
    sources = ["voice_transcript", "assistant", "approval", "camera", "research"]
    expected = [f"{source}-{index:03d}" for source in sources for index in range(30)]

    def worker(messages: list[str]) -> None:
        for message in messages:
            coordinator.emit(message, source="stress")

    chunks = [expected[i::5] for i in range(5)]
    threads = [threading.Thread(target=worker, args=(chunk,)) for chunk in chunks]
    with coordinator.prompt("> "):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    written = [line for line in stream.getvalue().replace("\r", "\n").split("\n") if line.strip() and line.strip() != ">"]
    cleaned = [line.strip() for line in written]
    assert len(expected) == 150
    for message in expected:
        assert message in cleaned, f"lost message {message}"
    # Every emitted token appears exactly once -- no duplication, no splicing.
    for message in expected:
        assert cleaned.count(message) == 1, f"message {message} was duplicated or split"


def test_listeners_receive_every_message_for_a_future_ui():
    received: list[tuple[str, str]] = []
    coordinator = OutputCoordinator(io.StringIO())
    coordinator.add_listener(lambda source, text: received.append((source, text)))
    coordinator.emit("hello", source="assistant")
    assert received == [("assistant", "hello")]


def test_gui_listener_keeps_unicode_with_a_legacy_console():
    received = []
    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    coordinator = OutputCoordinator(stream)
    coordinator.add_listener(lambda source, text: received.append(text))
    coordinator.emit("مرحبا 🫡", source="voice_transcript")
    assert received == ["مرحبا 🫡"]


def test_a_broken_listener_cannot_break_output():
    stream = io.StringIO()
    coordinator = OutputCoordinator(stream)
    coordinator.add_listener(lambda source, text: (_ for _ in ()).throw(RuntimeError("ui closed")))
    coordinator.emit("still written")
    assert "still written" in stream.getvalue()


def test_coordinator_follows_a_swapped_sys_stdout():
    """prompt_toolkit's patch_stdout() replaces sys.stdout with a proxy that
    writes above the prompt. Capturing the stream at construction would bypass
    that proxy and reintroduce the corruption, so it must resolve lazily."""
    import sys

    coordinator = OutputCoordinator()
    replacement = io.StringIO()
    original = sys.stdout
    try:
        sys.stdout = replacement
        coordinator.emit("through the proxy")
    finally:
        sys.stdout = original
    assert "through the proxy" in replacement.getvalue()


def test_external_prompt_redraw_suppresses_manual_erase():
    """When a managed prompt owns redraw, doing it here as well would double
    handle and corrupt the line."""
    stream = io.StringIO()
    coordinator = OutputCoordinator(stream, width=20)
    coordinator.external_prompt_redraw = True
    with coordinator.prompt("> "):
        coordinator.emit("AI: hello")
    assert stream.getvalue() == "AI: hello\n"


def test_console_reader_falls_back_when_prompt_toolkit_is_unavailable(monkeypatch):
    """A missing dependency must degrade the console, not break it."""
    import builtins

    from app.output import ConsoleReader

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("prompt_toolkit"):
            raise ImportError("simulated missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    reader = ConsoleReader(OutputCoordinator(io.StringIO()))
    assert reader.managed is False

    monkeypatch.setattr(builtins, "input", lambda *args: "typed text")
    assert reader.read("> ") == "typed text"


def test_process_wide_coordinator_is_replaceable_for_tests():
    original = get_output()
    try:
        stream = io.StringIO()
        replacement = OutputCoordinator(stream)
        set_output(replacement)
        assert get_output() is replacement
    finally:
        set_output(original)
