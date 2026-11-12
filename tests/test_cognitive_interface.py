"""The interface's readings come from the parts that own them: telemetry, recall explanations, the topic chip text."""
from __future__ import annotations

from types import SimpleNamespace

from app.cognitive_telemetry import explain_recall, lines, snapshot
from app.gui_topic_chip import MAX_TITLE, chip_text
from app.memory.database import MemoryDatabase
from app.topics import ConversationTopics


def test_the_chip_names_the_topic_and_stays_short():
    assert chip_text("") == "Active"
    assert chip_text("GDP growth forecast") == "◈ GDP growth forecast"
    long = chip_text("GDP growth forecast for India in 2026 and beyond")
    assert long.endswith("…") and len(long) == MAX_TITLE + 2


def test_a_reading_holds_only_what_the_parts_reported(tmp_path):
    topics = ConversationTopics(tmp_path / "topics.db")
    topics.decide("s", "Which GPU should I buy for running local LLMs?")
    job = SimpleNamespace(query="solid state batteries", status="done", sources=4, confidence=0.71, elapsed=lambda: 12.4)
    runtime = SimpleNamespace(
        model=SimpleNamespace(model="hayat:latest", name="ollama"), topics=topics,
        turns=SimpleNamespace(recent=lambda limit: [{"latency": {"total_ms": 900.0}}, {"latency": {"total_ms": 300.0}},
                                                    {"latency": {}}]),
        research_jobs=SimpleNamespace(running=lambda: [job], recent=lambda limit: [job]),
        failures=SimpleNamespace(recent=lambda limit: [{"timestamp": "2026-11-09T10:00:00+00:00", "stage": "tool",
                                                        "exception_type": "TimeoutError", "exception_message": "slow"}]),
        learner=SimpleNamespace(status=lambda: (_ for _ in ()).throw(RuntimeError("down"))))
    reading = snapshot(runtime)
    assert reading["model"] == {"name": "hayat:latest", "provider": "ollama"}
    assert reading["latency"] == {"turns": 2, "median_ms": 600, "last_ms": 900, "slowest_ms": 900}
    assert reading["research"]["running"] == [{"query": "solid state batteries", "seconds": 12}]
    assert reading["topics"]["current"] == "GPU should I buy for running local LLMs" and reading["topics"]["topics"] == 1
    assert reading["errors"][0]["what"] == "TimeoutError: slow"
    assert "learning" not in reading and "memory" not in reading  # a part that failed or is absent is left out
    shown = dict(lines(reading))
    assert shown["Active model"] == "hayat:latest via ollama"
    assert "median 600 ms" in shown["Reply time"] and "TimeoutError" in shown["Recent error"]


def test_recall_is_explained_by_the_rankings_own_score_parts(tmp_path):
    memory = MemoryDatabase(tmp_path / "m.db")
    memory.initialize()
    memory.store_memory("semantic", "Canberra is the capital city of Australia.", 0.7, "test")
    memory.store_memory("episodic", "Opened the settings window and changed the theme.", 0.4, "test")
    found = explain_recall(memory, "capital of Australia")
    assert found and found[0]["text"].startswith("Canberra")
    assert found[0]["score"] > 0 and found[0]["because"]
    assert any("shares the question's words" in reason for reason in found[0]["because"])


def test_the_new_parts_are_brain_neurons_with_their_own_status_and_events(tmp_path):
    from app import brain_components
    from app.events import EventBus
    from app.forecasts import ForecastLedger
    from app.memory.retention import RetentionPractice

    memory = MemoryDatabase(tmp_path / "m.db")
    memory.initialize()
    memory.store_memory("semantic", "Canberra is the capital city of Australia.", 0.7, "test")
    seen = []
    bus = EventBus()
    for kind in ("TOPIC_PLACED", "MEMORY_REVIEWED", "FORECAST_KEPT", "FORECAST_JUDGED", "TOPIC_CHANGED_BY_OWNER"):
        bus.subscribe(kind, seen.append)
    topics, retention = ConversationTopics(tmp_path / "t.db"), RetentionPractice(memory, tmp_path / "r.db")
    ledger = ForecastLedger(tmp_path / "f.db")
    for part in (topics, retention, ledger):
        part.events = bus
    first = topics.decide("s", "What is the GDP growth forecast for India in 2026?")
    second = topics.decide("s", "btw give me a quick pasta recipe for dinner")
    topics.same_topic(second.message_id)
    retention.review(5)
    kept = ledger.record("predict india's growth next year", "Growth is likely to stay near 6.5% next year, with "
                         "risks from oil prices and weaker exports.")
    ledger.judge(kept, "right")
    assert [(event["event_type"], event["source"]) for event in seen] == [
        ("TOPIC_PLACED", "topics"), ("TOPIC_PLACED", "topics"), ("TOPIC_CHANGED_BY_OWNER", "topics"),
        ("MEMORY_REVIEWED", "retention"), ("FORECAST_KEPT", "forecasts"), ("FORECAST_JUDGED", "forecasts")]
    # Ids, relations and counts only: no message text leaves on the bus.
    assert "GDP" not in str([event["payload"] for event in seen]) and "pasta" not in str(seen[1]["payload"])
    assert seen[0]["payload"]["topic_id"] == first.topic_id

    components = {item.component_id: item for item in brain_components.build(
        services=["topics", "retention", "forecasts", "scorecard", "development"])}
    assert {"svc:topics", "svc:retention", "svc:forecasts", "svc:scorecard", "svc:development"} <= set(components)
    runtime = SimpleNamespace(topics=topics, retention=retention, forecasts=ledger)
    assert "current topic: GDP growth forecast for India in 2026" in brain_components.check(components["svc:topics"], runtime)
    assert "hit rate: 1.0" in brain_components.check(components["svc:forecasts"], runtime)
    assert "retention: 1.0" in brain_components.check(components["svc:retention"], runtime)
