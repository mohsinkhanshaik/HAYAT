"""Search the real feature and tool inventory, then open or run a declared action."""
from dataclasses import dataclass
import json
import threading
import uuid
import tkinter as tk
from tkinter import ttk

from app import capabilities
from app.gui_resources import StringVar
from app.gui_output import DisplayVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_workspace import WorkspaceCommand
from app.safety.sensitive import redact
from app.gui_fonts import DISPLAY
from app.gui_layout import ResponsiveSplit


@dataclass
class Feature:
    key: str
    title: str
    category: str
    description: str
    callback: object = None
    capability: object = None
    tool: object = None
    search_terms: str = ''

    def matches(self, query):
        extra = ' '.join(self.capability.keywords + self.capability.examples) if self.capability else ''
        text = f'{self.title} {self.description} {self.category} {self.key} {extra} {self.search_terms}'.casefold()
        # People type "model discovery" while the registry uses model_discovery.
        return all(word in text or word in text.replace('_', ' ').replace('-', ' ')
                   for word in query.casefold().split())


def feature_inventory(gui):
    rows = []
    for area in capabilities.CATALOG:
        rows.append(Feature('area:' + area.key, area.title, 'Features', area.summary,
                            lambda value=area: open_area(gui, value), area))
    for key, _icon, title, description in gui.VIEWS:
        rows.append(Feature('view:' + key, title, 'Workspaces', description,
                            lambda name=key: gui.show_view(name)))
    for title, method, group in gui.CONTROLS:
        plain = {'God mode': 'Extended computer access', 'Operator': 'Inspect Hayat components',
                 'Look': 'Describe camera image', 'Screen': 'Read current screen', 'Clipboard': 'Read clipboard',
                 'View': 'Open camera preview', 'Release RAM': 'Unload idle AI models',
                 'Theme': 'Appearance and text size', 'Self Check': 'Check Hayat health'}
        rows.append(Feature('control:' + method, plain.get(title, title), 'Controls', f'{title} · {group}', getattr(gui, method)))
    # Studios that are absent from the general control list remain discoverable.
    for title, method in (('Global intelligence', '_open_intelligence'), ('Knowledge Library', '_open_knowledge'),
                          ('Learning atlas', '_open_learning_atlas'),
                          ('Mind: topics, growth, scorecard', '_open_mind'),
                          ('Research lab: robustness, security, models', '_open_research_lab'),
                          ('Scientific Workbench: CSV statistics and relationships', '_open_scientific_workbench'),
                          ('Connected AI: OpenAI, Anthropic, Gemini and research tools', '_open_connected_ai'),
                          ('Digital products', '_open_products'), ('Voice settings', '_open_control_studio'),
                          ('All features', '_open_features'), ('Activity and transparency', '_open_activity')):
        callback = getattr(gui, method, None)
        if callable(callback):
            rows.append(Feature('studio:' + method, title, 'Studios', 'Open ' + title, callback))
    names = gui.runtime.registry.names()
    grouped, _ = capabilities.classify(names)
    for name in names:
        tool = gui.runtime.registry.get(name)
        areas = [area for area in capabilities.CATALOG if name in grouped.get(area.key, [])]
        terms = ' '.join(area.title + ' ' + ' '.join(area.keywords) for area in areas)
        rows.append(Feature('tool:' + name, name.replace('_', ' ').capitalize(), 'Tools', tool.description,
                            tool=tool, search_terms=terms))
    return rows


def open_area(gui, area):
    target = area.opens
    if target.startswith('view:'):
        gui.show_view(target[5:])
    elif target.startswith('studio:'):
        gui._open_control_studio(target[7:])
    elif target:
        getattr(gui, target)()
    elif area.examples:
        gui.use_example(area.examples[0])
        gui.show_view('chat')


def inventory_commands(gui):
    return [WorkspaceCommand(row.title, row.callback if row.callback else
            lambda name=row.tool.name: gui._open_features(name), row.description + ' ' + row.key)
            for row in feature_inventory(gui)]


