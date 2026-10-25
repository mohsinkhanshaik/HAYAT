"""Detect, read, track and record plates in an image, a recorded video or a live feed."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from app.anpr.detector import Detection, make_detector
from app.anpr.formats import custom_format, read_plate
from app.anpr.ocr import OcrResult, make_ocr
from app.anpr.sources import Source, check_source
from app.anpr.store import AnprStore
from app.anpr.tracker import PlateTracker, Track
from app.logging import log_swallowed

_log = logging.getLogger("hayat.anpr")


@dataclass
class FrameResult:
    """What one frame showed: boxes with their current reading, for the live preview."""
    frame_index: int
    seen_at: str
    plates: list[dict[str, Any]] = field(default_factory=list)
    annotated: Any = None


class AnprPipeline:
    def __init__(self, store: AnprStore, model_dir: Path | str, detector: Any = None, ocr: Any = None) -> None:
        self.store = store
        self.model_dir = Path(model_dir)
        settings = store.settings()
        self.detector = detector or make_detector(self.model_dir / "detector",
                                                  "yolo" if settings["detector"] == "yolo" else "contour", settings["use_gpu"])
        self._ocr = ocr
        self._second: Any = None
        self._second_tried = ocr is not None  # injected engines (tests) get no second opinion
        self._lock = threading.Lock()

    @property
    def ocr(self) -> Any:
        """Loaded on first use: EasyOCR's models take a few seconds and the dashboard should open at once."""
        with self._lock:
            if self._ocr is None:
                settings = self.store.settings()
                self._ocr = make_ocr(self.model_dir / "ocr", settings["ocr_engine"], settings["use_gpu"])
            return self._ocr

    @property
    def second_ocr(self) -> Any:
        """EasyOCR as a second reader behind fast-plate-ocr, loaded the first time it is needed."""
        with self._lock:
            if not self._second_tried:
                self._second_tried = True
                if getattr(self._ocr, "name", "").startswith("fast-plate-ocr"):
                    try:
                        from app.anpr.ocr import EasyOcrEngine
                        self._second = EasyOcrEngine(self.model_dir / "ocr" / "easyocr", self.store.settings()["use_gpu"])
                    except Exception:  # EasyOCR missing or its models unavailable: one engine it is
                        log_swallowed(_log, "loading EasyOCR as the second plate reader")
            return self._second

    def status(self) -> dict[str, Any]:
        engine = self._ocr.name if self._ocr is not None else "loads on first use"
        return {"detector": self.detector.name, "detector_device": getattr(self.detector, "device", "CPU"), "ocr": engine,
                "ocr_device": getattr(self._ocr, "device", "CPU") if self._ocr is not None else "—"}

    # ------------------------------------------------------------------ one frame
    def _read(self, frame: Any, detection: Detection, region: str, custom: dict) -> tuple[dict[str, Any], Any]:
        x1, y1, x2, y2 = detection.box
        height, width = frame.shape[:2]
        pad_x, pad_y = int((x2 - x1) * .06), int((y2 - y1) * .12)
        crop = frame[max(0, y1 - pad_y):min(height, y2 + pad_y), max(0, x1 - pad_x):min(width, x2 + pad_x)]
        if crop.size == 0:
            return {}, None
        result = self.ocr.read(crop)
        reading = read_plate(result.text, region, custom)
        # A reading that does not fit the plate format, or is unsure, gets a second opinion; the better one wins:
        # fits the format, then fewer repaired characters, then higher confidence.
        if not (reading.valid and not reading.trimmed and result.confidence >= .8) and self.second_ocr is not None:
            other = self.second_ocr.read(crop)
            other_reading = read_plate(other.text, region, custom)
            rank = lambda item: (item[1].valid, not item[1].trimmed, -item[1].repaired, item[0].confidence)
            first_plate = reading.plate
            result, reading = max(((result, reading), (other, other_reading)), key=rank)
            if reading.plate != first_plate:
                # The two readers disagree: the reading is kept, but a person should check it.
                result = OcrResult(result.text, min(result.confidence, .7), result.engine, result.region, result.region_confidence)
        confidence = result.confidence * (.9 if reading.trimmed else 1)  # found inside longer text: review it
        return {"plate": reading.plate, "raw_text": result.text, "confidence": round(confidence, 3),
                "detection_confidence": round(detection.confidence, 3), "format_valid": reading.valid, "region": region,
                "repaired": reading.repaired, "trimmed": reading.trimmed, "engine": result.engine,
                "bbox": list(detection.box)}, crop

    def _custom(self, settings: dict[str, Any]) -> dict[str, Any]:
        formats = {}
        for item in settings.get("custom_formats") or []:
            try:
                made = custom_format(item.get("code", ""), item.get("label", ""), item.get("spec", ""))
                formats[made.code] = made
            except (ValueError, TypeError):
                _log.warning("Ignoring an invalid custom plate format: %s", item)
        return formats

    def process_frame(self, frame: Any, frame_index: int = 0, seen_at: str = "") -> FrameResult:
        settings = self.store.settings()
        custom = self._custom(settings)
        result = FrameResult(frame_index, seen_at or datetime.now(UTC).isoformat())
        for detection in self.detector.detect(frame):
            reading, _crop = self._read(frame, detection, settings["region"], custom)
            if reading:
                result.plates.append(reading)
        result.annotated = annotate(frame, result.plates)
        return result

    # ------------------------------------------------------------------ sources
    def process_image(self, path: str) -> list[dict[str, Any]]:
        """Every plate in one image, each stored once."""
        import cv2
        source = check_source(path)
        if source.kind != "image":
            raise ValueError("Not an image file.")
        frame = cv2.imread(str(source.target))
        if frame is None:
            raise ValueError(f"Could not read the image {source.label}.")
        settings = self.store.settings()
        custom = self._custom(settings)
        stored = []
        for detection in self.detector.detect(frame):
            reading, crop = self._read(frame, detection, settings["region"], custom)
            if reading.get("plate"):
                stored.append(self.store.add({**reading, "source": source.label, "source_kind": "image",
                                              "seen_at": datetime.now(UTC).isoformat()}, crop))
        return stored

    def process_video(self, source: str | int, on_frame: Callable[[FrameResult], None] | None = None,
                      stop: threading.Event | None = None, max_seconds: float | None = None) -> dict[str, Any]:
        """A recorded video, webcam or registered RTSP camera: tracked, one record per plate passing."""
        import cv2
        settings = self.store.settings()
        checked: Source = check_source(source, tuple(settings["authorized_cameras"]))
        if checked.kind == "image":
            return {"stored": self.process_image(str(checked.target)), "frames": 1}
        capture = cv2.VideoCapture(checked.target if checked.kind != "webcam" else int(checked.target))
        if not capture.isOpened():
            raise ValueError(f"Could not open {checked.label}.")
        live = checked.kind in ("webcam", "rtsp")
        fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
        stride = max(1, int(settings["frame_stride"]))
        custom = self._custom(settings)
        tracker = PlateTracker(max_missing=max(4, int(12 / stride)))
        started_wall, stored, index, read = datetime.now(UTC), [], 0, 0
        started = time.monotonic()

        def finish(tracks: list[Track]) -> None:
            for track in tracks:
                text, confidence, agreeing = track.vote()
                if not text:
                    continue
                reading = read_plate(text, settings["region"], custom)
                offset = track.first_frame / fps
                seen = (started_wall + timedelta(seconds=time.monotonic() - started)) if live else started_wall
                record = {"plate": reading.plate, "raw_text": text, "confidence": confidence, "readings": agreeing,
                          "detection_confidence": round(max(item[2] for item in track.readings), 3),
                          "format_valid": reading.valid, "region": settings["region"], "source": checked.label,
                          "source_kind": checked.kind, "seen_at": seen.isoformat(),
                          "video_offset": None if live else round(offset, 2), "bbox": list(track.box),
                          "engine": getattr(self._ocr, "name", "")}
                stored.append(self.store.add(record, track.best_crop))

        try:
            while not (stop and stop.is_set()):
                if max_seconds and time.monotonic() - started > max_seconds:
                    break
                ok, frame = capture.read()
                if not ok:
                    if live:
                        time.sleep(.05)
                        if time.monotonic() - started > 5 and read == 0:
                            raise ValueError(f"{checked.label} sends no frames.")
                        continue
                    break
                index += 1
                if index % stride:
                    continue
                read += 1
                detections = self.detector.detect(frame)
                pairs, ended = tracker.update(detections, index)
                plates = []
                for detection, track in pairs:
                    reading, crop = self._read(frame, detection, settings["region"], custom)
                    if not reading:
                        continue
                    track.readings.append((reading["plate"], reading["confidence"], detection.confidence))
                    score = reading["confidence"] * detection.confidence
                    if score > track.best_score:
                        track.best_score, track.best_crop = score, crop
                    voted = track.vote()[0]
                    plates.append({**reading, "track_id": track.track_id, "voted": voted})
                finish(ended)
                if on_frame:
                    seen_at = datetime.now(UTC).isoformat() if live else f"{index / fps:.1f} s"
                    on_frame(FrameResult(index, seen_at, plates, annotate(frame, plates)))
        finally:
            capture.release()
            finish(tracker.finish())
        return {"stored": stored, "frames": index, "frames_read": read, "source": checked.label}


def annotate(frame: Any, plates: list[dict[str, Any]]) -> Any:
    """Boxes, plate text and confidence drawn on a copy of the frame."""
    import cv2
    image = frame.copy()
    for plate in plates:
        x1, y1, x2, y2 = plate["bbox"]
        colour = (80, 222, 74) if plate.get("format_valid") else (65, 165, 242)
        cv2.rectangle(image, (x1, y1), (x2, y2), colour, 2)
        label = f"{plate.get('voted') or plate['plate'] or '?'}  {plate['confidence']:.0%}"
        (width, height), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, .6, 2)
        top = max(0, y1 - height - 10)
        cv2.rectangle(image, (x1, top), (x1 + width + 8, top + height + 8), colour, -1)
        cv2.putText(image, label, (x1 + 4, top + height + 3), cv2.FONT_HERSHEY_SIMPLEX, .6, (20, 20, 20), 2, cv2.LINE_AA)
    return image
