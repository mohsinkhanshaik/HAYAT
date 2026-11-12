"""Animated startup over the reserved STA/Tk root; runtime work stays off Tk."""
from __future__ import annotations

import math
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from contextlib import ExitStack

from app.gui_presentation import diagnostic_text
from app.startup import BOOT_STAGES


class BootScreen:
    def __init__(self, root):
        self.root = root
        self.started = time.monotonic()
        self.stage = 0
        self.closed = False
        self.cancelled = threading.Event()
        self.finished = False
        self.result = None
        self.error = None
        self.updates = queue.Queue()
        self._timer = None
        from app.gui_motion import system_allows_motion
        self.motion = system_allows_motion()
        self.root.title('HAYAT · Starting workspace')
        self.root.configure(bg='#080D16')
        self.root.resizable(True, True)
        self.root.minsize(460, 400)
        from app.gui_resources import manage_tk_collection
        manage_tk_collection(root)
        self.frame = tk.Frame(root, bg='#080D16', padx=24, pady=18)
        self.frame.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Label(self.frame, text='HAYAT', bg='#080D16', fg='#F1F7FF',
                 font=('Segoe UI Semibold', 26), anchor='w').pack(fill='x')
        tk.Label(self.frame, text='Preparing your local workspace', bg='#080D16', fg='#72E1DE',
                 font=('Segoe UI', 12), anchor='w').pack(fill='x')
        self.canvas = tk.Canvas(self.frame, bg='#080D16', height=90, highlightthickness=0)
        self.canvas.pack(fill='x', pady=(6, 4))
        footer = tk.Frame(self.frame, bg='#080D16')
        footer.pack(side='bottom', fill='x', pady=(8, 0))
        self.caption = tk.Label(footer, text='', bg='#080D16', fg='#B1C1D5', anchor='w', justify='left', wraplength=500)
        self.caption.pack(fill='x')
        self.bar = ttk.Progressbar(footer, maximum=len(BOOT_STAGES), mode='determinate')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('Boot.Horizontal.TProgressbar', background='#72E1DE', troughcolor='#1A2A3E',
                        lightcolor='#72E1DE', darkcolor='#72E1DE', bordercolor='#425D77', borderwidth=0)
        self.bar.configure(style='Boot.Horizontal.TProgressbar')
        self.bar.pack(fill='x', pady=8)
        self.status = tk.Label(footer, text='Optional engines load when needed. No sensors are opened by this screen.',
                               bg='#080D16', fg='#B1C1D5', anchor='w', wraplength=500, justify='left')
        self.status.pack(fill='x')
        self.close_button = tk.Button(footer, text='Close startup', command=self.request_close, bg='#1A2A3E',
                                       fg='#F1F7FF', activebackground='#263C53', activeforeground='#FFFFFF',
                                       relief='flat', padx=12, pady=6)
        self.close_button.pack(anchor='e', pady=(8, 0))
        self.steps = tk.Text(self.frame, height=9, width=42, wrap='word', bg='#121E2D', fg='#B1C1D5',
                             relief='flat', state='disabled', padx=12, pady=8, font=('Segoe UI', 10))
        self.steps.pack(fill='both', expand=True)
        self.steps.tag_configure('current', foreground='#72E1DE')
        self.steps.tag_configure('done', foreground='#93E7B5')
        footer.bind('<Configure>', lambda event: (
            self.caption.configure(wraplength=max(100, event.width)),
            self.status.configure(wraplength=max(100, event.width))))
        root.protocol('WM_DELETE_WINDOW', self.request_close)
        root.bind('<Escape>', lambda _event: self.request_close())
        root.bind('<Destroy>', self._destroyed, add='+')
        width = min(640, root.winfo_screenwidth() - 32)
        height = min(560, root.winfo_screenheight() - 80)
        root.geometry(f'{width}x{height}+{max(0, (root.winfo_screenwidth()-width)//2)}+{max(0, (root.winfo_screenheight()-height)//2)}')
        root.deiconify()
        self._paint_steps()
        self._tick()

    def publish(self, stage):
        # The build thread only writes data. Every Tcl call stays on Tk's thread.
        self.updates.put(('stage', stage))

    def start(self, builder):
        def work():
            try:
                runtime = builder(self.publish)
                self.updates.put(('runtime', runtime))
            except Exception as error:
                self.updates.put(('error', diagnostic_text(error, 240)))
        self.worker = threading.Thread(target=work, name='hayat-runtime-startup', daemon=True)
        self.worker.start()

    def _paint_steps(self):
        self.steps.configure(state='normal')
        self.steps.delete('1.0', 'end')
        for index, (_, label) in enumerate(BOOT_STAGES):
            marker = '✓' if index < self.stage else '›' if index == self.stage else '·'
            self.steps.insert('end', marker + '  ' + label + '\n', 'done' if index < self.stage else 'current' if index == self.stage else '')
        self.steps.configure(state='disabled')
        self.steps.see(f'{self.stage+1}.0')
        self.bar['value'] = self.stage

    def _tick(self):
        self._timer = None
        if self.closed:
            return
        while not self.updates.empty():
            kind, value = self.updates.get_nowait()
            if kind == 'stage':
                indices = {key: index for index, (key, _) in enumerate(BOOT_STAGES)}
                if value in indices:
                    self.stage = max(self.stage, indices[value])
                    self._paint_steps()
            elif kind == 'notice':
                self.status.configure(text=diagnostic_text(value, 240))
            elif kind == 'runtime':
                self.result, self.finished = value, True
                self.root.quit()
            elif kind == 'error':
                self.error, self.finished = value, True
                self.status.configure(text='Startup could not finish: ' + value + '\nClose and restart with --safe-mode to skip optional integrations.', fg='#FF9292')
                if self.cancelled.is_set():
                    self.root.quit()
        elapsed = time.monotonic() - self.started
        self.caption.configure(text=f'{self.stage} of {len(BOOT_STAGES)} stages completed · {elapsed:.1f}s elapsed'
                               + (' · Closing when initialization returns' if self.cancelled.is_set() else ''))
        self.canvas.delete('all')
        width = max(1, self.canvas.winfo_width())
        center, cy = width / 2, 45
        for radius in (22, 35):
            self.canvas.create_oval(center-radius, cy-radius, center+radius, cy+radius, outline='#425D77')
        angle = elapsed * 1.5 if self.motion else 0
        x, y = center + 35 * math.cos(angle), cy + 35 * math.sin(angle)
        self.canvas.create_line(center-100, cy, center-45, cy, fill='#425D77')
        self.canvas.create_line(center+45, cy, center+100, cy, fill='#425D77')
        self.canvas.create_oval(x-4, y-4, x+4, y+4, fill='#72E1DE', outline='')
        self.canvas.create_text(center, cy, text='H', fill='#F1F7FF', font=('Segoe UI Semibold', 20))
        self._timer = self.root.after(50, self._tick)

    def request_close(self):
        self.cancelled.set()
        self.close_button.configure(state='disabled')
        if self.finished:
            self.root.quit()

    def detach(self):
        self.closed = True
        if self._timer:
            self.root.after_cancel(self._timer)
            self._timer = None
        self.root.unbind('<Escape>')
        self.frame.destroy()

    def _destroyed(self, event):
        if event.widget is self.root:
            self.closed = True
            if self._timer:
                self.root.after_cancel(self._timer)
                self._timer = None


