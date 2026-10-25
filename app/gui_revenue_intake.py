"""Manual revenue inputs, selected attachments and nonblocking tool execution."""
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.gui_resources import StringVar
from app.gui_windows import place_over
from app.revenue_inputs import input_guide


class RevenueIntake(tk.Toplevel):
    def __init__(self, owner, name):
        super().__init__(owner)
        self.withdraw()
        self.owner, self.name = owner, name
        self.paths = []
        self.results = queue.Queue()
        self.busy = False
        self.title('HAYAT · ' + name.replace('_', ' ').title())
        self.configure(bg=owner.palette.PANEL)
        self.minsize(600, 620)
        p = owner.palette
        footer = ttk.Frame(self)
        footer.pack(side='bottom', fill='x', padx=16, pady=10)
        self.run_button = ttk.Button(footer, text='Run workflow', command=self.run)
        self.run_button.pack(side='left')
        ttk.Button(footer, text='Prepare in chat', command=self.in_chat).pack(side='left', padx=8)
        ttk.Button(footer, text='Close', command=self.destroy).pack(side='right')
        tk.Label(self, text=input_guide(name), wraplength=670, justify='left',
                 bg=p.PANEL, fg=p.MUTED).pack(fill='x', padx=16, pady=10)
        self.goal = StringVar(self)
        ttk.Label(self, text='Goal / actual brief').pack(anchor='w', padx=16)
        ttk.Entry(self, textvariable=self.goal).pack(fill='x', padx=16, pady=(0, 8))
        self.fields = {}
        field_sets = {
            'income_execution_plan': [('skills', 'Your skills', ''), ('buyer', 'Target buyer', ''),
                ('offer', 'Paid offer', ''), ('deliverable', 'What you will deliver', ''),
                ('weekly_hours', 'Hours available per week', '5')],
            'pricing_engine': [('hours', 'Delivery hours', ''), ('hourly_cost', 'Hourly cost', ''),
                ('direct_costs', 'Direct costs', '0'), ('margin_percent', 'Target margin %', '30')],
            'offer_unit_economics': [('price', 'Price per sale', ''), ('direct_costs', 'Direct costs', '0'),
                ('delivery_hours', 'Delivery hours', ''), ('hourly_cost', 'Value of your hour', '')],
            'income_idea_engine': [('skills', 'Your actual skills', '')],
            'income_portfolio_plan': [('skills', 'Your actual skills', '')],
            'revenue_goal_planner': [('monthly_target', 'Monthly contribution target', ''),
                ('contribution_per_sale', 'Contribution per sale', ''), ('hours_per_sale', 'Hours per sale', ''),
                ('monthly_available_hours', 'Available hours / month', '')],
            'open_source_integration': [('repository', 'GitHub owner/repository', ''),
                ('target_stack', 'Target language / framework', '')],
            'data_cleanup_service': [('path', 'CSV path (or attach below)', '')],
        }
        if name in field_sets:
            form = ttk.Frame(self)
            form.pack(fill='x', padx=16)
            for row, (key, label, default) in enumerate(field_sets[name]):
                ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=2)
                variable = StringVar(self, default)
                self.fields[key] = variable
                ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky='ew', padx=(10, 0))
            form.columnconfigure(1, weight=1)
        advanced = ttk.Frame(self)
        advanced.pack(fill='x', padx=16, pady=6)
        self.action, self.key = StringVar(self, 'report'), StringVar(self)
        ttk.Label(advanced, text='Action').pack(side='left')
        ttk.Combobox(advanced, textvariable=self.action, width=10, state='readonly',
                     values=('report', 'create', 'record', 'update', 'list', 'snapshot')).pack(side='left', padx=6)
        ttk.Label(advanced, text='Record key').pack(side='left')
        ttk.Entry(advanced, textvariable=self.key).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Label(self, text='Manual data (JSON object; numbers, client, prices, URLs or file path)').pack(anchor='w', padx=16)
        self.data_text = tk.Text(self, height=4, bg=p.BLACK, fg=p.WHITE, insertbackground=p.WHITE, wrap='word',
                                 font=('Consolas', 10))  # typed as JSON
        self.data_text.pack(fill='x', padx=16)
        self.data_text.insert('1.0', '{}')
        ttk.Label(self, text='Notes / supplied evidence').pack(anchor='w', padx=16, pady=(6, 0))
        self.context_text = tk.Text(self, height=3, bg=p.BLACK, fg=p.WHITE, insertbackground=p.WHITE, wrap='word',
                                    font=('Segoe UI', 10))
        self.context_text.pack(fill='x', padx=16)
        attach = ttk.Frame(self)
        attach.pack(fill='x', padx=16, pady=8)
        ttk.Button(attach, text='Attach files', command=self.attach).pack(side='left')
        ttk.Button(attach, text='Clear attachments', command=self.clear_attachments).pack(side='left', padx=6)
        self.attachment_label = StringVar(self, 'No attachments')
        ttk.Label(attach, textvariable=self.attachment_label).pack(side='left')
        self.status = StringVar(self, 'Enter your real inputs. Results and errors appear below.')
        ttk.Label(self, textvariable=self.status, wraplength=670).pack(fill='x', padx=16)
        from app.gui_theme import DarkScrolledText
        self.output = DarkScrolledText(self, height=7, wrap='word', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI', 10))
        self.output.pack(fill='both', expand=True, padx=16, pady=8)
        place_over(self, owner, (740, 850))
        self.deiconify()

    def attach(self):
        selected = filedialog.askopenfilenames(parent=self, title='Attach business evidence', filetypes=[
            ('Documents and data', '*.pdf *.docx *.xlsx *.csv *.json *.txt *.md *.png *.jpg *.jpeg'), ('All files', '*.*')])
        combined = list(dict.fromkeys([*self.paths, *selected]))
        if len(combined) > 5:
            self.status.set('Choose at most five attachments.')
            return
        self.paths = combined
        self.attachment_label.set(f'{len(self.paths)} file(s) selected')

    def clear_attachments(self):
        self.paths.clear()
        self.attachment_label.set('No attachments')

    def arguments(self):
        data = json.loads(self.data_text.get('1.0', 'end').strip() or '{}')
        if not isinstance(data, dict):
            raise ValueError('Manual data must be a JSON object, for example {"price": 100}.')
        data.update({key: value.get().strip() for key, value in self.fields.items() if value.get().strip()})
        if self.name == 'data_cleanup_service' and len(self.paths) == 1:
            data.setdefault('path', self.paths[0])
            attachments = []  # The CSV tool reads the entire selected dataset itself.
        else:
            attachments = list(self.paths)
        return {'goal': self.goal.get().strip(), 'data': data, 'context': self.context_text.get('1.0', 'end').strip(),
                'action': self.action.get(), 'key': self.key.get().strip(), 'attachments': attachments}

    def in_chat(self):
        try:
            arguments = self.arguments()
        except (ValueError, TypeError) as exc:
            self.status.set(str(exc))
            return
        self.owner.gui.use_example(self.name + ' ' + json.dumps(arguments))
        self.destroy()

    def run(self):
        if self.busy:
            return
        try:
            arguments = self.arguments()
        except (ValueError, TypeError) as exc:
            self.status.set(str(exc))
            return
        self.busy = True
        self.run_button.configure(state='disabled')
        self.status.set('Running… You can keep using HAYAT.')
        registry, name, results = self.owner.workspace.registry, self.name, self.results

        def work():
            try:
                result = registry.execute(name, arguments)
                results.put((result.success, result.output if result.success else result.error))
            except Exception as exc:
                results.put((False, str(exc)))
        threading.Thread(target=work, name='revenue-intake', daemon=True).start()
        self.after(100, self.poll)

    def poll(self):
        try:
            ok, result = self.results.get_nowait()
        except queue.Empty:
            self.after(100, self.poll)
            return
        self.busy = False
        self.run_button.configure(state='normal')
        self.status.set('Completed. Output paths are shown below.' if ok else 'Needs attention — inputs preserved for correction.')
        self.output.delete('1.0', 'end')
        from app.plain_output import describe

        self.output.insert('1.0', describe(result))
        if ok and self.owner.winfo_exists():
            self.owner.refresh()
