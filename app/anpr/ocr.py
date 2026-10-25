"""Reading the characters on a detected plate.

Engines, best first: fast-plate-ocr (an ONNX model trained only on plates,
MIT), EasyOCR (general text, PyTorch; already in HAYAT), and PaddleOCR when the
owner installs it. Each returns the text and a confidence from 0 to 1.
Models are stored in HAYAT's data folder.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger("hayat.anpr")
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FAST_PLATE_MODEL = "cct-s-v2-global-model"


@dataclass
class OcrResult:
    text: str
    confidence: float
    engine: str
    #: The plate's country/region as fast-plate-ocr's global model sees it, when it says.
    region: str = ""
    region_confidence: float = 0.0


def prepare(crop: Any, height: int = 96) -> Any:
    """A plate crop scaled to a readable height, contrast-equalised, as BGR."""
    import cv2
    if crop is None or crop.size == 0:
        return crop
    scale = height / max(1, crop.shape[0])
    if scale > 1:
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * scale)), height), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


class FastPlateOcr:
    name = "fast-plate-ocr (ONNX)"

    def __init__(self, model_dir: Path, use_gpu: str = "auto") -> None:
        import fast_plate_ocr.inference.hub as hub
        from fast_plate_ocr import LicensePlateRecognizer  # noqa: F401 -- a missing package fails here
        from app.anpr.detector import onnx_providers
        model_dir.mkdir(parents=True, exist_ok=True)
        hub.MODEL_CACHE_DIR = model_dir  # not ~/.cache: HAYAT keeps its models in its own folder
        self.rebuild(onnx_providers(use_gpu))

    def rebuild(self, providers: list[Any]) -> None:
        from fast_plate_ocr import LicensePlateRecognizer
        from app.anpr.detector import session_device
        self._model = LicensePlateRecognizer(FAST_PLATE_MODEL, providers=providers)
        self.device = session_device(self._model)

    def read(self, crop: Any) -> OcrResult:
        from app.anpr.detector import with_cpu_fallback
        output = with_cpu_fallback(self, lambda: self._model.run(crop, return_confidence=True))
        text, confidence = _fast_plate_output(output)
        result = OcrResult(text, confidence, self.name)
        first = output[0] if output else None
        if getattr(first, "region", None):
            result.region, result.region_confidence = str(first.region), float(getattr(first, "region_prob", 0) or 0)
        return result


def _fast_plate_output(output: Any) -> tuple[str, float]:
    """fast-plate-ocr has returned plain strings, (texts, probabilities) and prediction objects across versions."""
    import numpy as np
    if isinstance(output, tuple) and len(output) == 2:
        texts, probabilities = output
        text = texts[0] if isinstance(texts, (list, tuple)) else str(texts)
        probs = np.asarray(probabilities[0] if len(np.shape(probabilities)) > 1 else probabilities, dtype=float)
        visible = [p for p, char in zip(probs, text) if char != "_"] or list(probs)
        return text.replace("_", ""), float(np.mean(visible)) if visible else 0.0
    first = output[0] if isinstance(output, (list, tuple)) and output else output
    if hasattr(first, "plate"):
        probs = getattr(first, "char_probs", None)
        value = float(np.mean(probs)) if probs is not None and len(probs) else .8
        return str(first.plate).replace("_", ""), value
    return str(first).replace("_", ""), .8


class EasyOcrEngine:
    name = "EasyOCR"

    def __init__(self, model_dir: Path, use_gpu: str = "auto") -> None:
        import easyocr
        model_dir.mkdir(parents=True, exist_ok=True)
        gpu = False
        if use_gpu != "off":
            try:
                import torch
                gpu = torch.cuda.is_available()
            except ImportError:
                gpu = False
        self.device = "GPU" if gpu else "CPU"
        self._reader = easyocr.Reader(["en"], gpu=gpu, model_storage_directory=str(model_dir),
                                      user_network_directory=str(model_dir), verbose=False)

    def read(self, crop: Any) -> OcrResult:
        results = self._reader.readtext(prepare(crop), allowlist=ALNUM, detail=1, paragraph=False)
        text, confidence = assemble(results)
        return OcrResult(text, confidence, self.name)


def assemble(results: list[Any]) -> tuple[str, float]:
    """EasyOCR boxes to plate text: the main characters only, line by line, left to right.

    Small print on and around a plate ("IND", a dealer's name, a serial) is
    dropped by height; the rest is grouped into lines by vertical centre, which
    keeps an angled plate on one line and reads a two-line plate top first.
    """
    if not results:
        return "", 0.0
    boxes = []
    for points, text, confidence in results:
        ys, xs = [point[1] for point in points], [point[0] for point in points]
        boxes.append({"text": text, "confidence": float(confidence), "x": min(xs), "y": (min(ys) + max(ys)) / 2,
                      "height": max(ys) - min(ys)})
    tallest = max(box["height"] for box in boxes) or 1
    main = [box for box in boxes if box["height"] >= .55 * tallest]
    lines: list[list[dict[str, Any]]] = []
    for box in sorted(main, key=lambda item: item["y"]):
        if lines and abs(box["y"] - sum(item["y"] for item in lines[-1]) / len(lines[-1])) < .5 * tallest:
            lines[-1].append(box)
        else:
            lines.append([box])
    text = "".join(box["text"] for line in lines for box in sorted(line, key=lambda item: item["x"]))
    confidence = sum(box["confidence"] for box in main) / len(main)
    return re.sub(r"[^A-Z0-9]", "", text.upper()), confidence


class PaddleOcrEngine:
    name = "PaddleOCR"

    def __init__(self, model_dir: Path, use_gpu: str = "auto") -> None:
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(use_angle_cls=False, lang="en")

    def read(self, crop: Any) -> OcrResult:
        output = self._ocr.ocr(prepare(crop)) or []
        texts, scores = [], []
        for line in output[0] or [] if output else []:
            texts.append(line[1][0])
            scores.append(float(line[1][1]))
        return OcrResult(re.sub(r"[^A-Z0-9]", "", "".join(texts).upper()), sum(scores) / len(scores) if scores else 0.0,
                         self.name)


ENGINES = {"fast-plate-ocr": FastPlateOcr, "easyocr": EasyOcrEngine, "paddleocr": PaddleOcrEngine}


def make_ocr(model_dir: Path, engine: str = "auto", use_gpu: str = "auto") -> Any:
    """The chosen engine, or the first that loads (fast-plate-ocr, then EasyOCR, then PaddleOCR)."""
    order = [engine] if engine in ENGINES else ["fast-plate-ocr", "easyocr", "paddleocr"]
    errors = []
    for name in order:
        try:
            return ENGINES[name](model_dir / name, use_gpu)
        except Exception as exc:  # not installed, or its model could not be loaded
            log_swallowed(_log, f"loading OCR engine {name}")
            errors.append(f"{name}: {type(exc).__name__}: {str(exc)[:120]}")
    raise RuntimeError("No OCR engine could be loaded. " + " | ".join(errors))
