"""Any language, a persona that talks like a person, and software installed only on a yes."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from app.intent.router import IntentRouter
from app.languages import LanguageSkills, detect, language_code, script_of
from app.persona import Persona
from app.session import SessionState
from app.software import SoftwareInstaller, parse_details, parse_table
from app.voice.tts import BilingualTTS, TextToSpeechProvider


# ------------------------------------------------------------------ detection
@pytest.mark.parametrize("text,code", [
    ("Hola, ¿cómo estás? Quiero aprender inglés", "es"), ("Ich möchte heute Abend ins Kino gehen", "de"),
    ("آپ کیسے ہیں؟ میں ٹھیک ہوں", "ur"), ("आप कैसे हैं? मैं ठीक हूँ", "hi"), ("నువ్వు ఎలా ఉన్నావు", "te"),
    ("こんにちは、元気ですか", "ja"), ("你好，你好吗", "zh"),
])
def test_other_languages_are_recognised(text, code):
    detection = detect(text)
    assert detection.code == code and detection.reliable


@pytest.mark.parametrize("text", ["hi hello", "ok", "Kya haal hai bhai, kaise ho?", "Can you open notepad for me"])
def test_short_or_romanised_english_letters_are_not_called_foreign(text):
    detection = detect(text)
    assert detection is None or detection.code == "en" or not detection.reliable


def test_language_names_and_scripts():
    assert language_code("Urdu") == "ur" and language_code("farsi") == "fa" and language_code("klingon") is None
    assert script_of("नमस्ते")[1][0] == "hi" and script_of("plain") is None


# ------------------------------------------------------------------ learning a language
def test_learning_a_language_downloads_its_voice_and_queues_study(tmp_path):
    queued = []
    learner = SimpleNamespace(enqueue=lambda topic, **kwargs: queued.append(topic) or True)
    skills = LanguageSkills(tmp_path / "learning", tmp_path / "voices", learner=lambda: learner)
    plan = skills.plan("ur")
    assert "ur_PK-aegis_female-medium" in plan["text"] and "64 MB" in plan["text"] and not plan["voice_installed"]

    def download(voice, root):
        (root / f"{voice}.onnx").write_bytes(b"model")

    report = skills.install("ur", download=download)
    assert report["voice_installed"] and len(report["study_queued"]) == 3 and "Urdu grammar" in queued[0]
    assert skills.installed() == ["ur"] and skills.speaker("ur").available
    assert skills.status()["installed"][0]["studied"]
    assert skills.note("es") == 1 and skills.note("es") == 2


# ------------------------------------------------------------------ software
TABLE = """   - \\ |
Name                Id                         Version Match
-------------------------------------------------------------------
VLC media player    VideoLAN.VLC               3.0.23  Moniker: vlc
Screenbox           Starpine.Screenbox         0.22.0  Tag: vlc
"""
SHOW = """Found VLC media player [VideoLAN.VLC]
Version: 3.0.23
Publisher: VideoLAN
Description:
  VLC is a free and open source multimedia player.
