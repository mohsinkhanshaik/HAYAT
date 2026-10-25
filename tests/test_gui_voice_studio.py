"""Voice Studio page: real levels, pipeline stages, device and speed settings, conversation and events."""
import time
import tkinter as tk
from tkinter import ttk
import queue
from types import SimpleNamespace

import pytest

from app.gui_theme import Palette
from app.gui_voice_studio import STAGE_OF, VoicePage, decibels
from app.voice.models import VoiceState
from tests.conftest import close_window, open_window


class FakeMemory:
    def __init__(self):
        self.rows = []

    def conversation_history(self, session_id, limit=30):
        return self.rows[-limit:]


class FakeVoice:
    def __init__(self):
        self.callbacks = []
        self.level = {"input": 0.0, "output": 0.0}
        self.tts = SimpleNamespace(speed=1.0, voice_name="en_US-lessac-medium", output_device=3)
        self.stt = SimpleNamespace(model_name="tiny.en")
        self.recorder = SimpleNamespace(device=1)
        self.session = SimpleNamespace(memory=FakeMemory(), session_id="voice-test")
        self.devices = SimpleNamespace(list_devices=lambda: {
            "microphones": [{"id": 1, "name": "Mic A", "host_api": "WASAPI"}, {"id": 2, "name": "Mic B", "host_api": "WASAPI"}],
            "speakers": [{"id": 3, "name": "Speakers", "host_api": "WASAPI"}], "default_input": 1, "default_output": 3})
        self.chosen = []

    def add_state_callback(self, callback):
        self.callbacks.append(callback)

    def levels(self):
        return dict(self.level)

    def set_input_device(self, index):
        self.chosen.append(("input", index))

    def set_output_device(self, index):
        self.chosen.append(("output", index))


class Studio:
    def __init__(self, gui):
        self.gui, self.palette, self.actions, self.statuses = gui, type(gui), [], []

    def voice_action(self, action):
        self.actions.append(action)

    def set_status(self, text, page=None):
        self.statuses.append(text)


@pytest.fixture
def voice_page(shared_tk):
    parent = open_window(shared_tk, "1300x860-4000-4000")
    voice = FakeVoice()

    class GUI(Palette):
        pass
    gui = GUI()
    gui.root, gui.runtime = parent, SimpleNamespace(voice=voice)
    gui.depth_nav = SimpleNamespace(motion=False)
    gui._probe_microphone = lambda: None
    gui._test_speaker = lambda: None
    examples = []
    gui.use_example = examples.append
    frame = tk.Frame(parent)
    frame.pack(fill="both", expand=True)
    studio = Studio(gui)
    page = VoicePage(studio, frame)
    parent.update()
    try:
        yield page, voice, studio, examples, parent
    finally:
        close_window(parent)


def status(state="listening", enabled=True, mode="continuous"):
    return {"enabled": enabled, "state": state, "mode": mode, "stt_provider": "faster_whisper", "tts_provider": "piper",
            "push_to_talk_key": "ctrl+space", "last_error": None}


def test_levels_pipeline_and_settings_follow_the_voice_system(voice_page):
    page, voice, studio, _examples, parent = voice_page
    voice.level = {"input": 0.5, "output": 0.0}
    page.update({}, status("listening"))
    parent.update()
    assert page.cards["input"]["level"].get() == "-6 dB" and page.cards["output"]["level"].get() == "-∞ dB"
    assert page.cards["input"]["state"].get() == "Listening…" and page._stage == 0
    assert page.toggle.cget("text") == "Stop voice"
    assert str(page.mode_buttons["continuous"].cget("style")) == "HayatAccent.TButton"
    assert page.config_vars["tts"].get() == "piper · en_US-lessac-medium" and page.config_vars["stt"].get() == "faster_whisper · tiny.en"
    assert page.config_vars["mode"].get() == "Hands-free" and page.config_vars["hotkey"].get() == "ctrl+space"
    page.update({}, status("speaking"))
    assert page._stage == 3 and page.cards["output"]["state"].get() == "Speaking…"
    page.update({}, status("ready", enabled=False))
    assert page._stage is None and page.cards["input"]["state"].get() == "Off" and page.toggle.cget("text") == "Start voice"
    assert {state: STAGE_OF[state] for state in ("transcribing", "acting", "approval_required")} == {
        "transcribing": 1, "acting": 2, "approval_required": 2}
    assert decibels(0) == "-∞ dB" and decibels(1) == "0 dB"


def test_devices_speed_samples_and_controls_change_real_settings(voice_page):
    page, voice, studio, examples, _parent = voice_page
    page.load_devices()
    assert page.cards["input"]["device"].get() == "Mic A (WASAPI)" and page.cards["output"]["device"].get() == "Speakers (WASAPI)"
    page.cards["input"]["device"].set("Mic B (WASAPI)")
    page.choose_device("input")
    assert voice.chosen == [("input", 2)] and "Mic B" in studio.statuses[-1]
    page.speed.set(1.46)
    page.set_speed()
    assert voice.tts.speed == 1.5 and page.speed_label.get() == "1.5×"
    page.try_sample("Show global intelligence")
    assert examples == ["Show global intelligence"]
    page.toggle.invoke()
    page.mode_buttons["wake_word"].invoke()
    assert studio.actions == ["toggle", "wake_word"]


