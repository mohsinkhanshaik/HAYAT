"""HAYAT answers to "Hayat" and "Jarvis", remembers wake mode on this computer, and says what happened while he was away."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.voice.wakeword import WakeWordDetector


def test_both_names_wake_it_with_or_without_a_greeting():
    wake = WakeWordDetector()
    assert wake.extract_command("Jarvis, what's on today?") == "what's on today"
    assert wake.extract_command("Hey Jarvis turn off the lights.") == "turn off the lights"
    assert wake.extract_command("Hayat, play some music") == "play some music"
    assert wake.extract_command("ok hayat what time is it") == "what time is it"
    assert wake.extract_command("Hyatt, open notepad") == "open notepad"  # how Whisper writes "Hayat"
    assert wake.extract_command("Jervis what's the weather") == "what's the weather"
    assert wake.extract_command("Jarvis?") == ""  # the name alone: answer, then listen for the request
    assert wake.extract_command("so anyway hey hayat remind me at five") == "remind me at five"
    assert wake.describe() == "Hayat or Jarvis"


def test_a_name_mentioned_in_passing_does_not_wake_it():
    wake = WakeWordDetector()
    assert wake.extract_command("I watched a film where jarvis runs the house") is None
    assert wake.extract_command("we stayed at the hyatt last week") is None
    assert wake.extract_command("ordinary conversation") is None
    assert wake.extract_command("") is None


def test_configured_phrases_and_other_names_still_work():
    wake = WakeWordDetector("hey ai", ["hey we are"])
    assert wake.extract_command("Hey AI, open Chrome") == "open chrome"
    assert wake.extract_command("Hey we are, open Chrome") == "open chrome"
    friday = WakeWordDetector(names=["friday"])
    assert friday.extract_command("Friday, lights on") == "lights on"
    assert friday.extract_command("Jarvis, lights on") is None


def test_wake_mode_and_names_are_remembered_on_this_computer(tmp_path):
    from app.voice.preferences import VoicePreferences

    prefs = VoicePreferences(tmp_path / "voice-preferences.json")
    assert prefs.load() == {}
    prefs.save(mode="wake_word")
    prefs.save(wake_names=["Jarvis", " Hayat "])
    assert prefs.load() == {"mode": "wake_word", "wake_names": ["jarvis", "hayat"]}
    (tmp_path / "voice-preferences.json").write_text('{"mode": "always_on", "wake_names": [3]}', encoding="utf-8")
    assert prefs.load() == {}  # nothing unexpected is trusted


def test_typed_messages_addressed_to_jarvis_are_read_without_the_name():
    from app.intent.normalize import normalize

    assert normalize("Jarvis, what is 15% of 240?").text.lower().startswith("what is 15% of 240")


def _runtime(jobs=(), failures=(), rules=()):
    return SimpleNamespace(research_jobs=SimpleNamespace(recent=lambda limit: list(jobs)),
                           failures=SimpleNamespace(recent=lambda limit: list(failures)),
                           event_triggers=SimpleNamespace(list=lambda: list(rules)))


def test_the_away_note_reports_only_what_the_records_hold():
    from app.away import note

    now = datetime(2026, 11, 12, 18, 0, tzinfo=UTC)
    since = now - timedelta(hours=5)
    finished = SimpleNamespace(status="done", asked="cheapest flights to Lisbon in May",
                               finished_at=(now - timedelta(hours=1)).isoformat(), query="")
    earlier = SimpleNamespace(status="done", asked="old question", finished_at=(since - timedelta(hours=1)).isoformat(),
                              query="")
    failure = {"timestamp": (now - timedelta(hours=2)).isoformat(), "user_request": "summarise the news"}
    rule = {"goal": "tell me when BTC drops below 60k", "last_run_at": (now - timedelta(minutes=30)).isoformat()}
    text = note(_runtime([finished, earlier], [failure], [rule]), since, now=now)
    assert text.startswith("While you were away (5 hours):")
    assert "1 search finished (“cheapest flights to Lisbon in May”)" in text and "old question" not in text
    assert "1 thing failed" in text and "summarise the news" in text
    assert "1 of your watch rules fired (“tell me when BTC drops below 60k”)" in text
    assert note(_runtime(), since, now=now) == ""  # nothing happened: nothing is said
    assert note(_runtime([finished]), now - timedelta(minutes=30), now=now) == ""  # a pause is not being away
    assert note(_runtime([finished]), None, now=now) == ""


def test_the_names_work_in_hindi_urdu_arabic_and_telugu_script_and_with_hinglish_greetings():
    wake = WakeWordDetector()
    assert wake.extract_command("हयात, आज मौसम कैसा है?") == "आज मौसम कैसा है"
    assert wake.extract_command("حیات، آج کیا خبر ہے") == "آج کیا خبر ہے"
    assert wake.extract_command("يا حياة ما الوقت الآن") == "ما الوقت الآن"
    assert wake.extract_command("హయాత్ ఈరోజు వాతావరణం ఎలా ఉంది") == "ఈరోజు వాతావరణం ఎలా ఉంది"
    assert wake.extract_command("suno hayat kya time hua") == "kya time hua"
    assert wake.extract_command("अरे जार्विस लाइट बंद करो") == "लाइट बंद करो"
    assert wake.extract_command("حیات؟") == ""


def test_calling_either_name_in_chat_is_answered_at_once_and_controls_reach_voice():
    from app.intent.router import IntentRouter

    router = IntentRouter()
    for called in ("jarvis?", "Hayat", "hey jarvis", "hey hayat", "हयात", "حیات؟"):
        routed = router.route(called)
        assert routed.entities.get("presence") == "summoned" and routed.response, called
    for command in ("listen for jarvis", "Listen for Hayat.", "turn on wake words"):
        assert router.route(command).control == "VOICE_WAKE", command
    assert router.route("Jarvis, what is 15% of 240?").goal.lower().startswith("what is 15% of 240")


def test_the_away_note_finds_his_last_message_in_an_earlier_session(tmp_path):
    from app.away import last_seen
    from app.memory.database import MemoryDatabase

    memory = MemoryDatabase(tmp_path / "agent.db")
    memory.initialize()
    memory.record_message("old-session", "user", "goodnight")
    assert last_seen(memory, "new-session", "hey jarvis") is not None
    assert last_seen(memory, "new-session", "goodnight") is None  # the message itself is not "before"
