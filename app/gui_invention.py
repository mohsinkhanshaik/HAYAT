"""Invention projects in the GUI: the goal and its frozen tests, bounded runs, and a readable journal.

* Overview: the state, the best candidate written out, and a chart of every round (cases passed,
  and how close the formula came).
* Rounds: each round's candidate and its critique; select one to read it.
* Cases: every frozen case with the expected and actual value and how far off it was, for the
  selected round; try your own formula on the same cases (graded, never saved).
* Simulate: the best formula (or yours) swept across each input, the effect of a 10 % change in
  each input, and a Monte Carlo with every input off by up to the chosen spread.
* Research: the sources a run read.
* Raw: the full saved project.
Work it out fits standard shapes to the cases without a model; runs do it first unless switched off.
Export writes a Markdown report to data/invention/exports.
"""
import json
import math
import queue
import re
import threading
import tkinter as tk
from decimal import InvalidOperation
from pathlib import Path
from tkinter import ttk

from app.gui_resources import BooleanVar, StringVar

UP, DOWN = '#4ADE80', '#F05D5E'
STATE_COLOURS = {'TESTED': UP, 'TEST_FAILED': DOWN, 'BLOCKED': DOWN, 'NEEDS_VALIDATION': '#E8C46A', 'RUNNING': '#60A5FA',
                 'STOPPED': '#E8C46A', 'BUDGET_REACHED': '#E8C46A', 'INTERRUPTED': '#E8C46A', 'DRAFT': '#9AA4AE'}
STATE_WORDS = {'TESTED': 'every frozen case passed (those cases only)', 'TEST_FAILED': 'the best candidate still fails some cases',
               'NEEDS_VALIDATION': 'a proposal without numeric cases: it needs real-world tests', 'BLOCKED': 'a required input failed',
               'STOPPED': 'stopped on request; rounds kept', 'BUDGET_REACHED': 'the run reached its budget; rounds kept',
               'INTERRUPTED': 'HAYAT closed during a run; run it again to resume', 'DRAFT': 'not run yet', 'RUNNING': 'running now'}