License: GPL-2.0
"""


def fake_winget(calls, install_output="Successfully installed", code=0):
    def run(arguments, **kwargs):
        calls.append(arguments)
        if arguments[1] == "search":
            return subprocess.CompletedProcess(arguments, 0, TABLE, "")
        if arguments[1] == "show":
            return subprocess.CompletedProcess(arguments, 0, SHOW, "")
        return subprocess.CompletedProcess(arguments, code, install_output, "")
    return run


def test_winget_output_is_parsed():
    rows = parse_table(TABLE)
    assert rows[0] == {"name": "VLC media player", "id": "VideoLAN.VLC", "version": "3.0.23", "match": "Moniker: vlc"}
    details = parse_details(SHOW)
    assert details["publisher"] == "VideoLAN" and details["license"] == "GPL-2.0" and "multimedia" in details["description"]


def test_software_is_explained_and_installed_only_after_a_yes(tmp_path, monkeypatch):
    calls = []
    clock = [1000.0]
    installer = SoftwareInstaller(run=fake_winget(calls), journal=tmp_path / "installs.jsonl", clock=lambda: clock[0])
    monkeypatch.setattr(installer, "available", lambda: True)
    plan = installer.plan("vlc")
    assert plan["id"] == "VideoLAN.VLC" and "VideoLAN" in plan["text"] and "GPL-2.0" in plan["text"]
    refused = installer.install("VideoLAN.VLC")
    assert not refused["ok"] and "Not approved" in refused["error"]
    assert not any(call[1] == "install" for call in calls)
    installer.approve("VideoLAN.VLC")
    done = installer.install("VideoLAN.VLC")
    assert done["ok"] and "--silent" in calls[-1] and "--exact" in calls[-1]
    assert not installer.approved("VideoLAN.VLC")             # one yes, one install
    installer.approve("VideoLAN.VLC")
    clock[0] += 601
    assert not installer.install("VideoLAN.VLC")["ok"]         # an old yes has expired
    assert (tmp_path / "installs.jsonl").read_text(encoding="utf-8").count("VideoLAN.VLC") == 1


def test_bad_ids_are_refused(tmp_path):
    installer = SoftwareInstaller(run=fake_winget([]), journal=tmp_path / "j.jsonl")
    installer.approve("x; rm -rf")
    assert not installer.install("x; rm -rf")["ok"]


# ------------------------------------------------------------------ persona
def test_persona_persists_and_keeps_the_honesty_line(tmp_path):
    persona = Persona(tmp_path / "persona.json")
    assert persona.rule() == ""
    persona.set(True)
    rule = Persona(tmp_path / "persona.json").rule()
    assert "real person" in rule and "tell the truth" in rule and "Do not invent personal experiences" in rule


# ------------------------------------------------------------------ routing
@pytest.mark.parametrize("text,control,entity", [
    ("install vlc", "SOFTWARE_INSTALL", "vlc"), ("download and install ffmpeg", "SOFTWARE_INSTALL", "ffmpeg"),
    ("install urdu", "LANGUAGE_INSTALL", "ur"), ("learn spanish yourself", "LANGUAGE_INSTALL", "es"),
    ("learn to speak french", "LANGUAGE_INSTALL", "fr"), ("talk to me in urdu", "REPLY_LANGUAGE", "ur"),
    ("speak hindi with me", "REPLY_LANGUAGE", "hi"), ("act like a real person", "PERSONA", True),
    ("back to assistant mode", "PERSONA", False),
])
def test_requests_route_to_their_controls(text, control, entity):
    intent = IntentRouter().route(text)
    value = intent.entities.get("software") or intent.entities.get("language")
    assert intent.control == control and (value if control != "PERSONA" else intent.entities["enabled"]) == entity


@pytest.mark.parametrize("text", ["install the python package requests", "install it", "teach me urdu", "install windows updates"])
def test_other_install_and_lesson_requests_are_left_alone(text):
    assert IntentRouter().route(text).control not in {"SOFTWARE_INSTALL", "LANGUAGE_INSTALL"}


def test_yes_and_no_answer_a_pending_install_only():
    router, session = IntentRouter(), SessionState()
    assert router.route("yes", session).control != "INSTALL_CONFIRM"
    session.pending_install = {"kind": "software", "id": "VideoLAN.VLC", "name": "VLC"}
    assert router.route("yes", session).control == "INSTALL_CONFIRM"
    assert router.route("no", session).control == "INSTALL_CANCEL"


# ------------------------------------------------------------------ the prompt and the voices
def test_the_prompt_carries_language_persona_and_incoming_rules():
    from app.agent.conversation import _incoming_rule, _language_rule

    assert "entirely in natural, clear Urdu" in _language_rule("ur")
    assert "written in Spanish" in _incoming_rule("es", "en") and "Reply in English" in _incoming_rule("es", "en")
    assert _incoming_rule("es", "es") == ""


class Voice(TextToSpeechProvider):
    def __init__(self, log, label):
        self.log, self.label, self.available = log, label, True

    def speak(self, text, wait=False):
        self.log.append((self.label, text))

    def stop(self):
        pass

    @property
    def is_speaking(self):
        return False


def test_a_reply_in_another_language_is_read_by_its_voice():
    log = []
    voices = {"es": Voice(log, "spanish"), "hi": Voice(log, "hindi")}
    language = ["es"]
    tts = BilingualTTS(Voice(log, "english"), lambda: None, voice_for=voices.get, language_supplier=lambda: language[0])
    tts.speak("Hola, ¿cómo estás?", wait=True)
    assert log == [("spanish", "Hola, ¿cómo estás?")]
    log.clear()
    language[0] = "en"
    tts.speak("Namaste is नमस्ते in Hindi", wait=True)          # one word may read as Nepali; the Hindi voice says it
    assert log == [("english", "Namaste is"), ("hindi", "नमस्ते"), ("english", "in Hindi")]


# ------------------------------------------------------------------ the whole turn
def test_install_persona_and_foreign_messages_through_the_assistant(monkeypatch):
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=False, start_background_services=False)
    try:
        reply = runtime.assistant.handle("install urdu").response.display_text
        assert "Urdu" in reply and "Say \"yes\"" in reply and runtime.session.pending_install["code"] == "ur"
        assert "won't install" in runtime.assistant.handle("no").response.display_text
        assert runtime.session.pending_install is None

        calls = []
        monkeypatch.setattr(runtime.software, "run", fake_winget(calls))
        monkeypatch.setattr(runtime.software, "available", lambda: True)
        plan = runtime.assistant.handle("install vlc").response.display_text
        assert "VideoLAN.VLC" in plan and not any(call[1] == "install" for call in calls)
        done = runtime.assistant.handle("yes").response.display_text
        assert "Installed VLC media player" in done and any(call[1] == "install" for call in calls)

        assert "real person" in runtime.assistant.handle("act like a real person").response.display_text
        assert runtime.persona.enabled and "real person" in runtime.conversation.persona_supplier()
        runtime.assistant.handle("back to assistant mode")
        assert not runtime.persona.enabled

        first = runtime.assistant.handle("Hola, ¿cómo estás? Quiero aprender inglés hoy").response.display_text
        assert "install Spanish" in first
        again = runtime.assistant.handle("Hola otra vez, ¿qué tal todo por allí?").response.display_text
        assert "install Spanish" not in again                    # offered once per session
    finally:
        runtime.persona.set(False)
        runtime.close()
