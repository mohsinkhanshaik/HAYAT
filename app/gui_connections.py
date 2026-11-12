"""Read-only explorer of registered tools and declared subsystem contracts."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.gui_layout import ResponsiveSplit
from app.gui_output import DisplayVar, SafeTreeview
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_presentation import safe_text


def check_label(check):
    """A recorded pass is a dated observation, never a promise of readiness."""
    if check.get('passed') is True:
        return 'Recorded pass'
    if check.get('passed') is False:
        return 'Needs attention'
    return 'Not checked'


class ConnectionsView(tk.Frame):
    def __init__(self, parent, gui, on_tool):
        p = type(gui)
        super().__init__(parent, bg=p.BLACK)
        self.gui, self.p, self.on_tool = gui, p, on_tool
        self.snapshot = None
        self.selected = None
        self.on_snapshot = None
        self.nodes, self.tools = {}, []
        self._results, self._busy, self._poll_id = queue.Queue(), False, None
        self._closed = False
        self.bind('<Destroy>', self._destroyed, add='+')
        bar = tk.Frame(self, bg=p.BLACK)
        bar.pack(fill='x', pady=(8, 6))
        self.refresh_button = ttk.Button(bar, text='Refresh inventory', style='Hayat.TButton', command=self.refresh)
        self.refresh_button.pack(side='right', padx=(8, 0))
        self.summary = DisplayVar(self, 'Open this tab to read the local inventory.')
        tk.Label(bar, textvariable=self.summary, bg=p.BLACK, fg=p.GOLD_SOFT,
                 font=('Segoe UI Semibold', 10), anchor='w', justify='left', wraplength=650).pack(fill='x', expand=True)
        self.stamp = DisplayVar(self, 'Inventory and saved checks. Selecting a tool does not run it.')
        self.stamp_label = tk.Label(self, textvariable=self.stamp, bg=p.BLACK, fg=p.MUTED,
                                   font=('Segoe UI', 9), anchor='w', justify='left', wraplength=960)
        self.stamp_label.pack(fill='x', pady=(0, 8))
        split = self.split = ResponsiveSplit(self, p, breakpoint=700)
        split.pack(fill='both', expand=True)
        listing = tk.Frame(split, bg=p.BLACK)
        split.add(listing)
        search = tk.Frame(listing, bg=p.BLACK)
        search.pack(fill='x', pady=(0, 6))
        self.query = StringVar(self)
        ttk.Label(search, text='Find a subsystem').pack(side='left')
        self.entry = ttk.Entry(search, textvariable=self.query, width=14)
        self.entry.pack(side='left', fill='x', expand=True, padx=(8, 0))
        self.tree = self._table(listing, ('state', 'tools'), ('Saved check', 'Tools'), (115, 55))
        self.tree.heading('#0', text='Subsystem')
        self.tree.column('#0', width=155, minwidth=120)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show())
        self.query.trace_add('write', lambda *_: self.filter())
        self.entry.bind('<Down>', lambda _event: (self.tree.focus_set(), 'break')[1])
        self.entry.bind('<Escape>', lambda _event: (self.query.set(''), 'break')[1])
        detail = tk.Frame(split, bg=p.PANEL, padx=12, pady=10)
        split.add(detail)
        self.title = DisplayVar(self, 'Select a subsystem')
        tk.Label(detail, textvariable=self.title, bg=p.PANEL, fg=p.WHITE,
                 font=('Segoe UI Semibold', 14), anchor='w').pack(fill='x')
        self.tabs = ttk.Notebook(detail)
        self.tabs.pack(fill='both', expand=True, pady=(8, 0))
        links = tk.Frame(self.tabs, bg=p.PANEL)
        tool_page = tk.Frame(self.tabs, bg=p.PANEL)
        evidence = tk.Frame(self.tabs, bg=p.PANEL)
        self.tabs.add(links, text='Connections')
        self.tabs.add(tool_page, text='Linked tools')
        self.tabs.add(evidence, text='Evidence')
        self.canvas = tk.Canvas(links, bg=p.PANEL, highlightthickness=0, height=165)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _event: self.draw())
        self.canvas.bind('<Button-1>', self._pick)
        self.contracts = DarkScrolledText(links, height=5, wrap='word', bg=p.PANEL, fg=p.WHITE,
                                         font=('Segoe UI', 10))
        self.contracts.pack(fill='both', expand=True, pady=(5, 0))
        self.tool_tree = self._table(tool_page, ('scope',), ('Permission',), (140,))
        self.tool_tree.heading('#0', text='Registered tool')
        self.tool_tree.column('#0', width=230, minwidth=120)
        self.inspect_button = ttk.Button(tool_page, text='Inspect selected tool', command=self.inspect_tool,
                                         style='HayatAccent.TButton', state='disabled')
        self.inspect_button.pack(fill='x', pady=(8, 0))
        self.tool_tree.bind('<<TreeviewSelect>>', lambda _event: self.inspect_button.configure(
            state='normal' if self.tool_tree.selection() else 'disabled'))
        self.tool_tree.bind('<Double-1>', lambda _event: self.inspect_tool())
        self.tool_tree.bind('<Return>', lambda _event: (self.inspect_tool(), 'break')[1])
        self.evidence = DarkScrolledText(evidence, height=8, wrap='word', bg=p.PANEL, fg=p.WHITE,
                                        font=('Segoe UI', 10))
        self.evidence.pack(fill='both', expand=True)
        self.brain_button = ttk.Button(detail, text='Explore in 3D brain map', command=self.open_brain,
                                       style='Hayat.TButton')
        self.brain_button.pack(fill='x', pady=(10, 0), side='bottom', before=self.tabs)
        self._boxes = []
        self.bind('<Configure>', lambda event: self.stamp_label.configure(wraplength=max(240, event.width - 20)), add='+')

    def _table(self, parent, columns, headings, widths):
        frame = tk.Frame(parent, bg=self.p.BLACK)
        frame.pack(fill='both', expand=True)
        tree = SafeTreeview(frame, columns=columns, selectmode='browse', height=6)
        for name, heading, width in zip(columns, headings, widths):
            tree.heading(name, text=heading)
            tree.column(name, width=width, minwidth=45, stretch=False)
        y = ttk.Scrollbar(frame, command=tree.yview)
        x = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        return tree

    def refresh(self):
        if self._closed or self._busy:
            return
        self._busy = True
        self.refresh_button.configure(state='disabled')
        self.stamp.set('Reading local registrations and saved checks…')
        runtime, results = self.gui.runtime, self._results
        def work():
            try:
                from app.system_insight import build_snapshot
                results.put((build_snapshot(runtime), None))
            except Exception as exc:
                results.put((None, f'{type(exc).__name__}: {exc}'))
        threading.Thread(target=work, name='hayat-connections', daemon=True).start()
        self._poll_id = self.after(50, self._poll)

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            snapshot, error = self._results.get_nowait()
        except queue.Empty:
            self._poll_id = self.after(50, self._poll)
            return
        self._busy = False
        self.refresh_button.configure(state='normal')
        if error:
            self.stamp.set('Inventory unavailable: ' + error)
        else:
            self.set_snapshot(snapshot)

    def set_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.nodes = {node['node_id']: node for node in snapshot.get('nodes', [])}
        self.tools = snapshot.get('tools', [])
        summary = snapshot.get('summary', {})
        self.summary.set(f"{summary.get('registered_tools', len(self.tools))} tools  ·  "
                         f"{summary.get('mapped_tools', 0)} catalogued  ·  {len(self.nodes)} subsystems  ·  "
                         f"{len(snapshot.get('edges', []))} contracts")
        health = snapshot.get('health', {})
        stamp = health.get('checked_at')
        self.stamp.set(('Saved check: ' + str(stamp) if stamp else 'No system check recorded.') +
                       '  ·  Inventory is not a live engine test.')
        self.filter()
        if callable(self.on_snapshot):
            self.on_snapshot(snapshot)

    def filter(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        query = self.query.get().casefold().split()
        checks = (self.snapshot or {}).get('health', {}).get('nodes', {})
        for key, node in self.nodes.items():
            tools = [tool for tool in self.tools if key in tool.get('mesh_nodes', [])]
            words = ' '.join([node.get('label', key), node.get('responsibility', ''), node.get('layer', ''),
                              *(tool['name'] for tool in tools)]).casefold()
            if not all(word in words for word in query):
                continue
            self.tree.insert('', 'end', iid=key, text=node.get('label', key),
                             values=(check_label(checks.get(key, {})), len(tools)))
        rows = self.tree.get_children()
        key = selected[0] if selected and selected[0] in rows else (rows[0] if rows else None)
        if key:
            self.tree.selection_set(key)
            self.tree.focus(key)
        self.show()

    @staticmethod
    def _write(widget, text):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', safe_text(text))
        widget.configure(state='disabled')

    def show(self):
        selected = self.tree.selection()
        self.selected = selected[0] if selected else None
        node = self.nodes.get(self.selected, {})
        self.title.set(node.get('label', 'No matching subsystem'))
        self.tool_tree.delete(*self.tool_tree.get_children())
        self.inspect_button.configure(state='disabled')
        tools = [tool for tool in self.tools if self.selected in tool.get('mesh_nodes', [])]
        for tool in tools:
            self.tool_tree.insert('', 'end', iid=tool['name'], text=tool['name'], values=(tool.get('permission', ''),))
        contracts = []
        for edge in (self.snapshot or {}).get('edges', []):
            if edge['source'] == self.selected or edge['target'] == self.selected:
                label = lambda key: self.nodes.get(key, {}).get('label', key)
                contracts.append(f"{label(edge['source'])} → {label(edge['target'])}\n{edge['contract']}")
        self._write(self.contracts, '\n\n'.join(contracts) or 'No declared connections in this selection.')
        health = (self.snapshot or {}).get('health', {})
        check = health.get('nodes', {}).get(self.selected, {})
        lines = [node.get('responsibility', 'Try a shorter search.'), '', check_label(check),
                 'Checked: ' + str(health.get('checked_at') or 'not recorded'),
                 str(check.get('detail') or 'No observation has been recorded for this subsystem.'), '',
                 f'{len(tools)} registered tools mapped here by the capability catalog.',
                 'Declared connections describe software contracts; they are not measured neural activity.']
        if check.get('evidence'):
            from app.plain_output import describe
            lines += ['', 'Saved observations', describe(check['evidence'])]
        ids = {tool.get('component_id') for tool in tools}
        verified = [link for link in (self.snapshot or {}).get('connections', [])
                    if link.get('a') in ids or link.get('b') in ids]
        if verified:
            lines += ['', 'Verified object connections']
            lines += [f"{link['a']} → {link['b']}\n{link['basis']}" for link in verified]
        if (self.snapshot or {}).get('gaps'):
            lines += ['', 'Inventory gaps'] + [str(gap.get('detail', gap)) for gap in self.snapshot['gaps']]
        self._write(self.evidence, '\n'.join(lines))
        self.draw()

    def draw(self):
        canvas, p = self.canvas, self.p
        canvas.delete('all')
        self._boxes = []
        if not getattr(self, 'selected', None):
            return
        width, height = max(240, canvas.winfo_width()), max(150, canvas.winfo_height())
        edges = (self.snapshot or {}).get('edges', [])
        incoming = sorted({edge['source'] for edge in edges if edge['target'] == self.selected})
        outgoing = sorted({edge['target'] for edge in edges if edge['source'] == self.selected})
        positions = [(self.selected, width / 2, height / 2, True)]
        for side, keys in ((.16, incoming), (.84, outgoing)):
            for i, key in enumerate(keys):
                y = 25 + (height - 42) * (i + .5) / max(1, len(keys))
                x = width * side
                start, end = ((x, y), (width/2, height/2)) if side < .5 else ((width/2, height/2), (x, y))
                dx = end[0] - start[0]
                canvas.create_line(start[0] + (8 if dx > 0 else -8), start[1],
                                   end[0] - (8 if dx > 0 else -8), end[1], fill=p.LINE, arrow='last')
                positions.append((key, x, y, False))
        for key, x, y, active in positions:
            color = p.GOLD if active else p.ROYAL_SOFT
            canvas.create_oval(x-5, y-5, x+5, y+5, fill=color, outline=p.PANEL)
            text = self.nodes.get(key, {}).get('label', key)
            canvas.create_text(x, y+10, text=text, fill=color, font=('Segoe UI', 8), anchor='n', width=max(65, width*.28))
            self._boxes.append((x-55, y-8, x+55, y+28, key))
        canvas.create_text(12, 7, text='INCOMING', anchor='nw', fill=p.MUTED, font=('Segoe UI', 8))
        canvas.create_text(width-12, 7, text='OUTGOING', anchor='ne', fill=p.MUTED, font=('Segoe UI', 8))

    def _pick(self, event):
        for x1, y1, x2, y2, key in reversed(self._boxes):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if not self.tree.exists(key):
                    self.query.set('')
                self.tree.selection_set(key)
                self.tree.focus(key)
                self.tree.see(key)
                self.show()
                return

    def inspect_tool(self):
        selected = self.tool_tree.selection()
        if selected:
            self.on_tool(selected[0])

    def open_brain(self):
        self.gui.show_view('operator')
        select = getattr(self.gui, '_select_brain_node', None)
        if self.selected and callable(select):
            select(self.selected)

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
