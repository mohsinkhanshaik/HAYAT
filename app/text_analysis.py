"""Text analysis with NLTK: keywords, sentences, readability, sentiment and parts of speech, from text or a file.

Everything runs on this computer. NLTK's language data (sentence splitter,
stop-word lists, the VADER sentiment lexicon, the part-of-speech tagger --
about 22 MB) is downloaded once, on request, into data/nltk_data inside the
HAYAT folder ("set up language tools"). Until then the analysis still works with
simpler built-in rules and says which parts are approximate.

NLTK's own file sandbox is kept closed while it runs (app/security/nltk_guard.py).
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed
from app.security.nltk_guard import NLTK_DATA, install

_log = logging.getLogger(__name__)

TEXT_TOOL_NAMES = ("text_analyze", "text_tools_setup")
PACKAGES = {"punkt_tab": "sentence and word splitting", "stopwords": "stop-word lists (the, and, of …)",
            "vader_lexicon": "VADER sentiment lexicon", "averaged_perceptron_tagger_eng": "part-of-speech tagger"}
#: Where each package lands under nltk_data, to tell installed from missing without loading anything.
_PACKAGE_PATHS = {"punkt_tab": "tokenizers/punkt_tab", "stopwords": "corpora/stopwords",
                  "vader_lexicon": "sentiment/vader_lexicon.zip", "averaged_perceptron_tagger_eng":
                  "taggers/averaged_perceptron_tagger_eng"}
MAX_CHARS = 400_000
READ_SUFFIXES = (".txt", ".md", ".markdown", ".text", ".csv", ".log", ".json", ".html", ".htm", ".pdf", ".docx")
_FALLBACK_STOPWORDS = frozenset(
    "a an and are as at be been but by for from has have he her his i if in into is it its me my no not of on or our "
    "she so than that the their them then there these they this to too us was we were what when which who will with "
    "would you your can could do does did just also about over more most some such only own same very".split())


def _installed(name: str, folder: Path) -> bool:
    return (folder / _PACKAGE_PATHS[name]).exists()


class TextAnalyzer:
    def __init__(self, folder: Path | None = None, download: Callable[[str, str], bool] | None = None) -> None:
        self.folder = Path(folder or NLTK_DATA)
        self._download = download

    # ---------------------------------------------------------------- setup
    def status(self) -> dict[str, Any]:
        from app.security import nltk_guard

        packages = {name: _installed(name, self.folder) for name in PACKAGES}
        return {"folder": str(self.folder), "packages": packages, "ready": all(packages.values()),
                "nltk": nltk_guard.status()}

    def setup(self) -> dict[str, Any]:
        """Download NLTK's language data into the HAYAT folder (only what is missing)."""
        install()
        self.folder.mkdir(parents=True, exist_ok=True)
        results = {}
        for name in PACKAGES:
            if _installed(name, self.folder):
                results[name] = "already installed"
                continue
            try:
                ok = self._fetch(name)
                results[name] = "installed" if ok and _installed(name, self.folder) else "failed"
            except Exception as exc:
                log_swallowed(_log, f"downloading NLTK {name}", level=logging.WARNING)
                results[name] = f"failed: {type(exc).__name__}"
        size = sum(file.stat().st_size for file in self.folder.rglob("*") if file.is_file())
        ready = all(_installed(name, self.folder) for name in PACKAGES)
        return {"setup_view": True, "results": results, "ready": ready, "folder": str(self.folder),
                "size_mb": round(size / 2**20, 1),
                "summary": ("Language tools are ready" if ready else "Some language data could not be downloaded")
                + f" ({round(size / 2**20, 1)} MB in {self.folder})."}

    def _fetch(self, name: str) -> bool:
        if self._download is not None:
            return bool(self._download(name, str(self.folder)))
        import nltk

        return bool(nltk.download(name, download_dir=str(self.folder), quiet=True, raise_on_error=False))

    def _nltk(self) -> Any | None:
        """NLTK with HAYAT's data folder first in its search path, or None if NLTK is not installed."""
        install()
        try:
            import nltk
        except ImportError:
            return None
        folder = str(self.folder)
        if folder not in [str(item) for item in nltk.data.path]:
            nltk.data.path.insert(0, folder)
        return nltk

    # -------------------------------------------------------------- reading
    @staticmethod
    def read(path: str) -> str:
        file = Path(os.path.expandvars(path)).expanduser()
        if not file.is_file():
            raise ValueError(f"No file at {file}.")
        if file.suffix.lower() not in READ_SUFFIXES:
            raise ValueError("Give a text, Markdown, CSV, log, JSON, HTML, PDF or Word (.docx) file.")
        if file.stat().st_size > 40 * 1024 * 1024:
            raise ValueError("That file is over 40 MB.")
        suffix = file.suffix.lower()
        if suffix == ".pdf":
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(str(file)).pages[:200])
        elif suffix == ".docx":
            import docx
            text = "\n".join(paragraph.text for paragraph in docx.Document(str(file)).paragraphs)
        else:
            text = file.read_text(encoding="utf-8", errors="replace")
            if suffix in (".html", ".htm"):
                text = re.sub(r"(?is)<(script|style)\b.*?</\1>|<[^>]+>", " ", text)
        return text

    # -------------------------------------------------------------- analysis
    def analyze(self, text: str = "", path: str = "", top: int = 10) -> dict[str, Any]:
        if path:
            text = self.read(path)
        text = str(text or "")[:MAX_CHARS]
        if len(text.split()) < 3:
            raise ValueError("Give at least a sentence of text (or a file path) to analyze.")
        top = max(3, min(int(top or 10), 30))
        nltk = self._nltk()
        status = {name: _installed(name, self.folder) for name in PACKAGES}
        approximate = []

        if nltk is not None and status["punkt_tab"]:
            sentences = nltk.sent_tokenize(text)
            words = [word for word in nltk.word_tokenize(text) if re.search(r"\w", word)]
        else:
            sentences = [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part]
            words = re.findall(r"[A-Za-z0-9][\w'-]*", text)
            approximate.append("sentences and words (simple splitting)")
        if nltk is not None and status["stopwords"]:
            from nltk.corpus import stopwords
            stop = set(stopwords.words("english"))
        else:
            stop = set(_FALLBACK_STOPWORDS)
            approximate.append("keywords (short built-in stop-word list)")
        content = [word.lower() for word in words if word.isalpha() and word.lower() not in stop and len(word) > 2]
        keywords = [{"word": word, "count": count} for word, count in Counter(content).most_common(top)]
        pairs = Counter(zip(content, content[1:]))
        phrases = [{"phrase": f"{a} {b}", "count": count} for (a, b), count in pairs.most_common(top) if count > 1][:top]

        syllables = sum(_syllables(word) for word in words if word.isalpha())
        alpha = [word for word in words if word.isalpha()]
        per_sentence = len(alpha) / max(1, len(sentences))
        per_word = syllables / max(1, len(alpha))
        ease = round(206.835 - 1.015 * per_sentence - 84.6 * per_word, 1)
        grade = round(0.39 * per_sentence + 11.8 * per_word - 15.59, 1)
        result: dict[str, Any] = {
            "analysis_view": True, "source": path or "text", "characters": len(text), "words": len(words),
            "sentences": len(sentences), "unique_words": len({word.lower() for word in alpha}),
            "average_sentence_words": round(per_sentence, 1), "reading_minutes": round(len(words) / 230, 1),
            "readability": {"flesch_reading_ease": ease, "grade_level": max(0.0, grade), "meaning": _ease_words(ease)},
            "keywords": keywords, "phrases": phrases,
        }
        if nltk is not None and status["vader_lexicon"]:
            from nltk.sentiment import SentimentIntensityAnalyzer
            analyzer = SentimentIntensityAnalyzer()
            overall = analyzer.polarity_scores(text[:100_000])
            scored = [(analyzer.polarity_scores(sentence)["compound"], sentence) for sentence in sentences[:2000]]
            scored.sort()
            result["sentiment"] = {"compound": overall["compound"], "label": _mood(overall["compound"]),
                                   "positive": overall["pos"], "negative": overall["neg"], "neutral": overall["neu"],
                                   "most_negative": scored[0][1][:240] if scored and scored[0][0] < -0.05 else None,
                                   "most_positive": scored[-1][1][:240] if scored and scored[-1][0] > 0.05 else None}
        else:
            approximate.append("sentiment (needs the VADER lexicon)")
        if nltk is not None and status["averaged_perceptron_tagger_eng"] and status["punkt_tab"]:
            tags = Counter(tag[:2] for _word, tag in nltk.pos_tag(words[:20_000]))
            total = max(1, sum(tags.values()))
            result["parts_of_speech"] = {"nouns_pct": round(100 * tags["NN"] / total), "verbs_pct": round(100 * tags["VB"] / total),
                                         "adjectives_pct": round(100 * tags["JJ"] / total),
                                         "adverbs_pct": round(100 * tags["RB"] / total)}
        else:
            approximate.append("parts of speech (needs the tagger)")
        result["approximate"] = approximate
        result["summary"] = _summary(result)
        return result


