"""Reasoning desk: observed checks and an editable, unsent decision brief."""
from __future__ import annotations

import queue
import logging
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from app.gui_output import DisplayVar, SafeTreeview
from app.gui_theme import DarkScrolledText
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def decision_brief(goal: str, constraints: str = "", evidence: str = "", success: str = "") -> str:
    """User notes remain labelled input; preparing a brief does not execute it."""
    if not goal.strip():
        raise ValueError("Describe the decision or problem first.")
    fields = (("Goal", goal), ("Constraints", constraints),
              ("Supplied evidence and unknowns (not independently verified)", evidence),
              ("Success criteria", success))
    if any(len(value) > 6000 for _, value in fields):
        raise ValueError("Keep each field within 6,000 characters.")
    return ("Help me work through this decision.\n\n"
            + "\n\n".join(f"{label}:\n{value.strip() or 'Not specified.'}" for label, value in fields)
            + "\n\nCompare plausible options against my constraints and success criteria. Separate supplied facts, "
              "assumptions and unknowns. Explain the tradeoffs and what evidence would change the recommendation. "
              "If a missing fact blocks a useful answer, ask the most important question. Propose a small, testable "
              "next step. Do not claim a tool ran or an outcome was verified without observed results.")


def reasoning_snapshot(runtime):
    """No generation, learning enqueue, network fetch or sensor activation."""
    from app.answer_provenance import describe
    from app.startup import readiness

    report = readiness(runtime)
    private = bool(getattr(getattr(runtime, 'authority', None), 'privacy_mode', False))
    control = getattr(runtime, 'metacognition', None)
    gaps = [] if private or control is None else control.curriculum(8)
    errors = dict(getattr(control, 'observation_errors', {}) or {}) if not private else {}
    return {'startup': report, 'answer': describe(runtime), 'gaps': gaps, 'private': private,
            'gap_errors': errors, 'gaps_available': control is not None and not private,
            'modes': runtime.modes.snapshot()}


