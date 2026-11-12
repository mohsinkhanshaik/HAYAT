"""Engineering and independent-case checks alongside a frozen invention project."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.gui_output import DisplayVar
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText


DIMENSION_EXAMPLES = {
    'Force = mass × acceleration': ('mass * acceleration', 'mass=kg, acceleration=m/s^2', 'N'),
    'Energy = power × time': ('power * time', 'power=W, time=s', 'J'),
    'Speed = distance / time': ('distance / time', 'distance=m, time=s', 'm/s'),
}


def assignments(text):
    result = {}
    for part in text.split(','):
        name, separator, value = part.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or not name or not value or name in result:
            raise ValueError('Use unique assignments such as mass=kg, speed=m/s.')
        result[name] = value
    return result


class InventionLab(tk.Toplevel):
    def __init__(self, panel):
        super().__init__(panel.winfo_toplevel())
        self.withdraw()
        self.panel, self.p = panel, panel.palette
        self._closed, self._busy, self._timer = False, False, None
        self.results = queue.Queue()
        self.buttons = []
        self.title('HAYAT · Experiment Lab')
        self.configure(bg=self.p.BLACK)
        self.minsize(500, 440)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.bind('<Destroy>', self._dispose, add='+')
        tk.Label(self, text='Experiment Lab', bg=self.p.BLACK, fg=self.p.GOLD_SOFT,
                 font=('Segoe UI Semibold', 18), anchor='w').pack(fill='x', padx=14, pady=(12, 4))
        self.status = DisplayVar(self, 'Read-only analyses · no changes to frozen cases or saved rounds.')
        self.status_label = tk.Label(self, textvariable=self.status, bg=self.p.BLACK, fg=self.p.MUTED,
                                    justify='left', anchor='w', wraplength=740)
        self.status_label.pack(fill='x', padx=14, pady=(0, 8))
        self.progress = ttk.Progressbar(self, mode='indeterminate', style='Hayat.Horizontal.TProgressbar')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        self.pages = {}
        from app.gui_panels import ScrollableColumn
        for name in ('Dimensions', 'Conversion', 'Unseen cases'):
            column = ScrollableColumn(self.tabs, self.p, width=740)
            self.tabs.add(column, text=name)
            self.pages[name] = column
        self.formula = StringVar(self, panel.formula.get().strip() or
                                 (((panel.data or {}).get('best') or {}).get('candidate') or {}).get('formula') or '')
        names = sorted({name for case in (panel.data or {}).get('tests', []) for name in case['inputs']})
        self.unit_inputs = StringVar(self, ', '.join(name + '=1' for name in names))
        self.output_unit = StringVar(self, '1')
        page = self.pages['Dimensions'].body
        self.dimension_example = StringVar(self, next(iter(DIMENSION_EXAMPLES)))
        examples = tk.Frame(page, bg=self.p.BLACK)
        examples.pack(fill='x', pady=4)
        ttk.Button(examples, text='Load example', command=self.load_dimension_example).pack(side='right', padx=(6, 0))
        ttk.Combobox(examples, textvariable=self.dimension_example, values=list(DIMENSION_EXAMPLES),
                     state='readonly', width=24).pack(side='left', fill='x', expand=True)
        self._label(page, 'Formula · numbers are dimensionless coefficients')
        ttk.Entry(page, textvariable=self.formula).pack(fill='x', pady=4)
        self._label(page, 'Variable units · for example mass=kg, speed=m/s')
        ttk.Entry(page, textvariable=self.unit_inputs).pack(fill='x', pady=4)
        self._label(page, 'Expected output unit · for example J, W or m/s^2')
        ttk.Entry(page, textvariable=self.output_unit).pack(fill='x', pady=4)
        self._button(page, 'Check dimensions and scale', self.audit)
        self.unit_result = self._report(page)
        page = self.pages['Conversion'].body
        self.amount, self.from_unit, self.to_unit = StringVar(self, '1'), StringVar(self, 'kWh'), StringVar(self, 'J')
        self._label(page, 'Value')
        ttk.Entry(page, textvariable=self.amount).pack(fill='x', pady=4)
        from app.engineering_units import UNITS
        for title, variable in (('From', self.from_unit), ('To', self.to_unit)):
            self._label(page, title + ' · named or compound units; degC/degF for absolute temperature')
            ttk.Combobox(page, textvariable=variable, values=sorted(UNITS) + ['degC', 'degF']).pack(fill='x', pady=4)
        self._button(page, 'Convert units', self.convert)
        self.conversion_result = self._report(page)
        page = self.pages['Unseen cases'].body
        self._label(page, 'Tests a fixed formula without fitting constants or changing the project.')
        ttk.Entry(page, textvariable=self.formula).pack(fill='x', pady=4)
        self._label(page, 'Independent input/output cases · same JSON shape as acceptance cases')
        self._label(page, 'Example: [{"inputs": {"x": 3}, "expected": 6}]. Use new inputs with independently known answers.')
        self.holdouts = DarkScrolledText(page, height=5, width=32, protect_output=False, wrap='word',
                                        bg=self.p.PANEL, fg=self.p.WHITE, insertbackground=self.p.WHITE, font=('Consolas', 10))
        self.holdouts.pack(fill='x', pady=4)
        self.holdouts.insert('1.0', '[]')
        self._button(page, 'Test unseen cases', self.holdout)
        self.holdout_result = self._report(page)
        for column in self.pages.values():
            column.adopt(column.body)
        self.bind('<Configure>', self._fit, add='+')
        from app.gui_windows import place_over
        place_over(self, panel.winfo_toplevel(), (820, 680))
        self.deiconify()
        self._poll()

    def _label(self, parent, text):
        label = tk.Label(parent, text=text, bg=self.p.BLACK, fg=self.p.MUTED, anchor='w',
                         justify='left', wraplength=700, font=('Segoe UI', 10))
        label.pack(fill='x', pady=(8, 2))
        parent.bind('<Configure>', lambda event: label.configure(wraplength=max(180, event.width-16)), add='+')

    def _button(self, parent, title, action):
        button = ttk.Button(parent, text=title, command=action, style='HayatAccent.TButton')
        button.pack(fill='x', pady=8)
        self.buttons.append(button)

    def _report(self, parent):
        box = DarkScrolledText(parent, height=9, width=32, state='disabled', wrap='word',
                               bg=self.p.PANEL, fg=self.p.WHITE, padx=10, pady=8, font=('Segoe UI', 10))
        box.pack(fill='x', pady=4)
        return box

    def audit(self):
        try:
            inputs = assignments(self.unit_inputs.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self._run('invention_unit_audit', {'formula': self.formula.get(), 'inputs': inputs, 'output': self.output_unit.get()}, self.unit_result)

    def load_dimension_example(self):
        formula, inputs, output = DIMENSION_EXAMPLES[self.dimension_example.get()]
        self.formula.set(formula)
        self.unit_inputs.set(inputs)
        self.output_unit.set(output)
        self.unit_result.configure(state='normal')
        self.unit_result.delete('1.0', 'end')
        self.unit_result.configure(state='disabled')
        self.status.set('Example loaded. Check dimensions and scale to calculate its result.')

    def convert(self):
        self._run('invention_convert_units', {'value': self.amount.get(), 'source': self.from_unit.get(),
                                            'target': self.to_unit.get()}, self.conversion_result)

    def holdout(self):
        if not self.panel.project_id:
            self.status.set('Create or open a project in Invention before testing unseen cases.')
            return
        try:
            tests = json.loads(self.holdouts.get('1.0', 'end-1c'))
        except ValueError:
            self.status.set('Supply a JSON list of independent cases with inputs and expected values.')
            return
        self._run('invention_holdout', {'project_id': self.panel.project_id, 'tests': tests,
                                       'formula': self.formula.get()}, self.holdout_result)

    def _run(self, tool, arguments, output):
        if self._closed or self._busy:
            return
        self._busy = True
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('Checking locally… no model, fitting or internet request is started.')
        def worker():
            try:
                from app.execution import execution_scope
                runtime = self.panel.runtime
                ticket = runtime.registry.interlock.ticket()
                with execution_scope(session_id=runtime.session.session_id, task_id=self.panel.project_id):
                    runtime.registry.interlock.check(ticket)
                    result = runtime.registry.execute(tool, arguments)
                    runtime.registry.interlock.check(ticket)
                self.results.put((output, result.output if result.success else None, result.error))
            except Exception as exc:
                self.results.put((output, None, str(exc)))
        threading.Thread(target=worker, daemon=True, name='hayat-experiment-lab').start()

    def _poll(self):
        if self._closed:
            return
        from app.gui_motion import MOTION
        if self._busy and MOTION['enabled']:
            if not self.progress.winfo_manager():
                self.progress.pack(fill='x', padx=14, before=self.tabs)
                self.progress.start(16)
        else:
            self.progress.stop()
            self.progress.pack_forget()
        try:
            output, data, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.progress.stop()
            self.progress.pack_forget()
            for button in self.buttons:
                button.state(['!disabled'])
            if error:
                self.status.set('Check could not complete: ' + str(error))
                output.configure(state='normal')
                output.delete('1.0', 'end')
                output.insert('1.0', 'Check did not complete: ' + str(error))
                output.configure(state='disabled')
            else:
                from app.plain_output import describe_full
                content = data.get('summary') or describe_full(data)
                if data.get('summary'):
                    content += '\n\n' + data.get('scope', '')
                output.configure(state='normal')
                output.delete('1.0', 'end')
                output.insert('1.0', content)
                output.configure(state='disabled')
                self.status.set('Local analysis complete · project and acceptance contract unchanged.')
        self._timer = self.after(80, self._poll)

    def _fit(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(240, self.winfo_width()-30))

    def _dispose(self, event):
        if event.widget is self:
            self._closed = True
            try:
                self.progress.stop()
            except tk.TclError:
                pass
            if self._timer is not None:
                self.after_cancel(self._timer)
                self._timer = None
