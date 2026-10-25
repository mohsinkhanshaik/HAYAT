"""Live loudness for on-screen display only.

Nothing here keeps audio. The microphone meter holds one number, the most
recent block's loudness; the playback envelope holds one loudness value per
40 ms of the reply currently being spoken and forgets it when playback ends.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

#: Loudness below this reads as silence; at the top it reads as full scale.
#: Speech at a normal distance from a laptop microphone lands mid-range.
FLOOR_DB, CEILING_DB = -60.0, -12.0


def loudness(rms: float) -> float:
    """Map RMS amplitude to 0..1 on a decibel scale, as ears hear it."""
    if not math.isfinite(rms) or rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return max(0.0, min(1.0, (db - FLOOR_DB) / (CEILING_DB - FLOOR_DB)))


class LevelMeter:
    """The latest input loudness, fading to zero once blocks stop arriving.

    Written from the audio callback thread and read from the GUI thread; a
    tuple assignment is atomic in CPython, so no lock sits on the audio path.
    """

    #: A closed stream must not leave a frozen level on screen.
    HOLD_SECONDS = .25

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._sample: tuple[float, float] = (0.0, 0.0)

    def push(self, block: Any) -> None:
        try:
            import numpy as np

            samples = np.asarray(block, dtype=np.float32).reshape(-1)
            rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        except Exception:
            return
        self._sample = (loudness(rms), self._clock())

    def read(self) -> float:
        level, at = self._sample
        age = self._clock() - at
        if age <= self.HOLD_SECONDS:
            return level
        return level * math.exp(-(age - self.HOLD_SECONDS) * 8)


class PlaybackEnvelope:
    """Loudness of the reply being spoken, looked up by elapsed play time."""

    WINDOW_SECONDS = .04

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._levels: list[float] = []
        self._started = 0.0

    def start(self, samples: Any, sample_rate: int) -> None:
        levels: list[float] = []
        try:
            import numpy as np

            mono = np.asarray(samples, dtype=np.float32)
            if mono.ndim > 1:
                mono = mono.mean(axis=1)
            step = max(1, int(sample_rate * self.WINDOW_SECONDS))
            usable = len(mono) - len(mono) % step
            if usable:
                windows = mono[:usable].reshape(-1, step)
                levels = [loudness(float(value)) for value in np.sqrt(np.mean(np.square(windows), axis=1))]
        except Exception:
            levels = []
        with self._lock:
            self._levels, self._started = levels, self._clock()

    def stop(self) -> None:
        with self._lock:
            self._levels = []

    def read(self) -> float:
        with self._lock:
            levels, started = self._levels, self._started
        if not levels:
            return 0.0
        index = int((self._clock() - started) / self.WINDOW_SECONDS)
        return levels[index] if 0 <= index < len(levels) else 0.0
