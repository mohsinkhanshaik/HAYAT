"""Read the Quran from a published text, with Roman-English transliteration.

The owner asked "start quran from starting with roman english so that i can
read". It was taken for an application called "Quran From Starting With Roman
English" and refused. Answering from the model would have been worse: a
language model reciting scripture from memory drops and alters words, and
nobody reading along to learn could tell.

So the words come from a published source, never from the model: the Tanzil
Uthmani Arabic text, its English transliteration and the Saheeh International
translation, served together by the free api.alquran.cloud. Each surah is
cached under data/knowledge/quran after its first read, so a surah read once
reads again offline. The reading position is kept, so "continue" goes on from
the last ayah shown.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

API = "https://api.alquran.cloud/v1"
EDITIONS = "quran-uthmani,en.transliteration,en.sahih"
SOURCE = ("Tanzil Uthmani Arabic text, English transliteration and Saheeh International translation, "
          "via api.alquran.cloud")
READING_NOTE = ("Transliteration is a reading aid. Some Arabic sounds have no English letter, so learn "
                "pronunciation (tajweed) with a qualified teacher or a recited recording.")
CONTINUE_HINT = 'Say "continue quran" for the next ayahs.'
#: Exactly as the Uthmani edition writes it at the start of ayah 1.
BISMILLAH = "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ"

#: Names people say that do not match the API's spelling ("Al-Faatiha").
ALIASES = {
    "fatiha": 1, "fatihah": 1, "opening": 1, "alhamd": 1, "baqarah": 2, "baqara": 2, "imran": 3, "nisa": 4,
    "maidah": 5, "kahf": 18, "maryam": 19, "taha": 20, "yasin": 36, "yaseen": 36, "ya sin": 36, "rahman": 55,
    "waqiah": 56, "waqia": 56, "mulk": 67, "naba": 78, "duha": 93, "inshirah": 94, "sharh": 94, "qadr": 97,
    "zalzalah": 99, "asr": 103, "kawthar": 108, "kausar": 108, "kafirun": 109, "nasr": 110, "masad": 111,
    "lahab": 111, "ikhlas": 112, "falaq": 113, "nas": 114, "naas": 114,
}


def _key(name: str) -> str:
    """'Al-Faatiha' and 'al fatiha' both become 'fatiha'."""
    value = re.sub(r"[^a-z ]", "", str(name or "").lower().replace("-", " "))
    value = re.sub(r"^(?:surah|surat|sura|soorah)\s+", "", value.strip())
    # The article only when it is separate: "as asr" -> "asr", but "asr" stays.
    value = re.sub(r"^(?:al|an|ar|as|at|ad|ash|az|adh)\s+", "", value).strip()
    value = re.sub(r"(.)\1", r"\1", value)  # faatiha -> fatiha, yaseen -> yasen
    return value.replace(" ", "")


def _candidates(name: str) -> set[str]:
    key = _key(name)
    # "alfatiha" written as one word.
    joined = re.sub(r"^(?:al|an|ar|as|at|ad|ash|az|adh)(?=[a-z]{3,})", "", key)
    return {key, joined} - {""}


_ALIAS_KEYS = {_key(name): number for name, number in ALIASES.items()}


class QuranReader:
    def __init__(self, get_json: Callable[[str], Any], folder: Path) -> None:
        self.get_json, self.folder = get_json, Path(folder)

    # ------------------------------------------------------------- sources
    def _cached(self, name: str, url: str) -> Any:
        path = self.folder / name
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log_swallowed(_log, f"reading cached Quran file {name}")
        data = self.get_json(url)
        if not isinstance(data, dict) or data.get("code") != 200:
            raise ValueError("The Quran text service did not return the text. Try again in a moment.")
        self.folder.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        partial.replace(path)
        return data

    def surahs(self) -> list[dict[str, Any]]:
        data = self._cached("surahs.json", f"{API}/surah")["data"]
        return [item for item in data if isinstance(item, dict) and isinstance(item.get("number"), int)]

    def resolve(self, surah: Any) -> int:
        if isinstance(surah, int) or str(surah).strip().isdigit():
            number = int(surah)
            if not 1 <= number <= 114:
                raise ValueError("A surah number goes from 1 to 114.")
            return number
        wanted = _candidates(str(surah))
        if not wanted:
            return 1
        for candidate in wanted:
            if candidate in _ALIAS_KEYS:
                return _ALIAS_KEYS[candidate]
        for item in self.surahs():
            names = {_key(item.get("englishName", "")), _key(item.get("englishNameTranslation", ""))}
            if wanted & names:
                return item["number"]
        for item in self.surahs():
            if any(len(candidate) >= 4 and candidate in _key(item.get("englishName", "")) for candidate in wanted):
                return item["number"]
        raise ValueError(f"I couldn't find a surah called {surah!r}. Give its name or number (1-114).")

    # ------------------------------------------------------------- reading
    def read(self, surah: Any = 1, start_ayah: int = 1, count: int = 7, arabic: bool = True) -> dict[str, Any]:
        number = self.resolve(surah)
        data = self._cached(f"surah-{number:03d}.json", f"{API}/surah/{number}/editions/{EDITIONS}")["data"]
        if not isinstance(data, list) or len(data) != 3:
            raise ValueError("The Quran text service returned an incomplete surah.")
        text, transliteration, translation = data
        total = int(text.get("numberOfAyahs") or len(text.get("ayahs") or []))
        start = max(1, min(int(start_ayah or 1), total))
        count = max(1, min(int(count or 7), 30))
        end = min(total, start + count - 1)
        ayahs = []
        bismillah = ""
        for index in range(start - 1, end):
            words = str(text["ayahs"][index]["text"]).lstrip("﻿")
            # The Uthmani edition prefixes the Bismillah to ayah 1 of every surah
            # but Al-Fatiha (where it is ayah 1) and At-Tawba (which has none);
            # the transliteration and translation do not, so the lines disagreed.
            if index == 0 and number not in (1, 9):
                tokens = words.split()
                if len(tokens) > 4 and [_letters(token) for token in tokens[:4]] == _BISMILLAH_LETTERS:
                    bismillah, words = " ".join(tokens[:4]), " ".join(tokens[4:])
            ayahs.append({
                "ayah": index + 1,
                "arabic": words if arabic else "",
                "transliteration": _clean(transliteration["ayahs"][index]["text"]),
                "translation": _clean(translation["ayahs"][index]["text"]),
            })
        following = ({"surah": number, "ayah": end + 1} if end < total
                     else {"surah": number + 1, "ayah": 1} if number < 114 else None)
        self._save_position(following)
        return {
            "quran": True,
            "surah": {"number": number, "name": text.get("englishName"), "meaning": text.get("englishNameTranslation"),
                      "arabic_name": text.get("name"), "ayahs": total, "revelation": text.get("revelationType")},
            "range": f"{number}:{start}-{end}" if end > start else f"{number}:{start}",
            "ayahs": ayahs, "next": following, "source": SOURCE, "note": READING_NOTE,
            "bismillah": ({"arabic": bismillah, "transliteration": "Bismillaahir Rahmaanir Raheem",
                           "translation": "In the name of Allah, the Entirely Merciful, the Especially Merciful."}
                          if bismillah else None),
            "next_step": CONTINUE_HINT if following else "That is the end of the Quran.",
        }

    def continue_reading(self, count: int = 7, arabic: bool = True) -> dict[str, Any]:
        position = self._position() or {"surah": 1, "ayah": 1}
        return self.read(position["surah"], position["ayah"], count, arabic)

    def _position_path(self) -> Path:
        return self.folder / "position.json"

    def _position(self) -> dict[str, int] | None:
        try:
            value = json.loads(self._position_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(value, dict) and isinstance(value.get("surah"), int) and isinstance(value.get("ayah"), int):
            return value
        return None

    def _save_position(self, position: dict[str, int] | None) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self._position_path().write_text(json.dumps(position or {"surah": 1, "ayah": 1}), encoding="utf-8")
        except OSError:
            log_swallowed(_log, "saving the Quran reading position")


def _letters(word: str) -> str:
    """Base letters only: vowel marks, tatweel and the wasla form of alif removed.

    The service and this file encode the same Bismillah with different
    combining-mark sequences, so the words are compared by their letters.
    """
    import unicodedata

    bare = "".join(ch for ch in unicodedata.normalize("NFD", word) if unicodedata.category(ch) != "Mn")
    return bare.replace("ـ", "").replace("ٱ", "ا")


_BISMILLAH_LETTERS = [_letters(word) for word in BISMILLAH.split()]


def _clean(value: Any) -> str:
    return re.sub(r"<[^>]+>", "", " ".join(str(value or "").split()))


# ------------------------------------------------------------------ routing
_QURAN = r"(?:quran|qur'?an|koran|kuran)"
_SURAH = r"(?:surah|surat|sura|soorah|chapter)"
_READ_VERB = re.compile(
    rf"(?i)^(?:(?:please|ok|okay|now|can\s+you|could\s+you|lets?|let's)\s+)*"
    rf"(?:start|begin|read|recite|show|open|teach\s+me(?:\s+to\s+read)?|help\s+me\s+read|give\s+me|play)\b.*\b(?:{_QURAN}|{_SURAH})\b")
_CONTINUE = re.compile(
    rf"(?i)^(?:(?:ok|okay|now|please)\s+)*(?:continue|next|go\s+on|keep\s+going|more)"
    rf"(?:\s+(?:reading\s+)?(?:the\s+)?(?:{_QURAN}|ayahs?|ayat|verses?|reading))?[\s.!?]*$")
#: Questions about the Quran are not requests to read it.
_QUESTION = re.compile(r"(?i)\b(?:what\s+does|what\s+do|say\s+about|says\s+about|about\s+the|meaning\s+of\s+life|"
                       r"how\s+many|who\s+wrote|when\s+was|why)\b")


def quran_request(text: str, last_response: str = "") -> dict[str, Any] | None:
    """Arguments for quran_read, {"continue": True}, or None."""
    value = " ".join(str(text or "").split()).strip()
    if not value:
        return None
    if _CONTINUE.match(value):
        named = re.search(rf"(?i)\b(?:{_QURAN}|ayahs?|ayat|verses?)\b", value)
        if named or CONTINUE_HINT in (last_response or ""):
            return {"continue": True}
        return None
    if not _READ_VERB.match(value) or _QUESTION.search(value):
        return None
    arguments: dict[str, Any] = {}
    number = re.search(rf"(?i)\b{_SURAH}\s+(?:no\.?\s*|number\s+)?(\d{{1,3}})\b", value)
    named = re.search(rf"(?i)\b{_SURAH}\s+((?:al[- ]?|an[- ]?|ar[- ]?|as[- ]?|ya[- ]?)?[a-z][a-z'-]+)", value)
    reference = re.search(r"\b(\d{1,3}):(\d{1,3})\b", value)
    if reference:
        arguments.update(surah=int(reference[1]), start_ayah=int(reference[2]))
    elif number:
        arguments["surah"] = int(number[1])
    elif named and named[1].lower() not in {"from", "with", "in", "and"}:
        arguments["surah"] = named[1]
    ayah = re.search(r"(?i)\b(?:ayah|ayat|verse|aya)\s+(\d{1,3})\b", value)
    if ayah and "start_ayah" not in arguments:
        arguments["start_ayah"] = int(ayah[1])
    return arguments


def direct_quran_plan(text: str, names: Any, last_response: str = "") -> list[dict[str, Any]] | None:
    request = quran_request(text, last_response)
    if request is None:
        return None
    tool = "quran_continue" if request.get("continue") else "quran_read"
    if tool not in set(names):
        return None
    arguments = {} if request.get("continue") else request
    return [{"description": "Read the Quran from the published text with transliteration and translation.",
             "tool": tool, "arguments": arguments, "expected": {"type": "tool_success"}, "max_retries": 0}]


def register_quran_tools(registry: Any, get_json: Callable[[str], Any], folder: Path) -> QuranReader:
    reader = QuranReader(get_json, folder)
    registry.register(
        "quran_read",
        "Read ayahs of the Quran from the published Tanzil text: Arabic, Roman-English transliteration and the "
        "Saheeh International translation. surah is a name (Al-Fatiha, Yasin) or number 1-114.",
        {"type": "object", "properties": {
            "surah": {"type": ["string", "integer"]}, "start_ayah": {"type": "integer", "minimum": 1},
            "count": {"type": "integer", "minimum": 1, "maximum": 30}, "arabic": {"type": "boolean"}},
         "additionalProperties": False},
        "low", "public_api_get", reader.read)
    registry.register(
        "quran_continue",
        "Continue reading the Quran from where the last reading stopped.",
        {"type": "object", "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 30},
                                          "arabic": {"type": "boolean"}}, "additionalProperties": False},
        "low", "public_api_get", reader.continue_reading)
    return reader
