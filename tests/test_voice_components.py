
import numpy as np

from app.voice.models import AudioData, Transcription
from app.voice.stt import SpeechToTextProvider
from app.voice.tts import TextToSpeechProvider
from app.voice.vad import EnergyVoiceActivityDetector
from app.voice.wakeword import WakeWordDetector


class FakeSTT(SpeechToTextProvider):
    name = "fake"

    def transcribe(self, audio, cancel=None):
        return Transcription("hello ai", "en", 1.0, 2)


class FakeTTS(TextToSpeechProvider):
    name = "fake"

    def __init__(self):
        self.messages = []
        self.stopped = False

    def speak(self, text, wait=False):
        self.messages.append(text)

    def stop(self):
        self.stopped = True

    @property
    def is_speaking(self):
        return False


def test_stt_and_tts_abstractions():
    audio = AudioData(np.zeros(1600, dtype=np.float32), 16000)
    assert FakeSTT().transcribe(audio).text == "hello ai"
    tts = FakeTTS()
    tts.speak("hello")
    tts.stop()
    assert tts.messages == ["hello"] and tts.stopped


def test_vad_and_wake_word():
    vad = EnergyVoiceActivityDetector(0.01)
    assert not vad.analyze(np.zeros(800)).is_speech
    assert vad.analyze(np.ones(800) * 0.1).is_speech
    wake = WakeWordDetector("hey ai", ["hey we are"])
    assert wake.extract_command("Hey AI, open Chrome") == "open chrome"
    assert wake.extract_command("ordinary conversation") is None
    assert wake.extract_command("Hey we are, open Chrome") == "open chrome"
