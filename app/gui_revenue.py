"""A browsable revenue workspace: workflows, recorded evidence and local deliverables."""
import json
import tkinter as tk
from tkinter import ttk

from app.gui_resources import StringVar
from app.gui_windows import place_over
from app.gui_depth import DepthSurface
from app.revenue_catalog import CATALOG, BY_NAME
from app.revenue_inputs import input_guide


class RevenueWindow(tk.Toplevel):
    def __init__(self, gui):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette, self.workspace = gui, type(gui), gui.runtime.revenue
        p = self.palette
        self.title('HAYAT · Revenue workspace')
        self.transient(gui.root)
        self.configure(bg=p.BLACK)
        self.minsize(720, 530)
        header_surface = DepthSurface(self, p, padding=14)
        header_surface.pack(fill='x', padx=14, pady=(8, 4))
        header = header_surface.body
        tk.Label(header, text='HAYAT  /  OPPORTUNITIES · DELIVERY · REVENUE',
                 font=('Segoe UI Semibold', 9), bg=p.PANEL, fg=p.OLIVE_SOFT,
                 anchor='w').pack(fill='x', pady=(0, 7))
        ttk.Button(header, text='+ Build an earning plan', style='HayatAccent.TButton',
                   command=lambda: self.open_intake('income_execution_plan')).pack(side='right', padx=(12, 0))
        tk.Label(header, text='Business command centre', font=('Segoe UI Semibold', 24),
                 bg=p.PANEL, fg=p.WHITE, anchor='w').pack(fill='x')
        tk.Label(header, text='Discover opportunities. Prepare good work. Measure what earns.',
                 bg=p.PANEL, fg=p.MUTED, anchor='w').pack(fill='x', pady=(4, 10))
        self.summary = StringVar(self)
        tk.Label(header, textvariable=self.summary, bg=p.PANEL, fg=p.GOLD_SOFT,
                 anchor='w', justify='left', wraplength=850).pack(fill='x')
        footer_surface = DepthSurface(self, p, padding=10)
        footer_surface.pack(side='bottom', fill='x', padx=14, pady=(0, 8))
        footer = footer_surface.body
        from app.gui_intelligence_parts import FlowRow
        buttons = FlowRow(footer, p.PANEL, gap=6, height=36)
        buttons.pack(fill='x')
        self.use_button = buttons.add(ttk.Button(buttons, text='Use workflow in chat', style='Hayat.TButton', command=self.use_selected))
        for label, callback in (('Enter inputs / Run', self.open_intake), ('Refresh records', self.refresh),
                                 ('Osiris playbook', self.use_osiris), ('Close', self.destroy)):
            buttons.add(ttk.Button(buttons, text=label, style='HayatAccent.TButton' if label.startswith('Enter') else 'Hayat.TButton', command=callback))
        tk.Label(footer, text='Drafts stay local. Campaign email needs exact-message approval; social posts use a review calendar.',
                 bg=p.PANEL, fg=p.MUTED, anchor='w', wraplength=850).pack(fill='x', pady=(10, 0))
        self.tabs = ttk.Notebook(self)
        stats = tk.Frame(self, bg=p.BLACK)
        stats.pack(fill='x', padx=22, pady=(4, 10))
        self.stat_values = {}
        for index, (key, title) in enumerate([('actions', 'OPEN ACTIONS'), ('pipeline', 'TRACKED LEADS'),
                                             ('artifacts', 'SAVED DELIVERABLES'), ('experiments', 'RECORDED TESTS')]):
            card = tk.Frame(stats, bg=p.PANEL_2, padx=14, pady=10, highlightthickness=1, highlightbackground=p.LINE)
            card.grid(row=0, column=index, sticky='nsew', padx=(0, 6))
            stats.columnconfigure(index, weight=1, uniform='stat')
            value = self.stat_values[key] = StringVar(self, '—')
            tk.Label(card, text=title, font=('Segoe UI Semibold', 8), fg=p.MUTED, bg=p.PANEL_2).pack(anchor='w')
            tk.Label(card, textvariable=value, font=('Segoe UI Semibold', 25), fg=p.GOLD, bg=p.PANEL_2).pack(anchor='w')
        self.tabs.pack(fill='both', expand=True, padx=22, pady=(0, 10))
        workflows = tk.Frame(self.tabs, bg=p.PANEL)
        self.tabs.add(workflows, text='Workflows')
        filters = tk.Frame(workflows, bg=p.PANEL, padx=12, pady=10)
        filters.pack(fill='x')
        self.query = StringVar(self)
        self.group = StringVar(self, 'All areas')
        ttk.Entry(filters, textvariable=self.query, width=32).pack(side='left', fill='x', expand=True)
        groups = ['All areas', *dict.fromkeys(tool.group for tool in CATALOG)]
        ttk.Combobox(filters, textvariable=self.group, values=groups, state='readonly', width=19).pack(side='left', padx=(10, 0))
        split = tk.PanedWindow(workflows, orient='horizontal', bg=p.PANEL, sashwidth=8, bd=0)
        split.pack(fill='both', expand=True)
        library = tk.Frame(split, bg=p.PANEL)
        split.add(library, minsize=260, width=460)
        self.tools = self.table(library, [('area', 'Area', 90), ('mode', 'Operation', 80)], height=10)
        self.tools.column('#0', width=220, minwidth=140)
        self.tools.bind('<<TreeviewSelect>>', lambda _event: self.show_tool())
        detail_holder = tk.Frame(split, bg=p.PANEL, padx=8, pady=10)
        split.add(detail_holder, minsize=250)
        ttk.Button(detail_holder, text='Enter inputs, attach evidence & run →', style='HayatAccent.TButton',
                   command=self.open_intake).pack(fill='x', pady=(0, 10))
        self.details = tk.Text(detail_holder, bg=p.BLACK, fg=p.WHITE, wrap='word', height=7,
                               relief='flat', padx=12, pady=10, font=('Segoe UI', 10), state='disabled')
        scrollbar = ttk.Scrollbar(detail_holder, command=self.details.yview)
        scrollbar.pack(side='right', fill='y')
        self.details.configure(yscrollcommand=scrollbar.set)
        self.details.pack(fill='both', expand=True)
        self.records = {}
        for title, columns in [
            ('Income actions', [('priority', 'Priority', 90), ('status', 'State', 120), ('tool', 'Suggested workflow', 200)]),
            ('Pipeline', [('source', 'Source', 150), ('status', 'Stage', 100), ('follow', 'Follow-up', 110)]),
            ('Experiments', [('value', 'Recorded contribution', 150), ('hours', 'Hours', 90)]),
            ('Campaigns', [('status', 'Review / delivery state', 170), ('count', 'Messages', 100)]),
            ('Artifacts', [('kind', 'Type', 120), ('created', 'Created', 170)]),
        ]:
            frame = tk.Frame(self.tabs, bg=p.PANEL)
            self.tabs.add(frame, text=title)
            tree = self.table(frame, columns)
            self.records[title] = tree
            tree.bind('<<TreeviewSelect>>', lambda _event, label=title: self.show_record(label))
        self.record_details = {}
        self.query.trace_add('write', lambda *_args: self.filter_tools())
        self.group.trace_add('write', lambda *_args: self.filter_tools())
        self.bind('<Escape>', lambda _event: self.destroy())
        self.filter_tools()
        self.refresh()
        place_over(self, gui.root, (1160, 820))
        self.deiconify()
        self.lift()

    def table(self, parent, columns, height=12, expand=True):
        holder = tk.Frame(parent, bg=self.palette.PANEL)
        holder.pack(fill='both', expand=expand, padx=12, pady=(0, 10))
        tree = ttk.Treeview(holder, columns=[name for name, _, _ in columns], show='tree headings', height=height)
        tree.heading('#0', text='Workflow / record')
        tree.column('#0', width=330, minwidth=200)
        for name, label, width in columns:
            tree.heading(name, text=label)
            tree.column(name, width=width, minwidth=70)
        bar = ttk.Scrollbar(holder, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        tree.pack(fill='both', expand=True)
        return tree

    def filter_tools(self):
        selected = self.tools.selection()
        self.tools.delete(*self.tools.get_children())
        text = self.query.get().lower().strip()
        for tool in CATALOG:
            if self.group.get() != 'All areas' and tool.group != self.group.get():
                continue
            if text and text not in (tool.name.replace('_', ' ') + ' ' + tool.task).lower():
                continue
            self.tools.insert('', 'end', iid=tool.name, text=tool.name.replace('_', ' ').capitalize(), values=(tool.group, tool.mode))
        rows = self.tools.get_children()
        if rows:
            self.tools.selection_set(selected[0] if selected and selected[0] in rows else rows[0])
        self.show_tool()

    def show_tool(self):
        selected = self.tools.selection()
        tool = BY_NAME.get(selected[0]) if selected else None
        self.use_button.configure(state='normal' if tool else 'disabled')
        text = (f'{tool.name.replace("_", " ").capitalize()}\n\n{tool.task}\n\n'
                f'Inputs\n{input_guide(tool.name)}\n\n'
                'Use workflow in chat prepares an editable request. It does not execute or send anything.') if tool else 'No workflows match this filter.'
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def use_selected(self):
        selected = self.tools.selection()
        if not selected:
            return
        name = selected[0]
        tool = BY_NAME[name]
        arguments = {'goal': 'Describe your actual request here', 'data': {}}
        if tool.mode in {'dashboard', 'probability'}:
            arguments = {}
        if tool.mode in {'pipeline', 'experiment', 'metrics', 'calendar', 'campaign', 'monitor'}:
            arguments = {'action': 'report'}
        if name in {'income_action_queue', 'osiris_status', 'osiris_playbook'}:
            arguments = {}
        self.gui.use_example(name + ' ' + json.dumps(arguments))
        self.destroy()

    def use_osiris(self):
        self.gui.use_example('Show Osiris playbook')
        self.destroy()

    def open_intake(self, name=None):
        selected = self.tools.selection()
        if name or selected:
            from app.gui_revenue_intake import RevenueIntake
            return RevenueIntake(self, name or selected[0])

    def refresh(self):
        snapshot = self.workspace.dashboard({})
        rows = snapshot['books']['by_currency']
        self.summary.set(' · '.join(f"{row['currency']}: profit {row['profit_before_tax']}" for row in rows)
                         or f'{len(CATALOG)} workflows ready to browse · no revenue recorded yet')
        for tree in self.records.values():
            tree.delete(*tree.get_children())
        self.record_details = {}
        from app.income import action_queue
        for item in action_queue(self.workspace, 'report', '', {})['actions']:
            key = 'income-' + item['key']
            row = item['data']
            self.records['Income actions'].insert('', 'end', iid=key, text=row['title'],
                values=(row['priority'], row['status'], row.get('tool') or ''))
            self.record_details[key] = item
        for lead in self.workspace.ledger.leads('all')['leads']:
            key = f"lead-{lead['id']}"
            self.records['Pipeline'].insert('', 'end', iid=key, text=lead['title'], values=(lead['source'], lead['status'], lead.get('follow_up') or ''))
            self.record_details[key] = lead
        for item in snapshot['experiments']['experiments']:
            key = 'experiment-' + item['key']
            self.records['Experiments'].insert('', 'end', iid=key, text=item['key'],
                values=((str(item.get('net_contribution', 'Unknown')) + ' ' + item['data'].get('currency', 'USD')), item['data'].get('hours', 'Unknown')))
            self.record_details[key] = item
        for item in snapshot['campaigns']['campaigns']:
            key = item['campaign_id']
            self.records['Campaigns'].insert('', 'end', iid=key, text=key, values=(item['state'], len(item['messages'])))
            self.record_details[key] = item
        for item in snapshot['artifacts']:
            self.records['Artifacts'].insert('', 'end', iid=item['id'], text=item['title'], values=(item['kind'], item['created']))
            self.record_details[item['id']] = item
        for name, tab in (('actions', 'Income actions'), ('pipeline', 'Pipeline'),
                          ('artifacts', 'Artifacts'), ('experiments', 'Experiments')):
            count = len(self.records[tab].get_children())
            if name == 'actions':
                count = sum(self.records[tab].item(row, 'values')[1] not in {'done', 'cancelled'}
                            for row in self.records[tab].get_children())
            self.stat_values[name].set(str(count))

    def show_record(self, title):
        selected = self.records[title].selection()
        if not selected:
            return
        from app.gui_theme import DarkScrolledText
        record = self.record_details[selected[0]]
        details = tk.Toplevel(self)
        details.withdraw()
        details.title('HAYAT · ' + title + ' record')
        details.transient(self)
        details.configure(bg=self.palette.BLACK)
        text = DarkScrolledText(details, wrap='word', height=18, font=('Segoe UI', 10),
                                bg=self.palette.PANEL, fg=self.palette.WHITE, padx=12, pady=10)
        text.pack(fill='both', expand=True, padx=16, pady=16)
        from app.plain_output import describe

        text.insert('1.0', describe(record))
        text.configure(state='disabled')
        if title == 'Income actions':
            row = record['data']
            controls = ttk.Frame(details)
            controls.pack(fill='x', padx=16, pady=(0, 8))

            def open_work():
                tool = row.get('tool')
                if tool not in BY_NAME:
                    return
                intake = self.open_intake(tool)
                intake.goal.set(row['title'])
                intake.context_text.insert('1.0', row.get('instructions', '') + '\nAcceptance: ' + row.get('acceptance', ''))

            def record_outcome():
                from tkinter import simpledialog, messagebox
                evidence = simpledialog.askstring('Record completed work',
                    'What was completed? Include a deliverable path or other outcome evidence.', parent=details)
                if not evidence or not evidence.strip():
                    return
                result = self.workspace.registry.execute('income_action_queue', {
                    'action': 'update', 'key': record['key'], 'data': {'status': 'done', 'evidence': evidence}})
                if result.success:
                    self.refresh()
                    details.destroy()
                else:
                    messagebox.showerror('Could not save outcome', result.error, parent=details)
            ttk.Button(controls, text='Open suggested workflow', command=open_work).pack(side='left')
            ttk.Button(controls, text='Record completed outcome', command=record_outcome).pack(side='left', padx=8)
        if title == 'Artifacts':
            def show_in_chat():
                self.gui.show_view('chat')
                self.gui._append('assistant', record['title'] + '\n' + record['path'])
                details.destroy()
            ttk.Button(details, text='Show artifact in chat', command=show_in_chat).pack(pady=(0, 8))
        ttk.Button(details, text='Close', command=details.destroy).pack(pady=(0, 12))
        details.bind('<Escape>', lambda _event: details.destroy())
        place_over(details, self, (680, 480))
        details.deiconify()
        return details
