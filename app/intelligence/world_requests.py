"""Everyday world questions mapped to the intelligence tools.

"Which planes are flying over Dubai", "is it going to rain in Lahore tomorrow",
"any storms near Florida", "give me a world brief", "watch Istanbul for
earthquakes". Before this they were ordinary chat or a generic web search, so
the model answered live-world questions from memory. Each request becomes one
read of HAYAT's public feeds; questions *about* a hazard ("what causes
earthquakes", "how do hurricanes form") stay ordinary questions.
"""
from __future__ import annotations

import re
from typing import Any

# Hazard words -> merged-event category (app/intelligence/objects.py).
_HAZARDS = (
    (r"earthquakes?|quakes?|tremors?|seismic\s+activity", "earthquakes"),
    (r"wildfires?|forest\s+fires?|bush\s*fires?|fires", "wildfires"),
    (r"(?:severe\s+|bad\s+)?(?:thunder)?storms?|hurricanes?|typhoons?|cyclones?|tornado(?:e)?s?|severe\s+weather", "severe_weather"),
    (r"floods?|flooding", "floods"),
    (r"tsunamis?(?:\s+(?:warnings?|alerts?))?", "tsunami"),
    (r"volcan(?:o|oes|ic\s+eruptions?|ic\s+activity)|eruptions?", "volcanoes"),
    (r"conflicts?|fighting|clashes", "conflict"),
    (r"(?<!cyber\s)(?<!cyber)attacks?|explosions?|bombings?", "attack"),
    (r"emergenc(?:y|ies)|disasters?", "emergency"),
    (r"(?:disease\s+)?outbreaks?|epidemics?|pandemics?", "health"),
    (r"asteroids?|meteors?|fireballs?|near[\s-]earth\s+objects?|space\s+rocks?", "space"),
    (r"air\s+quality|air\s+pollution|haze|smog|pollution", "air_quality"),
)
_HAZARD = "|".join(f"(?:{words})" for words, _ in _HAZARDS)
# Feeds read by the brief rather than the merged events.
_FEEDS = (
    (r"cyber[\s-]*(?:attacks?|security\s+(?:alerts?|incidents?|news)|threats?|incidents?)|ransomware(?:\s+attacks?)?|"
     r"(?:major\s+)?(?:hacks|data\s+breaches)|(?:new\s+)?(?:exploited\s+)?vulnerabilit(?:y|ies)", "cyber"),
    (r"space\s+weather|solar\s+(?:storms?|flares?|activity)|geomagnetic\s+(?:storms?|activity)|aurora\s+(?:alerts?|forecast)",
     "space_weather"),
)
# Watch feeds per hazard; every key exists in app/intelligence/feeds.BY_KEY.
_WATCH_FEEDS = {
    "earthquakes": ["earthquakes", "earthquakes_emsc", "earthquakes_jma", "earthquakes_geonet", "earthquakes_bmkg"],
    "wildfires": ["wildfires_us", "natural_events"],
    "severe_weather": ["storms_nhc", "natural_events", "disasters", "aviation_sigmet", "warnings_hko"],
    "floods": ["disasters", "natural_events", "floods_uk"],
    "volcanoes": ["natural_events", "disasters", "volcanoes_usgs", "volcanoes_geonet"],
    "tsunami": ["tsunami_ntwc", "tsunami_ptwc", "earthquakes", "earthquakes_emsc", "disasters"],
    "health": ["outbreaks_who"],
    "space": ["asteroids_jpl", "fireballs_jpl"],
    "air_quality": ["air_quality_sg"],
    "cyber": ["cyber"],
    "space_weather": ["space_weather", "geomagnetic_kp", "solar_flares"],
}
# Feeds read before answering a hazard question, so "none" means none was
# reported, not that the feed was never fetched. Cooldowns still apply.
_NEWS_FEEDS = ["gdelt_security", "news_bbc_world", "news_aljazeera", "news_un"]
_REFRESH_FEEDS = {**_WATCH_FEEDS, "conflict": _NEWS_FEEDS, "attack": _NEWS_FEEDS, "emergency": [*_NEWS_FEEDS, "disasters"]}
_EVENT_FEEDS = ["earthquakes", "earthquakes_emsc", "natural_events", "disasters", "wildfires_us", "storms_nhc",
                "gdelt_security", "airports_faa", "tsunami_ntwc", "tsunami_ptwc", "volcanoes_usgs", "volcanoes_geonet",
                "outbreaks_who"]

