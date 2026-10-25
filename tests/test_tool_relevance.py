"""The planner's shortlist reaches the right tool when a request is worded naturally."""
from __future__ import annotations

import pytest

from app.agent.planner import Planner
from app.tools.relevance import ToolIndex, index_for, terms
from scripts.benchmark_tool_recall import CASES, HELD_OUT, evaluate


@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    built = build_runtime(use_model=False, start_background_services=False)
    yield built
    built.close()


def test_everyday_words_reach_tools_described_in_other_words():
    index = ToolIndex([
        {"name": "intelligence_aircraft", "description": "Live aircraft positions from public ADS-B feeds."},
        {"name": "intelligence_weather", "description": "Current weather and forecast for a place."},
        {"name": "write_file", "description": "Write text to a file in the project."},
    ])
    assert index.relevant("which planes are flying over Dubai")[0] == "intelligence_aircraft"
    assert index.relevant("is it going to rain in Lahore")[0] == "intelligence_weather"
    assert index.relevant("hello") == []
    assert "the" not in terms("show me the weather") and "weather" in terms("show me the weather")


def test_inflected_words_keep_their_concept_links():
    index = ToolIndex([
        {"name": "hardware_inventory", "description": "Read USB devices."},
        {"name": "hazard_report", "description": "Current earthquake alerts."},
        {"name": "weather_report", "description": "Read a weather forecast."},
    ])
    assert index.relevant("what is plugged in")[0] == 'hardware_inventory'
    assert index.relevant("recent quakes")[0] == 'hazard_report'
    assert index.relevant("storms tomorrow")[0] == 'weather_report'
    assert terms("where am I, what's this?") == []


def test_measured_shortlist_recall_does_not_regress(runtime):
    """Keyword rules alone: 47.6% (tuning) and 65% (held out) on 2026-10-10."""
    planner = Planner(runtime.registry)
    tuning, held_out = evaluate(planner), evaluate(planner, HELD_OUT)
    assert tuning["recall"] >= 0.9, tuning["misses"]
    assert held_out["recall"] >= 0.8, held_out["misses"]
    # More reach must not mean flooding the brain with tools.
    assert tuning["median_shortlist"] <= 30 and held_out["median_shortlist"] <= 30
    assert len(CASES) == 42 and len(HELD_OUT) == 20


def test_index_follows_the_live_registry(runtime):
    index = index_for(runtime.registry)
    assert index is index_for(runtime.registry)
    assert "intelligence_aircraft" in index.relevant("which planes are flying over Dubai")


def test_the_shortlist_never_cuts_between_equal_scores():
    index = ToolIndex([{"name": f"tool_{index}", "description": "weather forecast"} for index in range(12)])
    assert len(index.relevant("weather", limit=10)) == 12  # twelve equal scores: none left out by the cut
    index = ToolIndex([{"name": "weather_tool", "description": "weather weather"}, {"name": "other", "description": "weather"}])
    assert index.relevant("weather", limit=1) == ["weather_tool"]  # a real gap is still cut
