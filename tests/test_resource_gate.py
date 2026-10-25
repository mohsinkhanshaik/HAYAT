"""Heavy work in sequence, and refused outright when there is no room.

Mohsin found the machine at 94% memory and asked for jobs to run one after
another rather than together. Measuring showed HAYAT was not the cause -- LM
Studio held 18.8GB against HAYAT's 536MB -- but the failure being avoided is
real: an image model and a language model loading into the same two free
gigabytes do not fail politely.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.resources import (
    CRITICAL_FREE_BYTES,
    NotEnoughMemory,
    ResourceGate,
)

GB = 1024**3


def _gate(free_gb: float, critical: int = CRITICAL_FREE_BYTES) -> ResourceGate:
    gate = ResourceGate(critical_free=critical)
    gate.free_bytes = lambda: int(free_gb * GB)
    return gate


# ------------------------------------------------------------- one at a time


def test_two_heavy_jobs_do_not_run_together():
    """The whole point: sequence, not concurrency."""
    gate = _gate(free_gb=30)
    overlapping = []
    inside = threading.Event()
    release = threading.Event()

    def first():
        with gate.heavy("first", needs_bytes=GB):
            inside.set()
            release.wait(timeout=5)

    def second():
        with gate.heavy("second", needs_bytes=GB, wait=5):
            overlapping.append(gate.busy)

    thread = threading.Thread(target=first, daemon=True)
    thread.start()
    assert inside.wait(timeout=5)

    waiter = threading.Thread(target=second, daemon=True)
    waiter.start()
    time.sleep(0.3)
    assert not overlapping, "the second job started while the first was running"

    release.set()
    thread.join(timeout=5)
    waiter.join(timeout=5)
    assert overlapping, "the second job never ran"


def test_the_lane_is_released_even_when_a_job_fails():
    gate = _gate(free_gb=30)
    with pytest.raises(ValueError):
        with gate.heavy("explodes", needs_bytes=GB):
            raise ValueError("boom")
    assert not gate.busy
    with gate.heavy("after", needs_bytes=GB):
        pass  # must not deadlock behind the failed job


def test_waiting_for_the_lane_is_bounded():
    """A stuck job must not silently freeze everything behind it."""
    gate = _gate(free_gb=30)
    inside = threading.Event()
    release = threading.Event()

    def hold():
        with gate.heavy("holder", needs_bytes=GB):
            inside.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert inside.wait(timeout=5)
    try:
        with pytest.raises(NotEnoughMemory, match="gave up"):
            with gate.heavy("latecomer", needs_bytes=GB, wait=0.5):
                pass
    finally:
        release.set()
        thread.join(timeout=5)


# ------------------------------------------------------- refusing, not crashing


def test_a_job_larger_than_free_memory_is_refused_before_it_starts():
    """Saying "there is 2GB free and this needs 6" beats an allocation that
    fails halfway through."""
    gate = _gate(free_gb=2.4)
    with pytest.raises(NotEnoughMemory, match="Not enough memory"):
        with gate.heavy("huge model", needs_bytes=int(6 * GB), wait=0.5):
            pass


def test_the_refusal_says_what_is_free_and_what_was_needed():
    gate = _gate(free_gb=2.4)
    try:
        with gate.heavy("huge", needs_bytes=int(6 * GB), wait=0.5):
            pass
    except NotEnoughMemory as exc:
        message = str(exc)
    assert "2.4GB free" in message
    assert "6.0GB" in message
    assert "Close something" in message


def test_headroom_is_kept_rather_than_using_the_last_byte():
    """Windows pages hard well before it reports failure, so the useful limit
    sits above zero by a wide margin."""
    gate = _gate(free_gb=3.0)
    with pytest.raises(NotEnoughMemory):
        with gate.heavy("just under", needs_bytes=int(2.5 * GB), wait=0.5):
            pass


def test_a_job_that_fits_runs_immediately():
    gate = _gate(free_gb=30)
    started = time.monotonic()
    with gate.heavy("small", needs_bytes=GB) as reservation:
        assert reservation.label == "small"
    assert time.monotonic() - started < 1.0


def test_unknown_memory_is_not_treated_as_permission():
    """Refusing on a machine we cannot measure beats allocating into the dark."""
    gate = ResourceGate()
    gate.free_bytes = lambda: None
    with pytest.raises(NotEnoughMemory):
        with gate.heavy("something big", needs_bytes=GB, wait=0.5):
            pass


def test_it_never_tries_to_free_someone_elses_memory():
    """The memory belongs to other programs Mohsin is using. Unloading them to
    make room is help nobody asks for twice."""
    import inspect

    from app import resources

    source = inspect.getsource(resources)
    for forbidden in ("taskkill", "Stop-Process", "terminate(", "kill(", "keep_alive"):
        assert forbidden not in source, f"the gate must not reclaim memory ({forbidden})"


# ----------------------------------------------------------------- reporting


def test_pressure_reports_in_terms_a_person_can_act_on():
    """2.0GB is tight but not yet critical; 1.0GB is where HAYAT stops
    starting large things."""
    tight = _gate(free_gb=2.0).pressure()
    assert tight["critical"] is False
    assert tight["tight"] is True
    assert tight["free_gb"] == 2.0
    assert isinstance(tight["used_percent"], int)

    assert _gate(free_gb=1.0).pressure()["critical"] is True


def test_a_comfortable_machine_is_not_reported_as_tight():
    assert _gate(free_gb=20).pressure()["tight"] is False


def test_the_report_says_when_it_will_hold_off():
    assert "hold off" in _gate(free_gb=1.0).report()


def test_refusals_are_counted_so_the_pattern_is_visible():
    gate = _gate(free_gb=1.0)
    for _ in range(2):
        with pytest.raises(NotEnoughMemory):
            with gate.heavy("big", needs_bytes=int(8 * GB), wait=0.2):
                pass
    assert gate.status()["refused"] == 2


# --------------------------------------------------------------- integration


def test_image_generation_refuses_rather_than_crashing_when_memory_is_gone(tmp_path, monkeypatch):
    from app.models.diffusion import ImageGenerator
    from app import resources

    engine = tmp_path / "sd-cli.exe"; engine.write_bytes(b"x")
    model = tmp_path / "m.safetensors"; model.write_bytes(b"x" * 2048)
    monkeypatch.setattr(resources, "GATE", _gate(free_gb=0.2))
    # Exercise the real admission/refusal path without a two-minute wall-clock
    # wait. The queue deadline itself has dedicated ResourceGate tests above.
    monkeypatch.setattr(resources, "heavy", lambda label, needs_bytes=0:
                        resources.GATE.heavy(label, needs_bytes, wait=0))

    generator = ImageGenerator(engine_path=engine, model_path=model, output_root=tmp_path)
    result = generator.generate("a cat", steps=4, width=256, height=256)

    assert not result.ok
    assert "memory" in result.detail.lower()


def test_the_real_machine_reports_its_pressure():
    from app.resources import GATE

    pressure = GATE.pressure()
    assert pressure["total_gb"] and pressure["free_gb"] is not None
    assert 0 <= pressure["used_percent"] <= 100
