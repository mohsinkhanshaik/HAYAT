import cv2
import numpy as np

from app.perception.camera.tracking import SceneTracker, EyeProcessor


def textured():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    for x in range(25, 290, 30):
        for y in range(25, 210, 30):
            cv2.rectangle(frame, (x, y), (x+8, y+8), (210, 210, 210), -1)
    return frame


def test_real_optical_flow_measures_known_translation_and_keeps_bounded_trails():
    tracker = SceneTracker()
    base = textured()
    first = tracker.update(base)
    assert 20 < first["count"] <= tracker.MAX_POINTS
    original_ids = {row["id"] for row in first["tracks"]}
    for offset in range(1, 18):
        moved = cv2.warpAffine(base, np.float32([[1, 0, offset*2], [0, 1, offset]]), (320, 240))
        result = tracker.update(moved)
    assert abs(result["motion_px"] - 5**.5) < .5
    assert result["moving"] > 10
    assert original_ids & {row["id"] for row in result["tracks"]}
    assert max(len(row["points"]) for row in result["tracks"]) <= tracker.TRAIL
    assert sum(c["points"] for c in result["cells"]) == result["count"]


def test_region_tracking_resets_and_empty_images_invent_no_objects():
    processor = EyeProcessor()
    processor.configure(roi=(0, 0, .5, .5))
    result = processor(textured()).info["tracking"]
    assert result["count"] > 0
    assert all(0 <= x < 160 and 0 <= y < 120 for row in result["tracks"] for x, y in row["points"])
    processor.configure(enabled=False)
    assert processor(textured()).info["tracking"] == {"enabled": False}
    processor.configure()
    result = processor(np.zeros((240, 320, 3), np.uint8)).info["tracking"]
    assert result["count"] == result["moving"] == 0
    assert all(cell["points"] == 0 for cell in result["cells"])


def test_size_change_does_not_reuse_coordinates_from_previous_frame():
    tracker = SceneTracker()
    tracker.update(textured())
    result = tracker.update(np.zeros((100, 100, 3), np.uint8))
    assert result["tracks"] == []