def _syllables(word: str) -> int:
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups) - (1 if word.endswith("e") and len(groups) > 1 and not word.endswith("le") else 0)
    return max(1, count)


def _ease_words(score: float) -> str:
    for floor, words in ((90, "very easy"), (70, "easy"), (60, "plain English"), (50, "fairly difficult"),
                         (30, "difficult"), (-math.inf, "very difficult")):
        if score >= floor:
            return words
    return "very difficult"


def _mood(compound: float) -> str:
    return "positive" if compound >= 0.05 else "negative" if compound <= -0.05 else "neutral"


def _summary(result: dict[str, Any]) -> str:
    count = lambda number, word: f"{number:,} {word}" + ("" if number == 1 else "s")  # noqa: E731
    parts = [f"{count(result['words'], 'word')} in {count(result['sentences'], 'sentence')} "
             f"(about {result['reading_minutes']} min to read)",
             f"readability {result['readability']['flesch_reading_ease']} ({result['readability']['meaning']}, "
             f"grade {result['readability']['grade_level']})"]
    if result.get("sentiment"):
        parts.append(f"tone {result['sentiment']['label']} ({result['sentiment']['compound']:+.2f})")
    if result["keywords"]:
        parts.append("key words: " + ", ".join(item["word"] for item in result["keywords"][:6]))
    return "; ".join(parts) + "."


