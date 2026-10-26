"""Public professional investigation workspace using the real research pipeline."""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

from app.gui_empty import show_empty
from app.gui_intelligence_parts import FlowRow
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.research.investigations import build_report, export_report, profile_seed, report_text
from app.gui_fonts import DISPLAY


class PersonResearchStudio(tk.Toplevel):
    def __init__(self, research):
        super().__init__(research)
        self.research, self.gui, p = research, research.gui, research.palette
        self.configure(bg=p.BLACK)
        self.title('HAYAT · Professional research')
        self.minsize(720, 550)
        self.transient(research)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self.destroyed, add='+')
        self.bind('<Escape>', lambda _e: self.close())
        self.closed, self.busy, self.job, self.report = False, False, None, None
        self.results, self.generation, self.timer = queue.Queue(), 0, None
        self._worker = None
        self._job_context = ''
        self._job_ticket = self._job_route = None
        self.status = StringVar(self, 'Enter a name or an explicit GitHub handle / profile. Candidates remain separate.')
        top = tk.Frame(self, bg=p.BLACK, padx=12, pady=10)
        top.pack(fill='x')
        tk.Label(top, text='Professional research', bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16)).pack(side='left')
        ttk.Button(top, text='Close', command=self.close).pack(side='right')
        from app.gui_research_links import research_links
        self.workspace_links = research_links(top, self.gui, 'professional', name=lambda: self.query.get(), context=lambda: self.context.get())
        self.report_actions = ttk.Menubutton(top, text='Report actions', style='Hayat.Research.TMenubutton')
        self.report_menu = tk.Menu(self.report_actions, tearoff=False, bg=p.PANEL_2, fg=p.WHITE,
                                   activebackground=p.GOLD_SOFT, activeforeground=p.BLACK)
        self.report_actions.configure(menu=self.report_menu)
        self.report_actions.pack(side='right', padx=4)
        self.report_items = {}
        for title, action in (('Save investigation', self.save), ('History / authorized records', self.history),
                              ('Export JSON', lambda: self.export('.json')), ('Export PDF', lambda: self.export('.pdf')),
                              ('Verify owner presence', self.verify)):
            self.report_menu.add_command(label=title, command=action)
            self.report_items[title] = self.report_menu.index('end')
        self.query_hint = StringVar(self, 'Known name, explicit GitHub handle or professional profile')
        tk.Label(self, textvariable=self.query_hint, bg=p.BLACK, fg=p.MUTED, anchor='w').pack(fill='x', padx=12, pady=(0, 3))
        self.query = ttk.Entry(self)
        self.query.pack(fill='x', padx=12)
        self.query.bind('<Return>', lambda _e: self.search())
        mode_row = self.mode_row = tk.Frame(self, bg=p.BLACK)
        mode_row.pack(fill='x', padx=12, pady=(6, 0))
        mode_row.columnconfigure(1, weight=1)
        self.input_mode = StringVar(self, 'Name / professional profile')
        self.mode_combo = ttk.Combobox(mode_row, textvariable=self.input_mode, state='readonly', width=31,
                     values=('Name / professional profile', 'Company / organization', 'Public business contact'))
        self.mode_combo.grid(row=0, column=0, sticky='w')
        self.public_contact = BooleanVar(self, False)
        self.contact_consent = tk.Checkbutton(mode_row, text='Contact is already publicly listed for business',
            variable=self.public_contact, bg=p.BLACK, fg=p.WHITE, activebackground=p.BLACK, activeforeground=p.WHITE,
            selectcolor=p.PANEL, anchor='w', justify='left', wraplength=350)
        self.contact_consent.grid(row=0, column=1, sticky='w', padx=8)
        fields = tk.Frame(self, bg=p.BLACK, padx=12)
        fields.pack(fill='x', pady=6)
        self.context, self.profile = StringVar(self), StringVar(self)
        for index, (label, value) in enumerate((('Role / organization', self.context), ('Profile URL', self.profile))):
            tk.Label(fields, text=label, bg=p.BLACK, fg=p.MUTED).grid(row=index, column=0, sticky='w', padx=(0, 8))
            ttk.Entry(fields, textvariable=value).grid(row=index, column=1, sticky='ew', pady=3)
        fields.columnconfigure(1, weight=1)
        from app.gui_intelligence_parts import FlowRow
        actions = self.actions = FlowRow(self, p.BLACK, gap=6, height=34)
        actions.pack(fill='x', padx=12)
        self.buttons = {}
        for title, command in (('Research', self.search), ('Cancel', self.cancel), ('Compare selected', self.compare),
                               ('Open source', self.open_source)):
            button = ttk.Button(actions, text=title, command=command)
            actions.add(button)
            self.buttons[title] = button
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                    wraplength=760, justify='left', anchor='w')
        self.status_label.pack(fill='x', padx=12, pady=8)
        self.progress = ttk.Progressbar(self, mode='indeterminate', style='Hayat.Horizontal.TProgressbar')
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True, padx=12, pady=(0, 12))
        sources = tk.Frame(self.tabs, bg=p.PANEL)
        self.tabs.add(sources, text='Sources')
        self.sources = ttk.Treeview(sources, columns=('publisher', 'match', 'date'), show='tree headings', selectmode='extended', height=6)
        for column, title, width in (('#0', 'Source candidate', 340), ('publisher', 'Publisher', 160),
                                     ('match', 'Context', 100), ('date', 'Retrieved', 140)):
            self.sources.heading(column, text=title)
            self.sources.column(column, width=width, minwidth=90)
        scroll = ttk.Scrollbar(sources, command=self.sources.yview)
        scroll.pack(side='right', fill='y')
        horizontal = ttk.Scrollbar(sources, orient='horizontal', command=self.sources.xview)
        horizontal.pack(side='bottom', fill='x')
        self.sources.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.sources.pack(fill='both', expand=True)
        show_empty(self.sources, 'Public professional source candidates appear here after you start research.\n'
                   'Each stays separate; identity is never assumed.', p)
        self.sources.bind('<<TreeviewSelect>>', lambda _e: (self.show_evidence(), self._buttons()))
        self.sources.bind('<Double-Button-1>', lambda _e: self.open_source())
        self.panels = {}
        for key, title in (('evidence', 'Evidence'), ('timeline', 'Timeline'),
                           ('report', 'Report')):
            panel = DarkScrolledText(self.tabs, wrap='word', bg=p.PANEL, fg=p.WHITE, state='disabled', height=10, font=('Segoe UI', 10))
            self.tabs.add(panel, text=title)
            self.panels[key] = panel
        comparison = tk.PanedWindow(self.tabs, orient='horizontal', bg=p.PANEL, sashwidth=8, showhandle=True)
        self.comparison_sides = []
        for _ in range(2):
            side = DarkScrolledText(comparison, wrap='word', bg=p.PANEL, fg=p.WHITE, state='disabled',
                                    width=36, height=10, font=('Segoe UI', 10))
            comparison.add(side.frame, stretch='always', minsize=220)
            self.comparison_sides.append(side)
        self.panels['comparison'] = comparison
        self.tabs.add(comparison, text='Compare')
        graph_page = tk.Frame(self.tabs, bg=p.PANEL)
        self.graph = tk.Canvas(graph_page, bg=p.PANEL, highlightthickness=0)
        graph_scroll = ttk.Scrollbar(graph_page, command=self.graph.yview)
        graph_scroll.pack(side='right', fill='y')
        self.graph.configure(yscrollcommand=graph_scroll.set)
        self.graph.pack(fill='both', expand=True)
        self.tabs.add(graph_page, text='Relationships')
        self.graph.bind('<Configure>', lambda _e: self.draw_graph())
        from app.gui_motion import attach
        attach(self.graph)
        self.bind('<Configure>', self._fit)
        self.query.bind('<KeyRelease>', lambda _e: self._buttons(), add='+')
        self.input_mode.trace_add('write', lambda *_a: self._mode_changed())
        self.public_contact.trace_add('write', lambda *_a: self._buttons())
        self._mode_changed()
        place_over(self, self.gui.root, (1000, 760))
        self.poll()

    def _fit(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(300, event.width - 30))
            self.contact_consent.configure(wraplength=max(180, event.width - 330))

    def _mode_changed(self):
        business = self.input_mode.get() == 'Public business contact'
        self.contact_consent.grid() if business else self.contact_consent.grid_remove()
        self.public_contact.set(False)
        self.query_hint.set('Already-public business email or organization phone route' if business else
                            'Organization name' if self.input_mode.get() == 'Company / organization' else
                            'Known name, explicit GitHub handle or professional profile')
        self._buttons()

    def _buttons(self):
        if self.closed:
            return
        working = self.busy or bool(self._worker and self._worker.is_alive()) or self.job is not None
        selection = self.sources.selection()
        for title, button in self.buttons.items():
            enabled = not working
            if title == 'Cancel':
                enabled = self.busy or bool(self.job and self.job.status == 'running')
            elif title == 'Research':
                enabled = enabled and bool(self.query.get().strip()) and (self.input_mode.get() != 'Public business contact' or self.public_contact.get())
            elif title == 'Compare selected':
                enabled = enabled and self.report is not None and len(selection) == 2
            elif title == 'Open source':
                enabled = enabled and self.report is not None and bool(selection)
            button.configure(state='normal' if enabled else 'disabled')
        for title, index in self.report_items.items():
            enabled = not working and (self.report is not None if title in {'Save investigation', 'Export JSON', 'Export PDF'} else True)
            self.report_menu.entryconfigure(index, state='normal' if enabled else 'disabled')

    def _network_guard(self):
        runtime = self.gui.runtime
        modes = set(getattr(getattr(runtime, 'session', None), 'effective_modes', ()))
        if getattr(getattr(runtime, 'authority', None), 'privacy_mode', False) or modes & {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
            raise PermissionError('Privacy or local-only mode blocks public research.')

    def work(self, title, operation, complete, *, network=False):
        if self.busy or (self._worker and self._worker.is_alive()) or self.job is not None:
            self.status.set('Wait for the current operation or cancel it first.')
            return False
        try:
            ticket = self.gui.runtime.registry.interlock.ticket()
            if network:
                self._network_guard()
        except Exception as error:
            self.status.set(str(error)[:350])
            return False
        from app.privacy_masks import ROUTE
        route = ROUTE.snapshot()
        self.busy = True
        self.generation += 1
        generation = self.generation
        self.status.set(title)
        self.progress.pack(fill='x', padx=12, pady=(0, 6), before=self.tabs)
        self.progress.start(20)
        def run():
            try:
                if self.closed or generation != self.generation:
                    raise InterruptedError('The operation was cancelled before it started.')
                self.gui.runtime.registry.interlock.check(ticket)
                if network:
                    self._network_guard()
                    if ROUTE.snapshot() != route:
                        raise PermissionError('Privacy route changed before the operation started.')
                value = operation()
                if self.closed or generation != self.generation:
                    raise InterruptedError('The operation was cancelled; its result was discarded.')
                self.gui.runtime.registry.interlock.check(ticket)
                if network:
                    self._network_guard()
                    if ROUTE.snapshot() != route:
                        raise PermissionError('Privacy route changed; start a new operation.')
                self.results.put((generation, complete, value, None, ticket, route if network else None))
            except Exception as error:
                self.results.put((generation, complete, None, str(error)[:350], ticket, route if network else None))
        self._worker = threading.Thread(target=run, name='hayat-professional-research', daemon=True)
        self._worker.start()
        self._buttons()
        return True

    def search(self):
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        if self.busy or (self._worker and self._worker.is_alive()) or self.job is not None:
            self.status.set('Wait for the current operation to finish.')
            return
        try:
            ticket = self.gui.runtime.registry.interlock.ticket()
            self._network_guard()
        except Exception as error:
            self.status.set(str(error)[:350])
            return
        name, context, profile = self.query.get().strip(), self.context.get().strip(), self.profile.get().strip()
        if not name:
            self.status.set('Enter a known name, partial name, @GitHub handle or GitHub profile URL.')
            return
        if self.input_mode.get() == 'Public business contact':
            from app.research.investigations import contact_sources
            consent = self.public_contact.get()
            self.work('Finding public business directory sources…', lambda: contact_sources(
                self.gui.runtime.research, name, context, publicly_listed=consent), self.paint, network=True)
            return
        if self.input_mode.get() == 'Company / organization':
            if len(name) > 240 or any(ord(char) < 32 for char in name):
                self.status.set('Enter an organization name under 240 characters.')
                return
            self._job_context = context
            from app.privacy_masks import ROUTE
            self._job_ticket, self._job_route = ticket, ROUTE.snapshot()
            self.job = self.gui.runtime.research_jobs.start(
                f'{name} {context} official organization public projects and announcements'.strip(), level='deep')
            self.progress.pack(fill='x', padx=12, pady=(0, 6), before=self.tabs)
            self.progress.start(20)
            self.status.set('Searching public organization sources…')
            self._buttons()
            return
        def run():
            manager = self.gui.runtime.research
            resolved, url = profile_seed(name, profile, getattr(manager.fetcher, 'get_json', None))
            from app.research.people import PersonRequest
            request = PersonRequest(resolved, context, url)
            return request
        self.work('Resolving the professional search input…', run, self.started, network=True)

    def started(self, request):
        from app.privacy_masks import ROUTE
        self._job_ticket = self.gui.runtime.registry.interlock.ticket()
        self._job_route = ROUTE.snapshot()
        self.job = self.gui.runtime.research_jobs.start(request.name, asked=request.name, level='deep', person={
            'name': request.name, 'context': request.context, 'profile_url': request.profile_url})
        self._job_context = request.context
        self.progress.pack(fill='x', padx=12, pady=(0, 6), before=self.tabs)
        self.progress.start(20)
        self.status.set('Searching public professional sources and scholarly records…')
        self._buttons()

    def poll(self):
        if self.closed:
            return
        for _ in range(10):
            try:
                generation, complete, value, error, ticket, route = self.results.get_nowait()
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
                try:
                    self.gui.runtime.registry.interlock.check(ticket)
                    if route is not None:
                        from app.privacy_masks import ROUTE
                        self._network_guard()
                        if ROUTE.snapshot() != route:
                            raise PermissionError('Privacy route changed; pending result discarded.')
                    complete(value)
                except Exception as problem:
                    self.status.set(str(problem)[:350])
        if self.job:
            job = self.job
            if job.status == 'running':
                self.status.set(f'Searching · {int(job.elapsed)} seconds · Cancel remains available')
            else:
                self.job = None
                self.progress.stop()
                self.progress.pack_forget()
                if job.status == 'done' and job.result is not None:
                    try:
                        from app.privacy_masks import ROUTE
                        self.gui.runtime.registry.interlock.check(self._job_ticket)
                        self._network_guard()
                        if ROUTE.snapshot() != self._job_route:
                            raise PermissionError('Privacy route changed; search result discarded.')
                        self.paint(build_report(job.result, self._job_context))
                    except Exception as error:
                        self.status.set(str(error)[:350])
                else:
                    self.status.set(job.error or 'Search cancelled.')
        self._buttons()
        self.timer = self.after(150, self.poll)

    def paint(self, report):
        from app.privacy_masks import ROUTE
        report.setdefault('tor_required', ROUTE.proxy is not None)
        self.report = report
        self.sources.delete(*self.sources.get_children())
        for source in report['sources']:
            self.sources.insert('', 'end', iid=source['id'], text=source['title'], values=(source['publisher'],
                                ', '.join(source['context_matches']) or 'unresolved', source['retrieved_at']))
        show_empty(self.sources, 'No public source matched this name and context on the pages searched.\n'
                   'Nothing was assumed. Add an organization or field to narrow it.', self.research.palette)
        self.set_text('report', report_text(report))
        self.set_text('timeline', '\n\n'.join(f"{r['year']} · {r['status']}\n{r['text']}\n" + ', '.join(r['source_ids'])
                                               for r in report['timeline']) or 'No dated, supported professional claim was found.')
        self.status.set(f"{len(report['sources'])} source candidates · {len(report['claims'])} cited claims · identity unverified"
                        + (f" · {len(report['provider_errors'])} provider errors (see report)" if report['provider_errors'] else ''))
        self.draw_graph()
        self.show_evidence()
        self._buttons()

    def set_text(self, key, text):
        panel = self.panels[key]
        if panel.get('1.0', 'end-1c') == text:
            return
        scroller = getattr(panel, '_smooth_scroller', None)
        if scroller is not None:
            scroller.cancel()
        panel.configure(state='normal')
        panel.delete('1.0', 'end')
        panel.insert('end', text)
        panel.configure(state='disabled')

    def find_contacts(self):
        """Prefill an explicit discovery form; no request or saved-person merge."""
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        window = self.gui._open_networking()
        if window.find_professional_contacts(name=self.query.get().strip(),
                                             organization=self.context.get().strip(),
                                             official_url=self.profile.get().strip()):
            self.status.set('Contact discovery opened. Check the organization and source before starting the search.')
        else:
            self.status.set(window.status.get())

    def show_evidence(self):
        if not self.report:
            return
        selected = set(self.sources.selection())
        lines = []
        for claim in self.report['claims']:
            evidence = [e for e in claim['evidence'] if not selected or e['source_id'] in selected]
            if evidence:
                lines += [claim['status'] + ' · evidence confidence ' + str(claim['confidence']), claim['text']]
                lines += [f"[{e['source_id']}] {e['excerpt']}\n{e['url']}\nRetrieved {e['retrieved_at']}" for e in evidence]
                lines += ['Contrary: ' + e['excerpt'] + '\n' + e['url'] for e in claim['contradictions']]
                lines.append('')
        self.set_text('evidence', '\n'.join(lines) or 'No claim passed citation checks for this selection.')

    def compare(self):
        if not self.report or len(self.sources.selection()) != 2:
            self.status.set('Select exactly two source candidates with Ctrl / Shift, then Compare.')
            return
        ids = list(self.sources.selection())
        self.report['selected_candidates'] = ids
        for panel, sid in zip(self.comparison_sides, ids):
            source = next(s for s in self.report['sources'] if s['id'] == sid)
            lines = [source['title'], source['url'], 'Retrieved: ' + source['retrieved_at'],
                     'Context matches: ' + (', '.join(source['context_matches']) or 'none'),
                     'Identity unverified. This is an owner-selected source candidate.', '']
            for claim in self.report['claims']:
                evidence = [e for e in claim['evidence'] if e['source_id'] == sid]
                if evidence:
                    lines += [claim['status'] + ': ' + claim['text']]
                    lines += [e['excerpt'] for e in evidence]
                    lines.append('')
            panel.configure(state='normal')
            panel.delete('1.0', 'end')
            panel.insert('end', '\n'.join(lines))
            panel.configure(state='disabled')
        self.status.set('Comparing two separate source candidates. Drag the divider to change column widths.')
        self.tabs.select(self.panels['comparison'])

    def draw_graph(self):
        self.graph.delete('all')
        if not self.report:
            return
        width = max(400, self.graph.winfo_width())
        sources = self.report['sources'][:20]
        self.graph.create_text(15, 15, text='Publisher → retrieved source. Lines do not imply employment or a personal relationship.',
                               anchor='nw', fill=self.gui.MUTED, width=width-30)
        for i, source in enumerate(sources):
            y = 65 + i * 45
            self.graph.create_line(width*.28, y, width*.57, y, fill=self.gui.GOLD_SOFT, arrow='last')
            self.graph.create_text(15, y, text=source['publisher'], anchor='w', fill=self.gui.WHITE, width=width*.25)
            item = self.graph.create_text(width*.6, y, text=source['title'], anchor='w', fill=self.gui.GOLD_SOFT, width=width*.36)
            self.graph.tag_bind(item, '<Button-1>', lambda _e, sid=source['id']: self.select_source(sid))
        self.graph.configure(scrollregion=(0, 0, width, max(200, 80 + len(sources)*45)))

    def select_source(self, sid):
        self.sources.selection_set(sid)
        self.sources.see(sid)
        self.show_evidence()
        self.tabs.select(self.panels['evidence'])

    def open_source(self):
        if not self.research._search_allowed():
            self.status.set(self.research.detail_status.get())
            return
        selected = self.sources.selection()
        if not self.report or not selected:
            self.status.set('Select a source first.')
            return
        source = next(s for s in self.report['sources'] if s['id'] == selected[0])
        def open_page():
            from app.research.safety import validate_public_url
            from app.privacy_masks import ROUTE
            required = self.report.get('tor_required') is True or ROUTE.proxy is not None
            if required and ROUTE.proxy is None:
                raise PermissionError('Start Tor before opening this Tor-sourced result; direct fallback is disabled.')
            target = validate_public_url(source['url'], via_tor=required)
            launch = getattr(getattr(self.gui.runtime, 'research', None), 'search_browser', None)
            if callable(launch):
                outcome = launch(target, required)
                if not isinstance(outcome, dict) or outcome.get('opened') is not True:
                    raise RuntimeError('The browser did not confirm that it opened this public source.')
            elif required:
                raise RuntimeError('A masked source browser is unavailable; no direct browser fallback was used.')
            elif not webbrowser.open(target):
                raise RuntimeError('The browser did not confirm that it opened this public source.')
        self.work('Opening selected public source…', open_page, lambda _v: self.status.set('Opened selected source.'), network=True)

    def export(self, extension):
        if not self.report:
            self.status.set('Run or open an investigation first.')
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=extension,
                                          initialfile='professional-research' + extension,
                                          filetypes=[(extension[1:].upper(), '*' + extension)])
        if path:
            report = dict(self.report)
            self.work('Exporting report…', lambda: export_report(report, path),
                      lambda _v: self.status.set('Report exported. This explicit export is a plaintext file.'))

    def save(self):
        if not self.report:
            self.status.set('Run or open an investigation first.')
            return
        self.history(save_report=self.report)

    def history(self, save_report=None):
        RecordLibrary(self, save_report)

    def verify(self):
        def run():
            result = self.gui.runtime.registry.execute('verify_owner_present', {'reason': 'Owner opted in to research record access'})
            if not result.success:
                raise RuntimeError(result.error)
            return result.output
        def show(value):
            from app.plain_output import describe

            self.status.set('Signed-in owner presence only; no researched person identity is verified. '
                            + describe(value).replace('\n', ' · '))
        self.work('Waiting for Windows Hello…', run, show)

    def cancel(self):
        self.generation += 1
        self.busy = False
        if self.job:
            self.gui.runtime.research_jobs.cancel(self.job.job_id)
            self.job = None
        self.progress.stop()
        self.progress.pack_forget()
        self.status.set('Cancelled. In-flight public requests may finish; their results will be discarded.')
        self._buttons()

    def close(self):
        self.cancel()
        self.closed = True
        if self.timer:
            self.after_cancel(self.timer)
        self.destroy()

    def destroyed(self, event):
        if event.widget is not self or self.closed:
            return
        self.closed = True
        self.generation += 1
        if self.job:
            self.gui.runtime.research_jobs.cancel(self.job.job_id)
        if self.timer:
            self.after_cancel(self.timer)


