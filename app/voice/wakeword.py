"""Wake words: HAYAT answers to its names, "Hayat" and "Jarvis" by default.

A name works on its own at the start of what was said ("Jarvis, what's on today?", "Hayat?"), or after a greeting
anywhere ("ok so hey jarvis turn the lights off"). A bare name in the middle of a sentence ("I read about jarvis in a
film") does not wake it: that is talk about the name, not to it. Whisper writes some names the way they sound, so each
name has the spellings it is actually heard as ("hyatt" for Hayat, "jervis" for Jarvis).

Audio never leaves the speech-to-text pipeline; this only reads the transcript.
"""
from __future__ import annotations

import re

#: How each name is commonly written by speech recognition.
HEARD_AS: dict[str, tuple[str, ...]] = {
    # Latin spellings Whisper writes, and the name in Hindi, Urdu, Arabic and Telugu script (a multilingual model
    # writes what it hears in the script of the language it detected).
    "hayat": ("hayat", "hayath", "hayaat", "hyatt", "hayatt", "hiyat", "hayet", "haya t",
              "हयात", "हयाथ", "حیات", "حياة", "حيات", "హయాత్", "హయత్"),
    "jarvis": ("jarvis", "jervis", "jarvas", "javis", "jarviss", "jarvi",
               "जार्विस", "जारविस", "جارویس", "جارفيس", "جارفس", "జార్విస్", "జార్విస"),
}
#: Ways people get attention before a name, in the languages he speaks.
GREETINGS = ("hey", "hi", "ok", "okay", "hello", "yo", "oi", "suno", "sunna", "arey", "arre", "ae", "ya", "yaa",
             "ऐ", "अरे", "सुनो", "ارے", "سنو", "يا", "ఏయ్", "ఓయ్")
DEFAULT_NAMES = ("hayat", "jarvis")

#: Letters, digits and the marks of Indic and Arabic scripts stay; everything else is punctuation. A plain \w drops
#: the vowel signs of Devanagari and Telugu (they are combining marks), which split "हयात" in two.
_NOT_WORD = re.compile(r"[،؛؟٪-٭۔।॥]|[^\w\s'؀-ۿݐ-ݿऀ-ൿ]")


def _normalize(text: str) -> str:
    return " ".join(_NOT_WORD.sub(" ", str(text or "").lower()).split())


class WakeWordDetector:
    """Phrase gate for wake mode: finds a name (or a configured phrase) in a transcript and returns the command after it."""

    def __init__(self, phrase: str = "hey hayat", aliases: list[str] | None = None,
                 names: list[str] | tuple[str, ...] | None = None) -> None:
        self.phrase = _normalize(phrase)
        self.names = tuple(dict.fromkeys(_normalize(name) for name in (names if names is not None else DEFAULT_NAMES)
                                         if _normalize(name)))
        spoken = {form for name in self.names for form in HEARD_AS.get(name, (name,))}
        greeted = {f"{greeting} {form}" for greeting in GREETINGS for form in spoken}
        # Longest first, so "hey jarvis" is taken before "jarvis".
        self.phrases = sorted({self.phrase, *(_normalize(item) for item in (aliases or [])), *greeted} - {""},
                              key=len, reverse=True)
        self.bare = sorted(spoken, key=len, reverse=True)

    def extract_command(self, transcription: str) -> str | None:
        """The words after the wake word ("" when only the name was said), or None when it was not addressed."""
        normalized = _normalize(transcription)
        if not normalized:
            return None
        for phrase in self.phrases:
            found = re.search(rf"(?:^|\s){re.escape(phrase)}(?=\s|$)", normalized)
            if found:
                return normalized[found.end():].strip()
        # A bare name counts only at the very start: "Jarvis, ..." or "Hayat?".
        for form in self.bare:
            if normalized == form or normalized.startswith(form + " "):
                return normalized[len(form):].strip()
        return None

    def describe(self) -> str:
        """The names in plain words, for the voice window and replies: "Hayat or Jarvis"."""
        shown = [name.title() for name in self.names] or [self.phrase.title()]
        return " or ".join(shown) if len(shown) <= 2 else ", ".join(shown[:-1]) + " or " + shown[-1]
