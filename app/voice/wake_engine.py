"""A low-power wake-word gate using openWakeWord.

The default wake mode transcribes every utterance with Whisper and looks for
"hey hayat" in the text: accurate, but it runs speech recognition on
everything said near the microphone. openWakeWord scores 80 ms frames with a
tiny model instead, and only after it fires is the command recorded and
transcribed. Nothing is kept or transcribed before that.

There is no pre-trained "hey hayat" model. The free pre-trained ones are
phrases such as "hey jarvis", "alexa" and "hey mycroft"; a custom phrase needs
a model trained with openWakeWord's own tooling, whose .onnx file can then be
named in ``voice.wake_word.model``. The engine is opt-in and falls back to the
phrase mode, with the reason, whenever its model is missing.
"""
from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any, Callable

PRETRAINED = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy")


def _models_dir() -> Path | None:
    try:
        import openwakeword
    except ImportError:
        return None
    return Path(openwakeword.__file__).parent / "resources" / "models"


class OpenWakeWordGate:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5,
                 factory: Callable[..., Any] | None = None) -> None:
        self.model = str(model or "hey_jarvis").strip()
        self.threshold = max(0.05, min(float(threshold), 0.99))
        self._factory = factory
        self._engine: Any = None

    def _model_file(self) -> Path | None:
        if self.model.lower().endswith(".onnx"):
            path = Path(self.model).expanduser()
            return path if path.is_file() else None
        directory = _models_dir()
        if directory is None:
            return None
        found = sorted(glob.glob(str(directory / f"{self.model}_v*.onnx")))
        feature = directory / "melspectrogram.onnx"
        return Path(found[-1]) if found and feature.is_file() else None

    def ready(self) -> tuple[bool, str]:
        if self._factory is not None:
            return True, "ready"
        if _models_dir() is None:
            return False, "openWakeWord is not installed"
        if self._model_file() is None:
            return False, (f"the '{self.model}' wake model is not downloaded; run "
                           f"python -m scripts.setup_wakeword {self.model}")
        return True, "ready"

    def detector(self) -> Callable[[Any], bool]:
        """A fresh scorer: frame of int16 samples -> heard the wake word."""
        if self._engine is None:
            if self._factory is not None:
                self._engine = self._factory()
            else:
                from openwakeword.model import Model

                target = str(self._model_file() or self.model)
                self._engine = Model(wakeword_models=[target], inference_framework="onnx")
        engine = self._engine
        engine.reset()
        return lambda frame: max(engine.predict(frame).values(), default=0.0) >= self.threshold


def download(model: str = "hey_jarvis") -> list[str]:
    """Fetch the shared feature models and one pre-trained wake model."""
    from openwakeword.utils import download_models

    if model not in PRETRAINED:
        raise ValueError(f"{model} is not a pre-trained openWakeWord model; choose one of {', '.join(PRETRAINED)}")
    download_models(model_names=[model])
    directory = _models_dir()
    return sorted(os.path.basename(path) for path in glob.glob(str(directory / "*.onnx"))) if directory else []
