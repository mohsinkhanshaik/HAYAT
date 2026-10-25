"""Bounded, in-memory feature tracking in image coordinates (no inferred depth)."""
from __future__ import annotations

import cv2
import numpy as np


class SceneTracker:
    MAX_POINTS = 96
    TRAIL = 12

    def __init__(self):
        self.reset()

    def reset(self):
        self.previous = None
        self._last_mean = None
        self.paths = []
        self.next_id = 1
        self.frame = 0

    #: Extra points, in a budget of their own, where the picture is changing.
    MOTION_POINTS = 48
    #: Frames a motion point may sit still before its slot is freed.
    MOTION_PATIENCE = 15

    def _seed_where_changing(self, gray, roi) -> None:
        """Add points where the picture changed and no point is watching.

        Spread evenly, 96 points gave a hand or a person crossing the view two
        or three points -- too few to see it as something moving. Differences
        between frames show where the change is; points are added there, up
        to a bounded extra budget, so moving things are followed densely.
        """
        room = self.MOTION_POINTS - sum(1 for row in self.paths if row.get("motion"))
        if room <= 0:
            return
        h, w = gray.shape
        change = cv2.absdiff(self.previous, gray)
        _, mask = cv2.threshold(change, 25, 255, cv2.THRESH_BINARY)
        mask = cv2.dilate(mask, np.ones((9, 9), np.uint8))
        if roi is not None:
            limit = np.zeros_like(mask)
            x1, y1, x2, y2 = roi
            limit[int(y1*h):int(y2*h), int(x1*w):int(x2*w)] = 255
            mask = cv2.bitwise_and(mask, limit)
        # A change over most of the frame is the camera moving or the light
        # changing, not something moving in it: nothing to single out.
        if not mask.any() or cv2.countNonZero(mask) > .5 * mask.size:
            return
        for row in self.paths:
            cv2.circle(mask, tuple(int(v) for v in row["points"][-1]), 7, 0, -1)
        corners = cv2.goodFeaturesToTrack(gray, min(room, 24), .02, 7, mask=mask)
        if corners is not None:
            for x, y in corners.reshape(-1, 2):
                self.paths.append({"id": self.next_id, "points": [(float(x), float(y))],
                                   "motion": True, "still": 0})
                self.next_id += 1

    def update(self, bgr, roi=None):
        picture = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)      # what the camera saw
        h, w = picture.shape
        if self.previous is not None and self.previous.shape != picture.shape:
            self.reset()
        # Webcams re-expose constantly: a global brightness change made a
        # still room look full of movement (32 of 40 frames in a test).
        # Matching this frame's brightness to the last one removes it.
        raw_mean = float(picture.mean())
        matched = _match_brightness(picture, self._last_mean) if self._last_mean is not None else picture
        self._last_mean = raw_mean
        # Local light -- a monitor's glow beside the camera -- changes one
        # part of the picture smoothly, which gain matching cannot undo (35 of
        # 60 frames still read as movement). Tracking works on the picture
        # with smooth lighting removed: edges and texture, which is what a
        # moving thing actually brings.
        gray = _without_lighting(matched)
        self.frame += 1
        displacements = []
        if self.paths and self.previous is not None:
            before = np.float32([row["points"][-1] for row in self.paths]).reshape(-1, 1, 2)
            after, ok, error = cv2.calcOpticalFlowPyrLK(self.previous, gray, before, None,
                                                      winSize=(21, 21), maxLevel=3)
            kept = []
            if after is not None:
                back, back_ok, _ = cv2.calcOpticalFlowPyrLK(gray, self.previous, after, None,
                                                          winSize=(21, 21), maxLevel=3)
                if back is not None:
                    for i, row in enumerate(self.paths):
                        x, y = after[i, 0]
                        reliable = (ok[i, 0] and back_ok[i, 0] and error[i, 0] < 35
                                    and np.linalg.norm(before[i, 0] - back[i, 0]) < 1.5)
                        inside = 0 <= x < w and 0 <= y < h
                        if roi is not None:
                            inside = inside and roi[0]*w <= x <= roi[2]*w and roi[1]*h <= y <= roi[3]*h
                        if reliable and inside:
                            delta = after[i, 0] - before[i, 0]
                            displacements.append((float(x/w), float(y/h), float(np.linalg.norm(delta))))
                            row["points"] = (row["points"] + [(float(x), float(y))])[-self.TRAIL:]
                            if row.get("motion"):
                                row["still"] = row["still"] + 1 if float(np.linalg.norm(delta)) < .5 else 0
                                if row["still"] > self.MOTION_PATIENCE:
                                    continue    # stopped moving: free the slot for what moves next
                            kept.append(row)
            self.paths = kept
        # Replenish without relabelling points that remain visible.
        background = [row for row in self.paths if not row.get("motion")]
        if len(background) < self.MAX_POINTS and (self.frame % 5 == 1 or len(background) < 8):
            mask = np.full(gray.shape, 255, np.uint8)
            if roi is not None:
                mask[:] = 0
                x1, y1, x2, y2 = roi
                mask[int(y1*h):int(y2*h), int(x1*w):int(x2*w)] = 255
            for row in self.paths:
                cv2.circle(mask, tuple(int(v) for v in row["points"][-1]), 12, 0, -1)
            corners = cv2.goodFeaturesToTrack(gray, self.MAX_POINTS-len(background), .025, 12, mask=mask)
            if corners is not None:
                for x, y in corners.reshape(-1, 2):
                    self.paths.append({"id": self.next_id, "points": [(float(x), float(y))]})
                    self.next_id += 1
        if self.previous is not None:
            self._seed_where_changing(gray, roi)
        self.previous = gray
        cells = [{"points": 0, "moving": 0} for _ in range(12)]
        for row in self.paths:
            x, y = row["points"][-1]
            cells[min(2, int(y/h*3))*4+min(3, int(x/w*4))]["points"] += 1
        for x, y, speed in displacements:
            if speed >= 1.2:
                cells[min(2, int(y*3))*4+min(3, int(x*4))]["moving"] += 1
        return {"width": w, "height": h, "count": len(self.paths), "cells": cells,
                "moving": sum(cell["moving"] for cell in cells),
                "motion_px": float(np.median([d[2] for d in displacements])) if displacements else 0,
                "tracks": [{"id": row["id"], "points": list(row["points"])} for row in self.paths],
                "roi": roi, "brightness": raw_mean,
                "sharpness": float(cv2.Laplacian(picture, cv2.CV_64F).var())}


