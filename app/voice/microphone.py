from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable

from app.voice.devices import DeviceManager
from app.voice.levels import LevelMeter
from app.voice.models import AudioData
from app.voice.vad import EnergyVoiceActivityDetector


class MicrophoneRecorder:
    def __init__(self, config: dict, devices: DeviceManager | None = None, vad: EnergyVoiceActivityDetector | None = None) -> None:
        self.config = config
        self.devices = devices or DeviceManager()
        self.sample_rate = int(config.get("sample_rate", 16000))
        self.channels = int(config.get("channels", 1))
        self.maximum_seconds = float(config.get("maximum_seconds", 30))
        self.minimum_push_seconds = max(0.1, float(config.get("minimum_push_seconds", 0.4)))
        self.minimum_utterance_seconds = max(0.2, min(float(config.get("minimum_utterance_seconds", 1.5)), 4.0))
        self.pre_roll_seconds = max(0.0, min(float(config.get("pre_roll_seconds", 0.5)), 2.0))
        self.speech_start_chunks = max(1, min(int(config.get("speech_start_chunks", 3)), 8))
        #: Hands-free: once speech has started, a frame quieter than this share
        #: of the speaker's own level counts as silence, even if steady
        #: background noise is above the detector's threshold.
        self.release_ratio = max(0.05, min(float(config.get("release_ratio", 0.2)), 0.6))
        #: Hands-free: the longest one utterance may run before it is cut and
        #: transcribed, so continuous noise can never hold the microphone open.
        self.max_utterance_seconds = max(3.0, min(float(config.get("max_utterance_seconds", 15)), 60.0))
        self.device = self.devices.resolve(config.get("device", "auto"), "input")
        self.vad = vad or EnergyVoiceActivityDetector()
        self.last_capture: dict = {"captured": False, "reason": "not_started"}
        self._meter: LevelMeter | None = None

    @property
    def meter(self) -> LevelMeter:
        """Display-only loudness of the open stream; holds no audio.

        Created on first use so an audio callback never fails over it.
        """
        meter = self.__dict__.get("_meter")
        if meter is None:
            meter = self.__dict__["_meter"] = LevelMeter()
        return meter

    @meter.setter
    def meter(self, value: LevelMeter) -> None:
        self._meter = value

    @staticmethod
    def _sounddevice():
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Microphone support is unavailable. Run .\\setup.ps1.") from exc
        return sd

    def record_while(self, keep_recording: Callable[[], bool], cancel: threading.Event | None = None) -> AudioData:
        self._require_input()
        sd = self._sounddevice()
        import numpy as np

        chunks: list = []
        started = 0.0

        stream_status: list[str] = []

        def callback(indata, frames, timestamp, status) -> None:
            if status:
                stream_status.append(str(status))
            self.meter.push(indata)
            chunks.append(indata.copy())

        with sd.InputStream(samplerate=self.sample_rate, channels=self.channels, dtype="float32", device=self.device, callback=callback, blocksize=800):
            started = time.monotonic()
            while not (cancel and cancel.is_set()):
                elapsed = time.monotonic() - started
                if elapsed >= self.maximum_seconds:
                    break
                # Opening a Windows stream takes long enough that a quick key
                # release used to end capture before the first callback.
                if elapsed >= self.minimum_push_seconds and not keep_recording():
                    break
                time.sleep(0.01)
        samples = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, self.channels), dtype=np.float32)
        audio = AudioData(samples.reshape(-1) if self.channels == 1 else samples, self.sample_rate, self.channels)
        self.last_capture = self._capture_summary(audio, speech_started=bool(len(audio.samples)), stream_status=stream_status, mode="push_to_talk")
        return audio

    def listen_for(self, detected: Callable, cancel: threading.Event | None = None,
                   timeout: float | None = None, frame_samples: int = 1280) -> bool:
        """Stream 16-bit frames to ``detected`` until it fires.

        For a wake-word engine: nothing is kept or transcribed, each 80 ms
        frame is scored and dropped. Returns False on cancel or timeout.
        """
        self._require_input()
        sd = self._sounddevice()
        import numpy as np

        frames: queue.Queue = queue.Queue(maxsize=64)

        def callback(indata, count, timestamp, status) -> None:
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self.meter.push(mono)
            try:
                frames.put_nowait((np.clip(mono, -1.0, 1.0) * 32767).astype(np.int16).copy())
            except queue.Full:
                pass  # scoring fell behind; dropping a frame beats growing without bound

        deadline = None if timeout is None else time.monotonic() + timeout
        with sd.InputStream(samplerate=self.sample_rate, channels=self.channels, dtype="float32",
                            device=self.device, callback=callback, blocksize=frame_samples):
            while not (cancel and cancel.is_set()):
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                try:
                    frame = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if detected(frame):
                    return True
        return False

    def record_until_silence(
        self,
        cancel: threading.Event | None = None,
        speech_start_timeout: float = 5.0,
        silence_seconds: float = 0.8,
    ) -> AudioData:
        self._require_input()
        sd = self._sounddevice()
        import numpy as np

        audio_queue: queue.Queue = queue.Queue()
        captured: list = []
        pre_roll: deque = deque(maxlen=max(1, int(self.pre_roll_seconds * self.sample_rate / 800)))
        started = time.monotonic()
        last_speech: float | None = None
        first_speech: float | None = None
        stream_status: list[str] = []
        peak = 0.0
        speech_run = 0
        speech_level = 0.0
        ended_by = "silence"

        def callback(indata, frames, timestamp, status) -> None:
            if status:
                stream_status.append(str(status))
            self.meter.push(indata)
            audio_queue.put(indata.copy())

        with sd.InputStream(samplerate=self.sample_rate, channels=self.channels, dtype="float32", device=self.device, callback=callback, blocksize=800):
            while not (cancel and cancel.is_set()):
                elapsed = time.monotonic() - started
                if elapsed >= self.maximum_seconds:
                    break
                try:
                    chunk = audio_queue.get(timeout=0.15)
                except queue.Empty:
                    now = time.monotonic()
                    if (last_speech is not None and first_speech is not None
                            and now - last_speech >= silence_seconds
                            and now - first_speech >= self.minimum_utterance_seconds):
                        break
                    continue
                peak = max(peak, float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
                activity = self.vad.analyze(chunk.reshape(-1))
                if activity.is_speech:
                    if last_speech is None:
                        speech_run += 1
                        speech_level = max(speech_level, activity.rms)
                        if speech_run >= self.speech_start_chunks:
                            captured.extend(pre_roll)
                            pre_roll.clear()
                            first_speech = time.monotonic()
                            last_speech = first_speech
                    elif activity.rms >= self.release_ratio * speech_level:
                        # Still the speaker, not the room: the level stays near their own.
                        last_speech = time.monotonic()
                        speech_level = 0.9 * speech_level + 0.1 * activity.rms
                elif last_speech is None:
                    # A click or a single noise frame is not an utterance.
                    speech_run = 0
                if last_speech is not None:
                    captured.append(chunk)
                    now = time.monotonic()
                    if (first_speech is not None and now - last_speech >= silence_seconds
                            and now - first_speech >= self.minimum_utterance_seconds):
                        break
                    if first_speech is not None and now - first_speech >= self.max_utterance_seconds:
                        ended_by = "length_cap"
                        break
                else:
                    pre_roll.append(chunk)
                    if elapsed >= speech_start_timeout:
                        break
        samples = np.concatenate(captured, axis=0) if captured else np.zeros((0, self.channels), dtype=np.float32)
        audio = AudioData(samples.reshape(-1) if self.channels == 1 else samples, self.sample_rate, self.channels)
        self.last_capture = self._capture_summary(audio, speech_started=last_speech is not None, stream_status=stream_status, mode="silence_bounded")
        self.last_capture["observed_peak"] = round(peak, 6)
        self.last_capture["speech_start_chunks"] = self.speech_start_chunks
        self.last_capture["ended_by"] = ended_by if last_speech is not None else "no_speech"
        self.last_capture["noise_floor"] = round(float(getattr(self.vad, "noise_floor", 0.0)), 6)
        return audio

    def probe(self, seconds: float = 0.25) -> dict:
        self._require_input()
        sd = self._sounddevice()
        import numpy as np
        frames = max(1, int(max(0.1, seconds) * self.sample_rate))
        samples = sd.rec(frames, samplerate=self.sample_rate, channels=self.channels, dtype="float32", device=self.device, blocking=True)
        audio = AudioData(samples.reshape(-1) if self.channels == 1 else samples, self.sample_rate, self.channels)
        peak = float(np.max(np.abs(audio.samples))) if len(audio.samples) else 0.0
        result = {"captured": len(audio.samples) > 0, "duration_seconds": round(audio.duration_seconds, 3), "sample_rate": audio.sample_rate, "peak": round(peak, 6), "device": self.device}
        self.last_capture = result | {"mode": "probe", "reason": "ok" if peak > 0 else "silent_input"}
        return result

    def _require_input(self) -> None:
        diagnostics = self.devices.diagnose_input(include_windows=False)
        if diagnostics["available"]:
            return
        self.last_capture = diagnostics | {"captured": False, "reason": "no_input_device"}
        raise RuntimeError(
            "No Windows recording device is available (default input is -1). "
            "Enable or repair the microphone/audio controller, then run /voice devices and /voice probe."
        )

    def _capture_summary(self, audio: AudioData, *, speech_started: bool, stream_status: list[str], mode: str) -> dict:
        import numpy as np

        samples = np.asarray(audio.samples, dtype=np.float32)
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
        reason = "ok" if speech_started and len(samples) else "no_speech_detected"
        return {
            "captured": bool(len(samples)), "speech_started": speech_started,
            "duration_seconds": round(audio.duration_seconds, 3), "peak": round(peak, 6),
            "rms": round(rms, 6), "sample_rate": audio.sample_rate, "device": self.device,
            "mode": mode, "reason": reason, "stream_status": list(dict.fromkeys(stream_status)),
        }
