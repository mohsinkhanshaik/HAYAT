"""Search the real feature and tool inventory, then open or run a declared action."""
from dataclasses import dataclass
import json
import threading
import uuid
import tkinter as tk
from tkinter import ttk

from app import capabilities
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_workspace import WorkspaceCommand
from app.safety.sensitive import redact


@dataclass
class Feature:
    key: str
    title: str
    category: str
    description: str
    callback: object = None
    capability: object = None
    tool: object = None

    def matches(self, query):
        extra = ' '.join(self.capability.keywords + self.capability.examples) if self.capability else ''
        return all(word in f'{self.title} {self.description} {self.category} {self.key} {extra}'.casefold()
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
                          ('Digital products', '_open_products'), ('Voice settings', '_open_control_studio'),
                          ('All features', '_open_features'), ('Activity and transparency', '_open_activity')):
        callback = getattr(gui, method, None)
        if callable(callback):
            rows.append(Feature('studio:' + method, title, 'Studios', 'Open ' + title, callback))
    for name in gui.runtime.registry.names():
        tool = gui.runtime.registry.get(name)
        rows.append(Feature('tool:' + name, name.replace('_', ' ').capitalize(), 'Tools', tool.description, tool=tool))
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
    from app.tools.registry import ToolRegistry
    original = runtime.registry
    registry = ToolRegistry(original.permissions, original.approvals)
    for field in ('authority', 'events', 'failures', 'understanding', 'context_supplier'):
        setattr(registry, field, getattr(original, field, None))
    tool = original.get(name)
    registry.register(tool.name, tool.description, tool.input_schema, tool.risk, tool.permission, tool.handler)
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
                         task_id=task_id):
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
        self.title('HAYAT · All features and tools')
        self.configure(bg=gui.BLACK)
        self.minsize(760, 540)
        self.bind('<Escape>', lambda _event: (self.destroy(), 'break')[1])
        header = tk.Frame(self, bg=gui.BLACK, padx=16, pady=12)
        header.pack(fill='x')
        tk.Label(header, text='All features and tools', bg=gui.BLACK, fg=gui.WHITE,
                 font=('Segoe UI Semibold', 18)).pack(side='left')
        ttk.Button(header, text='Activity and transparency', command=gui._open_activity).pack(side='right')
        filters = tk.Frame(self, bg=gui.BLACK, padx=16)
        filters.pack(fill='x')
        ttk.Label(filters, text='Find a feature').pack(side='left')
        self.query = StringVar(self)
        self.entry = ttk.Entry(filters, textvariable=self.query)
        self.entry.pack(side='left', fill='x', expand=True, padx=8)
        self.category = StringVar(self, 'Everything')
        ttk.Combobox(filters, textvariable=self.category, values=['Everything', 'Features', 'Workspaces', 'Controls', 'Studios', 'Tools'],
                     state='readonly', width=15).pack(side='right')
        self.count = StringVar(self)
        tk.Label(self, textvariable=self.count, bg=gui.BLACK, fg=gui.MUTED,
                 anchor='w').pack(fill='x', padx=16, pady=8)
        split = ttk.Panedwindow(self, orient='horizontal')
        split.pack(fill='both', expand=True, padx=16, pady=(0, 12))
        listing = tk.Frame(split, bg=gui.BLACK)
        split.add(listing, weight=2)
        self.tree = ttk.Treeview(listing, columns=('category',), selectmode='browse')
        self.tree.heading('#0', text='Feature / tool')
        self.tree.heading('category', text='Area')
        self.tree.column('#0', width=270)
        self.tree.column('category', width=95, stretch=False)
        scrollbar = ttk.Scrollbar(listing, command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show())
        self.tree.bind('<Double-1>', lambda _event: self.open())
        self.tree.bind('<Return>', lambda _event: (self.open(), 'break')[1])
        detail = tk.Frame(split, bg=gui.PANEL, padx=14, pady=12)
        split.add(detail, weight=3)
        self.title_var = StringVar(self)
        tk.Label(detail, textvariable=self.title_var, bg=gui.PANEL, fg=gui.WHITE,
                 wraplength=490, justify='left', anchor='w', font=('Segoe UI Semibold', 14)).pack(fill='x')
        self.description = DarkScrolledText(detail, height=11, wrap='word', bg=gui.PANEL,
                                           fg=gui.WHITE, font=('Segoe UI', 10))
        self.description.pack(fill='both', expand=True, pady=8)
        self.open_button = ttk.Button(detail, text='Open feature', command=self.open)
        self.open_button.pack(fill='x', pady=4)
        self.examples = ttk.Button(detail, text='Put example in chat', command=self.example)
        self.examples.pack(fill='x', pady=4)
        self.arguments_label = ttk.Label(detail, text='Tool arguments (JSON) · only Run tool executes them')
        self.arguments = tk.Text(detail, height=5, wrap='word', bg=gui.PANEL_2, fg=gui.WHITE,
                                 insertbackground=gui.WHITE, font=('Cascadia Mono', 10))
        self.run_button = ttk.Button(detail, text='Run selected tool', command=self.run_tool)
        self.feedback = StringVar(self)
        tk.Label(detail, textvariable=self.feedback, bg=gui.PANEL, fg=gui.GOLD_SOFT,
                 wraplength=480, justify='left', anchor='w').pack(fill='x', side='bottom', pady=4)
        self._busy = False
        self.current = None
        self.query.trace_add('write', lambda *_: self.refresh())
        self.category.trace_add('write', lambda *_: self.refresh())
        self.entry.bind('<Down>', self.focus_results)
        self.refresh()
        place_over(self, gui.root, (1080, 760))
        self.deiconify()
        self.entry.focus_set()
        from app.gui_icons import apply_icons
        apply_icons(self, type(gui))

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
        self.feedback.set('')
        self.title_var.set(row.title if row else 'No matching features')
        content = row.description if row else 'Try a shorter phrase, such as income, camera, files or intelligence.'
        if row and row.capability:
            content += '\n\nHow to start\n' + row.capability.how + '\n\nExamples\n' + '\n'.join(row.capability.examples)
        if row and row.tool:
            from app.plain_output import describe_schema

            content += (f'\n\nTool: {row.tool.name}\nPermission scope: {row.tool.permission}\nRisk: {row.tool.risk}'
                        '\n\nWhat it needs\n' + describe_schema(row.tool.input_schema))
        self.description.configure(state='normal')
        self.description.delete('1.0', 'end')
        self.description.insert('1.0', content)
        self.description.configure(state='disabled')
        self.open_button.configure(state='normal' if row and row.callback else 'disabled')
        self.open_button.configure(text='Prepare request in chat' if row and row.capability and not row.capability.opens else 'Open feature')
        self.examples.configure(state='normal' if row and row.capability and row.capability.examples else 'disabled')
        for widget in (self.arguments_label, self.arguments, self.run_button):
            widget.pack_forget()
        if row and row.tool:
            self.arguments_label.pack(fill='x', pady=(8, 4))
            self.arguments.pack(fill='x')
            self.run_button.pack(fill='x', pady=6)
            self.arguments.delete('1.0', 'end')
            self.arguments.insert('1.0', '{}')

    def open(self):
        if self.current and self.current.callback:
            self.current.callback()

    def example(self):
        if self.current and self.current.capability:
            self.gui.use_example(self.current.capability.examples[0])
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
