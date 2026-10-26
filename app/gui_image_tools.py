"""Manual, offline image metadata and duplicate comparison tools."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.gui_intelligence_parts import FlowRow
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY


class LocalImageToolsWindow(tk.Toplevel):
    def __init__(self, gui, path='', other=''):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        p = self.palette
        self._closed, self.busy, self.generation = False, False, 0
        self._cancel = threading.Event()
        self._results = queue.Queue()
        self._worker, self._poll_job = None, None
        self._interlock, self._ticket = None, None
        self.last_result = None
        self.title('HAYAT · Local image tools')
        self.configure(bg=p.BLACK)
        self.minsize(590, 440)
        self.transient(gui.root)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _e: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self.path, self.other = StringVar(self, path), StringVar(self, other)
        self.status = StringVar(self, 'Choose local images, then select an action. No analysis starts automatically.')

        head = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        head.pack(fill='x')
        tk.Label(head, text='Local image tools', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side='left')
        ttk.Button(head, text='Close', command=self.close, style='Hayat.TButton').pack(side='right')
        from app.gui_research_links import research_links
        self.workspace_links = research_links(head, gui, 'images', image=self.path.get)
        self.intro = tk.Label(self, text='Read technical metadata and quality heuristics, or compare local files for duplicates. '
                              'Images stay on this computer. No face identification, web search or upload.',
                              bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=760)
        self.intro.pack(fill='x', padx=14, pady=(0, 8))
        self.fields = tk.Frame(self, bg=p.BLACK)
        self.fields.pack(fill='x', padx=14)
        self.fields.columnconfigure(1, weight=1)
        self.entries, self.choosers = {}, {}
        for index, (key, title, variable) in enumerate((('path', 'First image', self.path), ('other', 'Second image', self.other))):
            tk.Label(self.fields, text=title, bg=p.BLACK, fg=p.MUTED).grid(row=index, column=0, sticky='w', padx=(0, 8))
            entry = ttk.Entry(self.fields, textvariable=variable)
            entry.grid(row=index, column=1, sticky='ew', pady=4)
            button = ttk.Button(self.fields, text='Choose image…', command=lambda selected=key: self.choose_image(selected))
            button.grid(row=index, column=2, padx=(7, 0), pady=4)
            self.entries[key], self.choosers[key] = entry, button
        self.actions = FlowRow(self, p.BLACK, gap=5, height=36)
        self.actions.pack(fill='x', padx=14, pady=6)
        self.buttons = {}
        for key, label, command in (('inspect', 'Inspect image', self.inspect), ('compare', 'Compare images', self.compare),
                                    ('status', 'Check toolkit', self.check_toolkit), ('cancel', 'Cancel', self.cancel)):
            button = ttk.Button(self.actions, text=label, command=command,
                                style='HayatAccent.TButton' if key == 'inspect' else 'Hayat.TButton')
            self.actions.add(button)
            self.buttons[key] = button
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                     anchor='w', justify='left', wraplength=760)
        self.status_label.pack(fill='x', padx=14, pady=(0, 6))
        self.progress = ttk.Progressbar(self, mode='indeterminate', style='Hayat.Horizontal.TProgressbar')
        self.report = DarkScrolledText(self, bg=p.PANEL, fg=p.WHITE, wrap='word', state='disabled',
                                       height=12, font=('Segoe UI', 10), padx=10, pady=10)
        self.report.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        self._set_report('LOCAL IMAGE WORKSPACE\n\n'
                         'Inspect image: file dimensions, hashes, allowlisted technical metadata and bounded quality measurements.\n\n'
                         'Compare images: exact bytes, exact decoded pixels and perceptual hash Hamming distances. '
                         'A perceptual distance describes image appearance; it is not a person match, identity percentage or authenticity verdict.\n\n'
                         'Check toolkit: installed package versions, availability and project-folder locations. '
                         'This window does not install packages or download models.\n\n'
                         'The second image is optional for inspection. No camera or gallery enrollment is started.')
        self.path.trace_add('write', lambda *_a: self._buttons())
        self.other.trace_add('write', lambda *_a: self._buttons())
        self.bind('<Configure>', self._fit, add='+')
        self._buttons()
        place_over(self, gui.root, (860, 680))
        self.deiconify()
        self.lift()
        self._poll_job = self.after(80, self._poll)

    def _fit(self, event):
        if event.widget is self:
            for label in (self.intro, self.status_label):
                label.configure(wraplength=max(250, event.width - 35))

    def _buttons(self):
        if self._closed:
            return
        running = self._worker is not None and self._worker.is_alive()
        available = not self.busy and not running
        for key, button in self.buttons.items():
            enabled = available
            if key == 'cancel':
                enabled = self.busy
            elif key == 'inspect':
                enabled = available and bool(self.path.get().strip())
            elif key == 'compare':
                enabled = available and bool(self.path.get().strip()) and bool(self.other.get().strip())
            button.configure(state='normal' if enabled else 'disabled')
        for button in self.choosers.values():
            button.configure(state='normal' if available else 'disabled')

    def choose_image(self, selected='path'):
        if self.busy or (self._worker is not None and self._worker.is_alive()):
            return
        if selected not in {'path', 'other'}:
            return
        chosen = filedialog.askopenfilename(parent=self, title='Choose ' + ('first' if selected == 'path' else 'second') + ' local image',
                                            filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff')])
        if chosen:
            (self.path if selected == 'path' else self.other).set(chosen)
            self.status.set('Local image selected. Press Inspect image or Compare images to run the tool.')

    def _local_path(self, variable, label):
        value = variable.get().strip()
        if not value:
            raise ValueError('Choose the ' + label + ' local image first.')
        if value.startswith(('\\\\', '//')) or '://' in value:
            raise ValueError('Choose images stored on this computer; URLs and network shares are not accepted.')
        return str(Path(value).expanduser())

    @staticmethod
    def _accept_report(operation, value):
        if not isinstance(value, dict):
            raise ValueError('The local image tool returned no structured report.')
        if operation in {'inspect', 'compare'}:
            if (value.get('operation') != operation or value.get('local_only') is not True
                    or value.get('identity_inference') is not False):
                raise ValueError('The tool did not return the required local image-only report. Its output was not displayed.')
        elif type(value.get('available')) is not bool or not isinstance(value.get('packages'), list):
            raise ValueError('The toolkit did not return package availability details.')
        return value

    def _run(self, operation, tool, arguments, message):
        if self._closed or self.busy:
            return False
        if self._worker is not None and self._worker.is_alive():
            self.status.set('The cancelled local worker is still finishing. Wait before starting another operation.')
            return False
        try:
            interlock = self.gui.runtime.registry.interlock
            ticket = interlock.ticket()
        except Exception as error:
            self.status.set(str(error)[:400])
            return False
        self.generation += 1
        generation, cancel = self.generation, threading.Event()
        self._cancel, self.busy, self._interlock, self._ticket = cancel, True, interlock, ticket
        self.status.set(message)
        self.progress.pack(fill='x', padx=14, pady=(0, 5), before=self.report.frame)
        self.progress.start(15)
        self._buttons()
        registry, results = self.gui.runtime.registry, self._results
        def check():
            if cancel.is_set():
                raise InterruptedError('Local image operation cancelled.')
            interlock.check(ticket)
        def work():
            try:
                from app.tools.image_tools import image_tool_controls
                check()
                with image_tool_controls(check=check):
                    result = registry.execute(tool, arguments)
                check()
                if not result.success:
                    raise RuntimeError(result.error or 'The local image tool failed.')
                value = self._accept_report(operation, result.output)
                results.put((generation, operation, value, ''))
            except Exception as error:
                results.put((generation, operation, None, str(error)[:500]))
        self._worker = threading.Thread(target=work, name='hayat-local-image-tools', daemon=True)
        self._worker.start()
        return True

    def inspect(self):
        try:
            path = self._local_path(self.path, 'first')
        except ValueError as error:
            self.status.set(str(error))
            return False
        return self._run('inspect', 'local_image_inspect', {'path': path}, 'Inspecting the first image locally…')

    def compare(self):
        try:
            first, second = self._local_path(self.path, 'first'), self._local_path(self.other, 'second')
        except ValueError as error:
            self.status.set(str(error))
            return False
        return self._run('compare', 'local_image_compare', {'path': first, 'other': second}, 'Comparing both images locally…')

    def check_toolkit(self):
        return self._run('status', 'local_image_toolkit_status', {}, 'Checking installed local image tools and versions…')

    def cancel(self):
        self._cancel.set()
        self.generation += 1
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.status.set('Cancelled. Results will be discarded; the local worker must finish before another operation starts.')
        self._buttons()

    def _poll(self):
        if self._closed:
            return
        for _ in range(15):
            try:
                generation, operation, value, error = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            self.busy = False
            self.progress.stop()
            self.progress.pack_forget()
            if error:
                self.status.set(error)
            else:
                try:
                    self._interlock.check(self._ticket)
                    from app.perception.image_tools import format_image_tool_report
                    text = format_image_tool_report(value)
                    if not isinstance(text, str):
                        raise ValueError('The local image report could not be formatted.')
                    self._set_report(text)
                    self.last_result = value
                    self.status.set('Local toolkit report ready. Package versions and project locations are shown below.' if operation == 'status'
                                    else 'Local image report ready. Measurements describe the files; no identity or authenticity is established.')
                except Exception as problem:
                    self.status.set(str(problem)[:500])
            self._buttons()
        if not self.busy and self._worker is not None and not self._worker.is_alive():
            self._worker = None
            self._buttons()
        self._poll_job = self.after(80, self._poll)

    def _set_report(self, text):
        if self.report.get('1.0', 'end-1c') == text:
            return
        self.report._smooth_scroller.cancel()
        self.report.configure(state='normal')
        self.report.delete('1.0', 'end')
        self.report.insert('1.0', text)
        self.report.configure(state='disabled')

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        self.generation += 1
        self.progress.stop()
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self.destroy()

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        self._cancel.set()
        self.generation += 1
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
