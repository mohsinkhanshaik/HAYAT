"""Learning Arabic: the course, the tutor, the routing and the full turn."""
import random
import re

import pytest

from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.learning.courses import COURSES, course_for
from app.learning.courses.arabic import LETTERS, UNITS, all_items, letter_forms
from app.learning.language import (
    LanguageTutor, is_lesson_reply, looks_like_new_request, normalize_arabic, normalize_latin,
)
from app.learning.manager import LearningManager
from app.session import SessionState

ARABIC = re.compile("[؀-ۿ]")


class FakeVoice:
    available = True

    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)


class FakeListener:
    available = True

    def __init__(self, heard=""):
        self.heard = heard
        self.privacy = lambda: False

    def listen(self):
        return self.heard, 1.5


def tutor(tmp_path, *, voice=None, listener=None, clock=None):
    return LanguageTutor(tmp_path / "learning.db", voice=voice, listener=listener,
                         clock=clock or (lambda: 1_000.0), rng=random.Random(3))


def right_answer(item, skill):
    return {"reading": item.transliteration[0], "listening": item.meanings[0],
            "writing": item.arabic, "speaking": item.arabic}[skill]


# ------------------------------------------------------------------ content
def test_the_course_is_complete_and_well_formed():
    items = all_items()
    assert len(LETTERS) == 28 and len({letter.arabic for letter in LETTERS}) == 28
    assert len(items) == sum(len(unit.items) for unit in UNITS)       # ids are unique
    for item in items.values():
        assert ARABIC.search(item.arabic), item.item_id
        assert item.transliteration and item.meanings, item.item_id
    assert {unit.unit_id for unit in UNITS} >= {"letters-1", "marks", "greetings", "numbers", "sentences"}
    assert "arabic" in COURSES


def test_letter_forms_respect_letters_that_do_not_join():
    baa = next(letter for letter in LETTERS if letter.arabic == "ب")
    daal = next(letter for letter in LETTERS if letter.arabic == "د")
    assert letter_forms(baa) == {"isolated": "ب", "initial": "بـ", "medial": "ـبـ", "final": "ـب"}
    assert letter_forms(daal)["initial"] == "د" and letter_forms(daal)["medial"] == "ـد"


def test_arabic_and_latin_are_compared_the_way_a_teacher_would():
    assert normalize_arabic("السَّلَامُ عَلَيْكُمْ") == "السلام عليكم"
    assert normalize_arabic("أُمٌّ") == normalize_arabic("ام")
    assert normalize_arabic("مَدْرَسَةٌ") == normalize_arabic("مدرسه")
    assert normalize_latin("I'm fine!") == "i am fine"
    assert normalize_latin("him") == "him"
    assert normalize_latin("Na'am") == "naam"


def test_a_course_is_found_by_any_of_its_names():
    assert course_for("teach me arabic") == "arabic"
    assert course_for("the Arabic alphabet") == "arabic"
    assert course_for("العربية") == "arabic"
    assert course_for("teach me kubernetes") is None


# ------------------------------------------------------------------ the tutor
def test_starting_introduces_letters_and_opens_an_exercise(tmp_path):
    voice = FakeVoice()
    step = tutor(tmp_path, voice=voice).start("arabic")
    assert "Unit 1 of 16" in step.text and "ب" in step.text and "baa" in step.text
    assert step.exercise is not None and step.exercise["skill"] == "reading"
    assert len(step.introduced) == 4
    assert voice.said, "new letters are spoken when the Arabic voice is installed"


def test_right_answers_move_forward_and_a_wrong_one_comes_back(tmp_path):
    clock = [1_000.0]
    teacher = tutor(tmp_path, voice=FakeVoice(), clock=lambda: clock[0])
    teacher.start("arabic")
    items = all_items()
    exercise = teacher.current("arabic")
    missed = exercise["item_id"]
    step = teacher.answer("arabic", "zzzz")
    assert step.verdict["correct"] is False and "Not quite" in step.text
    for _ in range(12):
        exercise = teacher.current("arabic")
        step = teacher.answer("arabic", right_answer(items[exercise["item_id"]], exercise["skill"]))
        assert step.verdict["correct"], (exercise, step.text)
        clock[0] += 30
    # The missed item is due again inside the session.
    seen_again = [row for row in teacher._progress_rows("arabic")
                  if row["item_id"] == missed and row["incorrect"] > 0]
    assert seen_again and seen_again[0]["box"] >= 1


def test_writing_must_be_in_arabic_and_vowel_marks_are_optional(tmp_path):
    teacher = tutor(tmp_path)
    teacher.start("arabic")
    thanks = all_items()["thanks"]
    exercise = teacher._exercise(thanks, "writing", 1)
    assert teacher.grade("arabic", exercise, "شكرا").correct
    assert teacher.grade("arabic", exercise, "شُكْرًا").correct
    assert teacher.grade("arabic", exercise, "شكر").close
    teacher._save_state("arabic", current=exercise)
    step = teacher.answer("arabic", "shukran")
    assert "Arabic letters" in step.text and step.verdict is None      # not graded, not counted


