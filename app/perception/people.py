"""Local COCO person detection. No identities, biometrics or demographic inference."""
from __future__ import annotations

import hashlib
import importlib.util
import math
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data/models/people/ssdlite320_mobilenet_v3_large_coco-a79551df.pth"
MODEL_SHA256 = "a79551df90c79834bcd3bb3845ef9d966b5449a3a9b2833ae8404778ca5d65d2"
MODEL_URL = "https://download.pytorch.org/models/ssdlite320_mobilenet_v3_large_coco-a79551df.pth"
LIMITATION = "Person detections can miss small, dark, occluded or unusual views and can include false positives. A zero count does not prove nobody is present. No identity or protected traits are inferred."


class PersonDetector:
    def __init__(self, path=MODEL, loader=None):
        self.path, self._loader = Path(path), loader
        self._model = None
        self._lock = threading.RLock()

    def status(self):
        return {"person_detection": True, "installed": self.path.is_file(), "loaded": self._model is not None,
                "dependencies": {name: importlib.util.find_spec(name) is not None for name in ("torch", "torchvision")},
                "weights_verified": self._model is not None,
                "model": "Torchvision SSDLite320 MobileNetV3 COCO", "device": "CPU", "cloud": False,
                "path": str(self.path), "source": MODEL_URL, "sha256": MODEL_SHA256, "limitations": LIMITATION}

    def _load(self):
        if self._loader:
            return self._loader()
        if not self.path.is_file():
            raise FileNotFoundError("Person model is missing. Run python scripts/setup_person_detection.py.")
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("The person model does not match its pinned SHA-256.")
        import torch
        from torchvision.models.detection import ssdlite320_mobilenet_v3_large
        model = ssdlite320_mobilenet_v3_large(weights=None, weights_backbone=None)
        model.load_state_dict(torch.load(self.path, map_location="cpu", weights_only=True))
        return model.eval()

    def frame(self, image, confidence=.5):
        if not math.isfinite(confidence) or not .05 <= confidence <= .95:
            raise ValueError("Choose a confidence threshold between 0.05 and 0.95.")
        import torch
        from torchvision.transforms.functional import to_tensor
        width, height = image.size
        if width < 1 or height < 1 or width * height > 20_000_000:
            raise ValueError("Images must contain between 1 and 20 million pixels.")
        image = image.convert("RGB")
        with self._lock, torch.inference_mode():
            if self._model is None:
                self._model = self._load()
            result = self._model([to_tensor(image)])[0]
        people = []
        for label, score, box in zip(result["labels"].tolist(), result["scores"].tolist(), result["boxes"].tolist(), strict=True):
            if label != 1 or score < confidence:
                continue
            x1, y1, x2, y2 = box
            if not all(math.isfinite(v) for v in (*box, score)) or x2 <= x1 or y2 <= y1:
                continue
            bounded = [round(max(0, min(width, x1))), round(max(0, min(height, y1))),
                       round(max(0, min(width, x2))), round(max(0, min(height, y2)))]
            if bounded[2] <= bounded[0] or bounded[3] <= bounded[1]:
                continue
            people.append({"box": bounded, "score": round(score, 4)})
            if len(people) >= 100:
                break
        return {"person_detection": True, "count": len(people), "people": people,
                "width": width, "height": height, "threshold": confidence, "device": "CPU", "cloud": False,
                "model": "SSDLite320 MobileNetV3 COCO", "limitations": LIMITATION}

    def image(self, path, confidence=.5):
        from PIL import Image, ImageOps
        path = Path(path)
        if path.stat().st_size > 40_000_000:
            raise ValueError("Choose an image smaller than 40 MB.")
        with Image.open(path) as image:
            if image.width * image.height > 20_000_000:
                raise ValueError("Choose an image smaller than 20 million pixels.")
            result = self.frame(ImageOps.exif_transpose(image), confidence)
        return {**result, "path": str(path)}


def register_person_tools(registry, camera):
    detector = PersonDetector()
    registry.people = detector
    obj = lambda props=None, required=None: {"type": "object", "properties": props or {}, "required": required or [], "additionalProperties": False}
    threshold = {"type": "number", "minimum": .05, "maximum": .95}
    def image(path, confidence=.5):
        return detector.image(registry.permissions.validate_project_path(path), confidence)
    def camera_people(confidence=.5):
        # Keep a temporary activation alive until inference is complete: off()
        # purges temporary frames. Never leave a previously idle camera open.
        temporary_activation = not camera.capture_backend.active
        if temporary_activation:
            camera.on()
        try:
            frame = camera.capture(retain=False)
            path = Path(frame["path"])
            try:
                return {**detector.image(path, confidence), "source": "owner camera", "raw_frames_retained": False, "path": None}
            finally:
                path.unlink(missing_ok=True)
        finally:
            if temporary_activation:
                camera.off()
    registry.register("people_status", "Read local person-detector readiness without opening a camera.", obj(), "low", "system_read", detector.status)
    registry.register("detect_people", "Detect visible people in a local image, returning boxes and scores. No identities or demographics.",
                      obj({"path": {"type": "string"}, "confidence": threshold}, ["path"]), "low", "filesystem_read", image)
    registry.register("camera_people", "Capture the owner's camera once and detect people locally; delete the temporary frame. No identity inference.",
                      obj({"confidence": threshold}), "medium", "camera_use", camera_people)
    return detector
