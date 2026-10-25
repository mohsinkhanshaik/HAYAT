"""The window that shows what the camera is actually seeing."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from app.camera_view import PREVIEW_FPS, PREVIEW_WIDTH, CameraStream, CameraView


class Palette:
    BLACK = "#07080A"
    PANEL = "#101216"
    MUTED = "#8A93A0"
    GOLD = "#D4A82C"


class FakeBackend:
    """Stands in for the real device: hands back a BGR array."""

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self.reads = 0

    def read(self):
        import numpy

        self.reads += 1
        return numpy.zeros((720, 1280, 3), dtype=numpy.uint8)


def _runtime(active: bool = True, privacy: bool = False):
    backend = FakeBackend(active)
    return SimpleNamespace(
        camera=SimpleNamespace(capture_backend=backend),
        authority=SimpleNamespace(privacy_mode=privacy),
    ), backend


@pytest.fixture(scope="module")
def _interpreter(shared_tk):
    """One Tk interpreter for the module.

    A root per test exhausts Tk part-way through -- it starts raising
    `invalid command name "tcl_findLibrary"`, which this fixture then reported
    as "no display available", skipping tests for a reason that had nothing to
    do with displays.
    """
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, hidden=True)
    yield window
    close_window(window)


@pytest.fixture
def root(_interpreter):
    """A clean frame inside the shared interpreter, discarded after the test."""
    holder = tk.Frame(_interpreter)
    yield holder
    try:
        holder.destroy()
    except tk.TclError:
        pass


def _settle(root, view, frames: int = 3, seconds: float = 5.0):
    deadline = time.time() + seconds
    while time.time() < deadline and view.stream._frames < frames:
        root.update()
        time.sleep(0.02)
    return view.stream._frames


# ------------------------------------------------------------------ the picture


def test_frames_reach_the_screen(root):
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        assert _settle(root, view) >= 3
        assert backend.reads >= 3
        assert view.stream._photo is not None
        # Scaled down: 1280 wide would make the preview larger than the
        # assistant it belongs to.
        assert view.stream._photo.width() == PREVIEW_WIDTH
    finally:
        view.close()


def test_the_reader_never_touches_tk(root):
    """Tk is thread-confined, exactly like Playwright.

    The first version of this called `window.after` from the reader thread and
    raised `main thread is not in main loop`. The reader now stops at a PIL
    image and the UI thread turns it into a Tk one.
    """
    import ast
    import inspect
    import textwrap

    def names_used(method):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        # The docstring explains the rule and names ImageTk while doing so, so
        # this reads the code rather than the prose.
        return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            alias.name for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

    grab = names_used(CameraStream._grab)
    assert "ImageTk" not in grab, "the reader thread is building Tk objects"
    assert "ImageTk" in names_used(CameraStream._consume)

    body = inspect.getsource(CameraStream._pump) + inspect.getsource(CameraStream._grab)
    assert "self.window" not in body, "the reader thread is touching the window"


def test_a_slow_painter_gets_the_newest_frame_not_a_backlog(root):
    runtime, _ = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        # Whatever the reader produced last is one image, never a queue.
        assert not isinstance(view.stream._pending, (list, tuple))
    finally:
        view.close()


# ------------------------------------------------------------------- privacy


def test_privacy_mode_stops_the_view_looking(root):
    """A live view of the room is exactly what privacy mode exists to stop.
    A preview that kept running would make the setting a lie."""
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        runtime.authority.privacy_mode = True
        time.sleep(0.4)
        for _ in range(20):
            root.update()
            time.sleep(0.02)
        settled = backend.reads
        time.sleep(0.5)
        for _ in range(20):
            root.update()
            time.sleep(0.02)

        assert backend.reads == settled, "the device was read while privacy was on"
        assert "not looking" in view.canvas.cget("text").lower()
    finally:
        view.close()


def test_a_camera_that_is_off_says_so_rather_than_showing_the_last_frame(root):
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        backend.active = False
        time.sleep(0.3)
        for _ in range(20):
            root.update()
            time.sleep(0.02)
        assert "camera is off" in view.canvas.cget("text").lower()
    finally:
        view.close()


def test_nothing_is_written_to_disk(root, tmp_path, monkeypatch):
    """The whole point is that this is the live device, not a recording.

    Checked by watching the working directory rather than by grepping for
    function names, because the second kind of test passes the moment someone
    writes a file a different way.
    """
    monkeypatch.chdir(tmp_path)
    runtime, _ = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        assert _settle(root, view, frames=10) >= 10
    finally:
        view.close()

    assert list(tmp_path.iterdir()) == []

    import inspect

    source = inspect.getsource(CameraStream)
    for writer in ("imwrite", "write_bytes", "write_text", "imsave"):
        assert writer not in source, f"the preview calls {writer}"


# ----------------------------------------------------------------- lifecycle


def test_closing_the_view_does_not_switch_the_camera_off(root):
    """Turning the camera off stays a separate, deliberate act. A preview that
    silently owned the device would be its own kind of surprise."""
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    _settle(root, view)
    view.close()

    assert not view.alive
    assert backend.active is True  # untouched


def test_closing_twice_is_harmless(root):
    runtime, _ = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    view.close()
    view.close()
    assert not view.alive


def test_the_frame_rate_is_modest_on_purpose():
    """Fifteen looks continuous and leaves the camera and UI nearly idle."""
    assert 10 <= PREVIEW_FPS <= 20


def test_a_frame_already_in_flight_is_dropped_when_privacy_turns_on(root):
    """The reader may have grabbed a frame microseconds before the switch
    flipped. Painting it afterwards puts the room on screen after Mohsin said
    stop looking -- the wrong side of the line by a whole frame."""
    from PIL import Image

    runtime, _ = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        painted = view.stream._frames

        runtime.authority.privacy_mode = True
        # A frame that made it into the slot before the flag did.
        view.stream._pending = Image.new("RGB", (PREVIEW_WIDTH, 100))
        for _ in range(10):
            root.update()
            time.sleep(0.02)

        assert view.stream._frames == painted, "an in-flight frame was painted under privacy"
        assert "not looking" in view.canvas.cget("text").lower()
    finally:
        view.close()


# ------------------------------------------------------------------ flicker


def test_one_dropped_frame_does_not_replace_the_picture(root):
    """A single failed read between two good ones is normal for a webcam.

    If every one replaced the picture with a sentence and then the picture
    came back, the panel would strobe between the two -- which is exactly what
    Mohsin saw.
    """
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        assert view.stream._photo is not None

        failures = {"count": 0}
        original = backend.read

        def flaky():
            failures["count"] += 1
            if failures["count"] % 4 == 0:
                raise RuntimeError("dropped")
            return original()

        backend.read = flaky
        for _ in range(60):
            root.update()
            time.sleep(0.02)

        # Still showing a picture, not an explanation.
        assert view.stream._photo is not None
        assert view.canvas.cget("text") == ""
    finally:
        view.close()


def test_a_camera_that_really_went_away_is_still_reported(root):
    """Tolerating a dropped frame must not mean tolerating a dead device."""
    runtime, backend = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view)
        backend.read = lambda: (_ for _ in ()).throw(RuntimeError("gone"))
        deadline = time.time() + 4
        while time.time() < deadline and not view.canvas.cget("text"):
            root.update()
            time.sleep(0.02)
        assert view.canvas.cget("text"), "a dead camera was never reported"
    finally:
        view.close()


def test_the_indicator_does_not_blink(root):
    """A pulsing light beside a live picture reads as the picture flickering,
    and what it signalled -- that the camera is on -- the picture already
    says."""
    runtime, _ = _runtime()
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        _settle(root, view, frames=20)
        colours = set()
        for _ in range(40):
            root.update()
            colours.add(view.dot.cget("fg"))
            time.sleep(0.02)
        assert len(colours) == 1, f"the indicator changed colour: {colours}"
    finally:
        view.close()


def test_motion_map_clears_with_privacy_and_camera_off(root):
    from tests.test_camera_tracking import textured
    runtime, backend = _runtime()
    backend.read = textured
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        assert _settle(root, view) >= 3
        assert view._last_map["count"] > 0
        runtime.authority.privacy_mode = True
        for _ in range(5):
            root.update()
            time.sleep(.08)
        assert view._last_map == {}
        assert view._image_size == (0, 0)
        assert not view.eye.active
        assert view.stream._photo is None
    finally:
        view.close()


def test_stop_camera_remains_available_during_scene_analysis(root):
    import threading
    runtime, backend = _runtime()
    started, release = threading.Event(), threading.Event()
    calls = []
    def execute(name, args):
        calls.append(name)
        if name == "camera_observe":
            started.set()
            release.wait(3)
        elif name == "camera_off":
            backend.active = False
        return SimpleNamespace(success=True, output={}, error="")
    runtime.registry = SimpleNamespace(execute=execute)
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        view.understand()
        assert started.wait(1)
        view.toggle_camera()
        deadline = time.monotonic() + 2
        while backend.active and time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        assert not backend.active
        for _ in range(8):
            root.update()
            time.sleep(.02)
        assert view._busy
        view.toggle_camera()
        assert "camera_on" not in calls
    finally:
        release.set()
        view.close()


# ------------------------------------------------------------------ the live eye


class MovingBackend(FakeBackend):
    """A low-detail room with a textured object crossing it, left to right."""

    def read(self):
        import cv2
        import numpy

        self.reads += 1
        rng = numpy.random.default_rng(7)
        room = cv2.resize((rng.random((60, 80)) * 255).astype(numpy.uint8), (640, 480))
        room = cv2.GaussianBlur(room, (61, 61), 0)
        patch = (numpy.random.default_rng(3).random((100, 100)) * 255).astype(numpy.uint8)
        # Bounce smoothly between the walls: a wrap-around would teleport it.
        travel = (self.reads * 8) % 800
        x = 60 + (travel if travel < 400 else 800 - travel)
        room[190:290, x:x + 100] = patch
        return cv2.cvtColor(room, cv2.COLOR_GRAY2BGR)


def _settle_scene(root, view, seconds: float = 8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        said = view.summary.get()
        if "moving" in said and ("heading right" in said or "heading left" in said):
            return True
        time.sleep(0.02)
    return False


def test_the_eye_says_what_is_moving_and_maps_it(root):
    runtime = SimpleNamespace(camera=SimpleNamespace(capture_backend=MovingBackend()),
                              authority=SimpleNamespace(privacy_mode=False))
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        assert _settle_scene(root, view), view.summary.get()
        assert "camera itself" not in view.summary.get()        # the room is still; only the object moves
        assert any(value > 0 for value in view._heat)            # the map glows where it moved
        assert view.map.find_withtag("arrow")                     # and says which way
        view.toggle_view()
        assert view.processor.view == "mesh" and str(view.view_button.cget("text")) == "Points view"
        view.toggle_view()
        assert view.processor.view == "points"
        view.reset_map()
        assert view._heat == [0.0] * 12
    finally:
        view.close()


def test_the_eye_goes_quiet_when_the_camera_stops(root):
    runtime, backend = _runtime(active=False)
    view = CameraView(root, runtime, Palette)
    view.window.withdraw()
    try:
        deadline = time.time() + 3
        while time.time() < deadline and "No live picture" not in view.summary.get():
            root.update()
            time.sleep(0.02)
        assert "No live picture" in view.summary.get()
        assert view._heat == [0.0] * 12
    finally:
        view.close()
