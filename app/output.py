"""Single serialized owner of terminal output.

In --combined mode the main thread sits inside input("> ") holding a prompt and
whatever the user has typed so far, while background threads (the voice
listener, the voice task worker, approval prompts) wrote to stdout directly.
A write from another thread lands wherever the cursor happens to be, so it
overwrites the prompt and the user's in-progress line -- observed as merged
messages, vanished user input, and camera/provenance fragments interleaved with
unrelated conversation turns.

Every user-facing write now goes through one lock here. When a background
message arrives while a prompt is displayed, the prompt line is erased, the
message is written on its own clean line, and the prompt is redrawn. Workers
publish; this object is the only thing that writes.
"""
from __future__ import annotations

import logging
import sys
import time
import threading
from contextlib import contextmanager
from typing import Any, Callable, TextIO

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def enable_unicode_console() -> str:
    """Make stdout able to carry what HAYAT actually says.

    The Windows console starts as cp1252, which cannot encode an emoji. Every
    `print()` in the CLI therefore raises UnicodeEncodeError the moment a
    reply contains one -- and HAYAT is now asked to be warm and to use the odd
    emoji, so that is the normal case rather than a rare one. The coordinator
    already degrades gracefully, but bare `print()` does not go through it.

    Switching the stream to UTF-8 fixes every path at once, including the ones
    that were never routed through the coordinator. `errors="replace"` is the
    backstop for a console that still cannot render a glyph: a replacement
    character is a cosmetic problem, an exception mid-reply is a lost answer.
    """
    changed = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") in {"utf8", "utf8mb4"}:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
            changed.append(name)
        except Exception:
            # A redirected or captured stream may refuse; the coordinator's
            # own degradation still covers anything written through it.
            log_swallowed(_log, "switching a console stream to UTF-8")
    return ", ".join(changed)


