"""Following each plate across video frames, so one passing car gives one record, not one per frame.

A detection joins the track whose last box it overlaps most (IoU), or whose
centre is closest when boxes move fast. Every OCR reading of a track is kept;
when the track ends, the readings vote: each distinct cleaned text scores the
sum of its confidences, and the winner is stored once.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.anpr.detector import Detection, _iou


@dataclass
class Track:
    track_id: int
    box: tuple[int, int, int, int]
    first_frame: int
    last_frame: int
    readings: list[tuple[str, float, float]] = field(default_factory=list)  # (text, ocr confidence, detection confidence)
    best_crop: object = None
    best_score: float = -1.0

    def vote(self) -> tuple[str, float, int]:
        """(text, confidence, how many readings agreed): the confidence-weighted majority."""
        scores: dict[str, float] = defaultdict(float)
        counts: dict[str, int] = defaultdict(int)
        for text, confidence, _detection in self.readings:
            if text:
                scores[text] += confidence
                counts[text] += 1
        if not scores:
            return "", 0.0, 0
        text = max(scores, key=lambda key: (scores[key], counts[key]))
        return text, round(scores[text] / counts[text], 3), counts[text]


class PlateTracker:
    def __init__(self, iou_threshold: float = .25, max_missing: int = 12, max_jump: float = 1.5) -> None:
        self.iou_threshold, self.max_missing, self.max_jump = iou_threshold, max_missing, max_jump
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def update(self, detections: list[Detection], frame_index: int) -> tuple[list[tuple[Detection, Track]], list[Track]]:
        """Assign detections to tracks; return (pairs for this frame, tracks that just ended)."""
        pairs: list[tuple[Detection, Track]] = []
        unused = dict(self.tracks)
        for detection in sorted(detections, key=lambda item: -item.confidence):
            best, best_score = None, 0.0
            for track in unused.values():
                overlap = _iou(detection.box, track.box)
                if overlap < self.iou_threshold:
                    # Fast motion: accept a nearby centre within max_jump plate-widths.
                    cx, cy = (detection.box[0] + detection.box[2]) / 2, (detection.box[1] + detection.box[3]) / 2
                    tx, ty = (track.box[0] + track.box[2]) / 2, (track.box[1] + track.box[3]) / 2
                    width = max(1, track.box[2] - track.box[0])
                    distance = ((cx - tx) ** 2 + (cy - ty) ** 2) ** .5 / width
                    overlap = (self.max_jump - distance) / self.max_jump * self.iou_threshold if distance < self.max_jump else 0
                if overlap > best_score:
                    best, best_score = track, overlap
            if best is None:
                best = Track(self._next, detection.box, frame_index, frame_index)
                self.tracks[best.track_id] = best
                self._next += 1
            else:
                unused.pop(best.track_id)
            best.box, best.last_frame = detection.box, frame_index
            pairs.append((detection, best))
        ended = [track for track in self.tracks.values() if frame_index - track.last_frame > self.max_missing]
        for track in ended:
            self.tracks.pop(track.track_id)
        return pairs, ended

    def finish(self) -> list[Track]:
        """Every track still open (end of the video or stream)."""
        ended, self.tracks = list(self.tracks.values()), {}
        return ended