class EyeProcessor:
    """The UI sets an immutable request; the stream worker owns all tracking state."""

    #: How tracked points are drawn: "points" (dots and trails) or "mesh".
    VIEWS = ("points", "mesh")

    def __init__(self):
        self.request = (True, None, 0)
        self._applied = None
        self.tracker = SceneTracker()
        self._last_regions = []
        # Only changes what is drawn, so it does not restart tracking.
        self.view = "points"

    def configure(self, *, enabled=True, roi=None):
        self.request = (enabled, roi, self.request[2] + 1)

    def reset(self):
        self.tracker.reset()
        self._last_regions = []
        self._applied = None

    def __call__(self, bgr):
        from PIL import Image, ImageDraw
        request = self.request
        if request != self._applied:
            self.tracker.reset()
            self._last_regions = []
            self._applied = request
        enabled, roi, _ = request
        picture = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if not enabled:
            picture.info["tracking"] = {"enabled": False}
            return picture
        result = self.tracker.update(bgr, roi)
        result["enabled"] = True
        from app.perception.camera.scene_motion import analyse

        # What the motion means: camera movement, moving regions, a sentence.
        scene = analyse(result)
        # Something really moving is still there a frame later; a one-frame
        # region is noise. Reporting only regions that overlap one from the
        # previous frame costs a fifteenth of a second and removes most of
        # what sensor noise and flicker still produce.
        seen, self._last_regions = self._last_regions, scene["regions"]
        confirmed = [region for region in scene["regions"] if any(_overlap(region["box"], old["box"]) for old in seen)]
        if len(confirmed) != len(scene["regions"]):
            from app.perception.camera.scene_motion import sentence

            scene = {**scene, "regions": confirmed, "summary": sentence(scene["camera"], confirmed)}
        result["scene"] = scene
        draw = ImageDraw.Draw(picture)
        if self.view == "mesh":
            _draw_mesh(draw, result, picture.size)
        else:
            for row in result["tracks"]:
                points = row["points"]
                if len(points) > 1:
                    draw.line(points, fill="#52d8ff", width=2)
                x, y = points[-1]
                draw.ellipse((x-2, y-2, x+2, y+2), fill="#f3c34d")
        _draw_regions(draw, result["scene"]["regions"], picture.size)
        if roi:
            w, h = picture.size
            draw.rectangle((roi[0]*w, roi[1]*h, roi[2]*w, roi[3]*h), outline="#f3c34d", width=2)
        picture.info["tracking"] = result
        return picture


#: Moving regions are green: gold is the tracked points, blue their trails.
REGION_COLOUR = "#a4d779"


