"""The cause-and-effect and graph-network tabs draw what was measured, at the smallest window size too."""
from __future__ import annotations

import os
import queue
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")

from app.causal_lab import CausalLab  # noqa: E402
from app.graph_network import GraphLab  # noqa: E402
from tests.test_causal_lab import confounded  # noqa: E402
from tests.test_graph_network import communities  # noqa: E402


@pytest.fixture
def lab(shared_tk, tmp_path, monkeypatch):
    from tkinter import ttk

    from app.gui_research_lab import ResearchLab
    from app.gui_theme import Palette, style_controls
    from tests.conftest import close_window, open_window

    pending: queue.Queue = queue.Queue()
    owner = open_window(shared_tk, '900x700+10+10')
    table = tmp_path / "trial.csv"
    confounded(rows=700).to_csv(table, index=False)
    links = tmp_path / "links.csv"
    _count, edges = communities(groups=3, size=20, seed=6)
    links.write_text("from,to\n" + "\n".join(f"n{a},n{b}" for a, b in edges), encoding="utf-8")

    class GUI(Palette):
        root = owner
        runtime = SimpleNamespace(causal_lab=CausalLab(tmp_path / "causal.db"), graph_lab=GraphLab(tmp_path / "graph.db"))

        def _post(self, function, *args):
            pending.put((function, args))

        def _open_reverse_engineer(self):
            pass

    monkeypatch.setattr('app.research_lab.ghidra_status', lambda *args: {'installed': False})
    ttk.Style(shared_tk).theme_use('clam')
    style_controls(shared_tk, Palette)
    window = ResearchLab(GUI())

    def settle(done=None, quiet=0.5):
        deadline, last = time.monotonic() + 60, time.monotonic()
        while time.monotonic() < deadline:
            shared_tk.update()
            try:
                function, arguments = pending.get_nowait()
            except queue.Empty:
                if done() if done else time.monotonic() - last > quiet:
                    return
                time.sleep(.01)
                continue
            function(*arguments)
            last = time.monotonic()
        pytest.fail('the background work did not report back')

    window.geometry('840x620+20+20')
    settle()
    try:
        yield SimpleNamespace(window=window, settle=settle, table=table, links=links, tk=shared_tk)
    finally:
        window.close()
        close_window(owner)


def shot(window, name):
    if os.environ.get('HAYAT_SHOTS') == '1':
        from pathlib import Path

        from PIL import ImageGrab
        folder = Path('.test-runs/gui-shots')
        folder.mkdir(parents=True, exist_ok=True)
        window.lift()
        for _ in range(4):
            window.update()
            time.sleep(.05)
        ImageGrab.grab(window=int(window.wm_frame(), 16)).save(folder / name)


def test_every_tab_title_is_whole_at_the_smallest_size(lab):
    window = lab.window
    from tkinter import font as tkfont
    from tkinter import ttk

    titles = [window.tabs.tab(index, 'text') for index in window.tabs.tabs()]
    assert titles == ['Overview', 'Robustness', 'Cyber defence', 'Software analysis', 'Model discovery', 'Causes', 'Graph']
    assert set(window.pages) >= {'Cause & effect', 'Graph network'}        # the pages keep their full names
    # A notebook that runs out of width cuts every title short. Each tab must be at least as wide as its own words.
    font = tkfont.Font(root=window, font=ttk.Style(window).lookup('TNotebook.Tab', 'font') or 'TkDefaultFont')
    window.tabs.select(0)
    lab.tk.update()
    widths: dict[int, int] = {}
    for x in range(0, window.tabs.winfo_width()):             # which tab is under each pixel of the strip
        found = window.tabs.tk.call(window.tabs, 'identify', 'tab', x, 12)
        if found != '':
            widths[int(found)] = widths.get(int(found), 0) + 1
    assert sorted(widths) == list(range(len(titles)))         # every tab is on the strip, inside the window
    for index, title in enumerate(titles):
        assert widths[index] >= font.measure(title), f'{title!r} is cut short ({widths[index]} px for {font.measure(title)})'
    window.tabs.select(window.pages['Cyber defence'])
    window.defense_views.select(1)
    lab.settle()
    assert window.patch_text.winfo_viewable() and window.patch_text.winfo_height() >= 60
    assert window.finding_text.winfo_viewable() and window.finding_text.winfo_height() >= 60
    shot(window, '123-cyber-defense-patch-layout.png')


