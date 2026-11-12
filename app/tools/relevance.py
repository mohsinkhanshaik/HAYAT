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


class SemanticTools:
    """Meaning, not shared words: "will it be windy in Chicago tomorrow" is about weather, "put Firefox on this
    computer" is installing software, though neither shares a word with the tool that does it.

    Each tool's name and description is embedded once with HAYAT's local embedding model (nomic-embed-text through
    Ollama) and kept in a cache file keyed by the model and the text, so only new or changed tools are embedded
    again. A request is one more embedding (about 20 ms); the tools closest to it join the word-based shortlist.
    Without an embedding model nothing changes: the word-based index works alone.
    """

    MINIMUM = 0.25  # cosine similarity below which a tool is not close enough to suggest
    BATCH = 64
    # Two views per tool, the closer one counts: its name alone ("intelligence weather") and its name with the full
    # description. Measured on the 42 tuning requests, the right tool reached the top 8 for 29 with both against 25
    # with the description alone (all-minilm), and 34 with both on nomic-embed-text; held-out checked once after.

    def __init__(self, provider: Any, cache: Any = None) -> None:
        self.provider, self.cache = provider, cache
        self._vectors: dict[str, list[list[float]]] = {}
        self._signature: tuple[str, ...] = ()
        self._lock = threading.Lock()

    @staticmethod
    def _texts(tool: dict[str, Any]) -> tuple[str, str]:
        name = tool["name"].replace("_", " ")
        return name, (name + ": " + " ".join(str(tool.get("description") or "").split()))[:500]

    def _key(self, text: str) -> str:
        import hashlib

        identity = (getattr(self.provider, "name", ""), getattr(self.provider, "base_url", ""),
                    getattr(self.provider, "model", ""), getattr(self.provider, "version", "1"))
        return hashlib.sha256(f"{identity}\0{text}".encode()).hexdigest()[:24]

    @staticmethod
    def _valid_vector(vector: Any, dimension: int | None = None) -> bool:
        return (isinstance(vector, list) and bool(vector) and (dimension is None or len(vector) == dimension)
                and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value) for value in vector)
                and any(value != 0 for value in vector))

    def prepare(self, tools: Iterable[dict[str, Any]], *, blocking: bool = True) -> int:
        """Embed every tool not yet embedded (from the cache first); returns how many were embedded now."""
        import json

        tools = list(tools)
        signature = (self._key(""), *sorted(text for tool in tools for text in self._texts(tool)))
        if not self._lock.acquire(blocking=blocking):
            return 0  # background warmup owns it; lexical selection stays available
        try:
            if signature == self._signature and self._vectors:
                return 0
            stored: dict[str, list[float]] = {}
            if self.cache is not None:
                try:
                    stored = json.loads(self.cache.read_text(encoding="utf-8")) if self.cache.exists() else {}
                except (OSError, ValueError):
                    stored = {}
            if not isinstance(stored, dict):
                stored = {}
            dimension = getattr(self.provider, "dimension", None)
            stored = {key: vector for key, vector in stored.items() if self._valid_vector(vector, dimension)}
            texts = {text for tool in tools for text in self._texts(tool)}
            missing = sorted(text for text in texts if self._key(text) not in stored)
            for start in range(0, len(missing), self.BATCH):
                batch = missing[start:start + self.BATCH]
                vectors = self.provider.embed(batch)
                if not isinstance(vectors, list) or len(vectors) != len(batch):
                    raise ValueError("Tool embedding response has the wrong batch size")
                for text, vector in zip(batch, vectors, strict=True):
                    if not self._valid_vector(vector, dimension):
                        raise ValueError("Tool embedding response contains an invalid vector")
                    dimension = len(vector)
                    stored[self._key(text)] = [round(value, 5) for value in vector]
            self._vectors = {tool["name"]: [stored[self._key(text)] for text in self._texts(tool) if self._key(text) in stored]
                             for tool in tools}
            keys = {text: self._key(text) for text in texts}
            self._signature = signature
            if missing and self.cache is not None:
                try:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    live = set(keys.values())
                    self.cache.write_text(json.dumps({key: value for key, value in stored.items() if key in live}),
                                          encoding="utf-8")
                except OSError:
                    pass  # the vectors are in memory; the cache only saves the next start some seconds
            return len(missing)
        finally:
            self._lock.release()

    def nearest(self, request: str, limit: int = 5) -> list[str]:
        if not self._vectors or not str(request or "").strip():
            return []
        vectors = self._vectors  # prepare replaces the complete immutable snapshot
        response = self.provider.embed([str(request)[:500]])
        if not isinstance(response, list) or len(response) != 1 or not self._valid_vector(response[0]):
            raise ValueError("Tool query embedding is invalid")
        query = response[0]
        norm = math.sqrt(sum(value * value for value in query)) or 1.0
        scored = []
        for name, views in vectors.items():
            if any(len(vector) != len(query) for vector in views):
                raise ValueError("Tool query and stored embedding dimensions differ")
            similarity = max((sum(a * b for a, b in zip(query, vector, strict=True)) / (norm * (math.sqrt(sum(v * v for v in vector)) or 1.0))
                              for vector in views), default=0.0)
            if similarity >= self.MINIMUM:
                scored.append((similarity, name))
        return [name for _similarity, name in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]


_semantic: dict[str, Any] = {"tools": None}


def use_embeddings(provider: Any, cache: Any = None) -> SemanticTools | None:
    """Give the shortlist meaning-based matching with this embedding provider (None turns it off)."""
    _semantic["tools"] = SemanticTools(provider, cache) if provider is not None and getattr(provider, "available", True) else None
    return _semantic["tools"]


def semantic_matches(registry: Any, request: str, limit: int = 6) -> list[str]:
    """The tools closest in meaning to the request, or [] without an embedding model (or when it fails)."""
    tools = _semantic["tools"]
    if tools is None:
        return []
    try:
        tools.prepare(registry.describe(), blocking=False)
        return [name for name in tools.nearest(request, limit) if name in set(registry.names())]
    except Exception:  # the embedding model stopped: words alone, as before
        from app.logging import log_swallowed
        import logging

        log_swallowed(logging.getLogger(__name__), "matching tools by meaning")
        return []

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
