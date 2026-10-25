"""The live eye's tracker, on synthetic frames only: no camera is ever opened."""
from __future__ import annotations

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from app.perception.camera.tracking import EyeProcessor, SceneTracker


def textured(shift_x: int = 0, shift_y: int = 0, size=(240, 320), seed=7):
    """A random-texture scene, rolled by a known number of pixels."""
    rng = np.random.default_rng(seed)
    base = (rng.random((size[0] // 4, size[1] // 4)) * 255).astype(np.uint8)
    base = cv2.resize(base, (size[1], size[0]), interpolation=cv2.INTER_NEAREST)
    moved = np.roll(np.roll(base, shift_y, axis=0), shift_x, axis=1)
    return cv2.cvtColor(moved, cv2.COLOR_GRAY2BGR)


def test_a_still_scene_is_tracked_but_nothing_moves():
    tracker = SceneTracker()
    first = tracker.update(textured())
    assert first["count"] > 20
    for _ in range(3):
        result = tracker.update(textured())
    assert result["moving"] == 0 and result["motion_px"] < .5


def test_a_shifting_scene_reports_the_true_shift_and_moving_cells():
    tracker = SceneTracker()
    tracker.update(textured(0))
    result = tracker.update(textured(4))
    assert result["motion_px"] == pytest.approx(4, abs=.6)
    assert result["moving"] > 10
    assert sum(cell["moving"] for cell in result["cells"]) == result["moving"]
    assert all(len(track["points"]) >= 2 for track in result["tracks"][:10])


def test_features_stay_inside_a_selected_region():
    tracker = SceneTracker()
    roi = (.5, .5, 1.0, 1.0)
    for step in range(3):
        result = tracker.update(textured(step), roi)
    w, h = result["width"], result["height"]
    for track in result["tracks"]:
        x, y = track["points"][-1]
        assert x >= roi[0] * w - 1 and y >= roi[1] * h - 1


def test_a_new_frame_size_resets_rather_than_mixing_scenes():
    tracker = SceneTracker()
    tracker.update(textured())
    result = tracker.update(textured(size=(120, 160)))
    assert result["width"] == 160 and result["motion_px"] == 0


def test_the_processor_draws_overlays_and_obeys_pause_and_reconfigure():
    processor = EyeProcessor()
    picture = processor(textured())
    assert picture.info["tracking"]["enabled"] and picture.size == (320, 240)
    processor.configure(enabled=False)
    paused = processor(textured(3))
    assert paused.info["tracking"] == {"enabled": False}
    processor.configure(enabled=True, roi=(.1, .1, .6, .6))
    again = processor(textured(3))
    assert again.info["tracking"]["roi"] == (.1, .1, .6, .6)
    processor.reset()
    assert processor.tracker.paths == []


# --------------------------------------------------------------- understanding

from app.perception.camera.scene_motion import direction, place, sentence


def wall(size=(240, 320)):
    """A low-detail, wall-like background, as most rooms are."""
    return cv2.GaussianBlur(textured(size=size), (31, 31), 0)


def with_object(x, y=100, background=None):
    frame = (background if background is not None else wall()).copy()
    rng = np.random.default_rng(3)
    patch = cv2.cvtColor((rng.random((50, 50)) * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    frame[y:y + 50, x:x + 50] = patch
    return frame


def run(frames, processor=None):
    processor = processor or EyeProcessor()
    result = None
    for frame in frames:
        result = processor(frame).info["tracking"]
    return result


def test_direction_and_place_words():
    assert direction(5, 0) == "right" and direction(-5, 0) == "left"
    assert direction(0, -5) == "up" and direction(0, 5) == "down"      # screen y grows downward
    assert direction(3, -3) == "up-right" and direction(0, 0) == "still"
    assert place(.1, .5) == "left" and place(.5, .5) == "centre"
    assert place(.9, .1) == "top right" and place(.5, .9) == "bottom"


def test_a_moving_object_is_found_where_it_is_and_heading_the_right_way():
    background = wall()
    for xs, heading in ((range(40, 200, 4), "right"), (range(200, 40, -4), "left")):
        scene = run([with_object(x, background=background) for x in xs])["scene"]
        assert scene["camera"] is None
        assert scene["regions"] and scene["regions"][0]["heading"] == heading
        assert f"heading {heading}" in scene["summary"]
    scene = run([with_object(130, y, background) for y in range(20, 150, 5)])["scene"]
    assert scene["regions"][0]["heading"] == "down"


def test_points_are_seeded_where_things_change(monkeypatch):
    """Spread evenly over a busy room, the tracker gave a moving object two or
    three points and never saw it move. Seeding where frames differ finds it,
    and follows it about three times as densely against a plain wall."""
    busy = [with_object(x, background=textured()) for x in range(40, 200, 4)]
    plain = [with_object(x) for x in range(40, 200, 4)]
    seeded_busy, seeded_plain = run(busy)["scene"]["regions"], run(plain)["scene"]["regions"]
    monkeypatch.setattr(SceneTracker, "MOTION_POINTS", 0)
    unseeded_busy, unseeded_plain = run(busy)["scene"]["regions"], run(plain)["scene"]["regions"]
    assert seeded_busy and unseeded_busy == []
    assert seeded_plain[0]["points"] >= 2 * unseeded_plain[0]["points"]


def test_a_panning_camera_is_not_mistaken_for_the_scene_moving():
    scene = run([textured(0), textured(5)])["scene"]
    assert scene["camera"] == "right"
    assert "panning left" in scene["summary"] and scene["regions"] == []


def test_a_still_or_featureless_view_says_so():
    assert "still" in run([textured(0)] * 4)["scene"]["summary"]
    blank = np.full((240, 320, 3), 128, np.uint8)
    assert "Too little detail" in run([blank] * 3)["scene"]["summary"]


def test_the_sentence_leads_with_the_camera_and_counts_regions():
    region = {"where": "left", "heading": "right"}
    assert sentence("left", []) == "The camera itself is moving (panning right). Nothing moves against the scene."
    assert sentence(None, [region]) == "Something is moving on the left, heading right."
    five = sentence(None, [region] * 5)
    assert five.startswith("5 things are moving: on the left, heading right;") and "and 2 more" in five
    assert sentence(None, [{"where": "top right", "heading": "down"}]) == "Something is moving at the top right, heading down."
    assert sentence(None, [{"where": "centre", "heading": "up"}]) == "Something is moving in the centre, heading up."


def test_the_mesh_view_draws_without_restarting_tracking():
    processor = EyeProcessor()
    processor(textured(0))
    tracked = len(processor.tracker.paths)
    processor.view = "mesh"
    picture = processor(textured(0))
    assert picture.size == (320, 240) and len(processor.tracker.paths) >= tracked


def test_an_object_filling_most_tracked_points_is_not_called_a_camera_pan():
    """On a plain wall, points seeded on a moving object can outnumber the
    background's; they all agree, but they are all in one place."""
    background = cv2.GaussianBlur(textured(), (61, 61), 0)
    scene = run([with_object(x, background=background) for x in range(40, 200, 4)])["scene"]
    assert scene["camera"] is None
    assert scene["regions"][0]["heading"] == "right"


# ------------------------------------------------------ real cameras are noisy

_noise = np.random.default_rng(11)


def _degrade(frame, i, *, grain=6.0, exposure=0.0, glow=0.0):
    """Sensor grain, auto-exposure flicker and a pulsing screen glow on the right."""
    out = frame.astype(np.float32)
    if exposure:
        out *= 1 + exposure * np.sin(i * 1.3)
    if glow:
        light = np.zeros(out.shape[:2], np.float32)
        light[:, int(out.shape[1] * .62):] = 1.0
        light = cv2.GaussianBlur(light, (101, 101), 0)[..., None]
        out *= 1 + glow * np.sin(i * 1.7) * light
    out += _noise.normal(0, grain, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def _false_alarms(frames):
    processor = EyeProcessor()
    return sum(bool(processor(frame).info["tracking"]["scene"]["regions"]) for frame in frames)


@pytest.mark.parametrize("conditions", [
    {"grain": 12.0},                                      # heavy sensor grain
    {"exposure": .25},                                    # auto-exposure flicker: was 32/40 false
    {"glow": .30},                                        # a monitor's glow beside the camera: was 35/60
    {"grain": 12.0, "exposure": .25, "glow": .30},        # all at once
])
def test_a_still_room_is_never_reported_as_moving(conditions):
    for room in (wall((480, 640)), textured(size=(480, 640))):
        assert _false_alarms([_degrade(room, i, **conditions) for i in range(40)]) == 0


def test_real_movement_is_still_seen_through_grain_flicker_and_glow():
    frames = [_degrade(with_object(x), i, exposure=.12, glow=.30) for i, x in enumerate(range(40, 200, 4))]
    processor = EyeProcessor()
    seen = [processor(frame).info["tracking"]["scene"] for frame in frames]
    assert sum(bool(scene["regions"]) for scene in seen) >= 35
    assert seen[-1]["regions"][0]["heading"] == "right"


def test_a_region_must_persist_a_frame_before_it_is_reported():
    background = wall()
    processor = EyeProcessor()
    reported = [bool(processor(with_object(x, background=background)).info["tracking"]["scene"]["regions"])
                for x in range(40, 80, 4)]
    assert reported[0] is False and reported[-1] is True      # not on first sight; soon after
    # Reconfiguring starts over: a region from before cannot confirm a new one.
    processor.configure(enabled=True, roi=(0.0, 0.0, 1.0, 1.0))
    after = processor(with_object(84, background=background)).info["tracking"]["scene"]
    assert after["regions"] == [] and processor._last_regions == []