# ------------------------------------------------------------------ chat
def report(history: list[dict[str, Any]]) -> str | None:
    for event in reversed(history or []):
        if event.get("tool") not in TEXT_TOOL_NAMES:
            continue
        result = event.get("result") or {}
        value = result.get("output") or {}
        if not result.get("success") or not isinstance(value, dict):
            continue
        if value.get("setup_view"):
            return value["summary"] + "\n" + "\n".join(f"  {name}: {state}" for name, state in value["results"].items())
        if value.get("analysis_view"):
            lines = [value["summary"]]
            if value.get("phrases"):
                lines.append("Repeated phrases: " + ", ".join(item["phrase"] for item in value["phrases"][:5]))
            sentiment = value.get("sentiment") or {}
            if sentiment.get("most_positive"):
                lines.append(f"Most positive: “{sentiment['most_positive']}”")
            if sentiment.get("most_negative"):
                lines.append(f"Most negative: “{sentiment['most_negative']}”")
            if value.get("parts_of_speech"):
                pos = value["parts_of_speech"]
                lines.append(f"Nouns {pos['nouns_pct']}% · verbs {pos['verbs_pct']}% · adjectives {pos['adjectives_pct']}% · "
                             f"adverbs {pos['adverbs_pct']}%")
            if value.get("approximate"):
                lines.append("Approximate until the language tools are set up (say 'set up language tools'): "
                             + "; ".join(value["approximate"]) + ".")
            return "\n".join(lines)
    return None


