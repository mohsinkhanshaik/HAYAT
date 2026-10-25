"""Manual local comparison against explicitly enrolled, consenting people."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.gui_intelligence_parts import FlowRow
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over


class EnrolledFacesWindow(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        p = self.palette
        self._closed, self.busy, self.generation = False, False, 0
        self._cancel = threading.Event()
        self._results = queue.Queue()
        self._poll_job = None
        self._worker = None
        self._operation = ''
        self._people = {}
        self.last_result = None
        self.title('HAYAT · Enrolled people · local only')
        self.configure(bg=p.BLACK)
        self.minsize(640, 580)
        self.transient(gui.root)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self.name, self.image_path = StringVar(self), StringVar(self)
        self.consent = BooleanVar(self, False)
        self.status = StringVar(self, 'Choose a local image or refresh your enrolled list. No camera, network or download starts here.')

        header = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        header.pack(fill='x')
        tk.Label(header, text='Enrolled people · local only', bg=p.BLACK, fg=p.WHITE,
                 font=('Segoe UI Semibold', 16)).pack(side='left')
        ttk.Button(header, text='Close', command=self.close, style='Hayat.TButton').pack(side='right')
        from app.gui_research_links import research_links
        self.workspace_links = research_links(header, gui, 'faces', image=self.image_path.get)
        self.intro = tk.Label(self, text='Compare your image only with people explicitly enrolled on this computer. '
                              'Unknown faces stay unknown. This does not search the web or verify a legal identity.',
                              bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=800)
        self.intro.pack(fill='x', padx=14, pady=(0, 8))
        fields = self.fields = tk.Frame(self, bg=p.BLACK)
        fields.pack(fill='x', padx=14)
        fields.columnconfigure(1, weight=1)
        tk.Label(fields, text='Local image', bg=p.BLACK, fg=p.MUTED).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.path_entry = ttk.Entry(fields, textvariable=self.image_path)
        self.path_entry.grid(row=0, column=1, sticky='ew', pady=3)
        self.choose_button = ttk.Button(fields, text='Choose image…', command=self.choose_image)
        self.choose_button.grid(row=0, column=2, padx=(7, 0), pady=3)
        tk.Label(fields, text='Enrollment name', bg=p.BLACK, fg=p.MUTED).grid(row=1, column=0, sticky='w', padx=(0, 8))
        self.name_entry = ttk.Entry(fields, textvariable=self.name)
        self.name_entry.grid(row=1, column=1, columnspan=2, sticky='ew', pady=3)
        self.consent_control = tk.Checkbutton(self,
            text="I have this person's explicit permission to store a local face template under this name.", variable=self.consent,
            bg=p.BLACK, fg=p.WHITE, activebackground=p.BLACK, activeforeground=p.WHITE, selectcolor=p.PANEL,
            anchor='w', justify='left', wraplength=800, font=('Segoe UI', 9), padx=0)
        self.consent_control.pack(anchor='w', padx=14, pady=(5, 3))
        self.retention = tk.Label(self, text='Enrollment stores numeric face templates locally until you forget that person. '
                                  'They are sensitive biometric data. A comparison can be wrong.',
                                  bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=800)
        self.retention.pack(fill='x', padx=14, pady=(0, 5))
        self.actions = FlowRow(self, p.BLACK, gap=5, height=36)
        self.actions.pack(fill='x', padx=14, pady=3)
        self.buttons = {}
        for key, label, command in (
                ('recognize', 'Compare local image', self.recognize), ('enroll', 'Enroll consenting person', self.enroll),
                ('status', 'Check local setup', self.check_status), ('list', 'Refresh enrolled list', self.refresh),
                ('forget', 'Forget selected person', self.forget), ('cancel', 'Cancel', self.cancel)):
            button = ttk.Button(self.actions, text=label, command=command,
                                style='HayatAccent.TButton' if key == 'recognize' else 'Hayat.TButton')
            self.actions.add(button)
            self.buttons[key] = button
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                     anchor='w', justify='left', wraplength=800)
        self.status_label.pack(fill='x', padx=14, pady=5)
        self.progress = ttk.Progressbar(self, mode='indeterminate', style='Hayat.Horizontal.TProgressbar')
        self.body = tk.PanedWindow(self, orient='vertical', bg=p.BLACK, sashwidth=7, bd=0)
        self.body.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        gallery = tk.Frame(self.body, bg=p.BLACK)
        gallery_head = tk.Frame(gallery, bg=p.BLACK)
        gallery_head.pack(fill='x', pady=(0, 3))
        self.gallery_summary = StringVar(self, 'Your enrolled gallery')
        tk.Label(gallery_head, textvariable=self.gallery_summary, bg=p.BLACK, fg=p.WHITE, anchor='w').pack(side='left')
        self.gallery_filter = StringVar(self)
        self.filter_entry = ttk.Entry(gallery_head, textvariable=self.gallery_filter, width=20)
        self.filter_entry.pack(side='right')
        tk.Label(gallery_head, text='Filter', bg=p.BLACK, fg=p.MUTED).pack(side='right', padx=6)
        self._gallery_rows = []
        self.gallery_filter.trace_add('write', lambda *_a: self._render_people())
        table_frame = tk.Frame(gallery, bg=p.PANEL)
        table_frame.pack(fill='both', expand=True)
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.people = ttk.Treeview(table_frame, columns=('signatures',), show='tree headings', selectmode='browse', height=4)
        self.people.heading('#0', text='Enrolled person')
        self.people.heading('signatures', text='Stored templates')
        self.people.column('#0', width=350, minwidth=190)
        self.people.column('signatures', width=150, minwidth=110)
        self.people.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table_frame, orient='vertical', command=self.people.yview)
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.people.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.people.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.people.bind('<<TreeviewSelect>>', lambda _e: self._buttons())
        self.report = DarkScrolledText(self.body, bg=p.PANEL, fg=p.WHITE, wrap='word', state='disabled',
                                       height=9, font=('Segoe UI', 10), padx=10, pady=8)
        self.body.add(gallery, height=150, minsize=105)
        self.body.add(self.report.frame, minsize=100)
        self._set_report('LOCAL ENROLLED-PEOPLE COMPARISON\n\n'
                         '1. Choose a local image.\n'
                         '2. Compare it with your enrolled gallery, or enter a name and explicitly confirm permission to enroll.\n'
                         '3. Review enrolled, unknown, ambiguous or unreadable results.\n\n'
                         'Raw cosine similarity measures vector similarity. It is not a probability or proof of identity.\n'
                         'No public camera, Internet identity search, model download or message is started by this window.')
        self.name.trace_add('write', self._scope_changed)
        self.image_path.trace_add('write', self._scope_changed)
        self.consent.trace_add('write', lambda *_a: self._buttons())
        self.bind('<Configure>', self._fit, add='+')
        self._buttons()
        place_over(self, gui.root, (920, 760))
        self.deiconify()
        self.lift()
        self._poll_job = self.after(80, self._poll)

    def _scope_changed(self, *_args):
        if self.consent.get():
            self.consent.set(False)
        self._buttons()

    def _fit(self, event):
        if event.widget is self:
            compact = event.height < 640
            if compact and self.intro.winfo_manager():
                self.intro.pack_forget()
                self.retention.pack_forget()
            elif not compact and not self.intro.winfo_manager():
                self.intro.pack(fill='x', padx=14, pady=(0, 8), before=self.fields)
                self.retention.pack(fill='x', padx=14, pady=(0, 5), before=self.actions)
            for label in (self.intro, self.retention, self.status_label):
                label.configure(wraplength=max(250, event.width - 35))
            self.consent_control.configure(wraplength=max(250, event.width - 65))

    def _buttons(self):
        if self._closed:
            return
        running = self._worker is not None and self._worker.is_alive()
        for key, button in self.buttons.items():
            enabled = not self.busy and not running
            if key == 'cancel':
                enabled = self.busy and self._operation not in {'enroll', 'forget'}
            elif key == 'recognize':
                enabled = enabled and bool(self.image_path.get().strip())
            elif key == 'enroll':
                enabled = enabled and bool(self.name.get().strip()) and bool(self.image_path.get().strip()) and self.consent.get()
            elif key == 'forget':
                enabled = enabled and bool(self.people.selection())
            button.configure(state='normal' if enabled else 'disabled')
        self.choose_button.configure(state='disabled' if self.busy or running else 'normal')

    def choose_image(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self, title='Choose a local image for enrolled-person comparison',
                                         filetypes=[('Images', '*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff')])
        if path:
            self.image_path.set(path)
            self.status.set('Local image selected. Compare it or enroll one consenting person; no analysis starts automatically.')

    def _local_path(self):
        value = self.image_path.get().strip()
        if not value:
            raise ValueError('Choose a local image first.')
        if value.startswith(('\\\\', '//')) or '://' in value:
            raise ValueError('Choose an image stored on this computer; URLs and network shares are not accepted here.')
        return str(Path(value).expanduser())

    def _run(self, operation, tool, arguments, message):
        if self._closed or self.busy:
            return False
        if self._worker is not None and self._worker.is_alive():
            self.status.set('The previous local operation is still finishing. Wait before starting another.')
            return False
        try:
            interlock = self.gui.runtime.registry.interlock
            ticket = interlock.ticket()
        except Exception as error:
            self.status.set(str(error)[:400])
            return False
        self.generation += 1
        generation, cancel = self.generation, threading.Event()
        self._cancel, self.busy = cancel, True
        self._operation = operation
        self.status.set(message)
        self.progress.pack(fill='x', padx=14, pady=(0, 5), before=self.body)
        self.progress.start(15)
        self._buttons()
        registry, results = self.gui.runtime.registry, self._results
        def check():
            if cancel.is_set():
                raise InterruptedError('Local face operation cancelled.')
            interlock.check(ticket)
        def dispatch(name, payload):
            check()
            from app.tools.faces import face_controls
            with face_controls(check=check):
                result = registry.execute(name, payload)
            check()
            if not result.success:
                raise RuntimeError(result.error or 'Local face tool failed.')
            if not isinstance(result.output, dict):
                raise ValueError('Local face tool returned no structured result.')
            return result.output
        def work():
            try:
                value = dispatch(tool, arguments)
                gallery = None
                if ((operation == 'enroll' and value.get('enrolled') is True)
                        or (operation == 'forget' and value.get('forgotten') is True)):
                    try:
                        gallery = dispatch('face_list_enrolled', {})
                    except Exception as error:
                        check()
                        gallery = {'refresh_error': str(error)[:200]}
                results.put((generation, operation, value, gallery, ''))
            except Exception as error:
                results.put((generation, operation, None, None, str(error)[:500]))
        self._worker = threading.Thread(target=work, name='hayat-enrolled-faces', daemon=True)
        self._worker.start()
        return True

    def recognize(self):
        try:
            path = self._local_path()
        except ValueError as error:
            self.status.set(str(error))
            return False
        return self._run('recognize', 'face_recognize', {'path': path}, 'Comparing the local image with enrolled templates…')

    def enroll(self):
        name = self.name.get().strip()
        if not name or not self.consent.get():
            self.status.set('Enter a name and explicitly confirm that person agreed to local template storage.')
            return False
        try:
            path = self._local_path()
        except ValueError as error:
            self.status.set(str(error))
            return False
        return self._run('enroll', 'face_enroll', {'name': name, 'path': path, 'consent': True},
                         'Enrolling the consenting person locally…')

    def check_status(self):
        return self._run('status', 'face_recognition_status', {}, 'Checking local face model availability…')

    def refresh(self):
        return self._run('list', 'face_list_enrolled', {}, 'Reading your local enrolled gallery…')

    def forget(self):
        selection = self.people.selection()
        person = self._people.get(selection[0]) if selection else None
        if not person:
            self.status.set('Select an enrolled person to remove their local templates.')
            return False
        name = person['name']
        return self._run('forget', 'face_forget', {'name': name}, 'Forgetting local templates for ' + name + '…')

    def cancel(self):
        if self.busy and self._operation in {'enroll', 'forget'}:
            self.status.set('Local save/removal is completing; refresh the gallery afterward to inspect it.')
            return
        self._cancel.set()
        self.generation += 1
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.status.set('Cancelled. An in-flight local call may finish; results will be discarded. Refresh to check the gallery.')
        self._buttons()

    def _poll(self):
        if self._closed:
            return
        for _ in range(15):
            try:
                generation, operation, value, gallery, error = self._results.get_nowait()
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
                self.last_result = value
                if gallery is not None and isinstance(gallery.get('enrolled'), list):
                    self._paint_people(gallery.get('enrolled', []))
                self._complete(operation, value)
                if gallery is not None and gallery.get('refresh_error'):
                    self.status.set(self.status.get() + ' Gallery refresh incomplete: ' + gallery['refresh_error'])
            self._buttons()
        if not self.busy and self._worker is not None and not self._worker.is_alive():
            self._worker = None
            self._buttons()
        self._poll_job = self.after(80, self._poll)

    def _complete(self, operation, value):
        if operation == 'recognize':
            from app.perception.faces import format_recognition_report
            self._set_report(format_recognition_report(value))
            self.status.set('Local comparison finished. Review states and raw cosine similarity; identity is not verified.'
                            if value.get('available') else 'Local recognition unavailable. See the reported reason; nothing was downloaded.')
        elif operation == 'status':
            self._paint_people(value.get('enrolled', []))
            lines = ['LOCAL FACE SETUP', 'Detection available: ' + ('yes' if value.get('detection') else 'no'),
                     'Enrolled-person recognition available: ' + ('yes' if value.get('recognition') else 'no'),
                     'Model directory: ' + str(value.get('models_dir', 'Not reported')), str(value.get('reason', '')),
                     'Gallery storage: ' + str(value.get('gallery_storage', 'Not reported')), str(value.get('gallery_error', '')),
                     '', 'No model download was started. Recognition compares only your local enrolled gallery.']
            self._set_report('\n'.join(lines))
            self.status.set('Local setup checked. No camera, network request or model download was started.')
        elif operation == 'list':
            self._paint_people(value.get('enrolled', []))
            if value.get('gallery_error'):
                self.status.set('Local gallery unavailable: ' + str(value['gallery_error']))
                self._set_report(self.status.get())
            else:
                self.status.set(f'{len(self._people)} person(s) enrolled locally. Select a row to forget that person.')
        elif operation == 'enroll':
            if value.get('enrolled') is True:
                self.consent.set(False)
                duplicate = value.get('duplicate_sample') is True
                text = (f"Existing template retained for {value.get('name', 'person')} locally · {value.get('signatures', '?')} stored template(s)."
                        if duplicate else f"Enrolled {value.get('name', 'person')} locally · {value.get('signatures', '?')} stored template(s).")
                self.status.set(text + ' Permission must be confirmed again for another enrollment.')
                note = ('\n\nThis numerically identical template was already stored. No sample slot was consumed; '
                        'use a different clear, consented still image to add variety.' if duplicate else '')
                self._set_report(text + note + '\n\nThe template stays in the local gallery until you forget this person. '
                                 'No legal identity has been verified; no Internet search was performed.')
            else:
                self.status.set('Not enrolled: ' + str(value.get('reason', 'No enrollment was stored.')))
                self._set_report(self.status.get())
        elif operation == 'forget':
            self.status.set(('Forgot local templates for ' if value.get('forgotten') else 'No enrolled templates were found for ')
                            + str(value.get('name', 'that person')) + '.')
            self._set_report(self.status.get() + '\n\nRefresh shows the remaining local enrolled gallery. No message was sent.')

    def _paint_people(self, rows):
        self._gallery_rows = [row for row in rows if isinstance(row, dict) and isinstance(row.get('name'), str)]
        self._render_people()

    def _render_people(self):
        selected = self.people.selection()
        name = self._people.get(selected[0], {}).get('name') if selected else None
        position = self.people.yview()
        self.people.delete(*self.people.get_children())
        self._people = {}
        query = self.gallery_filter.get().strip().casefold()
        for index, row in enumerate(self._gallery_rows):
            if query and query not in row['name'].casefold():
                continue
            key = str(index)
            self._people[key] = row
            self.people.insert('', 'end', iid=key, text=row['name'], values=(row.get('signatures', '?'),))
            if row['name'] == name:
                self.people.selection_set(key)
        if position:
            self.people.yview_moveto(position[0])
        self.gallery_summary.set(f'Enrolled gallery · {len(self._people)} / {len(self._gallery_rows)} shown')
        self._buttons()

    def _set_report(self, value):
        if self.report.get('1.0', 'end-1c') == value:
            return
        self.report._smooth_scroller.cancel()
        self.report.configure(state='normal')
        self.report.delete('1.0', 'end')
        self.report.insert('1.0', value)
        self.report.configure(state='disabled')

    def close(self):
        self._cancel.set()
        self.generation += 1
        self.progress.stop()
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
