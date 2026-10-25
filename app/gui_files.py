"""Responsive local file editing and a bounded, paged PDF preview."""
from __future__ import annotations

from app.gui_resources import StringVar

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from app.gui_windows import place_over
from app.tools.registry import ToolResult


class FileEditor(tk.Toplevel):
    def __init__(self, gui, path: Path):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.path = gui, path
        self._closed = False
        self._dirty = False
        self._digest = None
        self._newline = '\n'
        self.title(f'Edit {path.name}')
        self.configure(bg=gui.BLACK)
        self.transient(gui.root)
        self.status = StringVar(self, value='Reading file…')
        bar = tk.Frame(self, bg=gui.PANEL)
        bar.pack(fill='x')
        self.save_button = ttk.Button(bar, text='Save', command=self.save, state='disabled', style='Hayat.TButton')
        self.save_button.pack(side='left', padx=8, pady=8)
        ttk.Button(bar, text='Reload', command=self.load, style='Hayat.TButton').pack(side='left', padx=4)
        ttk.Button(bar, text='Revise with HAYAT', command=self.revise, style='Hayat.TButton').pack(side='left', padx=4)
        ttk.Button(bar, text='Close', command=self.close, style='Hayat.TButton').pack(side='right', padx=8)
        tk.Label(self, textvariable=self.status, bg=gui.BLACK, fg=gui.MUTED, anchor='w', wraplength=740).pack(fill='x', padx=10, pady=5)
        body = tk.Frame(self, bg=gui.BLACK)
        body.pack(fill='both', expand=True)
        self.text = tk.Text(body, wrap='none', undo=True, bg=gui.PANEL_2, fg=gui.WHITE,
                            insertbackground=gui.GOLD, font=('Consolas', 11), state='disabled')
        vertical = ttk.Scrollbar(body, orient='vertical', command=self.text.yview)
        horizontal = ttk.Scrollbar(body, orient='horizontal', command=self.text.xview)
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.text.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.text.bind('<<Modified>>', self.modified)
        self.bind('<Control-s>', lambda _e: (self.save(), 'break')[1])
        self.bind('<Escape>', lambda _e: self.close())
        self.protocol('WM_DELETE_WINDOW', self.close)
        self._generation = 0
        self._busy = False
        place_over(self, gui.root, (850, 650))
        self.deiconify()
        self.load()

    def _run(self, name, arguments, callback):
        self._generation += 1
        generation = self._generation
        self._busy = True
        self.save_button.configure(state='disabled')
        def worker():
            try:
                result = self.gui.runtime.registry.execute(name, arguments)
            except Exception as exc:
                result = ToolResult(name, False, error=f'{type(exc).__name__}: {exc}')
            self.gui._post(self._deliver, generation, callback, result)
        try:
            self.gui._preview_workers.submit(worker)
        except Exception as exc:
            self._deliver(generation, callback, ToolResult(name, False, error=f'{type(exc).__name__}: {exc}'))

    def _deliver(self, generation, callback, result):
        if self._closed or not self.winfo_exists() or generation != self._generation:
            return
        self._busy = False
        callback(result)

    def load(self):
        if self._busy:
            return
        if self._dirty and not messagebox.askyesno('Reload file', 'Discard your unsaved edits and reload?', parent=self):
            return
        self.text.configure(state='disabled')
        self.status.set('Reading file…')
        self._run('read_editable_file', {'path': str(self.path)}, self._loaded)

    def _loaded(self, result):
        if not result.success:
            self.status.set(result.error + ' Use Revise with HAYAT for Word, PDF, Excel, or PowerPoint files.')
            return
        data = result.output
        self._digest = data['sha256']
        self._newline = '\r\n' if '\r\n' in data['content'] and '\n' not in data['content'].replace('\r\n', '') else '\n'
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', data['content'].replace('\r\n', '\n'))
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._dirty = False
        self.save_button.configure(state='normal')
        self.status.set(f'{self.path} · {data["bytes"]} bytes · Loaded')

    def modified(self, _event=None):
        if self.text.edit_modified():
            self._dirty = True
            self.text.edit_modified(False)
            self.status.set('Unsaved changes · Ctrl+S saves with a backup')

    def save(self):
        if self._digest is None or self._busy:
            return
        # Consume the pending edit notification before reporting a save result.
        # Otherwise a synchronous submission failure is overwritten by it.
        self.modified()
        content = self.text.get('1.0', 'end-1c').replace('\n', self._newline)
        self.text.configure(state='disabled')
        self.status.set('Saving…')
        self._run('save_edited_file', {'path': str(self.path), 'content': content,
                                     'expected_sha256': self._digest}, self._saved)

    def _saved(self, result):
        self.text.configure(state='normal')
        self.save_button.configure(state='normal')
        if not result.success:
            self.status.set(result.error)
            return
        self._digest = result.output['sha256']
        self._dirty = False
        self.status.set(f'Saved · Backup: {result.output["backup"]}')

    def revise(self):
        self.gui.use_example(f'Read and revise this file: "{self.path}". Make these changes: ')
        self.gui.root.lift()

    def close(self):
        if self._busy:
            self.status.set('Wait for the current read/save to finish before closing.')
            return
        if self._dirty and not messagebox.askyesno('Unsaved changes', 'Close without saving your edits?', parent=self):
            return
        self._closed = True
        self.destroy()