_SETUP = re.compile(r"(?i)^(?:please\s+)?(?:set\s*up|install|download|get)\s+(?:the\s+)?(?:language|text|nltk)\s+"
                    r"(?:tools|data|analysis)\b")
_ANALYZE = re.compile(
    r"(?is)^(?:please\s+)?(?:analy[sz]e|check|what(?:'s|\s+is)\s+the\s+(?:sentiment|tone|readability)\s+of|"
    r"(?:find|get|show)\s+(?:the\s+)?keywords\s+(?:in|of)|how\s+readable\s+is|sentiment\s+of|tone\s+of|keywords\s+(?:in|of))"
    r"(?:\s+(?:the\s+)?(?:sentiment|tone|readability|keywords)\s+(?:of|in))?\s*"
    r"(?:(?:this|the|my)\s+)?(?:(?:text|file|document|essay|article|email|message|review|note)\b)?\s*"
    r"(?:(?:in|at|from|called)\s+)?[:\-]?\s*(?P<rest>.+)$")
_PATH = re.compile(r"(?i)^[\"']?((?:[a-z]:[\\/]|\.{0,2}[\\/]|~[\\/])?[^\"'\n]*\.(?:txt|md|markdown|text|csv|log|json|html?|pdf|docx))[\"']?\s*[.?!]*$")


def text_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split()) if "\n" not in str(text or "") else str(text).strip()
    if _SETUP.match(value):
        return {"tool": "text_tools_setup", "arguments": {}}
    match = _ANALYZE.match(value)
    if not match:
        return None
    # "Analyze report.pdf" means "what does it say" -- the document tools' job. This tool is for asking about the
    # text itself: its sentiment, tone, readability, keywords, word count, or "analyze this text".
    if not (re.search(r"(?i)\b(?:sentiment|tone|readab\w*|keywords?|word\s+count|text\s+analysis)\b", value)
            or re.match(r"(?i)^(?:please\s+)?(?:analy[sz]e|check)\s+(?:(?:this|the|my)\s+)?text\b", value)):
        return None
    rest = match["rest"].strip()
    path = _PATH.match(rest)
    if path:
        return {"tool": "text_analyze", "arguments": {"path": path[1]}}
    # A text to analyze, not a request like "analyze the market" or "check my router".
    if len(rest.split()) < 6 and ":" not in value:
        return None
    return {"tool": "text_analyze", "arguments": {"text": rest.strip("\"'“”")}}


def direct_text_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    request = text_request(text)
    if not request or request["tool"] not in set(names):
        return None
    description = ("Download NLTK's language data into the HAYAT folder." if request["tool"] == "text_tools_setup"
                   else "Analyze the text with NLTK: keywords, readability, sentiment, parts of speech.")
    return [{"description": description, "tool": request["tool"], "arguments": request["arguments"],
             "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_text_tools(registry: Any) -> TextAnalyzer:
    analyzer = TextAnalyzer()
    registry.text_analysis = analyzer
    registry.register(
        "text_analyze",
        "Analyze text or a text/PDF/Word file on this computer with NLTK: word and sentence counts, reading time, "
        "readability (Flesch ease and grade), key words and repeated phrases, sentiment (VADER) with the most positive "
        "and negative sentences, and the mix of nouns, verbs and adjectives.",
        {"type": "object", "properties": {"text": {"type": "string", "maxLength": MAX_CHARS},
                                          "path": {"type": "string", "maxLength": 500},
                                          "top": {"type": "integer", "minimum": 3, "maximum": 30}},
         "additionalProperties": False},
        "low", "filesystem_read", analyzer.analyze)
    registry.register(
        "text_tools_setup",
        "Download NLTK's language data (sentence splitter, stop words, sentiment lexicon, part-of-speech tagger; about "
        "22 MB) into data/nltk_data inside the HAYAT folder, so text analysis is exact and works offline.",
        {"type": "object", "properties": {}, "additionalProperties": False}, "low", "public_download",
        lambda: analyzer.setup())
    return analyzer