def run_desktop(*, use_model=True, safe_mode=False):
    from app.gui import HayatGUI, startup_window
    root = startup_window()
    boot = BootScreen(root)
    resources = ExitStack()
    def build(progress):
        from app.windows.com_apartment import configure_gui_sta
        configure_gui_sta()
        from app import portable
        if use_model and (os.environ.get('HAYAT_BOOT_OLLAMA') == '1' or portable.active()):
            from app.launcher import Job, OwnOllama
            boot.updates.put(('notice', 'Starting the configured local model service. The workspace remains responsive.'))
            job = Job()
            resources.callback(job.close)
            server = OwnOllama(job=job)
            resources.callback(server.stop)
            try:
                answered = server.start()
            except OSError:
                answered = False
            if not answered:
                boot.updates.put(('notice', 'The local model service did not answer. Starting available tools; review model readiness.'))
        from app.runtime import build_runtime
        from app.startup import readiness
        runtime = build_runtime(interactive_approvals=False, use_model=use_model,
                                safe_mode=safe_mode, on_progress=progress)
        try:
            runtime.startup_report = readiness(runtime)
        except Exception:
            runtime.close()
            raise
        return runtime
    boot.start(build)
    root.mainloop()
    runtime = boot.result
    if boot.cancelled.is_set() or runtime is None:
        boot.detach()
        if runtime is not None:
            runtime.close()
        resources.close()
        root.destroy()
        return 1 if boot.error else 0
    try:
        boot.publish('workspace')
        window = HayatGUI(runtime)
        boot.stage = len(BOOT_STAGES)
        boot._paint_steps()
        boot.frame.lift()
        boot.status.configure(text='Workspace assembled. Optional model engines load on demand; inspect Startup readiness for details.')
        root.after(120, boot.detach)
        return window.run()
    finally:
        if not boot.closed and root.winfo_exists():
            boot.detach()
        runtime.close()
        resources.close()