class NativeFileEditor(tk.Toplevel):
    """Exact document text edits and typed XLSX cell edits, without a model call."""

    _run = FileEditor._run

    def __init__(self, gui, path: Path):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.path = gui, path
        self._closed, self._busy, self._generation = False, False, 0
        self._digest, self._last_backup = None, None
        self._spreadsheet = path.suffix.lower() == '.xlsx'
        self._controls = []
        self.title(f'Edit {path.name}')
        self.transient(gui.root)
        self.configure(bg=gui.BLACK, padx=12, pady=10)
        bar = tk.Frame(self, bg=gui.BLACK)
        bar.pack(fill='x')
        self.save_button = ttk.Button(bar, text='Apply edit', command=self.save, state='disabled', style='HayatAccent.TButton')
        self.save_button.pack(side='left')
        ttk.Button(bar, text='Reload', command=self.load, style='Hayat.TButton').pack(side='left', padx=7)
        ttk.Button(bar, text='Revise with HAYAT', command=lambda: gui.use_example(f'Read and revise "{path}". Requested changes: '), style='Hayat.TButton').pack(side='left')
        self.status = StringVar(self, 'Reading native file…')
        tk.Label(self, textvariable=self.status, bg=gui.BLACK, fg=gui.MUTED, wraplength=750, anchor='w', justify='left').pack(fill='x', pady=8)
        from app.gui_theme import DarkScrolledText
        self.preview = DarkScrolledText(self, wrap='word', height=13, bg=gui.PANEL_2, fg=gui.WHITE, state='disabled', font=('Consolas', 10))
        self.preview.pack(fill='both', expand=True)
        fields = tk.Frame(self, bg=gui.BLACK)
        fields.pack(fill='x', pady=9)
        fields.columnconfigure(1, weight=1)
        self.values = {}
        if self._spreadsheet:
            entries = [('sheet', 'Sheet', ''), ('range', 'Preview range', 'A1:F30'), ('cell', 'Cell to edit', 'A1'), ('value', 'New value', ''), ('type', 'Value type', 'Text')]
        else:
            entries = [('old_text', 'Exact text to find', ''), ('new_text', 'Replacement text', ''), ('matches', 'Expected matches', '1')]
        for row, (key, label, initial) in enumerate(entries):
            tk.Label(fields, text=label, bg=gui.BLACK, fg=gui.WHITE).grid(row=row, column=0, sticky='nw', padx=(0, 12), pady=4)
            value = self.values[key] = StringVar(self, initial)
            if key in {'sheet', 'type'}:
                widget = ttk.Combobox(fields, textvariable=value, values=['Text', 'Number', 'Formula', 'Boolean', 'Empty'] if key == 'type' else [], state='readonly')
                if key == 'sheet':
                    self.sheet_picker = widget
                    widget.bind('<<ComboboxSelected>>', lambda _e: self.load())
            elif key in {'old_text', 'new_text'}:
                widget = tk.Text(fields, height=2, wrap='word', bg=gui.PANEL_2, fg=gui.WHITE, insertbackground=gui.GOLD, font=('Segoe UI', 10))
                setattr(self, key, widget)
            else:
                widget = ttk.Entry(fields, textvariable=value)
            widget.grid(row=row, column=1, sticky='ew', pady=4)
            self._controls.append(widget)
        tk.Label(self, text='Saves check the file version and keep a backup. Native document edits preserve unrelated formatting.', bg=gui.BLACK, fg=gui.MUTED, wraplength=750, anchor='w').pack(fill='x')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _e: self.close())
        place_over(self, gui.root, (860, 760))
        self.deiconify()
        self.load()

    def _deliver(self, generation, callback, result):
        if self._closed or not self.winfo_exists() or generation != self._generation:
            return
        self._busy = False
        for widget in self._controls:
            widget.configure(state='readonly' if isinstance(widget, ttk.Combobox) else 'normal')
        callback(result)
        self.save_button.configure(state='normal' if self._digest and not self._busy else 'disabled')

    def _execute(self, name, arguments, callback):
        for widget in self._controls:
            widget.configure(state='disabled')
        self._run(name, arguments, callback)

    def load(self):
        if self._busy:
            return
        self._digest = None
        arguments = {'path': str(self.path)}
        name = 'read_document_for_edit'
        if self._spreadsheet:
            name = 'read_spreadsheet_for_edit'
            arguments.update(sheet_name=self.values['sheet'].get() or None, cell_range=self.values['range'].get())
        self.status.set('Reading native file…')
        self._execute(name, arguments, self._loaded)

    def _loaded(self, result):
        if not result.success:
            self.status.set(result.error)
            return
        data = result.output
        self._digest = data['sha256']
        if self._spreadsheet:
            self.sheet_picker.configure(values=data['sheets'])
            self.values['sheet'].set(data['sheet'])
            text = f"{data['sheet']} · {data['range']}\n\n" + '\n'.join(f"{item['cell']}  [{item['type']}]  {item['value']}" for item in data['cells'])
        else:
            text = '\n\n'.join(f'{index + 1}. {value}' for index, value in enumerate(data['paragraphs']))
            if data['truncated']:
                text += '\n\nPreview is limited. Exact matching still checks all supported document text.'
        self.preview.configure(state='normal')
        self.preview.delete('1.0', 'end')
        self.preview.insert('1.0', text)
        self.preview.configure(state='disabled')
        self.status.set(f'Loaded {self.path.name}' + (f' · Backup: {self._last_backup}' if self._last_backup else ''))

    def save(self):
        if self._busy or not self._digest:
            return
        arguments = {'path': str(self.path), 'expected_sha256': self._digest}
        try:
            if self._spreadsheet:
                import math
                kind, value = self.values['type'].get(), self.values['value'].get()
                item = {'sheet': self.values['sheet'].get(), 'cell': self.values['cell'].get().strip()}
                if kind == 'Number':
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError('Use a finite number.')
                elif kind == 'Boolean':
                    if value.casefold() not in {'true', 'false'}:
                        raise ValueError('Boolean values must be true or false.')
                    value = value.casefold() == 'true'
                elif kind == 'Empty':
                    value = None
                elif kind == 'Formula' and not value.startswith('='):
                    raise ValueError('A formula must start with =.')
                elif kind == 'Text':
                    item['value_type'] = 'text'
                item['value'] = value
                arguments['cells'] = [item]
                name = 'edit_spreadsheet_cells'
            else:
                arguments.update(old_text=self.old_text.get('1.0', 'end-1c'), new_text=self.new_text.get('1.0', 'end-1c'), expected_matches=int(self.values['matches'].get()))
                name = 'edit_document_text'
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.status.set('Applying edit…')
        self._execute(name, arguments, self._saved)

    def _saved(self, result):
        if not result.success:
            self.status.set(result.error)
            return
        self._last_backup = result.output['backup']
        self.load()

    def close(self):
        if self._busy:
            self.status.set('Wait for the current read/save to finish before closing.')
            return
        self._closed = True
        self.destroy()