def test_reading_accepts_the_meaning_or_the_sound(tmp_path):
    teacher = tutor(tmp_path)
    item = all_items()["how-are-you"]
    exercise = teacher._exercise(item, "reading", 1)
    assert teacher.grade("arabic", exercise, "How are you?").correct
    assert teacher.grade("arabic", exercise, "kayfa haluka").correct
    assert not teacher.grade("arabic", exercise, "good night").correct
    letter = next(letter for letter in LETTERS if letter.arabic == "ب")
    assert teacher.grade("arabic", teacher._exercise(letter, "reading", 0), "b").correct
    assert not teacher.grade("arabic", teacher._exercise(letter, "reading", 0), "t").correct


def test_listening_needs_the_voice_and_speaking_needs_the_listener(tmp_path):
    item = all_items()["thanks"]
    assert tutor(tmp_path).skills_for(item) == ["reading", "writing"]
    both = tutor(tmp_path, voice=FakeVoice(), listener=FakeListener())
    assert both.skills_for(item) == ["reading", "writing", "listening", "speaking"]


def test_a_spoken_answer_is_transcribed_and_graded(tmp_path):
    listener = FakeListener("شكرا")
    teacher = tutor(tmp_path, voice=FakeVoice(), listener=listener)
    teacher.start("arabic")
    teacher._save_state("arabic", current=teacher._exercise(all_items()["thanks"], "speaking", 1))
    step = teacher.record_answer("arabic")
    assert step.verdict["correct"] and step.verdict["heard"] == "شكرا"
    listener.heard = "مرحبا"
    teacher._save_state("arabic", current=teacher._exercise(all_items()["thanks"], "speaking", 1))
    step = teacher.record_answer("arabic")
    assert not step.verdict["correct"] and "I heard: مرحبا" in step.text


def test_commands_hint_skip_repeat_and_stop(tmp_path):
    voice = FakeVoice()
    teacher = tutor(tmp_path, voice=voice)
    teacher.start("arabic")
    assert teacher.answer("arabic", "hint").text.startswith("Hint")
    voice.said.clear()
    teacher.answer("arabic", "repeat")
    assert voice.said
    skipped = teacher.answer("arabic", "skip")
    assert "The answer" in skipped.text and skipped.verdict["correct"] is False
    stopped = teacher.answer("arabic", "stop lesson")
    assert stopped.finished and "saved" in stopped.text


def test_a_lesson_resumes_after_a_restart(tmp_path):
    first = tutor(tmp_path)
    first.start("arabic")
    open_exercise = first.current("arabic")
    second = tutor(tmp_path)
    step = second.start("arabic")
    assert "Welcome back" in step.text and second.current("arabic") == open_exercise


def test_a_new_request_is_not_mistaken_for_an_answer(tmp_path):
    teacher = tutor(tmp_path)
    teacher.start("arabic")
    assert teacher.answer("arabic", "open notepad") is None
    assert teacher.answer("arabic", "what is the weather in dubai tomorrow?") is None
    assert looks_like_new_request("open notepad")
    assert is_lesson_reply("shukran", {"type": "language"})
    assert is_lesson_reply("مرحبا بك", {"type": "language"})
    assert not is_lesson_reply("please research the history of the arabic language in great depth for me",
                               {"type": "language"})


def test_progress_reports_every_skill(tmp_path):
    teacher = tutor(tmp_path, voice=FakeVoice())
    teacher.start("arabic")
    exercise = teacher.current("arabic")
    teacher.answer("arabic", right_answer(all_items()[exercise["item_id"]], exercise["skill"]))
    data = teacher.progress("arabic")
    assert data["introduced"] == 4 and data["skills"]["reading"]["attempts"] == 1
    assert "Reading" in teacher.progress_text("arabic") and "Speaking" in teacher.progress_text("arabic")


# ------------------------------------------------------------------ the manager
def manager(tmp_path, **kwargs):
    learning = LearningManager(tmp_path / "learning.db")
    learning.language = tutor(tmp_path, **kwargs)
    return learning


def test_teach_me_arabic_opens_a_lesson_that_takes_answers(tmp_path):
    learning, session = manager(tmp_path, voice=FakeVoice()), SessionState()
    first = learning.handle("teach me arabic", session)
    assert "Unit 1" in first and session.pending_learning["type"] == "language"
    assert session.pending_learning["course"] == "arabic" and "at" in session.pending_learning
    exercise = learning.language.current("arabic")
    reply = learning.handle(right_answer(all_items()[exercise["item_id"]], exercise["skill"]), session)
    assert any(word in reply for word in ("Correct", "Right", "Well done", "Yes", "Exactly"))
    assert learning.topic("Arabic")["correct_answers"] == 1
    assert "paused" in learning.handle("stop lesson", session) and session.pending_learning is None
    assert "Welcome back" in learning.handle("continue arabic", session)
    assert "Reading" in learning.handle("arabic progress", session)


