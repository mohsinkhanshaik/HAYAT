from types import SimpleNamespace
import threading
import time

from app.memory.database import MemoryDatabase
from app.voice.manager import SpokenResponseFormatter, VoiceManager
from app.voice.models import Transcription
from app.voice.models import AudioData
import numpy as np
from app.voice.stt import SpeechToTextProvider
from app.voice.tts import TextToSpeechProvider


class FakeSTT(SpeechToTextProvider):
    name = "fake_stt"

    def transcribe(self, audio, cancel=None):
        return Transcription("status")


class FakeTTS(TextToSpeechProvider):
    name = "fake_tts"

    def __init__(self):
        self.messages = []
        self.stops = 0
        self.output_device = None

    def speak(self, text, wait=False):
        self.messages.append(text)

    def stop(self):
        self.stops += 1

    @property
    def is_speaking(self):
        return False


class FakeRecorder:
    pass


class FakeAgent:
    is_running = False

    def cancel_current(self):
        return False


class FakeManager:
    def __init__(self):
        self.goals = []

    def route(self, goal):
        self.goals.append(goal)
        return SimpleNamespace(complete=True, summary="Task succeeded.", task_id="task-voice", history=[])


class FakeConversation:
    def reply(self, text):
        return "Yesterday we worked on the voice interface."


def make_runtime(tmp_path):
    memory = MemoryDatabase(tmp_path / "voice-manager.db")
    memory.initialize()
    return SimpleNamespace(
        memory=memory, agent=FakeAgent(), manager=FakeManager(), conversation=FakeConversation(),
        registry=SimpleNamespace(approval_callback=lambda *args: False),
    )


def config():
    return {
        "push_to_talk_key": "ctrl+space", "mode": "push_to_talk",
        "microphone": {}, "stt": {}, "tts": {}, "vad": {}, "wake_word": {},
        "approvals": {"enabled": True, "timeout_seconds": 1},
        "spoken_responses": {"enabled": True}, "progress_speech": {"enabled": False},
        "privacy": {"log_transcriptions": False},
    }


def test_voice_manager_fast_commands_and_memory(tmp_path):
    runtime = make_runtime(tmp_path)
    tts = FakeTTS()
    manager = VoiceManager(runtime, config(), FakeSTT(), tts, FakeRecorder())
    assert manager.process_transcript("mute").spoken_response == ""
    assert manager.muted
    assert "remember" in manager.process_transcript("Remember that Atlas is the main project").spoken_response.lower()
    recalled = runtime.memory.retrieve("Atlas main project", "semantic")
    assert recalled and "Atlas" in recalled[0]["content"]
    assert manager.process_transcript("stop").display_response.startswith("Speech playback stopped")
    close = manager.process_transcript("Close Notepad")
    assert close.complete is True
    assert runtime.manager.goals == ["Close Notepad"]


def test_submitted_voice_transcript_is_emitted_once_before_reply(tmp_path, monkeypatch):
    from app.output import get_output

    events = []
    monkeypatch.setattr(get_output(), "emit", lambda text, source="system": events.append((source, text)))
    manager = VoiceManager(make_runtime(tmp_path), config(), FakeSTT(), FakeTTS(), FakeRecorder())
    manager.response_callback = lambda response: events.append(("reply", response.display_response))
    manager.submit_transcript("stop talking")
    assert events[0] == ("voice_transcript", "You: stop talking")
    assert sum(source == "voice_transcript" for source, _ in events) == 1
    assert any(source == "reply" for source, _ in events)


def test_voice_log_omits_transcription_content_by_default(tmp_path):
    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    manager.log_path = tmp_path / "voice.jsonl"
    manager._log_transcription("private spoken words")
    logged = manager.log_path.read_text(encoding="utf-8")
    assert "private spoken words" not in logged
    assert '"content_logged": false' in logged


def test_voice_manager_routes_through_existing_manager_and_conversation(tmp_path):
    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    response = manager.process_transcript("Open Chrome")
    assert response.complete is True
    assert runtime.manager.goals == ["Open Chrome"]
    answer = manager.process_transcript("What were we working on yesterday?")
    assert "voice interface" in answer.display_response