class InventionPanel(tk.Frame):
    def __init__(self, parent, palette, runtime, compose):
        super().__init__(parent, bg=palette.BLACK)
        self.palette, self.runtime = palette, runtime
        self.compose = compose
        self._results, self._pending = queue.Queue(), False
        self._closed, self._poll_handle = False, None
        self.project_id, self.data, self.trial = None, None, None
        self.status = StringVar(self, 'Create an invention goal or open a saved project.')
        self.use_research = BooleanVar(self, True)
        self.work_out_first = BooleanVar(self, True)
        self.spread = StringVar(self, '10')
        self.cycles = StringVar(self, '3')
        self.tolerance = StringVar(self, '0')
        self.formula = StringVar(self, '')
        self.case_inputs = StringVar(self, '')
        self.case_expected = StringVar(self, '')
        self.simulation_input = StringVar(self, 'All inputs')
        self.simulation_probe = StringVar(self, 'Move over a curve to inspect a computed point.')
        self._source_rows, self._sim_regions = {}, []
        self._layout_handle = None
        p = palette
        tk.Label(self, text='Autonomous Invention Workbench', bg=p.BLACK, fg=p.WHITE,
                 font=('Segoe UI Semibold', 13), anchor='w').pack(fill='x', pady=(8, 4))
        self.subtitle = tk.Label(self, text='Goal → evidence → proposal → critique → measured experiments. Every round is saved.',
                                bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left')
        self.subtitle.pack(fill='x')
        from app.gui_intelligence_parts import FlowRow
        controls = FlowRow(self, p.BLACK, gap=6, height=36)
        controls.pack(fill='x', pady=8)
        self.create_button = ttk.Button(controls, text='Create project', command=self.create_project)
        self.run_button = ttk.Button(controls, text='Run cycles', command=self.run_project)
        self.stop_button = ttk.Button(controls, text='Stop', command=self.stop_project)
        self.refresh_button = ttk.Button(controls, text='Refresh', command=self.refresh)
        self.export_button = ttk.Button(controls, text='Export report', command=self.export)
        self.work_button = ttk.Button(controls, text='Work it out', command=self.work_out)
        controls.add(self.create_button)
        controls.add(self.work_button)
        controls.add(self.run_button)
        controls.add(ttk.Spinbox(controls, from_=1, to=8, width=3, textvariable=self.cycles, state='readonly'))
        for widget in (self.stop_button, self.refresh_button, self.export_button):
            controls.add(widget)
        controls.add(ttk.Checkbutton(controls, text='Research primary sources', variable=self.use_research))
        controls.add(ttk.Checkbutton(controls, text='Work it out first', variable=self.work_out_first))
        body = self.body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=7, bd=0)
        body.pack(fill='both', expand=True)
        from app.gui_panels import ScrollableColumn
        self.editor_column = ScrollableColumn(body, p, width=340)
        left = self.editor_column.body
        right = self.workspace = tk.Frame(body, bg=p.BLACK)
        body.add(self.editor_column, minsize=260, width=340)
        body.add(right, minsize=300)
        from app.invention_templates import TEMPLATES
        self.example = StringVar(self, next(iter(TEMPLATES)))
        examples = tk.Frame(left, bg=p.BLACK)
        examples.pack(fill='x', pady=(4, 0))
        ttk.Combobox(examples, textvariable=self.example, values=tuple(TEMPLATES),
                     state='readonly', width=18).pack(side='left', fill='x', expand=True)
        self.example_button = ttk.Button(examples, text='Load example', command=self.load_example)
        self.example_button.pack(side='left', padx=(4, 0))
        self.goal = self._input(left, 'Goal', height=4)
        self.constraints = self._input(left, 'Constraints (one per line)', height=3)
        self.tests = self._input(left, 'Numeric acceptance cases (optional JSON)', height=4)
        self.tests.insert('1.0', '[]')
        row = tk.Frame(left, bg=p.BLACK)
        row.pack(fill='x', pady=(4, 0))
        tk.Label(row, text='Tolerance', bg=p.BLACK, fg=p.WHITE).pack(side='left')
        ttk.Entry(row, textvariable=self.tolerance, width=8).pack(side='left', padx=6)
        ttk.Button(row, text='Check cases', command=self.check_cases).pack(side='right')
        builder = tk.Frame(left, bg=p.BLACK)
        builder.pack(fill='x', pady=(4, 0))
        tk.Label(builder, text='Add a case', bg=p.BLACK, fg=p.WHITE).pack(side='left')
        inputs = ttk.Entry(builder, textvariable=self.case_inputs, width=12)
        inputs.pack(side='left', padx=(6, 2), fill='x', expand=True)
        tk.Label(builder, text='→', bg=p.BLACK, fg=p.MUTED).pack(side='left')
        expected = ttk.Entry(builder, textvariable=self.case_expected, width=7)
        expected.pack(side='left', padx=2)
        expected.bind('<Return>', lambda _event: self.add_case())
        ttk.Button(builder, text='Add', command=self.add_case).pack(side='left', padx=(2, 0))
        self.case_note = tk.Label(left, text='Add a case as  x=2, y=3 → 13,  or edit the JSON.\n'
                                  'Frozen at creation. Tolerance 0 = exact; 0.01 = within 1 %.', justify='left',
                                  bg=p.BLACK, fg=p.MUTED, anchor='w')
        self.case_note.pack(fill='x', pady=(3, 8))
        tk.Label(left, text='Saved projects', bg=p.BLACK, fg=p.WHITE, anchor='w').pack(fill='x', pady=(8, 2))
        self.projects = self._table(left, columns=('state',), show='tree headings', height=5)
        self.projects.heading('#0', text='Saved goal')
        self.projects.heading('state', text='State')
        self.projects.column('#0', width=190, stretch=True)
        self.projects.column('state', width=110, stretch=False)
        for state, colour in STATE_COLOURS.items():
            self.projects.tag_configure(state, foreground=colour)
        self.projects.bind('<<TreeviewSelect>>', self.open_project)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill='both', expand=True)
        self.tabs.add(self._build_overview(self.tabs), text='Overview')
        self.tabs.add(self._build_rounds(self.tabs), text='Rounds')
        self.tabs.add(self._build_cases(self.tabs), text='Cases')
        self.tabs.add(self._build_simulate(self.tabs), text='Simulate')
        self.tabs.add(self._build_research(self.tabs), text='Research')
        evidence = tk.Frame(self.tabs, bg=p.BLACK)
        ttk.Button(evidence, text='Draft next experiment in chat', command=self.draft_experiment).pack(anchor='w', pady=(6, 4))
        self.evidence_text = self._text(evidence)
        self.evidence_text.pack(fill='both', expand=True)
        self.tabs.add(evidence, text='Evidence')
        from app.gui_theme import DarkScrolledText
        raw = tk.Frame(self.tabs, bg=p.BLACK)
        self.report = DarkScrolledText(raw, bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE, wrap='word', font=('Consolas', 9))
        self.report.pack(fill='both', expand=True)
        self.report.configure(state='disabled')
        self.tabs.add(raw, text='Raw')
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.PANEL_2, fg=p.GOLD_SOFT,
                                    anchor='w', justify='left', wraplength=850, padx=10, pady=8)
        self.status_label.pack(fill='x', pady=(8, 0))
        self.editor_column.adopt(left)
        self.bind('<Configure>', self._schedule_layout, add='+')
        self.bind('<Destroy>', self._dispose, add='+')
        self._poll_handle = self.after(120, self._poll)

    # ------------------------------------------------------------------ layout
    def _schedule_layout(self, event):
        if event.widget is self and self._layout_handle is None:
            self._layout_handle = self.after_idle(self._responsive_layout)

    def _responsive_layout(self):
        self._layout_handle = None
        if self._closed:
            return
        width = self.winfo_width()
        compact = width < 980
        orientation = 'vertical' if compact else 'horizontal'
        if str(self.body.cget('orient')) != orientation:
            self.body.configure(orient=orientation)
            self.body.paneconfigure(self.editor_column, minsize=150 if compact else 260,
                                    height=230 if compact else 0, width=340 if not compact else 0)
            self.body.paneconfigure(self.workspace, minsize=180 if compact else 300)
        for label in (self.subtitle, self.status_label):
            label.configure(wraplength=max(220, width - 24))
        self.case_note.configure(wraplength=max(200, self.editor_column.winfo_width() - 32))

    @staticmethod
    def _table(parent, **options):
        """Both axes remain reachable when columns exceed the viewport."""
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, **options)
        vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        return tree

    def _input(self, parent, label, height):
        tk.Label(parent, text=label, bg=self.palette.BLACK, fg=self.palette.WHITE,
                 anchor='w').pack(fill='x', pady=(6, 2))
        from app.gui_theme import DarkScrolledText
        widget = DarkScrolledText(parent, height=height, wrap='word', bg=self.palette.PANEL,
                                  fg=self.palette.WHITE, insertbackground=self.palette.WHITE, font=('Segoe UI', 10))
        widget.pack(fill='x')
        return widget

    def _text(self, parent, height=8):
        from app.gui_theme import DarkScrolledText
        p = self.palette
        widget = DarkScrolledText(parent, bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE, wrap='word', font=('Segoe UI', 10),
                                  height=height, padx=10, pady=8)
        widget.tag_configure('h', font=('Segoe UI Semibold', 12), foreground=p.WHITE, spacing1=6, spacing3=2)
        widget.tag_configure('label', font=('Segoe UI Semibold', 9), foreground=p.MUTED, spacing1=6)
        widget.tag_configure('mono', font=('Consolas', 10), foreground=p.GOLD_SOFT)
        widget.tag_configure('good', foreground=UP)
        widget.tag_configure('bad', foreground=DOWN)
        widget.tag_configure('muted', foreground=p.MUTED)
        widget.configure(state='disabled')
        return widget

    @staticmethod
    def _write(widget, parts):
        """parts: [(text, tag)]."""
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        for text, tag in parts:
            widget.insert('end', text, tag or ())
        widget.configure(state='disabled')

    def _build_overview(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        from app.gui_intelligence_parts import FlowRow
        top = FlowRow(frame, p.BLACK)
        top.pack(fill='x', pady=(6, 4))
        self.state_badge = tk.Label(top, text='No project open', bg=p.PANEL, fg=p.MUTED, font=('Segoe UI Semibold', 10), padx=10, pady=4)
        top.add(self.state_badge)
        self.state_words = tk.Label(top, text='', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        top.add(self.state_words)
        tk.Label(frame, text='Rounds: cases passed (bars) and how close the formula came (dots: higher is closer)', bg=p.BLACK, fg=p.MUTED,
                 font=('Segoe UI', 8), anchor='w').pack(fill='x')
        self.chart = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=150, cursor='hand2')
        self.chart.pack(fill='x', pady=(2, 6))
        self.chart.bind('<Configure>', lambda _event: self.draw_chart())
        self.chart.bind('<Button-1>', self.chart_click)
        self.overview = self._text(frame)
        self.overview.pack(fill='both', expand=True)
        return frame

    def _build_rounds(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        from app.gui_intelligence_parts import FlowRow
        actions = FlowRow(frame, p.BLACK)
        actions.pack(fill='x', pady=(6, 2))
        self.use_round_button = ttk.Button(actions, text='Use selected formula', command=self.use_round_formula)
        actions.add(self.use_round_button)
        actions.add(ttk.Button(actions, text='Compare with best', command=self.compare_round))
        self.round_list = self._table(frame, columns=('passed', 'error', 'critic'), selectmode='browse', height=7)
        for key, label, width in (('#0', 'Round · candidate', 320), ('passed', 'Passed', 80), ('error', 'Closeness', 110),
                                  ('critic', 'Critic', 160)):
            self.round_list.heading(key, text=label)
            self.round_list.column(key, width=width, stretch=key == '#0')
        self.round_list.tag_configure('best', foreground=UP)
        self.round_list.bind('<<TreeviewSelect>>', lambda _event: self.round_selected())
        self.round_text = self._text(frame)
        self.round_text.pack(fill='both', expand=True)
        return frame

    def _build_cases(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        self.cases_note = tk.Label(frame, text='Open a project to see its frozen cases.', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9), anchor='w')
        self.cases_note.pack(fill='x', pady=(6, 2))
        self.case_table = self._table(frame, columns=('expected', 'actual', 'error', 'result'), selectmode='none', height=8)
        for key, label, width in (('#0', 'Inputs', 240), ('expected', 'Expected', 120), ('actual', 'Actual', 160), ('error', 'Off by', 100),
                                  ('result', 'Result', 120)):
            self.case_table.heading(key, text=label)
            self.case_table.column(key, width=width, stretch=key in ('#0', 'actual'))
        self.case_table.tag_configure('pass', foreground=UP)
        self.case_table.tag_configure('fail', foreground=DOWN)
        from app.gui_intelligence_parts import FlowRow
        row = FlowRow(frame, p.BLACK)
        row.pack(fill='x', pady=(8, 0))
        row.add(tk.Label(row, text='Try your own formula', bg=p.BLACK, fg=p.WHITE))
        self.formula_entry = ttk.Entry(row, textvariable=self.formula, width=30)
        row.add(self.formula_entry)
        self.formula_entry.bind('<Return>', lambda _event: self.try_formula())
        self.try_button = ttk.Button(row, text='Grade it', command=self.try_formula)
        row.add(self.try_button)
        tk.Label(frame, text='Uses the case inputs by name with + − × ÷ ** and brackets, pi and e, and sqrt, exp, log, sin, cos, tan, '
                             'abs, min, max. Unknown constants such as c1 in  c1 * x ** c2  are fitted to the cases. Graded on the '
                             'frozen cases; never saved as a round.', bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w',
                 justify='left', wraplength=760).pack(fill='x', pady=(2, 0))
        return frame

    def _build_simulate(self, parent):
        p = self.palette
        from app.gui_panels import ScrollableColumn
        self.simulation_column = ScrollableColumn(parent, p, width=600)
        frame = self.simulation_column.body
        from app.gui_intelligence_parts import FlowRow
        row = FlowRow(frame, p.BLACK)
        row.pack(fill='x', pady=(6, 4))
        self.simulate_button = ttk.Button(row, text='Simulate best formula', command=self.simulate_best)
        row.add(self.simulate_button)
        self.simulate_mine_button = ttk.Button(row, text='Simulate my formula', command=self.simulate_mine)
        row.add(self.simulate_mine_button)
        row.add(tk.Label(row, text='Input uncertainty ±', bg=p.BLACK, fg=p.WHITE))
        row.add(ttk.Spinbox(row, from_=1, to=50, increment=1, width=4, textvariable=self.spread))
        row.add(tk.Label(row, text='%', bg=p.BLACK, fg=p.WHITE))
        row.add(tk.Label(row, text='Inspect', bg=p.BLACK, fg=p.WHITE))
        self.sweep_picker = ttk.Combobox(row, textvariable=self.simulation_input, state='readonly', width=15,
                                         values=('All inputs',))
        row.add(self.sweep_picker)
        self.sweep_picker.bind('<<ComboboxSelected>>', lambda _event: self.draw_simulation())
        tk.Label(frame, text='Each input swept across the cases (shaded) and a quarter beyond, the others held at their middle value. '
                             'Red marks: no value there. Bars: the effect of a 10 % rise in each input.',
                 bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 8), anchor='w', justify='left', wraplength=760).pack(fill='x')
        self.sim_chart = tk.Canvas(frame, bg=p.PANEL, highlightthickness=0, height=230)
        self.sim_chart.pack(fill='x', pady=(2, 6))
        self.sim_chart.bind('<Configure>', lambda _event: self.draw_simulation())
        self.sim_chart.bind('<Motion>', self.probe_simulation)
        self.sim_chart.bind('<Leave>', lambda _event: self.sim_chart.delete('probe'))
        tk.Label(frame, textvariable=self.simulation_probe, bg=p.BLACK, fg=p.GOLD_SOFT, anchor='w', justify='left',
                 wraplength=640).pack(fill='x')
        self.sim_text = self._text(frame, height=6)
        self.sim_text.pack(fill='both', expand=True)
        self.simulation = None
        self.simulation_column.adopt(frame)
        return self.simulation_column

    def _build_research(self, parent):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        self.source_list = self._table(frame, columns=('url',), height=5, selectmode='browse')
        self.source_list.heading('#0', text='Source read by this project')
        self.source_list.heading('url', text='Publisher URL')
        self.source_list.column('#0', width=260, minwidth=140)
        self.source_list.column('url', width=340, minwidth=180)
        self.source_list.bind('<<TreeviewSelect>>', lambda _event: self.source_selected())
        self.source_list.bind('<Double-1>', lambda _event: self.open_source())
        self.source_list.bind('<Return>', lambda _event: self.open_source())
        self.source_button = ttk.Button(frame, text='Open selected source', command=self.open_source, state='disabled')
        self.source_button.pack(anchor='w', pady=(4, 0))
        self.research_text = self._text(frame)
        self.research_text.pack(fill='both', expand=True, pady=(6, 0))
        return frame

    # ------------------------------------------------------------------ actions
    def _call(self, name, arguments, *, mode='report'):
        if self._pending:
            return
        try:
            ticket = self.runtime.registry.interlock.ticket()
        except PermissionError as exc:
            self.status.set(str(exc))
            return
        self._pending = True
        self.status.set('Working… saved rounds are retained. Stop requests cancellation.')
        for button in self._busy_buttons():
            button.state(['disabled'])

        def worker():
            from app.execution import execution_scope
            try:
                with execution_scope(session_id=self.runtime.session.session_id,
                                     task_id=arguments.get('project_id')):
                    self.runtime.registry.interlock.check(ticket)
                    result = self.runtime.registry.execute(name, arguments)
                    self.runtime.registry.interlock.check(ticket)
                self._results.put((mode, result.output if result.success else None, result.error))
            except Exception as exc:
                self._results.put((mode, None, str(exc)))
        threading.Thread(target=worker, daemon=True, name='hayat-invention').start()

    def _busy_buttons(self):
        return (self.create_button, self.run_button, self.refresh_button, self.try_button, self.work_button,
                self.simulate_button, self.simulate_mine_button, self.example_button)

    def load_example(self):
        """Only edits the draft. Existing saved projects and frozen cases stay intact."""
        if self._pending:
            return False
        from app.invention_templates import TEMPLATES
        template = TEMPLATES.get(self.example.get())
        if template is None:
            self.status.set('Choose an example from the list.')
            return False
        if (self.goal.get('1.0', 'end').strip() or self.constraints.get('1.0', 'end').strip()
                or self.tests.get('1.0', 'end').strip() not in ('', '[]')):
            from tkinter import messagebox
            if not messagebox.askyesno('Replace editor draft?',
                                     'Load an editable example into the editor? Unsaved edits will be replaced. '
                                     'Saved projects stay intact.', parent=self):
                return False
        self.project_id, self.data, self.trial = None, None, None
        self.evidence_audit = None
        for widget, value in ((self.goal, template['goal']),
                              (self.constraints, '\n'.join(template['constraints'])),
                              (self.tests, json.dumps(template['tests'], indent=2))):
            widget.delete('1.0', 'end')
            widget.insert('1.0', value)
        self.tolerance.set(template['tolerance'])
        self.formula.set('')
        self.projects.selection_remove(*self.projects.selection())
        for table in (self.round_list, self.case_table, self.source_list):
            table.delete(*table.get_children())
        self._source_rows.clear()
        self.source_button.state(['disabled'])
        self.state_badge.configure(text='NEW DRAFT', fg=self.palette.MUTED)
        self.state_words.configure(text='Illustrative example; not a measured experiment.')
        for widget in (self.overview, self.round_text, self.research_text, self.evidence_text, self.report):
            self._write(widget, [('Example loaded as a new draft. Create project, then Work it out. '
                                  'Use independent measurements to test a real invention.', 'muted')])
        self.cases_note.configure(text='Create the draft to freeze its acceptance cases.')
        self.show_simulation(None)
        self.draw_chart()
        self.check_cases()
        self.status.set('Editable synthetic example loaded. Create project, then Work it out for a local numeric fit. '
                        'No model or research has run; saved projects are unchanged.')
        return True

    def _cases_from_box(self):
        tests = json.loads(self.tests.get('1.0', 'end').strip() or '[]')
        if not isinstance(tests, list) or len(tests) > 32:
            raise ValueError('Supply a JSON list of at most 32 cases.')
        from app.invention import InventionWorkbench
        for number, test in enumerate(tests, 1):
            if (not isinstance(test, dict) or set(test) != {'inputs', 'expected'}
                    or not isinstance(test['inputs'], dict) or not 1 <= len(test['inputs']) <= 12):
                raise ValueError(f'Case {number} needs "inputs" (names and numbers) and "expected".')
            try:
                for name, value in test['inputs'].items():
                    if not re.fullmatch(r'[A-Za-z][A-Za-z_0-9]{0,31}', name):
                        raise ValueError(f'Invalid input name: {name}.')
                    InventionWorkbench._number(value)
                InventionWorkbench._number(test['expected'])
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f'Case {number}: every value must be a finite, bounded number. {exc}') from exc
        return tests

    def add_case(self):
        """Append one case from 'x=2, y=3' and an expected value to the JSON, without hand-editing it."""
        numeric = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
        pair_pattern = rf'([A-Za-z][A-Za-z_0-9]{{0,31}})\s*=\s*({numeric})'
        pieces = self.case_inputs.get().strip().split(',')
        matches = [re.fullmatch(pair_pattern, item.strip()) for item in pieces]
        expected = self.case_expected.get().strip()
        if not all(matches) or not re.fullmatch(numeric, expected):
            self.case_note.configure(text='Write inputs as  x=2, y=3  and the expected value as a number.', fg=DOWN)
            return False
        pairs = [match.groups() for match in matches]
        if len({name for name, _ in pairs}) != len(pairs):
            self.case_note.configure(text='Each input name must occur once; duplicate names are not accepted.', fg=DOWN)
            return False
        try:
            tests = self._cases_from_box()
            from app.invention import InventionWorkbench
            for _, value in pairs:
                InventionWorkbench._number(value)
            InventionWorkbench._number(expected)
            if len(pairs) > 12 or len(tests) >= 32:
                raise ValueError('A project allows at most 32 cases, with at most 12 inputs in each.')
        except (ValueError, InvalidOperation) as exc:
            self.case_note.configure(text=f'Fix the JSON first: {exc}', fg=DOWN)
            return False
        tests.append({'inputs': {name: value for name, value in pairs}, 'expected': expected})
        self.tests.delete('1.0', 'end')
        self.tests.insert('1.0', json.dumps(tests))
        self.case_inputs.set('')
        self.case_expected.set('')
        return self.check_cases()

    def check_cases(self):
        try:
            tests = self._cases_from_box()
            tolerance = float(self.tolerance.get() or 0)
            if not math.isfinite(tolerance) or not 0 <= tolerance <= .5:
                raise ValueError('Tolerance is from 0 (exact) to 0.5.')
        except ValueError as exc:
            self.case_note.configure(text=f'Not valid yet: {exc}', fg=DOWN)
            return False
        names = sorted({name for test in tests for name in test['inputs']})
        self.case_note.configure(text=f"{len(tests)} cases over {', '.join(names) or 'no inputs'} · "
                                      + ('exact' if not tolerance else f'within {tolerance:g} × the expected value'), fg=UP)
        return True

    def create_project(self):
        if not self.check_cases():
            self.status.set(self.case_note.cget('text'))
            return
        try:
            tests = self._cases_from_box()
            tolerance = float(self.tolerance.get() or 0)
        except ValueError:
            self.status.set('Acceptance cases must be valid JSON, and the tolerance a number.')
            return
        arguments = {'goal': self.goal.get('1.0', 'end').strip(),
                     'constraints': [line for line in self.constraints.get('1.0', 'end').strip().splitlines() if line.strip()],
                     'tests': tests}
        if tolerance:
            arguments['tolerance'] = tolerance
        self._call('invention_create', arguments)

    def run_project(self):
        if not self.project_id:
            self.status.set('Create or select a project first.')
            return
        try:
            cycles = int(self.cycles.get() or 3)
            if not 1 <= cycles <= 8:
                raise ValueError
        except ValueError:
            self.status.set('Choose from 1 to 8 cycles.')
            return
        self._call('invention_run', {'project_id': self.project_id, 'cycles': cycles,
                                    'research': self.use_research.get(), 'work_out': self.work_out_first.get()})

    def work_out(self):
        if not self.project_id:
            self.status.set('Create or select a project first.')
            return
        if not (self.data or {}).get('tests'):
            self.status.set('Working a formula out needs numeric cases. Add cases and create a new project.')
            return
        self._call('invention_work_out', {'project_id': self.project_id})

    def _spread(self):
        try:
            value = float(self.spread.get()) / 100
        except ValueError:
            raise ValueError('Input uncertainty must be a number from 1 to 50 percent.') from None
        if not math.isfinite(value) or not .01 <= value <= .5:
            raise ValueError('Input uncertainty must be a number from 1 to 50 percent.')
        return value

    def simulate_best(self):
        if not self.project_id:
            self.status.set('Open a project first: a simulation runs over its cases.')
            return
        try:
            spread = self._spread()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._call('invention_simulate', {'project_id': self.project_id, 'spread': spread}, mode='simulate')

    def simulate_mine(self):
        if not self.project_id or not self.formula.get().strip():
            self.status.set('Open a project and type a formula under Cases & try a formula first.')
            return
        try:
            spread = self._spread()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._call('invention_simulate', {'project_id': self.project_id, 'formula': self.formula.get().strip(),
                                         'spread': spread}, mode='simulate')

    def stop_project(self):
        if self.project_id and getattr(self.runtime, 'invention', None):
            result = self.runtime.invention.stop(self.project_id)
            self.status.set('Stop requested.' if result['stop_requested'] else 'No active run for this project.')

    def refresh(self):
        if not self._pending:
            self._call('invention_list', {}, mode='list')

    def open_project(self, _event=None):
        selection = self.projects.selection()
        if selection and not self._pending:
            self._call('invention_get', {'project_id': selection[0]})

    def try_formula(self):
        if not self.project_id:
            self.status.set('Open a project first: a formula is graded on its frozen cases.')
            return
        if self.formula.get().strip():
            self._call('invention_try', {'project_id': self.project_id, 'formula': self.formula.get().strip()}, mode='try')

    def export(self):
        workbench = getattr(self.runtime, 'invention', None)
        if not self.project_id or workbench is None:
            self.status.set('Open a project to export its report.')
            return
        try:
            ticket = self.runtime.registry.interlock.ticket()
            self.runtime.registry.interlock.check(ticket)
        except PermissionError as exc:
            self.status.set(str(exc))
            return
        folder = Path(workbench.root) / 'exports'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{self.project_id}.md'
        path.write_text(workbench.report_markdown(self.project_id), encoding='utf-8')
        self.status.set(f'Report saved: {path}')
        return path

    # ------------------------------------------------------------------ showing a project
    def _paint(self, data):
        self.project_id, self.data, self.trial = data['id'], data, None
        for widget, value in ((self.goal, data['goal']),
                              (self.constraints, '\n'.join(data['constraints'])),
                              (self.tests, json.dumps(data['tests'], indent=2))):
            widget.delete('1.0', 'end')
            widget.insert('1.0', value)
        self.tolerance.set(data.get('tolerance', '0'))
        from app.plain_output import describe_full

        text = (f"{data['state']} · {len(data['rounds'])} saved rounds\n\n"
                + data['stop_reason'] + '\n\n' + describe_full(data))
        self.report.configure(state='normal')
        self.report.delete('1.0', 'end')
        self.report.insert('1.0', text)
        self.report.configure(state='disabled')
        colour = STATE_COLOURS.get(data['state'], self.palette.MUTED)
        self.state_badge.configure(text=data['state'].replace('_', ' '), fg=colour)
        self.state_words.configure(text=STATE_WORDS.get(data['state'], '') + (f" · {data['stop_reason']}" if data.get('stop_reason') else ''))
        self.paint_overview()
        self.paint_rounds()
        self.paint_cases((data.get('best') or {}).get('measurement'), 'best candidate' if data.get('best') else None)
        self.paint_research()
        from app.invention_evidence import audit_project
        self.evidence_audit = data.get('evidence_audit') or audit_project(data)
        self.paint_evidence()
        self.show_simulation(data.get('simulation'))
        self.draw_chart()
        self.status.set(f"{data['state']} · Numeric tests cover supplied cases. Novelty and physical validation remain open.")

    def paint_overview(self):
        data = self.data
        best = data.get('best')
        parts = [(data['goal'] + '\n', 'h')]
        if data['constraints']:
            parts += [('Constraints\n', 'label')] + [(f'• {item}\n', None) for item in data['constraints']]
        parts += [('Acceptance contract\n', 'label'),
                  (f"{len(data['tests'])} frozen cases · tolerance {data.get('tolerance', '0')} · hash {data['acceptance_hash'][:12]}…\n", 'muted')]
        if best:
            candidate, measured = best['candidate'], best['measurement']
            parts += [(f"\nBest candidate (round {best['round']})\n", 'label'), (candidate['title'] + '\n', 'h'),
                      (f"{measured['passed']} of {measured['total']} cases passed" if measured['total'] else 'No numeric cases supplied',
                       'good' if measured['total'] and measured['passed'] == measured['total'] else 'bad' if measured['total'] else 'muted')]
            if measured.get('mean_relative_error') is not None:
                parts.append((f" · mean relative error {measured['mean_relative_error']:.6g}", 'muted'))
            parts += [('\n\nHypothesis\n', 'label'), (candidate['hypothesis'] + '\n', None), ('Design\n', 'label'), (candidate['design'] + '\n', None),
                      ('Formula\n', 'label'), ((candidate['formula'] or '(none: not a numeric design)') + '\n', 'mono')]
            for key, label in (('assumptions', 'Assumptions'), ('next_tests', 'Next tests (still to do)')):
                if candidate[key]:
                    parts += [(label + '\n', 'label')] + [(f'• {item}\n', None) for item in candidate[key]]
        else:
            parts.append(('\nNo rounds yet. Press Run cycles.\n', 'muted'))
        parts.append(('\nNumeric tests cover the supplied cases only. Novelty, patentability and physical validity are not verified.\n', 'muted'))
        self._write(self.overview, parts)

    def paint_evidence(self):
        from app.invention_evidence import audit_markdown
        parts = []
        for line in audit_markdown(self.evidence_audit).splitlines():
            tag = 'h' if line.startswith('## ') else 'label' if line.startswith('### ') else None
            text = re.sub(r'^#{2,3} ', '', line)
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            text = re.sub(r'`([^`]+)`', r'\1', text)
            text = re.sub(r'\\([\\`*_{}\[\]<>#|])', r'\1', text)
            parts.append((text + '\n', tag))
        self._write(self.evidence_text, parts)

    def draft_experiment(self):
        if not self.data:
            self.status.set('Open a project to prepare its next experiment.')
            return
        audit = self.evidence_audit
        actions = [row['action'] for row in audit.get('next_experiments', [])]
        draft = (f"Help me plan the next experiment for invention project {self.project_id}: {self.data['goal']}\n\n"
                 + 'Recorded evidence gaps:\n' + '\n'.join(f'- {gap}' for gap in audit.get('gaps', []))
                 + '\n\nNext experiment candidates:\n' + '\n'.join(f'- {action}' for action in actions)
                 + '\n\nChoose a practical next experiment and define the baseline, units, method, uncertainty, '
                   'repeat count and measurable pass/fail criteria. Keep it a plan for my review.')
        self.compose(draft)
        self.status.set('Experiment planning draft added to chat. Review it before sending.')

    def paint_rounds(self):
        tree = self.round_list
        tree.delete(*tree.get_children())
        best = (self.data.get('best') or {}).get('round')
        for record in self.data['rounds']:
            measured, review = record['measurement'], record.get('review') or {}
            error = measured.get('mean_relative_error')
            critic = (review.get('verdict') or review.get('state') or '—') + (' · revised' if review.get('revised') else '')
            tree.insert('', 'end', iid=str(record['round']), text=f"{record['round']} · {record['candidate']['title'][:60]}"
                        + ('  ★ best' if record['round'] == best else ''),
                        values=(f"{measured['passed']}/{measured['total']}", '—' if error is None else f'{error:.4g}', critic),
                        tags=('best',) if record['round'] == best else ())
        self._write(self.round_text, [('Select a round to read its candidate and critique.', 'muted')])

    def round_selected(self):
        selection = self.round_list.selection()
        if not selection or not self.data:
            return
        record = next(item for item in self.data['rounds'] if str(item['round']) == selection[0])
        candidate, measured, review = record['candidate'], record['measurement'], record.get('review') or {}
        parts = [(f"Round {record['round']} · {candidate['title']}\n", 'h'), (f"{str(record.get('at', ''))[:16]} UTC\n", 'muted'),
                 ('Hypothesis\n', 'label'), (candidate['hypothesis'] + '\n', None), ('Design\n', 'label'), (candidate['design'] + '\n', None),
                 ('Formula\n', 'label'), ((candidate['formula'] or '(none)') + '\n', 'mono')]
        if record.get('method') == 'fit':
            parts.append(('Worked out by fitting standard shapes to the cases; no model was used.\n', 'muted'))
        if record.get('fitted'):
            constants = ', '.join(f'{name} = {value:.6g}' for name, value in record['fitted']['constants'].items())
            parts.append((f"The model gave the shape {record['fitted']['shape']}; HAYAT fitted {constants}.\n", 'muted'))
        loo = record.get('leave_one_out')
        if loo:
            parts.append((f"Predicting each case from the others: mean error {loo['mean_relative_error']:.3g} "
                          f"({loo['predicted_within_tolerance']} of {loo['cases']} within tolerance)\n", 'muted'))
        parts += [('Measured\n', 'label'), (f"{measured['passed']} of {measured['total']} cases" + (
                     f" · mean relative error {measured['mean_relative_error']:.6g}" if measured.get('mean_relative_error') is not None else '') + '\n',
                     'good' if measured['total'] and measured['passed'] == measured['total'] else None)]
        if record.get('method') != 'fit':  # a fitted round had no model, so no critique
            parts += [('Critique (a model opinion, not verification)\n', 'label'),
                      (f"{review.get('verdict') or review.get('state', 'unavailable')}" + (' · revised' if review.get('revised') else '')
                       + (f" · critic {review.get('critic')}" if review.get('critic') else '') + '\n', 'muted')]
            parts += [(f'• {issue}\n', None) for issue in review.get('issues') or []]
            if review.get('error'):
                parts.append((review['error'] + '\n', 'bad'))
        if record.get('alternatives'):
            parts.append(('Other shapes that came close\n', 'label'))
            parts += [(f"• {row['description']}: {row['formula']} · {row['passed']} passed\n", None) for row in record['alternatives']]
        self._write(self.round_text, parts)
        self.paint_cases(measured, f"round {record['round']}")

    def _selected_round(self):
        selection = self.round_list.selection()
        return next((row for row in (self.data or {}).get('rounds', [])
                     if selection and str(row['round']) == selection[0]), None)

    def use_round_formula(self):
        record = self._selected_round()
        if not record or not record['candidate'].get('formula'):
            self.status.set('Select a round with a numeric formula first.')
            return
        self.formula.set(record['candidate']['formula'])
        self.tabs.select(2)
        self.formula_entry.focus_set()
        self.status.set(f"Formula from round {record['round']} copied. Edit it, grade it or simulate it; saved rounds stay intact.")

    def compare_round(self):
        record = self._selected_round()
        best = (self.data or {}).get('best')
        if not record or not best:
            self.status.set('Select a measured round to compare with the best candidate.')
            return
        parts = [('Selected round and best candidate\n', 'h')]
        for label, row in ((f"Selected round {record['round']}", record), (f"Best round {best['round']}", best)):
            measured = row['measurement']
            error = measured.get('mean_relative_error')
            parts += [(label + '\n', 'label'), (row['candidate']['title'] + '\n', None),
                      ((row['candidate'].get('formula') or '(no numeric formula)') + '\n', 'mono'),
                      (f"{measured['passed']}/{measured['total']} frozen cases passed; "
                       + ('no numeric error measured' if error is None else f'mean relative error {error:.6g}') + '\n', None)]
        parts.append(('\nThese are the same frozen cases. This comparison does not establish generalization or physical validity.\n', 'muted'))
        self._write(self.round_text, parts)
        self.status.set('Comparing the selected round with the best measured candidate.')

    def paint_cases(self, measured, source):
        table = self.case_table
        table.delete(*table.get_children())
        tests = (self.data or {}).get('tests') or []
        if not tests:
            self.cases_note.configure(text='This project has no numeric cases: its proposals need real-world tests.', fg=self.palette.MUTED)
            return
        observed = (measured or {}).get('cases') or [{'inputs': test['inputs'], 'expected': str(test['expected'])} for test in tests]
        for index, case in enumerate(observed):
            inputs = ', '.join(f'{key}={value}' for key, value in case['inputs'].items())
            if 'passed' not in case:
                values, tag = (case['expected'], '', '', 'not run'), ()
            elif case.get('error'):
                values, tag = (case['expected'], case['error'][:60], '', '✗ did not run'), ('fail',)
            else:
                error = case.get('relative_error')
                values = (case['expected'], case['actual'], '' if error is None else f'{error:.3g}', '✓ passed' if case['passed'] else '✗ failed')
                tag = ('pass',) if case['passed'] else ('fail',)
            table.insert('', 'end', iid=str(index), text=inputs, values=values, tags=tag)
        if measured:
            self.cases_note.configure(text=f"{source}: {measured['passed']} of {measured['total']} passed"
                                           + (f" · tolerance {measured.get('tolerance')}" if measured.get('tolerance') not in (None, '0') else ' · exact'),
                                      fg=UP if measured['passed'] == measured['total'] else DOWN)
        else:
            self.cases_note.configure(text=f'{len(tests)} frozen cases · not run yet', fg=self.palette.MUTED)

    def paint_research(self):
        research = self.data.get('research')
        self.source_list.delete(*self.source_list.get_children())
        self._source_rows.clear()
        self.source_button.state(['disabled'])
        if not research:
            self._write(self.research_text, [('No research for this project yet (or it was switched off).', 'muted')])
            return
        parts = [('Research\n', 'h')]
        if research.get('error'):
            parts.append((f"Research did not complete: {research['error']}\nThis stays an evidence gap.\n", 'bad'))
        for index, source in enumerate(research.get('sources') or []):
            if isinstance(source, dict):
                identifier = str(index)
                self._source_rows[identifier] = source
                self.source_list.insert('', 'end', iid=identifier,
                                        text=source.get('title') or source.get('url') or 'Untitled source',
                                        values=(source.get('url') or '(URL unavailable)',))
                parts += [((source.get('title') or source.get('url') or 'source') + '\n', 'label'), ((source.get('url') or '') + '\n', 'mono')]
                if source.get('summary') or source.get('snippet'):
                    parts.append(((source.get('summary') or source.get('snippet'))[:400] + '\n', None))
        if research.get('answer') or research.get('summary'):
            parts += [('\nSummary\n', 'label'), (str(research.get('answer') or research.get('summary'))[:3000] + '\n', None)]
        self._write(self.research_text, parts)

    def source_selected(self):
        selected = self.source_list.selection()
        source = self._source_rows.get(selected[0]) if selected else None
        self.source_button.state(['!disabled'] if source and source.get('url') else ['disabled'])
        if source:
            self._write(self.research_text, [((source.get('title') or 'Source') + '\n', 'h'),
                        ((source.get('url') or 'No publisher URL supplied') + '\n', 'mono'),
                        ('Retrieved\n', 'label'), (str(source.get('retrieved_at') or source.get('fetched_at')
                                                     or source.get('at') or 'Timestamp not supplied by the research provider') + '\n', 'muted'),
                        ('Evidence excerpt\n', 'label'),
                        (str(source.get('summary') or source.get('snippet') or 'No excerpt saved.') + '\n', None),
                        ('\nA saved source link is evidence provenance; its contents still require review.\n', 'muted')])

    def _internet_allowed(self):
        from app.privacy_masks import ROUTE
        modes = set(getattr(self.runtime.session, 'effective_modes', ()))
        private = getattr(getattr(self.runtime, 'authority', None), 'privacy_mode', False)
        if private or modes & {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
            self.status.set('Privacy or local-only mode is on. Turn it off before opening an internet source.')
            return False
        if ROUTE.proxy is not None:
            self.status.set('Tor routing is active. Open this source with HAYAT\'s masked browser; the ordinary browser is blocked.')
            return False
        return True

    def open_source(self):
        if self._pending or not self._internet_allowed():
            return
        selected = self.source_list.selection()
        source = self._source_rows.get(selected[0]) if selected else None
        if not source or not source.get('url'):
            self.status.set('Select a source with a publisher URL first.')
            return
        try:
            ticket = self.runtime.registry.interlock.ticket()
        except PermissionError as exc:
            self.status.set(str(exc))
            return
        url = source['url']
        self._pending = True
        self.status.set('Checking the selected public source address…')
        for button in self._busy_buttons():
            button.state(['disabled'])

        def worker():
            try:
                from app.research.safety import validate_public_url
                from app.privacy_masks import ROUTE
                self.runtime.registry.interlock.check(ticket)
                if ROUTE.proxy is not None:
                    raise PermissionError('Tor routing is active; ordinary browser handoff is blocked.')
                validated = validate_public_url(url)
                self.runtime.registry.interlock.check(ticket)
                self._results.put(('open-source', (ticket, validated), None))
            except Exception as exc:
                self._results.put(('open-source', None, str(exc)))
        threading.Thread(target=worker, daemon=True, name='hayat-invention-source').start()

    def draw_chart(self):
        canvas, p = self.chart, self.palette
        canvas.delete('all')
        width, height = max(200, canvas.winfo_width()), max(100, canvas.winfo_height())
        rounds = (self.data or {}).get('rounds') or []
        self._chart_rounds = []
        if not rounds:
            canvas.create_text(width / 2, height / 2, text='Rounds appear here as they run.', fill=p.MUTED, font=('Segoe UI', 9))
            return
        total = max(1, max(record['measurement']['total'] for record in rounds))
        errors = [record['measurement'].get('mean_relative_error') for record in rounds]
        largest_error = max((error for error in errors if error is not None), default=0) or 1
        step = (width - 40) / len(rounds)
        best = (self.data.get('best') or {}).get('round')
        bottom = height - 22
        canvas.create_line(20, bottom, width - 20, bottom, fill=p.LINE)
        points = []
        for index, record in enumerate(rounds):
            measured = record['measurement']
            x = 20 + step * (index + .5)
            bar = (bottom - 16) * measured['passed'] / total
            colour = UP if measured['total'] and measured['passed'] == measured['total'] else '#60A5FA'
            canvas.create_rectangle(x - min(28, step * .3), bottom - bar, x + min(28, step * .3), bottom, fill=colour,
                                    outline=p.GOLD if record['round'] == best else '', width=2)
            canvas.create_text(x, bottom + 10, text=f"R{record['round']} · {measured['passed']}/{measured['total']}", fill=p.MUTED,
                               font=('Segoe UI', 8))
            error = errors[index]
            if error is not None:
                y = 14 + (bottom - 30) * min(1.0, error / largest_error)
                points.append((x, y))
                canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill=p.GOLD, outline='')
            self._chart_rounds.append((x - step / 2, x + step / 2, record['round']))
        if len(points) > 1:
            canvas.create_line(*[value for point in points for value in point], fill=p.GOLD, dash=(3, 3))

    def chart_click(self, event):
        for left, right, number in getattr(self, '_chart_rounds', []):
            if left <= event.x <= right:
                self.tabs.select(1)
                self.round_list.selection_set(str(number))
                self.round_list.see(str(number))
                self.round_selected()
                return

    def show_simulation(self, result):
        self.simulation = result if isinstance(result, dict) else None
        names = [row['input'] for row in (self.simulation or {}).get('sweeps') or []]
        self.sweep_picker.configure(values=('All inputs', *names))
        if self.simulation_input.get() not in names:
            self.simulation_input.set(names[0] if names else 'All inputs')
        if not self.simulation:
            self._write(self.sim_text, [('No simulation yet. Work it out or run the project, then press Simulate best formula.', 'muted')])
        elif self.simulation.get('error'):
            self._write(self.sim_text, [(f"The simulation could not run: {self.simulation['error']}", 'bad')])
        else:
            parts = [(f"Simulating {self.simulation['formula']}\n", 'mono')]
            parts += [(line + '\n', 'bad' if 'inside the range' in line else None) for line in self.simulation.get('summary') or []]
            sensitivity = self.simulation.get('sensitivity') or []
            if sensitivity:
                parts.append(('\nSensitivity to a 10 % increase\n', 'label'))
                for row in sensitivity:
                    effect = row.get('effect_of_10_percent')
                    parts.append((f"{row['input']}: " + (f'{effect * 100:+.3g} %' if effect is not None else 'not defined') + '\n', None))
            parts.append(('\n' + self.simulation.get('basis', '') + '\n', 'muted'))
            self._write(self.sim_text, parts)
        self.draw_simulation()

    def draw_simulation(self):
        canvas, p = self.sim_chart, self.palette
        canvas.delete('all')
        self._sim_regions = []
        width, height = max(300, canvas.winfo_width()), max(160, canvas.winfo_height())
        result = self.simulation
        if not result or result.get('error') or not result.get('sweeps'):
            canvas.create_text(width / 2, height / 2, text='Sweeps and sensitivity appear here after a simulation.',
                               fill=p.MUTED, font=('Segoe UI', 9))
            return
        selected = self.simulation_input.get()
        available = result['sweeps']
        bars = [row for row in result.get('sensitivity') or [] if row.get('effect_of_10_percent') is not None]
        bar_width = 230 if bars and width >= 640 else 0
        capacity = max(1, min(4, int((width - bar_width) / 180)))
        sweeps = available[:capacity] if selected == 'All inputs' else [row for row in available if row['input'] == selected]
        if not sweeps:
            sweeps = available[:1]
        self.simulation_probe.set((f'Showing {len(sweeps)} of {len(available)} inputs. Choose an input to inspect its full curve. '
                                   if len(sweeps) < len(available) and selected == 'All inputs' else '')
                                  + 'Move over a curve to inspect a computed point.')
        panel = (width - bar_width - 12) / len(sweeps)
        for index, sweep in enumerate(sweeps):
            left, right = 10 + index * panel + 6, 10 + (index + 1) * panel - 6
            top, bottom = 22, height - 26
            points = sweep['points']
            xs = [x for x, _ in points]
            ys = [y for _, y in points if y is not None]
            canvas.create_text(left, 10, text=f"{sweep['input']} · {sweep['trend']}", anchor='w', fill=p.WHITE,
                               font=('Segoe UI Semibold', 9))
            canvas.create_rectangle(left, top, right, bottom, outline=p.LINE)
            if not ys:
                continue
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            if y0 == y1:
                y0, y1 = y0 - 1, y1 + 1

            def at(x, y):
                return (left + (x - x0) / ((x1 - x0) or 1) * (right - left),
                        bottom - (y - y0) / (y1 - y0) * (bottom - top))
            self._sim_regions.append({'input': sweep['input'], 'bounds': (left, top, right, bottom),
                                      'points': [(at(x, y if y is not None else y0)[0],
                                                  at(x, y if y is not None else y0)[1], x, y) for x, y in points],
                                      'range': sweep['range']})
            low, high = sweep['range']
            canvas.create_rectangle(at(low, y1)[0], top, at(high, y1)[0], bottom, fill=p.PANEL_2, outline='')
            line = []
            for x, y in points:
                if y is None:
                    canvas.create_line(at(x, y0)[0], bottom - 6, at(x, y0)[0], bottom, fill=DOWN, width=2)
                    if len(line) > 3:
                        canvas.create_line(*line, fill=p.GOLD, width=2)
                    line = []
                else:
                    line += list(at(x, y))
            if len(line) > 3:
                canvas.create_line(*line, fill=p.GOLD, width=2)
            canvas.create_text(left, bottom + 10, text=f'{x0:.3g}', anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
            canvas.create_text(right, bottom + 10, text=f'{x1:.3g}', anchor='e', fill=p.MUTED, font=('Segoe UI', 8))
            canvas.create_text(left + 3, top + 8, text=f'{y1:.4g}', anchor='w', fill=p.MUTED, font=('Segoe UI', 7))
            canvas.create_text(left + 3, bottom - 8, text=f'{y0:.4g}', anchor='w', fill=p.MUTED, font=('Segoe UI', 7))
        if bar_width:
            left = width - bar_width
            canvas.create_text(left, 10, text='Effect of +10 %', anchor='w', fill=p.WHITE, font=('Segoe UI Semibold', 9))
            largest = max(abs(row['effect_of_10_percent']) for row in bars) or 1
            # Name | bars either side of zero | value: the value has its own column so a long bar never covers it.
            middle = left + 48 + (bar_width - 48 - 52) / 2
            for index, row in enumerate(bars[:8]):
                y = 32 + index * 22
                effect = row['effect_of_10_percent']
                length = ((bar_width - 48 - 52) / 2 - 2) * abs(effect) / largest
                canvas.create_rectangle(middle, y, middle + (length if effect >= 0 else -length), y + 12,
                                        fill=UP if effect >= 0 else DOWN, outline='')
                canvas.create_text(left, y + 6, text=row['input'][:10], anchor='w', fill=p.MUTED, font=('Segoe UI', 8))
                canvas.create_text(width - 4, y + 6, text=f'{effect * 100:+.1f}%', anchor='e', fill=p.WHITE, font=('Segoe UI', 8))
            canvas.create_line(middle, 26, middle, 32 + len(bars[:8]) * 22, fill=p.LINE)

    def probe_simulation(self, event):
        self.sim_chart.delete('probe')
        for region in self._sim_regions:
            left, top, right, bottom = region['bounds']
            if left <= event.x <= right and top <= event.y <= bottom:
                px, py, x, value = min(region['points'], key=lambda row: abs(row[0] - event.x))
                low, high = region['range']
                location = 'inside supplied input range' if low <= x <= high else 'outside supplied input range'
                self.simulation_probe.set(f"{region['input']} = {x:.6g} → "
                                          + (f'{value:.6g}' if value is not None else 'undefined') + f' ({location}; computed sample)')
                self.sim_chart.create_line(px, top, px, bottom, fill=self.palette.MUTED, dash=(2, 3), tags='probe')
                if value is not None:
                    self.sim_chart.create_oval(px - 4, py - 4, px + 4, py + 4, outline=self.palette.WHITE,
                                              fill=self.palette.GOLD, tags='probe')
                return

    def show_trial(self, result):
        self.trial = result
        self.tabs.select(2)
        self.paint_cases(result['measurement'], f"your formula {result['formula']} (not saved)")

    # ------------------------------------------------------------------ polling
    def _poll(self):
        if self._closed:
            return
        self.drain()
        self._poll_handle = self.after(120, self._poll)

    def drain(self):
        if self._closed:
            return
        try:
            while True:
                mode, data, error = self._results.get_nowait()
                self._pending = False
                for button in self._busy_buttons():
                    button.state(['!disabled'])
                if error:
                    self.status.set('Could not complete operation: ' + error)
                elif mode == 'open-source':
                    ticket, url = data
                    try:
                        self.runtime.registry.interlock.check(ticket)
                        if self._internet_allowed():
                            import webbrowser
                            opened = webbrowser.open(url)
                            self.status.set('Opened selected publisher source.' if opened else 'The browser did not open the source.')
                    except PermissionError as exc:
                        self.status.set(str(exc))
                elif mode == 'list':
                    children = self.projects.get_children()
                    if children:
                        self.projects.delete(*children)
                    for project in data:
                        self.projects.insert('', 'end', iid=project['id'], text=project['goal'][:55], values=(project['state'],),
                                             tags=(project['state'],))
                    self.status.set(f'{len(data)} saved projects. Select one to inspect all rounds.')
                elif mode == 'try':
                    self.show_trial(data)
                    measured = data['measurement']
                    fitted = ''
                    if data.get('fitted_constants'):
                        fitted = 'Fitted ' + ', '.join(f'{k} = {v:.6g}' for k, v in data['fitted_constants'].items()) + ': '
                    self.status.set(f"{fitted}{data['formula']} passed {measured['passed']} of {measured['total']} frozen cases "
                                    '(graded, not saved).')
                elif mode == 'simulate':
                    self.tabs.select(3)
                    self.show_simulation(data)
                    self.status.set('Simulated ' + data['formula'] + '. This shows the formula, not the device.')
                else:
                    self._paint(data)
                    if not self.projects.exists(data['id']):
                        self.projects.insert('', 'end', iid=data['id'], text=data['goal'][:55], values=(data['state'],), tags=(data['state'],))
                    else:
                        self.projects.item(data['id'], values=(data['state'],), tags=(data['state'],))
        except queue.Empty:
            pass

    def _dispose(self, event):
        if event.widget is self:
            self._closed = True
            if self._poll_handle:
                self.after_cancel(self._poll_handle)
            if self._layout_handle:
                self.after_cancel(self._layout_handle)
            if self._pending and self.project_id and getattr(self.runtime, 'invention', None):
                self.runtime.invention.stop(self.project_id)
