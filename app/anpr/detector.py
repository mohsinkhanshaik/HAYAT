"""Licence-plate detection: a YOLO model through ONNX Runtime, or an OpenCV fallback.

The YOLO detector is open-image-models' YOLOv9 licence-plate model (MIT),
run by ONNX Runtime: on the GPU when a CUDA provider is installed, otherwise
on the CPU. Its weights are kept in HAYAT's data folder. When the package or
model is missing, a classical detector (edges, rectangular contours with a
plate's aspect ratio) still finds clear, front-on plates, so the system works
before anything is downloaded; the dashboard says which detector is running.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger("hayat.anpr")
#: The larger, more accurate of the package's plate detectors (608 px input).
YOLO_MODEL = "yolo-v9-s-608-license-plate-end2end"


@dataclass
class Detection:
    box: tuple[int, int, int, int]   # x1, y1, x2, y2 in frame pixels
    confidence: float
    label: str = "license_plate"

    @property
    def width(self) -> int:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]


#: GPU memory one of HAYAT's vision models may take. The brain model shares the owner's 8 GB card,
#: so these models stay small guests on it (each needs well under 1 GB).
GPU_MEMORY_LIMIT = 1 << 30
_cuda_ready: bool | None = None


def _cuda_libraries() -> bool:
    """Load the CUDA and cuDNN libraries that pip installed beside onnxruntime-gpu (once; Windows needs this)."""
    global _cuda_ready
    if _cuda_ready is None:
        try:
            import onnxruntime
            if hasattr(onnxruntime, "preload_dlls"):
                onnxruntime.preload_dlls()
            _cuda_ready = True
        except Exception:  # the libraries are missing or broken: the CPU still works
            log_swallowed(_log, "loading the CUDA libraries for ONNX Runtime", level=logging.WARNING)
            _cuda_ready = False
    return _cuda_ready


def onnx_providers(use_gpu: str = "auto") -> list[Any]:
    """CUDA first when asked for (or 'auto') and installed, with a memory cap; CPU always last."""
    try:
        import onnxruntime
        available = onnxruntime.get_available_providers()
    except ImportError:
        return ["CPUExecutionProvider"]
    chosen: list[Any] = []
    if use_gpu != "off":
        if "CUDAExecutionProvider" in available and _cuda_libraries():
            chosen.append(("CUDAExecutionProvider", {"device_id": 0, "gpu_mem_limit": GPU_MEMORY_LIMIT,
                                                     "arena_extend_strategy": "kSameAsRequested"}))
        elif "DmlExecutionProvider" in available:
            chosen.append("DmlExecutionProvider")
    if use_gpu == "on" and not chosen:
        _log.warning("GPU requested for plate detection but no CUDA/DirectML provider is installed; using the CPU.")
    return [*chosen, "CPUExecutionProvider"]


def session_device(model: Any) -> str:
    """Where a loaded model really runs: ONNX Runtime quietly falls back to the CPU when CUDA cannot start."""
    try:
        first = model.model.get_providers()[0]
    except Exception:
        return "CPU"
    return "CPU" if first == "CPUExecutionProvider" else "GPU (CUDA)" if first.startswith("CUDA") else "GPU"


def gpu_failure(error: BaseException) -> bool:
    text = str(error).lower()
    return any(word in text for word in ("cuda", "cudnn", "cublas", "out of memory", "failed to allocate", "gpu"))


def with_cpu_fallback(owner: Any, run: Any) -> Any:
    """Run on the GPU; if it fails there (the brain model may have filled its memory), rebuild on the CPU and retry.

    `owner` has `device` and `rebuild(providers)`; the switch is permanent for this model and is logged."""
    try:
        return run()
    except Exception as error:
        if str(getattr(owner, "device", "CPU")).startswith("CPU") or not gpu_failure(error):
            raise
        _log.warning("%s failed on the GPU (%s); continuing on the CPU.", type(owner).__name__, str(error)[:160])
        owner.rebuild(["CPUExecutionProvider"])
        return run()


class YoloPlateDetector:
    """open-image-models' YOLOv9 plate detector (ONNX)."""

    name = "YOLOv9 (open-image-models, ONNX)"

    def __init__(self, model_dir: Path, use_gpu: str = "auto", confidence: float = .3) -> None:
        model_dir.mkdir(parents=True, exist_ok=True)
        # The package downloads into ~/.cache by default; HAYAT keeps its models in its own folder.
        import open_image_models.detection.core.hub as hub
        hub.MODEL_CACHE_DIR = model_dir
        from open_image_models import LicensePlateDetector  # noqa: F401 -- a missing package fails here, before any frame
        self.confidence = confidence
        self.rebuild(onnx_providers(use_gpu))

    def rebuild(self, providers: list[Any]) -> None:
        from open_image_models import LicensePlateDetector
        self.providers = providers
        self._model = LicensePlateDetector(detection_model=YOLO_MODEL, conf_thresh=self.confidence, providers=providers)
        self.device = session_device(self._model)

    def detect(self, frame: Any) -> list[Detection]:
        found = []
        for result in with_cpu_fallback(self, lambda: self._model.predict(frame)) or []:
            box = result.bounding_box
            x1, y1, x2, y2 = (int(round(value)) for value in (box.x1, box.y1, box.x2, box.y2))
            found.append(Detection((x1, y1, x2, y2), float(result.confidence), str(getattr(result, "label", "license_plate"))))
        return found


class ContourPlateDetector:
    """Classical fallback: plate-shaped rectangles of strong edges. Works best on clear, front-on plates."""

    name = "OpenCV contour detector (fallback)"
    device = "CPU"

    def __init__(self, min_area: int = 900, aspect: tuple[float, float] = (1.5, 6.5)) -> None:
        self.min_area, self.aspect = min_area, aspect

    def detect(self, frame: Any) -> list[Detection]:
        import cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = cv2.bilateralFilter(gray, 9, 60, 60)
        edges = cv2.Canny(gray, 50, 180)
        edges = cv2.dilate(edges, None, iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        height, width = gray.shape[:2]
        found: list[Detection] = []
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:60]:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h < self.min_area or w > width * .9 or h > height * .6:
                continue
            ratio = w / max(1, h)
            if not self.aspect[0] <= ratio <= self.aspect[1]:
                continue
            approx = cv2.approxPolyDP(contour, .04 * cv2.arcLength(contour, True), True)
            rectangular = 4 <= len(approx) <= 6
            # Plates are high-contrast inside: text strokes against a flat background.
            patch = gray[y:y + h, x:x + w]
            if float(patch.std()) < 30:
                continue  # flat (a window, a panel): no characters inside
            contrast = float(patch.std()) / 64
            score = min(.95, .35 + .25 * rectangular + min(.35, contrast * .35))
            box = (x, y, x + w, y + h)
            if all(_iou(box, other.box) < .3 for other in found):
                found.append(Detection(box, round(score, 3)))
        return [detection for detection in found if detection.confidence >= .5][:8]


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0.0


def make_detector(model_dir: Path, prefer: str = "yolo", use_gpu: str = "auto") -> Any:
    """The YOLO detector when its package and model load, else the fallback (and the reason is logged)."""
    if prefer == "yolo":
        try:
            return YoloPlateDetector(model_dir, use_gpu)
        except Exception:  # missing package, offline first download, or a broken model file
            log_swallowed(_log, "loading the YOLO plate detector; using the OpenCV fallback", level=logging.WARNING)
    return ContourPlateDetector()
