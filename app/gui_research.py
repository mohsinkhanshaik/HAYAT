"""The Searches window: what HAYAT is looking up, what it found, and how sure it is.

A search used to be invisible once it left the chat: it either answered or
seemed stuck. Each one is now a job, listed here while it runs and after --
query, state, time, how many sources, confidence -- with the full answer, its
trust verdict and any catalogue copies one click away. Worker threads queue
changes and the window polls, so Tk is only touched on its own thread.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont
from typing import Any

from app.gui_theme import DarkScrolledText
from app.gui_icons import apply_icons
from app.gui_windows import MARGIN, place_over, work_area
from app.gui_resources import BooleanVar, StringVar
from app.gui_intelligence_parts import FlowRow

STATES = {"running": "Searching…", "done": "Finished", "failed": "Nothing reliable", "cancelled": "Cancelled"}
DATA_KINDS = {'Public datasets': 'datasets', 'Public papers': 'publications', 'Data & papers': 'all', 'Life-science papers': 'literature'}


class ResearchStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.jobs = gui.runtime.research_jobs
        self.changes: queue.Queue = queue.Queue()
        self._browser_results: queue.Queue = queue.Queue()
        self._browser_pending = False
        self._closed = False
        self._poll_job = None
        self._display_signature = None
        self.title("HAYAT · Searches")
        area = work_area(gui.root)
        self.minsize(min(720, area[2] - area[0] - 2 * MARGIN), min(560, area[3] - area[1] - 2 * MARGIN - 40))
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: (self.close(), "break")[1])
        self._build()
        self.bind('<Configure>', self._fit)
        self._unsubscribe = self.jobs.add_listener(lambda job: self.changes.put(job.job_id))
        self.bind("<Destroy>", self._destroyed, add="+")
        apply_icons(self, self.palette)
        place_over(self, gui.root, (860, 700))
        self.deiconify()
        self.lift()
        self.refresh()
        self._poll()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=10, pady=8)
        header.pack(fill="x")
        tk.Label(header, text="Searches", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.close).pack(side="right")
        shortcuts = self.research_shortcuts = FlowRow(header, p.BLACK, gap=4, height=32)
        shortcuts.pack(side='left', fill='x', expand=True, padx=(8, 4))
        self.person_studio_button = ttk.Button(shortcuts, text='Professional research…', style='Hayat.TButton', command=self.open_person_studio)
        shortcuts.add(self.person_studio_button)
        self.onion_pages_button = ttk.Button(shortcuts, text='Onion pages', style='Hayat.TButton',
                                            command=lambda: self.gui._open_onion_navigation())
        shortcuts.add(self.onion_pages_button)
        self.image_research_button = ttk.Button(shortcuts, text="Image research…", style="Hayat.TButton",
                                                command=self.open_image_research)
        shortcuts.add(self.image_research_button)

        tk.Label(self, text="Deep combines public indexes and follows evidence links. Choose Onion (Tor) for public onion research, "
                           "or search professional sources and registry metadata.",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=700).pack(
                     fill="x", padx=16, pady=(0, 8))

        mode_row = tk.Frame(self, bg=p.BLACK, padx=16)
        mode_row.pack(fill='x', pady=(0, 6))
        self.search_kind = StringVar(self, 'Topic')
        self.kind_choice = ttk.Combobox(mode_row, textvariable=self.search_kind, values=('Topic', 'Onion (Tor)', 'Tor web (public internet)', 'Person (public work)', *DATA_KINDS),
                                        width=23, state='readonly')
        self.kind_choice.pack(side='left')
        self.kind_choice.bind('<<ComboboxSelected>>', lambda _event: self._show_person_fields())
        self.person_details_visible = BooleanVar(self, True)
        self.person_details_toggle = ttk.Checkbutton(mode_row, text='Context / profile', variable=self.person_details_visible,
                                                     command=self._show_person_fields, state='disabled')
        self.person_details_toggle.pack(side='right')
        self.query_hint = StringVar(self, 'Topic, question or public URL')
        tk.Label(mode_row, textvariable=self.query_hint, bg=p.BLACK, fg=p.MUTED).pack(side='left', padx=8)

        ask = tk.Frame(self, bg=p.BLACK, padx=16)
        ask.pack(fill="x", pady=(0, 8))
        self.query = tk.Entry(ask, font=("Segoe UI", 11), bg=p.PANEL, fg=p.WHITE, insertbackground=p.GOLD, relief="flat")
        self.query.pack(side="left", fill="x", expand=True, ipady=5)
        self.query.bind("<Return>", lambda _e: self.search())
        self.depth = StringVar(self, "Deep")
        self.depth_choice = ttk.Combobox(ask, textvariable=self.depth, values=("Quick", "Normal", "Deep"),
                                         state="readonly", width=8)
        self.depth_choice.pack(side="left", padx=(8, 0))
        ttk.Button(ask, text="Search", style="HayatAccent.TButton", command=self.search).pack(side="left", padx=(8, 0))

        self.person_fields = tk.Frame(self, bg=p.BLACK, padx=16)
        self.person_context, self.person_url = StringVar(self), StringVar(self)
        for row, (label, variable) in enumerate((('Public role / organization (optional)', self.person_context),
                                                ('Public profile URL (optional)', self.person_url))):
            tk.Label(self.person_fields, text=label, bg=p.BLACK, fg=p.MUTED).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=3)
            entry = ttk.Entry(self.person_fields, textvariable=variable)
            entry.grid(row=row, column=1, sticky='ew', pady=3)
            entry.bind('<Return>', lambda _event: self.search())
        self.person_fields.columnconfigure(1, weight=1)

        table = self.job_table = tk.Frame(self, bg=p.BLACK, padx=16)
        table.pack(fill="x")
        self.tree = ttk.Treeview(table, columns=("state", "time", "sources", "confidence"), show="tree headings",
                                 height=7, selectmode="browse")
        for column, title, width in (("#0", "Search", 280), ("state", "State", 115), ("time", "Time", 60),
                                     ("sources", "Sources", 65), ("confidence", "Confidence", 85)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=50 if column != "#0" else 140,
                             stretch=column == "#0", anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        table.columnconfigure(0, weight=1)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.grid(row=0, column=0, sticky='ew')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.show_selected())

        buttons = tk.Frame(self, bg=p.BLACK, padx=16, pady=6)
        buttons.pack(fill="x")
        self.cancel_button = ttk.Button(buttons, text="Cancel search", style="Hayat.TButton", command=self.cancel)
        self.cancel_button.pack(side="left")
        self.again_button = ttk.Button(buttons, text="Search again, deeper", style="Hayat.TButton", command=self.again)
        self.again_button.pack(side="left", padx=6)
        self.browse_button = ttk.Button(buttons, text='Browse', style='Hayat.TButton', command=self.browse_search)
        self.browse_button.pack(side='left', padx=(0, 6))
        ttk.Button(buttons, text="Add image…", style="Hayat.TButton", command=self.add_image).pack(side="left", padx=6)
        ttk.Button(buttons, text="Copy answer", style="Hayat.TButton", command=self.copy_answer).pack(side="right")

        tk.Label(self, text="Images open in chat for visible content and text. A photo does not establish someone's identity.",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=700).pack(fill="x", padx=16)
        self.detail_status = StringVar(self)
        self.status_label = tk.Label(self, textvariable=self.detail_status, bg=p.BLACK, fg=p.GOLD_SOFT,
                                     font=("Segoe UI", 9), anchor="w", justify='left', wraplength=700)
        self.status_label.pack(fill="x", padx=16, pady=6)
        self.status_label.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(200, event.width - 8)))

        self.result = DarkScrolledText(self, wrap="word", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 10),
                                                padx=12, pady=10, relief="flat", state="disabled")
        self.result.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    def open_person_studio(self):
        from app.gui_person_research import PersonResearchStudio
        window = getattr(self, '_person_studio', None)
        if window is None or not window.winfo_exists():
            self._person_studio = PersonResearchStudio(self)
        else:
            window.lift()

    def _show_person_fields(self):
        person = self.search_kind.get() == 'Person (public work)'
        self.query_hint.set('Known name; unverified source matches' if person else 'Topic, question or public URL')
        if self.search_kind.get() in DATA_KINDS:
            self.query_hint.set('Registry metadata; contents need review')
        if self.search_kind.get() == 'Onion (Tor)':
            self.query_hint.set('Onion sources only; indexes may be blocked')
        if self.search_kind.get() == 'Tor web (public internet)':
            self.query_hint.set('General public-web topics through Tor')
        self.person_details_toggle.configure(state='normal' if person else 'disabled')
        if person and self.person_details_visible.get():
            self.person_fields.pack(fill='x', pady=(0, 6), before=self.job_table)
        else:
            self.person_fields.pack_forget()
        self._fit_table()

    def _fit(self, event):
        if event.widget is self:
            self._fit_table()

    def _fit_table(self):
        compact = self.winfo_height() < 640
        if compact:
            height = 1 if self.person_fields.winfo_manager() else 2
        else:
            height = 5 if self.search_kind.get() == 'Topic' else 3
        if int(self.tree['height']) != height:
            self.tree.configure(height=height)
        heading = tkfont.Font(root=self, font=ttk.Style(self).lookup('Treeview.Heading', 'font') or 'TkHeadingFont')
        for column in self.tree['columns']:
            minimum = max(50, heading.measure(self.tree.heading(column, 'text')) + 18)
            self.tree.column(column, minwidth=minimum, width=max(minimum, int(self.tree.column(column, 'width'))))

    def _poll(self) -> None:
        self._poll_job = None
        if self._closed:
            return
        changed = False
        while True:
            try:
                self.changes.get_nowait()
                changed = True
            except queue.Empty:
                break
        if changed or self.jobs.running():
            self.refresh()          # running jobs: keep the time column moving
        try:
            message = self._browser_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._browser_pending = False
            self.browse_button.configure(state='normal')
            self.detail_status.set(message)
        self._poll_job = self.after(1000, self._poll)

    def browse_search(self) -> None:
        if self._browser_pending or not self._search_allowed():
            return
        job = self._selected()
        query = self.query.get().strip() or (job.query if job else '')
        onion = self.search_kind.get() == 'Onion (Tor)' if self.query.get().strip() else bool(job and job.onion)
        tor_web = self.search_kind.get() == 'Tor web (public internet)' if self.query.get().strip() else bool(job and job.tor_web)
        if not query:
            self.detail_status.set('Enter a topic or select a search to browse.')
            return
        self._browser_pending = True
        self.browse_button.configure(state='disabled')
        self.detail_status.set('Opening the search browser; Tor bootstrap may take a moment...')
        def work():
            from app.research.handoff import browser_handoff
            try:
                outcome = browser_handoff(self.jobs.research(), query, onion, open_browser=True, tor_only=tor_web)
                message = 'Search browser opened. ' + outcome['next_step']
            except Exception as error:
                message = 'Browser handoff: ' + str(error)[:240]
            self._browser_results.put(' '.join(message.split()))
        threading.Thread(target=work, name='hayat-search-browser', daemon=True).start()

    def refresh(self) -> None:
        jobs = self.jobs.recent(30)
        wanted = {job.job_id for job in jobs}
        for iid in self.tree.get_children():
            if iid not in wanted:
                self.tree.delete(iid)
        for index, job in enumerate(jobs):
            confidence = f"{job.confidence:.0%}" if job.status == "done" else ""
            if job.public_data is not None and job.status == 'done':
                confidence = 'metadata'
            values = (STATES.get(job.status, job.status), f"{int(job.elapsed)} s" if job.status == "running"
                      else "", job.sources or "", confidence)
            if not self.tree.exists(job.job_id):
                self.tree.insert("", index, iid=job.job_id, text=job.query, values=values)
            else:
                self.tree.item(job.job_id, text=job.query, values=values)
                if self.tree.index(job.job_id) != index:
                    self.tree.move(job.job_id, "", index)
        self.show_selected()
        from app.gui_empty import show_empty
        show_empty(self.tree, "No searches yet. Type a question above and press Search: HAYAT searches in the background, "
                              "and the answer appears here and in the chat when it is ready.", self.palette)

    def _selected(self):
        selected = self.tree.selection()
        return self.jobs.get(selected[0]) if selected else None

    def show_selected(self) -> None:
        job = self._selected()
        self.cancel_button.configure(state="normal" if job is not None and job.status == "running" else "disabled")
        self.again_button.configure(state="normal" if job is not None else "disabled")
        if job is None:
            self.detail_status.set("Select a search to read its answer. Results also arrive in chat.")
            if self._display_signature is not None:
                self._set_result("")
                self._display_signature = None
            return
        self.detail_status.set(f"{job.level.title()} · {STATES.get(job.status, job.status)}"
                               + (' · Onion research through Tor' if job.onion else '')
                               + (' · Public internet through Tor' if job.tor_web else '')
                               + (' · Public-work sources; identity unverified' if job.person else '')
                               + (' · Registry metadata; findings unverified' if job.public_data else '')
                               + (f" · {int(job.elapsed)} s elapsed" if job.status == "running" else ""))
        signature = (job.job_id, job.status, job.answer, job.error)
        if signature == self._display_signature:
            return  # polling must not replace text, selection, or the reading position
        self._display_signature = signature
        text = (f"Still searching for \"{job.query}\" ({int(job.elapsed)} s so far). The answer will appear here and "
                "in the chat." if job.status == "running" else
                f'Search cancelled: "{job.query}".' if job.status == "cancelled" else self.jobs.result_text(job))
        self._set_result(text)

    def _set_result(self, text: str) -> None:
        self.result._smooth_scroller.cancel()
        self.result.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("end", text)
        self.result.yview_moveto(0)
        self.result.configure(state="disabled")

    def search(self) -> None:
        query = self.query.get().strip()
        if not query or not self._search_allowed():
            return
        options = {}
        if self.search_kind.get() == 'Onion (Tor)':
            options['onion'] = True
        if self.search_kind.get() == 'Tor web (public internet)':
            options['tor_web'] = True
        if self.search_kind.get() in DATA_KINDS:
            options['public_data'] = {'kind': DATA_KINDS[self.search_kind.get()]}
        if self.search_kind.get() == 'Person (public work)':
            from app.research.people import PersonRequest
            try:
                request = PersonRequest(query, self.person_context.get(), self.person_url.get())
            except ValueError as error:
                self.detail_status.set(str(error))
                return
            options['person'] = {'name': request.name, 'context': request.context, 'profile_url': request.profile_url}
        job = self.jobs.start(query, asked=query, level=self.depth.get().lower(), **options)
        self.query.delete(0, "end")
        self.jobs.release(job)
        self.refresh()
        self.tree.selection_set(job.job_id)
        self.tree.see(job.job_id)
        self.show_selected()

    def cancel(self) -> None:
        job = self._selected()
        if job is not None and job.status == "running":
            self.jobs.cancel(job.job_id)
            self.refresh()

    def again(self) -> None:
        job = self._selected()
        if job is None or not self._search_allowed():
            return
        options = {'person': dict(job.person)} if job.person is not None else {}
        if job.public_data is not None:
            options['public_data'] = dict(job.public_data)
        if job.onion:
            options['onion'] = True
        if job.tor_web:
            options['tor_web'] = True
        new = self.jobs.start(job.query, asked=job.asked, level="deep", **options)
        self.jobs.release(new)
        self.refresh()
        self.tree.selection_set(new.job_id)
        self.tree.see(new.job_id)
        self.show_selected()

    def _search_allowed(self) -> bool:
        runtime = self.gui.runtime
        if runtime.registry.interlock.halted:
            self.detail_status.set('Emergency stop active. Resume tools before searching.')
            return False
        modes = set(getattr(getattr(runtime, "session", None), "effective_modes", ()))
        if getattr(getattr(runtime, "authority", None), "privacy_mode", False) or modes & {"PRIVATE", "LOCAL", "LOCAL_ONLY"}:
            self.detail_status.set("Privacy or local-only mode is on. Turn it off to search the internet.")
            return False
        return True

    def add_image(self) -> None:
        self.withdraw()
        attached = False
        try:
            attached = self.gui._add_files(kind="image")
        finally:
            if not self._closed:
                if attached:
                    self.gui.root.lift()  # show the image reading in chat; keep the search draft
                else:
                    self.deiconify()
                    self.lift()

    def open_image_research(self):
        from app.gui_image_research import ImageResearchStudio
        panel = getattr(self, '_image_research', None)
        if panel is None or not panel.winfo_exists():
            panel = self._image_research = ImageResearchStudio(self)
        panel.deiconify()
        panel.lift()
        return panel

    def copy_answer(self) -> None:
        text = self.result.get("1.0", "end-1c").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _destroyed(self, event: Any) -> None:
        if event.widget is self:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self._unsubscribe()

    def close(self) -> None:
        self._cleanup()
        self.destroy()
