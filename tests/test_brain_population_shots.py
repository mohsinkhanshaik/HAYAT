r"""Brain 5D with the owner's real memory and knowledge neurons (read-only), for a visual review and timing.

    $env:HAYAT_SHOTS = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_brain_population_shots.py

Reads data/memory/agent.db and data/knowledge/knowledge.db through read-only connections; writes only PNGs
to .test-runs/gui-shots/.
"""
from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_SHOTS") != "1", reason="screenshots on request")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".test-runs" / "gui-shots"


def _grab(window, name):
    from PIL import ImageGrab

    window.update()
    OUT.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(window=int(window.wm_frame(), 16)).save(OUT / f"{name}.png")


def _timed(widget, frames=8):
    samples = []
    for step in range(frames):
        widget.angle += .05
        started = time.perf_counter()
        widget.redraw()
        widget.update_idletasks()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 1)


def _copy(source: Path, target: Path) -> Path:
    """A consistent read-only snapshot of an owner store (SQLite backup API); the original is never written."""
    import sqlite3

    real = sqlite3.connect.real_connect  # the suite redirects data/ paths to its sandbox; this copy is read-only
    with real(f"file:{source.as_posix()}?mode=ro", uri=True) as origin, real(target) as copy:
        origin.backup(copy)
    return target


def test_capture_the_six_times_brain(shared_tk, tmp_path):
    from app.brain_population import SUBSYSTEMS, build
    from app.gui_brain import BrainGraph
    from app.gui_theme import Palette
    from tests.test_brain_components import full_snapshot

    memory, knowledge = ROOT / "data/memory/agent.db", ROOT / "data/knowledge/knowledge.db"
    if not memory.is_file():
        pytest.skip("no owner memory store on this machine")
    memory, knowledge = _copy(memory, tmp_path / "agent.db"), _copy(knowledge, tmp_path / "knowledge.db")
    stores = {name: _copy(ROOT / "data" / relative, tmp_path / f"{name}.db")
              for name, relative in (("tool_understanding", "tool_understanding.db"), ("experience", "memory/experience.db"),
                                     ("learning", "learning/learning.db"), ("events", "memory/events.db"),
                                     ("code_monitor", "memory/code-monitor.db"), ("authority", "memory/authority.db"),
                                     ("intelligence", "memory/intelligence.db")) if (ROOT / "data" / relative).is_file()}
    # The real model store is only read (the GGUF headers), never written.
    stores.update({"code_root": ROOT / "app", "models_dir": ROOT / "runtime" / "ollama-models",
                   "answer_model": "hayat:latest", "embedding_model": "nomic-embed-text:latest"})
    shared_tk.deiconify()
    window = __import__("tkinter").Toplevel(shared_tk)
    window.geometry("1100x640+0+0")
    window.attributes("-topmost", True)
    widget = BrainGraph(window, Palette, lambda *_: None)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    window.update()
    snapshot = full_snapshot()
    widget.set_components(snapshot)
    window.update()
    before = _timed(widget)
    _grab(window, "60-brain-before-population")
    started = time.perf_counter()
    snapshot.population = build(SUBSYSTEMS + 753, memory, knowledge, stores=stores, force=True)  # the live base, 21 + 753
    build_ms = (time.perf_counter() - started) * 1000
    widget.set_components(snapshot)
    window.update()
    after = _timed(widget)
    widget.mesh_detail = "Auto"
    widget._drag_x = 1  # what a drag looks like to the renderer: the lighter moving frame
    moving = _timed(widget)
    widget._drag_x = None
    widget.redraw()
    _grab(window, "61-brain-six-times")
    widget.show_all_tools = True
    index = max(range(len(snapshot.population)), key=lambda i: len(snapshot.population.neighbours(i)))
    widget.select_population(index)
    window.update()
    _grab(window, "62-brain-neuron-chosen")
    print("\ncounts", snapshot.population.counts())
    print(f"build {build_ms:.0f} ms · redraw before {before} ms · after {after} ms · moving {moving} ms · "
          f"population layer {widget._population_ms:.0f} ms")
    window.destroy()
    shared_tk.withdraw()
