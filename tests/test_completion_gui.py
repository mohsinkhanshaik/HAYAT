import base64
import io
import time
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from app.gui_gallery import ImageGallery
from app.gui_task_graph import TaskGraphEditor
from app.gui_theme import Palette, apply_theme
from app.memory.database import MemoryDatabase


@pytest.fixture(scope="module")
def root(shared_tk):
    from tests.conftest import close_window, open_window

    window = open_window(shared_tk, hidden=True)
    yield window
    close_window(window)


def test_plan_editor_saves_edits_and_routes_to_composer_without_running_tools(root, tmp_path):
    memory = MemoryDatabase(tmp_path / "memory.db")
    memory.initialize()
    runtime = SimpleNamespace(memory=memory, authority=SimpleNamespace(privacy_mode=False))
    composed = []
    editor = TaskGraphEditor(root, Palette, runtime, composed.append)
    editor.goal.insert("1.0", "Build the report")
    editor.add()
    editor.goal.delete("1.0", "end")
    editor.goal.insert("1.0", "Verify totals")
    editor.dependencies.set("1")
    editor.add()
    editor.save()
    editor.new()
    editor.load()
    editor.review()
    assert "Verify totals (after: 1)" in composed[0]
    assert len(editor.canvas.find_all()) == 5
    editor.tree.selection_set("1")
    editor.remove()
    assert editor.nodes == [{"id": "2", "goal": "Verify totals", "depends_on": []}]
    runtime.authority.privacy_mode = True
    editor.refresh()
    assert editor.nodes == []
    editor.destroy()


def test_gallery_paints_off_thread_results_and_hides_them_in_privacy(root):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", (100, 50), "blue").save(stream, format="PNG")
    def execute(name, arguments):
        payload = {"images": [{"path": "example.png", "origin": "Generated", "prompt": "Example"}], "has_more": False}
        if name == "image_preview":
            payload = {"png": base64.b64encode(stream.getvalue()).decode()}
        return SimpleNamespace(success=True, output=payload, error=None)
    runtime = SimpleNamespace(authority=SimpleNamespace(privacy_mode=False), registry=SimpleNamespace(execute=execute))
    gallery = ImageGallery(root, Palette, runtime, lambda text: None)
    gallery.refresh()
    deadline = time.monotonic() + 3
    while gallery._pending and time.monotonic() < deadline:
        root.update()
        gallery.drain()
        time.sleep(.01)
    assert len(gallery.photos) == 1 and "1 images" in gallery.status.get()
    runtime.authority.privacy_mode = True
    gallery.drain()
    assert not gallery.cards.winfo_children()
    gallery.destroy()


def test_theme_changes_existing_text_canvas_and_native_controls(root):
    class TestPalette(Palette):
        pass
    text = tk.Text(root, bg=TestPalette.BLACK, fg=TestPalette.WHITE)
    text.tag_configure("status", foreground=TestPalette.MUTED)
    canvas = tk.Canvas(root, bg=TestPalette.BLACK)
    item = canvas.create_rectangle(0, 0, 20, 20, fill=TestPalette.PANEL_2)
    apply_theme(root, TestPalette, "Light")
    assert text.cget("background") == TestPalette.BLACK == "#F6F7FA"
    assert canvas.itemcget(item, "fill") == TestPalette.PANEL_2
    apply_theme(root, TestPalette, "High contrast")
    assert text.cget("background") == "#000000"
    assert text.tag_cget("status", "foreground") == TestPalette.MUTED


def test_button_states_follow_each_theme(root):
    from tkinter import ttk

    class LocalPalette(Palette):
        pass

    for name in ("Dark", "Light", "High contrast"):
        apply_theme(root, LocalPalette, name)
        style = ttk.Style(root)
        assert style.lookup("HayatAccent.TButton", "foreground", ("active",)) == LocalPalette.BLACK
        assert style.lookup("HayatAccent.TButton", "background", ("active",)) == LocalPalette.GOLD_SOFT
        assert style.lookup("Hayat.TButton", "foreground", ("pressed",)) == "#FFFFFF"
        assert style.lookup("Hayat.TButton", "bordercolor", ("focus",)) == LocalPalette.GOLD
        assert style.lookup("Hayat.Vertical.TScrollbar", "troughcolor") == LocalPalette.BLACK
        assert style.lookup("Hayat.Horizontal.TProgressbar", "background") == LocalPalette.GOLD
        # A scrollbar with nothing to scroll is "disabled"; clam's default for
        # that state is a pale grey stripe across a dark window.
        for bar in ("Horizontal.TScrollbar", "Vertical.TScrollbar", "Hayat.Vertical.TScrollbar"):
            for state in ((), ("disabled",), ("active",), ("pressed",)):
                assert style.lookup(bar, "background", state).upper() in {
                    LocalPalette.PANEL.upper(), LocalPalette.PANEL_2.upper(),
                    LocalPalette.PANEL_HOVER.upper(), LocalPalette.ROYAL.upper()}, (name, bar, state)


def test_theme_text_and_primary_buttons_keep_readable_contrast():
    from app.gui_theme import THEMES

    def luminance(hex_colour):
        channels = [int(hex_colour[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                  for value in channels]
        return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))

    for name, palette in THEMES.items():
        for foreground, background in (("WHITE", "PANEL"), ("MUTED", "PANEL"),
                                       ("BLACK", "GOLD"), ("BLACK", "GOLD_SOFT")):
            light, dark = sorted((luminance(palette[foreground]), luminance(palette[background])),
                                 reverse=True)
            assert (light + .05) / (dark + .05) >= 4.5, (name, foreground, background)


def test_training_review_keeps_rejects_and_skips_one_record_at_a_time(root, tmp_path):
    from app.gui_training import TrainingReview
    from app.training.pipeline import TrainingDataPipeline

    pipeline = TrainingDataPipeline(tmp_path / "training", config={})
    for text in ("create an image of a tiger", "open notepad", "what is the time"):
        pipeline.ingest("planning", {"input": text, "ideal_plan": ["tool"]}, provenance={"source": "test"})
    runtime = SimpleNamespace(training=pipeline, authority=SimpleNamespace(privacy_mode=False))
    review = TrainingReview(root, Palette, runtime, lambda text: None)

    def settle():
        deadline = time.monotonic() + 3
        while review._pending and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        review.drain()

    review.refresh(); settle()
    assert "3 waiting" in review.status.get()
    assert "Request:" in review.text.get("1.0", "end")
    review.decide(True); settle()
    review.decide(False); settle()
    assert "1 waiting" in review.status.get()
    states = pipeline.status()["states"]
    assert states["REJECTED"] == 1 and states["TRAINING_READY"] + states["HELD_OUT"] == 1
    review.decide(False); settle()
    assert "Nothing is waiting" in review.text.get("1.0", "end")
    assert str(review.keep["state"]) == "disabled"
    review.destroy()
