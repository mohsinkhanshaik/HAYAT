from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class VoiceActivity:
    is_speech: bool
    rms: float
    threshold: float


class EnergyVoiceActivityDetector:
    """Small local VAD with an adaptive noise floor and no cloud dependency."""

    def __init__(self, energy_threshold: float = 0.0035, adaptation: float = 0.04) -> None:
        self.minimum_threshold = max(0.001, float(energy_threshold))
        self.noise_floor = self.minimum_threshold / 3.0
        self.adaptation = min(0.25, max(0.001, adaptation))
        #: Recent frame levels (about two seconds of 50 ms frames).
        self._recent: deque[float] = deque(maxlen=40)

    def set_threshold(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value) or not 0.001 <= value <= 0.05:
            raise ValueError("Speech threshold must be between 0.001 and 0.05.")
        self.minimum_threshold = value
        self.noise_floor = value / 3.0
        self._recent.clear()

    def analyze(self, samples: Any) -> VoiceActivity:
        if len(samples) == 0:
            return VoiceActivity(False, 0.0, self.minimum_threshold)
        squares = (float(value) * float(value) for value in samples)
        rms = math.sqrt(sum(squares) / len(samples))
        # Laptop array microphones commonly sit near 0.0025 RMS at idle. A
        # 0.006 hard floor rejected quiet speech and acoustic echo-cancelled
        # syllables. The adaptive floor still rises above steady background
        # noise, while the smaller multiplier keeps normal speech reachable.
        threshold = max(self.minimum_threshold, self.noise_floor * 1.8)
        is_speech = rms >= threshold
        self._recent.append(rms)
        if not is_speech:
            self.noise_floor = ((1.0 - self.adaptation) * self.noise_floor) + (self.adaptation * rms)
        elif len(self._recent) >= 10:
            # The floor used to learn only from frames already judged silent,
            # so a fan or a TV louder than the first threshold counted as
            # speech forever and hands-free never stopped listening. The
            # quietest recent frames are noise whatever else is happening, so
            # the floor now rises slowly towards them (minimum statistics).
            quiet = sorted(self._recent)[len(self._recent) // 10]
            if quiet > self.noise_floor:
                self.noise_floor += 0.03 * (quiet - self.noise_floor)
        return VoiceActivity(is_speech, rms, threshold)
