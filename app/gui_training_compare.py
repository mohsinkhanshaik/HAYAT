"""Training -> Compare runs: two recorded evaluation runs side by side.

Files are read on a worker thread; Tk only paints finished results. Closing
the window drops any result still on its way, and every number shown names
the file it came from.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over


class CompareRunsWindow(tk.Toplevel):
    # None means data/models under the project; tests point it elsewhere.
    models_root = None

    def __init__(self, parent, palette):
        super().__init__(parent)
        self.withdraw()
        self.palette = palette
        self.title('HAYAT · Compare training runs')
        self.configure(bg=palette.BLACK)
        self.minsize(620, 420)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: (self.close(), 'break')[1])
        self.runs, self._by_label = [], {}
        self._results = queue.Queue()
        self._closed = False
        self._timer = None
        tk.Label(self, text='Compare two recorded evaluation runs', bg=palette.BLACK, fg=palette.WHITE,
                 anchor='w', font=('Segoe UI Semibold', 13)).pack(fill='x', padx=16, pady=(12, 4))
        tk.Label(self, text='Numbers come from the saved evaluation files; nothing is re-measured. '
                            'Runs on different holdouts are flagged instead of ranked.',
                 bg=palette.BLACK, fg=palette.MUTED, anchor='w', justify='left', wraplength=700).pack(fill='x', padx=16)
        pick = tk.Frame(self, bg=palette.BLACK, padx=16, pady=8)
        pick.pack(fill='x')
        self.first, self.second = StringVar(self), StringVar(self)
        self.choices = []
        for row, (label, variable) in enumerate((('First', self.first), ('Second', self.second))):
            ttk.Label(pick, text=label).grid(row=row, column=0, sticky='w', pady=2)
            box = ttk.Combobox(pick, textvariable=variable, state='readonly', width=60)
            box.grid(row=row, column=1, sticky='ew', padx=8, pady=2)
            box.bind('<<ComboboxSelected>>', lambda _event: self.compare())
            self.choices.append(box)
        pick.columnconfigure(1, weight=1)
        self.status = StringVar(self, 'Reading recorded runs...')
        ttk.Label(self, textvariable=self.status).pack(fill='x', padx=16)
        self.body = DarkScrolledText(self, wrap='word', bg=palette.PANEL, fg=palette.WHITE,
                                     font=('Consolas', 10), height=18)
        self.body.pack(fill='both', expand=True, padx=16, pady=(6, 14))
        place_over(self, parent.winfo_toplevel(), (820, 600))
        self.deiconify()
        self._work(self._discover)
        self._poll()

    def _root_dir(self):
        if self.models_root:
            return self.models_root
        from app.config import PROJECT_ROOT
        return PROJECT_ROOT / 'data' / 'models'

    def _discover(self):
        from app.training.run_compare import discover_runs
        return {'runs': discover_runs(self._root_dir())}

    def _work(self, job):
        def run():
            try:
                payload = job()
            except Exception as exc:
                payload = {'error': f'{type(exc).__name__}: {exc}'}
            if not self._closed:
                self._results.put(payload)
        threading.Thread(target=run, daemon=True, name='hayat-compare-runs').start()

    def compare(self):
        first, second = self._by_label.get(self.first.get()), self._by_label.get(self.second.get())
        if not (first and second):
            return
        self.status.set('Comparing...')

        def job():
            from app.training.run_compare import compare_runs, describe
            return {'text': describe(compare_runs(first, second))}
        self._work(job)

    def _poll(self):
        try:
            payload = self._results.get_nowait()
        except queue.Empty:
            payload = None
        if payload is not None:
            self._paint(payload)
        if not self._closed:
            self._timer = self.after(60, self._poll)

    def _paint(self, payload):
        if 'error' in payload:
            self.status.set('Could not read the runs: ' + payload['error'])
            return
        if 'runs' in payload:
            self.runs = payload['runs']
            self._by_label = {run['label']: run for run in self.runs}
            labels = list(self._by_label)
            for box in self.choices:
                box.configure(values=labels)
            if len(labels) < 2:
                self.status.set(f'{len(labels)} recorded run found. Two are needed; runs appear after a training '
                                'evaluation finishes.')
                self._show('No comparison yet.')
                return
            self.status.set(f'{len(labels)} recorded runs. Choose two.')
            # Newest file first, baseline before candidate: the latest training's own pair.
            self.first.set(labels[0])
            self.second.set(labels[1])
            self.compare()
            return
        self.status.set('Compared.')
        self._show(payload['text'])

    def _show(self, text):
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        self.body.insert('1.0', text)
        self.body.configure(state='disabled')

    def close(self):
        self._closed = True
        if self._timer:
            self.after_cancel(self._timer)
        self.destroy()
