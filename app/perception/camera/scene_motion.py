"""What the tracked motion means, in words and regions -- from the tracks alone.

The tracker follows texture points. This turns their last movement into things
a person can read: whether the camera itself is moving, which regions of the
picture are moving and which way, and one plain sentence. Everything is image
space. There is no depth, no object identity and no face or person
recognition: a "region" is a group of points moving together, nothing more.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

#: Pixels per frame below which a point counts as still.
MOVING_PX = 1.2
#: The camera is taken to be moving when this share of points moves together...
CAMERA_SHARE = .6
#: ...across at least this many of the picture's 4 x 3 cells.
CAMERA_CELLS = 6
#: A grid this fine groups nearby moving points into regions.
GRID = (16, 12)
#: A region needs at least this many moving points to be reported...
MIN_REGION_POINTS = 3
#: ...moving together: their mean motion at least this share of their mean speed.
COHERENCE = .6


def direction(dx: float, dy: float) -> str:
    """A compass word for an image-space vector (screen y grows downward)."""
    if math.hypot(dx, dy) < 1e-9:
        return "still"
    angle = math.degrees(math.atan2(-dy, dx)) % 360
    names = ("right", "up-right", "up", "up-left", "left", "down-left", "down", "down-right")
    return names[int((angle + 22.5) // 45) % 8]


def place(x: float, y: float) -> str:
    """Where in the picture a point is, in thirds: 'top left', 'centre', ..."""
    column = ("left", "centre", "right")[min(2, int(x * 3))]
    row = ("top", "middle", "bottom")[min(2, int(y * 3))]
    if row == "middle":
        return column
    return f"{row} {column}" if column != "centre" else row


def vectors(result: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    """(x, y, dx, dy) of each track's last step, in pixels."""
    found = []
    for track in result.get("tracks") or []:
        points = track.get("points") or []
        if len(points) >= 2:
            (x0, y0), (x1, y1) = points[-2], points[-1]
            found.append((x1, y1, x1 - x0, y1 - y0))
    return found


def analyse(result: dict[str, Any]) -> dict[str, Any]:
    """Camera motion, moving regions, a per-cell flow map and a sentence."""
    width = float(result.get("width") or 0)
    height = float(result.get("height") or 0)
    steps = vectors(result)
    empty = {"camera": None, "camera_shift": (0.0, 0.0), "regions": [], "cells": [(0.0, 0.0, 0)] * 12,
             "summary": "Too little detail to follow. Add light or point at something textured."}
    if not width or not height or len(steps) < 6:
        return empty

    # 1. Is the camera itself moving? Most points shifting the same way says so.
    shift_x = median(dx for *_rest, dx, _dy in steps)
    shift_y = median(dy for *_rest, _dx, dy in steps)
    camera = None
    if math.hypot(shift_x, shift_y) >= MOVING_PX:
        agreeing = [(x, y) for x, y, dx, dy in steps
                    if math.hypot(dx - shift_x, dy - shift_y) < max(1.0, .5 * math.hypot(shift_x, shift_y))]
        # A moving camera shifts the whole picture; a moving object shifts
        # one part of it. On a plain wall most tracked points can sit on the
        # object, so agreement alone called a walk past the camera a pan.
        spread = {(min(3, int(x / width * 4)), min(2, int(y / height * 3))) for x, y in agreeing}
        if len(agreeing) / len(steps) >= CAMERA_SHARE and len(spread) >= CAMERA_CELLS:
            camera = direction(shift_x, shift_y)
    if camera is None:
        shift_x = shift_y = 0.0

    # 2. What moves relative to that: residual motion, grouped on a grid.
    moving = [(x, y, dx - shift_x, dy - shift_y) for x, y, dx, dy in steps
              if math.hypot(dx - shift_x, dy - shift_y) >= MOVING_PX]
    columns, rows = GRID
    buckets: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for item in moving:
        key = (min(columns - 1, int(item[0] / width * columns)), min(rows - 1, int(item[1] / height * rows)))
        buckets.setdefault(key, []).append(item)
    seen: set[tuple[int, int]] = set()
    regions = []
    for start in buckets:
        if start in seen:
            continue
        group, frontier = [], [start]
        seen.add(start)
        while frontier:
            cx, cy = frontier.pop()
            group.extend(buckets[(cx, cy)])
            for nx in (cx - 1, cx, cx + 1):
                for ny in (cy - 1, cy, cy + 1):
                    if (nx, ny) in buckets and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        frontier.append((nx, ny))
        if len(group) < MIN_REGION_POINTS:
            continue
        xs, ys = [item[0] for item in group], [item[1] for item in group]
        mean_dx = sum(item[2] for item in group) / len(group)
        mean_dy = sum(item[3] for item in group) / len(group)
        # Points on a real moving thing move together. Noise and flicker
        # jitter them in every direction: their average cancels out.
        spread = sum(math.hypot(item[2], item[3]) for item in group) / len(group)
        if math.hypot(mean_dx, mean_dy) < COHERENCE * spread:
            continue
        middle = ((min(xs) + max(xs)) / 2 / width, (min(ys) + max(ys)) / 2 / height)
        regions.append({
            "box": (min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height),
            "points": len(group), "speed": math.hypot(mean_dx, mean_dy),
            "dx": mean_dx, "dy": mean_dy, "heading": direction(mean_dx, mean_dy),
            "where": place(*middle),
        })
    regions.sort(key=lambda region: region["points"], reverse=True)

    # 3. A 4 x 3 flow map: mean residual motion and moving count per cell.
    cells = []
    for index in range(12):
        column, row = index % 4, index // 4
        inside = [item for item in moving
                  if min(3, int(item[0] / width * 4)) == column and min(2, int(item[1] / height * 3)) == row]
        if inside:
            cells.append((sum(item[2] for item in inside) / len(inside),
                          sum(item[3] for item in inside) / len(inside), len(inside)))
        else:
            cells.append((0.0, 0.0, 0))

    return {"camera": camera, "camera_shift": (shift_x, shift_y), "regions": regions,
            "cells": cells, "summary": sentence(camera, regions)}


def sentence(camera: str | None, regions: list[dict[str, Any]]) -> str:
    """One plain line about the motion, most significant first."""
    parts = []
    if camera:
        parts.append(f"The camera itself is moving ({_camera_word(camera)}).")
    if regions:
        described = [f"{_where(region['where'])}, heading {region['heading']}" for region in regions[:3]]
        more = f" and {len(regions) - 3} more" if len(regions) > 3 else ""
        if len(regions) == 1:
            parts.append(f"Something is moving {described[0]}.")
        else:
            parts.append(f"{len(regions)} things are moving: {'; '.join(described)}{more}.")
    elif camera:
        parts.append("Nothing moves against the scene.")
    else:
        parts.append("The scene is still.")
    return " ".join(parts)


def _where(where: str) -> str:
    """'left' -> 'on the left', 'centre' -> 'in the centre', 'top right' -> 'at the top right'."""
    if where == "centre":
        return "in the centre"
    if where in {"left", "right"}:
        return f"on the {where}"
    return f"at the {where}"


def _camera_word(heading: str) -> str:
    # Points drift the opposite way to the camera: a pan to the right moves
    # the picture's content left.
    opposite = {"right": "panning left", "left": "panning right", "up": "tilting down",
                "down": "tilting up", "up-right": "moving down-left", "up-left": "moving down-right",
                "down-right": "moving up-left", "down-left": "moving up-right"}
    return opposite.get(heading, "moving")
