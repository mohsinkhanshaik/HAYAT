"""Editable simple schema fields, backed by the complete JSON argument draft."""
from __future__ import annotations

import json
import math
import tkinter as tk
from tkinter import ttk

from app.gui_help import Tooltip
from app.gui_motion import attach
from app.gui_resources import StringVar


class GuidedInputs(ttk.Frame):
    def __init__(self, parent, palette, changed):
        super().__init__(parent)
        self.changed, self.palette = changed, palette
        self.fields, self._syncing = {}, False
        self.canvas = tk.Canvas(self, height=105, bg=palette.PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.inner = ttk.Frame(self.canvas, padding=6)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.inner.columnconfigure(1, weight=1)
        self.inner.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        attach(self.canvas)

    def build(self, schema, values):
        self._syncing = True
        for child in self.inner.winfo_children():
            child.destroy()
        self.fields = {}
        required = schema.get('required', [])
        self.required = set(required)
        for name, prop in schema.get('properties', {}).items():
            if not isinstance(prop, dict):
                continue
            kind = prop.get('type', 'string' if 'enum' in prop else '')
            if isinstance(kind, list):
                kind = next((item for item in kind if item != 'null'), '')
            if kind not in ('string', 'integer', 'number', 'boolean'):
                continue  # nested/conditional structures remain fully editable in JSON
            row = len(self.fields)
            owner = ttk.Frame(self.inner)
            owner.grid(row=row, column=0, columnspan=2, sticky='ew')
            owner.columnconfigure(1, weight=1)
            label = ttk.Label(owner, text=name.replace('_', ' ') + (' *' if name in required else ''))
            label.grid(row=0, column=0, sticky='w', padx=(0, 8), pady=3)
            variable = StringVar(owner)
            choices = prop.get('enum')
            if kind == 'boolean':
                choices = [True, False]
            encoded = {json.dumps(item): item for item in choices or []}
            if choices is not None:
                control = ttk.Combobox(owner, textvariable=variable, values=['', *encoded], state='readonly')
            else:
                control = ttk.Entry(owner, textvariable=variable)
            control.grid(row=0, column=1, sticky='ew', pady=3)
            Tooltip(control, prop.get('description') or f'{kind} value' + ('; required' if name in required else '; optional'), self.palette)
            self.fields[name] = (variable, kind, encoded)
            variable.trace_add('write', lambda *_args: self._edited())
        if not self.fields:
            ttk.Label(self.inner, text='No simple fields. Use JSON arguments for any nested or conditional inputs.',
                      wraplength=380).grid(row=0, column=0, columnspan=2, sticky='w')
        else:
            ttk.Label(self.inner, text='* Required · Empty optional fields are omitted · More options in JSON arguments',
                      wraplength=380).grid(row=len(self.fields), column=0, columnspan=2, sticky='w', pady=4)
        self.load(values)
        self._syncing = False
        self.canvas.yview_moveto(0)

    def load(self, values):
        previous, self._syncing = self._syncing, True
        try:
            for name, (variable, kind, encoded) in self.fields.items():
                value = values.get(name)
                variable.set('' if value is None else json.dumps(value) if encoded or kind == 'boolean' else str(value))
        finally:
            self._syncing = previous

    def load_raw(self, values):
        previous, self._syncing = self._syncing, True
        try:
            for name, raw in values.items():
                if name in self.fields:
                    self.fields[name][0].set(raw)
        finally:
            self._syncing = previous

    def values(self, base):
        result = dict(base)
        for name, (variable, kind, encoded) in self.fields.items():
            raw = variable.get()
            if raw == '':
                if name in result and result[name] is None:
                    continue
                if name in self.required and kind == 'string' and not encoded:
                    result[name] = ''
                else:
                    result.pop(name, None)
                continue
            if encoded:
                result[name] = encoded[raw]
            elif kind in ('integer', 'number'):
                try:
                    result[name] = int(raw) if kind == 'integer' else float(raw)
                    if kind == 'number' and not math.isfinite(result[name]):
                        raise ValueError('non-finite number')
                except ValueError:
                    raise ValueError(f'{name}: enter a valid {kind}.') from None
            else:
                result[name] = raw
        return result

    def _edited(self):
        if not self._syncing:
            self.changed()
