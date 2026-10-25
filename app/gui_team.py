"""A live workspace for HAYAT's temporary specialist teams: limits, teams, workers and evidence.

Three levels, top to bottom:

* **This machine** -- how big a team the hardware allows and why (from
  app.agents.capacity): workers per team, teams at once, the brain's context
  window and the measured time per worker.
* **Teams** -- each running or finished team with its progress against its
  own time and tool-call budget.
* **Workers** -- every worker of the selected team: status, the workers it
  waits for, tools used, sources and time. Selecting one shows its mission,
  findings, source links and gaps in full; with no worker selected the pane
  describes the whole team.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.gui_depth import DepthSurface
from app.gui_windows import place_over


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"


class TeamWorkspace(tk.Toplevel):
    def __init__(self, parent, palette, teams, roles=()):
        super().__init__(parent)
        self.withdraw()
        self.palette, self.teams = palette, teams
        self.title('HAYAT · Team workspace')
        self.configure(bg=palette.BLACK)
        self.transient(parent)
        self.minsize(760, 560)
        self._poll = None
        self._rows = {}
        self._preview_specs = []
        self._worker_team = None
        header_surface = DepthSurface(self, palette, padding=14)
        header_surface.pack(fill='x', padx=14, pady=(8, 6))
        header = header_surface.body
        tk.Label(header, text='Team workspace', bg=palette.PANEL, fg=palette.WHITE,
                 font=('Segoe UI Semibold', 19), anchor='w').pack(fill='x')
        tk.Label(header, text='Temporary specialist agents: what this machine allows, what each team is doing, '
                              'and every worker\'s evidence.',
                 bg=palette.PANEL, fg=palette.MUTED, anchor='w', wraplength=980, justify='left').pack(fill='x', pady=(5, 8))
        tk.Label(header, text='Core specialists: ' + (', '.join(roles) or 'No specialist roles registered'),
                 bg=palette.PANEL, fg=palette.GOLD_SOFT, wraplength=980, justify='left', anchor='w').pack(fill='x')

        footer = tk.Frame(self, bg=palette.BLACK, padx=22, pady=10)
        footer.pack(side='bottom', fill='x')
        self.stop_button = ttk.Button(footer, text='Stop selected team', style='Hayat.TButton', command=self.stop_selected)
        self.stop_button.pack(side='left')
        ttk.Button(footer, text='Refresh', style='Hayat.TButton', command=self.refresh).pack(side='left', padx=8)
        self.preview_button = ttk.Button(footer, text='Preview job team', style='HayatRoyal.TButton', command=self.preview_job_team,
                                         state='normal' if callable(getattr(teams, 'plan', None)) else 'disabled')
        self.preview_button.pack(side='left')
        ttk.Button(footer, text='Close', style='Hayat.TButton', command=self.destroy).pack(side='right')

        self._build_capacity()

        body = tk.PanedWindow(self, orient='horizontal', bg=palette.BLACK, sashwidth=6, bd=0)
        body.pack(fill='both', expand=True, padx=22, pady=(4, 0))
        lists = tk.Frame(body, bg=palette.BLACK)
        detail_frame = tk.Frame(body, bg=palette.PANEL)
        body.add(lists, minsize=380, stretch='always')
        body.add(detail_frame, minsize=300, stretch='always')

        tk.Label(lists, text='Teams', bg=palette.BLACK, fg=palette.WHITE, font=('Segoe UI Semibold', 11),
                 anchor='w').pack(fill='x')
        self.jobs = ttk.Treeview(lists, columns=('state', 'progress', 'time', 'calls'), show='tree headings', height=5)
        for column, title, width in (('#0', 'Goal', 260), ('state', 'Status', 140), ('progress', 'Workers', 70),
                                     ('time', 'Time / budget', 110), ('calls', 'Tool calls', 90)):
            self.jobs.heading(column, text=title)
            self.jobs.column(column, width=width, minwidth=60, stretch=column == '#0')
        self.jobs.pack(fill='both', expand=True)
        self.jobs.bind('<<TreeviewSelect>>', lambda _event: self._team_selected())

        tk.Label(lists, text='Workers of the selected team', bg=palette.BLACK, fg=palette.WHITE,
                 font=('Segoe UI Semibold', 11), anchor='w').pack(fill='x', pady=(10, 0))
        self.workers = ttk.Treeview(lists, columns=('state', 'after', 'used', 'sources', 'time'),
                                    show='tree headings', height=7)
        for column, title, width in (('#0', 'Worker', 170), ('state', 'Status', 165), ('after', 'Waits for', 150),
                                     ('used', 'Tools used', 95), ('sources', 'Sources', 75), ('time', 'Time', 60)):
            self.workers.heading(column, text=title)
            self.workers.column(column, width=width, minwidth=50, stretch=column in {'#0', 'after'})
        self.workers.pack(fill='both', expand=True)
        self.workers.bind('<<TreeviewSelect>>', lambda _event: self.show_details())

        self.details = tk.Text(detail_frame, height=7, bg=palette.PANEL, fg=palette.WHITE, relief='flat',
                               padx=14, pady=12, wrap='word', font=('Segoe UI', 10), state='disabled')
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.details.yview)
        detail_scroll.pack(side='right', fill='y')
        self.details.configure(yscrollcommand=detail_scroll.set)
        self.details.pack(fill='both', expand=True)

        self.bind('<Escape>', lambda _event: self.destroy())
        self.refresh()
        place_over(self, parent, (1120, 780))
        self.deiconify()
        self.lift()

    # ------------------------------------------------------------- capacity

    def _build_capacity(self):
        p = self.palette
        frame = tk.Frame(self, bg=p.BLACK)
        frame.pack(fill='x', padx=22, pady=(2, 6))
        top = tk.Frame(frame, bg=p.BLACK)
        top.pack(fill='x')
        tk.Label(top, text='This machine', bg=p.BLACK, fg=p.WHITE, font=('Segoe UI Semibold', 11),
                 anchor='w').pack(side='left')
        ttk.Button(top, text='Recheck limits', style='Compact.Hayat.TButton',
                   command=lambda: self.show_capacity(refresh=True)).pack(side='right')
        tiles = tk.Frame(frame, bg=p.BLACK)
        tiles.pack(fill='x', pady=(4, 2))
        self.capacity_values = {}
        for index, (key, title) in enumerate((('workers', 'Workers per team'), ('teams', 'Teams at once'),
                                              ('window', 'Brain window'), ('pace', 'Time per worker'))):
            tile = tk.Frame(tiles, bg=p.PANEL, padx=12, pady=8)
            tile.grid(row=0, column=index, sticky='nsew', padx=(0 if index == 0 else 8, 0))
            tiles.columnconfigure(index, weight=1, uniform='capacity')
            tk.Label(tile, text=title, bg=p.PANEL, fg=p.MUTED, font=('Segoe UI', 9), anchor='w').pack(fill='x')
            value = tk.StringVar(self, '…')
            tk.Label(tile, textvariable=value, bg=p.PANEL, fg=p.GOLD, font=('Segoe UI Semibold', 15),
                     anchor='w').pack(fill='x')
            self.capacity_values[key] = value
        self.capacity_reasons = tk.StringVar(self, '')
        reasons = tk.Label(frame, textvariable=self.capacity_reasons, bg=p.BLACK, fg=p.MUTED, font=('Segoe UI', 9),
                           justify='left', anchor='w')
        reasons.pack(fill='x', pady=(4, 0))
        frame.bind('<Configure>', lambda event: reasons.configure(wraplength=max(300, event.width - 10)))
        self.show_capacity()

    def show_capacity(self, *, refresh: bool = False):
        measure = getattr(self.teams, 'capacity', None)
        if not callable(measure):
            for value in self.capacity_values.values():
                value.set('—')
            self.capacity_reasons.set('Team limits are not available in this runtime.')
            return
        capacity = measure(refresh=refresh)
        self.capacity = capacity
        self.capacity_values['workers'].set(str(capacity.max_workers))
        self.capacity_values['teams'].set(str(capacity.concurrent_teams))
        self.capacity_values['window'].set(f"{capacity.context_tokens:,} tokens")
        self.capacity_values['pace'].set(f"{capacity.seconds_per_worker:.0f} s "
                                         + (f"(measured, {capacity.measured_from_runs} runs)"
                                            if capacity.measured_from_runs else "(estimate)"))
        self.capacity_reasons.set('\n'.join('• ' + reason for reason in capacity.reasons))

    # ---------------------------------------------------------------- teams

    def refresh(self):
        if self._poll is not None:
            self.after_cancel(self._poll)
        snapshot = self.teams.status() if self.teams else {'running': [], 'recent': []}
        rows = {}
        for running, entries in ((True, snapshot['running']), (False, snapshot['recent'])):
            for entry in entries:
                row = {**entry, 'running': running}
                key = row['team_id']
                rows[key] = row
                state = ('Stopping' if row.get('cancelled') else row.get('current_worker') or 'Starting') if running else 'Delivered' if row.get('complete') else 'Incomplete'
                budget = row.get('max_seconds')
                time_text = _minutes(float(row.get('seconds') or 0)) + (f" / {_minutes(budget)}" if budget else '')
                calls = row.get('tool_calls')
                calls_text = (f"{calls} / {row['max_tool_calls']}" if calls is not None and row.get('max_tool_calls')
                              else str(calls) if calls is not None else '')
                values = (state, f"{row.get('done', 0)}/{row.get('total', '?')}", time_text, calls_text)
                if self.jobs.exists(key):
                    self.jobs.item(key, text=row['goal'][:90], values=values)
                else:
                    self.jobs.insert('', 'end', iid=key, text=row['goal'][:90], values=values)
        for old in self.jobs.get_children():
            if old not in rows:
                self.jobs.delete(old)
        self._rows = rows
        self._fill_workers()
        self.show_details()
        self._poll = self.after(1500, self.refresh)

    def _selected_team(self):
        selected = self.jobs.selection()
        return self._rows.get(selected[0]) if selected else None

    def _team_selected(self):
        self._fill_workers()
        self.show_details()

    @staticmethod
    def _worker_state(spec, worker, row):
        if worker:
            if worker.get('complete'):
                return 'Complete'
            return 'Evidence gathered' if worker.get('ok') else 'Unverified / incomplete'
        if spec['role'] == row.get('current_worker'):
            return 'Working'
        return 'Skipped' if not row.get('running') else 'Waiting'

    def _fill_workers(self):
        row = self._selected_team()
        team_id = row['team_id'] if row else None
        if team_id != self._worker_team:
            self.workers.delete(*self.workers.get_children())
            self._worker_team = team_id
        if not row:
            return
        results = {worker['role']: worker for worker in row.get('results', [])}
        keep = set()
        for spec in row.get('worker_specs', []):
            worker = results.get(spec['role'])
            values = (self._worker_state(spec, worker, row), ', '.join(spec.get('depends_on', [])) or 'starts',
                      len(worker.get('tools_used', [])) if worker else '', len(worker.get('sources', [])) if worker else '',
                      f"{worker.get('seconds', 0):.0f} s" if worker else '')
            key = spec['role']
            keep.add(key)
            if self.workers.exists(key):
                self.workers.item(key, text=key, values=values)
            else:
                self.workers.insert('', 'end', iid=key, text=key, values=values)
        for old in self.workers.get_children():
            if old not in keep:
                self.workers.delete(old)

    # -------------------------------------------------------------- details

    def show_details(self):
        row = self._selected_team()
        self.stop_button.configure(state='normal' if row and row['running'] and not row.get('cancelled') else 'disabled')
        worker_selected = self.workers.selection()
        if row and worker_selected:
            text = self._worker_text(row, worker_selected[0])
        elif not row:
            text = ('No team selected.\n\nAsk in chat: “Research 20 AI infrastructure companies and find jobs suitable for me.”\n\n'
                    'HAYAT creates temporary Research, Job Search, Company Intelligence, Resume Matching and Verification agents.\n'
                    'Select a team to see its workers, handoffs, deliverables, evidence and missing work; select a worker '
                    'for its findings and sources in full.\n\n'
                    'Ask “how many agents can you run?” for this machine\'s limits in words.')
            if self._preview_specs:
                text = 'Plan preview · no workers started\n\nResearch 20 AI infrastructure companies and find jobs suitable for me.\n\n'
                text += '\n\n'.join(f"{spec.role}\nAfter: {', '.join(spec.depends_on) or 'Starts the research'}\n"
                                    f"Deliverable: {spec.deliverable}\nTools: {', '.join(spec.tools) or 'Unavailable'}"
                                    for spec in self._preview_specs)
        else:
            text = self._team_text(row)
        if self.details.get('1.0', 'end-1c') != text:
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', text)
            self.details.configure(state='disabled')

    def _team_text(self, row):
        parts = [row['goal']]
        budget = []
        if row.get('max_seconds'):
            budget.append(f"Time: {_minutes(float(row.get('seconds') or 0))} of a {_minutes(row['max_seconds'])} budget")
        if row.get('max_tool_calls'):
            budget.append(f"Tool calls: {row.get('tool_calls', 0)} of {row['max_tool_calls']}")
        if row.get('planned_by'):
            budget.append(f"Planned by: {row['planned_by']}")
        if budget:
            parts.append(' · '.join(budget))
        results = {worker['role']: worker for worker in row.get('results', [])}
        for spec in row.get('worker_specs', []):
            worker = results.get(spec['role'])
            state = ('Complete' if worker.get('complete') else 'Partial / needs review') if worker else (
                'Working' if spec['role'] == row.get('current_worker') else 'Waiting')
            parts.append(f"{spec['role']} · {state}\n"
                         f"After: {', '.join(spec.get('depends_on', [])) or 'Starts the research'}\n"
                         f"Deliverable: {spec.get('deliverable') or spec['mission']}")
        if row.get('cancelled'):
            parts.append('Stopping at the next call boundary. An active model or tool call may finish first.')
        for worker in row.get('results', []):
            state = 'Evidence gathered' if worker['ok'] else 'Unverified / incomplete'
            parts.append(f"{worker['role']} · {state}\n"
                         f"Successful tools: {', '.join(worker.get('successful_tools', [])) or 'none'}\n"
                         f"Company coverage: {len(worker.get('coverage', []))}\n"
                         f"Sources: {len(worker.get('sources', []))} · {worker.get('note') or 'No gap recorded'}")
        if row.get('note'):
            parts.append('Note: ' + row['note'])
        if row.get('report'):
            parts.append('Saved report: ' + row['report'])
        return '\n\n'.join(parts)

    def _worker_text(self, row, role):
        spec = next((item for item in row.get('worker_specs', []) if item['role'] == role), None)
        worker = next((item for item in row.get('results', []) if item['role'] == role), None)
        if spec is None:
            return self._team_text(row)
        lines = [f"{role} · {self._worker_state(spec, worker, row)}",
                 f"Mission: {spec.get('mission', '')}",
                 f"Waits for: {', '.join(spec.get('depends_on', [])) or 'nobody — starts the work'}",
                 f"Deliverable: {spec.get('deliverable') or 'its findings for the next workers'}",
                 f"Tools it may use: {', '.join(spec.get('tools', [])) or 'none'} (read and research only)"]
        if not worker:
            lines.append('\nNot run yet.' if row.get('running') else '\nNot run: the team stopped before this worker.')
            return '\n'.join(lines)
        used = list(dict.fromkeys(worker.get('tools_used', [])))
        lines += [f"Tools used: {len(worker.get('tools_used', []))} calls ({', '.join(used) or 'none'})",
                  f"Succeeded with: {', '.join(dict.fromkeys(worker.get('successful_tools', []))) or 'none'}",
                  f"Time: {worker.get('seconds', 0):.0f} s · Sources: {len(worker.get('sources', []))}"
                  f" · Coverage: {len(worker.get('coverage', []))}",
                  f"Gaps: {worker.get('note') or 'none recorded'}"]
        findings = str(worker.get('findings') or '').strip()
        lines.append('\nFindings:\n' + (findings[:6000] + ('\n… (full text in the saved report)' if len(findings) > 6000 else '')
                                        if findings else 'none'))
        sources = worker.get('sources', [])
        if sources:
            lines.append('\nSources:\n' + '\n'.join(f"• {url}" for url in sources[:40])
                         + (f"\n… and {len(sources) - 40} more" if len(sources) > 40 else ''))
        return '\n'.join(lines)

    # -------------------------------------------------------------- actions

    def stop_selected(self):
        selected = self.jobs.selection()
        if selected and self.teams:
            self.teams.cancel(selected[0])
        self.refresh()

    def preview_job_team(self):
        if self.teams:
            self._preview_specs, _ = self.teams.plan('Research 20 AI infrastructure companies and find jobs suitable for me.')
            self.jobs.selection_remove(*self.jobs.selection())
            self.show_details()

    def destroy(self):
        if self._poll is not None:
            self.after_cancel(self._poll)
            self._poll = None
        super().destroy()
