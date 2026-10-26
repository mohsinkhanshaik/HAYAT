"""Manual Tor-only source reading, with visible progress and local session history."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Any

from app.gui_empty import show_empty
from app.gui_intelligence_parts import FlowRow
from app.gui_resources import BooleanVar, StringVar
from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.research.onion_navigation import navigation_controls
from app.gui_fonts import DISPLAY


class OnionNavigationWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        p = self.palette
        self.title('HAYAT · Onion navigator')
        self.configure(bg=p.BLACK)
        self.minsize(580, 500)
        self.transient(gui.root)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda _event: self.close())
        self.bind('<Destroy>', self._destroyed, add='+')
        self._closed, self._pending, self._generation = False, False, 0
        self._results: queue.Queue = queue.Queue()
        self._poll_job = self._layout_job = None
        self._cancel = threading.Event()
        self._history, self._history_index = [], -1
        self._page_rows, self._link_rows = {}, {}
        self.report: dict[str, Any] = {}
        self.url, self.query = StringVar(self), StringVar(self)
        self.follow = BooleanVar(self, False)
        self.pages, self.depth = StringVar(self, '3'), StringVar(self, '1')
        self.status = StringVar(self, 'Choose a site in Find onion sites, or paste a v3 onion URL, then Read.')
        self.site_filter, self.site_category = StringVar(self, ''), StringVar(self, 'All categories')
        self.check_text = StringVar(self, '')
        self._site_rows: dict[str, dict[str, Any]] = {}
        self._check_job = None
        self.link_detail = StringVar(self, 'Select a link to inspect its destination. External links are reference-only.')

        head = tk.Frame(self, bg=p.BLACK, padx=14, pady=10)
        head.pack(fill='x')
        tk.Label(head, text='Onion navigator', bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 16)).pack(side='left')
        ttk.Button(head, text='Close', command=self.close).pack(side='right')
        self.guidance = tk.Label(self, text='Read public HTML/text through Tor. Inspect source hashes and timestamps, then follow a selected same-site link.',
                                 bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=750)
        self.guidance.pack(fill='x', padx=14, pady=(0, 8))
        address = tk.Frame(self, bg=p.PANEL, padx=10, pady=8)
        address.pack(fill='x', padx=14)
        tk.Label(address, text='Public .onion URL', bg=p.PANEL, fg=p.WHITE, anchor='w').pack(fill='x')
        self.address = ttk.Entry(address, textvariable=self.url)
        self.address.pack(fill='x', pady=(4, 2))
        self.address.bind('<Return>', lambda _event: self.read())
        self.check_label = tk.Label(address, textvariable=self.check_text, bg=p.PANEL, fg=p.MUTED, anchor='w', justify='left',
                                    wraplength=900, font=('Segoe UI', 9))
        self.check_label.pack(fill='x', pady=(0, 4))
        self.url.trace_add('write', lambda *_args: self._schedule_check())
        topic = tk.Frame(address, bg=p.PANEL)
        topic.pack(fill='x')
        tk.Label(topic, text='Optional topic', bg=p.PANEL, fg=p.MUTED).pack(side='left', padx=(0, 8))
        ttk.Entry(topic, textvariable=self.query).pack(side='left', fill='x', expand=True)

        controls = FlowRow(self, p.BLACK, gap=6)
        controls.pack(fill='x', padx=14, pady=8)
        self.read_button = controls.add(ttk.Button(controls, text='Read page', command=self.read, style='HayatAccent.TButton'))
        self.cancel_button = controls.add(ttk.Button(controls, text='Cancel', command=self.cancel, state='disabled'))
        self.back_button = controls.add(ttk.Button(controls, text='Back', command=self.back, state='disabled'))
        self.forward_button = controls.add(ttk.Button(controls, text='Forward', command=self.forward, state='disabled'))
        controls.add(ttk.Button(controls, text='Copy citations', command=self.copy_citations))
        controls.add(ttk.Button(controls, text='Clear history', command=self.clear_history))
        controls.add(ttk.Checkbutton(controls, text='Follow same-site links', variable=self.follow))
        controls.add(ttk.Label(controls, text='Pages'))
        controls.add(ttk.Spinbox(controls, from_=1, to=8, width=3, textvariable=self.pages, state='readonly'))
        controls.add(ttk.Label(controls, text='Depth'))
        controls.add(ttk.Spinbox(controls, from_=1, to=2, width=3, textvariable=self.depth, state='readonly'))

        footer = tk.Frame(self, bg=p.BLACK, padx=14, pady=8)
        footer.pack(side='bottom', fill='x')
        self.status_label = tk.Label(footer, textvariable=self.status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                    anchor='w', justify='left', wraplength=750)
        self.status_label.pack(fill='x')
        self.progress = ttk.Progressbar(footer, mode='indeterminate')
        self.privacy_label = tk.Label(footer, text='No scripts, sign-in, form submission or persistent page cache. Request URLs can remain in transfer logs. Tor does not guarantee anonymity.',
                                     bg=p.BLACK, fg=p.MUTED, anchor='w', justify='left', wraplength=750)
        self.privacy_label.pack(fill='x', pady=(5, 0))

        self.body = tk.PanedWindow(self, orient='horizontal', bg=p.BLACK, sashwidth=7, bd=0)
        self.body.pack(fill='both', expand=True, padx=14)
        sources = tk.Frame(self.body, bg=p.PANEL)
        self.body.add(sources, minsize=160, width=235)
        tk.Label(sources, text='Retrieved sources', bg=p.PANEL, fg=p.WHITE, anchor='w', padx=8, pady=6).pack(fill='x')
        self.source_list = self._table(sources, ('source',), ('Source',), height=5)
        self.source_list.column('source', width=210, stretch=True)
        self.source_list.bind('<<TreeviewSelect>>', self.show_page)
        workspace = tk.Frame(self.body, bg=p.BLACK)
        self.body.add(workspace, minsize=300)
        self.tabs = ttk.Notebook(workspace)
        self.tabs.pack(fill='both', expand=True)
        self._build_sites_tab()
        text_tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(text_tab, text='Page evidence')
        self.text = DarkScrolledText(text_tab, wrap='word', bg=p.PANEL, fg=p.WHITE, state='disabled',
                                     font=('Segoe UI', 10), relief='flat', padx=12, pady=10)
        self.text.pack(fill='both', expand=True)
        links_tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(links_tab, text='Links')
        self.link_list = self._table(links_tab, ('label', 'scope'), ('Link', 'Scope'), height=6)
        self.link_list.column('label', width=300, stretch=True)
        self.link_list.column('scope', width=120, stretch=False)
        self.link_list.bind('<<TreeviewSelect>>', self.show_link)
        self.link_list.bind('<Double-1>', lambda _event: self.read_link())
        self.link_label = tk.Label(links_tab, textvariable=self.link_detail, bg=p.BLACK, fg=p.MUTED,
                                  anchor='w', justify='left', wraplength=500)
        self.link_label.pack(fill='x', pady=6)
        self.link_button = ttk.Button(links_tab, text='Read selected same-site link', command=self.read_link, state='disabled')
        self.link_button.pack(anchor='w', pady=(0, 5))
        report_tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(report_tab, text='Report / errors')
        self.details = DarkScrolledText(report_tab, wrap='word', bg=p.PANEL, fg=p.WHITE, state='disabled',
                                        font=('Consolas', 9), relief='flat', padx=10, pady=10)
        self.details.pack(fill='both', expand=True)
        self._set_text(self.text, 'Select a public onion page to read. Sources are untrusted external evidence; a citation verifies retrieval, not the publisher’s claims.')
        self.bind('<Configure>', self._fit)
        place_over(self, gui.root, (980, 760))
        self.deiconify()
        self.lift()
        self._paint_empty()
        self._poll()

    # ------------------------------------------------------------------ find onion sites
    def _build_sites_tab(self):
        """Verified onion sites by category, an onion-index search with each result's nature, and a details pane."""
        p = self.palette
        tab = tk.Frame(self.tabs, bg=p.BLACK)
        self.tabs.add(tab, text='Find onion sites')
        row = tk.Frame(tab, bg=p.BLACK)
        row.pack(fill='x', pady=(6, 4))
        entry = ttk.Entry(row, textvariable=self.site_filter, width=30)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda _event: self.load_sites())
        self.category_box = ttk.Combobox(row, textvariable=self.site_category, values=['All categories'], state='readonly', width=30)
        self.category_box.pack(side='left', padx=6)
        self.category_box.bind('<<ComboboxSelected>>', lambda _event: self.load_sites())
        ttk.Button(row, text='Show sites', command=self.load_sites).pack(side='left')
        ttk.Button(row, text='Search the onion index too', command=lambda: self.load_sites(search_index=True)).pack(side='left', padx=6)
        frame = tk.Frame(tab, bg=p.BLACK)
        frame.pack(fill='both', expand=True)
        self.site_table = ttk.Treeview(frame, columns=('kind', 'up', 'trust'), show='tree headings', selectmode='browse', height=8)
        for key, title, width in (('#0', 'Site', 230), ('kind', 'What it is', 190), ('up', 'Reachable', 90),
                                  ('trust', 'Verified', 110)):
            self.site_table.heading(key, text=title)
            self.site_table.column(key, width=width, minwidth=70, stretch=key in {'#0', 'kind'})
        bar = ttk.Scrollbar(frame, orient='vertical', command=self.site_table.yview, style='Hayat.Vertical.TScrollbar')
        self.site_table.configure(yscrollcommand=bar.set)
        self.site_table.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        self.site_table.bind('<<TreeviewSelect>>', lambda _event: self.show_site())
        self.site_table.bind('<Double-1>', lambda _event: self.read_site())
        self.site_detail = tk.Label(tab, text='Press Show sites: verified onion services of news organisations, search engines, '
                                              'social networks, privacy tools, public bodies and newsrooms\' SecureDrop inboxes.',
                                    bg=p.PANEL, fg=p.WHITE, anchor='w', justify='left', wraplength=700, padx=10, pady=8)
        self.site_detail.pack(fill='x', pady=(6, 4))
        actions = FlowRow(tab, p.BLACK, gap=5)
        actions.pack(fill='x')
        actions.add(ttk.Button(actions, text='Read it here', command=self.read_site))
        actions.add(ttk.Button(actions, text='Copy address', command=self.copy_site))
        actions.add(ttk.Button(actions, text='Open the proof page', command=self.open_proof))
        self.site_credit = tk.Label(tab, text='', bg=p.BLACK, fg=p.MUTED, anchor='w', font=('Segoe UI', 8))
        self.site_credit.pack(fill='x', pady=(4, 0))

    def load_sites(self, search_index=False):
        if self._closed:
            return
        try:
            self._allowed()
            registry = self.gui.runtime.registry
        except Exception as exc:
            self.status.set(str(exc))
            return
        category = self.site_category.get()
        arguments = {'query': self.site_filter.get().strip(), 'category': '' if category.startswith('All') else category.rsplit(' (', 1)[0],
                     'search_index': bool(search_index)}
        if search_index and not arguments['query']:
            self.status.set('Type a topic first (for example: news, privacy, books), then search the onion index.')
            return
        self.status.set('Searching the onion index through Tor…' if search_index else 'Reading the verified onion directory…')
        results = self._results

        def work():
            try:
                result = registry.execute('onion_sites', arguments)
                if not result.success:
                    raise RuntimeError(result.error)
                results.put((self._generation, 'sites', result.output))
            except Exception as exc:
                results.put((self._generation, 'sites_error', str(exc)))
        threading.Thread(target=work, name='hayat-onion-sites', daemon=True).start()

    def show_sites(self, value):
        self.site_table.delete(*self.site_table.get_children())
        self._site_rows = {}
        categories = ['All categories'] + [f'{name} ({count})' for name, count in value.get('categories') or []]
        self.category_box.configure(values=categories)
        for number, site in enumerate(value.get('directory') or []):
            key = f'd{number}'
            self._site_rows[key] = {**site, 'verified': True}
            reach = f"{site['up']} of {site['checks']} days" if site.get('checks') else 'not checked'
            self.site_table.insert('', 'end', iid=key, text=site['name'], values=(site['category'], reach, 'Yes, with proof'))
        index = value.get('index') or {}
        for number, item in enumerate(index.get('results') or []):
            key = f'i{number}'
            self._site_rows[key] = item
            self.site_table.insert('', 'end', iid=key, text=item['name'][:80],
                                   values=(item['nature'], 'unknown', 'Yes: ' + item['listed_as'] if item['verified'] else 'No (index)'))
        found = len(value.get('directory') or [])
        text = f"{found} verified site{'s' if found != 1 else ''}"
        if 'index' in value:
            text += f", {len(index.get('results') or [])} onion-index results ({index.get('note') or index.get('error', '')})"
        if value.get('directory_error'):
            text += f". The directory could not be refreshed ({value['directory_error']}); showing the kept copy"
        self.status.set(text + '. Choose one to see what it is; Read it here opens it through Tor.')
        self.site_credit.configure(text=f"Directory: {value.get('credit', '')} · {value.get('source', '')}")
        if self._site_rows:
            first = next(iter(self._site_rows))
            self.site_table.selection_set(first)

    def _site(self):
        selection = self.site_table.selection()
        return self._site_rows.get(selection[0]) if selection else None

    def show_site(self):
        site = self._site()
        if not site:
            return
        if site.get('verified') and 'category' in site:
            text = (f"{site['name']} · {site['category']}\n{site['about']}\nAddress: {site['url']}\n"
                    f"Proof from the organisation: {site.get('proof') or 'listed in the directory'}")
        else:
            text = (f"{site['name']} · looks like: {site['nature']}"
                    + (f" (listed as {site['listed_as']})" if site.get('verified') else ' · unverified: anyone can run an onion site')
                    + f"\n{site.get('about', '')}\nAddress: {site['url']}")
        self.site_detail.configure(text=text)

    def read_site(self):
        site = self._site()
        if site:
            self.url.set(site['url'])
            self.tabs.select(1)
            self.read(site['url'])

    def copy_site(self):
        site = self._site()
        if site:
            self.clipboard_clear()
            self.clipboard_append(site['url'])
            self.status.set('Address copied.')

    def open_proof(self):
        site = self._site()
        if site and site.get('proof'):
            webbrowser.open(site['proof'])
        elif site:
            self.status.set('This one has no proof page: it came from the onion index, not the verified directory.')

    def _schedule_check(self):
        if self._check_job is not None:
            self.after_cancel(self._check_job)
        self._check_job = self.after(400, self.check_address)

    def check_address(self):
        """Under the address: listed (with proof), a lookalike copy of a listed site, or unknown."""
        self._check_job = None
        url = self.url.get().strip()
        if '.onion' not in url:
            self.check_text.set('')
            return
        registry = getattr(self.gui.runtime, 'registry', None)
        if registry is None:
            return
        results, generation = self._results, self._generation

        def work():  # the first check may read the directory through Tor: never on the window's thread
            try:
                result = registry.execute('onion_sites', {'check_url': url})
                value = result.output if result.success else {'verdict': 'unknown', 'text': result.error or ''}
            except Exception as exc:
                value = {'verdict': 'unknown', 'text': f'Could not check this address: {exc}'}
            results.put((generation, 'check', {**value, 'for': url}))
        threading.Thread(target=work, name='hayat-onion-check', daemon=True).start()

    def show_check(self, value):
        if value.get('for') != self.url.get().strip():
            return  # the address changed since
        colours = {'listed': '#4ADE80', 'lookalike': '#F05D5E'}
        self.check_text.set(('✓ ' if value.get('verdict') == 'listed' else '⚠ ' if value.get('verdict') == 'lookalike' else '')
                            + str(value.get('text', '')))
        self.check_label.configure(fg=colours.get(value.get('verdict'), self.palette.MUTED))

    def _paint_empty(self):
        show_empty(self.source_list, 'No public page was retrieved. Open Report / errors to see why; nothing was fabricated.'
                   if self.report else 'Pages read in this session appear here, numbered for citation.', self.palette)
        show_empty(self.link_list, 'Links on the selected page appear here.\n'
                   'Same-site links can be followed; others are reference only.', self.palette)

    @staticmethod
    def _table(parent, columns, labels, height):
        # The parent's colour: unset, the corner between the two scrollbars was
        # a bright system-grey square in the dark window.
        frame = tk.Frame(parent, bg=parent.cget('bg'))
        frame.pack(fill='both', expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        table = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse', height=height)
        for key, label in zip(columns, labels, strict=True):
            table.heading(key, text=label)
        table.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
        y.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(frame, orient='horizontal', command=table.xview)
        x.grid(row=1, column=0, sticky='ew')
        table.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        return table

    @staticmethod
    def _set_text(widget, value):
        if widget.get('1.0', 'end-1c') == value:
            return
        scroller = getattr(widget, '_smooth_scroller', None)
        if scroller is not None:
            scroller.cancel()
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', value)
        widget.configure(state='disabled')
        widget.yview_moveto(0)

    def _fit(self, event):
        if event.widget is not self or self._closed:
            return
        width = max(260, event.width - 36)
        for label in (self.guidance, self.status_label, self.privacy_label):
            label.configure(wraplength=width)
        self.link_label.configure(wraplength=max(240, width - 260))
        orient = 'vertical' if event.width < 800 else 'horizontal'
        if self.body.cget('orient') != orient:
            self.body.configure(orient=orient)
            panes = self.body.panes()
            self.source_list.configure(height=1 if orient == 'vertical' else 5)
            self.body.paneconfigure(panes[0], minsize=110 if orient == 'vertical' else 160,
                                    height=110 if orient == 'vertical' else 200)
            self.body.paneconfigure(panes[1], minsize=100 if orient == 'vertical' else 300)

    def _allowed(self):
        runtime = self.gui.runtime
        modes = set(getattr(getattr(runtime, 'session', None), 'effective_modes', ()))
        if getattr(getattr(runtime, 'authority', None), 'privacy_mode', False) or modes & {'PRIVATE', 'LOCAL', 'LOCAL_ONLY'}:
            raise PermissionError('Privacy or local-only mode is on. Turn it off before reading internet sources.')

    def read(self, target=None):
        if self._pending or self._closed:
            return
        try:
            self._allowed()
            registry = self.gui.runtime.registry
            ticket = registry.interlock.ticket()
            arguments = {'url': target if isinstance(target, str) else self.url.get().strip(), 'query': self.query.get().strip(),
                         'max_pages': int(self.pages.get()) if self.follow.get() else 1,
                         'max_depth': int(self.depth.get()) if self.follow.get() else 0}
            registry.get('onion_navigate')  # fail visibly if integration is unavailable
        except Exception as exc:
            self.status.set(str(exc))
            return
        self.url.set(arguments['url'])
        self._pending = True
        self._generation += 1
        generation, results = self._generation, self._results
        self._cancel = cancel = threading.Event()
        self._busy(True)
        self.status.set('Checking Tor and selected source permissions…')

        def guard():
            registry.interlock.check(ticket)
            self._allowed()

        def work():
            try:
                with navigation_controls(cancel=cancel, check=guard,
                                         progress=lambda event: results.put((generation, 'progress', event))):
                    guard()
                    result = registry.execute('onion_navigate', arguments)
                    guard()
                    if cancel.is_set():
                        raise PermissionError('Navigation cancelled. Previous evidence is unchanged.')
                    if not result.success:
                        raise RuntimeError(result.error)
                    results.put((generation, 'result', result.output))
            except Exception as exc:
                results.put((generation, 'error', str(exc)))
        threading.Thread(target=work, name='hayat-onion-navigation', daemon=True).start()

    def _busy(self, busy):
        self.read_button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.back_button.configure(state='normal' if not busy and self._history_index > 0 else 'disabled')
        self.forward_button.configure(state='normal' if not busy and self._history_index + 1 < len(self._history) else 'disabled')
        self.link_button.configure(state='disabled')
        if busy:
            self.progress.pack(fill='x', pady=(5, 0), before=self.privacy_label)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.show_link()

    def _poll(self):
        if self._closed:
            return
        for _ in range(40):
            try:
                generation, kind, value = self._results.get_nowait()
            except queue.Empty:
                break
            if generation != self._generation:
                continue
            if kind == 'progress':
                self.status.set(value.get('message', 'Reading public source…'))
                continue
            if kind == 'sites':
                self.show_sites(value)
                continue
            if kind == 'sites_error':
                self.status.set('Could not load onion sites: ' + value[:400])
                continue
            if kind == 'check':
                self.show_check(value)
                continue
            self._pending = False
            if kind == 'error':
                self.status.set(value[:500])
            else:
                self._history = (self._history[:self._history_index + 1] + [value])[-20:]
                self._history_index = len(self._history) - 1
                self.show_report(value)
            self._busy(False)
        self._poll_job = self.after(80, self._poll)

    def show_report(self, value):
        self.report = value
        self.url.set(value.get('start_url', ''))
        self._page_rows.clear()
        self.source_list.delete(*self.source_list.get_children())
        for page in value.get('pages', []):
            identifier = str(page['citation'])
            self._page_rows[identifier] = page
            label = '[' + identifier + '] ' + page.get('title', page.get('final_url', 'Source'))
            self.source_list.insert('', 'end', iid=identifier, values=(label,))
        self._paint_empty()
        from app.plain_output import describe

        self._set_text(self.details, describe(value))
        if self._page_rows:
            self.source_list.selection_set(next(iter(self._page_rows)))
            self.show_page()
        else:
            self._set_text(self.text, 'No public page was retrieved. Open Report / errors to inspect the failed request; no result was fabricated.')
            self.link_list.delete(*self.link_list.get_children())
            self._link_rows.clear()
            self._paint_empty()
        self.status.set(f"{str(value.get('status', 'finished')).capitalize()} · {value.get('usable_pages', 0)} usable / "
                        f"{value.get('pages_retrieved', 0)} retrieved · {len(value.get('errors', []))} errors · "
                        f"{value.get('stop_reason', '')}. Source claims remain unverified.")
        self._busy(self._pending)

    def show_page(self, _event=None):
        selected = self.source_list.selection()
        page = self._page_rows.get(selected[0]) if selected else None
        if page is None:
            return
        warning = ('\nQUARANTINED: suspected source instructions. Text withheld.\n' if page.get('quarantined') else
                   '\nINTERACTIVE CHECK: this page needs user action; no bypass was attempted.\n' if page.get('interaction_required') else '')
        text = (f"[{page['citation']}] {page.get('title', '')}\n{page.get('final_url', '')}\n"
                f"Retrieved: {page.get('retrieved_at', '')}\nSHA-256: {page.get('sha256', '')}\n"
                f"Status: {page.get('status_code', '')} · {page.get('content_type', '')} · {page.get('bytes', 0)} bytes\n"
                + warning + '\n' + page.get('text', ''))
        if page.get('text_truncated'):
            text += '\n\n[Report excerpt limit reached; original response hash retained.]'
        self._set_text(self.text, text)
        self._link_rows.clear()
        self.link_list.delete(*self.link_list.get_children())
        for index, link in enumerate(page.get('links', [])):
            identifier = 'link-' + str(index)
            self._link_rows[identifier] = link
            scope = 'Same site' if link.get('followable') else 'Reference only'
            self.link_list.insert('', 'end', iid=identifier, values=(link.get('text') or link['url'], scope))
        self._paint_empty()
        self.show_link()

    def show_link(self, _event=None):
        selected = self.link_list.selection()
        link = self._link_rows.get(selected[0]) if selected else None
        self.link_detail.set(link['url'] if link else 'Select a link to inspect its destination. External links are reference-only.')
        self.link_button.configure(state='normal' if link and link.get('followable') and not self._pending else 'disabled')

    def read_link(self):
        selected = self.link_list.selection()
        link = self._link_rows.get(selected[0]) if selected else None
        if link and link.get('followable') and not self._pending:
            self.read(link['url'])
        else:
            self.status.set('Select a followable same-site link. Other references are displayed without opening another origin.')

    def back(self):
        if not self._pending and self._history_index > 0:
            self._history_index -= 1
            self.show_report(self._history[self._history_index])

    def forward(self):
        if not self._pending and self._history_index + 1 < len(self._history):
            self._history_index += 1
            self.show_report(self._history[self._history_index])

    def clear_history(self):
        if self._pending:
            self.status.set('Cancel the current navigation before clearing local session history.')
            return
        self._history, self._history_index = [], -1
        self.report = {}
        self._page_rows.clear()
        self._link_rows.clear()
        self.source_list.delete(*self.source_list.get_children())
        self.link_list.delete(*self.link_list.get_children())
        self._paint_empty()
        self._set_text(self.text, 'Session history cleared. Transfer logs are separate and may retain requested URLs.')
        self._set_text(self.details, '')
        self._busy(False)
        self.status.set('Local window history cleared. No page cache was written.')

    def copy_citations(self):
        citations = self.report.get('citations', [])
        if not citations:
            self.status.set('Read a source before copying citations.')
            return
        value = '\n\n'.join(f"[{row['id']}] {row['title']}\n{row['url']}\nRetrieved: {row['retrieved_at']}\nSHA-256: {row['sha256']}"
                            for row in citations)
        self.clipboard_clear()
        self.clipboard_append(value)
        self.status.set(f'{len(citations)} source citation(s) copied. They record retrieval, not independent verification.')

    def cancel(self):
        if self._pending:
            self._cancel.set()
            self.cancel_button.configure(state='disabled')
            self.status.set('Cancelling. An in-flight socket read may finish; its result will be discarded.')

    def close(self):
        if not self._closed:
            self._closed = True
            self._cancel.set()
            self._generation += 1
            for handle in (self._poll_job, self._layout_job):
                if handle is not None:
                    self.after_cancel(handle)
            self._poll_job = self._layout_job = None
            self.progress.stop()
            self.destroy()

    def _destroyed(self, event):
        if event.widget is self and not self._closed:
            self._closed = True
            self._cancel.set()
            self._generation += 1
            if self._poll_job is not None:
                self.after_cancel(self._poll_job)
                self._poll_job = None
