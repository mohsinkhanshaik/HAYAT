"""Which of HAYAT's tools fit a request, judged from what every tool says it does.

The planner shows the brain a shortlist, not all ~400 tools, and builds that
shortlist from keyword rules: "camera" adds the camera tools, "invoice" the
business tools. A request worded any other way -- "which planes are flying
over Dubai", "is it going to rain in Lahore", "clock me in" -- never put the
right tool in front of the brain, so the brain could not choose it however
well it reasoned. Measured on 42 everyday phrasings, the right tool reached
the shortlist 48% of the time.

This index reads every registered tool: its name, its own description, and
the capability area it belongs to (that area's summary, examples and the
words people use for it). A request is scored against all of them with
inverse-document-frequency weighting, so rare, specific words ("aircraft",
"unbilled") count for more than common ones ("show", "make"). A small table of
general concept links bridges everyday words to the vocabulary tools are
described in (planes -> aircraft, rain -> weather, gigs -> freelance jobs).
Those links are about language, not about particular tools.

It only adds candidates. Authority, risk and the planner's own rules decide
what may run; a high score never grants anything.
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Any, Iterable

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a am an and any are as at be been but by can could did do does doing for from get give got had has have how "
    "i i'm im if in into is it its just let lets me my now of on or our please show so some tell than that the "
    "their them then there these they this those to up us was we what when where which who why will with would "
    "you your yet still already also really very want need like make made one ones all about over out just "
    "hayat thing things stuff right going s".split())

#: Everyday words -> the words tools are described with. Each group is one concept.
CONCEPTS: tuple[tuple[str, ...], ...] = (
    ("plane", "planes", "aircraft", "flight", "flights", "flying", "airplane", "jet", "airport"),
    ("rain", "weather", "forecast", "temperature", "storm", "snow", "wind", "humid", "sunny"),
    ("quake", "quakes", "earthquake", "seismic", "tremor", "disaster", "hazard", "alert", "event"),
    ("gig", "gigs", "freelance", "job", "jobs", "contract", "client", "remote", "work"),
    ("slide", "slides", "deck", "presentation", "powerpoint", "pptx"),
    ("word", "docx", "document"),
    ("spreadsheet", "excel", "xlsx", "sheet", "table"),
    ("customer", "customers", "client", "clients", "buyer", "buyers"),
    ("money", "profit", "profitable", "profitability", "earn", "earnings", "income", "revenue", "margin"),
    ("owe", "owes", "unpaid", "overdue", "outstanding", "invoice", "invoices", "bill", "bills"),
    ("remind", "reminder", "nudge", "chase", "follow"),
    ("spent", "spend", "spending", "cost", "costs", "expense", "expenses", "purchase", "receipt"),
    ("clock", "timer", "time", "hours", "tracking", "track", "billable", "unbilled", "charged"),
    ("copy", "copied", "clipboard", "paste", "ctrl"),
    ("highlight", "highlights", "clip", "clips", "short", "shorts", "reel", "reels", "recording", "video"),
    ("draw", "drawing", "picture", "pictures", "image", "images", "photo", "photos", "illustration", "generate"),
    ("webcam", "camera", "cam", "see", "look"),
    ("monitor", "screen", "display", "second"),
    ("install", "setup", "download", "software", "program", "app", "application"),
    ("library", "package", "module", "pip", "dependency"),
    ("program", "app", "application", "software", "tool"),
    ("cheapest", "cheap", "price", "prices", "pricing", "compare", "comparison", "deal", "deals"),
    ("newsletter", "email", "emails", "mail", "subscribers", "weekly"),
    ("mail", "email", "send", "message"),
    ("whatsapp", "message", "text", "chat"),
    ("phone", "number", "contact", "contacts", "save"),
    ("google", "search", "seo", "ranking", "rank", "visibility"),
    ("convert", "conversion", "conversions", "landing", "signup", "funnel"),
    ("proposal", "pitch", "bid", "offer", "quote"),
    ("location", "where", "gps", "place", "city"),
    ("plugged", "connected", "devices", "device", "usb", "hardware"),
    ("healthy", "health", "status", "cpu", "memory", "ram", "disk", "performance"),
    ("backup", "back", "snapshot", "restore", "database"),
    ("changes", "change", "track", "watch", "monitor", "updates"),
    ("safe", "safely", "sandbox", "isolated", "snippet", "python", "code", "run"),
    ("parts", "mesh", "brain", "subsystems", "components", "together"),
    ("reliable", "reliability", "tools", "failures", "working"),
    ("lately", "recently", "focus", "situation", "working", "doing"),
    ("happened", "history", "past", "week", "yesterday", "timeline", "recent"),
    ("model", "models", "llm", "qwen", "llama", "pull"),
    ("surah", "quran", "ayah", "verse", "recite"),
    ("traffic", "camera", "cameras", "webcam", "live", "stream"),
)

def _stem(word: str) -> str:
    for suffix in ("ings", "ing", "ies", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


# Queries are stemmed before lookup. Keeping raw keys here silently lost
# concept links for inflected words such as "plugged", "quakes" and "changes".
_EXPANSION: dict[str, set[str]] = {}
for _group in CONCEPTS:
    _related = {_stem(word) for word in _group}
    for _word in _group:
        _EXPANSION.setdefault(_stem(_word), set()).update(_related)


def terms(text: str) -> list[str]:
    words = [word for word in _WORD.findall(str(text).lower().replace("_", " ")) if word not in _STOP]
    return [_stem(word) for word in words]


class ToolIndex:
    """IDF-weighted relevance of every registered tool to a request."""

    NAME_WEIGHT, DESCRIPTION_WEIGHT, AREA_WEIGHT, CONCEPT_WEIGHT = 3.0, 1.0, 0.5, 0.6

    def __init__(self, tools: Iterable[dict[str, Any]]) -> None:
        from app import capabilities

        tools = list(tools)
        names = [tool["name"] for tool in tools]
        grouped, _unplaced = capabilities.classify(names)
        area_text: dict[str, str] = {}
        for capability in capabilities.CATALOG:
            words = " ".join([capability.title, capability.summary, *capability.keywords, *capability.examples])
            for name in grouped.get(capability.key, ()):
                area_text[name] = area_text.get(name, "") + " " + words
        self._documents: dict[str, Counter] = {}
        for tool in tools:
            document: Counter = Counter()
            for term in terms(tool["name"]):
                document[term] += self.NAME_WEIGHT
            for term in terms(tool.get("description", "")):
                document[term] += self.DESCRIPTION_WEIGHT
            for term in terms(area_text.get(tool["name"], "")):
                document[term] += self.AREA_WEIGHT
            self._documents[tool["name"]] = document
        count = max(1, len(self._documents))
        frequency = Counter(term for document in self._documents.values() for term in document)
        self._idf = {term: math.log(1 + count / seen) for term, seen in frequency.items()}

    def scores(self, request: str) -> dict[str, float]:
        query: dict[str, float] = {}
        for word in terms(request):
            query[word] = max(query.get(word, 0.0), 1.0)
            for related in _EXPANSION.get(word, ()):
                query[related] = max(query.get(related, 0.0), self.CONCEPT_WEIGHT)
        results = {}
        for name, document in self._documents.items():
            score = sum(weight * self._idf.get(term, 0.0) * min(document.get(term, 0.0), 4.0)
                        for term, weight in query.items())
            if score > 0:
                results[name] = score
        return results

    def relevant(self, request: str, limit: int = 10, *, floor: float = 0.4) -> list[str]:
        """The best-fitting tools: at most `limit`, each within `floor` of the best score."""
        ranked = sorted(self.scores(request).items(), key=lambda item: (-item[1], item[0]))
        if not ranked:
            return []
        best = ranked[0][1]
        chosen = [name for name, score in ranked[:limit] if score >= best * floor]
        if len(chosen) == limit:
            # No cut between equals: 12.93 against 12.94 is noise, and on 2026-10-11 that cut left
            # newsletter_builder off "a weekly email for my bakery's customers" when two tools were added.
            last = ranked[limit - 1][1]
            chosen += [name for name, score in ranked[limit:limit + 3] if score >= last * TIE]
        return chosen


#: Scores within 2 % of the last place on the shortlist count as tied with it.
TIE = 0.98

_cache: dict[int, tuple[tuple[str, ...], ToolIndex]] = {}
_lock = threading.Lock()


def index_for(registry: Any) -> ToolIndex:
    """One index per registry, rebuilt only when its tools change."""
    names = tuple(registry.names())
    with _lock:
        cached = _cache.get(id(registry))
        if cached is None or cached[0] != names:
            cached = (names, ToolIndex(registry.describe()))
            _cache[id(registry)] = cached
        return cached[1]
