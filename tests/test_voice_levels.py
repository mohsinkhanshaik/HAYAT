"""Live loudness for the voice visual: honest, bounded, and never retained."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from app.voice.levels import LevelMeter, PlaybackEnvelope, loudness
from app.voice.manager import VoiceManager
from app.voice.microphone import MicrophoneRecorder
from app.voice.tts import PiperTTS
from tests.test_voice_capture_reliability import FakeDevices, FakeInputStream, FakeSoundDevice


class Clock:
    def __init__(self):
        self.now = 50.0

    def __call__(self):
        return self.now


def test_loudness_is_a_bounded_decibel_scale():
    assert loudness(0) == loudness(-1) == loudness(float("nan")) == 0.0
    assert loudness(1e-4) == 0.0          # -80 dB: silence
    assert loudness(1.0) == 1.0           # full scale
    quiet, speech = loudness(0.003), loudness(0.03)
    assert 0 < quiet < speech < 1


def test_meter_reports_the_latest_block_then_fades_when_blocks_stop():
    clock = Clock()
    meter = LevelMeter(clock)
    assert meter.read() == 0.0
    meter.push(np.ones(800, dtype=np.float32) * 0.05)
    level = meter.read()
    assert level > .5
    clock.now += LevelMeter.HOLD_SECONDS
    assert meter.read() == level
    clock.now += 1.0
    assert meter.read() < .01
    meter.push(np.zeros(800, dtype=np.float32))
    assert meter.read() == 0.0
    meter.push("not audio")  # a bad block is ignored, never raised into the audio thread
    assert meter.read() == 0.0


def test_envelope_follows_playback_time_and_forgets_it_afterwards():
    clock = Clock()
    envelope = PlaybackEnvelope(clock)
    rate = 1000
    quiet_then_loud = np.concatenate([np.ones(400) * 0.0005, np.ones(400) * 0.2]).astype(np.float32)
    envelope.start(quiet_then_loud, rate)
    assert envelope.read() == 0.0
    clock.now += .5
    assert envelope.read() > .9
    clock.now += 5
    assert envelope.read() == 0.0
    envelope.start(np.ones((800, 2), dtype=np.float32) * 0.2, rate)  # stereo is mixed down
    assert envelope.read() > .9
    envelope.stop()
    assert envelope.read() == 0.0


def test_every_capture_path_feeds_the_meter(monkeypatch):
    recorder = MicrophoneRecorder({"minimum_push_seconds": 0.1}, FakeDevices(True))
    monkeypatch.setattr(recorder, "_sounddevice", lambda: FakeSoundDevice)
    FakeInputStream.chunks = [np.ones(800) * 0.05 for _ in range(4)]
    recorder.record_while(lambda: False)
    assert recorder.meter.read() > .5

    recorder.meter = LevelMeter()
    FakeInputStream.chunks = [np.ones(800) * 0.05 for _ in range(4)] + [np.zeros(800)]
    recorder.record_until_silence(speech_start_timeout=0.01, silence_seconds=0.01)
    assert recorder.meter.read() == 0.0  # the last block was silent

    recorder.meter = LevelMeter()
    FakeInputStream.chunks = [np.ones(1280) * 0.05]
    assert recorder.listen_for(lambda frame: True) is True
    assert recorder.meter.read() > .5


def test_speech_playback_publishes_its_envelope_only_while_playing(monkeypatch):
    seen = []
    tts = PiperTTS.__new__(PiperTTS)
    tts.output_device = None

    def play(samples, rate, device=None, blocking=True):
        seen.append(tts.envelope.read())

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(play=play))
    tts._play(np.ones(22050, dtype=np.float32) * 0.2, 22050)
    assert seen and seen[0] > .9
    assert tts.envelope.read() == 0.0


def test_manager_levels_tolerate_components_without_meters():
    working = SimpleNamespace(recorder=SimpleNamespace(meter=SimpleNamespace(read=lambda: .4)),
                              tts=SimpleNamespace(envelope=SimpleNamespace(read=lambda: .7)))
    assert VoiceManager.levels(working) == {"input": .4, "output": .7}
    bare = SimpleNamespace(recorder=object(), tts=object())
    assert VoiceManager.levels(bare) == {"input": 0.0, "output": 0.0}

    def broken():
        raise RuntimeError("device vanished")

    failing = SimpleNamespace(recorder=SimpleNamespace(meter=SimpleNamespace(read=broken)), tts=object())
    assert VoiceManager.levels(failing) == pytest.approx({"input": 0.0, "output": 0.0})
