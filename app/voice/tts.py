from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.voice.devices import DeviceManager
from app.voice.levels import PlaybackEnvelope
from app.logging import log_swallowed
from app.voice.rendering import bounded, collect_piper, spoken_text

_log = logging.getLogger(__name__)


class TextToSpeechProvider(ABC):
    name = "base"

    @abstractmethod
    def speak(self, text: str, wait: bool = False) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def is_speaking(self) -> bool:
        raise NotImplementedError

    @property
    def envelope(self) -> PlaybackEnvelope:
        """Loudness of the reply now playing, for the voice visual."""
        envelope = self.__dict__.get("_envelope")
        if envelope is None:
            envelope = self.__dict__["_envelope"] = PlaybackEnvelope()
        return envelope


class WindowsSapiTTS(TextToSpeechProvider):
    name = "windows_sapi"

    def __init__(self, config: dict, devices: DeviceManager | None = None) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows SAPI speech is available only on Windows; use Piper.")
        self.voice = str(config.get("voice", "auto"))
        self.speed = float(config.get("speed", 1.0))
        self.volume = float(config.get("volume", 1.0))
        self.devices = devices or DeviceManager()
        self.output_device = self.devices.resolve(config.get("output_device", "auto"), "output")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def is_speaking(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def speak(self, text: str, wait: bool = False) -> None:
        text = " ".join(str(text).split()).strip()
        if not text:
            return
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, args=(text,), name="ai-tts", daemon=True)
        self._thread.start()
        if wait:
            self._thread.join()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            log_swallowed(_log, "stopping audio playback")
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def _worker(self, text: str) -> None:
        handle, name = tempfile.mkstemp(prefix="ai-tts-", suffix=".wav")
        os.close(handle)
        path = Path(name)
        try:
            self._synthesize(text, path)
            # mkstemp leaves an empty file, so "nothing synthesized" is size 0.
            if self._stop.is_set() or not path.exists() or path.stat().st_size == 0:
                return
            self._play(path)
        finally:
            path.unlink(missing_ok=True)

    def _synthesize(self, text: str, path: Path) -> None:
        rate = min(10, max(-10, round((self.speed - 1.0) * 5)))
        volume = min(100, max(0, round(self.volume * 100)))
        script = r"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($env:AI_TTS_VOICE -ne 'auto') { $synth.SelectVoice($env:AI_TTS_VOICE) }
