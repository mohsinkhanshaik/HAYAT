"""Threads, and the resources that only belong to one of them.

HAYAT is multi-threaded by necessity: tools run on the agent thread, the
learner researches on its own, the event bus writes on a third, the GUI draws
on a fourth, voice listens on a fifth, and research fetches pages on a pool.
Most state here is safe under that. Playwright's driver is not -- it belongs
to the thread that created it -- and the gap between those two facts crashed
real conversations twice.
"""
from __future__ import annotations

import concurrent.futures as cf
import threading
import time

import pytest

from app.failures import FailureClass, classify_exception
from app.tools.browser_host import HostStopped, PlaywrightHost


# ------------------------------------------------- the host, without a browser


@pytest.fixture
def host():
    made = PlaywrightHost(name="test-owner")
    yield made
    made.shutdown()


def test_work_runs_on_one_thread_no_matter_who_asks(host):
    """The property the whole fix rests on."""
    seen: list[int] = []
    host.run(lambda: seen.append(threading.get_ident()))

    with cf.ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: host.run(lambda: seen.append(threading.get_ident())), range(32)))

    assert len(seen) == 33
    assert len(set(seen)) == 1  # every one of them on the same thread
    assert seen[0] == host.owner_thread.ident


def test_a_call_from_the_owner_thread_does_not_deadlock(host):
    """The browser methods call each other -- navigate calls _page calls
    launch. Routing an inner call through the queue would mean waiting for a
    thread that is already busy waiting for us."""
    def outer():
        assert host.on_owner_thread
        return host.run(lambda: host.run(lambda: "three deep"))

    assert host.run(outer, timeout=5) == "three deep"


def test_an_exception_comes_back_to_the_caller_intact(host):
    """A real browser failure must still look like a browser failure. Only
    the failure caused by which thread asked should disappear."""
    def boom():
        raise ValueError("the page did not load")

    with pytest.raises(ValueError, match="the page did not load"):
        host.run(boom)

    # And the host is still usable afterwards.
    assert host.run(lambda: "still working") == "still working"


def test_work_that_never_returns_gives_up_rather_than_hanging(host):
    with pytest.raises(TimeoutError, match="within 1s"):
        host.run(lambda: time.sleep(30), timeout=1.0)


def test_the_owner_thread_is_started_lazily(host):
    """Nothing should pay for a browser that is never opened."""
    assert host.owner_thread is None
    host.run(lambda: None)
    assert host.owner_thread is not None and host.owner_thread.is_alive()


def test_a_shut_down_host_refuses_work_instead_of_hanging(host):
    host.run(lambda: None)
    host.shutdown()
    assert not host.owner_thread.is_alive()
    with pytest.raises(HostStopped):
        host.run(lambda: None)
    host.shutdown()  # idempotent


def test_concurrent_callers_all_get_their_own_answer(host):
    """Results must not be crossed between waiting threads."""
    with cf.ThreadPoolExecutor(16) as pool:
        answers = list(pool.map(lambda n: host.run(lambda value=n: value * 2), range(64)))
    assert answers == [n * 2 for n in range(64)]


# -------------------------------------------------------- naming the failure


def test_a_wrong_thread_error_is_no_longer_called_unknown():
    """It arrived as UNKNOWN and was attributed to Ollama, which was not
    involved. One cause, one fix, and now one name."""
    class GreenletError(Exception):
        pass

    exc = GreenletError(
        "Cannot switch to a different thread\n"
        "\tCurrent:  <greenlet.greenlet object at 0x1 (otid=0x2) current active started main>\n"
        "\tExpected: <greenlet.greenlet object at 0x3 (otid=0x4) suspended active started main>"
    )
    assert classify_exception(exc) is FailureClass.THREAD_CONFINEMENT


def test_the_lowercase_variant_is_caught_too():
    """greenlet phrases it both ways depending on how the thread ended."""
    exc = RuntimeError("cannot switch to a different thread (which happens to have exited)")
    assert classify_exception(exc) is FailureClass.THREAD_CONFINEMENT


def test_it_is_not_mistaken_for_a_timeout_or_bad_input():
    """Every rule below it in the classifier would have mislabelled this."""
    exc = RuntimeError("Cannot switch to a different thread")
    assert classify_exception(exc) is not FailureClass.TIMEOUT
    assert classify_exception(exc) is not FailureClass.INVALID_INPUT
    assert classify_exception(exc) is not FailureClass.UNKNOWN


