from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.safety.sensitive import SECRET_KEY_NAMES, redact as redact_structure
from app.networking_privacy import contains_private_tools, redact_private_payload


SECRET_KEYS = SECRET_KEY_NAMES


def redact(value: Any) -> Any:
    """Key-aware and value-aware redaction.

    Previously this matched only dictionary *keys*, so a credential appearing in
    stdout, in a goal string, or inside a file the agent read was written to
    `data/logs` verbatim. It now inspects string values as well.
    """
    return redact_structure(value)


class TaskLogger:
    def __init__(self, task_id: str) -> None:
        directory = PROJECT_ROOT / load_settings()["log_directory"]
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{task_id}.jsonl"
        self._private_networking = False

    def log(self, event: str, **details: Any) -> None:
        self._private_networking = self._private_networking or contains_private_tools(details)
        if self._private_networking:
            details = redact_private_payload(details)
        record = {"timestamp": datetime.now(UTC).isoformat(), "event": event, **redact(details)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")


PROCESS_LOG_NAME = "hayat.log"
# Loggers that narrate every request at INFO. Their warnings still reach the file.
_CHATTY = ("httpx", "httpcore", "urllib3", "PIL", "matplotlib", "asyncio", "watchdog")
_process_handler: logging.Handler | None = None


class _RedactingFilter(logging.Filter):
    """Secrets never reach the process log, in the message or in a traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING and record.name.split(".", 1)[0] in _CHATTY:
            return False
        record.msg, record.args = redact(record.getMessage()), None
        # Another handler (a console) may already have formatted the traceback
        # into exc_text, so redact whatever text is there, not only a fresh one.
        if record.exc_info or record.exc_text:
            record.exc_text = redact(record.exc_text or logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def configure_process_logging(directory: Path | None = None, level: str | None = None,
                              role: str = "") -> Path:
    """Send this process's Python logging to data/logs/hayat.log.

    Until 2026-09-30 nothing configured logging, so the many ``except``
    blocks that log instead of crashing wrote to nowhere -- the windowed app
    has no console. The file rotates at 2 MB with three old copies kept,
    passes through the same redaction as task logs, and is safe to call
    more than once. HAYAT_LOG_LEVEL (default INFO) sets the detail; DEBUG
    also records the expected, best-effort failures. A ``role`` (the
    scheduler daemon, the web server) gets its own file: those run beside
    the window, and Windows cannot rotate a log another process holds open.
    """
    global _process_handler
    directory = Path(directory) if directory else PROJECT_ROOT / load_settings()["log_directory"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (f"hayat-{role}.log" if role else PROCESS_LOG_NAME)
    root = logging.getLogger()
    if _process_handler is not None:
        root.removeHandler(_process_handler)
        _process_handler.close()
    chosen = getattr(logging, str(level or os.environ.get("HAYAT_LOG_LEVEL") or "INFO").upper(), logging.INFO)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8", delay=True)
    handler.setLevel(chosen)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(_RedactingFilter())
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > chosen:
        root.setLevel(chosen)
    logging.captureWarnings(True)
    _process_handler = handler
    return path


_swallowed_at: dict[tuple[str, str], float] = {}
_swallowed_lock = threading.Lock()


def log_swallowed(logger: logging.Logger, what: str, *, level: int = logging.DEBUG,
                  every: float = 60.0) -> None:
    """Record an exception the caller handles by carrying on. Call it inside ``except``.

    These used to be ``except Exception: pass`` -- right for a best-effort step,
    but it left no trace when the step kept failing. The traceback goes to the
    process log; the same ``what`` on the same logger is written at most once
    per ``every`` seconds, so a failure on a per-event path cannot flood it.
    DEBUG is for failures that are expected and harmless (a widget already
    destroyed); WARNING is for work that was actually lost.
    """
    if not logger.isEnabledFor(level):
        return
    key, now = (logger.name, what), time.monotonic()
    with _swallowed_lock:
        if now - _swallowed_at.get(key, float("-inf")) < every:
            return
        _swallowed_at[key] = now
    error = sys.exc_info()[1]
    if _network_trouble(error):
        # A timeout or a dropped connection says all it has to say in one line; the traceback is noise.
        logger.log(level, "%s failed; carried on (%s: %s)", what, type(error).__name__, str(error)[:200])
        return
    logger.log(level, "%s failed; carried on", what, exc_info=True)


def _network_trouble(error: BaseException | None) -> bool:
    if error is None:
        return False
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a core dependency
        return False
    return isinstance(error, httpx.TransportError)
