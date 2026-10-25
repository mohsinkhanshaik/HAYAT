from __future__ import annotations

import os
import math
import subprocess
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import PROJECT_ROOT
from app.linguistics import normalize_transcript
from app.voice.models import AudioData, Transcription


class SpeechToTextProvider(ABC):
    name = "base"

    @abstractmethod
    def transcribe(self, audio: AudioData, cancel=None) -> Transcription:
        raise NotImplementedError


class FasterWhisperSTT(SpeechToTextProvider):
    name = "faster_whisper"

    def __init__(self, config: dict) -> None:
        self.model_name = os.getenv("AI_VOICE_STT_MODEL", str(config.get("model", "tiny.en")))
        self.language = os.getenv("AI_VOICE_LANGUAGE", str(config.get("language", "auto")))
        self.device = str(config.get("device", "cpu"))
        self.compute_type = str(config.get("compute_type", "int8"))
        self.hotwords = str(config.get("hotwords", "AI Chrome Edge PowerShell Python GitHub YouTube Kubernetes"))
        self.beam_size = max(1, int(config.get("beam_size", 5)))
        self.initial_prompt = str(config.get("initial_prompt", "This is a command to a Windows assistant named AI."))
        multi = config.get("multi_decode", {})
        self.multi_decode = bool(multi.get("enabled", True))
        self.retry_confidence = max(0.0, min(float(multi.get("retry_below_confidence", 0.52)), 1.0))
        self.retry_beam_size = max(self.beam_size, min(int(multi.get("retry_beam_size", 8)), 16))
        self.maximum_decode_passes = max(1, min(int(multi.get("maximum_passes", 2)), 2))
        # The recorder already applies turn-level VAD in hands-free mode.
        # Whisper's second VAD pass can erase short commands such as "stop"
        # or "yes", so it is opt-in rather than silently applied twice.
        self.vad_filter = bool(config.get("vad_filter", False))
        self.normalize_audio = bool(config.get("normalize_audio", True))
        self.maximum_gain = max(1.0, min(float(config.get("maximum_gain", 8.0)), 20.0))
        self.target_peak = max(0.1, min(float(config.get("target_peak", 0.8)), 0.98))
        root = Path(str(config.get("download_root", "data/models/whisper")))
        self.download_root = root if root.is_absolute() else PROJECT_ROOT / root
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("Faster Whisper is not installed. Run .\\setup.ps1.") from exc
            self.download_root.mkdir(parents=True, exist_ok=True)
            self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type, download_root=str(self.download_root))
        return self._model

    def warmup(self) -> None:
        self._load()

    def transcribe(self, audio: AudioData, cancel=None) -> Transcription:
        started = time.perf_counter()
        if audio.duration_seconds < 0.12 or (cancel and cancel.is_set()):
            return Transcription("", duration_ms=int((time.perf_counter() - started) * 1000))
        import numpy as np
        samples = np.asarray(audio.samples, dtype=np.float32)
        if audio.channels > 1:
            samples = samples.reshape(-1, audio.channels).mean(axis=1)
        if self.normalize_audio and len(samples):
            # Remove a small DC offset and lift quiet but non-empty speech.
            # The gain is bounded so an almost-silent device is not turned
            # into full-scale noise and hallucinated as a command.
            samples = samples - float(np.mean(samples))
            peak = float(np.max(np.abs(samples)))
            if 0.001 <= peak < self.target_peak:
                gain = min(self.maximum_gain, self.target_peak / peak)
                samples = np.clip(samples * gain, -1.0, 1.0)
        model = self._load()
        raw_text, info, confidence = self._decode(model, samples, self.beam_size)
        candidates = [(raw_text, confidence, info)]
        uncertain = not raw_text or (confidence is not None and confidence < self.retry_confidence)
        if self.multi_decode and self.maximum_decode_passes > 1 and uncertain and not (cancel and cancel.is_set()):
            retry_text, retry_info, retry_confidence = self._decode(
                model, samples, self.retry_beam_size,
                prompt=(self.initial_prompt + " Use the provided technical names and acronyms exactly.").strip(),
            )
            candidates.append((retry_text, retry_confidence, retry_info))

        # Prefer non-empty text, then measured acoustic certainty. Stable sort
        # keeps the fast first pass when both results are equally convincing.
        selected = max(candidates, key=lambda item: (bool(item[0]), item[1] if item[1] is not None else -1.0))
        raw_text, confidence, info = selected
        text = normalize_transcript(raw_text)
        alternatives = tuple(dict.fromkeys(item[0] for item in candidates if item[0] and item[0] != raw_text))
        probability = confidence
        if probability is None:
            probability = getattr(info, "language_probability", None)
        return Transcription(
            text, getattr(info, "language", None), probability,
            int((time.perf_counter() - started) * 1000), raw_text,
            alternatives, len(candidates),
        )

    def _decode(self, model, samples, beam_size: int, *, prompt: str | None = None):
        segments, info = model.transcribe(
            samples,
            language=None if self.language == "auto" else self.language,
            beam_size=beam_size,
            vad_filter=self.vad_filter,
            condition_on_previous_text=False,
            hotwords=self.hotwords or None,
            initial_prompt=prompt if prompt is not None else (self.initial_prompt or None),
        )
        materialized = list(segments)
        text = " ".join(segment.text.strip() for segment in materialized if segment.text.strip()).strip()
        confidence_values: list[float] = []
        for segment in materialized:
            average_log_probability = getattr(segment, "avg_logprob", None)
            if average_log_probability is None:
                continue
            speech_probability = 1.0 - max(0.0, min(float(getattr(segment, "no_speech_prob", 0.0)), 1.0))
            confidence_values.append(math.exp(max(-10.0, min(float(average_log_probability), 0.0))) * speech_probability)
        confidence = sum(confidence_values) / len(confidence_values) if confidence_values else None
        return text, info, confidence