class ReasoningDesk(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p = gui, type(gui)
        self._closed, self._busy, self._timer = False, False, None
        self._results = queue.Queue()
        self.snapshot = None
        self.title('HAYAT · Reasoning desk')
        self.configure(bg=self.p.BLACK)
        self.minsize(520, 430)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<F5>', lambda _event: self.refresh())
        from app.gui_intelligence_parts import FlowRow
        header = FlowRow(self, self.p.BLACK, gap=6)
        header.pack(fill='x', padx=14, pady=(10, 6))
        header.add(tk.Label(header, text='Reasoning desk', bg=self.p.BLACK, fg=self.p.WHITE,
                            font=('Segoe UI Semibold', 18)))
        self.refresh_button = header.add(ttk.Button(header, text='Refresh checks (F5)', command=self.refresh))
        header.add(ttk.Button(header, text='Close', command=self.close))
        self.status = DisplayVar(self, 'Reading local checks…')
        self.status_label = tk.Label(self, textvariable=self.status, bg=self.p.BLACK, fg=self.p.MUTED,
                                    anchor='w', justify='left', wraplength=800, padx=14)
        self.status_label.pack(fill='x', pady=(0, 8))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        self.overview, self.learning_page, self.brief_page = (tk.Frame(self.tabs, bg=self.p.BLACK) for _ in range(3))
        self.tabs.add(self.overview, text='Checks')
        self.tabs.add(self.learning_page, text='Learning')
        self.tabs.add(self.brief_page, text='Decision brief')
        self._build_overview()
        self._build_brief()
        self.bind('<Configure>', self._fit, add='+')
        self.bind('<Destroy>', lambda event: self._dispose() if event.widget is self else None, add='+')
        from app.gui_windows import place_over
        place_over(self, gui.root, (920, 750))
        self.deiconify()
        self.refresh()
        self._poll()

    def _build_overview(self):
        from app.gui_intelligence_parts import FlowRow
        self.mode_row = FlowRow(self.overview, self.p.BLACK, gap=6)
        self.mode_row.pack(fill='x', pady=8)
        self.mode_buttons = {}
        for key, label in (('automatic', 'Automatic'), ('balanced', 'Balanced'), ('deep', 'Deep review')):
            self.mode_buttons[key] = self.mode_row.add(ttk.Button(
                self.mode_row, text=label, command=lambda profile=key: self.set_mode(profile)))
        self.mode_row.add(ttk.Button(self.mode_row, text='Startup details', command=self.gui._show_startup_readiness))
        self.checks = DarkScrolledText(self.overview, height=9, wrap='word', state='disabled',
                                      bg=self.p.PANEL, fg=self.p.WHITE, font=('Segoe UI', 10), padx=12, pady=10)
        self.checks.pack(fill='both', expand=True)
        tk.Label(self.learning_page, text='Evidence-based learning priorities', bg=self.p.BLACK, fg=self.p.GOLD_SOFT,
                 anchor='w', font=('Segoe UI Semibold', 11)).pack(fill='x', pady=(10, 5))
        self.gap_detail = DisplayVar(self, 'Select a priority to see how improvement should be verified.')
        self.gap_label = tk.Label(self.learning_page, textvariable=self.gap_detail, wraplength=800,
                                 anchor='w', justify='left', bg=self.p.BLACK, fg=self.p.MUTED)
        self.gap_label.pack(side='bottom', fill='x', pady=8)
        gap_frame = tk.Frame(self.learning_page, bg=self.p.BLACK)
        gap_frame.pack(fill='both', expand=True)
        self.gaps = SafeTreeview(gap_frame, columns=('topic', 'source'), show='headings', height=3)
        self.gaps.heading('topic', text='What needs attention')
        self.gaps.heading('source', text='Observed gap')
        self.gaps.column('topic', width=470, minwidth=170)
        self.gaps.column('source', width=170, minwidth=110)
        gap_scroll = ttk.Scrollbar(gap_frame, command=self.gaps.yview)
        gap_scroll.pack(side='right', fill='y')
        self.gaps.configure(yscrollcommand=gap_scroll.set)
        self.gaps.pack(fill='both', expand=True)
        self.gaps.bind('<<TreeviewSelect>>', self._select_gap)

    def _build_brief(self):
        from app.gui_panels import ScrollableColumn
        self.prepare_button = ttk.Button(self.brief_page, text='Prepare in chat · review before sending',
                                         style='HayatAccent.TButton', command=self.prepare)
        self.prepare_button.pack(side='bottom', fill='x', pady=8)
        self.editor_scroll = ScrollableColumn(self.brief_page, self.p, width=650)
        self.editor_scroll.pack(fill='both', expand=True)
        self.fields = {}
        for key, label, height in (('goal', 'Decision or problem', 3), ('constraints', 'Constraints · time, budget, privacy', 3),
                                   ('evidence', 'Known facts, sources and unknowns', 4), ('success', 'What a good result must achieve', 3)):
            tk.Label(self.editor_scroll.body, text=label, bg=self.p.BLACK, fg=self.p.GOLD_SOFT,
                     anchor='w', font=('Segoe UI Semibold', 10)).pack(fill='x', pady=(8, 4))
            editor = DarkScrolledText(self.editor_scroll.body, height=height, width=32, wrap='word',
                                      protect_output=False, bg=self.p.PANEL, fg=self.p.WHITE,
                                      insertbackground=self.p.WHITE, font=('Segoe UI', 10), padx=10, pady=8)
            editor.pack(fill='x')
            self.fields[key] = editor
        self.editor_scroll.adopt(self.editor_scroll.body)

    def set_mode(self, profile):
        if self.gui._working_since is not None:
            self.status.set('Finish or stop the current reply before changing reasoning depth.')
            return
        self.gui._set_reasoning_profile(profile)
        self.status.set('Reasoning change requested. Refresh checks after it completes. Deep review can take longer.')

    def refresh(self):
        if self._busy or self._closed:
            return
        self._busy = True
        self.refresh_button.configure(state='disabled')
        self.status.set('Reading local wiring and measured gaps; no model calls are started.')
        replying = self.gui._working_since is not None
        def read():
            try:
                value = reasoning_snapshot(self.gui.runtime)
                if replying:
                    value['answer'] = ['A reply was in progress during this snapshot. Refresh after it finishes to inspect its checks.']
                self._results.put((value, False))
            except Exception:
                log_swallowed(_log, 'reading reasoning desk checks')
                self._results.put((None, True))
        threading.Thread(target=read, daemon=True, name='hayat-reasoning-checks').start()

    def _poll(self):
        if self._closed:
            return
        if self.snapshot and self.gui.runtime.authority.privacy_mode and not self.snapshot['private']:
            self.render(self.snapshot)
        try:
            value, failed = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.refresh_button.configure(state='normal')
            if failed:
                self.status.set('Checks could not be read. Open Diagnostics to inspect runtime health.')
            else:
                self.render(value)
        self._timer = self.after(100, self._poll)

    def render(self, value):
        # Privacy may have changed while the background snapshot was being read.
        if self.gui.runtime.authority.privacy_mode:
            value = {**value, 'gaps': [], 'private': True, 'gaps_available': False, 'gap_errors': {}}
        if self.gui._working_since is not None:
            value = {**value, 'answer': ['A reply is in progress. Its answer checks are not final. Refresh after it finishes.']}
        self.snapshot = value
        report = value['startup']
        configured = value['modes']['configured']
        active = ('deep' if {'DEEP', 'ADVERSARIAL'} <= set(configured) else 'automatic' if configured == ['AUTO']
                  else 'balanced' if 'BALANCED' in configured else None)
        for key, button in self.mode_buttons.items():
            button.configure(style='HayatChosen.TButton' if active == key else 'Hayat.TButton')
        lines = ['REASONING PROFILE · ' + ' + '.join(configured), '', 'LAST ANSWER', *value['answer'], '', 'LOCAL READINESS']
        lines += [f"{item['name']} · {item['state']} — {item['detail']}" for item in report['checks']]
        lines += ['', 'Deep review adds bounded self-questioning and a skeptical pass. It is not a guarantee of accuracy.']
        self.checks.configure(state='normal')
        self.checks.delete('1.0', 'end')
        self.checks.insert('1.0', '\n'.join(lines))
        self.checks.configure(state='disabled')
        self.gaps.delete(*self.gaps.get_children())
        for index, item in enumerate(value['gaps']):
            self.gaps.insert('', 'end', iid=str(index), values=(item['topic'], item['source'].replace('_', ' ')))
        self.gap_detail.set('Learning priorities are hidden in privacy mode.' if value['private'] else
                            'Some gap measurements could not be read; an empty list does not establish health.' if value['gap_errors'] else
                            'Learning-priority measurements are unavailable.' if not value['gaps_available'] else
                            'Select a priority to see its verification requirement.' if value['gaps'] else
                            'No gaps found in the checked records. This is not a general capability test.')
        stamp = datetime.fromisoformat(report['checked_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
        self.status.set('Snapshot · ' + stamp + ' · Refresh after a reply or mode change.')

    def _select_gap(self, _event=None):
        if self.gui.runtime.authority.privacy_mode:
            self.gaps.delete(*self.gaps.get_children())
            self.snapshot = None
            self.gap_detail.set('Learning priorities are hidden in privacy mode.')
            return
        selected = self.gaps.selection()
        if selected and self.snapshot:
            item = self.snapshot['gaps'][int(selected[0])]
            self.gap_detail.set(item['reason'] + '\nVerify by: ' + item['verification'])

    def prepare(self):
        try:
            text = decision_brief(**{key: field.get('1.0', 'end-1c') for key, field in self.fields.items()})
        except ValueError as exc:
            self.status.set(str(exc))
            return
        if self.gui.input.get('1.0', 'end-1c').strip():
            self.status.set('The chat already has an unsent draft. Send or clear it before preparing this brief.')
            return
        if self.gui._working_since is not None:
            self.status.set('Finish or stop the current reply before preparing the decision brief.')
            return
        self.gui.use_example(text)
        self.status.set('Prepared in chat. Review the brief, then press Send when ready.')

    def _fit(self, event):
        if event.widget is self:
            width = max(220, self.winfo_width() - 36)
            for label in (self.status_label, self.gap_label):
                if label.winfo_pixels(label.cget('wraplength')) != width:
                    label.configure(wraplength=width)

    def _dispose(self):
        self._closed = True
        self.snapshot = None
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None

    def close(self):
        self._dispose()
        self.destroy()
