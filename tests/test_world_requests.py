"""Everyday live-world questions reach the intelligence tools; questions about a topic do not."""
import pytest
from jsonschema import validate

from app.intelligence.tools import TOOL_NAMES, direct_plan
from app.intelligence.world_requests import world_request
from app.intent.live_web import needs_live_web
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.session import SessionState

# Probed on 2026-10-10: before this module none of these reached an intelligence
# tool -- each was chat, a generic web search or (the watch) a video search.
WORLD = [
    ("which planes are flying over Dubai", "intelligence_aircraft", {"mode": "near", "place": "Dubai"}),
    ("air traffic over London", "intelligence_aircraft", {"place": "London"}),
    ("track flights near JFK", "intelligence_aircraft", {"place": "JFK"}),
    ("planes near me", "intelligence_aircraft", {"place": "here"}),
    ("what is flying over Paris right now", "intelligence_aircraft", {"place": "Paris"}),
    ("is it going to rain in Lahore tomorrow", "intelligence_around", {"place": "Lahore", "include": ["weather"]}),
    ("how windy is Chicago today", "intelligence_around", {"place": "Chicago", "include": ["weather"]}),
    ("what's the weather in Paris", "intelligence_around", {"place": "Paris", "include": ["weather"]}),
    ("weather forecast for Karachi", "intelligence_around", {"place": "Karachi"}),
    ("what is the weather like today", "intelligence_around", {"place": "here"}),
    ("will it snow in Toronto this weekend", "intelligence_around", {"place": "Toronto"}),
    ("how hot is it in Delhi", "intelligence_around", {"place": "Delhi"}),
    ("temperature in Riyadh", "intelligence_around", {"place": "Riyadh"}),
    ("do I need an umbrella in London tomorrow", "intelligence_around", {"place": "London"}),
    ("what happened near Karachi in the last week", "intelligence_region_changes", {"place": "Karachi", "hours": 168.0}),
    ("any wildfires in California", "intelligence_objects", {"category": "wildfires", "place": "California"}),
    ("any storms near Florida", "intelligence_objects", {"category": "severe_weather", "place": "Florida"}),
    ("show me floods in Pakistan", "intelligence_objects", {"category": "floods", "place": "Pakistan", "radius_km": 1000}),
    ("any earthquakes near Tokyo right now", "intelligence_objects", {"category": "earthquakes", "since_hours": 24.0}),
    ("is there a hurricane anywhere right now", "intelligence_objects", {"category": "severe_weather"}),
    ("are there any earthquakes today", "intelligence_objects", {"category": "earthquakes", "since_hours": 24.0}),
    ("latest earthquakes", "intelligence_objects", {"category": "earthquakes"}),
    ("has there been an earthquake in Turkey today", "intelligence_objects", {"place": "Turkey"}),
    ("any major earthquakes in the last 24 hours", "intelligence_objects", {"since_hours": 24.0}),
    ("any cyber attacks reported today", "intelligence_brief", {"feed": "cyber"}),
    ("space weather today", "intelligence_brief", {"feed": "space_weather"}),
    ("any solar flares today", "intelligence_brief", {"feed": "space_weather"}),
    ("give me a world brief", "intelligence_brief", {}),
    ("brief me", "intelligence_brief", {}),
    ("what are the latest alerts", "intelligence_alerts", {}),
    ("show me the map of current events", "intelligence_map", {}),
    ("show me the world map", "intelligence_map", {}),
    ("what is happening in the world today", "intelligence_assess", {}),
    ("watch Istanbul for earthquakes and tell me", "intelligence_watch", {"action": "save", "key": "istanbul"}),
    ("alert me about earthquakes near Istanbul", "intelligence_watch", {"action": "save", "key": "istanbul"}),
    ("monitor Japan for tsunamis", "intelligence_watch", {"action": "save", "key": "japan"}),
    ("stop watching Istanbul", "intelligence_watch", {"action": "delete", "key": "istanbul"}),
    ("list my watches", "intelligence_watch", {"action": "list"}),
]

