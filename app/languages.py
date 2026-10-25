"""Languages HAYAT can recognise, speak and study -- and learn on request.

HAYAT's reasoning model already reads and writes most languages; what it
lacked was the rest of a language as a skill: noticing that a message was in
Urdu rather than English, a voice to speak it, the multilingual speech model
to hear it, and a record of what it had studied. This module holds those.

* ``detect`` names the language of a message. It trusts a guess only when the
  message is in a non-Latin script or is long enough to judge -- "hi hello"
  was classified as Italian, and romanised Hindi as Finnish.
* ``plan`` explains what learning a language will download and study, so
  HAYAT can ask before it does anything. ``install`` then fetches the open
  Piper voice for it and queues public-source study (grammar, phrases,
  pronunciation) for the background learner.
* ``speaker`` returns a voice for a language, run in its own process.

Every voice is open weights from the Piper project (MIT); nothing is sent
anywhere, and after the download everything runs offline.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: code: (English name, default Piper voice, approximate download in MB).
#: Chosen from the Piper catalogue on 2026-09-30: medium quality where it exists.
VOICES: dict[str, tuple[str, str, int]] = {
    "ar": ("Arabic", "ar_JO-kareem-medium", 63),
    "bg": ("Bulgarian", "bg_BG-dimitar-medium", 63),
    "bn": ("Bengali", "bn_BD-google-medium", 77),
    "ca": ("Catalan", "ca_ES-upc_ona-medium", 63),
    "cs": ("Czech", "cs_CZ-jirka-medium", 63),
    "cy": ("Welsh", "cy_GB-bu_tts-medium", 77),
    "da": ("Danish", "da_DK-talesyntese-medium", 63),
    "de": ("German", "de_DE-mls-medium", 77),
    "el": ("Greek", "el_GR-joy-medium", 64),
    "en": ("English", "en_US-lessac-medium", 63),
    "es": ("Spanish", "es_ES-davefx-medium", 63),
    "et": ("Estonian", "et_EE-news-medium", 77),
    "eu": ("Basque", "eu_ES-antton-medium", 64),
    "fa": ("Persian", "fa_IR-amir-medium", 64),
    "fi": ("Finnish", "fi_FI-harri-medium", 63),
    "fr": ("French", "fr_FR-mls-medium", 77),
    "he": ("Hebrew", "he_IL-saspeech-medium", 63),
    "hi": ("Hindi", "hi_IN-pratham-medium", 64),
    "hu": ("Hungarian", "hu_HU-anna-medium", 63),
    "hy": ("Armenian", "hy_AM-gor-medium", 64),
    "id": ("Indonesian", "id_ID-news_tts-medium", 63),
    "is": ("Icelandic", "is_IS-bui-medium", 76),
    "it": ("Italian", "it_IT-paola-medium", 64),
    "ja": ("Japanese", "ja_JP-hi_fi_captain-medium", 77),
    "ka": ("Georgian", "ka_GE-natia-medium", 63),
    "kk": ("Kazakh", "kk_KZ-issai-high", 128),
    "ko": ("Korean", "ko_KR-kss-medium", 63),
    "ku": ("Kurdish", "ku_TR-berfin_renas-medium", 77),
    "lb": ("Luxembourgish", "lb_LU-marylux-medium", 63),
    "lt": ("Lithuanian", "lt_LT-reginute1-medium", 64),
    "lv": ("Latvian", "lv_LV-aivars-medium", 64),
    "ml": ("Malayalam", "ml_IN-arjun-medium", 63),
    "mr": ("Marathi", "mr_IN-google-medium", 77),
    "ne": ("Nepali", "ne_NP-chitwan-medium", 63),
    "nl": ("Dutch", "nl_BE-nathalie-medium", 63),
    "no": ("Norwegian", "no_NO-nvcc-medium", 77),
    "pl": ("Polish", "pl_PL-darkman-medium", 63),
    "pt": ("Portuguese", "pt_BR-cadu-medium", 63),
    "ro": ("Romanian", "ro_RO-mihai-medium", 63),
    "ru": ("Russian", "ru_RU-denis-medium", 63),
    "sk": ("Slovak", "sk_SK-lili-medium", 63),
    "sl": ("Slovenian", "sl_SI-artur-medium", 63),
    "sq": ("Albanian", "sq_AL-edon-medium", 64),
    "sr": ("Serbian", "sr_RS-serbski_institut-medium", 77),
    "sv": ("Swedish", "sv_SE-alma-medium", 63),
    "sw": ("Swahili", "sw_CD-lanfrica-medium", 63),
    "te": ("Telugu", "te_IN-maya-medium", 63),
    "th": ("Thai", "th_TH-tsync2-medium", 63),
    "tr": ("Turkish", "tr_TR-dfki-medium", 63),
    "uk": ("Ukrainian", "uk_UA-ukrainian_tts-medium", 77),
    "ur": ("Urdu", "ur_PK-aegis_female-medium", 64),
    "vi": ("Vietnamese", "vi_VN-vais1000-medium", 63),
    "zh": ("Chinese", "zh_CN-chaowen-medium", 63),
}
ALIASES = {"farsi": "fa", "persian": "fa", "mandarin": "zh", "chinese": "zh", "brazilian": "pt",
           "kurmanji": "ku", "filipino": None, "hinglish": None}
#: Unicode blocks, and the languages written in them (the first is the default guess).
SCRIPTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("arabic", "؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿", ("ar", "ur", "fa", "ku")),
    ("devanagari", "ऀ-ॿ", ("hi", "mr", "ne")),
    ("bengali", "ঀ-৿", ("bn",)),
    ("telugu", "ఀ-౿", ("te",)),
    ("malayalam", "ഀ-ൿ", ("ml",)),
    ("cyrillic", "Ѐ-ӿ", ("ru", "uk", "bg", "sr", "kk")),
    ("greek", "Ͱ-Ͽ", ("el",)),
    ("hebrew", "֐-׿", ("he",)),
    ("thai", "฀-๿", ("th",)),
    ("georgian", "Ⴀ-ჿ", ("ka",)),
    ("armenian", "԰-֏", ("hy",)),
    ("hangul", "가-힯ᄀ-ᇿ", ("ko",)),
    ("kana", "぀-ヿ", ("ja",)),
    ("han", "一-鿿", ("zh", "ja")),
)
_SCRIPT_PATTERNS = [(name, re.compile(f"[{chars}]"), codes) for name, chars, codes in SCRIPTS]
_LATIN_LETTER = re.compile(r"[A-Za-zÀ-ɏ]")


def language_name(code: str) -> str:
    return VOICES.get(code, (code,))[0] if code else ""


def language_code(name: str) -> str | None:
    """'Urdu' -> 'ur'. Accepts codes, English names and common aliases."""
    value = " ".join(str(name or "").lower().replace("language", "").split())
    if value in VOICES:
        return value
    if value in ALIASES:
        return ALIASES[value]
    return next((code for code, (english, _voice, _size) in VOICES.items() if english.lower() == value), None)


def script_of(text: str) -> tuple[str, tuple[str, ...]] | None:
    """The first non-Latin script in the text, with the languages written in it."""
    counts = [(len(pattern.findall(text or "")), name, codes) for name, pattern, codes in _SCRIPT_PATTERNS]
    counts = [item for item in counts if item[0]]
    if not counts:
        return None
    _count, name, codes = max(counts)
    if name == "han" and _SCRIPT_PATTERNS[12][1].search(text):         # kana present: Japanese
        return name, ("ja",)
    return name, codes


@dataclass
class Detection:
    code: str
    name: str
    confidence: float
    reliable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_identifier = None
_identifier_lock = threading.Lock()


def _langid(text: str, candidates: tuple[str, ...] | None = None) -> tuple[str, float]:
    global _identifier
    with _identifier_lock:
        if _identifier is None:
            from langid.langid import LanguageIdentifier, model

            _identifier = LanguageIdentifier.from_modelstring(model, norm_probs=True)
        _identifier.set_languages(list(candidates) if candidates else None)
        code, probability = _identifier.classify(text)
    return code, float(probability)


def detect(text: str) -> Detection | None:
    """Name the language of a message, or None when there is nothing to judge."""
    value = " ".join(str(text or "").split())
    if not value:
        return None
    script = script_of(value)
    try:
        if script is not None:
            name, codes = script
            code, probability = (codes[0], 1.0) if len(codes) == 1 else _langid(value, codes)
            return Detection(code, language_name(code), probability, True)
        if not _LATIN_LETTER.search(value):
            return None
        latin = [code for code in VOICES if script_of(language_name(code)) is None]
        code, probability = _langid(value, tuple(sorted(set(latin) - {"ar", "ur", "fa", "hi", "mr", "ne", "bn", "te",
                                                                        "ml", "ru", "uk", "bg", "sr", "kk", "el", "he",
                                                                        "th", "ka", "hy", "ko", "ja", "zh"})))
    except Exception:
        log_swallowed(_log, "detecting a message's language")
        return None
    words = len(re.findall(r"[^\W\d_]+", value))
    # Short Latin text is guessed badly ("hi hello" came out Italian), and
    # Hindi/Urdu typed in English letters came out Finnish at 97%. Real
    # foreign sentences score 99-100%, so only those count.
    reliable = code != "en" and words >= 4 and probability >= 0.99
    return Detection(code, language_name(code), probability, reliable or code == "en")


class LanguageVoice:
    """One installed Piper voice for a language, in its own process."""

    def __init__(self, voice: str, voice_root: Path, config: dict | None = None) -> None:
        self.voice, self.voice_root, self.config = voice, Path(voice_root), dict(config or {})
        self._tts = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return (self.voice_root / f"{self.voice}.onnx").is_file()

    def speak(self, text: str, wait: bool = False) -> None:
        if not text or not self.available or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        with self._lock:
            if self._tts is None:
                from app.voice.tts import IsolatedPiperTTS

                self._tts = IsolatedPiperTTS({**self.config, "voice": self.voice, "voice_root": str(self.voice_root)})
            tts = self._tts
        tts.speak(text, wait=wait)

    def stop(self) -> None:
        if self._tts is not None:
            self._tts.stop()

    @property
    def is_speaking(self) -> bool:
        return bool(self._tts is not None and self._tts.is_speaking)

    def close(self) -> None:
        tts, self._tts = self._tts, None
        if tts is not None:
            try:
                tts.close()
            except Exception:
                log_swallowed(_log, "closing a language voice")


class LanguageSkills:
    STUDY = ("{name} grammar essentials for beginners", "most common {name} phrases and their meanings",
             "{name} pronunciation and writing system guide")

    def __init__(self, root: Path | None = None, voice_root: Path | None = None, *,
                 learner: Callable[[], Any] = lambda: None, output_device: str = "auto") -> None:
        from app.voice.tts import VOICE_ROOT

        self.root = Path(root or PROJECT_ROOT / "data" / "learning")
        self.voice_root = Path(voice_root or VOICE_ROOT)
        self.state_path = self.root / "languages.json"
        self.learner = learner
        self.output_device = output_device
        self._speakers: dict[str, LanguageVoice] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ state
    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"installed": {}, "noticed": {}}

    def _save(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def voice_file(self, code: str) -> Path | None:
        """An installed voice for the language: the default one, else any on disk."""
        if code not in VOICES:
            return None
        default = self.voice_root / f"{VOICES[code][1]}.onnx"
        if default.is_file():
            return default
        found = sorted(self.voice_root.glob(f"{code}_*.onnx"))
        return found[0] if found else None

    def installed(self) -> list[str]:
        return [code for code in VOICES if self.voice_file(code) is not None]

    def speech_model_ready(self) -> bool:
        return (PROJECT_ROOT / "data" / "models" / "whisper" / "models--Systran--faster-whisper-small").is_dir()

    def note(self, code: str) -> int:
        """Count a message seen in this language; returns how many so far."""
        with self._lock:
            state = self._load()
            state.setdefault("noticed", {})[code] = int(state.get("noticed", {}).get(code, 0)) + 1
            self._save(state)
            return state["noticed"][code]

    # ------------------------------------------------------------ learning a language
    def plan(self, code: str) -> dict[str, Any]:
        name, voice, size = VOICES[code]
        installed = self.voice_file(code) is not None
        topics = [topic.format(name=name) for topic in self.STUDY]
        steps = []
        if not installed:
            steps.append(f"download the open Piper voice {voice} (about {size} MB) so I can speak {name}")
        if not self.speech_model_ready():
            steps.append("download the multilingual speech model (about 480 MB) so I can hear it")
        steps.append("queue public-source study for my background learner: " + "; ".join(topics))
        text = (f"To learn {name} I will " + ", then ".join(steps) + ". "
                f"I can already read and write {name}, because my language model knows it. Nothing is uploaded; "
                "after the download it all runs offline.")
        return {"code": code, "name": name, "voice": voice, "size_mb": size, "voice_installed": installed,
                "speech_model_ready": self.speech_model_ready(), "study": topics, "text": text}

    def install(self, code: str, *, download: Callable[[str, Path], Any] | None = None) -> dict[str, Any]:
        name, voice, size = VOICES[code]
        report: dict[str, Any] = {"code": code, "name": name, "voice": voice}
        if self.voice_file(code) is None:
            if download is None:
                from piper.download_voices import download_voice as download
            self.voice_root.mkdir(parents=True, exist_ok=True)
            download(voice, self.voice_root)
        report["voice_installed"] = self.voice_file(code) is not None
        queued = []
        learner = self.learner()
        for topic in (topic.format(name=name) for topic in self.STUDY):
            try:
                if learner is not None and learner.enqueue(topic, source="language_skill", priority=70):
                    queued.append(topic)
            except Exception:
                log_swallowed(_log, "queueing language study", level=logging.WARNING)
        report["study_queued"] = queued
        report["speech_model_ready"] = self.speech_model_ready()
        with self._lock:
            state = self._load()
            state.setdefault("installed", {})[code] = {"voice": voice, "installed_at": datetime.now(UTC).isoformat(),
                                                       "study": queued}
            self._save(state)
        return report

    def speaker(self, code: str) -> LanguageVoice | None:
        path = self.voice_file(code)
        if path is None:
            return None
        with self._lock:
            speaker = self._speakers.get(code)
            if speaker is None or speaker.voice != path.stem:
                speaker = self._speakers[code] = LanguageVoice(path.stem, self.voice_root,
                                                               {"output_device": self.output_device})
        return speaker

    def status(self) -> dict[str, Any]:
        state = self._load()
        installed = self.installed()
        return {
            "installed": [{"code": code, "name": language_name(code), "voice": self.voice_file(code).stem,
                           "studied": bool(state.get("installed", {}).get(code, {}).get("study"))} for code in installed],
            "available": [{"code": code, "name": name, "size_mb": size, "installed": code in installed}
                          for code, (name, _voice, size) in sorted(VOICES.items(), key=lambda item: item[1][0])],
            "noticed": state.get("noticed", {}),
            "speech_model_ready": self.speech_model_ready(),
        }

    def close(self) -> None:
        for speaker in list(self._speakers.values()):
            speaker.close()