_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
# A time phrase ("in the last 24 hours") is never a place.
_PLACE = r"(?P<place>(?!(?:the\s+)?(?:last|past|next|coming)\s)[^\s,?.!].{0,60}?)"
_WHEN = (r"(?:\s*,?\s*(?P<when>right\s+now|now|today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|week|weekend)|"
         r"at\s+the\s+moment|currently|lately|recently|so\s+far\s+today|"
         r"in\s+the\s+(?:last|past)\s+(?:\d{1,3}\s+|few\s+|couple\s+of\s+)?(?:hours?|days?|weeks?|month)|"
         r"over\s+the\s+(?:last|past)\s+(?:\d{1,3}\s+)?(?:hours?|days?|weeks?)))?")
_END = r"\s*[?.!]*$"
_NEAR = r"(?:near|in|around|over|above|at|close\s+to|off|off\s+the\s+coast\s+of)"
_WORLD = r"(?:anywhere(?:\s+in\s+the\s+world)?|worldwide|globally|in\s+the\s+world|around\s+the\s+world|on\s+earth)"
_NOT_PLACES = {"the world", "world", "anywhere", "everywhere", "general", "the area", "the past", "history", "the news",
               "the sky", "the air", "space", "my life", "life", "a movie", "a film", "the movie", "the film", "it"}


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


