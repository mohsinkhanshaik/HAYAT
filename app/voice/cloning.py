"""Optional local XTTS reference voices, isolated from the desktop process."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
import wave

from app.voice.isolated import SpeechWorker
from app.voice.tts import IsolatedPiperTTS, PiperTTS
from app.config import PROJECT_ROOT

LANGUAGES = {"ar", "zh-cn", "cs", "nl", "en", "fr", "de", "hi", "hu", "it", "ja", "ko", "pl", "pt", "ru", "es", "tr"}
_xtts_dll_handles = []


def xtts_dependencies() -> Path:
    return PROJECT_ROOT / 'runtime' / 'xtts-deps'


def load_xtts_types():
    """Called only in the speech child: keep Coqui's Transformers version isolated."""
    directory = xtts_dependencies()
    if (directory / 'transformers' / '__init__.py').is_file() and str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    libraries = PROJECT_ROOT / 'runtime' / 'ffmpeg-shared' / 'bin'
    if os.name == 'nt' and libraries.is_dir() and not _xtts_dll_handles:
        _xtts_dll_handles.append(os.add_dll_directory(str(libraries)))
        os.environ['PATH'] = str(libraries) + os.pathsep + os.environ.get('PATH', '')
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    return XttsConfig, Xtts


def reference_info(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Choose an existing WAV recording under 16 MB.")
    with wave.open(str(path), "rb") as source:
        rate, frames = source.getframerate(), source.getnframes()
        seconds = frames / rate if rate else 0
        if (source.getcomptype() != "NONE" or source.getsampwidth() != 2 or source.getnchannels() not in (1, 2)
                or not 16000 <= rate <= 48000 or not 3 <= seconds <= 60):
            raise ValueError("Reference voice needs 3–60 seconds of 16-bit mono/stereo WAV at 16–48 kHz.")
        raw = source.readframes(frames)
        if len(raw) != frames * source.getnchannels() * 2:
            raise ValueError("Reference recording is truncated.")
        import numpy as np
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
        if float(np.sqrt(np.mean(samples ** 2))) < 0.002:
            raise ValueError("Reference recording is silent or too quiet. Record clear speech.")
    return {"seconds": round(seconds, 2), "sample_rate": rate}


class LocalVoiceCloneTTS(IsolatedPiperTTS):
    name = "xtts"

    def __init__(self, config, devices=None):
        self.checkpoint = Path(config.get("checkpoint_dir") or "data/voices/xtts").expanduser().resolve()
        self.reference = Path(config.get("reference_audio") or "data/voices/reference.wav").expanduser().resolve()
        self.language = str(config.get("language", "en"))
        self.speaker_consent = config.get("speaker_consent") is True
        self._verified = False
        if self.language not in LANGUAGES:
            raise ValueError("Unsupported XTTS language.")
        PiperTTS.__init__(self, {**config, "voice": config.get("speaker_name") or "Reference voice"}, devices)
        self._synth = SpeechWorker({**config, "checkpoint_dir": str(self.checkpoint),
                                   "reference_audio": str(self.reference), "language": self.language}, worker=xtts_worker)

    @property
    def model_path(self):
        return self.checkpoint / "model.pth"

    @property
    def installed(self):
        return not self.blockers()

    def blockers(self):
        reasons = []
        if not self.speaker_consent:
            reasons.append("Confirm that the recording is yours or the speaker agreed to this use.")
        if importlib.util.find_spec("TTS") is None:
            reasons.append("The optional coqui-tts package is not installed in this Python environment.")
        for filename in ("config.json", "model.pth", "vocab.json"):
            if not (self.checkpoint / filename).is_file():
                reasons.append(f"Missing local XTTS checkpoint file: {filename}.")
        try:
            reference_info(self.reference)
        except (OSError, ValueError, wave.Error) as exc:
            reasons.append(str(exc))
        return reasons

    def ensure_voice(self, *, download=True):
        reasons = self.blockers()
        return not reasons, " ".join(reasons) if reasons else "Local reference voice is configured; synthesis has not yet been tested."

    def list_voices(self):
        return [self.voice_name] if self.installed else []

    def status(self):
        reasons = self.blockers()
        return {"provider": self.name, "voice": self.voice_name, "installed": not reasons,
                "available": not reasons, "blockers": reasons, "model": str(self.model_path),
                "reference_audio": str(self.reference), "language": self.language,
                "speaking": self.is_speaking, "local_only": True, "system_speech": False,
                "voice_cloning": True, "speaker_consent": self.speaker_consent,
                "synthesis_verified": self._verified}

    def _synthesize(self, text):
        reasons = self.blockers()
        if reasons:
            raise RuntimeError(" ".join(reasons))
        from app.resources import heavy
        with heavy("local reference voice", needs_bytes=self.model_path.stat().st_size):
            result = super()._synthesize(text)
        if result is not None and result.get("samples"):
            self._verified = True
        return result


def xtts_worker(connection, config):
    # No model-name lookup, downloads, hosted synthesis or audio export.
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    model, latent, embedding = None, None, None
    try:
        while True:
            _, request = connection.recv()
            try:
                import numpy as np
                XttsConfig, Xtts = load_xtts_types()
                from app.voice.rendering import bounded
                if model is None:
                    settings = XttsConfig()
                    settings.load_json(str(Path(config["checkpoint_dir"]) / "config.json"))
                    model = Xtts.init_from_config(settings)
                    model.load_checkpoint(settings, checkpoint_dir=config["checkpoint_dir"], use_deepspeed=False)
                    model.to(config.get("device", "cpu"))
                    latent, embedding = model.get_conditioning_latents(audio_path=[config["reference_audio"]])
                text, speed = request if isinstance(request, tuple) else (request, 1.0)
                result = model.inference(text, config["language"], latent, embedding,
                                         speed=bounded(speed, 0.5, 2, 1), enable_text_splitting=True)
                samples = np.asarray(result["wav"], dtype="<f4")
                if samples.ndim != 1 or not len(samples) or len(samples) > 14_400_000 or not np.isfinite(samples).all():
                    raise ValueError("XTTS did not return valid, bounded mono speech.")
                connection.send({"result": {"samples": np.clip(samples, -1, 1).tobytes(), "sample_rate": 24000}})
            except Exception as exc:
                connection.send({"error": f"{type(exc).__name__}: {exc}"})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()
