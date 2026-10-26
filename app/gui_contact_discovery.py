"""Explicit public professional discovery, source review and local contact saving."""
from __future__ import annotations

from copy import deepcopy
import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.gui_empty import show_empty
from app.gui_intelligence_parts import FlowRow
from app.gui_resources import StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY


_EMPTY_BEFORE = {
    'person': 'Professional candidates appear here after you press Find.\n'
              'Each source match stays a separate candidate; identity is never assumed.',
    'team': 'Organization routes (press office, admissions, contact pages) appear here,\n'
            'kept apart from the person you searched for.',
    'source': 'Every public page checked appears here, with why it was or was not used.',
}
_EMPTY_AFTER = {
    'person': 'No source-supported candidate on the pages checked. Nothing was guessed.\n'
              'Try the official directory URL, or more pages.',
    'team': 'No labelled organization contact route on the pages checked.',
    'source': 'No public page could be read. See the status line for the reason.',
}


def discovery_contact_payload(row, scope, report):
    """Project reviewed evidence into a new book record, never merge identities."""
    if scope not in {'person', 'team'}:
        raise ValueError('Select a professional candidate or team route to save.')
    source_url = row.get('source_url', '')
    source_hash = row.get('source_hash', '')
    source = next((item for item in report.get('sources', [])
                   if item.get('accepted') is True and source_url in {item.get('url'), item.get('final_url')}
                   and item.get('sha256') == source_hash and source_hash), None)
    if source is None:
        raise ValueError('This result has no matching retrieved source evidence. Search again before saving.')
    routes = row.get('channels', []) if scope == 'person' else [row]
    channels = []
    for route in routes:
        if (route.get('kind') not in {'email', 'profile', 'website'}
                or route.get('status') != 'public_professional' or not route.get('value')
                or route.get('source_url') != source_url or route.get('source_hash') != source_hash):
            raise ValueError('A contact route lacks matching public professional source evidence.')
        channels.append({key: route[key] for key in ('kind', 'value', 'status', 'source_url')})
    if not channels:
        raise ValueError('This candidate has no supported public professional contact route to save.')
    if len(channels) > 12:
        raise ValueError('Select a result with at most 12 professional contact routes.')
    name = row.get('name', '') if scope == 'person' else row.get('team_name', '')
    if not name:
        raise ValueError('The selected result needs a source-supported person or team name.')
    notes = ['Reviewed public professional source; identity is not verified.',
             'Scope: ' + ('individual candidate; keep namesakes separate' if scope == 'person' else
                          'organization/team route, not the requested person'),
             'Source: ' + source_url, 'Retrieved: ' + str(row.get('retrieved_at') or row.get('recorded_at') or source.get('retrieved_at', '')),
             'Source SHA-256: ' + source_hash, 'Published context: ' + str(row.get('snippet', ''))[:2400]]
    for route in routes:
        if route.get('snippet') and route.get('snippet') != row.get('snippet'):
            notes.append('Route context: ' + str(route['snippet'])[:800])
    return {'name': name, 'organization': row.get('organization', report.get('organization', '')),
            'role': row.get('role', '') if scope == 'person' else 'Public team contact route',
            'source_url': source_url, 'channels': channels, 'notes': '\n'.join(notes)[:8000]}