def _patterns(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(_compile(pattern) for pattern in patterns)


_HERE = {"me", "here", "my area", "my location", "my city", "where i am", "us", "this area", "my place"}


def _place(raw: str | None) -> str | None:
    """A cleaned place name, "here", or None when the words are not a place."""
    if not raw:
        return None
    value = re.sub(r"(?i)^(?:the\s+)?(?:city|state|country|region|province)\s+of\s+", "", raw.strip(" ,.?!"))
    value = re.sub(r"(?i)\s+(?:area|region)$", "", value) if len(value.split()) > 1 else value
    lowered = value.lower()
    if lowered in _HERE:
        return "here"
    if lowered in _NOT_PLACES or not value or len(value.split()) > 6:
        return None
    # A place is a name, not a clause ("how planes stay up", "if it rains").
    if re.search(r"(?i)\b(?:is|are|was|were|do|does|did|how|why|what|when|if|because|my|your|our|their|i|you|we|they)\b", value):
        return None
    return value


def _hours(when: str | None) -> float | None:
    if not when:
        return None
    text = when.lower()
    if text in {"right now", "now", "at the moment", "currently", "today", "tonight", "so far today"}:
        return 24.0
    if "this week" in text or text in {"lately", "recently"}:
        return 168.0
    if match := re.search(r"(\d{1,3})\s+(hour|day|week)", text):
        return float(match[1]) * {"hour": 1, "day": 24, "week": 168}[match[2]]
    if "few" in text or "couple" in text:
        return {"hour": 3.0, "day": 72.0, "week": 336.0}[re.search(r"(hour|day|week)", text)[1]]
    for unit, hours in (("hour", 1.0), ("day", 24.0), ("week", 168.0), ("month", 720.0)):
        if unit in text:
            return hours
    return None


def _category(words: str) -> str | None:
    for pattern, category in _HAZARDS:
        if re.fullmatch(pattern, words.strip(), re.IGNORECASE):
            return category
    return None


def _feed(words: str) -> str | None:
    for pattern, feed in _FEEDS:
        if re.fullmatch(pattern, words.strip(), re.IGNORECASE):
            return feed
    return None


def _wide(place: str) -> dict[str, Any]:
    """A country is searched more widely than a city's 300 km default."""
    from app.intelligence.gazetteer import locate
    found = locate(place)
    if found and found["precision"].startswith("country") and found["place"].lower() == place.lower():
        return {"radius_km": 1000}
    return {}


def _located(place: str) -> dict[str, Any] | None:
    """A named city or country (not an adjective) -- the guard for watch removal."""
    from app.intelligence.gazetteer import locate
    found = locate(place)
    if found and not found["precision"].startswith("country implied") and found["place"].lower() == place.lower():
        return found
    return None


def _hazard_request(value: str) -> dict[str, Any] | None:
    status = (r"(?:\s+(?:reported|happening|going\s+on|active|burning|raging|underway|hitting|alerts?|warnings?|"
              r"unrest|erupting|eruptions?|activity|(?:passing|approaching|coming)(?:\s+(?:by|close\s+to|near))?(?:\s+(?:the\s+)?earth)?|"
              r"near\s+(?:the\s+)?earth))?")
    quality = r"(?:(?:new|recent|current|active|major|big|large|strong|latest|ongoing|live|reported)\s+)*"
    head = (r"(?P<lead>(?:(?:are|is|were|was)\s+there|(?:have|has)\s+there\s+been|any(?:thing)?\s+(?:about|on)|any|"
            r"show(?:\s+me)?|list|find|get(?:\s+me)?|check(?:\s+for)?|give\s+me|"
            r"what(?:'s|\s+is)\s+the\s+latest\s+on|what\s+are\s+the\s+latest|(?:what|which))\s+)?(?:(?:any|a|an|the|all|all\s+the)\s+)?")
    pattern = _compile(_LEAD + head + quality + rf"(?P<what>{_HAZARD}|{'|'.join(p for p, _ in _FEEDS)})" + status
                         # "in the world" is tried before "in <place>", so "the world" is never taken for a place name.
                         + rf"(?:\s+(?:(?P<world>{_WORLD})|{_NEAR}\s+{_PLACE}))?" + _WHEN + _END)
    match = pattern.match(value)
    if not match:
        return None
    place = _place(match["place"]) if match["place"] else None
    if match["place"] and place is None:
        return None
    adjectives = re.search(r"(?i)\b(?:new|recent|current|active|latest|ongoing|live|reported)\b", value)
    # A bare noun ("earthquakes") is a topic, not a request for live data.
    if not (match["lead"] or place or match["world"] or match["when"] or adjectives):
        return None
    words = match["what"]
    if feed := _feed(words):
        arguments = {"feed": feed}
        if place and place != "here":
            arguments["query"] = place
        return {"tool": "intelligence_brief", "arguments": arguments, "refresh": _WATCH_FEEDS[feed]}
    category = _category(words)
    if not category:
        return None
    arguments: dict[str, Any] = {"category": category}
    if hours := _hours(match["when"]):
        arguments["since_hours"] = hours
    if place:
        arguments["place"] = place
        arguments.update(_wide(place))
    return {"tool": "intelligence_objects", "arguments": arguments, "refresh": _REFRESH_FEEDS[category]}


_WEATHER = (
    _compile(_LEAD + r"(?:what(?:'s|\s+is|\s+will)|whats|how(?:'s|\s+is|\s+will)|hows|tell\s+me|give\s+me|show\s+me|check)\s+"
               r"(?:the\s+)?(?:weather|forecast|weather\s+forecast|temperature)(?:\s+(?:like|be|be\s+like|going\s+to\s+be(?:\s+like)?))?"
               + _WHEN + rf"(?:\s+{_NEAR}\s+{_PLACE})?" + _WHEN.replace("when", "when2") + _END),
    _compile(_LEAD + r"(?:the\s+)?(?:current\s+)?(?:weather|weather\s+forecast|forecast|weather\s+report|temperature)\s+"
               rf"(?:in|at|for|near|around)\s+{_PLACE}" + _WHEN + _END),
    _compile(_LEAD + r"(?:is\s+it|will\s+it|is\s+it\s+going\s+to|it(?:'s|\s+is)\s+going\s+to|does\s+it\s+look\s+like\s+it(?:'ll|\s+will))\s+"
               r"(?:be\s+)?(?:rain(?:ing|y)?|snow(?:ing|y)?|storm(?:ing|y)?|sunny|cloudy|windy|hot|cold|warm|freezing|humid|foggy)"
               + _WHEN + rf"(?:\s+(?:in|at|near|around)\s+{_PLACE})?" + _WHEN.replace("when", "when2") + _END),
    _compile(_LEAD + r"how\s+(?:hot|cold|warm|windy|humid|rainy|wet|sunny|cloudy|chilly)\s+(?:is\s+it|is|will\s+it\s+be|will)\s+"
               r"(?:(?:in|at)\s+)?" + _PLACE + r"(?:\s+be)?" + _WHEN + _END),
    _compile(_LEAD + r"(?:do\s+i\s+need|should\s+i\s+(?:take|bring|carry))\s+an?\s+(?:umbrella|jacket|coat)"
               + _WHEN + rf"(?:\s+(?:in|at|for)\s+{_PLACE})?" + _WHEN.replace("when", "when2") + _END),
)


def _weather_request(value: str) -> dict[str, Any] | None:
    for pattern in _WEATHER:
        if match := pattern.match(value):
            raw = match["place"]
            if raw:
                # "how windy is it in Chicago" leaves "it in Chicago".
                raw = re.sub(r"(?i)^it\s+(?:in|at)\s+", "", raw)
            place = _place(raw) if raw else "here"
            if place is None:
                return None
            return {"tool": "intelligence_around", "arguments": {"place": place, "include": ["weather"]}}
    return None


_CRAFT = r"(?:planes|aircraft|airplanes|aeroplanes|flights|jets|helicopters|choppers|air\s+traffic|air\s+activity)"
_AIRCRAFT = (
    _compile(_LEAD + r"(?:(?:which|what|how\s+many|are\s+there\s+any|is\s+there\s+any|any)\s+)" + _CRAFT
               + r"(?:\s+(?:are|is))?(?:\s+(?:flying|in\s+the\s+air|overhead|passing|up|currently\s+flying))?\s+"
               rf"(?:over|above|near|around|in|by|close\s+to)\s+{_PLACE}" + _WHEN + _END),
    _compile(_LEAD + r"(?:show|list|find|track|get|check|give)(?:\s+me)?(?:\s+(?:the|all|all\s+the|any|live))?\s+"
               + _CRAFT + r"(?:\s+(?:flying|overhead))?\s+" rf"(?:over|above|near|around|in|at)\s+{_PLACE}" + _WHEN + _END),
    _compile(_LEAD + r"(?:what(?:'s|\s+is)\s+the\s+)?(?:live\s+|current\s+)?" + _CRAFT + r"(?:\s+like)?\s+"
               rf"(?:over|above|near|around|in|at)\s+{_PLACE}" + _WHEN + _END),
    _compile(_LEAD + r"what(?:'s|\s+is)\s+flying\s+(?:over|above|near|around)\s+" + _PLACE + _WHEN + _END),
)


def _aircraft_request(value: str) -> dict[str, Any] | None:
    for pattern in _AIRCRAFT:
        if match := pattern.match(value):
            place = _place(match["place"])
            return {"tool": "intelligence_aircraft", "arguments": {"mode": "near", "place": place}} if place else None
    return None


_ALERTS = _patterns(_LEAD + r"(?:(?:what\s+are|show(?:\s+me)?|list|read|check|open|any|are\s+there\s+any|do\s+i\s+have\s+any)\s+)?"
                     r"(?:(?:my|the|all|all\s+the|any)\s+)?(?:(?:latest|new|recent|current|unread|today'?s)\s+)?"
                     r"(?:intelligence|world|watch|hazard|global)\s+alerts" + _WHEN + _END,
                     _LEAD + r"(?:what\s+are|show(?:\s+me)?|list|any|are\s+there\s+any)\s+(?:(?:my|the)\s+)?"
                     r"(?:latest|new|recent|current)\s+alerts" + _WHEN + _END)
_BRIEF = _patterns(_LEAD + r"(?:(?:give|get|send|show|make|write)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+|my\s+|today'?s\s+)?)?"
                    r"(?:world|global|daily|morning|evening|intelligence|situation|world\s+news)\s+"
                    r"(?:brief|briefing|update|summary|sitrep|report)" + _WHEN + _END,
                    _LEAD + r"brief\s+me(?:\s+on\s+(?:the\s+world|world\s+events|what(?:'s|\s+is)\s+happening(?:\s+in\s+the\s+world)?))?"
                    + _WHEN + _END)
_ASSESS = _patterns(_LEAD + r"(?:what(?:'s|\s+is)|whats)\s+(?:happening|going\s+on)\s+(?:in|around|across)\s+the\s+world" + _WHEN + _END,
                     _LEAD + r"what(?:'s|\s+is)\s+the\s+(?:world|global)\s+situation" + _WHEN + _END)
_MAP = _compile(_LEAD + r"(?:show|open|display|bring\s+up)(?:\s+me)?(?:\s+(?:the|a))?\s+(?:"
                  r"(?:world|events?|live|intelligence|incident|hazard|disaster|crisis)\s+map"
                  r"|map\s+(?:of|with|showing|for)\s+(?:the\s+)?(?:(?:current|latest|live|world|all|today'?s)\s+)*"
                  r"(?:world\s+)?(?:events|incidents|situation|alerts|hazards|disasters|earthquakes|world)"
                  r"(?:\s+(?:in|around)\s+the\s+world)?)" + _END)
_CHANGES = _compile(_LEAD + r"what(?:'s|\s+has)?\s+(?:happened|been\s+happening|went\s+on)\s+(?:near|in|around|at)\s+"
                      + _PLACE + r"\s+(?P<when>(?:in|during|over)\s+the\s+(?:last|past)\s+(?:\d{1,3}\s+|few\s+|couple\s+of\s+)?"
                      r"(?:hours?|days?|weeks?|month)|today|this\s+week|lately|recently)" + _END)
_WATCH_TOPIC = rf"(?:{_HAZARD}|{'|'.join(p for p, _ in _FEEDS)}|events|alerts|incidents|anything|news|trouble)"
_WATCH = (
    _compile(_LEAD + r"(?:watch|monitor|keep\s+(?:an\s+)?eye\s+on|keep\s+watch\s+(?:on|over)|track)\s+" + _PLACE
               + rf"\s+for\s+(?:any\s+)?(?:new\s+)?(?P<topic>{_WATCH_TOPIC})"
               r"(?:\s*(?:,|and)?\s*(?:tell|alert|notify|warn|let)\s+me(?:\s+know)?(?:\s+(?:if|when)\s+.{1,40})?)?" + _END),
    _compile(_LEAD + r"(?:alert|notify|warn|tell)\s+me\s+(?:about|of|if\s+there\s+(?:is|are)|when\s+there\s+(?:is|are))\s+"
               rf"(?:any\s+|an?\s+)?(?:new\s+)?(?P<topic>{_WATCH_TOPIC})\s+(?:near|in|around|at)\s+{_PLACE}" + _END),
)
_UNWATCH = _compile(_LEAD + r"(?:(?:stop|quit)\s+(?:watching|monitoring|tracking)|(?:cancel|remove|delete|end)\s+(?:the\s+|my\s+)?"
                      r"(?:watch|monitoring)\s+(?:on|for|of))\s+" + _PLACE + _END)
_WATCH_LIST = _compile(_LEAD + r"(?:(?:list|show)(?:\s+me)?\s+(?:my|the|all|all\s+my)\s+(?:intelligence\s+|world\s+|place\s+)?"
                         r"(?:watch(?:es|\s+rules|\s+list)|watched\s+places)|what\s+(?:places|areas|regions)\s+(?:am\s+i|are\s+you)\s+"
                         r"(?:watching|monitoring))" + _END)


def _watch_key(place: str) -> str:
    return " ".join(place.lower().split())[:120]


def _watch_request(value: str) -> dict[str, Any] | None:
    if _WATCH_LIST.match(value):
        return {"tool": "intelligence_watch", "arguments": {"action": "list"}}
    if match := _UNWATCH.match(value):
        place = _place(match["place"])
        # "stop watching the video" is the player's job; only a known place is a watch.
        if place and place != "here" and _located(place):
            return {"tool": "intelligence_watch", "arguments": {"action": "delete", "key": _watch_key(place)}}
        return None
    for pattern in _WATCH:
        if not (match := pattern.match(value)):
            continue
        place = _place(match["place"])
        found = _located(place) if place and place != "here" else None
        if not found:
            return None
        topic = match["topic"]
        kind = _feed(topic) or _category(topic)
        feeds = _WATCH_FEEDS.get(kind or "", _EVENT_FEEDS)
        # About 200 km around the place's centre; a country gets more.
        span = 6.0 if found["precision"].startswith("country") else 2.0
        lat, lon = found["latitude"], found["longitude"]
        west, east = ((lon - span * 1.3 + 180) % 360) - 180, ((lon + span * 1.3 + 180) % 360) - 180
        title = f"{found['place']}: {' '.join(topic.lower().split())}"
        data = {"title": title, "feeds": feeds,
                "bbox": [round(west, 3), round(max(-90.0, lat - span), 3), round(east, 3), round(min(90.0, lat + span), 3)]}
        return {"tool": "intelligence_watch", "arguments": {"action": "save", "key": _watch_key(place), "data": data}}
    return None


# "What's happening around me?", "am I safe?", "any hazards near Dubai", "hazard radar": everything near a place at once.
_RADAR = _compile(
    _LEAD + r"(?:(?:show|open)\s+(?:me\s+)?)?(?:(?:the|my)\s+)?(?:(?:(?:hazard|danger|threat|risk)\s+)?radar|"
    r"(?:is\s+there\s+)?any(?:thing)?\s+(?:dangerous|hazards?|dangers?|threats?|risks?|emergenc(?:y|ies))|"
    r"(?:what(?:'s|\s+is)|whats)\s+(?:happening|going\s+on)|am\s+i\s+safe|is\s+it\s+safe|"
    r"(?:show|list|check)\s+(?:me\s+)?(?:the\s+)?(?:hazards?|dangers?|threats?|risks?))"
    r"(?:\s+(?:around|near|close\s+to|in|at|for)\s+(?P<place>(?!the\s+world)[^?.!]{1,60}?))?" + _WHEN + _END)


def _radar_request(value: str) -> dict[str, Any] | None:
    match = _RADAR.match(value)
    if not match:
        return None
    place = (match["place"] or "").strip()
    # "What's happening in the world" is the brief; "what's happening" with no place is not a radar question.
    if not place and not re.search(r"(?i)radar|am\s+i\s+safe|is\s+it\s+safe", value):
        return None
    arguments: dict[str, Any] = {}
    if place and place.lower() not in _HERE:
        arguments["place"] = place
    if hours := _hours(match["when"]):
        arguments["since_hours"] = hours
    return {"tool": "intelligence_radar", "arguments": arguments}


def world_request(text: str) -> dict[str, Any] | None:
    """{"tool", "arguments"[, "refresh"]} for an everyday live-world request, or None.

    "refresh" names the feeds to read first, when the answer depends on them.
    """
    value = " ".join(str(text or "").split())
    if not value or len(value) > 200:
        return None
    if _MAP.match(value):
        return {"tool": "intelligence_map", "arguments": {}}
    if radar := _radar_request(value):
        return radar
    if any(pattern.match(value) for pattern in _BRIEF):
        return {"tool": "intelligence_brief", "arguments": {}}
    if any(pattern.match(value) for pattern in _ASSESS):
        return {"tool": "intelligence_assess", "arguments": {}}
    if any(pattern.match(value) for pattern in _ALERTS):
        return {"tool": "intelligence_alerts", "arguments": {}}
    if found := _watch_request(value):
        return found
    if match := _CHANGES.match(value):
        place = _place(match["place"])
        if not place:
            return None
        hours = _hours(match["when"]) or 24.0
        if hours > 168:
            return {"tool": "intelligence_objects", "arguments": {"place": place, "since_hours": hours, **_wide(place)}}
        return {"tool": "intelligence_region_changes", "arguments": {"place": place, "hours": hours}}
    for request in (_aircraft_request, _weather_request, _hazard_request):
        if found := request(value):
            return found
    return None
