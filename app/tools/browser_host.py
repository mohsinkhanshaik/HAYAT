"""One thread owns Playwright, and every caller borrows it.

`sync_playwright().start()` builds a greenlet-based driver that belongs to the
thread that called it. Touch any object it returned -- a page, a context, even
`page.is_closed()` -- from a different thread and it raises:

    greenlet.error: Cannot switch to a different thread

That is exactly what kept happening here, and it was not one bug. HAYAT runs
tools on the agent thread, narrates from the event thread, draws from the Tk
GUI thread, learns on a background thread, and takes state snapshots from
whichever thread is answering. Whichever of those touched the browser first
owned it, and every other one broke -- including, twice, an ordinary
conversational turn that had nothing to do with browsing and died mid-answer
with a greenlet traceback.

Guarding each call site was the wrong shape of fix: it turns a crash into a
gap, and the next call site added forgets. The problem is not that callers
are careless, it is that a thread-confined resource was being shared between
threads. So it is confined properly: one dedicated thread starts Playwright
and performs every operation on it, and all other threads hand work across
and wait for the result. Exceptions come back to the caller with their
traceback intact, so a real browser failure still looks like a browser
failure -- what disappears is the failure caused purely by which thread asked.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable


#: A browser action that has not returned within this long is not going to.
#: Playwright's own timeouts are 30s; this sits above them so a real timeout
#: surfaces as Playwright's clear message rather than as this one.
DEFAULT_TIMEOUT = 180.0


class HostStopped(RuntimeError):
    """Raised when work is submitted after the host has shut down."""


class _Work:
    __slots__ = ("fn", "args", "kwargs", "done", "value", "error")

    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class PlaywrightHost:
    """A single thread that owns every Playwright object in this process.

    Lazy: the thread starts on first use, so nothing pays for a browser that
    is never opened. Reentrant: work submitted from the owner thread runs
    inline instead of deadlocking on itself, which matters because the browser
    methods call each other.
    """

    def __init__(self, name: str = "playwright-owner") -> None:
        self._name = name
        self._queue: queue.Queue[_Work | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._stopped = False

    @property
    def owner_thread(self) -> threading.Thread | None:
        return self._thread

    @property
    def on_owner_thread(self) -> bool:
        return self._thread is not None and threading.current_thread() is self._thread

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._stopped:
                raise HostStopped("the browser host has been shut down")
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._serve, name=self._name, daemon=True)
            self._thread.start()

    def _serve(self) -> None:
        while True:
            work = self._queue.get()
            if work is None:
                return
            try:
                work.value = work.fn(*work.args, **work.kwargs)
            except BaseException as exc:  # noqa: BLE001 -- relayed to the caller
                work.error = exc
            finally:
                work.done.set()

    def run(self, fn: Callable[..., Any], *args: Any, timeout: float = DEFAULT_TIMEOUT,
            **kwargs: Any) -> Any:
        """Run `fn` on the owning thread and return its result here.

        An exception raised over there is re-raised here, unchanged. The
        caller should not be able to tell that the work happened elsewhere --
        that is the whole point.
        """
        if self.on_owner_thread:
            # Already home. Going through the queue would mean waiting for a
            # thread that is busy waiting for us.
            return fn(*args, **kwargs)

        self._ensure_thread()
        work = _Work(fn, args, kwargs)
        self._queue.put(work)
        if not work.done.wait(timeout):
            raise TimeoutError(
                f"the browser did not finish {getattr(fn, '__name__', 'an action')} "
                f"within {timeout:.0f}s"
            )
        if work.error is not None:
            raise work.error
        return work.value

    def confine(self, method: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap a callable so every call runs on the owning thread."""

        def confined(*args: Any, **kwargs: Any) -> Any:
            return self.run(method, *args, **kwargs)

        confined.__name__ = getattr(method, "__name__", "confined")
        confined.__doc__ = getattr(method, "__doc__", None)
        # Kept so the schema generator and anything else reading signatures
        # still sees the real method rather than this wrapper.
        confined.__wrapped__ = method  # type: ignore[attr-defined]
        return confined

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop the owning thread. Safe to call more than once."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        if thread is not None and thread.is_alive():
            self._queue.put(None)
            thread.join(timeout=timeout)


#: The process-wide owner. Playwright's driver is per-process, so the thread
#: that owns it has to be too -- a second host would recreate the exact bug
#: this module exists to remove.
HOST = PlaywrightHost()