def test_other_topics_are_taught_by_the_model_one_idea_at_a_time(tmp_path):
    learning, session = LearningManager(tmp_path / "learning.db"), SessionState()
    prompts = []

    def teacher(prompt):
        prompts.append(prompt)
        return "Containers package an app with its files.\nQuestion: what does a container include?"

    lesson = learning.handle("teach me docker", session, teacher=teacher)
    assert "Containers package" in lesson and session.pending_learning["type"] == "lesson"
    assert session.pending_learning["question"] == "what does a container include?"
    learning.handle("the app and its dependencies", session, teacher=teacher)
    assert "the app and its dependencies" in prompts[-1] and "what does a container include?" in prompts[-1]
    assert learning.handle("open notepad", session, teacher=teacher) is None      # routed elsewhere
    assert "paused" in learning.handle("stop lesson", session, teacher=teacher)


def test_without_a_model_the_reply_to_what_do_you_know_is_not_lost(tmp_path):
    learning, session = LearningManager(tmp_path / "learning.db"), SessionState()
    first = learning.handle("teach me kubernetes", session)
    assert "tell me what you already know" in first and session.pending_learning["type"] == "diagnostic"
    follow = learning.handle("I know pods and deployments", session)
    assert "where to start" in follow and session.pending_learning["type"] == "quiz"


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text", [
    "learn arabic", "explain learn arabic", "teach me arabic", "i want to learn arabic", "help me learn arabic",
    "start my arabic lesson", "arabic lesson", "teach me the arabic alphabet", "can you teach me arabic",
])
def test_every_way_of_asking_starts_the_arabic_course(text):
    intent = IntentRouter().route(text)
    assert intent.intent == IntentKind.LEARNING and intent.goal == "teach me arabic"


def test_continue_and_progress_route_to_the_course():
    router = IntentRouter()
    assert router.route("continue arabic").goal == "continue arabic"
    assert router.route("my arabic progress").goal == "arabic progress"
    assert router.route("continue the project").intent != IntentKind.LEARNING


def test_an_open_lesson_lets_other_requests_through():
    router, session = IntentRouter(), SessionState()
    session.pending_learning = {"type": "language", "course": "arabic"}
    assert router.route("shukran", session).intent == IntentKind.LEARNING
    assert router.route("open notepad", session).intent != IntentKind.LEARNING


# ------------------------------------------------------------------ the whole turn
def test_learn_arabic_through_the_assistant_teaches_instead_of_asking_for_a_topic():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        reply = runtime.assistant.handle("learn arabic")
        text = reply.response.display_text
        assert "Tell me a topic" not in text
        assert ("Unit" in text or "Welcome back" in text) and ":" in text   # a shared sandbox may hold an open lesson
        assert runtime.session.pending_learning["type"] == "language"
        answer = runtime.assistant.handle("a")
        assert "The answer" in answer.response.display_text or "Right" in answer.response.display_text \
            or "Correct" in answer.response.display_text or "Yes" in answer.response.display_text \
            or "Well done" in answer.response.display_text or "Exactly" in answer.response.display_text
    finally:
        runtime.close()


def test_talking_to_hayat_during_a_lesson_is_not_graded_as_an_answer():
    """Live 2026-10-07/08: "Right, how do you feel now?", "learn and understand" and "i dint ask u that" got "Not quite"."""
    from app.intent.router import IntentRouter
    from app.learning.language import is_lesson_reply
    from app.session import SessionState

    lesson = {"type": "lesson", "topic": "business", "question": "What can you buy for $1 and sell for $3?"}
    for text in ("Right, how do you feel now?", "learn and understand", "i dint ask u that", "where are we?",
                 "Is it because the cost is lower?", "tell me about your day"):
        assert not is_lesson_reply(text, lesson), text
    for text in ("pens, sold at $3 each", "because storage isn't enough", "explain again", "next", "hint"):
        assert is_lesson_reply(text, lesson), text
    state = SessionState()
    state.pending_learning = dict(lesson)
    IntentRouter(None).route("i dint ask u that", state)
    assert state.pending_learning is None  # an unwanted lesson is set aside, not left waiting


def test_bare_where_are_we_reads_the_location_unless_a_lesson_is_open():
    from app.intent.router import IntentRouter
    from app.session import SessionState

    routed = IntentRouter(None).route("where are we?", SessionState())
    assert routed.entities == {"modality": "device"} and routed.goal == "where are we located now"
    state = SessionState()
    state.pending_learning = {"type": "lesson", "topic": "x", "question": "q?"}
    assert IntentRouter(None).route("where are we?", state).entities != {"modality": "device"}


def test_vague_learn_phrases_do_not_start_a_lesson_on_filler_words():
    from app.intent.router import IntentRouter

    router = IntentRouter(None)
    for text in ("learn and understand", "learn it", "learn more", "teach me about stuff"):
        assert router.lesson_request(text) is None, text
    assert router.lesson_request("learn docker") == "teach me docker"
    assert router.lesson_request("learn c++") == "teach me c++"
