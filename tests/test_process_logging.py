"""The process log exists, keeps secrets out, and swallowed errors leave a trace."""
import logging

import pytest

import app.logging as hayat_logging
from app.logging import configure_process_logging, log_swallowed


@pytest.fixture
def process_log(tmp_path):
    root = logging.getLogger()
    level = root.level
    path = configure_process_logging(tmp_path, level="DEBUG")
    try:
        yield path
    finally:
        handler = hayat_logging._process_handler
        root.removeHandler(handler)
        handler.close()
        hayat_logging._process_handler = None
        root.setLevel(level)
        logging.captureWarnings(False)


def _read(path):
    for handler in logging.getLogger().handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_the_process_log_is_written_and_secrets_are_redacted(process_log):
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    logging.getLogger("app.example").warning("calling with token=%s", secret)
    try:
        raise RuntimeError(f"bad key {secret}")
    except RuntimeError:
        logging.getLogger("app.example").exception("request failed")
    text = _read(process_log)
    assert process_log.name == "hayat.log"
    assert "calling with token=" in text and "request failed" in text
    assert "RuntimeError" in text                  # the traceback is kept
    assert secret not in text                      # in the message and in the traceback


def test_chatty_library_info_stays_out_but_its_warnings_do_not(process_log):
    logging.getLogger("httpx").info("HTTP Request: POST http://localhost:11434/api/chat")
    logging.getLogger("httpx").warning("connection pool is full")
    text = _read(process_log)
    assert "HTTP Request" not in text
    assert "connection pool is full" in text


def test_background_roles_get_their_own_file(tmp_path, process_log):
    assert configure_process_logging(tmp_path, role="scheduler").name == "hayat-scheduler.log"


def test_configuring_twice_keeps_one_handler(tmp_path, process_log):
    configure_process_logging(tmp_path, level="DEBUG")
    ours = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)
            and "hayat" in getattr(h, "baseFilename", "")]
    assert len(ours) == 1


def test_a_swallowed_error_is_logged_once_per_interval(process_log, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(hayat_logging.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hayat_logging, "_swallowed_at", {})
    log = logging.getLogger("app.example.swallow")
    for _ in range(5):
        try:
            raise OSError("disk unavailable")
        except OSError:
            log_swallowed(log, "writing the journal", level=logging.WARNING)
    clock[0] += 61
    try:
        raise OSError("disk unavailable")
    except OSError:
        log_swallowed(log, "writing the journal", level=logging.WARNING)
    text = _read(process_log)
    assert text.count("writing the journal failed; carried on") == 2
    assert "OSError: disk unavailable" in text


def test_a_swallowed_error_below_the_log_level_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(hayat_logging, "_swallowed_at", {})
    log = logging.getLogger("app.example.quiet")
    log.setLevel(logging.WARNING)
    try:
        try:
            raise ValueError("expected")
        except ValueError:
            log_swallowed(log, "an expected step")
        assert hayat_logging._swallowed_at == {}   # not even throttled: never recorded
    finally:
        log.setLevel(logging.NOTSET)