def test_an_effect_is_estimated_drawn_explained_and_reopened(lab):
    window, fields = lab.window, lab.window.causal_fields
    window.tabs.select(window.pages['Cause & effect'])
    lab.tk.update()
    window.estimate_effect()
    assert 'Give the table' in window.status.cget('text')
    fields['source'].insert(0, str(lab.table))
    fields['treatment'].insert(0, 'treated')
    fields['outcome'].insert(0, 'recovery')
    for entry in fields.values():                                           # the whole form fits the smallest window
        assert entry.winfo_viewable() and entry.winfo_rootx() + entry.winfo_width() <= window.winfo_rootx() + window.winfo_width()
    window.estimate_effect()
    lab.settle(lambda: window.causal_result is not None)
    result = window.causal_result
    assert result['verdict_kind'] == 'effect' and window.status.cget('text') == result['verdict']
    words = window.causal_text.get('1.0', 'end')
    assert result['verdict'] in words and 'Allowed for: severity, age' in words
    assert '✓ placebo treatment' in words and 'E-value' in words and 'Evaluation: hayat-causal-estimate-v1' in words
    # The chart holds one row per measured figure, and the reported estimate's interval is drawn where its numbers are.
    lab.tk.update()
    assert [row[2].split(':')[0] for row in window._causal_rows] == ['Plain comparison', 'Regression-adjusted', 'Doubly robust',
                                                                    'Shuffled treatment']
    assert window.causal_canvas.winfo_height() >= 100 and window.causal_text.winfo_height() >= 60
    top, bottom, said = window._causal_rows[2]
    window._pick_causal(SimpleNamespace(x=40, y=(top + bottom) / 2))
    assert window.status.cget('text') == said and '95% interval' in said
    shot(window, '120-research-lab-cause-and-effect.png')
    # An earlier estimate reopens from its record: nothing is fitted (the table is gone).
    lab.table.unlink()
    window.causal_result = None
    window.causal_views.select(1)
    lab.tk.update()
    assert window.causal_tree.get_children() == ('1',) and window.causal_tree.winfo_height() >= 40
    window.causal_tree.selection_set('1')
    window._reopen_causal()
    assert window.causal_result['interval_95'] == result['interval_95'] and 'nothing was refitted' in window.status.cget('text')
    assert window.overview_nodes['Cause & effect']['tip'].count('estimates: 1') == 1


def test_a_refused_table_is_reported_and_draws_nothing(lab):
    window, fields = lab.window, lab.window.causal_fields
    fields['source'].insert(0, str(lab.table))
    fields['treatment'].insert(0, 'no_such_column')
    fields['outcome'].insert(0, 'recovery')
    window.estimate_effect()
    lab.settle(lambda: "didn't work" in window.status.cget('text'))
    assert window.causal_result is None and window._causal_rows == [] and not window.causal_tree.get_children()


def test_a_graph_is_trained_scored_against_the_simple_rules_and_reopened(lab):
    window = lab.window
    window.tabs.select(window.pages['Graph network'])
    lab.tk.update()
    assert window.graph_fields['source'].get() == 'memory'
    window.graph_fields['source'].delete(0, 'end')
    window.graph_fields['source'].insert(0, str(lab.links))
    window.train_graph()
    lab.settle(lambda: window.graph_result is not None)
    result = window.graph_result
    assert window.status.cget('text') == result['verdict']
    labels = [row[2].split(':')[0] for row in window._graph_rows]
    assert labels == ['Graph network', 'Common neighbours', 'Adamic-Adar', 'Preferential attachment']
    words = window.graph_text.get('1.0', 'end')
    assert f"{result['hidden_links_tested']} hidden links tested" in words and 'none is created' in words
    assert 'Evaluation: hayat-link-prediction-v2' in words
    assert 'Selected on validation' in words and 'independent test scores' in words
    top, bottom, said = window._graph_rows[0]
    window._pick_graph(SimpleNamespace(x=30, y=(top + bottom) / 2))
    assert f"AUC {result['network']['auc']:.3f}" in window.status.cget('text') and '0.5 is chance' in said
    shot(window, '121-research-lab-graph-network.png')
    window.graph_result = None
    window.graph_tree.selection_set('1')
    window._reopen_graph()
    assert window.graph_result['network'] == result['network'] and 'nothing was retrained' in window.status.cget('text')
    # Memory is not available in this fixture: said plainly, nothing drawn from a failed run.
    window.graph_fields['source'].delete(0, 'end')
    window.graph_fields['source'].insert(0, 'memory')
    before = window.graph_result
    window.train_graph()
    lab.settle(lambda: "didn't work" in window.status.cget('text'))
    assert window.graph_result == before        # the earlier result stays on screen, unchanged


def test_expanded_profile_reaches_training_and_duplicate_clicks_do_not_spawn_extra_runs(lab, monkeypatch):
    import threading

    window = lab.window
    window.tabs.select(window.pages['Graph network'])
    window.graph_fields['source'].delete(0, 'end')
    window.graph_fields['source'].insert(0, str(lab.links))
    window.graph_profile.set('expanded')
    graph = window.runtime.graph_lab
    original = graph.run
    release = threading.Event()
    calls = []

    def run(source, profile='balanced'):
        calls.append(profile)
        assert release.wait(5)
        return original(source, profile=profile)

    monkeypatch.setattr(graph, 'run', run)
    try:
        window.train_graph()
        window.graph_profile.set('balanced')
        window.train_graph()
        assert window._graph_pending and str(window.graph_train_button['state']) == 'disabled'
    finally:
        release.set()
    lab.settle(lambda: window.graph_result is not None)
    assert calls == ['expanded']
    assert window.graph_result['network']['profile'] == 'expanded'
    assert window.graph_result['network']['capacity_ratio'] >= 10
    assert not window._graph_pending and str(window.graph_train_button['state']) == 'normal'
    assert len(graph.recent()) == 1