$synth.Rate = [int]$env:AI_TTS_RATE
$synth.Volume = [int]$env:AI_TTS_VOLUME
$synth.SetOutputToWaveFile($env:AI_TTS_OUTPUT)
$synth.Speak($env:AI_TTS_TEXT)
$synth.Dispose()
"""
        environment = os.environ.copy()
        environment.update({"AI_TTS_VOICE": self.voice, "AI_TTS_RATE": str(rate), "AI_TTS_VOLUME": str(volume), "AI_TTS_OUTPUT": str(path), "AI_TTS_TEXT": text})
        with self._lock:
            self._process = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=environment)
            process = self._process
        try:
            _, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise TimeoutError("Windows speech synthesis timed out.") from exc
        finally:
            with self._lock:
                self._process = None
        if process.returncode and not self._stop.is_set():
            raise RuntimeError(stderr.strip() or "Windows speech synthesis failed.")

    def _play(self, path: Path) -> None:
        import numpy as np
        import sounddevice as sd
        with wave.open(str(path), "rb") as source:
            sample_rate = source.getframerate()
            channels = source.getnchannels()
            width = source.getsampwidth()
            frames = source.readframes(source.getnframes())
        if width != 2:
            raise RuntimeError(f"Unsupported synthesized sample width: {width}")
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels)
        self.envelope.start(samples, sample_rate)
        try:
            sd.play(samples, sample_rate, device=self.output_device, blocking=True)
        finally:
            self.envelope.stop()

    @staticmethod
    def list_voices() -> list[str]:
        script = "Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }; $s.Dispose()"
        completed = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=10)
        return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


#: Open-weight voices live here. One directory, so a voice is a file the owner
#: can inspect, copy, or delete rather than something hidden in a system store.
VOICE_ROOT = Path(__file__).resolve().parents[2] / "data" / "voices"

DEFAULT_PIPER_VOICE = "en_US-lessac-medium"


class PiperTTS(TextToSpeechProvider):
    """Speech this project owns: an ONNX voice model, run in-process.

    Windows SAPI works and never sent audio anywhere, but it is Microsoft's
    speech stack reached by spawning PowerShell for every utterance -- which
    ties the assistant's voice to the operating system, costs a process per
    sentence, and offers whatever voices happen to be installed. Piper is MIT,
    the voices are open weights sitting in data/voices, and synthesis happens
    inside this process through onnxruntime.

    Measured on this machine: 6.75s of speech generated in 1.62s, 4.2x faster
    than real time, from a 63MB voice.
    """

    name = "piper"

    def __init__(self, config: dict, devices: DeviceManager | None = None) -> None:
        self.voice_name = str(config.get("voice", DEFAULT_PIPER_VOICE))
        if self.voice_name in {"auto", ""}:
            self.voice_name = DEFAULT_PIPER_VOICE
        self.speed = float(config.get("speed", 1.0))
        self.volume = float(config.get("volume", 1.0))
        self.voice_root = Path(config.get("voice_root", VOICE_ROOT))
        self.sentence_pause = bounded(config.get("sentence_pause", 0.16), 0, 0.5, 0.16)
        self.devices = devices or DeviceManager()
        self.output_device = self.devices.resolve(config.get("output_device", "auto"), "output")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._voice = None
        self._load_lock = threading.Lock()

    # ------------------------------------------------------------- the voice

    @property
    def model_path(self) -> Path:
        return self.voice_root / f"{self.voice_name}.onnx"

    @property
    def installed(self) -> bool:
        return self.model_path.is_file()

    def ensure_voice(self, *, download: bool = True) -> tuple[bool, str]:
        """Make sure the voice model exists locally.

        The download is the one moment this touches the network, and it fetches
        open weights rather than sending anything: no text, no audio, no
        identifier. After it, synthesis is offline forever.
        """
        if self.installed:
            return True, f"{self.voice_name} is present"
        if not download:
            return False, f"{self.voice_name} is not downloaded"
        try:
            from piper.download_voices import download_voice

            self.voice_root.mkdir(parents=True, exist_ok=True)
            download_voice(self.voice_name, self.voice_root)
        except Exception as exc:
            return False, f"could not download the voice {self.voice_name}: {exc}"
        return self.installed, (
            f"downloaded {self.voice_name}" if self.installed else "the download did not produce a model"
        )

    def _load(self):
        """Load once and keep it. Reloading a 63MB model per sentence is waste."""
        with self._load_lock:
            if self._voice is None:
                from piper import PiperVoice

                self._voice = PiperVoice.load(str(self.model_path))
            return self._voice

    # ---------------------------------------------------------------- speech

    @property
    def is_speaking(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def speak(self, text: str, wait: bool = False) -> None:
        # Emoji are fine to read and useless to hear: a synthesiser either
        # says nothing for them or reads out their unicode name. The displayed
        # reply keeps them; only the spoken rendering drops them.
        text = spoken_text(text)
        if not text:
            return
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, args=(text,), name="ai-tts-piper", daemon=True)
        self._thread.start()
        if wait:
            self._thread.join()

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            log_swallowed(_log, "stopping audio playback")
        thread, self._thread = self._thread, None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

    def _worker(self, text: str) -> None:
        try:
            self.synthesize_to_array(text)
        except Exception:
            # A voice failure must never take down the turn that was speaking.
            log_swallowed(_log, "speaking a reply", level=logging.WARNING)

    def synthesize_to_array(self, text: str):
        """Synthesise and play, returning the samples for tests to inspect."""
        import numpy as np

        rendered = collect_piper(self._load(), text, self.speed, getattr(self, "sentence_pause", 0.16), self._stop)
        if rendered is None:
            return None
        samples, rate = rendered
        samples = np.clip(samples * bounded(self.volume, 0, 2, 1), -1.0, 1.0)
        if not self._stop.is_set():
            self._play(samples, rate)
        return samples

    def synthesize_to_file(self, text: str, path: Path) -> Path:
        """Write a wav without playing it. Used by benchmarks and tests."""
        import numpy as np
        rendered = collect_piper(self._load(), text, self.speed, self.sentence_pause)
        if rendered is None:
            raise ValueError("No speech audio was generated.")
        samples, rate = rendered
        samples = np.clip(samples * bounded(self.volume, 0, 2, 1), -1, 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(rate)
            target.writeframes((samples * 32767).astype("<i2").tobytes())
        return path

    def _play(self, samples, sample_rate: int) -> None:
        import sounddevice as sd

        self.envelope.start(samples, sample_rate)
        try:
            sd.play(samples, sample_rate, device=self.output_device, blocking=True)
        finally:
            self.envelope.stop()

    def list_voices(self) -> list[str]:
        """Voices actually present on this disk."""
        if not self.voice_root.is_dir():
            return []
        return sorted(path.stem for path in self.voice_root.glob("*.onnx"))

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name, "voice": self.voice_name,
            "installed": self.installed, "model": str(self.model_path),
            "voices_available": self.list_voices(), "speaking": self.is_speaking,
            "local_only": True, "system_speech": False,
            "sentence_pause": self.sentence_pause, "voice_cloning": False,
        }


class IsolatedPiperTTS(PiperTTS):
    """Native synthesis stays in a restartable process; playback remains local."""

    def __init__(self, config, devices=None):
        super().__init__(config, devices)
        from app.voice.isolated import SpeechWorker, piper_worker
        self._synth = SpeechWorker({**config, "model_path": str(self.model_path)}, worker=piper_worker)

    def close(self):
        self.stop()
        self._synth.close()

    def _synthesize(self, text):
        text = spoken_text(text)
        if len(text) > 8000:
            raise ValueError("Speech synthesis supports at most 8,000 characters per utterance")
        self._synth.reopen()
        speed = bounded(self.speed, 0.5, 2.0, 1.0)
        return self._synth._request("synthesize", (text, speed) if speed != 1.0 else text, self._stop)

    def synthesize_to_array(self, text):
        import numpy as np
        result = self._synthesize(text)
        if not result or not result["samples"]:
            return None
        samples = np.frombuffer(result["samples"], dtype="<f4")
        samples = np.clip(samples * bounded(self.volume, 0, 2, 1), -1, 1)
        if not self._stop.is_set():
            self._play(samples, result["sample_rate"])
        return samples

    def synthesize_to_file(self, text, path):
        import numpy as np
        result = self._synthesize(text)
        if result is None:
            raise RuntimeError("Speech synthesis was cancelled")
        samples = np.frombuffer(result["samples"], dtype="<f4")
        samples = np.clip(samples * bounded(self.volume, 0, 2, 1), -1, 1)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(result["sample_rate"])
            target.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
        return path


def create_tts_provider(config: dict, devices: DeviceManager | None = None) -> TextToSpeechProvider:
    """Build the configured speech provider.

    Piper is the default: it is the one that does not depend on the operating
    system's speech stack. Windows SAPI stays reachable by configuration so a
    machine without a downloaded voice is not left mute, and `auto` falls back
    to it only when no Piper voice is present.
    """
    provider = str(config.get("provider", "piper")).lower()
    if provider == "xtts":
        from app.voice.cloning import LocalVoiceCloneTTS
        return LocalVoiceCloneTTS(config, devices)
    if provider in {"piper", "auto"}:
        piper = IsolatedPiperTTS(config, devices)
        if piper.installed or provider == "piper":
            return piper
        return WindowsSapiTTS(config, devices) if os.name == "nt" else piper
    if provider == "windows_sapi":
        return WindowsSapiTTS(config, devices)
    raise ValueError(f"Unsupported TTS provider: {provider}")


def _foreign_run_pattern():
    from app.languages import SCRIPTS

    letters = "".join(chars for _name, chars, _codes in SCRIPTS)
    return re.compile(f"[{letters}](?:[{letters}\\s\u060C\u061F\u3001\u3002.!:,-]*[{letters}\u061F\u3002.!])?")


_FOREIGN_RUN = _foreign_run_pattern()


def split_scripts(text: str) -> list[tuple[str, str]]:
    """Cut a reply into ("en", ...) runs and runs of another script, in order.

    An English voice given Arabic script reads it out letter by letter
    ("Arabic letter alif, Arabic letter lam..."), which is what Mohsin heard
    when a reply mixed the two. Each run is labelled with its language (from
    the script, and the detector when a script serves several languages) so it
    can go to a voice for that language.
    """
    from app.languages import detect

    parts: list[tuple[str, str]] = []
    cursor = 0
    for match in _FOREIGN_RUN.finditer(text or ""):
        before = text[cursor:match.start()]
        if before.strip(" \t\n()[]\"'«»-–—:,;"):
            parts.append(("en", before))
        run = match.group(0).strip()
        detection = detect(run)
        parts.append((detection.code if detection else "und", run))
        cursor = match.end()
    rest = (text or "")[cursor:]
    if rest.strip(" \t\n()[]\"'«»-–—:,;.!?"):
        parts.append(("en", rest))
    return [(language, " ".join(value.split())) for language, value in parts if value.strip()]


class BilingualTTS(TextToSpeechProvider):
    """English through the main voice, Arabic through the Arabic voice.

    Without an Arabic voice installed the Arabic runs are left out of the
    spoken reply (they are still on screen) rather than spelled out.
    """

    def __init__(self, primary: TextToSpeechProvider, arabic_supplier, *, voice_for=None,
                 language_supplier=None) -> None:
        self.primary = primary
        self.arabic_supplier = arabic_supplier
        #: code -> a voice for that language (None when none is installed).
        self.voice_for = voice_for or (lambda code: None)
        #: The reply language: in another language the whole reply uses its voice.
        self.language_supplier = language_supplier or (lambda: "en")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def name(self) -> str:                      # type: ignore[override]
        return getattr(self.primary, "name", "base")

    @property
    def envelope(self) -> PlaybackEnvelope:
        return self.primary.envelope

    @property
    def speed(self) -> float:
        return float(getattr(self.primary, "speed", 1.0) or 1.0)

    @speed.setter
    def speed(self, value: float) -> None:
        # The main voice reads the reply; the Voice Studio speed control reaches it here.
        if hasattr(self.primary, "speed"):
            self.primary.speed = value

    @property
    def voice_name(self) -> str:
        return str(getattr(self.primary, "voice_name", "") or "")

    def _arabic(self):
        try:
            voice = self.arabic_supplier()
        except Exception:
            log_swallowed(_log, "finding the Arabic voice")
            return None
        return voice if voice is not None and getattr(voice, "available", False) else None

    def _voice(self, code: str):
        try:
            voice = self._arabic() if code == "ar" else self.voice_for(code)
        except Exception:
            log_swallowed(_log, "finding a voice for a language")
            return None
        return voice if voice is not None and getattr(voice, "available", False) else None

    def speak(self, text: str, wait: bool = False) -> None:
        try:
            language = str(self.language_supplier() or "en")
        except Exception:
            language = "en"
        whole = self._voice(language) if language != "en" else None
        if whole is not None:
            # Replying in another language: that voice reads the whole reply.
            plan = [(whole, text)]
        else:
            parts = split_scripts(text)
            if all(code == "en" for code, _ in parts):
                self.primary.speak(text, wait=wait)
                return
            plan = []
            from app.languages import script_of

            for code, value in parts:
                voice = self.primary if code == "en" else self._voice(code)
                if voice is None and code != "en":
                    # One word of Devanagari may read as Nepali; a Hindi voice still says it right.
                    script = script_of(value)
                    voice = next((found for other in (script[1] if script else ()) if (found := self._voice(other))), None)
                if voice is not None:            # no voice for it: left out, never spelled
                    plan.append((voice, value))
            if all(voice is self.primary for voice, _ in plan):
                english = " ".join(value for _, value in plan)
                if english:
                    self.primary.speak(english, wait=wait)
                return
        self.stop()
        self._stop.clear()

        def run() -> None:
            for voice, value in plan:
                if self._stop.is_set():
                    return
                try:
                    voice.speak(value, wait=True)
                except Exception:
                    log_swallowed(_log, "speaking one part of a mixed-language reply", level=logging.WARNING)

        self._thread = threading.Thread(target=run, name="ai-tts-multilingual", daemon=True)
        self._thread.start()
        if wait:
            self._thread.join()

    def stop(self) -> None:
        self._stop.set()
        self.primary.stop()
        arabic = self._arabic()
        if arabic is not None:
            arabic.stop()
        try:
            language = str(self.language_supplier() or "en")
            other = self.voice_for(language) if language not in {"en", "ar"} else None
            if other is not None:
                other.stop()
        except Exception:
            log_swallowed(_log, "stopping a language voice")
        thread, self._thread = self._thread, None
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

    @property
    def is_speaking(self) -> bool:
        arabic = self._arabic()
        return bool((self._thread and self._thread.is_alive()) or self.primary.is_speaking
                    or (arabic is not None and arabic.is_speaking))

    def __getattr__(self, name: str):
        # status(), list_voices(), close(), synthesize_* ... belong to the main voice.
        if name == "primary":
            raise AttributeError(name)
        return getattr(self.primary, name)


#: The same class; the name kept for the Arabic-only callers and tests.
MultilingualTTS = BilingualTTS