def test_mohsin_is_told_what_it_was_rather_than_shown_a_traceback():
    from app.cli import describe_turn_failure

    said = describe_turn_failure(RuntimeError(
        "error: Cannot switch to a different thread\n\tCurrent: <greenlet...>"))
    assert "browser" in said.lower()
    assert "nothing to do with the model" in said.lower()
    assert "greenlet" not in said.lower()  # not the user's vocabulary


# ------------------------------------------------ the browser, really launched


browser_required = pytest.mark.skipif(
    not __import__("shutil").which("chrome")
    and not __import__("pathlib").Path(
        r"C:\Program Files\Google\Chrome\Application\chrome.exe").is_file(),
    reason="no Chrome available to drive",
)


@browser_required
def test_every_public_browser_method_is_confined():
    """Derived from the class, so a method added later cannot be forgotten --
    which a hand-written list of exclusions would eventually be."""
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools

    tools = BrowserTools(PermissionManager())
    public = [name for name in vars(BrowserTools) if not name.startswith("_")
              and callable(getattr(BrowserTools, name))]
    for name in public:
        assert hasattr(getattr(tools, name), "__wrapped__"), f"{name} is not confined"


@browser_required
def test_the_browser_survives_being_driven_from_many_threads():
    """The production path, reproduced: the background learner opens a page
    during research, and a conversational turn later reads it from the thread
    that is answering. That combination raised greenlet.error and killed the
    turn -- twice, in Mohsin's own session."""
    from app.safety.permissions import PermissionManager
    from app.tools.browser import BrowserTools

    tools = BrowserTools(PermissionManager())
    try:
        # Opened on a background thread, exactly as the learner would.
        with cf.ThreadPoolExecutor(1, thread_name_prefix="learner") as learner:
            learner.submit(tools.launch, True).result()
            learner.submit(tools.navigate, "about:blank").result()

        errors: list[str] = []

        def read(_):
            try:
                # page.title() is the call that actually raised. page.url did
                # not, which is why the bug looked intermittent.
                tools.page_state()
                tools.list_tabs()
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

        with cf.ThreadPoolExecutor(8, thread_name_prefix="turn") as pool:
            list(pool.map(read, range(24)))

        assert errors == []
    finally:
        tools.close()


@browser_required
def test_the_state_snapshot_reads_the_real_page_from_another_thread():
    """It used to report the field unreadable at best, and crash at worst."""
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False)
    try:
        with cf.ThreadPoolExecutor(1, thread_name_prefix="opener") as pool:
            pool.submit(runtime.browser.launch, True).result()
            pool.submit(runtime.browser.navigate, "about:blank").result()

        state = runtime.state.snapshot()
        assert state["browser"]["active"] is True
        assert state["browser"]["url"] == "about:blank"
        assert "readable" not in state["browser"]  # it was actually read
        runtime.browser.close()
    finally:
        runtime.close()


# --------------------------------------- what must not be done to this process


def test_nothing_has_monkey_patched_the_threads_out_from_under_us():
    """gevent and eventlet replace real threads with greenlets.

    Playwright's sync driver is itself greenlet-based and breaks under that
    patching, so importing either one and calling monkey_patch() would
    reintroduce the exact crash this module exists to prevent -- in a form the
    confinement host could not fix, because there would no longer be separate
    OS threads to confine work to. Neither is installed, and this test is here
    so that stays a decision rather than an accident.
    """
    import socket

    assert threading.Thread.__module__ == "threading"
    assert socket.socket.__module__ in {"socket", "_socket"}
    assert "gevent" not in str(threading.current_thread().__class__)


# ------------------------------------------ the surfaces Mohsin actually uses


def test_the_gui_explains_a_failure_instead_of_printing_a_traceback():
    """The window printed raw exceptions, which is how Mohsin met
    "error: Cannot switch to a different thread" followed by two lines of
    greenlet object addresses. The CLI already knew how to explain that; the
    window was never given the same courtesy."""
    from app.cli import describe_turn_failure

    raw = ("error: Cannot switch to a different thread\n"
           "\tCurrent:  <greenlet.greenlet object at 0x197A1CD97C0 (otid=0x197AA0449D0)>\n"
           "\tExpected: <greenlet.greenlet object at 0x197A1E9F640 (otid=0x197B5539B80)>")
    shown = describe_turn_failure(RuntimeError(raw))
    assert "otid" not in shown
    assert "0x197" not in shown
    assert len(shown.splitlines()) <= 4


