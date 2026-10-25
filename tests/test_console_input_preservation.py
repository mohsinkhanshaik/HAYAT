from __future__ import annotations

import threading
import time

import pytest

prompt_toolkit = pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import PromptSession


def test_background_writes_do_not_destroy_in_progress_typing():
    """The property that justified taking prompt_toolkit as a dependency.

    Plain input() leaves typed characters in the terminal's own line buffer,
    so erasing the line to print a background message erases them visually.
    patch_stdout writes above the prompt and redraws it together with whatever
    the user had already typed. This is the difference between the console
    corruption being mitigated and being actually fixed, so it is worth
    holding in place with a test.
    """
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            session = PromptSession()
            captured: dict[str, str] = {}

            def type_then_interrupt() -> None:
                pipe.send_text("hello wor")
                time.sleep(0.2)
                # Two background threads writing mid-word, as the voice
                # listener and task worker do in --combined mode.
                print("AI: Notepad is open.")
                print("You: (voice transcript)")
                time.sleep(0.2)
                pipe.send_text("ld\n")

            writer = threading.Thread(target=type_then_interrupt, daemon=True)
            writer.start()
            with patch_stdout():
                captured["text"] = session.prompt("> ")
            writer.join(timeout=3)

    assert captured["text"] == "hello world"
