from __future__ import annotations

import threading

import pytest

from app.events import CRITICAL_EVENT_TYPES, EventBus


@pytest.fixture()
def bus(tmp_path):
    instance = EventBus(tmp_path / "events.db")
    yield instance
    instance.close()


def test_sequence_strictly_increases_under_concurrent_producers(bus):
    """Ordering has to be assigned at publish time, not at flush time, or the
    recorded order stops matching the order things actually happened in."""
    sequences: list[int] = []
    lock = threading.Lock()

    def produce() -> None:
        local = [bus.publish("TASK_PROGRESS", "producer", {}) for _ in range(200)]
        with lock:
            sequences.extend(local)

    threads = [threading.Thread(target=produce) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(sequences) == 1000
    assert len(set(sequences)) == 1000, "sequence numbers must be unique"
    assert sorted(sequences) == list(range(1, 1001)), "sequence must be gapless and increasing"


def test_correlation_ids_are_carried_through(bus):
    seen: list[dict] = []
    bus.subscribe("TURN_COMPLETED", seen.append)
    bus.publish("TURN_COMPLETED", "assistant", {"ok": True}, session_id="s1", turn_id="t1", task_id="k1", final=True)

    event = seen[0]
    assert (event["session_id"], event["turn_id"], event["task_id"]) == ("s1", "t1", "k1")
    assert event["final"] is True
    assert event["runtime_id"] == bus.runtime_id
    assert event["sequence"] == 1


def test_a_broken_subscriber_cannot_stop_the_others_or_the_publisher(bus):
    delivered: list[str] = []
    bus.subscribe("TASK_PROGRESS", lambda event: (_ for _ in ()).throw(RuntimeError("subscriber died")))
    bus.subscribe("TASK_PROGRESS", lambda event: delivered.append(event["event_type"]))

    assert bus.publish("TASK_PROGRESS", "test", {}) == 1
    assert delivered == ["TASK_PROGRESS"]


def test_unsubscribe_stops_delivery(bus):
    received: list[dict] = []
    unsubscribe = bus.subscribe("TASK_PROGRESS", received.append)
    bus.publish("TASK_PROGRESS", "test", {})
    unsubscribe()
    bus.publish("TASK_PROGRESS", "test", {})
    assert len(received) == 1


def test_critical_events_are_never_dropped_when_the_queue_saturates(tmp_path):
    """Telemetry may be shed under overload; a decision-bearing event may not.
    Losing TURN_COMPLETED or an approval would misrepresent what happened."""
    instance = EventBus(tmp_path / "events.db", queue_size=16)
    try:
        for _ in range(500):
            instance.publish("TASK_PROGRESS", "flood", {})
        for index in range(50):
            instance.publish("TURN_COMPLETED", "assistant", {"i": index}, final=True)
        instance.flush()

        persisted = instance.recent(limit=500, event_type="TURN_COMPLETED")
        assert len(persisted) == 50, "critical events must survive a saturated queue"
        assert all(name in CRITICAL_EVENT_TYPES for name in {"TURN_COMPLETED"})
    finally:
        instance.close()


def test_publish_stays_fast_under_load(tmp_path):
    """Regression guard on the measured fix. The synchronous per-publish
    SQLite write measured 230 events/sec with a worst case of 4.24 seconds,
    which would stall a camera or voice thread. Persistence now happens on a
    background writer, so publishing must stay far below that."""
    import time

    instance = EventBus(tmp_path / "events.db")
    try:
        started = time.perf_counter()
        for _ in range(2000):
            instance.publish("TASK_PROGRESS", "bench", {})
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"2000 publishes took {elapsed:.2f}s; persistence has moved back onto the publisher"
        instance.flush()
    finally:
        instance.close()


def test_events_are_persisted_with_their_correlation(tmp_path):
    instance = EventBus(tmp_path / "events.db")
    try:
        instance.publish("FAILURE_RECORDED", "supervisor", {"why": "provider down"}, session_id="s9", turn_id="t9")
        assert instance.flush() is True
        rows = instance.recent(limit=10, event_type="FAILURE_RECORDED")
        assert rows and rows[0]["session_id"] == "s9" and rows[0]["turn_id"] == "t9"
    finally:
        instance.close()


def test_unknown_event_types_are_still_rejected(bus):
    with pytest.raises(ValueError):
        bus.publish("NOT_A_REAL_EVENT", "test", {})
