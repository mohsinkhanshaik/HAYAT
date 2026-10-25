from types import SimpleNamespace

import numpy as np

from app.linguistics import expand_query, normalize_transcript
from app.voice.models import AudioData
from app.voice.stt import FasterWhisperSTT


def test_sound_alike_and_technical_terms_are_repaired_without_fuzzy_rewriting():
    assert normalize_transcript("High at open power shell") == "HAYAT open PowerShell"
    assert normalize_transcript("analyse pie torch on cuda") == "analyse PyTorch on CUDA"
    assert normalize_transcript("open payroll.txt") == "open payroll.txt"


def test_retrieval_expansion_joins_acronyms_synonyms_and_long_forms():
    short = expand_query("why is PTT not working")
    assert "push to talk" in short.text.casefold()
    assert "push to talk" in short.concepts
    long = expand_query("improve optical character recognition")
    assert "ocr" in long.text.casefold().split()


class _FakeWhisper:
    def __init__(self, passes):
        self.passes = list(passes)
        self.beams = []

    def transcribe(self, samples, **kwargs):
        self.beams.append(kwargs["beam_size"])
        text, average_log_probability = self.passes.pop(0)
        segments = [] if not text else [SimpleNamespace(
            text=text, avg_logprob=average_log_probability, no_speech_prob=0.0,
        )]
        return segments, SimpleNamespace(language="en", language_probability=0.99)


def _audio():
    return AudioData(np.ones(8000, dtype=np.float32) * 0.05, 16000)


def test_uncertain_voice_decode_retries_once_and_selects_the_clearer_phrase():
    provider = FasterWhisperSTT({
        "model": "fake", "beam_size": 3,
        "multi_decode": {"enabled": True, "maximum_passes": 2,
                         "retry_below_confidence": 0.52, "retry_beam_size": 8},
    })
    fake = _FakeWhisper([("", -3.0), ("High at open power shell", -0.05)])
    provider._model = fake
    result = provider.transcribe(_audio())
    assert result.text == "HAYAT open PowerShell"
    assert result.raw_text == "High at open power shell"
    assert result.decode_passes == 2
    assert fake.beams == [3, 8]


def test_clear_voice_decode_stays_single_pass_for_low_latency():
    provider = FasterWhisperSTT({"model": "fake", "beam_size": 5,
                                 "multi_decode": {"enabled": True}})
    fake = _FakeWhisper([("open notepad", -0.05)])
    provider._model = fake
    result = provider.transcribe(_audio())
    assert result.text == "open notepad"
    assert result.decode_passes == 1
    assert fake.beams == [5]