def test_explaining_a_failure_does_not_lose_it():
    """Plain language for Mohsin, full classification for /doctor."""
    from app.failures import FailureLog

    log = FailureLog()
    record = log.from_exception(
        RuntimeError("Cannot switch to a different thread"),
        stage="gui_turn", user_request="how does your explain_my_tools tool work?")

    assert record.failure_class is FailureClass.THREAD_CONFINEMENT
    assert record.stage == "gui_turn"  # not blamed on whatever else was running
    assert record.exception_message


def test_a_gui_turn_runs_off_the_tk_thread_and_reports_back_onto_it():
    """Tk is single-threaded: touching a widget from a worker corrupts it.
    Work goes off the loop, results come back through root.after."""
    import inspect

    from app.gui import HayatGUI

    work = inspect.getsource(HayatGUI._work)
    assert "self._post(" in work                 # every UI touch is marshalled
    assert "self.chat.insert" not in work        # and none is done directly

    send = inspect.getsource(HayatGUI.send)
    assert "threading.Thread(" in send and "daemon=True" in send


def test_ocr_no_longer_refuses_when_the_caller_owns_a_loop():
    """asyncio.run refuses outright inside a running loop -- and refuses after
    creating the coroutine, so it fails with a 'never awaited' warning
    attached. The same shape of assumption as the browser bug: code that
    believed it owned the thread it stood on."""
    import asyncio

    from app.perception.camera.ocr import WindowsMediaOcr

    async def value():
        return ("lines", "text")

    assert WindowsMediaOcr._run(value()) == ("lines", "text")

    async def from_inside_a_loop():
        return WindowsMediaOcr._run(value())

    assert asyncio.run(from_inside_a_loop()) == ("lines", "text")


def test_the_background_learner_is_a_daemon_that_can_be_asked_to_stop():
    """An unattended loop that cannot be stopped is how a shutdown hangs."""
    import inspect

    from app.learning.continuous import ContinuousLearner

    source = inspect.getsource(ContinuousLearner)
    assert "daemon=True" in source
    assert "def request_stop" in source and "def stop" in source


# ------------------------------------- waiting for more than one reason at once


def test_a_composite_event_is_set_when_any_of_its_parts_is():
    from app.voice.interruption import AnyEvent

    first, second = threading.Event(), threading.Event()
    either = AnyEvent(first, second)
    assert not either.is_set()
    second.set()
    assert either.is_set()
    assert either.wait(0.5) is True


def test_switching_to_hands_free_reaches_a_listener_that_is_already_waiting():
    """The bug that made hands-free look dead.

    set_mode() only assigned a field, and the listener was parked inside
    wait_for_press() polling the shutdown event alone. So the mode had changed
    and nothing was looking at it -- pressing Hands-free did nothing at all
    until Ctrl+Space was pressed, which is the key hands-free exists to avoid.
    """
    from app.voice.interruption import AnyEvent, WindowsHotkey

    hotkey = WindowsHotkey("ctrl+space")
    shutdown, mode_changed = threading.Event(), threading.Event()
    returned = threading.Event()

    def wait() -> None:
        hotkey.wait_for_press(AnyEvent(shutdown, mode_changed))
        returned.set()

    threading.Thread(target=wait, daemon=True).start()
    time.sleep(0.1)
    assert not returned.is_set(), "it should still be waiting"

    mode_changed.set()
    assert returned.wait(2.0), "a mode change did not end the wait"
    shutdown.set()


def test_the_voice_manager_signals_the_listener_when_the_mode_changes():
    import inspect

    from app.voice.manager import VoiceManager

    source = inspect.getsource(VoiceManager.set_mode)
    assert "mode_changed.set()" in source, "a mode change must wake the listener"

    loop = inspect.getsource(VoiceManager._push_to_talk_cycle)
    assert "AnyEvent" in loop, "the wait must end on a mode change too"