def test_voice_manager_changes_devices_for_current_session(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    monkeypatch.setattr(manager.devices, "resolve", lambda requested, kind: 4 if kind == "input" else 7)
    assert manager.set_input_device("microphone") == 4
    assert manager.recorder.device == 4
    assert manager.set_output_device("speaker") == 7
    assert manager.tts.output_device == 7


def test_spoken_formatter_is_short_and_omits_paths():
    compact = SpokenResponseFormatter.compact_text("### See **D:\\AI\\very\\long\\path** and https://example.com/a")
    assert "D:\\" not in compact
    assert "https://" not in compact
    assert "#" not in compact and "*" not in compact


def test_privacy_mode_prevents_the_microphone_listener_from_starting(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime.authority = SimpleNamespace(privacy_mode=True)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    manager.start_background()
    assert manager.status()["privacy_blocked"] is True
    assert manager._runner_thread is None
    assert manager._resume_after_privacy is True


def test_voice_refuses_to_claim_started_without_a_recording_endpoint(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    manager.log_path = tmp_path / "no-input.jsonl"
    monkeypatch.setattr(manager.devices, "list_devices", lambda: {
        "microphones": [], "speakers": [], "default_input": -1, "default_output": -1,
    })
    assert manager.start_background() is False
    status = manager.status()
    assert status["enabled"] is False
    assert status["desired_running"] is False
    assert status["input_available"] is False
    assert "No Windows recording device" in status["last_error"]


def test_continuous_voice_rejects_its_own_speaker_echo(tmp_path):
    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), FakeRecorder())
    manager._last_spoken_text = "All thirty six tests passed successfully"
    assert manager._looks_like_speaker_echo("thirty six tests passed successfully")
    assert not manager._looks_like_speaker_echo("stop now please")


def test_listener_recovers_after_transient_cycle_failure(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    settings = config()
    settings["mode"] = "continuous"
    settings["reliability"] = {
        "initial_backoff_seconds": 0.01,
        "maximum_backoff_seconds": 0.01,
    }
    manager = VoiceManager(runtime, settings, FakeSTT(), FakeTTS(), FakeRecorder())
    manager.log_path = tmp_path / "recovery.jsonl"
    calls = 0

    def cycle():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("microphone was temporarily unavailable")
        manager.cancellation.shutdown.set()

    monkeypatch.setattr(manager, "_continuous_cycle", cycle)
    monkeypatch.setattr(manager, "_probe_devices", lambda: True)
    manager.run_forever()
    status = manager.status()
    assert calls == 2
    assert status["recovery_count"] == 1
    assert status["consecutive_failures"] == 0
    assert status["last_error"] is None
    assert status["last_heartbeat_at"]
    assert "watchdog_recovery" in status["capabilities"]


def test_voice_watchdog_restarts_only_while_service_is_desired(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    settings = config()
    settings["warm_stt_on_start"] = False
    settings["reliability"] = {"supervised": True, "watchdog_seconds": 0.02}
    manager = VoiceManager(runtime, settings, FakeSTT(), FakeTTS(), FakeRecorder())
    manager.log_path = tmp_path / "watchdog.jsonl"
    calls = 0
    called = threading.Event()

    def unexpectedly_exits():
        nonlocal calls
        calls += 1
        called.set()

    monkeypatch.setattr(manager, "run_forever", unexpectedly_exits)
    monkeypatch.setattr(manager, "_probe_devices", lambda: True)
    manager.start_background()
    assert called.wait(0.5)
    deadline = time.monotonic() + 1.0
    while calls < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls >= 2
    manager.stop()
    stopped_calls = calls
    time.sleep(0.08)
    assert calls == stopped_calls
    assert manager.status()["desired_running"] is False


def test_push_to_talk_press_captures_transcribes_and_submits(tmp_path):
    class PushRecorder:
        last_capture = {}

        def record_while(self, keep_recording, cancel):
            self.last_capture = {"captured": True, "duration_seconds": 0.5, "peak": 0.2}
            return AudioData(np.ones(8000, dtype=np.float32) * 0.2, 16000)

    class PushHotkey:
        combination = "ctrl+space"

        @staticmethod
        def wait_for_press(stop_event):
            return True

        @staticmethod
        def pressed():
            return False

        @staticmethod
        def wait_for_release(stop_event):
            return None

    runtime = make_runtime(tmp_path)
    manager = VoiceManager(runtime, config(), FakeSTT(), FakeTTS(), PushRecorder())
    manager.log_path = tmp_path / "push.jsonl"
    manager.hotkey = PushHotkey()
    responses = []
    manager.response_callback = responses.append
    manager._push_to_talk_cycle()
    assert responses and "Voice is" in responses[0].spoken_response
    assert '"event": "push_to_talk_pressed"' in manager.log_path.read_text(encoding="utf-8")