class ContactDiscoveryWindow(tk.Toplevel):
    def __init__(self, networking):
        super().__init__(networking)
        self.withdraw()
        self.networking, self.gui, self.palette = networking, networking.gui, networking.palette
        p = self.palette
        self._closed, self.busy, self.generation = False, False, 0
        self._cancel = threading.Event()
        self._results = queue.Queue()
        self._poll_job = None
        self.report, self._rows, self._saved = {}, {}, set()
        self._operation = ''
        self.title('HAYAT · Find professional contacts')
        self.configure(bg=p.BLACK)
        self.minsize(660, 600)
        self.transient(networking)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _e: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self.name = StringVar(self, networking.fields['name'].get().strip())
        self.organization = StringVar(self, networking.fields['organization'].get().strip())
        self.official_url = StringVar(self, networking.fields['source_url'].get().strip())
        self.max_pages = StringVar(self, '4')
        self.depth = StringVar(self, 'normal')
        self.route = StringVar(self, 'Public web')
        self.status = StringVar(self, 'Enter a known name and organization/work context. Search starts only when you press Find.')

        head = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        head.pack(fill='x')
        tk.Label(head, text='Find professional contacts', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side='left')
        ttk.Button(head, text='Close', command=self.close).pack(side='right')
        from app.gui_research_links import research_links
        self.workspace_links = research_links(head, self.gui, 'contacts', name=self.name.get, context=self.organization.get)
        self.guidance = tk.Label(self, text='Public professional routes with source evidence. Candidates remain separate; '
                                 'team routes belong to the organization. Nothing is sent or saved automatically.',
                                 bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=760)
        self.guidance.pack(fill='x', padx=14, pady=(0, 6))
        fields = tk.Frame(self, bg=p.BLACK)
        fields.pack(fill='x', padx=14)
        fields.columnconfigure(1, weight=1)
        self.entries = {}
        for index, (key, label, variable) in enumerate((
                ('name', 'Known name *', self.name), ('organization', 'Organization / work context *', self.organization),
                ('official_url', 'Official directory URL (optional)', self.official_url))):
            tk.Label(fields, text=label, bg=p.BLACK, fg=p.MUTED, anchor='w').grid(row=index, column=0, sticky='w', padx=(0, 8), pady=4)
            entry = ttk.Entry(fields, textvariable=variable)
            entry.grid(row=index, column=1, sticky='ew', pady=4)
            entry.bind('<Return>', lambda _e: self.search())
            self.entries[key] = entry
        actions = FlowRow(self, p.BLACK, gap=5, height=36)
        actions.pack(fill='x', padx=14, pady=6)
        self.search_button = ttk.Button(actions, text='Find professional contacts', command=self.search, style='HayatAccent.TButton')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.open_button = ttk.Button(actions, text='Open public source', command=self.open_source, state='disabled')
        self.save_button = ttk.Button(actions, text='Save selected to private book', command=self.save_selected, state='disabled')
        for button in (self.search_button, self.cancel_button, self.open_button, self.save_button):
            actions.add(button)
        handoffs = FlowRow(self, p.BLACK, gap=5, height=32)
        handoffs.pack(fill='x', padx=14, pady=(0, 4))
        self.image_button = ttk.Button(handoffs, text='Image-source checks', command=lambda: self.research_handoff('image'))
        self.onion_button = ttk.Button(handoffs, text='Organization onion sources', command=lambda: self.research_handoff('onion'))
        handoffs.add(self.image_button)
        handoffs.add(self.onion_button)
        limits = tk.Frame(self, bg=p.BLACK)
        limits.pack(fill='x', padx=14)
        tk.Label(limits, text='Public pages to check', bg=p.BLACK, fg=p.MUTED).pack(side='left')
        ttk.Combobox(limits, textvariable=self.max_pages, values=tuple(str(n) for n in range(1, 13)),
                     state='readonly', width=3).pack(side='left', padx=8)
        self.depth_combo = ttk.Combobox(limits, textvariable=self.depth, values=('normal', 'deep'), state='readonly', width=7)
        self.depth_combo.pack(side='left', padx=(0, 8))
        self.depth_combo.bind('<<ComboboxSelected>>', lambda _e: self.max_pages.set('12' if self.depth.get() == 'deep' else '4'))
        self.route_combo = ttk.Combobox(limits, textvariable=self.route, values=('Public web', 'Public web through Tor'), state='readonly', width=24)
        self.route_combo.pack(side='left')
        self.status_label = tk.Label(self, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                     anchor='w', justify='left', wraplength=760)
        self.status_label.pack(fill='x', padx=14, pady=6)
        self.progress = ttk.Progressbar(self, mode='indeterminate', style='Hayat.Horizontal.TProgressbar')
        self.body = tk.PanedWindow(self, orient='vertical', bg=p.BLACK, sashwidth=7, bd=0)
        self.body.pack(fill='both', expand=True, padx=14, pady=(0, 12))
        self.tabs = ttk.Notebook(self.body)
        self.tables = {}
        for scope, label, columns in (
                ('person', 'Candidates', (('organization', 'Organization', 200), ('routes', 'Public routes', 100))),
                ('team', 'Team routes', (('route', 'Public route', 290),)),
                ('source', 'Sources & limits', (('status', 'Status', 100), ('reason', 'Result', 280)))):
            page = tk.Frame(self.tabs, bg=p.PANEL)
            self.tabs.add(page, text=label)
            table = ttk.Treeview(page, columns=tuple(item[0] for item in columns), show='tree headings', selectmode='browse', height=5)
            table.heading('#0', text='Candidate' if scope == 'person' else 'Team' if scope == 'team' else 'Public source')
            table.column('#0', width=230, minwidth=160)
            for key, title, width in columns:
                table.heading(key, text=title)
                table.column(key, width=width, minwidth=80)
            networking._table_scrollbars(page, table)
            table.bind('<<TreeviewSelect>>', lambda _e: self.show_selected())
            table.bind('<Double-Button-1>', lambda _e: self.open_source())
            self.tables[scope] = table
        self.tabs.bind('<<NotebookTabChanged>>', lambda _e: self.show_selected())
        self.evidence = DarkScrolledText(self.body, bg=p.PANEL, fg=p.WHITE, wrap='word', height=8,
                                         state='disabled', font=('Segoe UI', 10), padx=10, pady=8)
        self.body.add(self.tabs, minsize=110, height=170)
        self.body.add(self.evidence.frame, minsize=90)
        self._show_empty_tables(_EMPTY_BEFORE)
        self._set_evidence('Run a search, then select one candidate or a team route to review its published context and sources.')
        self.bind('<Configure>', self._fit, add='+')
        place_over(self, self.gui.root, (900, 760))
        self.deiconify()
        self.lift()
        self._poll_job = self.after(80, self._poll)

    def _fit(self, event):
        if event.widget is self:
            if event.height < 700 and self.guidance.winfo_manager():
                self.guidance.pack_forget()
            elif event.height >= 700 and not self.guidance.winfo_manager():
                self.guidance.pack(fill='x', padx=14, pady=(0, 6), before=self.entries['name'].master)
            for label in (self.guidance, self.status_label):
                label.configure(wraplength=max(300, event.width - 35))

    def _network_guard(self):
        runtime = self.gui.runtime
        modes = set(getattr(getattr(runtime, 'session', None), 'effective_modes', ()))
        if getattr(getattr(runtime, 'authority', None), 'privacy_mode', False) or modes & {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
            raise PermissionError('Privacy or local-only mode is on. Turn it off before public contact discovery.')

    def _start(self, operation, title, work, *, network=False):
        if self.busy or self._closed:
            return False
        previous = getattr(self, '_worker', None)
        if previous is not None and previous.is_alive():
            self.status.set('The cancelled request is still finishing its in-flight call. Wait before starting another operation.')
            return False
        try:
            interlock, ticket = self.networking._ticket()
            if network:
                self._network_guard()
        except Exception as error:
            self.status.set(str(error)[:350])
            return False
        from app.privacy_masks import ROUTE
        route = ROUTE.snapshot()
        self.generation += 1
        generation, cancel = self.generation, threading.Event()
        self._cancel, self.busy, self._operation = cancel, True, operation
        self.status.set(title)
        self.progress.pack(fill='x', padx=14, pady=(0, 5), before=self.body)
        self.progress.start(15)
        self._buttons()
        def check():
            if cancel.is_set() or self._closed:
                raise InterruptedError('Contact operation was cancelled.')
            if interlock:
                interlock.check(ticket)
            if network:
                self._network_guard()
                if ROUTE.snapshot() != route:
                    raise PermissionError('Privacy route changed. Start a new contact operation.')
        def progress(value):
            self._results.put((generation, 'progress', value, ''))
        def run():
            try:
                check()
                value = work(check, progress)
                check()
                self._results.put((generation, operation, value, ''))
            except Exception as error:
                self._results.put((generation, operation, None, str(error)[:500]))
        self._worker = threading.Thread(target=run, name='hayat-public-contacts', daemon=True)
        self._worker.start()
        return True

    def search(self):
        name, organization, url = self.name.get().strip(), self.organization.get().strip(), self.official_url.get().strip()
        if not name or not organization:
            self.status.set('Enter a known name and its organization or work context; guessed private contacts are not searched.')
            return False
        try:
            pages = int(self.max_pages.get())
        except ValueError:
            self.status.set('Choose a public-page limit from 1 to 12.')
            return False
        if not 1 <= pages <= 12 or self.depth.get() not in {'normal', 'deep'} or self.route.get() not in {'Public web', 'Public web through Tor'}:
            self.status.set('Choose a valid depth, public route and page limit (1 to 12).')
            return False
        arguments = {'name': name, 'organization': organization, 'official_url': url, 'max_pages': pages}
        if self.depth.get() == 'deep':
            arguments['depth'] = 'deep'
        if self.route.get() == 'Public web through Tor':
            arguments['tor_only'] = True
        def work(check, progress):
            from app.research.public_contacts import contact_controls
            with contact_controls(check=check, progress=progress):
                result = self.gui.runtime.registry.execute('public_contact_search', arguments)
            if not result.success:
                raise RuntimeError(result.error or 'Public contact discovery did not complete.')
            if not isinstance(result.output, dict):
                raise ValueError('Contact discovery returned no structured evidence report.')
            return result.output
        return self._start('search', 'Searching public professional sources…', work, network=True)

    def cancel(self):
        if self.busy and self._operation == 'save':
            self.status.set('The local save is completing. It cannot be undone by cancelling a search.')
            return
        self._cancel.set()
        self.generation += 1
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self._buttons()
        self.status.set('Cancelled. In-flight public requests may finish; their results will be discarded.')

    def _poll(self):
        if self._closed:
            return
        for _ in range(30):
            try:
                generation, operation, value, error = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            if operation == 'progress':
                if isinstance(value, dict):
                    self.status.set(str(value.get('message') or value.get('stage') or 'Checking public sources…')[:350])
                continue
            self.busy = False
            self.progress.stop()
            self.progress.pack_forget()
            if error:
                self.status.set(error)
            elif operation == 'search':
                self.paint(value)
            elif operation == 'save':
                record, key, existed = value
                self._saved.add(key)
                self.networking.refresh()
                self.status.set(('Already saved' if existed else 'Saved new contact') + ': ' + record['name']
                                + '. Source evidence retained locally; nothing was sent.')
            elif operation == 'open':
                self.status.set('Selected public source opened. Opening it does not verify identity or send outreach.')
            self._buttons()
        self._poll_job = self.after(80, self._poll)

    def paint(self, report):
        self.report, self._rows, self._saved = report, {}, set()
        for table in self.tables.values():
            table.delete(*table.get_children())
        for scope, values in (('person', report.get('candidates', [])), ('team', report.get('team_routes', [])),
                              ('source', report.get('sources', []))):
            for index, row in enumerate(values):
                key = f'{scope}:{index}'
                self._rows[key] = (scope, row)
                label = row.get('name', '') if scope == 'person' else row.get('team_name', '') if scope == 'team' else row.get('final_url') or row.get('url', '')
                columns = ((row.get('organization', ''), len(row.get('channels', []))) if scope == 'person' else
                           (row.get('value', ''),) if scope == 'team' else (row.get('status', ''), row.get('reason', '')))
                self.tables[scope].insert('', 'end', iid=key, text=label, values=columns)
        self._show_empty_tables(_EMPTY_AFTER)
        people, teams = len(report.get('candidates', [])), len(report.get('team_routes', []))
        self.status.set(f'{people} professional candidate(s), {teams} team route(s). '
                        + ('Some sources could not be checked. ' if report.get('errors') else '')
                        + 'Review evidence before explicitly saving a selection.')
        self._set_evidence(self._summary())
        self._buttons()

    def _show_empty_tables(self, messages):
        for scope, table in self.tables.items():
            show_empty(table, messages[scope], self.palette)

    def _summary(self):
        lines = ['PUBLIC PROFESSIONAL DISCOVERY', f"Known name: {self.report.get('name', '')}",
                 'Context: ' + str(self.report.get('organization', '')), 'Identity verified: no',
                 'Separate candidate records may describe namesakes. A public route is not consent or willingness to help.']
        lines += ['Route: ' + str(self.report.get('route', (self.report.get('scope') or {}).get('route', 'Configured public route'))),
                  'Onion/private-record search: no. Organization onion discovery is a separate manual handoff.']
        coverage = self.report.get('coverage') or {}
        if coverage:
            lines += [f"Queries: {self.report.get('searches_completed', 0)} / {coverage.get('queries_planned', '?')}; "
                      f"pages retrieved: {coverage.get('pages_retrieved', 0)}; source hosts: {coverage.get('distinct_source_hosts', 0)}.",
                      f"Unchecked leads: {coverage.get('unvisited_leads', 0)}; stop reason: {coverage.get('stop_reason', 'unknown')}.",
                      'Absent elsewhere: unknown. This is bounded public-source coverage.']
            for query in coverage.get('queries', []):
                lines.append('Query: ' + str(query.get('query', '')) + ' — ' + str(query.get('status', 'unknown')))
        for error in self.report.get('errors', []):
            lines.append('Source error: ' + (str(error.get('error', error)) if isinstance(error, dict) else str(error)))
        lines.extend('Limit: ' + str(item) for item in self.report.get('limits', []))
        if not self.report.get('candidates') and not self.report.get('team_routes'):
            lines.append('No source-supported public professional route was found. No address was guessed.')
        return '\n'.join(lines)

    def research_handoff(self, kind):
        """Open a reviewable next workflow without searching or forwarding a photo/name."""
        launcher = getattr(self.gui, '_open_searches', None)
        if launcher is None:
            self.status.set('Open Searches from the main window to continue public research.')
            return False
        if kind == 'onion' and not self.organization.get().strip():
            self.status.set('Enter an organization for its public onion-source discovery.')
            return False
        launcher()
        studio = getattr(self.gui, '_research_studio', None)
        if studio is None:
            self.status.set('Searches could not be opened.')
            return False
        if kind == 'image':
            studio.open_image_research()
        elif kind == 'onion':
            studio.search_kind.set('Onion (Tor)')
            studio.query.delete(0, 'end')
            studio.query.insert(0, self.organization.get().strip())
            studio._show_person_fields()
        else:
            return False
        self.status.set('Prepared ' + ('image-source checks; choose your image and public page.' if kind == 'image' else
                        'organization-only onion discovery. Review the query and press Search; no search has started.'))
        return True

    def selected(self):
        scope = ('person', 'team', 'source')[self.tabs.index(self.tabs.select())]
        selection = self.tables[scope].selection()
        key = selection[0] if selection else ''
        return key, self._rows.get(key)

    def show_selected(self):
        key, selection = self.selected()
        if not selection:
            self._set_evidence(self._summary() if self.report else 'Select a result after running public professional discovery.')
            self._buttons()
            return
        scope, row = selection
        if scope == 'source':
            lines = [str(row.get('title', 'Public source')), 'URL: ' + str(row.get('final_url') or row.get('url', '')),
                     'Retrieved: ' + str(row.get('retrieved_at', '')), 'SHA-256: ' + str(row.get('sha256', '')),
                     'Accepted for contact evidence: ' + str(row.get('accepted', False)),
                     'Status: ' + str(row.get('status', '')), 'Result: ' + str(row.get('reason', ''))]
            lines.extend(['', self._summary()])
        else:
            lines = [str(row.get('name') if scope == 'person' else row.get('team_name', 'Team route')),
                     'INDIVIDUAL CANDIDATE · identity unverified' if scope == 'person' else 'TEAM ROUTE · not the requested person',
                     'Organization: ' + str(row.get('organization', '')), 'Source: ' + str(row.get('source_url', '')),
                     'Retrieved: ' + str(row.get('retrieved_at') or row.get('recorded_at', '')),
                     'Source SHA-256: ' + str(row.get('source_hash', '')), '', 'Published context:', str(row.get('snippet', ''))]
            for route in row.get('channels', []) if scope == 'person' else [row]:
                published = str(route.get('source_url') or row.get('source_url', ''))
                lines.extend(['', str(route.get('kind', 'route')) + ': ' + str(route.get('value', '')),
                              'Published on: ' + published if published else 'Published page: unknown',
                              'Public professional route · verify recipient before use', str(route.get('snippet', ''))])
            if key in self._saved:
                lines.extend(['', 'Saved to your private networking book. Nothing has been sent.'])
        self._set_evidence('\n'.join(lines))
        self._buttons()

    def _set_evidence(self, text):
        self.networking._set_text(self.evidence, text)

    def _buttons(self):
        self.search_button.configure(state='disabled' if self.busy else 'normal')
        self.cancel_button.configure(state='normal' if self.busy and self._operation != 'save' else 'disabled')
        key, selected = self.selected()
        self.open_button.configure(state='normal' if selected and not self.busy else 'disabled')
        self.save_button.configure(state='normal' if selected and selected[0] != 'source' and not self.busy and key not in self._saved else 'disabled')

    def save_selected(self):
        key, selected = self.selected()
        if not selected or key in self._saved:
            self.status.set('Select an unsaved professional candidate or team route first.')
            return False
        try:
            payload = discovery_contact_payload(selected[1], selected[0], self.report)
        except Exception as error:
            self.status.set(str(error)[:400])
            return False
        payload = deepcopy(payload)
        def save(check, _progress):
            check()
            for existing in self.networking.book.all():
                same_routes = {(route['kind'], route['value']) for route in existing.get('channels', [])} == {
                    (route['kind'], route['value']) for route in payload['channels']}
                if existing['name'] == payload['name'] and existing.get('source_url') == payload['source_url'] and same_routes:
                    return existing, key, True
            check()
            return self.networking.book.save(**payload), key, False
        return self._start('save', 'Saving selected source-backed contact with account encryption…', save)

    def open_source(self):
        _key, selected = self.selected()
        if not selected:
            self.status.set('Select a candidate, team route or source first.')
            return False
        scope, row = selected
        url = row.get('source_url', '') if scope != 'source' else row.get('final_url') or row.get('url', '')
        if not url:
            self.status.set('This result has no public source URL to open.')
            return False
        def open_page(check, _progress):
            check()
            from app.privacy_masks import ROUTE
            manager = self.gui.runtime.research
            required = self.report.get('tor_required') is True or (self.report.get('scope') or {}).get('tor_required') is True or ROUTE.proxy is not None
            if required and ROUTE.proxy is None:
                raise PermissionError('This result requires Tor. Start Tor before opening its source; direct fallback is disabled.')
            safe = manager.fetcher.validate_url(url)
            check()
            launch = getattr(manager, 'search_browser', None)
            if launch is None:
                raise RuntimeError('Public browser handoff is unavailable in this runtime.')
            result = launch(safe, required)
            if not isinstance(result, dict) or result.get('opened') is not True:
                raise RuntimeError('The browser did not confirm that it opened the public source.')
            return result
        return self._start('open', 'Checking and opening selected public source…', open_page, network=True)

    def close(self):
        self._cancel.set()
        self.generation += 1
        self.destroy()

    def _destroyed(self, event):
        if event.widget is not self:
            return
        self._closed = True
        self._cancel.set()
        self.generation += 1
        if self.progress.winfo_exists():  # already gone when the parent window closes first
            self.progress.stop()
        if self._poll_job:
            self.after_cancel(self._poll_job)
            self._poll_job = None