class RecordLibrary(tk.Toplevel):
    def __init__(self, studio, save_report=None):
        super().__init__(studio)
        self.studio, self.report, self.token = studio, save_report, None
        from app.research.record_vault import RecordVault
        self.vault = RecordVault(studio.gui.runtime.memory.path.parent / 'authorized-research')
        p = studio.gui
        self.title('HAYAT · Encrypted research & authorized records')
        self.configure(bg=p.BLACK)
        self.consent = BooleanVar(self, False)
        self.instructions = tk.Label(self, text='Local Windows-account encrypted storage. Records are never sent to search or AI models.\n'
                 'Confirm ownership / authorization for access. Grants expire after one hour and can be revoked.',
                 bg=p.BLACK, fg=p.WHITE, justify='left', wraplength=630)
        self.instructions.pack(fill='x', padx=12, pady=12)
        ttk.Checkbutton(self, text='I own these records or have explicit authorization to access them', variable=self.consent).pack(anchor='w', padx=12)
        authorization_row = tk.Frame(self, bg=p.BLACK)
        authorization_row.pack(fill='x', padx=12, pady=8)
        tk.Label(authorization_row, text='Authorization scope', bg=p.BLACK, fg=p.MUTED).pack(side='left', padx=(0, 8))
        self.authorization = ttk.Entry(authorization_row)
        self.authorization.pack(side='left', fill='x', expand=True)
        self.authorization.insert(0, 'Describe the scope and basis of your authorization')
        self.authorization.delete(0, 'end')
        bottom = tk.Frame(self, bg=p.BLACK)
        bottom.pack(side='bottom', fill='x')
        self.body = tk.PanedWindow(self, orient='vertical', bg=p.BLACK, sashwidth=7, bd=0)
        self.body.pack(fill='both', expand=True, padx=12, pady=(0, 8))
        records = tk.Frame(self.body, bg=p.PANEL)
        records.rowconfigure(0, weight=1)
        records.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(records, columns=('kind', 'access'), show='tree headings', height=6)
        self.tree.heading('#0', text='Saved record')
        self.tree.heading('kind', text='Type')
        self.tree.heading('access', text='Grant')
        self.tree.column('#0', width=330, minwidth=150)
        self.tree.column('kind', width=150, minwidth=90)
        self.tree.column('access', width=110, minwidth=90)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(records, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(records, orient='horizontal', command=self.tree.xview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        # Wraps at narrow widths; five side-packed buttons clipped "Delete selected".
        bar = FlowRow(bottom, p.BLACK, gap=5, height=34)
        bar.pack(fill='x', padx=12, pady=8)
        for title, command in (('Save report', self.save), ('Import document', self.import_document),
                               ('Authorize & open', self.open), ('Revoke access', self.revoke), ('Delete selected', self.delete)):
            bar.add(ttk.Button(bar, text=title, command=command))
        self.preview = DarkScrolledText(self.body, bg=p.PANEL, fg=p.WHITE, height=6, wrap='word', state='disabled',
                                        font=('Segoe UI', 10))
        self.body.add(records, height=150, minsize=100)
        self.body.add(self.preview.frame, minsize=90)
        self.status = tk.Label(bottom, text='Describe authorization, check consent, then choose an action.',
                               bg=p.BLACK, fg=p.GOLD_SOFT, wraplength=630)
        self.status.pack(fill='x', padx=12, pady=(0, 8))
        self.bind('<Configure>', self._fit)
        self.minsize(560, 480)
        place_over(self, studio.gui.root, (850, 620))
        self.refresh()

    def _fit(self, event):
        if event.widget is self:
            self.status.configure(wraplength=max(250, event.width - 28))
            self.instructions.configure(wraplength=max(250, event.width - 28), text=
                'Encrypted on this Windows account. Nothing is sent to search or AI.\n'
                'Authorize below; grants expire after one hour and can be revoked.' if event.height < 560 else
                'Local Windows-account encrypted storage. Records are never sent to search or AI models.\n'
                'Confirm ownership / authorization for access. Grants expire after one hour and can be revoked.')

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        try:
            for row in self.vault.list():
                self.tree.insert('', 'end', iid=row['id'], text=row['title'], values=(row['kind'], 'active' if row['accessible'] else 'reauthorize'))
        except Exception as error:
            self.status.configure(text=str(error))
        show_empty(self.tree, 'No saved records yet.\nUse Save report or Import document to add one.', self.studio.gui)

    def grant(self):
        self.studio.gui.runtime.registry.interlock.ticket()
        if not self.token:
            self.token = self.vault.grant(self.authorization.get(), consent=self.consent.get())
        return self.token

    def perform(self, operation):
        try:
            operation()
            self.refresh()
        except Exception as error:
            self.status.configure(text=str(error)[:300])

    def save(self):
        if not self.report:
            self.status.configure(text='Open this library with Save investigation to save a report.')
            return
        def save():
            self.vault.put(self.grant(), self.report['query'][:200], json.dumps(self.report), kind='public_investigation')
            self.status.configure(text='Investigation saved with Windows-account encryption.')
        self.perform(save)

    def import_document(self):
        # Validate consent before reading any file bytes.
        try:
            token = self.grant()
        except Exception as error:
            self.status.configure(text=str(error))
            return
        path = filedialog.askopenfilename(parent=self, filetypes=[('Documents', '*.txt *.md *.pdf *.docx')])
        if not path:
            return
        def read():
            attachment = self.studio.gui.runtime.attachments.read(path)
            if not attachment.ok:
                raise ValueError(attachment.error)
            return attachment
        def complete(attachment):
            try:
                self.vault.put(token, attachment.path.name[:200], attachment.text)
                if self.winfo_exists():
                    self.status.configure(text='Authorized document encrypted locally; not remembered or uploaded.')
                    self.refresh()
            except Exception as error:
                if self.winfo_exists():
                    self.status.configure(text=str(error))
        self.studio.work('Reading authorized document locally…', read, complete)

    def selected(self):
        ids = self.tree.selection()
        if not ids:
            raise ValueError('Select a saved record.')
        return ids[0]

    def open(self):
        def read():
            rid, token = self.selected(), self.grant()
            self.vault.reauthorize(token, rid)
            record = self.vault.get(token, rid)
            if record['kind'] == 'public_investigation':
                self.studio.paint(json.loads(record['content']))
                self.status.configure(text='Investigation opened in the research dashboard.')
            else:
                self.preview.configure(state='normal')
                self.preview.delete('1.0', 'end')
                self.preview.insert('end', record['content'])
                self.preview.configure(state='disabled')
                self.status.configure(text='Authorized record opened locally.')
        self.perform(read)

    def revoke(self):
        def revoke():
            if self.tree.selection():
                self.vault.revoke_record(self.selected())
            elif self.token:
                self.vault.revoke(self.token)
            else:
                raise ValueError('Select a record or authorize access first.')
            self.token = None
            self.consent.set(False)
            self.preview.configure(state='normal')
            self.preview.delete('1.0', 'end')
            self.preview.configure(state='disabled')
            self.status.configure(text='Access revoked. New explicit authorization is required to open records.')
        self.perform(revoke)

    def delete(self):
        self.perform(lambda: self.vault.delete(self.selected()))
