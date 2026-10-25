"""Readable spoken text and bounded local speech audio."""
from __future__ import annotations

import math
import re
from typing import Any

from app.intent.emoji import for_speech


def spoken_text(text: str) -> str:
    text = "\n".join(for_speech(line) for line in str(text or "").splitlines())
    text = re.sub(r"\[([^\]\n]+)\]\(https?://[^\s)]+\)", r"\1", text)
    text = re.sub(r"https?://[^\s<>]+", "link on screen", text)
    text = re.sub(r"(?m)^\s*```[^\n]*$", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    # Give headings and list items a breath, without splitting decimals or paths.
    return " ".join(line if index == len(lines) - 1 or line[-1:] in ".!?;:،。！？" else line + "."
                    for index, line in enumerate(lines)).strip()


def bounded(value: Any, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number)) if math.isfinite(number) else default


def collect_piper(voice: Any, text: str, speed: float, pause: float = 0.16, cancel: Any = None):
    import numpy as np
    speed = bounded(speed, 0.5, 2.0, 1.0)
    pause = bounded(pause, 0.0, 0.5, 0.16)
    options = {}
    if speed != 1.0:
        from piper import SynthesisConfig
        options["syn_config"] = SynthesisConfig(length_scale=1 / speed)
    chunks, rate, length = [], None, 0
    for chunk in voice.synthesize(spoken_text(text), **options):
        if cancel is not None and cancel.is_set():
            return None
        current_rate = int(getattr(chunk, "sample_rate", 22050))
        if not 8000 <= current_rate <= 192000 or rate is not None and rate != current_rate:
            raise ValueError("Speech output has an invalid or changing sample rate.")
        rate = current_rate
        audio = getattr(chunk, "audio_float_array", None)
        if audio is None:
            audio = np.frombuffer(getattr(chunk, "audio_int16_bytes", b""), dtype="<i2").astype(np.float32) / 32768
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1 or not np.isfinite(audio).all():
            raise ValueError("Speech output must be finite mono audio.")
        if not len(audio):
            continue
        if chunks and pause:
            silence = np.zeros(round(rate * pause), dtype=np.float32)
            chunks.append(silence); length += len(silence)
        length += len(audio)
        if length > min(rate * 600, 16_000_000):
            raise ValueError("Synthesized speech exceeds output limits.")
        chunks.append(audio)
    if not chunks:
        return None
    return np.clip(np.concatenate(chunks), -1, 1), rate
