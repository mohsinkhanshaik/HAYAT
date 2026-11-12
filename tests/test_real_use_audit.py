"""Fixes from the owner's own event log (2026-11-09 audit of the last 14 days: 24 of 762 tool calls failed).

* A call that failed for good (ValueError: "I couldn't find a place called 'Global Technology Sector'") was run
  three times running, then re-planning could hand back the very same call.
* Caltrans camera stills timed out after 25 s, and the same camera was asked again and again, 25 s each time.
"""
from __future__ import annotations

import hashlib
import time

import pytest

from app.agent.core import Agent, DETERMINISTIC_ERRORS
from app.agent.executor import Executor
from app.agent.planner import PlanStep
from app.agent.verifier import Verifier
from app.memory.database import MemoryDatabase
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry
from tests.test_intelligence_resources import expanded  # noqa: F401 -- fixture
from tests.test_parallel_steps import Planner

SCHEMA = {"type": "object", "properties": {"place": {"type": "string"}}, "additionalProperties": False}


def test_a_call_that_fails_for_good_is_not_repeated(tmp_path):
    calls = []

    def deep_look(place=""):
        calls.append(place)
        raise ValueError(f"I couldn't find a place called {place!r}.")

    def search(place=""):
        calls.append("search:" + place)
        return {"results": ["deep tech trends"]}

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    registry.register("deep_look", "A place.", SCHEMA, "low", "public_web_read", deep_look)
    registry.register("search", "Search.", SCHEMA, "low", "web_search", search)
    failing = PlanStep("deep look", "deep_look", {"place": "Global Technology Sector"}, {"type": "tool_success"}, 2)
    # Re-planning first offers the same failing call again, then a real alternative.
    recovery = [PlanStep("deep look again", "deep_look", {"place": "Global Technology Sector"}, {"type": "tool_success"}, 2),
                PlanStep("research it", "search", {"place": "deep tech"}, {"type": "tool_success"}, 0)]
    agent = Agent(Planner([failing], recovery), Executor(registry), Verifier(), memory, max_steps=10)
    outcome = agent.run("look deep in tech")
    assert calls == ["Global Technology Sector", "search:deep tech"]  # once, not three times; never the repeat
    assert outcome.complete
    assert "ValueError" in DETERMINISTIC_ERRORS and "TimeoutError" not in DETERMINISTIC_ERRORS


def test_a_network_hiccup_is_still_retried(tmp_path):
    attempts = []

    def flaky(place=""):
        attempts.append(place)
        if len(attempts) == 1:
            raise ConnectionError("busy")
        return {"ok": True}

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    registry = ToolRegistry(PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    registry.register("lookup", "Reads.", SCHEMA, "low", "public_api_get", flaky)
    agent = Agent(Planner([PlanStep("look", "lookup", {"place": "x"}, {"type": "tool_success"}, 1)]), Executor(registry),
                  Verifier(), memory, max_steps=10)
    assert agent.run("look x").complete and len(attempts) == 2


def test_the_deep_look_says_it_is_for_places_not_subjects():
    from app.intelligence.fusion import Fusion

    fusion = Fusion.__new__(Fusion)
    fusion.live = type("Live", (), {"geocode": staticmethod(lambda place: None)})()
    fusion.last_place, fusion.my_location = None, None
    with pytest.raises(ValueError, match="research it instead"):
        fusion.resolve("Global Technology Sector")


def _age(manager, event_id, seconds):
    from datetime import datetime, timedelta, UTC
    key = "camera_preview_" + hashlib.sha256(event_id.encode()).hexdigest()[:32]
    record = manager.store.preference(key)
    record["fetched_at"] = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    manager.store.save_preference(key, record)


def test_a_camera_that_times_out_shows_its_last_picture_marked_old(expanded, monkeypatch):  # noqa: F811
    london = "cameras_london:JamCams_test"
    first = expanded.camera_frame(london)
    assert not first.get("stale")
    _age(expanded, london, 7200)  # the cached still has expired: a new one is due
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args[0])
        raise TimeoutError("timed out")
    monkeypatch.setattr(expanded.fetcher, "fetch", timeout)
    old = expanded.camera_frame(london)
    assert old["stale"] and "An older picture, fetched 2 h ago" in old["basis"] and "not the current view" in old["basis"]
    assert old["path"] == first["path"] and len(calls) == 1
    # Asked again at once: no second 25 s wait.
    again = expanded.camera_frame(london)
    assert again["stale"] and len(calls) == 1
    # A health check still sees the failure itself.
    with pytest.raises(TimeoutError):
        expanded.camera_frame(london, fresh=True)
    # A camera that answered before counts as failing for the next six hours, as before.
    assert expanded.store.camera_health([london])[london]["verdict"] == "error"
    assert expanded._still_works(london) is False  # an old picture is not a working camera


def test_a_camera_with_no_picture_yet_says_when_to_try_again(expanded, monkeypatch):  # noqa: F811
    london = "cameras_london:JamCams_test"
    calls = []

    def timeout(*args, **kwargs):
        calls.append(args[0])
        raise TimeoutError("timed out")
    monkeypatch.setattr(expanded.fetcher, "fetch", timeout)
    with pytest.raises(TimeoutError):
        expanded.camera_frame(london)
    started = time.perf_counter()
    with pytest.raises(ValueError, match="did not answer .* Try it again in about"):
        expanded.camera_frame(london)
    assert len(calls) == 1 and time.perf_counter() - started < 1


def test_a_resource_alert_fires_once_per_episode_not_every_reading():
    from types import SimpleNamespace

    from app.observability.manager import ObservabilityManager

    published = []
    bus = SimpleNamespace(publish=lambda *args: published.append(args))
    manager = ObservabilityManager(None, SimpleNamespace(reserve=10, maximum=0), bus)
    reading = lambda ram: {"ram": {"used_percent": ram}, "disk": {"free_bytes": 10 ** 12}}
    fired = [manager._maybe_alert(reading(ram), now=second) for second, ram in
             ((0, 90), (5, 91), (10, 92), (15, 91), (20, 80), (25, 90), (700, 93))]
    # Crossing at 0 s, again after it dropped at 20 s and rose at 25 s; the long stay repeats after 10 minutes.
    assert fired == [True, False, False, False, False, True, True] and len(published) == 3


def test_tor_is_named_when_a_camera_times_out_through_it(expanded, monkeypatch):  # noqa: F811
    from app.privacy_masks import ROUTE

    monkeypatch.setattr(ROUTE, "snapshot", lambda: ("tor", None))
    assert "privacy route (Tor) is on" in expanded._route_hint()
