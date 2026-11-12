import json
import os
import time

from tests.test_gui_invention_advanced import studio as studio, create


def wait(lab):
    until = time.monotonic()+8
    while time.monotonic() < until:
        lab.update()
        if not lab._busy:
            return
        time.sleep(.01)
    raise AssertionError('Lab did not finish')


def test_lab_runs_governed_unit_and_holdout_tools_and_preserves_project(studio):
    panel, root, workbench = studio
    create(panel, root)
    before = workbench.get(panel.project_id)
    panel.open_lab()
    lab = panel._lab
    try:
        panel.open_lab()
        assert panel._lab is lab
        lab.formula.set('mass*acceleration')
        lab.unit_inputs.set('mass=kg, acceleration=m/s^2')
        lab.output_unit.set('N')
        lab.audit(); wait(lab)
        assert 'Dimensions match' in lab.unit_result.get('1.0', 'end')
        lab.convert(); wait(lab)
        assert '3600000' in lab.conversion_result.get('1.0', 'end')
        lab.formula.set('2*x')
        lab.holdouts.delete('1.0', 'end')
        lab.holdouts.insert('1.0', json.dumps([{'inputs': {'x': 8}, 'expected': 16}]))
        lab.holdout(); wait(lab)
        assert '1' in lab.holdout_result.get('1.0', 'end') and 'Unseen inputs' in lab.holdout_result.get('1.0', 'end')
        assert workbench.get(panel.project_id) == before
        panel.destroy()
        assert not lab.winfo_exists() and lab._timer is None
    finally:
        if lab.winfo_exists():
            lab.destroy()


def test_lab_errors_are_actionable_and_tabs_fit_small_enlarged_windows(studio):
    from app.gui_theme import apply_text_scale
    panel, root, _workbench = studio
    panel.open_lab()
    lab = panel._lab
    try:
        lab.from_unit.set('W'); lab.to_unit.set('J')
        lab.convert(); wait(lab)
        assert 'Incompatible dimensions' in lab.status.get()
        lab.geometry('540x490')
        apply_text_scale(lab, 1.5)
        for column in lab.pages.values():
            lab.tabs.select(column)
            lab.update()
            column.canvas.yview_moveto(1)
            lab.update()
            assert column.canvas.winfo_height() >= 120
            assert column.body.winfo_width() <= lab.winfo_width()
        if os.getenv('HAYAT_SHOTS') == '1':
            from tests.test_gui_screenshots import _grab
            from types import SimpleNamespace
            from tkinter import ttk
            from app.gui_theme import style_controls, style_hayat_buttons
            style = ttk.Style(root)
            style.theme_use('clam')
            style_controls(root, panel.palette)
            style_hayat_buttons(root, panel.palette)
            apply_text_scale(lab, 1.0)
            root.geometry('1120x780+20+20')
            root.deiconify()
            lab.geometry('820x680')
            lab.tabs.select(lab.pages['Dimensions'])
            lab.formula.set('mass*acceleration')
            lab.unit_inputs.set('mass=kg, acceleration=m/s^2')
            lab.output_unit.set('N')
            lab.audit(); wait(lab)
            lab.pages['Dimensions'].canvas.yview_moveto(0)
            until = time.monotonic()+.25
            while time.monotonic() < until:
                root.update()
                time.sleep(.01)
            lab.update()
            _grab(SimpleNamespace(root=root), '139-invention-experiment-lab', widget=lab)
            _grab(SimpleNamespace(root=root), '140-invention-workspace', widget=root)
    finally:
        apply_text_scale(root, 1.0)
        lab.destroy()


def test_dimension_examples_run_real_checks_without_changing_the_project(studio):
    from app.gui_invention_lab import DIMENSION_EXAMPLES
    panel, root, workbench = studio
    create(panel, root)
    before = workbench.get(panel.project_id)
    panel.open_lab()
    lab = panel._lab
    try:
        for name in DIMENSION_EXAMPLES:
            lab.dimension_example.set(name)
            lab.load_dimension_example()
            assert not lab.unit_result.get('1.0', 'end').strip()
            lab.audit()
            wait(lab)
            assert 'Dimensions match' in lab.unit_result.get('1.0', 'end')
        assert workbench.get(panel.project_id) == before
    finally:
        lab.destroy()


def test_invention_sections_and_bottom_edge_fit_compact_enlarged_text(studio):
    from app.gui_theme import apply_text_scale
    panel, root, _ = studio
    try:
        root.geometry('760x640')
        apply_text_scale(root, 1.5)
        root.update()
        panel._responsive_layout()
        root.update()
        assert panel.workspace.winfo_rooty()+panel.workspace.winfo_height() <= panel.winfo_rooty()+panel.winfo_height()
        assert panel.status_label.winfo_viewable()
        panel.section.set('Evidence')
        panel.section_picker.event_generate('<<ComboboxSelected>>')
        root.update()
        assert panel.tabs.tab(panel.tabs.select(), 'text') == 'Evidence'
    finally:
        apply_text_scale(root, 1)
