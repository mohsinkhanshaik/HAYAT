"""Did something move in the picture, or did only the light change? One comparison of two camera frames.

The camera change check used to average the pixel difference against the first
frame and call anything over a threshold a change. A lamp switching on, the
webcam re-exposing or the sun behind a cloud all passed, and a person crossing
one corner of the view could fail, diluted by the still rest of the picture.

This uses the same lighting removal as the motion tracker (tracking.py): the
global gain is matched first, then smooth illumination is subtracted, leaving
edges and texture, which is what a moving thing changes. What remains is
counted as an area of the picture, with where it is, so the answer is
"movement in the bottom left, 3 % of the picture" or "only the light changed".
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from app.perception.camera.scene_motion import place
from app.perception.camera.tracking import _match_brightness, _without_lighting

#: Frames are compared at this width: enough for a person across a room, cheap at any camera resolution.
WIDTH = 160
#: Detail must change by this much (0-255, after lighting removal) for a pixel to count.
PIXEL_THRESHOLD = 28
#: Share of the picture that must change for movement; below it is sensor noise.
MIN_AREA = 0.002
#: The camera itself moved, or the scene was replaced, when change reaches this share of a 4 x 4 grid's cells.
#: Change gathers at edges, so a replaced scene rarely changes most *pixels*; it changes almost every *region*.
WHOLE_PICTURE = 0.8
#: A grid cell counts as changed when this share of its pixels changed.
CELL_CHANGED = 0.02
GRID = 4
#: Raw mean difference (0-255) that counts as a lighting change when no detail moved.
LIGHTING_DIFFERENCE = 6.0


def prepare(gray: Any) -> Any:
    """A small, softened greyscale frame, ready to compare."""
    height, width = gray.shape[:2]
    scale = WIDTH / max(1, width)
    small = cv2.resize(gray, (WIDTH, max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA)
    return cv2.GaussianBlur(small, (5, 5), 0)


#: Blocks (in pixels of the small frame) whose structure is compared.
BLOCK = 10
#: Below this correlation a block's structure changed; light and clipping keep it near 1.
SAME_STRUCTURE = 0.7
#: A block flatter than this (detail standard deviation) is judged by whether detail appeared, not by correlation.
FLAT = 4.0


def _structure_changed(first: Any, second: Any, mask: Any) -> Any:
    """The mask, kept only inside blocks whose detail changed shape between the two frames."""
    height, width = first.shape
    keep = np.zeros_like(mask)
    a, b = first.astype(np.float32) - 128.0, second.astype(np.float32) - 128.0
    for top in range(0, height, BLOCK):
        for left in range(0, width, BLOCK):
            window = (slice(top, top + BLOCK), slice(left, left + BLOCK))
            if not mask[window].any():
                continue
            x, y = a[window] - a[window].mean(), b[window] - b[window].mean()
            sx, sy = float(x.std()), float(y.std())
            if sx < FLAT or sy < FLAT:
                changed = abs(sx - sy) >= FLAT  # detail appeared on a plain surface, or vanished from one
            else:
                changed = float((x * y).mean()) / (sx * sy) < SAME_STRUCTURE
            if changed:
                keep[window] = mask[window]
    return keep


def compare(before: Any, after: Any, *, min_area: float = MIN_AREA) -> dict[str, Any]:
    """How ``after`` differs from ``before`` (both from ``prepare``): kind, area, where and the raw difference."""
    raw = float(cv2.mean(cv2.absdiff(before, after))[0])
    matched = _match_brightness(after, float(before.mean()))
    first, second = _without_lighting(before), _without_lighting(matched)
    detail = cv2.absdiff(first, second)
    _, mask = cv2.threshold(detail, PIXEL_THRESHOLD, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # lone noisy pixels are not movement
    # Brightness steps clip highlights and shadows, which no gain undoes; the clipped edges then differ in value
    # but not in shape. Only regions whose structure changed (low correlation) keep their changed pixels.
    mask = _structure_changed(first, second, mask)
    area = cv2.countNonZero(mask) / mask.size
    height, width = mask.shape
    cells = [mask[row * height // GRID:(row + 1) * height // GRID, column * width // GRID:(column + 1) * width // GRID]
             for row in range(GRID) for column in range(GRID)]
    spread = sum(1 for cell in cells if cell.size and cv2.countNonZero(cell) / cell.size >= CELL_CHANGED) / len(cells)
    result: dict[str, Any] = {"raw_difference": round(raw, 2), "changed_area_pct": round(area * 100, 2),
                              "regions_changed_pct": round(spread * 100)}
    if spread >= WHOLE_PICTURE and area >= min_area:
        result.update(kind="whole_picture", moved=True,
                      summary="Almost the whole picture changed: the camera moved or the scene was replaced.")
    elif area >= min_area:
        points = cv2.findNonZero(mask)
        x, y, w, h = cv2.boundingRect(points)
        height, width = mask.shape
        where = place((x + w / 2) / width, (y + h / 2) / height)
        result.update(kind="movement", moved=True, where=where,
                      box=[round(x / width, 3), round(y / height, 3), round((x + w) / width, 3), round((y + h) / height, 3)],
                      summary=f"Movement in the {where} of the picture ({area * 100:.1f} % of it changed).")
    elif raw >= LIGHTING_DIFFERENCE:
        result.update(kind="lighting", moved=False,
                      summary="Only the light changed (brightness shifted, but nothing in the picture moved).")
    else:
        result.update(kind="none", moved=False, summary="Nothing changed.")
    return result
