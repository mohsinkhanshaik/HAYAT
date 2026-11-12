"""Saved evidence and all prediction inputs stay reachable at the minimum size."""
import queue
from types import SimpleNamespace

from app.gui_research_lab import ResearchLab
from app.gui_theme import Palette, style_controls
from tests.conftest import close_window, open_window


def test_minimum_window_and_saved_model_evidence(shared_tk, monkeypatch):
    pending = queue.Queue()
    owner = open_window(shared_tk, '900x700+10+10')
    saved = {'experiment_id': 7, 'at': '2026-11-09T18:30:00+00:00', 'source': 'example.csv',
             'target': 'sales', 'verdict': 'Nothing predictable found.', 'metric': 'RMSE',
             'validation': 'chronological', 'integrity': [], 'uncertainty': {'measured_on': 40},
             'candidates': [{'model': 'baseline', 'validation': 1.0, 'test': 1.1, 'folds_beating_baseline': 0}]}

    class GUI(Palette):
        root = owner
        runtime = SimpleNamespace(model_discovery=SimpleNamespace(
            recent=lambda limit: [{'id': 7, 'source': 'example.csv', 'verdict': saved['verdict'], 'at': saved['at']}],
            detail=lambda identity: saved if identity == 7 else None))

        def _post(self, function, *args):
            pending.put((function, args))

        def _open_reverse_engineer(self):
            pass

    monkeypatch.setattr('app.research_lab.ghidra_status', lambda *args: {'installed': False})
    from tkinter import ttk
    ttk.Style(shared_tk).theme_use('clam')
    style_controls(shared_tk, Palette)
    window = ResearchLab(GUI())
    try:
        window.geometry('840x620+20+20')
        window.lab_scene.select('Model discovery')
        assert 'Fits models against a baseline' in window.overview_text.get('1.0', 'end')
        assert len(window.lab_scene.points) == 10 and len(window.lab_scene.segments) == 5
        window._overview_open('Regression gate')
        assert window.tabs.tab(window.tabs.select(), 'text') == 'Robustness'
        window.tabs.select(window.pages['Model discovery'])
        shared_tk.update()
        window.discovery_views.select(window.discovery_history)
        shared_tk.update()
        assert window.experiment_tree.winfo_height() >= 40
        assert window.experiment_tree.winfo_rooty() + window.experiment_tree.winfo_height() < (
            window.status.winfo_rooty())
        window.experiment_tree.selection_set('7')
        window._show_experiment()
        shared_tk.update()
        assert 'Nothing predictable found' in window.discovery_text.get('1.0', 'end')
        assert 'Saved experiment 7' in window.status.cget('text')
        assert window.candidate_tree.item('c0', 'text') == 'baseline'
        right = window.winfo_rootx() + window.winfo_width()
        for field in window.discovery_fields.values():
            assert field.winfo_viewable() and field.winfo_rootx() + field.winfo_width() <= right
        assert window.candidate_tree.winfo_height() >= 40
        assert window.discovery_text.winfo_height() >= 55
        assert window.candidate_tree.cget('xscrollcommand')
        assert window.discovery_text.cget('yscrollcommand')
        # Tests use synthetic evidence only; save a target-window screenshot for visual QA.
        import os
        if os.environ.get('HAYAT_SHOTS') == '1':
            from pathlib import Path
            from PIL import ImageGrab
            folder = Path('.test-runs/gui-shots')
            folder.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / '100-research-lab-upgrade.png')
            window.tabs.select(window.pages['Overview'])
            shared_tk.update()
            ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / '101-research-lab-relationships.png')
    finally:
        window.close()
        close_window(owner)
