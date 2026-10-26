"""Responsive REA workbench; workers never touch Tk and all calls use HAYAT's registry."""
from __future__ import annotations

import json
import queue
import threading
import time
from contextvars import copy_context
import tkinter as tk
from tkinter import filedialog, ttk

from jsonschema import ValidationError, validate

from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_rea_inputs import GuidedInputs
from app.rea_workbench import WorkbenchSession, parse_arguments
from app.plain_output import describe
from app.gui_fonts import DISPLAY


class ReverseEngineeringStudio(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.registry = gui, gui.runtime.registry
        self._closed, self._busy = False, False
        self._queue = queue.Queue()
        self._tools = {}
        self._poll_id = None
        self.session = getattr(gui, '_rea_workbench_session', None) or WorkbenchSession()
        gui._rea_workbench_session = self.session
        self._request = None
        self._view_revision = 0
        self._selected_record = None
        self._history_ids = ()
        self._compact = None
        self._link_cancel = threading.Event()
        self._evidence_rows = {}
        self._evidence_root = ''
        self.title('HAYAT · Reverse engineering')
        self.configure(bg=gui.BLACK)
        self.minsize(680, 560)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self._destroyed, add='+')
        self.bind('<Escape>', lambda _e: self.close())
        self.bind('<Control-Return>', lambda _e: (self.run_operation(), 'break')[1])
        self.bind('<Control-f>', self.focus_find)
        self._build()
        place_over(self, gui.root, (1080, 780))
        self.deiconify()
        self._poll()
        self.load_tools()

    def _build(self):
        p = self.gui
        head = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        head.pack(fill='x')
        tk.Label(head, text='Reverse engineering', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side='left')
        self.reload = ttk.Button(head, text='Refresh operations', command=self.load_tools)
        self.reload.pack(side='right')
        ttk.Button(head, text='Save report…', command=self.save_result).pack(side='right', padx=6)
        ttk.Button(head, text='3D call graph', command=self.open_graph_3d).pack(side='right', padx=6)
        self.progress = ttk.Progressbar(self, mode='indeterminate')
        self.status = StringVar(self, 'Loading local REA operations…')
        tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD, anchor='w',
                 wraplength=900).pack(fill='x', padx=14, pady=(0, 8))
        target = ttk.Frame(self, padding=(14, 0, 14, 8))
        target.pack(fill='x')
        self.target = StringVar(self)
        ttk.Label(target, text='Target or app link').grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(target, textvariable=self.target)
        entry.grid(row=0, column=1, sticky='ew', padx=8)
        entry.bind('<Return>', lambda _event: self.inspect_link() if self.target.get().strip().startswith(('http://', 'https://')) else self.fill_target())
        ttk.Button(target, text='File…', command=self.choose_file).grid(row=0, column=2)
        ttk.Button(target, text='Folder…', command=self.choose_folder).grid(row=0, column=3, padx=(6, 0))
        self.link_button = ttk.Button(target, text='Inspect link', command=self.inspect_link)
        self.link_button.grid(row=0, column=4, padx=(6, 0))
        target.columnconfigure(1, weight=1)
        self.footer = ttk.Label(self, text='REA runs locally. Native analysis requires a compatible engine. '
                               'Changing actions appear in HAYAT’s confirmation window.', wraplength=900)
        self.footer.pack(side='bottom', fill='x', padx=14, pady=8)
        self.bind('<Configure>', self._fit, add='+')
        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=14)
        left, right = ttk.Frame(body), ttk.Frame(body)
        body.add(left, weight=1)
        body.add(right, weight=3)
        ttk.Label(left, text='Filter operations').pack(anchor='w', pady=(0, 3))
        self.filter = StringVar(self)
        self.filter_entry = ttk.Entry(left, textvariable=self.filter)
        self.filter_entry.pack(fill='x', pady=(0, 6))
        self.filter.trace_add('write', lambda *_args: self.filter_tools())
        listing = ttk.Frame(left)
        listing.pack(fill='both', expand=True)
        self.operations = ttk.Treeview(listing, columns=('mode',), show='tree headings', selectmode='browse')
        self.operations.heading('#0', text='Operation')
        self.operations.heading('mode', text='Action')
        self.operations.column('#0', width=210, minwidth=120)
        self.operations.column('mode', width=70, minwidth=60, stretch=False)
        vertical = ttk.Scrollbar(listing, orient='vertical', command=self.operations.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.operations.xview)
        self.operations.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.operations.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.operations.bind('<<TreeviewSelect>>', self.select_operation)
        notebook = self.notebook = ttk.Notebook(right)
        notebook.pack(fill='both', expand=True, padx=(10, 0))
        run, reference = ttk.Frame(notebook), ttk.Frame(notebook)
        notebook.add(run, text='Analysis')
        notebook.add(reference, text='Operation reference')
        engines = ttk.Frame(notebook)
        notebook.add(engines, text='Engine status')
        engine_bar = ttk.Frame(engines)
        engine_bar.pack(fill='x', pady=6)
        self.check_engine_button = ttk.Button(engine_bar, text='Check engines', command=self.check_engines)
        self.check_engine_button.pack(side='left')
        ttk.Button(engine_bar, text='Copy setup commands', command=self.copy_setup_commands).pack(side='left', padx=6)
        self.engine_output = DarkScrolledText(engines, wrap='word', state='disabled', font=('Cascadia Mono', 10),
                                            bg=p.PANEL, fg=p.WHITE, insertbackground=p.GOLD, selectbackground=p.ROYAL)
        self.engine_output.pack(fill='both', expand=True)
        from app.rea_diagnostics import SETUP_COMMANDS
        self._replace(self.engine_output, 'Check the local Node, REA, Ghidra and JDK installation. '
                      'This does not open a target or change your current analysis.\n\n'
                      'Setup commands (run from HAYAT’s folder):\n' + SETUP_COMMANDS)
        self.selected = StringVar(self, 'Select an operation')
        heading = ttk.Frame(run)
        heading.pack(fill='x', pady=(6, 4))
        self.inputs_visible = BooleanVar(self, True)
        ttk.Checkbutton(heading, text='Show inputs', variable=self.inputs_visible,
                        command=self._toggle_inputs).pack(side='right')
        ttk.Label(heading, textvariable=self.selected).pack(side='left', fill='x', expand=True)
        colors = {'bg': p.PANEL, 'fg': p.WHITE, 'insertbackground': p.GOLD, 'selectbackground': p.ROYAL}
        self.input_tabs = ttk.Notebook(run, height=135)
        self.input_tabs.pack(fill='x', pady=4)
        self.guided = GuidedInputs(self.input_tabs, p, self._guided_changed)
        raw = ttk.Frame(self.input_tabs)
        self.input_tabs.add(self.guided, text='Inputs')
        self.input_tabs.add(raw, text='JSON arguments')
        self.input_tabs.bind('<<NotebookTabChanged>>', self._input_tab_changed)
        self.arguments = DarkScrolledText(raw, height=6, wrap='word', font=('Cascadia Mono', 10), **colors)
        self.arguments.pack(fill='both', expand=True)
        bar = self._input_bar = ttk.Frame(run)
        bar.pack(fill='x')
        self.use_target = ttk.Button(bar, text='Use target', command=self.fill_target)
        self.use_target.pack(side='left')
        self.cancel_link_button = ttk.Button(bar, text='Cancel link', command=self.cancel_link, state='disabled')
        self.cancel_link_button.pack(side='left', padx=(6, 0))
        self.timeout = StringVar(self, '180')
        ttk.Label(bar, text='Timeout (s)').pack(side='left', padx=(12, 4))
        ttk.Entry(bar, textvariable=self.timeout, width=6).pack(side='left')
        self.run_button = ttk.Button(bar, text='Run analysis', command=self.run_operation, state='disabled')
        self.run_button.pack(side='right')
        history = ttk.Frame(run)
        history.pack(fill='x', pady=(6, 4))
        self.history = ttk.Combobox(history, state='readonly', width=30)
        self.history.set('Recent results — this session')
        self.history.pack(side='left', fill='x', expand=True)
        self.history.bind('<<ComboboxSelected>>', self.show_history)
        ttk.Button(history, text='Restore', command=self.restore_inputs).pack(side='left', padx=(6, 0))
        self.output_tabs = ttk.Notebook(run)
        self.output_tabs.pack(fill='both', expand=True)
        self.summary = DarkScrolledText(self.output_tabs, height=6, wrap='word', state='disabled', font=('Segoe UI', 10), **colors)
        report = ttk.Frame(self.output_tabs)
        self.output_tabs.add(self.summary.frame, text='Summary')
        self.output_tabs.add(report, text='Full report')
        self.output_tabs.select(report)
        find = ttk.Frame(report)
        find.pack(fill='x')
        self.find_query = StringVar(self)
        self.find_entry = ttk.Entry(find, textvariable=self.find_query)
        self.find_entry.pack(side='left', fill='x', expand=True)
        self.find_entry.bind('<Return>', lambda _e: self.find_next())
        ttk.Button(find, text='Find next', command=self.find_next).pack(side='left', padx=(4, 0))
        self.result = DarkScrolledText(report, height=12, wrap='word', state='disabled', font=('Cascadia Mono', 10), **colors)
        self.result.pack(fill='both', expand=True)
        self.result.tag_configure('rea_find', background=p.GOLD, foreground=p.BLACK)
        self._find_cursor = '1.0'
        evidence = ttk.Frame(self.output_tabs)
        self.output_tabs.add(evidence, text='Source evidence')
        evidence.columnconfigure(0, weight=1)
        evidence.rowconfigure(1, weight=1, minsize=85)
        evidence.rowconfigure(2, weight=1, minsize=80)
        self.verify_evidence_button = ttk.Button(evidence, text='Verify selected saved file',
                                                 command=self.verify_evidence, state='disabled')
        self.verify_evidence_button.grid(row=0, column=0, sticky='w', pady=4)
        listing = ttk.Frame(evidence)
        listing.grid(row=1, column=0, sticky='nsew')
        self.evidence_table = ttk.Treeview(listing, columns=('kind', 'bytes', 'url'), show='headings', height=4)
        for key, title, width in (('kind', 'Kind', 85), ('bytes', 'Bytes', 75), ('url', 'Published URL', 300)):
            self.evidence_table.heading(key, text=title)
            self.evidence_table.column(key, width=width, minwidth=60)
        vertical = ttk.Scrollbar(listing, command=self.evidence_table.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.evidence_table.xview)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        self.evidence_table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.evidence_table.pack(fill='both', expand=True)
        self.evidence_table.bind('<<TreeviewSelect>>', lambda _e: self.show_evidence())
        self.evidence_text = DarkScrolledText(evidence, height=5, wrap='word', state='disabled', font=('Segoe UI', 10), **colors)
        self.evidence_text.grid(row=2, column=0, sticky='nsew', pady=(5, 0))
        self._replace(self.evidence_text, 'Inspect a public app link to collect source hashes, import relationships and advertised source maps. No target code is executed.')
        self.reference = DarkScrolledText(reference, wrap='word', state='disabled', font=('Cascadia Mono', 10), **colors)
        self.reference.pack(fill='both', expand=True)
        self._refresh_history()
        if self.session.results:
            self.history.current(0)
            self.show_history()

    def _fit(self, event):
        if event.widget is self:
            self.footer.configure(wraplength=max(300, event.width - 28))
            height = 135 if event.height >= 680 else 80 if event.height >= 600 else 55
            self.input_tabs.configure(height=height)
            compact = event.height < 680
            if compact != self._compact:
                self._compact = compact
                self.inputs_visible.set(not compact)
                self._toggle_inputs()

    def _toggle_inputs(self):
        if self.inputs_visible.get():
            self.input_tabs.pack(fill='x', pady=4, before=self._input_bar)
        else:
            self.input_tabs.pack_forget()

    @staticmethod
    def _replace(widget, value):
        scroller = getattr(widget, '_smooth_scroller', None)
        if scroller is not None:
            scroller.cancel()
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', value)
        widget.configure(state='disabled')

    def choose_file(self):
        value = filedialog.askopenfilename(parent=self, title='Choose an analysis target')
        if value:
            self.target.set(value)
            self.fill_target()

    def choose_folder(self):
        value = filedialog.askdirectory(parent=self, title='Choose an extracted application')
        if value:
            self.target.set(value)
            self.fill_target()

    def save_result(self):
        from pathlib import Path
        value = self.result.get('1.0', 'end-1c')
        if not value:
            self.status.set('Run an analysis before saving a result.')
            return
        path = filedialog.asksaveasfilename(parent=self, title='Save displayed analysis', defaultextension='.txt',
                                          filetypes=[('Text report', '*.txt')])
        if path:
            try:
                Path(path).write_text(value, encoding='utf-8')
                self.status.set('Displayed result saved.')
            except OSError as error:
                self.status.set(f'Could not save: {error}')

    def _dispatch(self, name, arguments):
        if self._busy or self._closed:
            return
        interlock = getattr(self.registry, 'interlock', None)
        try:
            ticket = interlock.ticket() if interlock is not None else None
        except Exception as error:
            self.status.set(str(error))
            return
        self._busy = True
        self._link_cancel = threading.Event()
        cancel = self._link_cancel
        self.cancel_link_button.configure(state='normal' if name == 'reverse_engineer_link' else 'disabled')
        if name not in {'reverse_engineer_tools', 'reverse_engineer_status'} and self._compact:
            self.inputs_visible.set(False)
            self._toggle_inputs()
        self._request = {'name': name, 'arguments': arguments, 'started': time.monotonic(), 'revision': self._view_revision}
        self.status.set('Reading public app source… You can keep reading and scrolling.' if name == 'reverse_engineer_link'
                        else 'Working locally… You can keep reading and scrolling.')
        self.run_button.configure(state='disabled')
        self.reload.configure(state='disabled')
        self.link_button.configure(state='disabled')
        self.check_engine_button.configure(state='disabled')
        self.progress.pack(fill='x', padx=14, before=self.footer)
        self.progress.start(40)
        registry, results, session = self.registry, self._queue, self.session
        request = self._request
        def work():
            try:
                from app.rea_links import source_controls
                with source_controls(cancel=cancel if name == 'reverse_engineer_link' else None,
                                     check=(lambda: interlock.check(ticket)) if interlock is not None else None):
                    result = registry.execute(name, arguments)
                success, value = result.success, result.output if result.success else result.error
                if name == 'reverse_engineer_link' and cancel.is_set():
                    success, value = False, 'Link inspection cancelled; late results were discarded.'
            except Exception as error:
                success, value = False, f'{type(error).__name__}: {error}'
            record = None
            if name not in {'reverse_engineer_tools', 'reverse_engineer_status'}:
                record = session.remember(arguments.get('tool', name), arguments.get('arguments', arguments), arguments.get('timeout', 180),
                                          success, value, time.monotonic() - request['started'])
            results.put((name, success, value, record))
        context = copy_context()
        threading.Thread(target=lambda: context.run(work), name='hayat-rea-workbench', daemon=True).start()

    def cancel_link(self):
        if self._busy and self._request and self._request['name'] == 'reverse_engineer_link':
            self._link_cancel.set()
            self.cancel_link_button.configure(state='disabled')
            self.status.set('Cancelling link collection. An in-flight fetch or static analysis may finish; late results are discarded.')

    def open_graph_3d(self):
        """Open the call graph of the current target in a 3D view (app/gui_rea_graph3d.py)."""
        from app.gui_rea_graph3d import ReaGraph3DWindow, engine_loader

        registry = self.registry

        class _Reader:
            def read(self, tool, arguments=None):
                result = registry.execute("reverse_engineer_read", {"tool": tool, "arguments": arguments or {}})
                if not result.success:
                    raise RuntimeError(result.error or f"{tool} failed")
                return result.output

        window = getattr(self, "_graph_window", None)
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            window.refresh()
            return
        self._graph_window = ReaGraph3DWindow(self.gui, engine_loader(_Reader()), decompile=self._decompile_from_graph)

    def _decompile_from_graph(self, procedure):
        """Double-clicking a graph point shows that procedure's pseudocode in the workbench."""
        self._dispatch("reverse_engineer_read", {"tool": "procedure_pseudo_code", "arguments": {"procedure": procedure}})

    def load_tools(self):
        if self._busy or self._closed:
            return
        from app.reverse_engineer import available
        if not available():
            self._remember_draft()
            self._tools.clear()
            self.operations.delete(*self.operations.get_children())
            self.run_button.configure(state='disabled')
            # A new install has no REA yet: say exactly how to set it up, rather than failing cryptically.
            self.status.set("REA is not installed yet. In a terminal here run:  "
                            ".venv\\Scripts\\python.exe scripts/setup_rea.py  "
                            "(and scripts/setup_native_analysis.py for native binaries), then Refresh operations.")
            return
        self._dispatch('reverse_engineer_tools', {})

    def check_engines(self):
        if not self._busy and not self._closed:
            self.notebook.select(2)
            self._dispatch('reverse_engineer_status', {'check_engine': True})

    def copy_setup_commands(self):
        from app.rea_diagnostics import SETUP_COMMANDS
        try:
            self.clipboard_clear()
            self.clipboard_append(SETUP_COMMANDS)
        except tk.TclError as error:
            self.status.set(f'Could not copy setup commands: {error}')
        else:
            self.status.set('Setup commands copied. Run them from HAYAT’s folder, then check engines again.')

    def filter_tools(self):
        selected = self.operations.selection()
        view = self.operations.yview()
        self.operations.delete(*self.operations.get_children())
        query = self.filter.get().casefold().strip()
        for name, row in self._tools.items():
            if not query or query in (name + ' ' + row.get('summary', '')).casefold():
                self.operations.insert('', 'end', iid=name, text=name, values=('Confirm' if row['writes'] else 'Read',))
        if selected and self.operations.exists(selected[0]):
            self.operations.selection_set(selected[0])
            self.operations.yview_moveto(view[0])

    def select_operation(self, _event=None, *, force=False):
        choice = self.operations.selection()
        if not choice:
            return
        name = choice[0]
        if self.selected.get() == name and not force:
            return  # refresh/filter must not overwrite an edited argument draft
        self._remember_draft()
        row = self._tools[name]
        self.selected.set(name)
        schema = row.get('input_schema', {})
        values = {key: prop['default'] for key, prop in schema.get('properties', {}).items()
                  if isinstance(prop, dict) and 'default' in prop}
        self.arguments.delete('1.0', 'end')
        draft = self.session.drafts.get(name)
        self.arguments.insert('1.0', draft if draft is not None else json.dumps(values, indent=2))
        try:
            form_values = parse_arguments(self.arguments.get('1.0', 'end'))
        except ValueError:
            form_values = {}
        self.guided.build(schema, form_values if isinstance(form_values, dict) else {})
        self.guided.load_raw(self.session.field_drafts.get(name, {}))
        from app.plain_output import describe_schema

        self._replace(self.reference, row.get('description', '') + '\n\nWhat it needs\n' + describe_schema(schema))
        self.run_button.configure(text='Prepare action' if row['writes'] else 'Run analysis',
                                  state='disabled' if self._busy else 'normal')
        if self.target.get() and draft is None:
            self.fill_target()

    def _remember_draft(self):
        name = self.selected.get()
        raw = self.arguments.get('1.0', 'end-1c')
        if name in self._tools and len(raw) <= 64_000:
            self.session.drafts.pop(name, None)
            self.session.drafts[name] = raw
            if self.input_tabs.select() == str(self.guided):
                self.session.field_drafts[name] = {key: variable.get() for key, (variable, *_rest) in self.guided.fields.items()}
            else:
                self.session.field_drafts.pop(name, None)
            if len(self.session.drafts) > 20:
                old = next(iter(self.session.drafts))
                self.session.drafts.pop(old)
                self.session.field_drafts.pop(old, None)

    def _guided_changed(self):
        try:
            base = parse_arguments(self.arguments.get('1.0', 'end'))
            values = self.guided.values(base)
            self.arguments.delete('1.0', 'end')
            self.arguments.insert('1.0', json.dumps(values, indent=2))
        except (ValueError, KeyError) as error:
            self.status.set(f'Check inputs: {str(error)[:180]}')

    def _input_tab_changed(self, _event=None):
        if self.selected.get() not in self._tools:
            return
        if self.input_tabs.select() == str(self.guided):
            try:
                values = parse_arguments(self.arguments.get('1.0', 'end'))
                self.guided.load(values)
            except ValueError as error:
                self.status.set(f'Check JSON arguments: {str(error)[:180]}')
                self.input_tabs.select(1)

    def fill_target(self):
        row = self._tools.get(self.selected.get(), {})
        from app.reverse_engineer import is_url
        if is_url(self.target.get()) and self.selected.get() != 'open_binary':
            self.status.set('Use Inspect link for a web app or JavaScript URL; native download links prepare Open target.')
            return
        props = row.get('input_schema', {}).get('properties', {})
        field = next((key for key in ('input_path', 'path', 'artifact_path', 'binary_path') if key in props), None)
        if not field or not self.target.get().strip():
            self.status.set('Choose an operation with a local path input, then use the target.')
            return
        try:
            values = parse_arguments(self.arguments.get('1.0', 'end'))
            values[field] = self.target.get().strip()
            if 'format' in props and self.selected.get() == 'analyze_javascript_application':
                from pathlib import Path
                values['format'] = 'directory' if Path(values[field]).is_dir() else 'asar' if Path(values[field]).suffix == '.asar' else 'auto'
            self.arguments.delete('1.0', 'end')
            self.arguments.insert('1.0', json.dumps(values, indent=2))
            self.guided.load(values)
            self.status.set(f'Target entered for {self.selected.get()}')
        except (ValueError, TypeError) as error:
            self.status.set(str(error))

    def inspect_link(self):
        from app.reverse_engineer import is_url
        from app.rea_links import native_link
        url = self.target.get().strip()
        if not is_url(url):
            self.status.set('Paste a public http:// or https:// app link first.')
            return
        if native_link(url):
            self._dispatch('reverse_engineer_act', {'tool': 'open_binary', 'arguments': {'path': url}})
        else:
            self._dispatch('reverse_engineer_link', {'url': url})

    def run_operation(self):
        row = self._tools.get(self.selected.get())
        if row is None or self._busy:
            return
        try:
            raw = self.arguments.get('1.0', 'end')
            values = parse_arguments(raw)
            # Invalid numeric form text must not silently reuse the previous valid JSON value.
            if self.input_tabs.select() == str(self.guided):
                values = self.guided.values(values)
            validate(values, row.get('input_schema', {'type': 'object'}))
            timeout = float(self.timeout.get())
            if not 1 <= timeout <= 1800:
                raise ValueError('Timeout must be between 1 and 1800 seconds.')
        except ValidationError as error:
            self.inputs_visible.set(True)
            self._toggle_inputs()
            field = '.'.join(str(part) for part in error.absolute_path) or 'Arguments'
            self.status.set(f'Check inputs — {field}: {error.message[:200]}')
            return
        except Exception as error:
            self.inputs_visible.set(True)
            self._toggle_inputs()
            self.status.set(f'Check inputs: {str(error)[:250]}')
            return
        name = 'reverse_engineer_act' if row['writes'] else 'reverse_engineer_read'
        self._dispatch(name, {'tool': row['name'], 'arguments': values, 'timeout': timeout})

    def _poll(self):
        if self._closed:
            return
        try:
            name, success, value, record = self._queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.cancel_link_button.configure(state='disabled')
            self.progress.stop()
            self.progress.pack_forget()
            self.reload.configure(state='normal')
            self.link_button.configure(state='normal')
            self.check_engine_button.configure(state='normal')
            if name == 'evidence_verify':
                self._replace(self.evidence_text, value if isinstance(value, str) else describe(value))
                self.status.set('Evidence verification finished.' if success else 'Evidence verification failed; see details.')
            elif name == 'reverse_engineer_tools' and success:
                self._tools = {row['name']: row for row in value.get('tools', [])}
                self.filter_tools()
                self.status.set(f"REA {value.get('version', '')} · {len(self._tools)} operations available")
                if self.operations.selection():
                    self.select_operation(force=True)
                if not self.operations.selection() and self.operations.exists('analyze_javascript_application'):
                    self.operations.selection_set('analyze_javascript_application')
                    self.select_operation()
            elif name == 'reverse_engineer_status':
                from app.rea_diagnostics import format_engine_report
                self._replace(self.engine_output, format_engine_report(value) if success else f'Engine check failed:\n{value}')
                ready = success and value.get('engine_check', {}).get('ready')
                self.status.set('Ghidra is ready. Your current analysis is unchanged.' if ready
                                else 'Native engine setup needs attention — see Engine status.')
            elif name == 'reverse_engineer_tools' and not success:
                # The load failed after REA looked installed (e.g. a half-finished setup): show REA's own reason.
                self.status.set(f'Could not load REA operations: {str(value)[:220]}. '
                                'Try scripts/setup_rea.py, then Refresh operations.')
            else:
                request = self._request or {'arguments': {}, 'started': time.monotonic(), 'revision': self._view_revision}
                self._refresh_history()
                if request['revision'] == self._view_revision:
                    index = next((index for index, row in enumerate(self.session.results) if row['id'] == record['id']), None)
                    if index is not None:
                        self.history.current(index)
                    self.show_history()
                self.status.set('Action prepared; review in Confirmations.' if success and name == 'reverse_engineer_act'
                               else 'Analysis complete' if success else 'Operation failed — see details')
            if self.selected.get() in self._tools:
                self.run_button.configure(state='normal')
            self._request = None
        if self._busy and self._request:
            elapsed = int(time.monotonic() - self._request['started'])
            activity = 'Reading public app source' if self._request['name'] == 'reverse_engineer_link' else 'Working locally'
            self.status.set_if_changed(f"{activity} · {elapsed} s elapsed · Reading and scrolling remain available")
        self._refresh_history()
        self._poll_id = self.after(80, self._poll)

    def _refresh_history(self):
        records = self.session.results
        ids = tuple(row['id'] for row in records)
        if ids == self._history_ids:
            return
        self._history_ids = ids
        self.history.configure(values=[row['label'] for row in records])
        if self._selected_record is not None:
            for index, row in enumerate(records):
                if row['id'] == self._selected_record:
                    self.history.current(index)
                    break

    def show_history(self, _event=None):
        index = self.history.current()
        if not 0 <= index < len(self.session.results):
            return
        record = self.session.results[index]
        self._view_revision += 1
        self._selected_record = record['id']
        self._replace(self.summary, record['summary'] + f"\n\nElapsed: {record['elapsed']:.2f} s")
        self._replace(self.result, record['report'])
        self._evidence_root = record.get('evidence_root', '')
        metadata = record.get('source_evidence', {})
        self._evidence_rows.clear()
        self.evidence_table.delete(*self.evidence_table.get_children())
        for kind, rows in (('JavaScript', metadata.get('resources', [])), ('Source map', metadata.get('source_maps', []))):
            for row in rows:
                key = str(len(self._evidence_rows))
                self._evidence_rows[key] = row
                self.evidence_table.insert('', 'end', iid=key, values=(kind, row.get('bytes', ''), row.get('url', '')))
        self.verify_evidence_button.configure(state='disabled')
        self._replace(self.evidence_text, describe({'scope': metadata.get('scope', 'No retained public-source evidence for this operation.'),
                                                    'dependencies': metadata.get('dependencies', []),
                                                    'skipped': metadata.get('skipped_resources', [])}))
        self._find_cursor = '1.0'
        self.result.tag_remove('rea_find', '1.0', 'end')

    def show_evidence(self):
        selected = self.evidence_table.selection()
        row = self._evidence_rows.get(selected[0]) if selected else None
        self.verify_evidence_button.configure(state='normal' if row and not self._busy else 'disabled')
        if row:
            self._replace(self.evidence_text, describe(row))

    def verify_evidence(self):
        if self._busy:
            return
        selected = self.evidence_table.selection()
        row = dict(self._evidence_rows[selected[0]]) if selected and selected[0] in self._evidence_rows else None
        if not row:
            return
        root = self._evidence_root
        try:
            interlock = self.registry.interlock
            ticket = interlock.ticket()
        except Exception as error:
            self.status.set(str(error))
            return
        self._busy = True
        self.verify_evidence_button.configure(state='disabled')
        self._request = {'name': 'evidence_verify', 'started': time.monotonic()}
        def work():
            try:
                from app.rea_workbench import verify_collected_file
                self.registry.permissions.validate_project_path(row['path'], write=False)
                value = verify_collected_file(row, root)
                interlock.check(ticket)
                self._queue.put(('evidence_verify', True, value, None))
            except Exception as error:
                self._queue.put(('evidence_verify', False, str(error), None))
        threading.Thread(target=work, name='hayat-rea-evidence', daemon=True).start()

    def restore_inputs(self):
        index = self.history.current()
        if not 0 <= index < len(self.session.results):
            self.status.set('Choose a recent result to restore its inputs.')
            return
        record = self.session.results[index]
        name = record['operation']
        if name == 'reverse_engineer_link':
            self.target.set(json.loads(record['arguments']).get('url', ''))
            self.status.set('Link restored. Review it and press Inspect link; nothing has been submitted.')
            return
        if name not in self._tools:
            self.status.set('This operation is absent from the current catalog.')
            return
        self.filter.set('')
        self.operations.selection_set(name)
        self.select_operation()
        self.arguments.delete('1.0', 'end')
        self.arguments.insert('1.0', record['arguments'])
        self.guided.load(json.loads(record['arguments']))
        self.timeout.set(str(record['timeout']))
        self.status.set('Inputs restored. Review them and press Run; nothing has been submitted.')

    def focus_find(self, _event=None):
        self.notebook.select(0)
        self.output_tabs.select(1)
        self.find_entry.focus_set()
        return 'break'

    def find_next(self):
        query = self.find_query.get()
        if not query or len(query) > 2000:
            return
        self.output_tabs.select(1)
        self.result._smooth_scroller.cancel()
        match = self.result.search(query, self._find_cursor, stopindex='end', nocase=True, regexp=False)
        if not match:
            match = self.result.search(query, '1.0', stopindex=self._find_cursor, nocase=True, regexp=False)
        self.result.tag_remove('rea_find', '1.0', 'end')
        if match:
            end = self.result.index(f'{match}+{len(query)}c')
            self.result.tag_add('rea_find', match, end)
            self.result.see(match)
            self._find_cursor = end
        else:
            self.status.set('No match in the displayed report.')

    def close(self):
        if self._closed:
            return
        self._stop_polling()
        self.destroy()

    def _stop_polling(self):
        if self._closed:
            return
        self._link_cancel.set()
        try:
            self._remember_draft()
            self.progress.stop()
        except tk.TclError:
            pass  # the parent can destroy the editor before this Toplevel's Destroy event
        self._closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

    def _destroyed(self, event):
        if event.widget is self:
            self._stop_polling()