# Questions about a subject, remarks, and other tools' requests.
NOT_WORLD = [
    "what causes earthquakes", "how do hurricanes form", "I love rainy weather", "write a poem about storms",
    "what is a flood plain", "watch the new Dune trailer", "explain how planes fly",
    "tell me about the history of London", "stop watching the video", "watch the movie for free",
    "the weather is nice today", "what is the history of earthquakes in Japan", "earthquakes",
    "how many planes are there in the world", "tell me about fires", "watch youtube for news", "what happened in 1066",
]


@pytest.mark.parametrize(("text", "tool", "expected"), WORLD)
def test_world_requests_choose_the_tool_and_its_scope(text, tool, expected):
    step = direct_plan(text, TOOL_NAMES)
    assert step and step["tool"] == tool
    assert {key: step["arguments"].get(key) for key in expected} == expected


@pytest.mark.parametrize(("text", "tool", "expected"), WORLD)
def test_world_requests_route_as_actions(text, tool, expected):
    intent = IntentRouter().route(text, SessionState())
    assert intent.intent == IntentKind.ACTION
    assert intent.entities.get("modality") == "public_intelligence"


@pytest.mark.parametrize("text", NOT_WORLD)
def test_questions_about_a_subject_are_not_world_requests(text):
    assert world_request(text) is None
    assert direct_plan(text, TOOL_NAMES) is None


def test_watch_rules_are_bounded_to_the_place_and_the_hazard():
    step = world_request("watch Istanbul for earthquakes and tell me")
    data = step["arguments"]["data"]
    assert data["feeds"] == ["earthquakes", "earthquakes_emsc", "earthquakes_jma", "earthquakes_geonet",
                                  "earthquakes_bmkg"]
    west, south, east, north = data["bbox"]
    assert west < 28.98 < east and south < 41.01 < north  # Istanbul
    # Any topic without its own feeds watches the event feeds, never the cameras.
    general = world_request("watch Tokyo for incidents")["arguments"]["data"]["feeds"]
    assert general and not any(feed.startswith("cameras") for feed in general)


def test_a_time_phrase_is_never_read_as_a_place():
    arguments = world_request("any major earthquakes in the last 24 hours")["arguments"]
    assert "place" not in arguments and arguments["since_hours"] == 24.0
    assert world_request("what happened near Karachi in the last month")["arguments"]["since_hours"] == 720.0


def test_opinions_about_the_weather_are_not_web_research():
    assert needs_live_web("I love rainy weather") is None
    assert needs_live_web("the weather is nice today") is None
    assert needs_live_web("is the weather in Paris nice today?") is not None


@pytest.fixture(scope="module")
def runtime():
    from app.runtime import build_runtime

    built = build_runtime(use_model=False, start_background_services=False)
    yield built
    built.close()


def test_every_routed_request_fits_the_registered_tool(runtime):
    from app.agent.planner import Planner

    planner = Planner(runtime.registry)
    for text, tool, _ in WORLD:
        steps = planner._deterministic_plan(text)
        assert steps and steps[-1].tool == tool, text
        for step in steps:
            validate(instance=step.arguments, schema=runtime.registry.get(step.tool).input_schema)


def test_a_chat_watch_is_saved_listed_and_removed(runtime):
    registry = runtime.registry
    saved = registry.execute("intelligence_watch", world_request("monitor Japan for tsunamis")["arguments"])
    assert saved.success and saved.output["watch"]["title"] == "Japan: tsunamis"
    listed = registry.execute("intelligence_watch", world_request("list my watches")["arguments"])
    assert "japan" in {watch["key"] for watch in listed.output["watches"]}
    removed = registry.execute("intelligence_watch", world_request("stop watching Japan")["arguments"])
    assert removed.success and removed.output["deleted"] is True


def test_hazard_answers_refresh_their_feeds_first(runtime):
    from app.agent.planner import Planner

    steps = Planner(runtime.registry)._deterministic_plan("any earthquakes near Tokyo right now")
    assert [step.tool for step in steps] == ["intelligence_refresh", "intelligence_objects"]
    assert steps[0].arguments == {"feeds": ["earthquakes", "earthquakes_emsc", "earthquakes_jma", "earthquakes_geonet",
                                  "earthquakes_bmkg"],
                                  "due_only": True}
    validate(instance=steps[0].arguments, schema=runtime.registry.get("intelligence_refresh").input_schema)
    # Weather and aircraft are read live by their own tools; nothing to refresh.
    assert len(Planner(runtime.registry)._deterministic_plan("what's the weather in Paris")) == 1


