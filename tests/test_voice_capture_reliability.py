from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app.voice.microphone import MicrophoneRecorder
from app.voice.stt import FasterWhisperSTT
from app.voice.vad import EnergyVoiceActivityDetector


class FakeDevices:
    def __init__(self, available=True):
        self.available = available

    def resolve(self, requested, kind):
        return 3 if self.available else None

    def diagnose_input(self, include_windows=False):
        return {
            "available": self.available,
            "microphone_count": 1 if self.available else 0,
            "default_input": 3 if self.available else -1,
        }


class FakeInputStream:
    chunks = []

    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]

    def __enter__(self):
        for chunk in self.chunks:
            self.callback(np.asarray(chunk, dtype=np.float32).reshape(-1, 1), len(chunk), None, None)
        return self

    def __exit__(self, *args):
        return False


class FakeSoundDevice:
    InputStream = FakeInputStream


def test_missing_windows_input_fails_with_an_actionable_error(monkeypatch):
    recorder = MicrophoneRecorder({}, FakeDevices(False))
    monkeypatch.setattr(recorder, "_sounddevice", lambda: FakeSoundDevice)
    with pytest.raises(RuntimeError, match="default input is -1"):
        recorder.record_while(lambda: False)
    assert recorder.last_capture["reason"] == "no_input_device"


def test_push_to_talk_keeps_a_minimum_capture_after_fast_release(monkeypatch):
    FakeInputStream.chunks = [np.ones(800) * 0.025 for _ in range(4)]
    recorder = MicrophoneRecorder(
        {"minimum_push_seconds": 0.1, "sample_rate": 16000}, FakeDevices(True)
    )
    monkeypatch.setattr(recorder, "_sounddevice", lambda: FakeSoundDevice)
    audio = recorder.record_while(lambda: False)
    assert audio.duration_seconds == pytest.approx(0.2)
    assert recorder.last_capture["captured"] is True
    assert recorder.last_capture["mode"] == "push_to_talk"


def test_silence_bounded_capture_keeps_preroll_and_stops_after_speech(monkeypatch):
    quiet = [np.ones(800) * 0.001 for _ in range(4)]
    speech = [np.ones(800) * 0.03 for _ in range(3)]
    FakeInputStream.chunks = [*quiet, *speech, np.zeros(800)]
    recorder = MicrophoneRecorder(
        {"pre_roll_seconds": 0.1, "minimum_utterance_seconds": 0.2,
         "sample_rate": 16000, "maximum_seconds": 1},
        FakeDevices(True), EnergyVoiceActivityDetector(0.006),
    )
    monkeypatch.setattr(recorder, "_sounddevice", lambda: FakeSoundDevice)
    audio = recorder.record_until_silence(speech_start_timeout=0.2, silence_seconds=0.02)
    assert audio.duration_seconds >= 0.2
    assert recorder.last_capture["speech_started"] is True
    assert recorder.last_capture["observed_peak"] == pytest.approx(0.03)


def test_single_noise_spike_does_not_arm_an_utterance(monkeypatch):
    spike = [np.ones(800) * 0.03]
    quiet = [np.zeros(800) for _ in range(4)]
    speech = [np.ones(800) * 0.025 for _ in range(3)]
    FakeInputStream.chunks = [*spike, *quiet, *speech, np.zeros(800)]
    recorder = MicrophoneRecorder(
        {"pre_roll_seconds": 0.2, "minimum_utterance_seconds": 0.2,
         "speech_start_chunks": 3, "sample_rate": 16000, "maximum_seconds": 1},
        FakeDevices(True), EnergyVoiceActivityDetector(0.006),
    )
    monkeypatch.setattr(recorder, "_sounddevice", lambda: FakeSoundDevice)
    audio = recorder.record_until_silence(speech_start_timeout=0.3, silence_seconds=0.02)
    assert recorder.last_capture["speech_started"] is True
    assert recorder.last_capture["speech_start_chunks"] == 3
    assert audio.duration_seconds >= 0.2


def test_whisper_uses_bounded_normalization_and_does_not_double_filter_vad(monkeypatch):
    captured = {}

    class FakeModel:
        def transcribe(self, samples, **kwargs):
            captured["peak"] = float(np.max(np.abs(samples)))
            captured["kwargs"] = kwargs
            return [SimpleNamespace(text="hello hayat")], SimpleNamespace(language="en", language_probability=0.99)

    provider = FasterWhisperSTT({
        "model": "base.en", "vad_filter": False, "normalize_audio": True,
        "maximum_gain": 8, "target_peak": 0.8,
    })
    monkeypatch.setattr(provider, "_load", lambda: FakeModel())
    from app.voice.models import AudioData
    result = provider.transcribe(AudioData(np.ones(3200, dtype=np.float32) * 0.01, 16000))
    assert result.text == "hello hayat"
    assert captured["kwargs"]["vad_filter"] is False
    # A constant DC-only signal becomes silence and is not amplified.
    assert captured["peak"] < 1e-6
