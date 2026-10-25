"""A language tutor that actually teaches: read, write, listen and speak.

"Teach me Arabic" used to answer "tell me what you already know" and then
forget the conversation, because nothing kept a lesson open. This keeps one
open per course, in the learning database, so a lesson resumes where it
stopped -- from the chat or from the Learn window, which share it.

How a lesson runs:

* New items are introduced a few at a time: the Arabic, how it sounds, what it
  means, spoken aloud when the Arabic voice is installed.
* Each item is then practised in every skill that applies to it -- reading,
  writing, listening, speaking -- and each (item, skill) pair has its own
  spaced-repetition box. A right answer moves it up (seen again later), a
  wrong one moves it back to the start (seen again within the minute).
* Grading is deterministic and forgiving where a beginner deserves it:
  missing vowel marks, the different alef shapes and small spelling slips are
  accepted or called "close", never silently marked right.

Listening needs the Arabic voice (scripts/setup_arabic.py downloads it once).
Speaking needs a microphone and the multilingual Whisper model; recording
happens only when asked (the Speak button or "record"), never in privacy mode.
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.learning.courses import COURSES
from app.learning.courses.arabic import Item, letter_forms
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

SKILLS = ("reading", "writing", "listening", "speaking")
#: Seconds until an (item, skill) is due again, by box. Boxes 0-2 keep an item
#: inside the current session; from box 3 it waits for a later day.
INTERVALS = {0: 0, 1: 60, 2: 600, 3: 86_400, 4: 3 * 86_400, 5: 7 * 86_400, 6: 21 * 86_400}
MAX_BOX = max(INTERVALS)
NEW_PER_STEP = {"letter": 4, "mark": 3, "word": 3, "phrase": 2}

_HARAKAT = re.compile("[ً-ْٰـ]")
_ARABIC = re.compile("[؀-ۿ]")
_COMMANDS = {
    "hint": "hint", "a hint": "hint", "help": "hint", "skip": "skip", "next": "skip", "i don't know": "skip",
    "i dont know": "skip", "idk": "skip", "repeat": "repeat", "again": "repeat", "say it again": "repeat",
    "play": "repeat", "listen": "repeat", "record": "record", "speak": "record", "record me": "record",
    "stop": "stop", "stop lesson": "stop", "end lesson": "stop", "pause": "stop", "pause lesson": "stop",
    "exit": "stop", "quit": "stop", "progress": "progress", "my progress": "progress",
}
#: A message that starts like this is a request to HAYAT, not an answer.
_NEW_REQUEST = re.compile(
    r"(?i)^(?:open|close|launch|search|google|browse|play (?:a|some|the)|turn|set|remind|send|email|message|"
    r"call|take a screenshot|shut ?down|restart|download|install|run|create|delete|find|research|look up)\b")


#: Talking to HAYAT rather than answering the lesson: a question, an instruction, a complaint. Live on 2026-10-07/08,
#: "Right, how do you feel now?", "learn and understand" and "i dint ask u that" were each graded "Not quite".
_TALKING_TO_HAYAT = re.compile(
    r"(?i)^(?:(?:right|ok(?:ay)?|so|well|and|but|hey|hayat)[,.!\s]+)*(?:"
    r"(?:how|what|why|who|where|when|which|can|could|would|will|do|does|did|are|is|have|has)\b[^?]*\?+\s*$"
    r"|i\s+(?:didn'?t|dint|did\s+not|never)\s+ask|(?:that'?s|this\s+is|it'?s)\s+not\s+what\s+i|not\s+what\s+i\s+(?:asked|meant|want)"
    r"|(?:learn|study|understand|remember|go\s+ahead|explain(?!\s+(?:again|it\s+again|simpler))|tell\s+me|show\s+me|"
    r"let'?s|lets|please|stop\s+teaching|no\s+more\s+lessons?)\b)")
#: Of those, the ones that mean "this lesson is not what I want": the lesson is paused, not just skipped.
LESSON_COMPLAINT = re.compile(
    r"(?i)\b(?:i\s+(?:didn'?t|dint|did\s+not|never)\s+ask|not\s+what\s+i\s+(?:asked|meant|want)|stop\s+teaching|"
    r"no\s+more\s+lessons?)\b")


def looks_like_new_request(text: str) -> bool:
    """A message that asks HAYAT to do something else, not a lesson answer."""
    value = (text or "").strip()
    return bool(_NEW_REQUEST.match(value)) or (bool(_TALKING_TO_HAYAT.match(value)) and not has_arabic(value))


def is_lesson_reply(text: str, pending: dict | None) -> bool:
    """Cheap check the router makes while a lesson is open.

    A lesson used to swallow every message until it ended. Now an obvious new
    request ("open notepad", a long question) is routed normally and the lesson
    stays open for the next answer.
    """
    value = " ".join((text or "").split())
    if not value or looks_like_new_request(value):
        return False
    pending = pending or {}
    if pending.get("type") == "language":
        # A lesson left behind -- the conversation moved on, or fifteen quiet
        # minutes passed -- no longer catches "yes", "hello" or a book title.
        idle = time.time() - float(pending.get("at", time.time())) > 900
        if pending.get("paused") or idle:
            return has_arabic(value) or value.lower().strip(" .!?") in {"hint", "skip", "repeat", "record", "stop lesson"}
        return has_arabic(value) or len(value.split()) <= 8
    return True


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text or ""))


def normalize_arabic(text: str) -> str:
    """Compare Arabic the way a teacher reading a beginner's writing would.

    Vowel marks and the joining stroke are dropped (everyday writing omits
    them), the alef shapes become plain alef, and final yaa/alef maqsura and
    taa marbuta/haa are treated as the same letter.
    """
    value = _HARAKAT.sub("", text or "")
    value = re.sub("[أإآٱ]", "ا", value)
    value = value.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي").replace("ء", "")
    value = re.sub(r"[^ء-ي\s]", " ", value)
    return " ".join(value.split())


def normalize_latin(text: str) -> str:
    """Transliteration and English: no accents, apostrophes or doubled letters."""
    value = unicodedata.normalize("NFKD", (text or "").lower())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[ʿʾ'’‘`\-]", "", value)
    for short, full in (("im", "i am"), ("dont", "do not"), ("whats", "what is"), ("youre", "you are"), ("its", "it is")):
        value = re.sub(rf"\b{short}\b", full, value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"^(?:to|the|a|an)\s+", "", " ".join(value.split()))
    return value


def _squash(value: str) -> str:
    return re.sub(r"(.)\1+", r"\1", value.replace(" ", ""))


def _similarity(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return float(fuzz.ratio(a, b))


@dataclass
class Verdict:
    correct: bool
    close: bool
    score: float
    expected: str
    heard: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Step:
    """What the tutor says, and the exercise now open (if any)."""
    text: str
    exercise: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    introduced: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    course: str = ""


class ArabicVoice:
    """Speaks Arabic with the open Piper voice ar_JO-kareem, in its own process."""

    VOICE = "ar_JO-kareem-medium"

    def __init__(self, config: dict | None = None) -> None:
        self.config = dict(config or {})
        self._tts = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        from app.voice.tts import VOICE_ROOT

        root = Path(self.config.get("voice_root", VOICE_ROOT))
        return (root / f"{self.VOICE}.onnx").is_file()

    def _provider(self):
        with self._lock:
            if self._tts is None:
                from app.voice.tts import IsolatedPiperTTS

                self._tts = IsolatedPiperTTS({**self.config, "voice": self.VOICE, "speed": 0.9})
            return self._tts

    def say(self, text: str) -> None:
        self.speak(text)

    def speak(self, text: str, wait: bool = False) -> None:
        # Tests inject their own voice; the real one never plays during a run.
        if not text or not self.available or os.environ.get("PYTEST_CURRENT_TEST"):
            return
        self._provider().speak(text, wait=wait)

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
                log_swallowed(_log, "closing the Arabic voice")


class ArabicListener:
    """Records one answer on request and transcribes it as Arabic."""

    MODEL = "small"

    def __init__(self, microphone: dict | None = None, *, privacy: Callable[[], bool] = lambda: False,
                 model: str | None = None) -> None:
        self.microphone = dict(microphone or {})
        self.privacy = privacy
        self.model = model or self.MODEL
        self._stt = None
        self._checked: tuple[float, bool] = (0.0, False)

    def _model_present(self) -> bool:
        from app.config import PROJECT_ROOT

        root = PROJECT_ROOT / "data" / "models" / "whisper" / f"models--Systran--faster-whisper-{self.model}"
        return root.is_dir()

    @property
    def available(self) -> bool:
        if self.privacy() or os.environ.get("PYTEST_CURRENT_TEST"):
            return False
        at, known = self._checked
        if time.monotonic() - at < 60:
            return known
        try:
            import sounddevice as sd

            known = self._model_present() and any(int(d.get("max_input_channels", 0)) > 0 for d in sd.query_devices())
        except Exception:
            log_swallowed(_log, "checking for a microphone")
            known = False
        self._checked = (time.monotonic(), known)
        return known

    def ready(self) -> bool:
        """The multilingual model is on disk (no microphone check)."""
        return self._model_present() and not os.environ.get("PYTEST_CURRENT_TEST")

    def _recognizer(self):
        if self._stt is None:
            from app.voice.stt import FasterWhisperSTT

            self._stt = FasterWhisperSTT({"model": self.model, "language": "ar", "initial_prompt": "",
                                          "hotwords": "", "multi_decode": {"enabled": False}, "beam_size": 5})
        return self._stt

    def transcribe(self, audio, cancel=None, *, language: str | None = "ar", hotwords: str = "", prompt: str = ""):
        """Recognise recorded audio with the multilingual model.

        ``language=None`` detects the language, so in Arabic mode an English
        "English please" is still heard as English. ``hotwords`` and ``prompt``
        carry the English recogniser's hints (HAYAT's name) when it stands in for it.
        """
        recognizer = self._recognizer()
        recognizer.language = language or "auto"
        recognizer.hotwords = hotwords
        recognizer.initial_prompt = prompt
        return recognizer.transcribe(audio, cancel)

    def listen(self) -> tuple[str, float]:
        """Record until a pause, then transcribe. Returns (text, seconds of audio)."""
        if self.privacy():
            raise PermissionError("Privacy mode is on, so the microphone stays off.")
        from app.voice.microphone import MicrophoneRecorder

        audio = MicrophoneRecorder(self.microphone).record_until_silence(speech_start_timeout=6.0, silence_seconds=1.0)
        result = self.transcribe(audio, language="ar")
        return result.text, float(getattr(audio, "duration_seconds", 0.0))


class LanguageTutor:
    def __init__(self, path: Path, *, voice: Any = None, listener: Any = None,
                 clock: Callable[[], float] = time.time, rng: random.Random | None = None) -> None:
        self.path = Path(path)
        self.voice = voice
        self.listener = listener
        self.clock = clock
        self.rng = rng or random.Random()
        self._listeners: list[Callable[[str, Step], None]] = []
        self._lock = threading.RLock()
        self._initialize()

    # ------------------------------------------------------------ plumbing
    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS language_progress(course TEXT NOT NULL, item_id TEXT NOT NULL, skill TEXT NOT NULL,
                    box INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
                    incorrect INTEGER NOT NULL DEFAULT 0, last_seen REAL, PRIMARY KEY(course, item_id, skill));
                CREATE TABLE IF NOT EXISTS language_state(course TEXT PRIMARY KEY, unit_index INTEGER NOT NULL DEFAULT 0,
                    current_json TEXT, last_key TEXT, updated_at REAL);
                CREATE INDEX IF NOT EXISTS idx_language_due ON language_progress(course, due);
                """
            )

    def add_listener(self, callback: Callable[[str, Step], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def _notify(self, course: str, step: Step) -> None:
        for callback in list(self._listeners):
            try:
                callback(course, step)
            except Exception:
                log_swallowed(_log, "notifying a lesson observer")

    def channels(self) -> dict[str, bool]:
        listen = bool(self.voice is not None and getattr(self.voice, "available", False))
        speak = bool(self.listener is not None and getattr(self.listener, "available", False))
        return {"listen": listen, "speak": speak}

    def skills_for(self, item: Item, channels: dict[str, bool] | None = None) -> list[str]:
        channels = self.channels() if channels is None else channels
        skills = ["reading"]
        if item.kind != "mark":
            skills.append("writing")
        if channels.get("listen"):
            skills.append("listening")
        if channels.get("speak"):
            skills.append("speaking")
        return skills

    def _course(self, course: str):
        if course not in COURSES:
            raise KeyError(f"No built-in course for {course!r}.")
        return COURSES[course]

    def _items(self, course: str) -> dict[str, Item]:
        return self._course(course).all_items()

    def _state(self, course: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM language_state WHERE course=?", (course,)).fetchone()
            if row is None:
                db.execute("INSERT INTO language_state(course, unit_index, updated_at) VALUES(?,0,?)", (course, self.clock()))
                return {"unit_index": 0, "current": None, "last_key": None}
        return {"unit_index": int(row["unit_index"]), "last_key": row["last_key"],
                "current": json.loads(row["current_json"]) if row["current_json"] else None}

    def _save_state(self, course: str, **fields: Any) -> None:
        state = self._state(course)
        state.update(fields)
        with self._connect() as db:
            db.execute("UPDATE language_state SET unit_index=?, current_json=?, last_key=?, updated_at=? WHERE course=?",
                       (state["unit_index"], json.dumps(state["current"], ensure_ascii=False) if state["current"] else None,
                        state["last_key"], self.clock(), course))

    def _progress_rows(self, course: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute("SELECT * FROM language_progress WHERE course=?", (course,)).fetchall()

    # ------------------------------------------------------------ public API
    def current(self, course: str) -> dict[str, Any] | None:
        return self._state(course)["current"]

    def start(self, course: str) -> Step:
        """Resume the open exercise, or move the course forward."""
        with self._lock:
            state = self._state(course)
            units = self._course(course).UNITS
            if state["current"]:
                step = Step(f"Welcome back to {self._course(course).COURSE['title']}. Here is where we stopped.\n\n"
                            + self._render(state["current"]), state["current"], course=course)
                self._speak_for(state["current"])
            else:
                unit = units[min(state["unit_index"], len(units) - 1)]
                opening = (f"Let's learn {self._course(course).COURSE['title']}: reading, writing, listening and speaking.\n"
                           f"Unit {state['unit_index'] + 1} of {len(units)}: {unit.title}. {unit.intro}")
                step = self._advance(course, opening)
            step.text += self._footer()
            self._notify(course, step)
            return step

    def answer(self, course: str, text: str) -> Step | None:
        """Grade a reply to the open exercise. None when the reply is not an answer."""
        with self._lock:
            value = " ".join((text or "").split())
            command = _COMMANDS.get(value.lower().strip(" .!?"))
            state = self._state(course)
            exercise = state["current"]
            if command == "stop":
                step = Step("Lesson paused. Your progress is saved -- say \"continue Arabic\" to pick it up.",
                            None, finished=True, course=course)
                self._notify(course, step)
                return step
            if command == "progress" or (not has_arabic(value) and re.search(r"(?i)\bprogress\b|how am i doing", value)):
                return Step(self.progress_text(course), exercise, course=course)
            if re.match(r"(?i)^(?:teach me|continue|resume|start|begin|learn)\b", value) and not has_arabic(value):
                from app.learning.courses import course_for

                if course_for(value) == course or value.lower().strip(" .!") in {"continue", "continue lesson", "resume"}:
                    if exercise is None:
                        return self.start(course)
                    return Step("We're in the middle of an exercise:\n\n" + self._render(exercise), exercise, course=course)
            if exercise is None:
                return self.start(course)
            if command == "repeat":
                self._speak_for(exercise, force=True)
                return Step("Listen again.\n\n" + self._render(exercise), exercise, course=course)
            if command == "hint":
                exercise["hints"] = int(exercise.get("hints", 0)) + 1
                self._save_state(course, current=exercise)
                return Step(self._hint(course, exercise), exercise, course=course)
            if command == "record":
                return self.record_answer(course)
            if command == "skip":
                verdict = Verdict(False, False, 0.0, exercise["expected"])
                return self._conclude(course, exercise, verdict, skipped=True)
            if not self._is_answer(exercise, value):
                return None
            if exercise["skill"] == "writing" and not has_arabic(value):
                return Step("Write it in Arabic letters. In the Learn window use the on-screen keyboard, or switch "
                            "Windows to an Arabic keyboard (Win+Space).\n\n" + self._render(exercise), exercise, course=course)
            if exercise["skill"] == "speaking" and not has_arabic(value):
                return Step("This one is spoken: press Speak in the Learn window or type \"record\", then say it. "
                            "(Typing it in Arabic also counts, or say \"skip\".)\n\n" + self._render(exercise),
                            exercise, course=course)
            verdict = self.grade(course, exercise, value)
            return self._conclude(course, exercise, verdict)

    def record_answer(self, course: str) -> Step:
        """Speaking: record one answer from the microphone and grade it."""
        exercise = self.current(course)
        if exercise is None:
            return self.start(course)
        if exercise["skill"] != "speaking":
            return Step("This exercise isn't a speaking one -- type your answer.\n\n" + self._render(exercise), exercise, course=course)
        if self.listener is None or not getattr(self.listener, "available", False):
            return Step("I can't listen right now: " + self._listening_gap() + " Type \"skip\" to move on.", exercise, course=course)
        try:
            heard, _seconds = self.listener.listen()
        except PermissionError as exc:
            return Step(f"{exc} Type \"skip\" to move on.", exercise, course=course)
        except Exception as exc:
            log_swallowed(_log, "recording a spoken answer", level=logging.WARNING)
            return Step(f"Recording failed ({type(exc).__name__}). Try again, or type \"skip\".", exercise, course=course)
        if not heard.strip():
            return Step("I didn't hear anything. Press Speak (or type \"record\") and say it clearly, close to the microphone.",
                        exercise, course=course)
        with self._lock:
            verdict = self.grade(course, exercise, heard)
            return self._conclude(course, exercise, verdict)

    def replay(self, course: str) -> None:
        exercise = self.current(course)
        if exercise:
            self._speak_for(exercise, force=True)

    def say(self, text: str) -> bool:
        if self.voice is None or not getattr(self.voice, "available", False):
            return False
        try:
            self.voice.say(text)
            return True
        except Exception:
            log_swallowed(_log, "speaking Arabic", level=logging.WARNING)
            return False

    # ------------------------------------------------------------ grading
    def grade(self, course: str, exercise: dict[str, Any], answer: str) -> Verdict:
        item = self._items(course)[exercise["item_id"]]
        skill = exercise["skill"]
        if skill in {"writing", "speaking"}:
            heard = normalize_arabic(answer)
            targets = [normalize_arabic(item.arabic)]
            if item.kind == "letter":
                targets += [normalize_arabic(item.spoken)]
                if skill == "writing":           # any positional shape of the right letter
                    targets += [normalize_arabic(form) for form in letter_forms(item).values()]
            best = max((_similarity(heard, target) for target in targets if target), default=0.0)
            if heard and heard in targets:
                best = 100.0
            strict, near = (88.0, 60.0) if skill == "speaking" else (100.0, 75.0)
            return Verdict(best >= strict, strict > best >= near, best, item.arabic, answer.strip())
        # Reading and listening: the meaning, the sound, or (listening) the Arabic itself.
        if has_arabic(answer):
            given = normalize_arabic(answer)
            targets = [normalize_arabic(item.arabic)] + ([normalize_arabic(item.spoken)] if item.spoken else [])
            score = 100.0 if given in targets else max(_similarity(given, target) for target in targets)
        else:
            given = normalize_latin(answer)
            candidates = [normalize_latin(value) for value in (*item.meanings, *item.transliteration)]
            score = 0.0
            for candidate in candidates:
                if not candidate:
                    continue
                if given == candidate or _squash(given) == _squash(candidate):
                    score = 100.0
                    break
                score = max(score, _similarity(given, candidate), _similarity(_squash(given), _squash(candidate)))
            if item.kind == "letter" and len(given) <= 3:
                # A single sound is right or wrong; "b" is not "close" to "t".
                score = 100.0 if any(_squash(given) == _squash(c) for c in candidates) else 0.0
        return Verdict(score >= 88.0, 88.0 > score >= 70.0, score, self._expected_text(item, skill), answer.strip())

    def _is_answer(self, exercise: dict[str, Any], value: str) -> bool:
        """False for a message that is plainly a new request rather than an answer."""
        if not value:
            return False
        if has_arabic(value):
            return True
        if _NEW_REQUEST.match(value):
            return False
        words = value.split()
        if len(words) > 8:
            return False
        if len(words) >= 4 and value.rstrip().endswith("?"):
            expected = normalize_latin(exercise.get("expected_latin") or "")
            return bool(expected) and _similarity(normalize_latin(value), expected) >= 50
        return True

    # ------------------------------------------------------------ lesson flow
    def _conclude(self, course: str, exercise: dict[str, Any], verdict: Verdict, *, skipped: bool = False) -> Step:
        item = self._items(course)[exercise["item_id"]]
        self._record(course, item.item_id, exercise["skill"], verdict, hints=int(exercise.get("hints", 0)))
        if skipped:
            feedback = f"The answer: {self._reveal(item)}"
        elif verdict.correct:
            feedback = self.rng.choice(("Correct!", "Right!", "Well done.", "Yes!", "Exactly.")) + f"  {self._reveal(item)}"
        elif verdict.close:
            feedback = f"Close. {self._difference(verdict)} The answer: {self._reveal(item)}"
        else:
            heard = f" I heard: {verdict.heard}." if exercise["skill"] == "speaking" and verdict.heard else ""
            feedback = f"Not quite.{heard} The answer: {self._reveal(item)}"
        if item.note and not verdict.correct:
            feedback += f"\n{item.note}"
        step = self._advance(course, feedback)
        step.verdict = verdict.to_dict()
        self._notify(course, step)
        return step

    def _advance(self, course: str, lead: str) -> Step:
        """Open the next exercise: something due, else new items, else the next unit."""
        state = self._state(course)
        units = self._course(course).UNITS
        items = self._items(course)
        channels = self.channels()
        rows = {(row["item_id"], row["skill"]): row for row in self._progress_rows(course)}
        introduced = {item_id for item_id, _skill in rows}
        now = self.clock()
        due = [row for row in rows.values() if row["due"] <= now and row["item_id"] in items
               and row["skill"] in self.skills_for(items[row["item_id"]], channels)]
        introduced_now: list[Item] = []
        if not due:
            unit_index = state["unit_index"]
            while unit_index < len(units):
                fresh = [item for item in units[unit_index].items if item.item_id not in introduced]
                if fresh:
                    introduced_now = fresh[:NEW_PER_STEP.get(fresh[0].kind, 3)]
                    break
                if all(rows.get((item.item_id, "reading")) is not None and rows[(item.item_id, "reading")]["box"] >= 2
                       for item in units[unit_index].items):
                    unit_index += 1
                    if unit_index < len(units):
                        nxt = units[unit_index]
                        lead += f"\n\nUnit complete! Unit {unit_index + 1} of {len(units)}: {nxt.title}. {nxt.intro}"
                    continue
                break
            if unit_index != state["unit_index"]:
                self._save_state(course, unit_index=unit_index)
            if introduced_now:
                with self._connect() as db:
                    for item in introduced_now:
                        for skill in SKILLS:
                            db.execute("INSERT OR IGNORE INTO language_progress(course,item_id,skill,box,due) VALUES(?,?,?,0,?)",
                                       (course, item.item_id, skill, now))
                rows = {(row["item_id"], row["skill"]): row for row in self._progress_rows(course)}
                due = [rows[(item.item_id, "reading")] for item in introduced_now]
            elif unit_index >= len(units):
                upcoming = [row for row in rows.values() if row["skill"] in self.skills_for(items[row["item_id"]], channels)]
                if not upcoming:
                    return Step(lead + "\n\nYou have finished the course.", None, finished=True, course=course)
                due = [min(upcoming, key=lambda row: row["due"])]
                lead += "\n\nYou have finished every unit. From here it is review, as each item falls due."
            else:
                active = [row for row in rows.values() if row["item_id"] in {i.item_id for i in units[unit_index].items}
                          and row["skill"] in self.skills_for(items[row["item_id"]], channels)]
                due = [min(active, key=lambda row: (row["box"], row["due"]))] if active else []
        if not due:
            return Step(lead + "\n\nNothing to practise right now.", None, finished=True, course=course)
        # Interleave: prefer a different item from the last exercise, so one
        # item is not drilled through all four skills back to back.
        last_item = (state.get("last_key") or ":").split(":", 1)[0]
        due.sort(key=lambda row: (row["box"], row["due"]))
        choice = next((row for row in due if row["item_id"] != last_item), due[0])
        item = items[choice["item_id"]]
        exercise = self._exercise(item, choice["skill"], int(choice["box"]))
        self._save_state(course, current=exercise, last_key=f"{item.item_id}:{choice['skill']}")
        intro_text = ""
        if introduced_now:
            intro_text = "\n\nNew:\n" + "\n".join(self._introduce(item) for item in introduced_now)
            if self.voice is not None and channels["listen"]:
                self.say("، ".join(item.spoken or item.arabic for item in introduced_now))
        else:
            self._speak_for(exercise)
        text = f"{lead}{intro_text}\n\n{self._render(exercise)}"
        return Step(text.strip(), exercise, introduced=[self._card(item) for item in introduced_now], course=course)

    def _exercise(self, item: Item, skill: str, box: int) -> dict[str, Any]:
        display, prompt, audio = item.arabic, "", ""
        translit = item.transliteration[0]
        if item.kind == "letter":
            forms = letter_forms(item)
            position = "isolated" if box < 2 else self.rng.choice(list(forms))
            if skill == "reading":
                display = forms[position]
                where = "" if position == "isolated" else f" (its {position} form)"
                prompt = f"Which letter is this{where}? Type its sound or its name."
            elif skill == "writing":
                display = ""
                prompt = f"Write the letter for the sound '{translit}' ({item.meanings[0]})."
            elif skill == "listening":
                display, audio = "🔊", item.spoken
                prompt = "Listen: which letter's name did you hear? Type the letter, its sound or its name."
            else:
                prompt = f"Say the name of this letter aloud ({translit})."
        elif skill == "reading":
            prompt = ("How is this read? Type it in Latin letters." if item.kind == "mark"
                      else "What does this mean? (Typing how it sounds also counts.)")
        elif skill == "writing":
            display = ""
            prompt = f"Write in Arabic: \"{item.meanings[0]}\" ({translit}). Vowel marks are optional."
        elif skill == "listening":
            display, audio = "🔊", item.arabic
            prompt = "Listen, then type what it means (or how it sounds)."
        else:
            prompt = f"Say it aloud: {item.arabic} ({translit})."
        if skill == "speaking":
            prompt += " Press Speak in the Learn window, or type \"record\"."
        return {"item_id": item.item_id, "skill": skill, "kind": item.kind, "display": display, "prompt": prompt,
                "audio": audio, "expected": item.arabic, "expected_latin": item.meanings[0], "hints": 0,
                "rtl": skill in {"writing", "speaking"}}

    def _speak_for(self, exercise: dict[str, Any], *, force: bool = False) -> None:
        audio = exercise.get("audio") or (exercise.get("expected") if force else "")
        if audio:
            self.say(audio)

    # ------------------------------------------------------------ text
    def _render(self, exercise: dict[str, Any]) -> str:
        label = {"reading": "Reading", "writing": "Writing", "listening": "Listening", "speaking": "Speaking"}[exercise["skill"]]
        shown = f"\n    {exercise['display']}" if exercise["display"] and exercise["display"] != "🔊" else ""
        heard = "\n    (playing the audio -- type \"repeat\" to hear it again)" if exercise["skill"] == "listening" else ""
        return f"{label}: {exercise['prompt']}{shown}{heard}"

    @staticmethod
    def _introduce(item: Item) -> str:
        sounds = " / ".join(item.transliteration[:2])
        if item.kind == "letter":
            forms = letter_forms(item)
            return (f"  {item.arabic}  {item.meanings[0]} -- sounds like '{item.transliteration[0]}'. "
                    f"Forms: {forms['isolated']}  {forms['initial']}  {forms['medial']}  {forms['final']}. {item.note}")
        extra = f" {item.note}" if item.note else ""
        return f"  {item.arabic}  ({sounds}) = {item.meanings[0]}.{extra}"

    @staticmethod
    def _card(item: Item) -> dict[str, Any]:
        return {"item_id": item.item_id, "kind": item.kind, "arabic": item.arabic, "transliteration": item.transliteration[0],
                "meaning": item.meanings[0], "note": item.note,
                "forms": letter_forms(item) if item.kind == "letter" else None}

    @staticmethod
    def _reveal(item: Item) -> str:
        if item.kind == "letter":
            return f"{item.arabic} is {item.meanings[0]}, the sound '{item.transliteration[0]}'."
        return f"{item.arabic} ({item.transliteration[0]}) = {item.meanings[0]}."

    @staticmethod
    def _expected_text(item: Item, skill: str) -> str:
        return item.meanings[0] if skill in {"reading", "listening"} else item.arabic

    @staticmethod
    def _difference(verdict: Verdict) -> str:
        return f"You wrote {verdict.heard!r}." if verdict.heard else ""

    def _hint(self, course: str, exercise: dict[str, Any]) -> str:
        item = self._items(course)[exercise["item_id"]]
        level = int(exercise.get("hints", 1))
        if exercise["skill"] in {"reading", "listening"}:
            if level == 1:
                return f"Hint: it sounds like '{item.transliteration[0]}'."
            return f"Hint: it starts with '{item.meanings[0][:2]}…'."
        if level == 1:
            return f"Hint: it starts with {normalize_arabic(item.arabic)[:1]}  ({item.transliteration[0]})."
        return f"Hint: {item.arabic}"

    def _listening_gap(self) -> str:
        if self.listener is None:
            return "speech input isn't set up."
        if getattr(self.listener, "privacy", lambda: False)():
            return "privacy mode is on, so the microphone stays off."
        return "the Arabic speech model or a microphone is missing; run: python -m scripts.setup_arabic"

    def _footer(self) -> str:
        channels = self.channels()
        missing = []
        if not channels["listen"]:
            missing.append("listening -- the Arabic voice isn't installed; run: python -m scripts.setup_arabic")
        if not channels["speak"]:
            missing.append("speaking -- " + self._listening_gap())
        tips = "\n\nSay \"hint\", \"skip\", \"repeat\" or \"stop lesson\" at any time."
        return tips + "".join(f"\nNot available yet: {item}" for item in missing)

    # ------------------------------------------------------------ progress
    def _record(self, course: str, item_id: str, skill: str, verdict: Verdict, *, hints: int = 0) -> None:
        now = self.clock()
        with self._connect() as db:
            row = db.execute("SELECT box FROM language_progress WHERE course=? AND item_id=? AND skill=?",
                             (course, item_id, skill)).fetchone()
            box = int(row["box"]) if row else 0
            if verdict.correct:
                box = min(MAX_BOX, box + (1 if hints else 2 if box == 0 else 1))
            elif not verdict.close:
                box = 1
            db.execute(
                "INSERT INTO language_progress(course,item_id,skill,box,due,correct,incorrect,last_seen) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(course,item_id,skill) DO UPDATE SET box=excluded.box, due=excluded.due, "
                "correct=correct+excluded.correct, incorrect=incorrect+excluded.incorrect, last_seen=excluded.last_seen",
                (course, item_id, skill, box, now + INTERVALS[box], int(verdict.correct), int(not verdict.correct), now))

    def progress(self, course: str) -> dict[str, Any]:
        units = self._course(course).UNITS
        items = self._items(course)
        rows = self._progress_rows(course)
        state = self._state(course)
        skills = {}
        for skill in SKILLS:
            mine = [row for row in rows if row["skill"] == skill]
            attempts = sum(row["correct"] + row["incorrect"] for row in mine)
            correct = sum(row["correct"] for row in mine)
            learned = sum(1 for row in mine if row["box"] >= 3)
            skills[skill] = {"attempts": attempts, "accuracy": round(correct / attempts, 3) if attempts else None,
                             "learned": learned, "strength": round(sum(min(row["box"], MAX_BOX) for row in mine) / (MAX_BOX * len(items)), 3)}
        introduced = {row["item_id"] for row in rows}
        now = self.clock()
        channels = self.channels()
        due = sum(1 for row in rows if row["due"] <= now and row["item_id"] in items
                  and row["skill"] in self.skills_for(items[row["item_id"]], channels))
        return {"course": course, "title": self._course(course).COURSE["title"], "unit": min(state["unit_index"], len(units) - 1) + 1,
                "units": len(units), "unit_title": units[min(state["unit_index"], len(units) - 1)].title,
                "items": len(items), "introduced": len(introduced),
                "due": due, "skills": skills, "channels": channels}

    def progress_text(self, course: str) -> str:
        data = self.progress(course)
        lines = [f"{data['title']}: unit {data['unit']} of {data['units']} ({data['unit_title']}); "
                 f"{data['introduced']} of {data['items']} items met, {data['due']} due for practice."]
        for skill, row in data["skills"].items():
            accuracy = "no answers yet" if row["accuracy"] is None else f"{row['accuracy']:.0%} right over {row['attempts']} answers"
            lines.append(f"  {skill.title():<10} {row['learned']} learned for the long term · {accuracy}")
        return "\n".join(lines)