def run_declared_tool(runtime, name, arguments):
    """A manual operation owns its budget and keeps the normal authority gates."""
    from app.execution import execution_scope
    from app.safety.authority import Principal
    # Use the actual shared bus: a cloned registry loses the latched emergency
    # stop and location shield, and can drift as future guards are introduced.
    registry = runtime.registry
    tool = registry.get(name)
    task_id = 'gui-tool-' + uuid.uuid4().hex
    def publish(kind, **extra):
        if registry.events:
            from app.logging import log_swallowed
            import logging
            try:
                registry.events.publish(kind, 'gui-tools', {'tool': name, 'permission_scope': tool.permission,
                    'principal': Principal.LOCAL_OWNER.value, **extra}, task_id=task_id)
            except Exception:
                log_swallowed(logging.getLogger(__name__), 'publishing a GUI tool request')
    with execution_scope(principal=Principal.LOCAL_OWNER.value, explicitly_requested=True,
                         task_id=task_id), registry.budget_scope():
        publish('GUI_TOOL_REQUESTED', arguments=arguments)
        result = registry.execute(name, arguments)
        if not result.success and result.permission:
            publish('GUI_TOOL_REFUSED', error=result.error)
        return result


class FeaturesWindow(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.inventory = gui, feature_inventory(gui)
        self._grouped, _ = capabilities.classify(gui.runtime.registry.names())
        self.title('HAYAT · Tools & connections')
        self.configure(bg=gui.BLACK)
        self.minsize(760, 600)
        self.bind('<Escape>', lambda _event: (self.destroy(), 'break')[1])
        header = tk.Frame(self, bg=gui.BLACK, padx=16, pady=12)
        header.pack(fill='x')
        ttk.Button(header, text='Activity', command=gui._open_activity, style='Hayat.TButton').pack(side='right')
        tk.Label(header, text='Tools & connections', bg=gui.BLACK, fg=gui.WHITE,
                 font=(DISPLAY, 18), anchor='w').pack(fill='x')
        tk.Label(header, text='Find a capability. Inspect its tools. Follow the connections.', bg=gui.BLACK,
                 fg=gui.MUTED, font=('Segoe UI', 10), anchor='w').pack(fill='x', pady=(3, 0))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=16, pady=(0, 12))
        self.catalog_page = tk.Frame(self.tabs, bg=gui.BLACK)
        self.tabs.add(self.catalog_page, text='Explore tools')
        from app.gui_connections import ConnectionsView
        self.connections = ConnectionsView(self.tabs, gui, self.select_tool)
        self.tabs.add(self.connections, text='System connections')
        self.tabs.bind('<<NotebookTabChanged>>', self._tab_changed)
        filters = tk.Frame(self.catalog_page, bg=gui.BLACK)
        filters.pack(fill='x')
        ttk.Label(filters, text='Find a feature or tool').pack(side='left')
        self.query = StringVar(self)
        self.entry = ttk.Entry(filters, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True, padx=8)
        self.category = StringVar(self, 'Everything')
        ttk.Combobox(filters, textvariable=self.category, values=['Everything', 'Features', 'Workspaces', 'Controls', 'Studios', 'Tools'],
                     state='readonly', width=15).pack(side='right')
        self.count = DisplayVar(self)
        tk.Label(self.catalog_page, textvariable=self.count, bg=gui.BLACK, fg=gui.MUTED,
                 anchor='w').pack(fill='x', pady=8)
        # At the supported 760px minimum two columns preserve working room for
        # the inspector. Stacking both would leave its actions below the fold.
        split = self.split = ResponsiveSplit(self.catalog_page, type(gui), breakpoint=700)
        split.pack(fill='both', expand=True)
        listing = tk.Frame(split, bg=gui.BLACK)
        split.add(listing, weight=2)
        self.tree = ttk.Treeview(listing, columns=('category',), selectmode='browse')
        self.tree.heading('#0', text='Feature / tool')
        self.tree.heading('category', text='Area')
        self.tree.column('#0', width=270)
        self.tree.column('category', width=95, stretch=False)
        scrollbar = ttk.Scrollbar(listing, command=self.tree.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show())
        self.tree.bind('<Double-1>', lambda _event: self.open())
        self.tree.bind('<Return>', lambda _event: (self.open(), 'break')[1])
        detail = tk.Frame(split, bg=gui.PANEL, padx=14, pady=12)
        split.add(detail, weight=3)
        self.title_var = DisplayVar(self)
        self.detail_title = tk.Label(detail, textvariable=self.title_var, bg=gui.PANEL, fg=gui.WHITE,
                 wraplength=490, justify='left', anchor='w', font=('Segoe UI Semibold', 14))
        self.detail_title.pack(fill='x')
        detail.bind('<Configure>', lambda event: self.detail_title.configure(wraplength=max(160, event.width-30)), add='+')
        self.detail_tabs = ttk.Notebook(detail)
        self.detail_tabs.pack(fill='both', expand=True, pady=(8, 0))
        overview = tk.Frame(self.detail_tabs, bg=gui.PANEL)
        linked = tk.Frame(self.detail_tabs, bg=gui.PANEL)
        self.run_page = tk.Frame(self.detail_tabs, bg=gui.PANEL)
        self.evidence_page = tk.Frame(self.detail_tabs, bg=gui.PANEL)
        self.detail_tabs.add(overview, text='Overview')
        self.detail_tabs.add(linked, text='Links')
        self.detail_tabs.add(self.evidence_page, text='Evidence')
        self.detail_tabs.add(self.run_page, text='Run tool', state='disabled')
        ttk.Button(self.evidence_page, text='Refresh local evidence', command=self.connections.refresh).pack(
            fill='x', side='bottom', pady=(6, 0))
        self.evidence_text = DarkScrolledText(self.evidence_page, height=6, wrap='word', bg=gui.PANEL,
                                             fg=gui.WHITE, font=('Segoe UI', 10))
        self.evidence_text.pack(fill='both', expand=True)
        self.detail_tabs.bind('<<NotebookTabChanged>>', self._detail_changed)
        self.connections.on_snapshot = self._show_evidence
        self.description = DarkScrolledText(overview, height=6, wrap='word', bg=gui.PANEL,
                                           fg=gui.WHITE, font=('Segoe UI', 10))
        self.open_button = ttk.Button(overview, text='Open feature', command=self.open, style='HayatAccent.TButton')
        self.open_button.pack(fill='x', pady=4, side='bottom')
        self.examples = ttk.Button(overview, text='Put example in chat', command=self.example)
        self.examples.pack(fill='x', pady=4, side='bottom')
        self.description.pack(fill='both', expand=True, pady=8)
        self.related = self.connections._table(linked, ('kind',), ('Type',), (90,))
        self.related.heading('#0', text='Related capability / tool')
        self.related.column('#0', width=260, minwidth=120)
        self.related.bind('<Double-1>', lambda _event: self.open_related())
        self.related.bind('<Return>', lambda _event: (self.open_related(), 'break')[1])
        ttk.Button(linked, text='Inspect selection', command=self.open_related).pack(fill='x', pady=(8, 0))
        self.arguments_label = ttk.Label(self.run_page, text='Tool inputs (JSON)')
        self.arguments = tk.Text(self.run_page, height=5, wrap='word', bg=gui.PANEL_2, fg=gui.WHITE,
                                 insertbackground=gui.WHITE, font=('Cascadia Mono', 10))
        self.run_button = ttk.Button(self.run_page, text='Run selected tool', command=self.run_tool,
                                     style='HayatAccent.TButton')
        self.feedback = DisplayVar(self)
        tk.Label(detail, textvariable=self.feedback, bg=gui.PANEL, fg=gui.GOLD_SOFT,
                 wraplength=480, justify='left', anchor='w').pack(fill='x', side='bottom', pady=4)
        self._busy = False
        self.current = None
        self.query.trace_add('write', lambda *_: self.refresh())
        self.category.trace_add('write', lambda *_: self.refresh())
        self.entry.bind('<Down>', self.focus_results)
        self.entry.bind('<Escape>', lambda _event: (self.query.set(''), 'break')[1])
        self.bind('<Control-f>', lambda _event: (self.tabs.select(self.catalog_page), self.entry.focus_set(), 'break')[-1])
        self.refresh()
        place_over(self, gui.root, (1080, 760))
        self.deiconify()
        self.entry.focus_set()
        from app.gui_icons import apply_icons
        apply_icons(self, type(gui))

    def _tab_changed(self, _event=None):
        if self.tabs.select() == str(self.connections) and self.connections.snapshot is None:
            self.connections.refresh()

    def _detail_changed(self, _event=None):
        if self.detail_tabs.select() == str(self.evidence_page):
            if self.connections.snapshot is None:
                self.connections.refresh()
            self._show_evidence()

    def _show_evidence(self, _snapshot=None):
        snapshot, row = self.connections.snapshot, self.current
        lines = ['Select a tool or capability to inspect its recorded evidence.']
        if row and snapshot is None:
            lines = ['Open Evidence to read local configuration and recorded use.']
        elif row and snapshot:
            lines = ['Inventory read: ' + str(snapshot.get('generated_at', 'unknown')), '']
            tool = next((tool for tool in snapshot.get('tools', []) if row.tool and tool['name'] == row.tool.name), None)
            keys = [row.capability.key] if row.capability else tool.get('capabilities', []) if tool else []
            for area in snapshot.get('capabilities', []):
                if area['key'] in keys:
                    lines += [area['title'] + ' · ' + area['state'], area['note'], '']
            if tool:
                lines += ['Handler registered: ' + ('yes' if tool.get('handler_callable') else 'not verified')]
                observed = tool.get('observed', {})
                if observed.get('available'):
                    lines += [f"Recorded calls: {observed.get('calls', 0)} · failures: {observed.get('failures', 0)} · "
                              f"refused: {observed.get('refused', 0)}",
                              'Last recorded use: ' + str(observed.get('last_at') or 'none in this window'),
                              'History scope: ' + str(observed.get('scope', 'unknown'))]
                else:
                    lines += ['Recorded usage unavailable; no success rate is inferred.']
                for link in snapshot.get('connections', []):
                    if tool.get('component_id') in {link.get('a'), link.get('b')}:
                        lines += ['', f"Verified connection: {link['a']} → {link['b']}", link['basis']]
            health = snapshot.get('health', {})
            lines += ['', 'System check: ' + str(health.get('checked_at') or 'not recorded'),
                      'Configuration and past outcomes do not establish current service availability.']
        self.connections._write(self.evidence_text, '\n'.join(lines))

    def select_tool(self, name):
        self.select_feature('tool:' + name)

    def select_feature(self, key):
        # Connectors may register tools after this window opens. Keep exact
        # handoffs from a refreshed connection snapshot resolvable.
        if key not in {row.key for row in self.inventory}:
            self.inventory = feature_inventory(self.gui)
            self._grouped, _ = capabilities.classify(self.gui.runtime.registry.names())
        self.tabs.select(self.catalog_page)
        self.category.set('Everything')
        self.query.set('')
        if key in self.shown:
            self.tree.selection_set(key)
            self.tree.focus(key)
            self.tree.see(key)
            self.show()

    def open_related(self):
        selected = self.related.selection()
        if selected:
            self.select_feature(selected[0])

    def focus_results(self, _event):
        self.tree.focus_set()
        return 'break'

    def refresh(self):
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        shown = [row for row in self.inventory if row.matches(self.query.get()) and
                 (self.category.get() == 'Everything' or row.category == self.category.get())]
        self.shown = {row.key: row for row in shown}
        from app.gui_icons import icon_image, icon_kind
        for row in shown:
            self.tree.insert('', 'end', iid=row.key, text=row.title, values=(row.category,),
                             image=icon_image(self.tree, icon_kind(row.title), self.gui.ROYAL_SOFT))
        self.count.set(f'{len(shown)} matches · {len(self.gui.runtime.registry.names())} registered tools · search by name or what you want to do')
        key = selected[0] if selected and selected[0] in self.shown else (shown[0].key if shown else None)
        if key:
            self.tree.selection_set(key)
            self.tree.focus(key)
        self.show()

    def show(self):
        selection = self.tree.selection()
        row = self.shown.get(selection[0]) if selection else None
        if row is self.current:
            return
        self.current = row
        self._tool_area = None
        if row and row.tool:
            areas = [area for area in capabilities.CATALOG if row.tool.name in self._grouped.get(area.key, [])]
            # A narrowly declared tool beats a broad wildcard area for the
            # workspace handoff; every other area remains in Linked tools.
            areas.sort(key=lambda area: row.tool.name not in area.tools)
            self._tool_area = next((area for area in areas if area.opens), None)
        self.feedback.set('')
        self.title_var.set(row.title if row else 'No matching features')
        content = row.description if row else 'Try a shorter phrase, such as income, camera, files or intelligence.'
        if row and row.capability:
            content += '\n\nHow to start\n' + row.capability.how + '\n\nExamples\n' + '\n'.join(row.capability.examples)
        if row and row.tool:
            from app.plain_output import describe_schema

            content += (f'\n\nTool: {row.tool.name}\nPermission scope: {row.tool.permission}\nRisk: {row.tool.risk}'
                        '\n\nWhat it needs\n' + describe_schema(row.tool.input_schema))
            if self._tool_area:
                content += '\n\nWorkspace: ' + self._tool_area.title
        self.description.configure(state='normal')
        self.description.delete('1.0', 'end')
        self.description.insert('1.0', content)
        self.description.configure(state='disabled')
        self._show_evidence()
        self.related.delete(*self.related.get_children())
        if row and row.capability:
            area_keys = {row.capability.key}
        elif row and row.tool:
            area_keys = {key for key, names in self._grouped.items() if row.tool.name in names}
        else:
            area_keys = set()
        related_tools = {name for key in area_keys for name in self._grouped[key]}
        for related in self.inventory:
            if related is row:
                continue
            if ((related.capability and related.capability.key in area_keys) or
                    (related.tool and related.tool.name in related_tools)):
                self.related.insert('', 'end', iid=related.key, text=related.title, values=(related.category,))
        self.open_button.configure(state='normal' if row and (row.callback or self._tool_area) else 'disabled')
        self.open_button.configure(text='Open related workspace' if self._tool_area else
                                   'Prepare request in chat' if row and row.capability and not row.capability.opens else 'Open feature')
        area = row.capability if row and row.capability else self._tool_area
        self.examples.configure(state='normal' if area and area.examples else 'disabled')
        if not (row and row.tool) and self.detail_tabs.select() == str(self.run_page):
            self.detail_tabs.select(0)
        self.detail_tabs.tab(self.run_page, state='normal' if row and row.tool else 'disabled')
        for widget in (self.arguments_label, self.arguments, self.run_button):
            widget.pack_forget()
        if row and row.tool:
            self.arguments_label.pack(fill='x', pady=(8, 4))
            self.arguments.pack(fill='both', expand=True)
            self.run_button.pack(fill='x', pady=6)
            self.arguments.delete('1.0', 'end')
            self.arguments.insert('1.0', '{}')

    def open(self):
        if self.current and self.current.callback:
            self.current.callback()
        elif self._tool_area:
            open_area(self.gui, self._tool_area)

    def example(self):
        area = self.current.capability if self.current and self.current.capability else self._tool_area
        if area and area.examples:
            self.gui.use_example(area.examples[0])
            self.gui.show_view('chat')
            self.destroy()

    def run_tool(self):
        if self._busy or not self.current or not self.current.tool:
            return
        try:
            import jsonschema
            arguments = json.loads(self.arguments.get('1.0', 'end-1c'))
            jsonschema.validate(arguments, self.current.tool.input_schema)
        except (ValueError, jsonschema.ValidationError) as exc:
            self.feedback.set('Check arguments: ' + str(exc).splitlines()[0][:350])
            return
        tool = self.current.tool.name
        self._busy = True
        self.run_button.configure(state='disabled')
        self.feedback.set(tool + ' is running; see Activity for recorded actions.')
        def run():
            try:
                result = run_declared_tool(self.gui.runtime, tool, arguments)
                from app.plain_output import describe

                message = describe(redact(result.output)) if result.success else str(redact(result.error))
            except Exception as exc:
                message = str(redact(str(exc)))
            self.gui._post(self.finish, tool, message[:24000])
        threading.Thread(target=run, daemon=True, name='hayat-feature-tool').start()

    def finish(self, tool, message):
        self._busy = False
        self.gui._report('Tool result · ' + tool, message)
        if self.winfo_exists():
            self.run_button.configure(state='normal')
            self.feedback.set('Result added to chat: ' + tool)