def _draw_regions(draw, regions, size) -> None:
    """A box round each group of points moving together, and which way it goes."""
    w, h = size
    for region in regions[:6]:
        x1, y1, x2, y2 = region["box"]
        box = (x1 * w - 6, y1 * h - 6, x2 * w + 6, y2 * h + 6)
        draw.rectangle(box, outline=REGION_COLOUR, width=2)
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        length = min(40.0, 6 + region["speed"] * 4)
        norm = max(1e-6, (region["dx"] ** 2 + region["dy"] ** 2) ** .5)
        ex, ey = cx + region["dx"] / norm * length, cy + region["dy"] / norm * length
        draw.line((cx, cy, ex, ey), fill=REGION_COLOUR, width=3)
        draw.ellipse((ex - 3, ey - 3, ex + 3, ey + 3), fill=REGION_COLOUR)
        label = f"moving {region['heading']}"
        draw.rectangle((box[0], max(0, box[1] - 14), box[0] + 7 * len(label) + 6, max(14, box[1])),
                       fill="#101a2b")
        draw.text((box[0] + 3, max(0, box[1] - 13)), label, fill=REGION_COLOUR)


def _draw_mesh(draw, result, size) -> None:
    """A triangle mesh over the tracked points; moving vertices glow gold.

    Surfaces that move together stretch and shift as one patch, which reads
    more easily than scattered dots. It is a picture of the tracked points,
    not a reconstruction: there is no depth in it.
    """
    w, h = size
    points = [(row["points"][-1], row["points"]) for row in result["tracks"]]
    if len(points) < 3:
        return
    subdiv = cv2.Subdiv2D((0, 0, int(w), int(h)))
    moving = {}
    for (x, y), history in points:
        px, py = min(w - 1.0, max(0.0, x)), min(h - 1.0, max(0.0, y))
        try:
            subdiv.insert((float(px), float(py)))
        except cv2.error:
            continue
        if len(history) >= 2:
            (x0, y0), (x1, y1) = history[-2], history[-1]
            moving[(round(px), round(py))] = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5 >= 1.2
    longest = max(w, h) * .35
    for x1, y1, x2, y2, x3, y3 in subdiv.getTriangleList():
        corners = ((x1, y1), (x2, y2), (x3, y3))
        if any(not (0 <= x < w and 0 <= y < h) for x, y in corners):
            continue
        # Very long edges join unrelated parts of the picture; leave them out.
        if max(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** .5
               for a, b in ((corners[0], corners[1]), (corners[1], corners[2]), (corners[2], corners[0]))) > longest:
            continue
        hot = sum(moving.get((round(x), round(y)), False) for x, y in corners)
        colour = "#f3c34d" if hot >= 2 else "#52d8ff"
        draw.line((*corners[0], *corners[1], *corners[2], *corners[0]), fill=colour, width=1)
    for (x, y), _history in points:
        hot = moving.get((round(min(w - 1.0, max(0.0, x))), round(min(h - 1.0, max(0.0, y)))), False)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#f3c34d" if hot else "#52d8ff")


def _match_brightness(gray, before: float):
    """Scale ``gray`` so its mean brightness matches the previous frame's.

    Auto-exposure changes the whole picture's gain at once; an object moving
    changes only part of it. Undoing the global gain first leaves the real
    movement and removes the flicker.
    """
    current = float(gray.mean())
    if current < 1.0 or abs(current - before) < .5:
        return gray
    gain = max(.5, min(2.0, before / current))
    return cv2.convertScaleAbs(gray, alpha=gain)


#: Lighting varies over a larger scale than this (in pixels); texture and
#: edges vary over a smaller one.
LIGHTING_SCALE = 12.0


def _without_lighting(gray):
    """The picture minus its smooth lighting: detail around mid-grey.

    Subtracting a heavily blurred copy removes any illumination that changes
    gradually across the frame (a lamp, a screen's glow, a shadow's edge
    softened by distance) and keeps the edges and texture that tracking
    needs. Contrast is stretched so faint texture still yields corners.
    """
    blurred = cv2.GaussianBlur(gray, (0, 0), LIGHTING_SCALE)
    detail = gray.astype(np.float32) - blurred.astype(np.float32)
    # Signed detail around mid-grey; clipping, not absolute value, so dark
    # edges and light edges stay distinct.
    return np.clip(detail * 2.0 + 128.0, 0, 255).astype(np.uint8)


def _overlap(a, b) -> bool:
    """Whether two (x1, y1, x2, y2) boxes, in image fractions, overlap at all."""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]
