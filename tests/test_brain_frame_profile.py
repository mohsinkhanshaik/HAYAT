r"""Opt-in profile of one frame of the 3D brain with the owner's real population (read-only copies).

    $env:HAYAT_UI_PROFILE = "1"; .\.venv\Scripts\python.exe -m pytest -s tests/test_brain_frame_profile.py

Prints where a still frame and a dragged frame spend their time. Asserts nothing about speed; the frame-time
budget itself is asserted in tests/test_gui_brain_frame_budget.py on a synthetic population.
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import statistics
import time

import pytest

from tests.test_brain_population_shots import ROOT, _copy

pytestmark = pytest.mark.skipif(os.environ.get("HAYAT_UI_PROFILE") != "1", reason="a diagnostic, run on request")


def test_profile_a_brain_frame(shared_tk, tmp_path):
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
    stores.update({"code_root": ROOT / "app", "models_dir": ROOT / "runtime" / "ollama-models",
                   "answer_model": "hayat:latest", "embedding_model": "nomic-embed-text:latest"})
    shared_tk.deiconify()
    window = __import__("tkinter").Toplevel(shared_tk)
    window.geometry("1100x640+0+0")
    widget = BrainGraph(window, Palette, lambda *_: None)
    widget.pack(fill="both", expand=True)
    widget.set_motion(False)
    window.update()
    snapshot = full_snapshot()
    snapshot.population = build(SUBSYSTEMS + 753, memory, knowledge, stores=stores, force=True)
    widget.set_components(snapshot)
    widget.mesh_detail = "Auto"
    window.update()
    try:
        for label, dragging in (("still", None), ("dragged", 1)):
            widget._drag_x = dragging
            samples, profile = [], cProfile.Profile()
            for _ in range(8):
                widget.angle += .05
                started = time.perf_counter()
                profile.enable()
                widget.redraw()
                widget.update_idletasks()
                profile.disable()
                samples.append((time.perf_counter() - started) * 1000)
            text = io.StringIO()
            pstats.Stats(profile, stream=text).sort_stats("tottime").print_stats(16)
            tags: dict[str, int] = {}
            for item in widget.find_all():
                for tag in widget.gettags(item)[:2]:
                    tags[tag] = tags.get(tag, 0) + 1
            print(f"\n=== {label}: median {statistics.median(samples):.1f} ms, worst {max(samples):.1f} ms per frame; "
                  f"population layer {widget._population_ms:.1f} ms; canvas items {len(widget.find_all())}: "
                  + ", ".join(f"{name} {count}" for name, count in sorted(tags.items(), key=lambda kv: -kv[1])[:9]))
            for line in text.getvalue().splitlines():
                if line.strip() and (line.lstrip()[:1].isdigit() or "ncalls" in line):
                    print("   ", line[:150])
    finally:
        widget._drag_x = None
        window.destroy()
