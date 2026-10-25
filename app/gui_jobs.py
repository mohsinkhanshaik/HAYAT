"""Studios → Jobs: find postings, see why each fits, prepare and send applications, track them (app/jobs/).

Tabs:
* **Matches:** search now or let the automatic round do it; every job with its score, verdict,
  where it is and its source. Choosing one shows what it asks for and every part of its score.
  Buttons prepare the packet, send it (through the confirmation window, email postings only),
  open the employer's page, mark it applied, or hide it.
* **Applications:** the tracker, with status changes and follow-up dates.
* **Profile:** read a CV, see what HAYAT understood, and set preferences.
* **CV review:** how the CV reads against the best matches, with what to add or move.
* Each job has a **Deep look**: its duties against the CV, what it could do for you, the market around it, your
  history with similar roles, what applicants miss, the first 90 days and how to prepare. **Fill form** fills
  the employer's page in HAYAT's browser and stops for you to attach the CV and submit.
* **Sources:** which boards are read, when, what each gave, and the automatic round.

Sending acts as you toward an employer, so it always waits for your confirmation.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from app.gui_resources import BooleanVar, StringVar

VERDICT_COLOURS = {"strong": "#4ADE80", "good": "#A3E635", "stretch": "#E8C46A", "weak": "#9AA4AE", "not eligible": "#F05D5E"}
TABS = ("Matches", "Applications", "Profile", "CV review", "Learn", "Sources")


class JobsWindow(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.p, self.center = gui, type(gui), gui.runtime.jobs
        self.watcher = getattr(gui.runtime, "job_watcher", None)
        p = self.p
        self.title("HAYAT · Jobs")
        self.configure(bg=p.BLACK)
        self.minsize(680, 480)
        self.bind("<Escape>", lambda _event: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._jobs: queue.Queue = queue.Queue()
        self._results: queue.Queue = queue.Queue()
        self._closed = False
        self._search_stop = threading.Event()
        self._match_offset = 0
        self._match_filter = None
        self.pages = []
        self._request_ids: dict[str, int] = {}
        self._coalesced = {"matches", "detail", "insight", "sponsorship", "sources", "review", "learn", "onboarding", "source_access"}
        self._busy_actions: set[str] = set()
        self._profile_loaded: dict[str, Any] = {}
        self.match_rows: dict[str, dict[str, Any]] = {}
        self.selected: int | None = None
        head = tk.Frame(self, bg=p.BLACK)
        head.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(head, text="Jobs", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16), anchor="w").pack(side="left")
        self.status = tk.Label(head, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9))
        self.status.pack(side="right")
        introduction = tk.Label(self, text="HAYAT searches public job boards, reads each posting and your CV, scores the fit with every part shown, and "
                            "prepares applications. Sending an application always waits for your confirmation.",
                 bg=p.BLACK, fg=p.MUTED, wraplength=940, justify="left", anchor="w")
        introduction.pack(fill="x", padx=16)
        introduction.bind("<Configure>", lambda event: introduction.configure(wraplength=max(300, event.width - 4)))
        setup = tk.Frame(self, bg=p.PANEL, padx=10, pady=6)
        setup.pack(fill="x", padx=16, pady=(8, 0))
        self.setup_note = tk.Label(setup, text="Checking your profile...", bg=p.PANEL, fg=p.MUTED,
                                   font=("Segoe UI", 9), anchor="w", justify="left", wraplength=600)
        self.setup_note.pack(side="left", fill="x", expand=True)
        self.setup_note.bind("<Configure>", lambda event: self.setup_note.configure(wraplength=max(220, event.width - 4)))
        self._button(setup, "Profile & targets", lambda: self.tabs.select(TABS.index("Profile")), "person")
        self._button(setup, "Find employers", lambda: self.tabs.select(TABS.index("Sources")), "search")
        # Reserve actions before the wrapping note; packing the long note first clipped buttons on small windows.
        self.setup_note.pack_forget()
        for child in setup.winfo_children():
            if isinstance(child, ttk.Button):
                child.pack_configure(side="right")
        self.setup_note.pack(side="left", fill="x", expand=True)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=10)
        self.tabs.add(self._build_matches(), text="Matches")
        self.tabs.add(self._build_applications(), text="Applications")
        self.tabs.add(self._build_profile(), text="Profile")
        self.tabs.add(self._build_review(), text="CV review")
        self.tabs.add(self._build_learn(), text="Learn")
        self.tabs.add(self._build_sources(), text="Sources")
        self.tabs.bind("<<NotebookTabChanged>>", lambda _event: self.refresh_tab())
        threading.Thread(target=self._work, name="hayat-jobs-window", daemon=True).start()
        from app.gui_icons import apply_icons
        from app.gui_windows import place_over
        apply_icons(self, p)
        place_over(self, gui.root, (1180, 760))
        self.deiconify()
        self.refresh_tab()
        self.run("onboarding", self.center.show_profile)
        self._poll_id = self.after(150, self._tick)

    # ------------------------------------------------------------------ work off the UI thread
    def run(self, kind: str, call: Callable[[], Any], busy: str = "") -> None:
        if self._closed:
            return
        if kind in {"search", "prepare", "send", "fill", "discovery", "sponsor_import", "source_settings", "browser_search", "source_open"}:
            if kind in self._busy_actions:
                self.status.configure(text="That action is already running. Its result will appear here.")
                return
            self._busy_actions.add(kind)
            if kind == "browser_search":
                self.continue_browser_button.configure(state="disabled")
        if busy:
            self.status.configure(text=busy)
        if kind in {"profile", "preferences"}:
            # Capture on Tk's thread, then carry the snapshot with the result.
            snapshot = {key: variable.get() for key, variable in self.fields.items()}
            snapshot.update(remote_only=self.remote_only.get(), willing_to_relocate=self.willing_to_relocate.get(),
                            auto_prepare=self.auto_prepare.get())
            operation = call
            call = lambda: {**operation(), "_ui_fields": snapshot}
        request = self._request_ids.get(kind, 0) + 1
        self._request_ids[kind] = request
        self._jobs.put((kind, request, call))

    def _work(self) -> None:
        while not self._closed:
            try:
                kind, request, call = self._jobs.get(timeout=.5)
            except queue.Empty:
                continue
            if self._closed or (kind in self._coalesced and request != self._request_ids[kind]):
                continue
            try:
                self._results.put((kind, request, call(), None))
            except Exception as exc:  # shown in the window, never raised into Tk
                self._results.put((kind, request, None, str(exc)))

    def _tick(self) -> None:
        if self._closed:
            return
        while not self._results.empty():
            kind, request, value, error = self._results.get_nowait()
            self._busy_actions.discard(kind)
            if kind == "search_progress":
                if "search" in self._busy_actions:
                    self.search_note.configure(text=f"Reading {value['source']} - request {value['requests']}/{value['request_limit']}...", fg=self.p.MUTED)
                continue
            if kind == "search":
                self.search_progress.stop()
                self.search_progress.pack_forget()
                self.search_button.configure(state="normal")
                self.stop_search_button.configure(state="disabled")
            if kind == "browser_search":
                self.continue_browser_button.configure(state="normal" if getattr(self.center, "_job_browser_pending", None) else "disabled")
                if "search" not in self._busy_actions:
                    self.stop_search_button.configure(state="disabled")
            if kind in self._coalesced and request != self._request_ids.get(kind):
                continue
            if error:
                self.status.configure(text=error[:160], fg="#F05D5E")
                if kind == "search":
                    self.search_note.configure(text="Search could not finish: " + error[:700] + "\nYour saved jobs are still available. Check Sources or try again.",
                                               fg="#F05D5E")
                if kind in ("detail", "prepare", "send", "open", "status", "fill"):
                    self.say(self.detail, error)
                if kind in ("insight", "narrative"):
                    self.say(self.deep, error)
                    self.say(self.preparation_text, error)
                if kind == "review":
                    self.say(self.review_text, error)
                if kind == "sponsorship":
                    self.say(self.sponsorship_text, error)
                if kind in ("discovery", "sponsor_import"):
                    self.say(self.discovery_text, error)
                continue
            self.status.configure(text="", fg=self.p.MUTED)
            getattr(self, "show_" + kind)(value)
        self._poll_id = self.after(150, self._tick)

    def close(self) -> None:
        self._closed = True
        self._search_stop.set()
        self.search_progress.stop()
        if getattr(self, "_poll_id", None):
            self.after_cancel(self._poll_id)
        self.destroy()

    @staticmethod
    def say(box: tk.Text, text: str) -> None:
        # A refresh often returns identical content while the reader is scrolling.
        if box.get("1.0", "end-1c") == text:
            return
        scroller = getattr(box, "_smooth_scroller", None)
        if scroller is not None:
            scroller.cancel()
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _text(self, parent: tk.Misc, height: int = 10) -> tk.Text:
        p = self.p
        from app.gui_theme import DarkScrolledText
        box = DarkScrolledText(parent, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 9), wrap="word", height=height, relief="flat", padx=10, pady=8,
                      highlightthickness=0, cursor="arrow")
        box.configure(state="disabled")
        return box

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], Any], icon: str = "") -> ttk.Button:
        button = ttk.Button(parent, text=text, style="Compact.Hayat.TButton", command=command)
        if icon:
            button._hayat_icon = icon
        button.pack(side="left", padx=(0, 6))
        return button

    def refresh_tab(self) -> None:
        tab = TABS[self.tabs.index("current")]
        center = self.center
        if tab == "Matches":
            show_blocked = self.show_blocked.get()
            sort_by, sponsorship, remote, query = self.sort_by.get(), self.sponsor_filter.get(), self.remote.get(), self.query.get().strip()
            location = self.location.get().strip()
            active_filter = (show_blocked, sort_by, sponsorship, remote, query, location)
            if active_filter != self._match_filter:
                self._match_offset = 0
                self._match_filter = active_filter
            offset = self._match_offset
            self.run("matches", lambda: center.matches(limit=200, offset=offset, include_blocked=show_blocked, sort_by=sort_by,
                                                       sponsorship=sponsorship, remote_only=remote, query=query, location=location))
        elif tab == "Applications":
            self.run("applications", center.applications)
        elif tab == "Profile":
            self.run("profile", center.show_profile)
        elif tab == "CV review":
            self.run("review", center.cv_review, "Reading your CV against your best matches…")
        elif tab == "Learn":
            self.run("learn", center.learning_plan, "Working out what to learn from your matches…")
        else:
            self.run("sources", self._sources)

    def _sources(self) -> dict[str, Any]:
        from app.jobs.center import source_catalog
        preferences = self.center.profile.load()["preferences"]
        thread = getattr(self.watcher, "_thread", None)
        stopped = getattr(self.watcher, "_stop", None)
        return {"catalog": source_catalog(), "enabled": preferences["sources"], "companies": preferences["companies"],
                "state": self.center.store.source_state(),
                "cooldowns": self.center.store.source_cooldowns(),
                "watcher": {"last_round": getattr(self.watcher, "last_round", None),
                            "every_hours": round(getattr(self.watcher, "interval", 0) / 3600, 1),
                            "running": bool(thread is not None and thread.is_alive() and not (stopped is not None and stopped.is_set()))}}

    # ------------------------------------------------------------------ matches
    def _build_matches(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        bar = tk.Frame(frame, bg=p.BLACK)
        bar.pack(fill="x", pady=(6, 6))
        bar.columnconfigure(1, weight=2)
        bar.columnconfigure(3, weight=1)
        tk.Label(bar, text="Role / skills", bg=p.BLACK, fg=p.WHITE).grid(row=0, column=0, padx=(0, 6))
        self.query = StringVar(self, "")
        entry = ttk.Entry(bar, textvariable=self.query, width=25)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        entry.bind("<Return>", lambda _event: self.search())
        tk.Label(bar, text="Location", bg=p.BLACK, fg=p.WHITE).grid(row=0, column=2, padx=(0, 6))
        self.location = StringVar(self, "")
        where = ttk.Combobox(bar, textvariable=self.location, width=18, values=("Worldwide", "United States", "Canada", "United Kingdom", "India", "Germany", "Australia", "Singapore", "Brazil", "South Africa", "Japan", "United Arab Emirates"))
        where.grid(row=0, column=3, sticky="ew", padx=(0, 10))
        where.bind("<Return>", lambda _event: self.search())
        self.remote = BooleanVar(self, False)
        ttk.Checkbutton(bar, text="Remote only", variable=self.remote, command=self.refresh_tab).grid(row=0, column=4, padx=(0, 8))
        self.search_button = ttk.Button(bar, text="Search now", style="Compact.Hayat.TButton", command=self.search)
        self.search_button._hayat_icon = "search"
        self.search_button.grid(row=0, column=5)
        options = tk.Frame(frame, bg=p.BLACK)
        options.pack(fill="x", pady=(0, 5))
        tk.Label(options, text="Depth", bg=p.BLACK, fg=p.MUTED).pack(side="left")
        self.search_depth = StringVar(self, "deep")
        ttk.Combobox(options, textvariable=self.search_depth, values=("normal", "deep", "maximum"), state="readonly", width=10).pack(side="left", padx=6)
        self.stop_search_button = self._button(options, "Stop search", self.stop_search, "stop")
        self.stop_search_button.configure(state="disabled")
        tk.Label(options, text="Blank / Worldwide = all locations. Type any city or country; separate alternatives with ;", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8)).pack(side="left")
        browser = tk.Frame(frame, bg=p.BLACK)
        browser.pack(fill="x", pady=(0, 5))
        self.browser_platform = StringVar(self, "Employer boards")
        ttk.Combobox(browser, textvariable=self.browser_platform, values=("Employer boards", "LinkedIn", "Indeed", "Web"), state="readonly", width=18).pack(side="left", padx=(0, 6))
        self._button(browser, "Browse platform", self.browse_platform, "search")
        self.browser_scan = BooleanVar(self, False)
        ttk.Checkbutton(browser, text="Read page and scroll (HAYAT browser)", variable=self.browser_scan).pack(side="left")
        verification = tk.Frame(frame, bg=p.BLACK)
        verification.pack(fill="x", pady=(0, 5))
        tk.Label(verification, text="Web engine", bg=p.BLACK, fg=p.MUTED).pack(side="left")
        self.browser_engine = StringVar(self, "DuckDuckGo")
        ttk.Combobox(verification, textvariable=self.browser_engine, values=("Auto", "Google", "Bing", "DuckDuckGo"), state="readonly", width=12).pack(side="left", padx=6)
        self.continue_browser_button = self._button(verification, "Continue after verification", self.continue_browser)
        self.continue_browser_button.configure(state="disabled")
        self._button(verification, "Search free APIs", self.search, "search")
        self.search_progress = ttk.Progressbar(frame, mode="indeterminate", maximum=100)
        self.search_note = tk.Label(frame, text="Search public boards, or filter the jobs already saved below. Set your own roles and pay target in Profile.",
                                    bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w", justify="left", wraplength=1000)
        self.search_note.pack(fill="x", pady=(0, 5))
        self.search_note.bind("<Configure>", lambda event: self.search_note.configure(wraplength=max(250, event.width - 4)))
        self.show_blocked = BooleanVar(self, False)
        filters = tk.Frame(frame, bg=p.BLACK)
        filters.pack(fill="x", pady=(0, 6))
        tk.Label(filters, text="Sort", bg=p.BLACK, fg=p.MUTED).pack(side="left")
        self.sort_by = StringVar(self, "score")
        sorting = ttk.Combobox(filters, textvariable=self.sort_by, values=("score", "newest", "pay"), state="readonly", width=9)
        sorting.pack(side="left", padx=(6, 16))
        sorting.bind("<<ComboboxSelected>>", lambda _event: self.refresh_tab())
        tk.Label(filters, text="Sponsorship stated", bg=p.BLACK, fg=p.MUTED).pack(side="left")
        self.sponsor_filter = StringVar(self, "all")
        sponsorship = ttk.Combobox(filters, textvariable=self.sponsor_filter,
                                  values=("all", "offered", "not stated", "not offered", "conflicting"), state="readonly", width=14)
        sponsorship.pack(side="left", padx=6)
        sponsorship.bind("<<ComboboxSelected>>", lambda _event: self.refresh_tab())
        self._button(filters, "Filter saved jobs", self.refresh_tab, "search")
        self._button(filters, "Clear filters", self.clear_filters, "refresh")
        summary = tk.Frame(frame, bg=p.BLACK)
        summary.pack(fill="x", pady=(0, 5))
        ttk.Checkbutton(summary, text="Show not eligible", variable=self.show_blocked, command=self.refresh_tab).pack(side="left", padx=(0, 12))
        self.previous_matches = self._button(summary, "Previous", lambda: self.page_matches(-1))
        self.next_matches = self._button(summary, "Next 200", lambda: self.page_matches(1))
        self.match_count = tk.Label(summary, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w", justify="left")
        self.match_count.pack(side="left", fill="x", expand=True)
        self.match_count.bind("<Configure>", lambda event: self.match_count.configure(wraplength=max(200, event.width - 4)))
        body = ttk.PanedWindow(frame, orient="horizontal")
        self.match_panes = body
        body.bind("<Configure>", self._place_match_sash)
        body.bind("<ButtonRelease-1>", self._remember_match_sash)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=p.BLACK)
        columns = ("score", "verdict", "title", "company", "where", "source", "posted", "pay")
        self.match_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse", style="Markets.Treeview")
        for key, label, width in (("score", "Score", 64), ("verdict", "Verdict", 124), ("title", "Title", 220), ("company", "Company", 130),
                                  ("where", "Where", 120), ("source", "Source", 90), ("posted", "Posted", 76), ("pay", "Pay", 190)):
            self.match_tree.heading(key, text=label)
            self.match_tree.column(key, width=width, stretch=key in ("title", "company", "where"), anchor="e" if key == "score" else "w")
        for verdict, colour in VERDICT_COLOURS.items():
            self.match_tree.tag_configure(verdict, foreground=colour)
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.match_tree.yview)
        across = ttk.Scrollbar(left, orient="horizontal", command=self.match_tree.xview)
        self.match_tree.configure(yscrollcommand=scroll.set, xscrollcommand=across.set)
        across.pack(side="bottom", fill="x")
        scroll.pack(side="right", fill="y")
        self.match_tree.pack(fill="both", expand=True)
        self.match_tree.bind("<Configure>", lambda event: self._fit_match_columns(event.width), add="+")
        self.match_tree.bind("<<TreeviewSelect>>", lambda _event: self.match_selected())
        self.match_tree.bind("<Double-1>", lambda _event: self.act("open"))
        body.add(left, weight=3)
        right = tk.Frame(body, bg=p.BLACK)
        actions = tk.Frame(right, bg=p.BLACK)
        actions.pack(fill="x", pady=(0, 6))
        self.job_actions: dict[str, ttk.Button] = {}
        for index, (label, action) in enumerate((("Prepare packet", "prepare"), ("Email review…", "send"), ("Open page", "open"),
                                                ("Assist form", "fill"), ("Applied", "applied"), ("Hide", "hide"))):
            button = ttk.Button(actions, text=label, style="Compact.Hayat.TButton", command=lambda action=action: self.act(action), state="disabled")
            button.grid(row=index // 3, column=index % 3, sticky="ew", padx=(0, 4), pady=(0, 4))
            self.job_actions[action] = button
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        self.application_note = tk.Label(right, text="Choose a job to see its application route and preparation steps.",
                                         bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w", justify="left", wraplength=410)
        self.application_note.pack(fill="x", pady=(0, 5))
        self.application_note.bind("<Configure>", lambda event: self.application_note.configure(wraplength=max(220, event.width - 4)))
        self.detail_tabs = ttk.Notebook(right)
        self.detail_tabs.pack(fill="both", expand=True)
        self.detail = self._text(self.detail_tabs, 24)
        self.detail_tabs.add(self.detail, text="Match")
        deep = tk.Frame(self.detail_tabs, bg=self.p.BLACK)
        bar = tk.Frame(deep, bg=self.p.BLACK)
        bar.pack(fill="x", pady=(4, 4))
        self._button(bar, "Ask HAYAT's view", lambda: self.act("narrative"), "spark")
        tk.Label(bar, text="CV fit · pay · preparation", bg=self.p.BLACK, fg=self.p.MUTED, font=("Segoe UI", 8)).pack(side="left", padx=6)
        self.deep = self._text(deep, 24)
        self.deep.pack(fill="both", expand=True)
        self.detail_tabs.add(deep, text="Deep look")
        self.sponsorship_text = self._text(self.detail_tabs, 24)
        self.detail_tabs.add(self.sponsorship_text, text="Sponsorship")
        self.preparation_text = self._text(self.detail_tabs, 24)
        self.preparation_text.tag_configure("plan-heading", foreground=p.GOLD, font=("Segoe UI Semibold", 10), spacing1=7, spacing3=3)
        self.preparation_text.tag_configure("plan-link", foreground="#87CEFA", underline=True)
        self.preparation_text.tag_bind("plan-link", "<Button-1>", self._open_plan_link)
        self.detail_tabs.add(self.preparation_text, text="Plan")
        self.say(self.preparation_text, "Choose a job to see its preparation checklist, skill practice and public research links.")
        body.add(right, weight=2)
        self.after_idle(self._place_match_sash)
        page.adopt(frame)
        return page

    #: Narrowest readable width for each match column, and the order columns
    #: leave a narrow table. Every hidden one is in the selected job's detail.
    MATCH_MIN_WIDTHS = {"score": 56, "verdict": 110, "title": 180, "company": 110, "where": 100,
                        "source": 80, "posted": 76, "pay": 110}
    MATCH_DROP_ORDER = ("source", "where", "posted")

    def _fit_match_columns(self, width: int) -> None:
        """Show whole columns that fit rather than slicing the last one in half.

        All eight columns needed about 1,000 pixels; on a 1536-pixel screen the
        matches pane is about 700, so the table ended in a header cut to "Sou".
        """
        if width <= 1:
            return
        shown = list(self.MATCH_MIN_WIDTHS)
        for column in self.MATCH_DROP_ORDER:
            if sum(self.MATCH_MIN_WIDTHS[key] for key in shown) <= width:
                break
            shown.remove(column)
        current = list(self.match_tree["displaycolumns"])
        if current != shown and not (current == ["#all"] and len(shown) == len(self.MATCH_MIN_WIDTHS)):
            self.match_tree.configure(displaycolumns=shown)
        spare = max(0, width - sum(self.MATCH_MIN_WIDTHS[key] for key in shown))
        growing = [key for key in ("title", "company", "pay", "where") if key in shown]
        for key in shown:
            extra = spare // len(growing) if key in growing else 0
            self.match_tree.column(key, width=self.MATCH_MIN_WIDTHS[key] + extra, stretch=False)

    def _place_match_sash(self, _event: Any = None) -> None:
        if not self._closed and self.match_panes.winfo_width() > 1:
            self.match_panes.sashpos(0, int(self.match_panes.winfo_width() * getattr(self, "_match_ratio", .48)))

    def _remember_match_sash(self, _event: Any = None) -> None:
        if self.match_panes.winfo_width() > 1:
            self._match_ratio = self.match_panes.sashpos(0) / self.match_panes.winfo_width()

    def search(self) -> None:
        if "search" in self._busy_actions:
            return
        if "browser_search" in self._busy_actions:
            self.status.configure(text="Stop or finish the browser search before starting the API search.")
            return
        center, query, remote = self.center, self.query.get().strip(), self.remote.get() or None
        location = self.location.get().strip()
        self._search_stop.clear()
        depth = self.search_depth.get()
        self.search_button.configure(state="disabled")
        self.stop_search_button.configure(state="normal")
        self.search_progress.pack(fill="x", pady=(0, 3), before=self.search_note)
        self.search_progress.start(12)
        self.search_note.configure(text="Reading configured public boards. Results will include source failures so you can see what was actually checked.",
                                   fg=self.p.MUTED)
        self.run("search", lambda: center.search(query=query, location=location, remote=remote, force=True, depth=depth,
                                                    check=lambda: not self._closed and not self._search_stop.is_set(),
                                                    progress=lambda value: self._results.put(("search_progress", 0, value, None))), "Searching the job boards…")

    def stop_search(self) -> None:
        self._search_stop.set()
        self.search_note.configure(text="Stopping after the current network request. Retrieved pages will be kept.")
        self.stop_search_button.configure(state="disabled")

    def page_matches(self, direction: int) -> None:
        self._match_offset = max(0, self._match_offset + direction * 200)
        self.match_tree.yview_moveto(0)
        self.refresh_tab()

    def browse_platform(self) -> None:
        query, location = self.query.get().strip(), self.location.get().strip()
        platform, scan = self.browser_platform.get(), self.browser_scan.get()
        engine = self.browser_engine.get()
        if engine == "Auto":
            if "browser_search" in self._busy_actions or "search" in self._busy_actions:
                self.status.configure(text="Stop or finish the running search before starting Auto.")
                return
            self.browser_scan.set(True)
            self._search_stop.clear()
            self.stop_search_button.configure(state="normal")
            self.run("browser_search", lambda: self.center.browser_search(platform, query, location, True, engine=engine,
                     check=lambda: not self._closed and not self._search_stop.is_set()), "Trying public search providers...")
            return
        self.run("browser_search", lambda: self.center.browser_search(platform, query, location, scan, engine=engine), "Opening job search in the browser...")

    def continue_browser(self) -> None:
        self.run("browser_search", lambda: self.center.browser_search(resume=True), "Checking the existing browser tab...")

    def show_browser_search(self, value: dict[str, Any]) -> None:
        self.continue_browser_button.configure(state="normal" if value.get("manual_interaction_required") else "disabled")
        self.search_note.configure(text=value["next_step"], fg=self.p.GOLD if value.get("manual_interaction_required") else self.p.MUTED)
        # Keep the continuation button visible while the user completes the check.
        self.tabs.select(TABS.index("Matches" if value.get("manual_interaction_required") else "Sources"))
        text = value["next_step"] + "\n" + value["url"]
        text += "".join(f"\n{item['engine']}: {item['status']}" + (f"; {item['error']}" if item.get("error") else "")
                        + (f"; retry after {item['retry_at']}" if item.get("retry_at") else "") for item in value.get("attempts", []))
        if page := value.get("page"):
            text += "\n\n" + str(page.get("title", "")) + "\n" + str(page.get("text", ""))[:18000]
        self.say(self.discovery_text, text)

    def clear_filters(self) -> None:
        self.query.set("")
        self.location.set("")
        self.remote.set(False)
        self.show_blocked.set(False)
        self.sponsor_filter.set("all")
        self.sort_by.set("score")
        self.refresh_tab()

    def show_search(self, value: dict[str, Any]) -> None:
        sources = value.get("read", [])
        good = [item for item in sources if not item.get("error")]
        failed = [item for item in sources if item.get("error")]
        summary = f"Search finished: {value.get('new', 0)} new jobs; {len(good)} source(s) read"
        if "matching_total" in value:
            summary += f"; {value['matching_total']} match the search"
        if failed:
            summary += f"; {len(failed)} source(s) unavailable."
            summary += "\n" + " | ".join(f"{item.get('source', 'Source')}: {' '.join(str(item['error']).split())[:180]}" for item in failed[:4])
            if len(failed) > 4:
                summary += " | More details in Sources."
        if coverage := value.get("coverage"):
            summary += f"\n{coverage.get('depth', 'normal')} search - {coverage.get('requests', 'unknown')} requests - {coverage.get('stop_reason', 'configured sources checked')}. Not every platform is searchable by API."
        limited = [item for item in sources if item.get("limited")]
        if limited:
            summary += "\nCoverage: " + " | ".join(f"{item['source']}: {item.get('pages', 0)} page(s), {item.get('stop_reason', 'partial results')}" for item in limited)
        retries = [item for item in failed if item.get("retry_at")]
        if retries:
            summary += "\nRetry after: " + " | ".join(f"{item['source']}: {item['retry_at']}" for item in retries)
        if value.get("empty_reason"):
            summary += "\n" + value["empty_reason"]
        elif not sources and not value.get("deferred_sources"):
            summary += ". No boards were read; choose sources or discover an employer in Sources."
        if value.get("deferred_sources"):
            summary += "\nSource cooldowns: " + " | ".join(f"{item['source']}: {item['reason']}, retry after {item['retry_at']}" for item in value["deferred_sources"])
        self.search_note.configure(text=summary, fg=self.p.GOLD if failed else self.p.MUTED)
        self.refresh_tab()

    def show_matches(self, value: dict[str, Any]) -> None:
        tree = self.match_tree
        position, across = tree.yview()[0], tree.xview()[0]
        tree.delete(*tree.get_children())
        self.match_rows = {}
        for row in value["matches"]:
            iid = str(row["number"])
            self.match_rows[iid] = row
            tree.insert("", "end", iid=iid, values=(row["score"], row["verdict"] + (f" · {row['application']}" if row.get("application") else ""),
                                                    row["title"], row["company"], (row.get("mode") or "") + (" · " + row["location"][:30] if row["location"] else ""),
                                                    row["source_name"], (row["posted_at"] or "")[:10], row.get("pay") or "Not stated"),
                        tags=(row["verdict"],))
        counts = value["counts"]
        shown = len(value["matches"])
        total = value.get("matching_total", shown)
        offset = value.get("offset", 0)
        self.previous_matches.configure(state="normal" if offset else "disabled")
        self.next_matches.configure(state="normal" if value.get("next_offset") is not None else "disabled")
        self.match_count.configure(text=f"Showing {offset + 1 if shown else 0}-{offset + shown} of {total} matches · {counts.get('jobs', 0)} saved · {counts.get('verdict_strong', 0)} strong · "
                                   f"{counts.get('verdict_good', 0)} good" + ("" if value.get("profile_ready") else
                                                                              " · read your CV in Profile for real scores"))
        from app.gui_empty import show_empty
        show_empty(tree, "No saved jobs match these filters. Search now or widen the filters.", self.p)
        if not value["matches"]:
            self.selected = None
            self._set_application_actions()
            for box in (self.detail, self.deep, self.sponsorship_text, self.preparation_text):
                self.say(box, "No saved jobs match the current query and filters. Search now, or widen sponsorship, remote and eligibility filters.")
        if self.selected is not None and str(self.selected) in self.match_rows:
            tree.selection_set(str(self.selected))
        elif self.selected is not None:
            self.selected = None
            self._set_application_actions()
            for box in (self.detail, self.deep, self.sponsorship_text, self.preparation_text):
                self.say(box, "Choose a job from the filtered results.")
        tree.yview_moveto(position)
        tree.xview_moveto(across)

    def match_selected(self) -> None:
        selection = self.match_tree.selection()
        if not selection:
            return
        revision = self.match_rows.get(selection[0], {}).get("revision")
        if self.selected == int(selection[0]) and revision == getattr(self, "_selected_revision", None):
            return  # Rebuilding the table must not restart the selected report.
        self.selected = int(selection[0])
        self._selected_revision = revision
        self._set_application_actions(loading=True)
        center, number = self.center, self.selected
        self.run("detail", lambda: center.detail(number))
        self.say(self.deep, "Reading the role, your CV and the market around it…")
        self.run("insight", lambda: center.insight(number))
        self.say(self.preparation_text, "Building the application preparation checklist...")
        self.say(self.sponsorship_text, "Reading posting evidence and employer history…")
        self.run("sponsorship", lambda: center.sponsorship(number))

    def show_detail(self, value: dict[str, Any]) -> None:
        if value["job"]["number"] != self.selected:
            return
        from app.jobs.tools import report_lines
        self.say(self.detail, "\n".join(report_lines(value)))
        self._set_application_actions(value)

    def _set_application_actions(self, value: dict[str, Any] | None = None, *, loading: bool = False) -> None:
        for button in self.job_actions.values():
            button.configure(state="disabled")
        if value is None:
            self.application_note.configure(text="Reading the application route..." if loading else "Choose a job to see its application route and preparation steps.")
            return
        for action in ("prepare", "applied", "hide"):
            self.job_actions[action].configure(state="normal")
        job = value.get("job", {})
        web = bool(re.match(r"https?://", str(job.get("apply_url") or job.get("url") or "")))
        email = bool(value.get("reading", {}).get("apply_emails"))
        routes = {item["action"]: item for item in value.get("application_routes", [])}
        can_email = bool(routes.get("send", {}).get("available", email and self.center.mailer is not None and self.center.gate is not None))
        can_fill = bool(routes.get("fill", {}).get("available", web and self.center.browser is not None))
        status = (value.get("application_record") or {}).get("status")
        already_applied = status in ("applied", "interview", "offer")
        can_email = can_email and not already_applied
        self.job_actions["open"].configure(state="normal" if web else "disabled")
        self.job_actions["send"].configure(state="normal" if can_email else "disabled")
        self.job_actions["fill"].configure(state="normal" if can_fill else "disabled")
        if already_applied:
            note = f"Marked {status}. Use Applications to track the outcome and follow-up; an email is not sent again."
        elif email:
            note = "Email application: prepare your packet, then review the email before confirming it."
            if not can_email:
                note = "Email application: prepare your packet. Connect an email account and confirmation window to enable Email review."
                if routes.get("send", {}).get("setup_problem"):
                    note += " " + routes["send"]["setup_problem"]
        elif web:
            note = "Web application: prepare your packet, then open the employer's page and attach your CV."
            note += " Assist form fills known facts for your review; you submit." if can_fill else " Browser form assistance is unavailable in this session."
        else:
            note = "No supported application address is listed. Check the original posting before applying."
        self.application_note.configure(text=note)

    def show_insight(self, value: dict[str, Any]) -> None:
        if value["number"] != self.selected:
            return  # an answer for a job no longer chosen
        from app.jobs.tools import insight_lines
        self.say(self.deep, "\n".join(insight_lines(value)))
        from app.jobs.readiness import plan_lines
        if value.get("application_plan"):
            text = "\n".join(plan_lines(value["application_plan"]))
            self.say(self.preparation_text, text)
            for number, line in enumerate(text.splitlines(), 1):
                if number == 1 or (line and not line.startswith(" ") and line.endswith(":")):
                    self.preparation_text.tag_add("plan-heading", f"{number}.0", f"{number}.end")
                for link in re.finditer(r"https://[^\s]+", line):
                    self.preparation_text.tag_add("plan-link", f"{number}.{link.start()}", f"{number}.{link.end()}")

    def _open_plan_link(self, event: Any) -> str:
        box = self.preparation_text
        index = box.index(f"@{event.x},{event.y}")
        for start, end in zip(box.tag_ranges("plan-link")[::2], box.tag_ranges("plan-link")[1::2]):
            if box.compare(start, "<=", index) and box.compare(index, "<", end):
                url = box.get(start, end)
                opener = getattr(self.gui, "_open_source", self.center.opener)
                opener(url)
                break
        return "break"

    show_narrative = show_insight

    def show_sponsorship(self, value: dict[str, Any]) -> None:
        if value.get("number") != self.selected:
            return
        from app.jobs.sponsorship import research_lines
        self.say(self.sponsorship_text, "\n".join(research_lines(value)))

    def act(self, action: str) -> None:
        if self.selected is None:
            self.say(self.detail, "Choose a job first.")
            return
        center, number = self.center, self.selected
        calls = {"prepare": ("prepare", lambda: center.prepare(number), "Writing the application packet…"),
                 "send": ("send", lambda: center.send(number), "Preparing the confirmation…"),
                 "open": ("open", lambda: center.open(number), ""),
                 "fill": ("fill", lambda: center.fill(number), "Opening the employer's page and filling what HAYAT knows…"),
                 "narrative": ("narrative", lambda: center.insight(number, explain=True), "HAYAT is reading this job…"),
                 "applied": ("status", lambda: center.set_status(number, "applied", "marked from the Jobs window"), ""),
                 "hide": ("hide", lambda: center.hide(number), "")}
        kind, call, busy = calls[action]
        self.run(kind, call, busy)

    def _show_reply(self, value: dict[str, Any]) -> None:
        from app.jobs.tools import report_lines
        self.say(self.detail, "\n".join(report_lines(value)))
        self.refresh_tab()

    show_prepare = show_send = show_open = show_status = show_fill = _show_reply

    def show_hide(self, value: dict[str, Any]) -> None:
        self.selected = None
        self._set_application_actions()
        self.say(self.detail, f"Job {value['hidden']} is hidden.")
        self.refresh_tab()

    # ------------------------------------------------------------------ applications
    def _build_applications(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        table = tk.Frame(frame, bg=p.BLACK)
        table.pack(fill="both", expand=True, pady=(6, 6))
        columns = ("title", "company", "status", "updated", "applied", "follow")
        self.app_tree = ttk.Treeview(table, columns=columns, show="headings", selectmode="browse", style="Markets.Treeview", height=14)
        for key, label, width in (("title", "Title", 260), ("company", "Company", 160), ("status", "Status", 90), ("updated", "Updated", 90),
                                  ("applied", "Applied", 90), ("follow", "Follow up", 90)):
            self.app_tree.heading(key, text=label)
            self.app_tree.column(key, width=width, stretch=key in ("title", "company"))
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.app_tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.app_tree.xview)
        self.app_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        xbar.pack(side="bottom", fill="x")
        ybar.pack(side="right", fill="y")
        self.app_tree.pack(fill="both", expand=True)
        bar = tk.Frame(frame, bg=p.BLACK)
        bar.pack(fill="x")
        from app.jobs.store import STATUSES
        self.new_status = StringVar(self, "applied")
        ttk.Combobox(bar, textvariable=self.new_status, values=STATUSES, state="readonly", width=12).pack(side="left", padx=(0, 6))
        self._button(bar, "Set status", self.set_status, "check")
        self._button(bar, "Open packet folder", self.open_folder, "folder")
        self.app_note = tk.Label(frame, text="", bg=p.BLACK, fg=p.MUTED, anchor="w", justify="left")
        self.app_note.pack(fill="x", pady=(6, 0))
        page.adopt(frame)
        return page

    def show_applications(self, value: dict[str, Any]) -> None:
        tree = self.app_tree
        selected, vertical, horizontal = tree.selection(), tree.yview()[0], tree.xview()[0]
        tree.delete(*tree.get_children())
        self.app_rows = {str(row["number"]): row for row in value["applications"]}
        for row in value["applications"]:
            tree.insert("", "end", iid=str(row["number"]), values=(row["title"], row["company"], row["status"], row["updated_at"][:10],
                                                                    (row.get("applied_at") or "")[:10],
                                                                    (row.get("follow_up_at") or "")[:10] if row["status"] == "applied" else ""))
        self.app_note.configure(text=f"{len(value['applications'])} application(s). Applied ones get a follow-up draft after a week "
                                     "if you hear nothing; nothing is sent on its own.")
        from app.gui_empty import show_empty
        show_empty(tree, "No applications yet. Choose a job in Matches and press Prepare application.", self.p)
        if selected and selected[0] in self.app_rows:
            tree.selection_set(selected[0])
        tree.yview_moveto(vertical)
        tree.xview_moveto(horizontal)

    def set_status(self) -> None:
        selection = self.app_tree.selection()
        if not selection:
            self.app_note.configure(text="Choose an application first.")
            return
        center, number, status = self.center, int(selection[0]), self.new_status.get()
        self.run("status_tracker", lambda: center.set_status(number, status, "changed in the Jobs window"))

    def show_status_tracker(self, value: dict[str, Any]) -> None:
        self.app_note.configure(text=f"Job {value['number']} is now {value['status']}.")
        self.refresh_tab()

    def open_folder(self) -> None:
        selection = self.app_tree.selection()
        row = getattr(self, "app_rows", {}).get(selection[0]) if selection else None
        if not row or not row.get("folder"):
            self.app_note.configure(text="This one has no packet yet: prepare it from Matches.")
            return
        if hasattr(os, "startfile"):
            os.startfile(row["folder"])  # the owner's own packet folder, opened in Explorer

    # ------------------------------------------------------------------ profile
    def _build_profile(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        bar = tk.Frame(frame, bg=p.BLACK)
        bar.pack(fill="x", pady=(6, 6))
        self._button(bar, "Read my CV…", self.read_cv, "folder")
        self.skill = StringVar(self, "")
        ttk.Entry(bar, textvariable=self.skill, width=22).pack(side="left", padx=(12, 6))
        self._button(bar, "Add skill", lambda: self.change_skill(True), "plus")
        self._button(bar, "Remove skill", lambda: self.change_skill(False), "stop")
        body = tk.Frame(frame, bg=p.BLACK)
        body.pack(fill="both", expand=True)
        self.profile_text = self._text(body, 20)
        self.profile_text.pack(side="left", fill="both", expand=True, padx=(0, 10))
        from app.gui_panels import ScrollableColumn
        self.profile_scroll = ScrollableColumn(body, p, width=510)
        self.profile_scroll.pack(side="right", fill="y")
        form = self.profile_scroll.body
        tk.Label(form, text="Preferences", bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 10)).grid(row=0, column=0, columnspan=2, sticky="w")
        self.fields: dict[str, Any] = {}
        rows = (("target_titles", "Target titles (comma)"), ("search_terms", "Search terms (comma)"), ("locations", "Your places"),
                ("countries_ok", "Countries you may work in"), ("min_salary", "Minimum yearly pay"), ("salary_currency", "Currency"),
                ("level", "Level (junior…lead)"), ("exclude_words", "Exclude title words"), ("exclude_companies", "Exclude companies"),
                ("companies", "Follow boards (greenhouse:x)"), ("notify_score", "Tell me from score"), ("notice_period", "Notice period"),
                ("work_authorization", "Work authorisation"), ("salary_expectation", "Salary expectation"),
                ("work_status", "US work status (self-reported)"))
        for index, (key, label) in enumerate(rows, 1):
            tk.Label(form, text=label, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 8)).grid(row=index, column=0, sticky="w", pady=1)
            variable = StringVar(self, "")
            if key == "work_status":
                from app.jobs.profile import WORK_STATUSES
                ttk.Combobox(form, textvariable=variable, values=("", *WORK_STATUSES), state="readonly", width=26).grid(
                    row=index, column=1, sticky="we", pady=1)
            else:
                ttk.Entry(form, textvariable=variable, width=28).grid(row=index, column=1, sticky="we", pady=1)
            self.fields[key] = variable
        self.remote_only = BooleanVar(self, False)
        self.willing_to_relocate = BooleanVar(self, False)
        self.auto_prepare = BooleanVar(self, False)
        ttk.Checkbutton(form, text="Remote only", variable=self.remote_only).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(form, text="Willing to relocate (authorization still needs checking)",
                        variable=self.willing_to_relocate).grid(row=len(rows) + 2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(form, text="Prepare strong-match packets automatically (never sends)", variable=self.auto_prepare).grid(
            row=len(rows) + 3, column=0, columnspan=2, sticky="w")
        save = ttk.Button(form, text="Save preferences", style="Compact.Hayat.TButton", command=self.save_preferences)
        save.grid(row=len(rows) + 4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.profile_scroll.adopt(form)
        page.adopt(frame)
        return page

    def read_cv(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Your CV", filetypes=[("CV", "*.pdf *.docx *.txt *.md"), ("All files", "*.*")])
        if path:
            center = self.center
            self.run("profile", lambda: center.read_cv(path), "Reading your CV…")

    def change_skill(self, add: bool) -> None:
        skill, center = self.skill.get().strip(), self.center
        if skill:
            self.run("profile", lambda: center.change_skill(skill, add))

    def save_preferences(self) -> None:
        changes: dict[str, Any] = {key: variable.get() for key, variable in self.fields.items()}
        changes["min_salary"] = changes["min_salary"] or None
        changes["notify_score"] = changes["notify_score"] or 70
        changes["remote_only"], changes["auto_prepare"] = self.remote_only.get(), self.auto_prepare.get()
        changes["willing_to_relocate"] = self.willing_to_relocate.get()
        center = self.center
        self.run("preferences", lambda: center.set_preferences(changes), "Saving and re-scoring…")

    def show_profile(self, value: dict[str, Any], *, saved: bool = False) -> None:
        self.show_onboarding(value)
        from app.jobs.tools import report_lines
        self.say(self.profile_text, "\n".join(report_lines({**value, "profile_view": True})))
        preferences = value["preferences"]
        for key, variable in self.fields.items():
            current = preferences.get(key)
            rendered = ", ".join(current) if isinstance(current, list) else "" if current is None else str(current)
            self._load_preference(key, variable, rendered, "", value.get("_ui_fields", {}), saved)
        for key, variable in (("remote_only", self.remote_only), ("willing_to_relocate", self.willing_to_relocate),
                              ("auto_prepare", self.auto_prepare)):
            self._load_preference(key, variable, bool(preferences.get(key)), False, value.get("_ui_fields", {}), saved)

    def show_preferences(self, value: dict[str, Any]) -> None:
        self.show_profile(value, saved=True)

    def show_onboarding(self, value: dict[str, Any]) -> None:
        preferences = value.get("preferences", {})
        steps = []
        if not value.get("cv_file"):
            steps.append("Add your CV in Profile for evidence-based matching")
        elif not value.get("skills"):
            steps.append("Check the skills read from your CV in Profile")
        if not preferences.get("target_titles") and not preferences.get("search_terms"):
            steps.append("set your target roles or search terms")
        if not preferences.get("min_salary"):
            steps.append("set a pay target to compare high-paying roles")
        if steps:
            text = "Next: " + "; ".join(steps) + ". You can search any role now."
        else:
            targets = preferences.get("target_titles") or preferences.get("search_terms") or []
            text = "Profile ready · " + ", ".join(targets[:3])
            if preferences.get("remote_only"):
                text += " · remote preference"
            text += ". Search → inspect evidence → prepare → review and apply."
        self.setup_note.configure(text=text)

    def _load_preference(self, key: str, variable: Any, incoming: Any, initial: Any,
                         snapshot: dict[str, Any], saved: bool) -> None:
        # A background read may finish after typing started. Update clean fields;
        # retain edits until they are saved, even if another profile read arrives.
        current = variable.get()
        unchanged = current == snapshot.get(key, current)
        if unchanged and (saved or current == self._profile_loaded.get(key, initial)):
            variable.set(incoming)
        self._profile_loaded[key] = incoming

    # ------------------------------------------------------------------ CV review
    def _build_review(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        tk.Label(frame, text="How your CV reads against your best matches. Suggestions only: add a skill or a number only when it is true.",
                 bg=p.BLACK, fg=p.MUTED, anchor="w").pack(fill="x", pady=(6, 4))
        self.review_text = self._text(frame, 26)
        self.review_text.pack(fill="both", expand=True)
        page.adopt(frame)
        return page

    def show_review(self, value: dict[str, Any]) -> None:
        from app.jobs.tools import report_lines
        self.say(self.review_text, "\n".join(report_lines(value)))

    # ------------------------------------------------------------------ learn
    def _build_learn(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        tk.Label(frame, text="What to learn next, from your current matches: skills ranked by the jobs they unlock and the pay "
                 "they open, with where to learn each. Patterns from collected jobs, never a promise of an offer.",
                 bg=p.BLACK, fg=p.MUTED, anchor="w", justify="left", wraplength=1080).pack(fill="x", pady=(6, 4))
        self.learn_text = self._text(frame, 26)
        self.learn_text.pack(fill="both", expand=True)
        page.adopt(frame)
        return page

    def show_learn(self, value: dict[str, Any]) -> None:
        from app.jobs.tools import report_lines
        self.say(self.learn_text, "\n".join(report_lines(value)))

    # ------------------------------------------------------------------ sources
    def _build_sources(self) -> tk.Frame:
        p = self.p
        from app.gui_jobs_page import JobsPage
        page = JobsPage(self.tabs, p)
        self.pages.append(page)
        frame = page.body
        discovery = tk.Frame(frame, bg=p.BLACK)
        discovery.pack(fill="x", pady=6)
        tk.Label(discovery, text="Employer name / careers URL", bg=p.BLACK, fg=p.MUTED).pack(side="left", padx=(0, 6))
        self.company_query = StringVar(self, "")
        ttk.Entry(discovery, textvariable=self.company_query, width=25).pack(side="left", padx=(0, 6))
        self.follow_company = BooleanVar(self, False)
        ttk.Checkbutton(discovery, text="Follow if found", variable=self.follow_company).pack(side="left", padx=(0, 6))
        self._button(discovery, "Discover", self.discover_company, "search")
        self.discovery_text = self._text(frame, 6)
        self.discovery_text.pack(fill="x")
        self.say(self.discovery_text, "Discover a company's public careers board by name, then follow it to include its roles in searches. "
                 "Sponsor research shows official references and leads from an imported employer-history CSV. Historical decisions do not prove current sponsorship.")
        table = tk.Frame(frame, bg=p.BLACK)
        self.source_tree = ttk.Treeview(table, columns=("about", "state", "read", "kept", "error"), show="tree headings", height=10,
                                        style="Markets.Treeview")
        for key, label, width in (("#0", "Source", 140), ("about", "Covers", 300), ("state", "Last read (UTC)", 130), ("read", "Read", 60),
                                  ("kept", "Kept", 60), ("error", "Problem", 220)):
            self.source_tree.heading(key, text=label)
            self.source_tree.column(key, width=width, stretch=key in ("about", "error"))
        ybar = ttk.Scrollbar(table, orient="vertical", command=self.source_tree.yview)
        xbar = ttk.Scrollbar(table, orient="horizontal", command=self.source_tree.xview)
        self.source_tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.source_tree.bind("<<TreeviewSelect>>", lambda _event: self.inspect_source())
        xbar.pack(side="bottom", fill="x")
        ybar.pack(side="right", fill="y")
        self.source_tree.pack(fill="both", expand=True, pady=(6, 6))
        from app.gui_panels import ButtonRow
        bar = ButtonRow(frame, p)
        bar.pack(side="bottom", fill="x")
        bar.add("Search & prepare round", self.round_now, "Compact.Hayat.TButton")
        bar.add("Enable / pause selected", self.toggle_source, "Compact.Hayat.TButton")
        bar.add("Sponsor research", lambda: self.run("sponsor_overview", self.center.sponsorship), "Compact.Hayat.TButton")
        bar.add("Import sponsor CSV…", self.import_sponsors, "Compact.Hayat.TButton")
        self.open_source_button = bar.add("Open official site", self.open_selected_source, "Compact.Hayat.TButton")
        self.open_source_button.configure(state="disabled")
        self.source_note = tk.Label(frame, text="", bg=p.BLACK, fg=p.MUTED, anchor="w", justify="left", wraplength=940)
        self.source_note.pack(side="bottom", fill="x", pady=6)
        self.source_note.bind("<Configure>", lambda event: self.source_note.configure(wraplength=max(300, event.width - 4)))
        self.source_access_text = self._text(frame, 5)
        self.source_access_text.pack(side="bottom", fill="x", pady=(5, 0))
        self.say(self.source_access_text, "Choose a source to inspect its access state, cooldown and official manual route.")
        table.pack(fill="both", expand=True)
        page.adopt(frame)
        return page

    def inspect_source(self) -> None:
        selected = self.source_tree.selection()
        self.open_source_button.configure(state="normal" if selected else "disabled")
        if selected:
            source = selected[0]
            self.run("source_access", lambda: self.center.source_access(source))
        else:
            self.say(self.source_access_text, "Choose a source to inspect its access state, cooldown and official manual route.")

    def show_source_access(self, value: dict[str, Any]) -> None:
        if self.source_tree.selection() != (value["source"],):
            return
        from app.jobs.tools import report_lines
        self.say(self.source_access_text, "\n".join(report_lines(value)))

    def open_selected_source(self) -> None:
        selected = self.source_tree.selection()
        if selected:
            source = selected[0]
            self.run("source_open", lambda: self.center.open_source(source), "Opening the official source in your browser...")

    def show_source_open(self, value: dict[str, Any]) -> None:
        self.show_source_access(value)

    def discover_company(self) -> None:
        company, follow = self.company_query.get().strip(), self.follow_company.get()
        if not company:
            self.say(self.discovery_text, "Enter an employer name to check its public Greenhouse, Lever or Ashby board.")
            return
        self.run("discovery", lambda: self.center.discover_company(company, follow), "Checking public employer boards…")

    def show_discovery(self, value: dict[str, Any]) -> None:
        from app.jobs.tools import report_lines
        self.say(self.discovery_text, "\n".join(report_lines(value)))
        if value.get("followed"):
            self.run("sources", self._sources)

    def import_sponsors(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="USCIS employer-history CSV", filetypes=[("CSV", "*.csv")])
        if path:
            self.run("sponsor_import", lambda: self.center.import_sponsors(path), "Importing employer history…")

    def show_sponsor_import(self, value: dict[str, Any]) -> None:
        from app.jobs.tools import report_lines
        self.say(self.discovery_text, "\n".join(report_lines(value)))
        if self.selected is not None:
            number = self.selected
            self.run("sponsorship", lambda: self.center.sponsorship(number))

    def show_sponsor_overview(self, value: dict[str, Any]) -> None:
        from app.jobs.sponsorship import research_lines
        self.say(self.discovery_text, "\n".join(research_lines(value)))

    def show_sources(self, value: dict[str, Any]) -> None:
        tree = self.source_tree
        selected, vertical, horizontal = tree.selection(), tree.yview()[0], tree.xview()[0]
        tree.delete(*tree.get_children())
        self._source_settings = {"enabled": list(value["enabled"]), "companies": list(value["companies"]),
                                 "feeds": {item["name"] for item in value["catalog"] if item["kind"] == "feed"}}
        state = value["state"]
        cooldowns = value.get("cooldowns", {})
        def problem(name, seen):
            retry = cooldowns.get(name.split(":", 1)[0], {}).get("retry_at")
            return (seen.get("error") or "") + (f" · Retry after {retry}" if retry else "")
        for item in value["catalog"]:
            if item["kind"] != "feed":
                continue
            seen = state.get(item["name"]) or {}
            on = item["name"] in value["enabled"]
            tree.insert("", "end", iid=item["name"], text=("● " if on else "○ ") + item["title"],
                        values=(item["about"], str(seen.get("fetched_at") or "")[:16].replace("T", " "), seen.get("read", ""),
                                seen.get("kept", ""), problem(item["name"], seen)))
        for company in value["companies"]:
            seen = state.get(company) or {}
            tree.insert("", "end", iid=company, text="● " + company, values=("a company's own careers board",
                                                                             str(seen.get("fetched_at") or "")[:16].replace("T", " "),
                                                                             seen.get("read", ""), seen.get("kept", ""), problem(company, seen)))
        watcher = value["watcher"]
        schedule = (f"Automatic round every {watcher['every_hours']:g} h" if watcher.get("running")
                    else "Searches run when you start them")
        self.source_note.configure(text=schedule
                                        + (f"; last round {watcher['last_round'][:16].replace('T', ' ')} UTC" if watcher.get("last_round") else "; no previous round")
                                        + ". Public feeds are read at most hourly unless explicitly refreshed. Confirm the employer's identity "
                                          "and role-specific sponsorship before applying; a board slug can differ from its legal name. "
                                          "Explicit refresh respects publisher cooldowns. Enabling a source does not grant website permissions.")
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])
        tree.yview_moveto(vertical)
        tree.xview_moveto(horizontal)

    def toggle_source(self) -> None:
        selection = self.source_tree.selection()
        settings = getattr(self, "_source_settings", {})
        if not selection or not settings:
            self.source_note.configure(text="Choose a feed to enable or pause it. Choose a followed employer board to stop following it.")
            return
        name = selection[0]
        if name in settings["feeds"]:
            sources = list(settings["enabled"])
            if name in sources:
                sources.remove(name)
            else:
                sources.append(name)
            changes = {"sources": sources}
        elif name in settings["companies"]:
            changes = {"companies": [company for company in settings["companies"] if company != name]}
        else:
            return
        self.run("source_settings", lambda: self.center.set_preferences(changes), "Saving the selected source...")

    def show_source_settings(self, value: dict[str, Any]) -> None:
        self.show_onboarding(value)
        self.run("sources", self._sources)

    def round_now(self) -> None:
        watcher = self.watcher
        if watcher is None:
            self.source_note.configure(text="The automatic round is not available in this session.")
            return
        self.run("round", watcher.run_once, "Running the automatic round…")

    def show_round(self, value: dict[str, Any]) -> None:
        from app.jobs.watcher import round_text
        self.source_note.configure(text=round_text(value) or "The round found nothing new.")
        self.refresh_tab()