class OutputCoordinator:
    """Thread-safe, prompt-aware console writer."""

    def __init__(self, stream: TextIO | None = None, width: int = 120) -> None:
        # Resolved lazily when not pinned explicitly: prompt_toolkit's
        # patch_stdout() swaps sys.stdout for a proxy that writes above the
        # prompt. Capturing the original stream here would bypass that proxy
        # and reintroduce the corruption this class exists to prevent.
        self._explicit_stream = stream
        self._lock = threading.RLock()
        self._prompt = ""
        self._prompt_visible = False
        self._width = width
        self._listeners: list[Callable[[str, str], None]] = []
        # Set by attach_spinner(). emit() clears it before writing so a
        # message never lands on top of a half-drawn status line.
        self._spinner: Any = None
        # Set while a managed prompt (prompt_toolkit) owns redraw. That layer
        # already reprints the prompt and the user's in-progress text, so the
        # manual erase/redraw below must stand down rather than double-handle.
        self.external_prompt_redraw = False

    @property
    def _stream(self) -> TextIO:
        return self._explicit_stream if self._explicit_stream is not None else sys.stdout

    def add_listener(self, listener: Callable[[str, str], None]) -> None:
        """Subscribe to every emitted message as (source, text).

        A future UI renders from this instead of scraping stdout.
        """
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[str, str], None]) -> None:
        """Unsubscribe. Silent when the listener is already gone.

        Needed by anything that listens for the duration of one operation --
        the window captures output while a slash command runs. Without a way
        off the list, every command would leave another listener behind and
        the same line would be delivered once more each time.
        """
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def _encodable(self, text: str) -> str:
        """Make text safe for whatever this console can actually render.

        The Windows console here reports cp1252, which has no emoji. Writing
        one raises UnicodeEncodeError from inside the write, and since output
        is what reports everything else, a reply containing an emoji would
        take down the channel used to explain the failure. HAYAT is meant to
        use emoji, so this is a certainty rather than an edge case.

        Characters the console cannot represent are dropped rather than
        replaced with question marks: a sentence missing a decorative symbol
        reads fine, and one peppered with "?" looks like corruption.
        """
        encoding = getattr(self._stream, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
            return text
        except (UnicodeEncodeError, LookupError):
            pass
        from app.intent.emoji import strip_emoji

        reduced = strip_emoji(text)
        try:
            reduced.encode(encoding)
            return reduced
        except (UnicodeEncodeError, LookupError):
            return reduced.encode(encoding, "ignore").decode(encoding, "ignore")

    def emit(self, text: str, *, source: str = "system") -> None:
        """Write one message on its own line, preserving any active prompt."""
        if text is None:
            return
        rendered = self._encodable(str(text))
        with self._lock:
            # A status line is transient; a message is not. Clear the spinner
            # first so a reply never lands on top of half a spinner frame.
            spinner = self._spinner
            if spinner is not None:
                spinner.erase()
            manual_redraw = self._prompt_visible and not self.external_prompt_redraw
            if manual_redraw:
                self._erase_prompt_line()
            self._stream.write(rendered + "\n")
            if manual_redraw:
                self._stream.write(self._prompt)
            self._flush()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                # GUI listeners support Unicode even when a legacy terminal
                # cannot. Never lose spoken words or emoji in the transcript.
                listener(source, str(text))
            except Exception:
                # A broken listener (e.g. a closing UI) must never take down
                # the console or the thread that produced the message.
                log_swallowed(_log, "output listener")

    def attach_spinner(self, registry: Any = None) -> "Spinner":
        """Create the status line this console will show while work runs."""
        spinner = Spinner(self, registry)
        with self._lock:
            self._spinner = spinner
        return spinner

    @contextmanager
    def prompt(self, prompt: str):
        """Mark a prompt as displayed for the duration of a blocking read.

        Background emits during this window erase and redraw the prompt rather
        than writing over it.
        """
        with self._lock:
            self._prompt = prompt
            self._prompt_visible = True
        try:
            yield
        finally:
            with self._lock:
                self._prompt_visible = False
                self._prompt = ""

    def _erase_prompt_line(self) -> None:
        # \r + spaces + \r rather than an ANSI erase sequence: this has to work
        # in plain conhost, redirected output, and captured test streams alike.
        self._stream.write("\r" + " " * self._width + "\r")

    def _flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            log_swallowed(_log, "flushing console output")


class Spinner:
    """A live status line: something is happening, and this is what.

    Written through the coordinator's lock rather than to the stream directly,
    for the reason that class exists at all -- a background thread animating a
    line while another prints a result is how console output became unreadable
    before. The spinner erases itself before any message and redraws after, so
    a reply never lands on top of it.

    It shows work, not reassurance. The text comes from the activity register,
    so the line says "searching the web" because a search is genuinely running.
    When nothing is registered the spinner shows nothing: an animation that
    keeps turning after the work stopped is worse than no animation, because
    it is a claim rather than a decoration.
    """

    #: Braille renders beautifully and is unprintable on the cp1252 console
    #: this runs on, so the frames are chosen from what the stream can encode.
    UNICODE_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    ASCII_FRAMES = "|/-\\"

    def __init__(self, coordinator: "OutputCoordinator", registry: Any = None,
                 interval: float = 0.12) -> None:
        self._coordinator = coordinator
        self._registry = registry
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._frames = self._usable_frames()
        self._drawn = False
        self._last_width = 0
        self._last_emit = 0.0
        self._last_emitted_text = ""

    def _usable_frames(self) -> str:
        encoding = getattr(self._coordinator._stream, "encoding", None) or "utf-8"
        try:
            self.UNICODE_FRAMES.encode(encoding)
            return self.UNICODE_FRAMES
        except (UnicodeEncodeError, LookupError):
            return self.ASCII_FRAMES

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        """Begin animating. False when one is already running."""
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ai-spinner", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None
        self.erase()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @contextmanager
    def active(self):
        """Animate for the duration of a block, whatever happens inside it."""
        self.start()
        try:
            yield self
        finally:
            self.stop()

    def follow(self) -> None:
        """Show the status line whenever anything is working, from anywhere.

        Wrapping the typed turn covers typed turns and nothing else. In
        combined mode a spoken request runs through the voice worker, which
        never passes through that wrapper -- so asking out loud produced a
        silent wait while typing the same words showed progress. Following the
        register instead means every path is covered, including background
        work, because the spinner reacts to work existing rather than to one
        call site remembering to announce it.
        """
        if self._registry is None:
            return
        self._registry.add_listener(self._on_activity_change)

    def _on_activity_change(self, registry: Any) -> None:
        try:
            busy = registry.busy
        except Exception:
            return
        if busy and not self.running:
            self.start()
        elif not busy and self.running:
            self.stop()

    # -------------------------------------------------------------- drawing

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            self._draw(self._frames[index % len(self._frames)])
            index += 1
            self._stop.wait(self._interval)

    def _describe(self) -> str:
        if self._registry is None:
            return "thinking"
        try:
            return self._registry.headline() or "thinking"
        except Exception:
            return "thinking"

    #: How often to say something when the status line cannot be drawn in
    #: place. Long enough not to scroll the conversation away, short enough
    #: that a slow task does not look like a hang.
    FALLBACK_INTERVAL_SECONDS = 4.0

    def _draw(self, frame: str) -> None:
        coordinator = self._coordinator
        # A managed prompt (prompt_toolkit) owns the bottom line, so drawing
        # in place there would fight it. Standing down entirely was wrong
        # though: in --combined mode a spoken request arrives while the reader
        # is blocked on that prompt, so voice turns showed nothing at all
        # while typed ones animated. Emit instead -- patch_stdout places it
        # safely above the prompt -- throttled so it reads as progress rather
        # than spam.
        if coordinator.external_prompt_redraw:
            self._emit_progress()
            return
        # Never paint over a prompt the user is typing into.
        if coordinator._prompt_visible:
            return
        text = f"{frame} {self._describe()}"
        with coordinator._lock:
            line = coordinator._encodable(text)[: coordinator._width - 1]
            # Pad to whatever the previous frame occupied, so a shorter label
            # does not leave the tail of a longer one behind, then return to
            # column zero so the next frame overwrites this one in place.
            padding = " " * max(0, self._last_width - len(line))
            try:
                coordinator._stream.write("\r" + line + padding + "\r")
                coordinator._flush()
                self._last_width = len(line)
                self._drawn = True
            except Exception:
                self._stop.set()

    def _emit_progress(self) -> None:
        """Report progress as a line, for consoles where redraw is not ours."""
        now = time.monotonic()
        if now - self._last_emit < self.FALLBACK_INTERVAL_SECONDS:
            return
        self._last_emit = now
        description = self._describe()
        if description == self._last_emitted_text:
            return
        self._last_emitted_text = description
        self._coordinator.emit(f"  ... {description}", source="progress")

    def erase(self) -> None:
        """Clear the status line so the next message starts clean."""
        if not self._drawn:
            return
        with self._coordinator._lock:
            try:
                self._coordinator._erase_prompt_line()
                self._coordinator._flush()
            except Exception:
                log_swallowed(_log, "erasing the console prompt line")
            self._drawn = False
            self._last_width = 0


class ConsoleReader:
    """Reads a line while background threads keep writing safely.

    prompt_toolkit's patch_stdout() is used when importable because it is the
    only one of the two paths that preserves text the user has already typed:
    plain input() leaves those characters in the terminal's own line buffer,
    so erasing the line to print a message erases them visually and they
    reappear only when the user keeps typing. Measured on this machine, a
    background write during typing keeps the buffer intact under
    patch_stdout and loses it visually under input().

    The plain path stays as a fallback so a missing dependency degrades the
    console instead of breaking it.
    """

    def __init__(self, coordinator: OutputCoordinator | None = None,
                 status_supplier: Callable[[], str] | None = None) -> None:
        self._coordinator = coordinator or get_output()
        self._status_supplier = status_supplier
        self._session: Any = None
        self._patch: Any = None
        try:
            from prompt_toolkit.patch_stdout import patch_stdout
            from prompt_toolkit.shortcuts import PromptSession

            self._session = PromptSession(
                bottom_toolbar=(lambda: self._status_supplier() if self._status_supplier else "")
                if self._status_supplier else None,
                refresh_interval=0.5 if self._status_supplier else None,
            )
            self._patch = patch_stdout
        except Exception:
            self._session = None
            self._patch = None

    @property
    def managed(self) -> bool:
        return self._session is not None

    def read(self, prompt: str = "> ") -> str:
        if self._session is not None:
            self._coordinator.external_prompt_redraw = True
            try:
                with self._patch(raw=True):
                    return self._session.prompt(prompt)
            finally:
                self._coordinator.external_prompt_redraw = False
        with self._coordinator.prompt(prompt):
            return input(prompt)


_COORDINATOR: OutputCoordinator | None = None
_COORDINATOR_LOCK = threading.Lock()


def get_output() -> OutputCoordinator:
    """Process-wide coordinator. One terminal means one owner."""
    global _COORDINATOR
    with _COORDINATOR_LOCK:
        if _COORDINATOR is None:
            _COORDINATOR = OutputCoordinator()
        return _COORDINATOR


def set_output(coordinator: OutputCoordinator | None) -> None:
    """Replace the process-wide coordinator (tests, or a UI taking over)."""
    global _COORDINATOR
    with _COORDINATOR_LOCK:
        _COORDINATOR = coordinator


def emit(text: Any, *, source: str = "system") -> None:
    get_output().emit(text, source=source)
