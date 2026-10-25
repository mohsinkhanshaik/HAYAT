"""Hands-free listening ends when Mohsin stops talking, and push-to-talk keeps its keys."""
from __future__ import annotations

import queue as queue_module
from types import SimpleNamespace

import numpy as np
import pytest

import app.voice.microphone as microphone
from app.voice.interruption import KeyboardClaim, WindowsHotkey
from app.voice.microphone import MicrophoneRecorder
from app.voice.vad import EnergyVoiceActivityDetector


class FakeDevices:
    def resolve(self, requested, kind):
        return 3

    def diagnose_input(self, include_windows=False):
        return {"available": True, "microphone_count": 1, "default_input": 3}


class Clock:
    """Audio time: 50 ms passes for every chunk the recorder takes."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


def simulate(monkeypatch, chunks, **config):
    clock = Clock()

    class TimedQueue(queue_module.Queue):
        def get(self, block=True, timeout=None):
            try:
                item = super().get(block=False)
            except queue_module.Empty:
                clock.now += timeout or 0.05
                raise
            clock.now += len(item) / 16000
            return item

    class Stream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            for chunk in chunks:
                self.callback(np.asarray(chunk, dtype=np.float32).reshape(-1, 1), len(chunk), None, None)
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(microphone.queue, "Queue", TimedQueue)
    monkeypatch.setattr(microphone.time, "monotonic", clock.monotonic)
    recorder = MicrophoneRecorder({"sample_rate": 16000, "maximum_seconds": 30, **config}, FakeDevices(),
                                  EnergyVoiceActivityDetector(0.0035))
    monkeypatch.setattr(recorder, "_sounddevice", lambda: SimpleNamespace(InputStream=Stream))
    return recorder


def noise(level, seconds, rng):
    return [rng.normal(0, level, 800) for _ in range(int(seconds / 0.05))]


def test_background_noise_no_longer_keeps_the_microphone_open(monkeypatch):
    rng = np.random.default_rng(1)
    # A fan louder than the detector's starting threshold, speech on top, then the fan alone.
    fan, voice = 0.012, 0.09
    chunks = noise(fan, 1.0, rng) + noise(voice, 2.0, rng) + noise(fan, 25.0, rng)
    recorder = simulate(monkeypatch, chunks)
    audio = recorder.record_until_silence(speech_start_timeout=8, silence_seconds=1.4)
    assert recorder.last_capture["ended_by"] == "silence"
    assert audio.duration_seconds < 6.0, "stopped about 1.4 s after the speech ended, not at the 30 s limit"


def test_a_tv_talking_in_the_background_is_cut_at_the_utterance_cap(monkeypatch):
    rng = np.random.default_rng(2)
    # Someone talking nearby: bursts of speech with pauses shorter than the
    # silence that ends an utterance -- the kind of noise no floor can learn.
    tv = []
    for _ in range(40):
        tv += noise(0.12, 0.6, rng) + noise(0.03, 0.4, rng)
    recorder = simulate(monkeypatch, tv, max_utterance_seconds=10)
    audio = recorder.record_until_silence(speech_start_timeout=8, silence_seconds=1.4)
    assert recorder.last_capture["ended_by"] == "length_cap"
    assert 9.5 <= audio.duration_seconds <= 11.5


def test_a_constant_loud_noise_is_learned_and_ends_the_utterance(monkeypatch):
    rng = np.random.default_rng(5)
    recorder = simulate(monkeypatch, noise(0.09, 40.0, rng))
    audio = recorder.record_until_silence(speech_start_timeout=8, silence_seconds=1.4)
    assert recorder.last_capture["ended_by"] == "silence" and audio.duration_seconds < 15


def test_a_quiet_room_still_ends_on_silence(monkeypatch):
    rng = np.random.default_rng(3)
    chunks = noise(0.001, 0.5, rng) + noise(0.05, 1.5, rng) + noise(0.001, 5.0, rng)
    recorder = simulate(monkeypatch, chunks)
    audio = recorder.record_until_silence(speech_start_timeout=8, silence_seconds=1.0)
    assert recorder.last_capture["ended_by"] == "silence" and audio.duration_seconds < 4.0


def test_the_noise_floor_learns_a_constant_loud_background():
    rng = np.random.default_rng(4)
    vad = EnergyVoiceActivityDetector(0.0035)
    decisions = [vad.analyze(rng.normal(0, 0.012, 800)).is_speech for _ in range(200)]
    assert decisions[0] is True            # at first a fan above threshold looks like speech
    assert not any(decisions[-40:])        # ten seconds later it is recognised as the room
    assert vad.analyze(rng.normal(0, 0.09, 800)).is_speech


# ------------------------------------------------------------------ push-to-talk keys
def test_the_claim_swallows_ctrl_space_only_while_enabled():
    claim = KeyboardClaim([0x11, 0x20])
    assert claim.decide(0x20, 0x0100, injected=False, modifiers_down=True) is False   # not enabled yet
    claim.enabled = True
    assert claim.decide(0x20, 0x0100, injected=False, modifiers_down=True) is True and claim.down
    assert claim.decide(0x20, 0x0100, injected=False, modifiers_down=True) is True      # key repeat too
    assert claim.decide(0x20, 0x0101, injected=False, modifiers_down=True) is True and not claim.down
    assert claim.decide(0x20, 0x0100, injected=False, modifiers_down=False) is False    # plain space types
    assert claim.decide(0x41, 0x0100, injected=False, modifiers_down=True) is False     # Ctrl+A untouched
    assert claim.decide(0x20, 0x0100, injected=True, modifiers_down=True) is False      # HAYAT's own typing


def test_the_hook_is_never_installed_during_tests():
    hotkey = WindowsHotkey("ctrl+space")
    if hotkey.claim is None:
        pytest.skip("not Windows")
    assert hotkey.claim_keys(True) is False and hotkey.claim._thread is None


def test_exclusive_can_be_turned_off():
    assert WindowsHotkey("ctrl+space", exclusive=False).claim is None


# ------------------------------------------------------------------ transcripts of noise
def test_hands_free_rejects_what_whisper_hears_in_an_empty_room():
    from app.voice.manager import VoiceManager

    manager = SimpleNamespace(config={"vad": {}}, _NOISE_PHRASES=VoiceManager._NOISE_PHRASES)
    probably_noise = lambda text, confidence: VoiceManager._probably_noise(manager, text, confidence)
    assert probably_noise("Thank you.", 0.5)
    assert probably_noise("...", None)
    assert probably_noise("open notepad", 0.2)
    assert not probably_noise("open notepad", 0.7)
    assert not probably_noise("yes", 0.5)                   # approvals are answered with yes
    assert not probably_noise("Thank you.", 0.9)            # said clearly, it is meant