class WindowsSpeechSTT(SpeechToTextProvider):
    """Zero-download Windows System.Speech fallback for recorded PCM audio."""

    name = "windows_speech"

    def __init__(self, config: dict) -> None:
        self.language = str(config.get("language", "en-US"))

    def transcribe(self, audio: AudioData, cancel=None) -> Transcription:
        started = time.perf_counter()
        if not len(audio.samples):
            return Transcription("", duration_ms=0)
        path = self._write_temporary_wave(audio)
        script = r"""
Add-Type -AssemblyName System.Speech
$culture = [Globalization.CultureInfo]::GetCultureInfo($env:AI_SPEECH_LANGUAGE)
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$recognizer.SetInputToWaveFile($env:AI_SPEECH_AUDIO)
$result = $recognizer.Recognize()
if ($null -ne $result) { [Console]::Out.Write($result.Text) }
$recognizer.Dispose()
"""
        environment = os.environ.copy()
        environment["AI_SPEECH_AUDIO"] = str(path)
        environment["AI_SPEECH_LANGUAGE"] = self.language
        try:
            completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=20, env=environment)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or "Windows speech recognition failed.")
            raw = completed.stdout.strip()
            return Transcription(normalize_transcript(raw), self.language,
                                 duration_ms=int((time.perf_counter() - started) * 1000),
                                 raw_text=raw)
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _write_temporary_wave(audio: AudioData) -> Path:
        import numpy as np
        samples = np.asarray(audio.samples, dtype=np.float32)
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype("<i2")
        handle = tempfile.NamedTemporaryFile(prefix="ai-voice-", suffix=".wav", delete=False)
        handle.close()
        path = Path(handle.name)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(audio.channels)
            output.setsampwidth(2)
            output.setframerate(audio.sample_rate)
            output.writeframes(pcm.tobytes())
        return path


def create_stt_provider(config: dict) -> SpeechToTextProvider:
    provider = str(config.get("provider", "faster_whisper")).lower()
    if provider == "faster_whisper":
        from app.voice.isolated import IsolatedWhisperSTT
        return IsolatedWhisperSTT(config)
    if provider == "windows_speech":
        return WindowsSpeechSTT(config)
    raise ValueError(f"Unsupported STT provider: {provider}")