def test_conversation_and_live_events_come_from_the_session(voice_page):
    page, voice, _studio, _examples, parent = voice_page
    page.update({}, status("ready"))
    assert "Nothing said" in page.transcript.get("1.0", "end")
    voice.session.memory.rows = [{"timestamp": "2026-10-05T02:29:00", "role": "user", "content": "Latest earthquake news?"},
                                 {"timestamp": "2026-10-05T02:29:05", "role": "assistant", "content": "A magnitude 5.0 quake..."}]
    for callback in voice.callbacks:
        callback(VoiceState.LISTENING)
        callback(VoiceState.TRANSCRIBING)
    page._conversation_checked = 0
    page.update({}, status("transcribing"))
    text = page.transcript.get("1.0", "end")
    assert "You  02:29\nLatest earthquake news?" in text and "HAYAT  02:29\nA magnitude 5.0" in text
    labels = [child.cget("text") for line in page.doing.winfo_children() for child in line.winfo_children()]
    assert "Transcribing…" in labels and "Listening…" in labels


def test_compact_window_hides_the_side_column_but_keeps_controls(voice_page):
    page, _voice, _studio, _examples, parent = voice_page
    parent.geometry("1300x860-4000-4000")
    parent.update()
    assert page.side.winfo_ismapped()
    parent.geometry("840x640-4000-4000")
    parent.update()
    time.sleep(.05)
    parent.update()
    assert not page.side.winfo_ismapped() and page.toggle.winfo_ismapped()


def test_voice_3d_motion_menu_survives_live_refresh_and_follows_shared_reduced_motion(voice_page):
    from app.gui import HayatGUI
    from app.gui_studio import ControlStudio

    page, _voice, studio, _examples, _parent = voice_page
    gui = studio.gui
    changes = []
    gui.depth_nav.set_motion = lambda value: setattr(gui.depth_nav, 'motion', value)
    def shared_motion(value):
        changes.append(value)
        HayatGUI._set_workspace_motion(gui, value)
    gui._set_workspace_motion = shared_motion
    studio.results = queue.Queue()
    studio.current_page = 'voice'
    studio.winfo_viewable = lambda: True
    studio.voice_scene, studio.voice_page = page.scene, page

    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    button = next(widget for widget in descendants(page.page)
                  if isinstance(widget, ttk.Menubutton) and widget.cget('text').startswith('3D view'))
    menu = button.nametowidget(button.cget('menu'))

    page.scene.set_motion(True)
    assert gui.depth_nav.motion
    menu.invoke('Pause / resume motion')
    for _ in range(3):
        ControlStudio.update_live(studio, {}, status('ready'), False)
    assert not page.scene.motion and not gui.depth_nav.motion and changes[-1] is False

    menu.invoke('Pause / resume motion')
    ControlStudio.update_live(studio, {}, status('ready'), False)
    assert page.scene.motion and gui.depth_nav.motion and changes[-1] is True

    gui._set_workspace_motion(False)
    ControlStudio.update_live(studio, {}, status('ready'), False)
    assert not page.scene.motion and not gui.depth_nav.motion


def test_piper_receives_the_speed_setting():
    from app.voice.tts import PiperTTS
    calls = []

    class Voice:
        def synthesize(self, text, syn_config=None):
            calls.append(syn_config)
            return []
    tts = PiperTTS.__new__(PiperTTS)
    tts.speed, tts.volume = 1.25, 1.0
    tts._stop = __import__("threading").Event()
    tts._load = lambda: Voice()
    tts.synthesize_to_array("Hello")
    assert calls[0].length_scale == pytest.approx(0.8)
    tts.speed = 1.0
    tts.synthesize_to_array("Hello")
    assert calls[1] is None


def test_isolated_and_bilingual_voices_carry_the_speed():
    from app.voice.tts import BilingualTTS, IsolatedPiperTTS
    sent = []
    tts = IsolatedPiperTTS.__new__(IsolatedPiperTTS)
    tts.speed = 1.5
    tts._stop = __import__("threading").Event()
    tts._synth = SimpleNamespace(reopen=lambda: None, _request=lambda operation, payload, cancel: sent.append(payload))
    tts._synthesize("Hello")
    tts.speed = 1.0
    tts._synthesize("Hi")
    assert sent == [("Hello", 1.5), "Hi"]
    primary = SimpleNamespace(speed=1.0, voice_name="en_US-lessac-medium")
    bilingual = BilingualTTS(primary, lambda: None)
    bilingual.speed = 1.3
    assert primary.speed == 1.3 and bilingual.speed == 1.3 and bilingual.voice_name == "en_US-lessac-medium"