def test_a_failed_refresh_says_the_answer_may_miss_recent_events():
    from app.intelligence.tools import refresh_line, report

    failed = {"sources": [{"feed": "earthquakes", "status": "failed", "error": "ConnectError: offline"}], "failed": 1}
    line = refresh_line(failed)
    assert "could not be read" in line and "may miss recent events" in line
    fresh = refresh_line({"sources": [{"feed": "earthquakes", "status": "ok", "count": 61},
                                      {"feed": "earthquakes_emsc", "cached": True}], "failed": 0})
    assert fresh == "Checked public feeds first: earthquakes updated (61 items); earthquakes_emsc recently fetched."
    history = [{"tool": "intelligence_refresh", "arguments": {}, "result": {"success": True, "output": failed},
                "verification": {"passed": True}},
               {"tool": "intelligence_objects", "arguments": {"category": "earthquakes"},
                "result": {"success": True, "output": {"intelligence": True, "intelligence_objects": True, "total": 0,
                                                       "objects": [], "by_status": {}}},
                "verification": {"passed": True}}]
    text = report(history)
    assert text.startswith("Checked public feeds first") and "0 merged event reports" in text


def test_a_brief_is_not_said_to_hit_a_model_length_limit():
    from app.intelligence.tools import report

    output = {"intelligence": True, "headline": "3 source items match; 0 feeds stale or unavailable.",
              "analysis": "This brief lists observed evidence.", "truncated": True,
              "sources": [{"key": "cyber", "status": "ok"}, {"key": "cameras_london", "status": "ok"}]}
    text = report([{"tool": "intelligence_brief", "arguments": {"feed": "cyber"},
                    "result": {"success": True, "output": output}, "verification": {"passed": True}}])
    assert "length limit" not in text and "cameras_london" not in text and "cyber: ok" in text


def test_the_forecast_outlook_covers_today_and_tomorrow():
    from app.intelligence.live import daily_outlook

    def step(hour, temperature, rain):
        return {"time": f"2026-10-{10 + hour // 24:02d}T{hour % 24:02d}:00:00Z",
                "data": {"instant": {"details": {"air_temperature": temperature, "wind_speed": 3.0}},
                         "next_1_hours": {"details": {"precipitation_amount": rain}}}}

    series = [step(hour, 20 + hour % 5, 0.5 if 30 <= hour < 34 else 0.0) for hour in range(12, 60)]
    outlook = daily_outlook(series, 0.0)
    assert [day["day"] for day in outlook] == ["today", "tomorrow", "2026-10-12"]
    assert outlook[1]["rain_mm"] == 2.0 and outlook[1]["rain_hours"] == 4
    assert outlook[0]["rain_mm"] == 0 and outlook[0]["low_c"] == 20 and outlook[0]["high_c"] == 24
    # Lahore (74.3 E) is five hours ahead: 20:00 UTC is already the next local day.
    assert daily_outlook([step(20, 25, 0.0)], 74.3)[0]["date"] == "2026-10-11"


def test_a_weather_only_look_says_nothing_about_events():
    from app.intelligence.fusion import Fusion

    where = {"name": "Lahore", "latitude": 31.5, "longitude": 74.3}
    weather = {"temperature_c": 22.7, "wind_speed_ms": 1.5, "precipitation_next_hour_mm": 0,
               "outlook": [{"day": "tomorrow", "low_c": 21.0, "high_c": 29.7, "rain_mm": 0, "rain_hours": 0,
                            "max_wind_ms": 2.2}]}
    text = Fusion.interpret(where, 16, {"weather": weather}, [], [])
    assert "earthquakes" not in text
    assert "Tomorrow: 21.0–29.7°C, no rain forecast, wind up to 2.2 m/s." in text
    assert "No reported earthquakes" in Fusion.interpret(where, 16, {"events": []}, [], [])
