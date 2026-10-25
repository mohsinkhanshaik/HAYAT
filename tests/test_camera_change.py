"""Camera change checks tell movement from light, and say where in the picture something moved."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from app.perception.camera.change import compare, prepare


def room(seed: int = 1):
    rng = np.random.default_rng(seed)
    frame = cv2.GaussianBlur((rng.random((480, 640)) * 60 + 90).astype(np.uint8), (0, 0), 3)
    cv2.rectangle(frame, (300, 200), (360, 300), 40, -1)
    return frame


def with_person(frame):
    changed = frame.copy()
    cv2.rectangle(changed, (40, 300), (110, 470), 200, -1)
    return changed


def test_sensor_noise_is_nothing():
    base = room()
    noisy = np.clip(base.astype(int) + np.random.default_rng(2).normal(0, 3, base.shape), 0, 255).astype(np.uint8)
    assert compare(prepare(base), prepare(noisy))["kind"] == "none"


@pytest.mark.parametrize("relight", [
    lambda frame: cv2.convertScaleAbs(frame, alpha=1.35),                                       # webcam re-exposes
    lambda frame: cv2.add(frame, np.tile(np.linspace(0, 50, 640), (480, 1)).astype(np.uint8)),  # a lamp to one side
])
def test_a_change_of_light_is_not_movement(relight):
    base = room()
    found = compare(prepare(base), prepare(relight(base)))
    # The old check (mean difference >= 12) called both of these a change.
    assert found["kind"] == "lighting" and found["moved"] is False and found["raw_difference"] >= 12


def test_a_person_in_one_corner_is_found_and_placed():
    base = room()
    found = compare(prepare(base), prepare(with_person(base)))
    # The old check missed this: the mean difference over the whole frame is only about 3.
    assert found["raw_difference"] < 12
    assert found["kind"] == "movement" and found["moved"] and found["where"] == "bottom left"
    assert 2 < found["changed_area_pct"] < 20 and found["box"][0] < 0.1


def furnished(seed: int = 1):
    """A room with hard edges: furniture, frames, a window -- where clipping and panning show."""
    rng = np.random.default_rng(seed)
    frame = np.full((480, 640), 120, np.uint8)
    for _ in range(60):
        x, y = int(rng.integers(0, 600)), int(rng.integers(0, 440))
        cv2.rectangle(frame, (x, y), (x + int(rng.integers(20, 120)), y + int(rng.integers(20, 120))), int(rng.integers(30, 230)), -1)
    return frame


@pytest.mark.parametrize("gain", [1.3, 0.7])
def test_a_brightness_jump_that_clips_highlights_is_still_light(gain):
    base = furnished()
    found = compare(prepare(base), prepare(cv2.convertScaleAbs(base, alpha=gain)))
    assert found["kind"] == "lighting" and found["changed_area_pct"] == 0


def test_a_small_object_appearing_is_movement():
    base = furnished()
    changed = base.copy()
    cv2.circle(changed, (560, 80), 14, 20, -1)
    found = compare(prepare(base), prepare(changed))
    assert found["kind"] == "movement" and found["where"] == "top right"


@pytest.mark.parametrize("change", [lambda frame: furnished(7), lambda frame: np.roll(frame, 60, axis=1)])
def test_a_replaced_scene_or_a_panned_camera_is_the_whole_picture(change):
    base = furnished()
    found = compare(prepare(base), prepare(change(base)))
    assert found["kind"] == "whole_picture" and found["moved"] and found["regions_changed_pct"] >= 80


class FakeCapture:
    def __init__(self, frames):
        self.frames, self.active, self.device_index = list(frames), False, 0

    def open(self, index, width, height):
        self.active = True
        return {"width": width, "height": height}

    def read(self):
        frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    def close(self):
        self.active = False


def manager_with(frames, folder):
    from app.perception.camera.manager import CameraManager

    manager = CameraManager({})
    manager.capture_backend = FakeCapture(frames)
    manager.temp_root = folder  # turning the camera off tidies this folder; never the owner's data/camera
    return manager


def test_watch_change_reports_movement_with_where_and_stops_early(tmp_path):
    base = room()
    result = manager_with([base, base, with_person(base)], tmp_path).watch_change(seconds=5, interval=0.1)
    assert result["changed"] and result["kind"] == "movement" and result["where"] == "bottom left"
    assert result["elapsed_seconds"] < 2 and result["raw_frames_retained"] is False


def test_watch_change_reports_a_lamp_as_light_not_movement(tmp_path):
    base = room()
    lamp = cv2.convertScaleAbs(base, alpha=1.35)
    result = manager_with([base, lamp], tmp_path).watch_change(seconds=1, interval=0.1)
    assert result["changed"] is False and result["kind"] == "lighting" and result["lighting_changed"]