def open_editor(gui, path: Path):
    return NativeFileEditor(gui, path) if path.suffix.lower() in {'.docx', '.pptx', '.xlsx'} else FileEditor(gui, path)


class PdfViewer(tk.Toplevel):
    def __init__(self, gui, path: Path):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.path = gui, path
        self._page, self._count, self._generation = 0, 0, 0
        self._pdf_photos = []
        self.title(f'{path.name} — PDF preview')
        self.configure(bg=gui.BLACK)
        self.transient(gui.root)
        bar = tk.Frame(self, bg=gui.PANEL)
        bar.pack(fill='x')
        self.previous = ttk.Button(bar, text='Previous', command=lambda: self.render(self._page - 1), style='Hayat.TButton', state='disabled')
        self.previous.pack(side='left', padx=8, pady=8)
        self.next = ttk.Button(bar, text='Next', command=lambda: self.render(self._page + 1), style='Hayat.TButton', state='disabled')
        self.next.pack(side='left', padx=4)
        self.status = StringVar(self, value='Loading PDF…')
        tk.Label(bar, textvariable=self.status, bg=gui.PANEL, fg=gui.WHITE).pack(side='left', padx=8)
        ttk.Button(bar, text='Close', command=self.destroy, style='Hayat.TButton').pack(side='right', padx=8)
        ttk.Button(bar, text='Open externally', command=lambda: gui._open_file(path), style='Hayat.TButton').pack(side='right')
        self.canvas = tk.Canvas(self, bg=gui.BLACK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, command=self.canvas.yview, style='Hayat.Vertical.TScrollbar')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-4>', self.wheel)
        self.canvas.bind('<Button-5>', self.wheel)
        self.bind('<Escape>', lambda _e: self.destroy())
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        place_over(self, gui.root, (780, 820))
        self.deiconify()
        self.render(0)

    def destroy(self):
        self._generation += 1
        self._pdf_photos.clear()
        super().destroy()

    def render(self, page):
        if page < 0 or (self._count and page >= self._count):
            return
        self._generation += 1
        generation = self._generation
        self._page = page
        self.status.set(f'Loading page {page + 1}…')
        self.previous.configure(state='disabled')
        self.next.configure(state='disabled')
        def worker():
            try:
                from app.pdf_preview_worker import render_pdf
                rendered = render_pdf(self.path, page=page)
                result = (rendered['image'], rendered['pages'], '')
            except Exception as exc:
                result = (None, 0, f'Preview unavailable ({type(exc).__name__}). Use Open externally.')
            self.gui._post(self._show, generation, result)
        self.gui._preview_workers.submit(worker)

    def _show(self, generation, result):
        if not self.winfo_exists() or generation != self._generation:
            return
        image, self._count, error = result
        if image is None:
            self.status.set(error)
            return
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(image, master=self)
        self._pdf_photos[:] = [photo]
        self.canvas.delete('all')
        self.canvas.create_image(8, 8, image=photo, anchor='nw')
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        self.canvas.yview_moveto(0)
        self.status.set(f'Page {self._page + 1} of {self._count}')
        self.previous.configure(state='normal' if self._page else 'disabled')
        self.next.configure(state='normal' if self._page + 1 < self._count else 'disabled')

    def wheel(self, event):
        delta = getattr(event, 'delta', 0)
        step = (-1 if delta > 0 else 1) if delta else (1 if getattr(event, 'num', 0) == 5 else -1)
        self.canvas.yview_scroll(step, 'units')
        return 'break'
