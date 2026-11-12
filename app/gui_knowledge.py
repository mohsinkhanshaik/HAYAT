"""Searchable evidence library; database work stays off the Tk thread."""
from __future__ import annotations

import queue
import threading
from app.gui_resources import BooleanVar, StringVar
from app.gui_output import DisplayVar

import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk
from typing import Any

from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.logging import redact
from app.gui_fonts import DISPLAY


QUALITY_FILTERS = {
    "Supported and disputed": "all",
    "Verified and current": "verified_current",
    "Needs refresh": "needs_refresh",
    "Unresolved conflicts": "conflicts",
    "Missing direct evidence": "missing_evidence",
}


def self_check_text(stats: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    report = report or {}
    lines = [
        "HAYAT self-check",
        f"Evidence measured: {stats.get('measured_at') or 'Unknown'}",
        f"Verified and current: {stats.get('verified_current_claims', 'Unknown')}",
        f"Claims needing refresh: {stats.get('stale_claims', 'Unknown')}",
        f"Claims without a refresh deadline: {stats.get('undated_claims', 'Unknown')}",
        f"Invalid refresh deadlines: {stats.get('invalid_deadlines', 'Unknown')}",
        f"Claims missing direct evidence (including unverified): {stats.get('claims_missing_direct_evidence', 'Unknown')}",
        f"Unresolved conflicts: {stats.get('unresolved_conflicts', 'Unknown')}",
        "", "A current claim has retained direct evidence, a future refresh deadline, and no unresolved conflict. "
        "These checks measure evidence hygiene, not the probability that a claim is true.",
        "", "Learning priorities:",
    ]
    for item in report.get("top_priorities") or []:
        visibility = "public research eligible" if item.get("public_safe") else "local review only"
        lines.extend([f"• {item.get('topic', 'Unknown topic')} ({visibility})",
                      f"  Reason: {item.get('reason', 'Unspecified')}",
                      f"  Verify with: {item.get('verification', 'Unspecified')}"])
    if not report.get("top_priorities"):
        lines.append("No priorities reported; this alone does not establish that there are no gaps.")
    errors = report.get("observation_errors") or {}
    errors = {**errors, **{f"sensor {key}": value for key, value in
                         ((report.get("senses") or {}).get("observation_errors") or {}).items()}}
    if errors:
        lines.extend(["", "Measurements unavailable:", *[f"• {key}: {value}" for key, value in errors.items()]])
    lines.extend(["", "Operational self-monitoring does not establish subjective consciousness. "
                  "Refreshing this view does not start research or change model weights."])
    return "\n".join(lines)


def evidence_text(row: dict[str, Any]) -> str:
    freshness = "Needs refresh" if row.get("stale") else "Within its freshness window" if row.get("valid_until") else "No refresh deadline recorded"
    lines = [str(row.get("claim_text", "")), "", f"Evidence status: {row.get('status', 'UNKNOWN')}",
             f"Freshness: {freshness}", f"Last verified: {row.get('last_verified') or row.get('updated_at') or 'Unknown'}",
             f"Refresh by: {row.get('valid_until') or 'Unspecified'}", "",
             "Sources (support scores are heuristics, not accuracy measurements):"]
    for evidence in row.get("evidence", []):
        lines.extend([f"• {evidence.get('title') or 'Source'}", str(evidence.get("final_url") or ""),
                      f"Support: {evidence.get('support_type', 'UNKNOWN')}", str(evidence.get("excerpt") or ""), ""])
    if not row.get("evidence"):
        lines.append("No retained source evidence.")
    if row.get("has_unresolved_conflict"):
        lines.append("Unresolved conflicting evidence exists. Do not treat this claim as settled.")
    return "\n".join(lines)


def queue_public_topic(runtime: Any, topic: str) -> str:
    if getattr(getattr(runtime, "authority", None), "privacy_mode", False):
        return "Privacy mode is on. Turn it off before queuing public research."
    learner = getattr(runtime, "learner", None)
    if learner is None:
        return "Background learning is unavailable."
    if not learner.enqueue(topic, source="owner_knowledge_library", priority=85, relearn=True):
        return "Topic was not queued. Use a public, non-sensitive question; it may also already be queued."
    return "Topic queued. The learner will use its normal privacy, resource, and evidence checks."


class KnowledgeStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.store = gui.runtime.research.knowledge
        self.changes: queue.Queue = queue.Queue()
        self.generation = 0
        self.closed = False
        self.rows: dict[str, dict[str, Any]] = {}
        self._debounce = self._poll_id = None
        self._learning_busy = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hayat-knowledge")
        self._search_future = None
        self.title("HAYAT · Knowledge Library")
        self.configure(bg=gui.BLACK)
        self.transient(gui.root)
        self.minsize(620, 560)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self._build()
        self.bind("<Control-l>", lambda _event: self.entry.focus_set())
        self.bind("<Control-r>", lambda _event: self.refresh())
        place_over(self, gui.root, (920, 720))
        self.deiconify()
        self.refresh()
        self._poll()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="Knowledge Library", bg=p.BLACK, fg=p.WHITE,
                 font=(DISPLAY, 18)).pack(side="left")
        ttk.Button(header, text="Brain 5D", command=self.open_brain).pack(side="right")
        self.summary = DisplayVar(self, "Reading stored evidence…")
        self.summary_label = tk.Label(self, textvariable=self.summary, bg=p.PANEL, fg=p.HOLO, anchor="w",
                 padx=18, pady=12, font=("Segoe UI", 10), wraplength=700, justify="left")
        self.summary_label.pack(fill="x", padx=18)
        self.bind("<Configure>", lambda event: self.summary_label.configure(wraplength=max(300, event.width - 80)) if event.widget is self else None)
        controls = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        controls.pack(fill="x")
        tk.Label(controls, text="Find a fact or enter a public topic to learn", bg=p.BLACK, fg=p.MUTED).pack(anchor="w")
        self.query = StringVar(self)
        self.entry = tk.Entry(controls, textvariable=self.query, bg=p.PANEL, fg=p.WHITE,
                              insertbackground=p.GOLD, relief="flat", font=("Segoe UI", 12))
        from app.gui_theme import field_outline
        field_outline(self.entry, p)
        self.entry.pack(fill="x", pady=(5, 8), ipady=7)
        self.entry.bind("<Return>", lambda _event: self.refresh())
        self.entry.bind("<Control-a>", lambda _event: (self.entry.select_range(0, "end"), "break")[1])
        self.query.trace_add("write", self._schedule_search)
        actions = tk.Frame(controls, bg=p.BLACK)
        actions.pack(fill="x")
        self.fresh_only = BooleanVar(self, False)
        ttk.Button(actions, text="Refresh list", command=self.refresh).pack(side="right")
        self.learn_button = ttk.Button(actions, text="Learn this topic", command=self.learn_topic)
        self.learn_button.pack(side="right", padx=6)
        # Your own ChatGPT or Claude data export, read on this computer (app/chat_import.py).
        self.import_button = ttk.Button(actions, text="Import ChatGPT / Claude history…",
                                        command=self.import_chat_history)
        self.import_button.pack(side="left")
        filters = tk.Frame(controls, bg=p.BLACK)
        filters.pack(fill="x", pady=(8, 0))
        ttk.Label(filters, text="Evidence view").pack(side="left", padx=(0, 8))
        self.quality = StringVar(self, "Supported and disputed")
        self.quality_selector = ttk.Combobox(filters, textvariable=self.quality, values=list(QUALITY_FILTERS),
                                             state="readonly", width=28)
        self.quality_selector.pack(side="left", fill="x", expand=True)
        self.quality_selector.bind("<<ComboboxSelected>>", self._quality_changed)
        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        table = ttk.Frame(panes)
        self.tree = ttk.Treeview(table, columns=("status", "freshness"), show="tree headings", height=5, selectmode="browse")
        for column, title, width in (("#0", "Learned fact", 510), ("status", "Evidence", 150), ("freshness", "Freshness", 130)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=80, stretch=column == "#0")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())
        self.tree.tag_configure("stale", foreground=p.GOLD)
        self.tree.tag_configure("disputed", foreground=p.ALERT)
        panes.add(table, weight=1)
        self.details_tabs = ttk.Notebook(panes)
        self.detail = DarkScrolledText(self.details_tabs, height=6, bg=p.PANEL, fg=p.WHITE, relief="flat",
                                               padx=12, pady=12, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.self_check = DarkScrolledText(self.details_tabs, height=6, bg=p.PANEL, fg=p.WHITE, relief="flat",
                                           padx=12, pady=12, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.details_tabs.add(self.detail, text="Selected evidence")
        self.details_tabs.add(self.self_check, text="HAYAT self-check")
        panes.add(self.details_tabs, weight=1)
        footer = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        # Reserve the action bar before the expandable panes consume the height.
        footer.pack(side="bottom", fill="x", before=panes)
        self.status = DisplayVar(self, "")
        self.copy_button = ttk.Button(footer, text="Copy with sources", command=self.copy_selected, state="disabled")
        self.copy_button.pack(side="right", padx=(8, 0))
        status_label = tk.Label(footer, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, anchor="w", justify="left", wraplength=400)
        status_label.pack(side="left", fill="x", expand=True)
        footer.bind("<Configure>", lambda event: status_label.configure(
            wraplength=max(180, event.width - self.copy_button.winfo_reqwidth() - 50)))

    def _schedule_search(self, *_args) -> None:
        if self._debounce:
            self.after_cancel(self._debounce)
        self.generation += 1  # invalidate old responses even during debounce
        self._debounce = self.after(300, self.refresh)

    def open_brain(self) -> None:
        self.gui.show_view("operator")
        self.withdraw()

    def _quality_changed(self, _event=None) -> None:
        self.fresh_only.set(False)
        self.refresh()

    def refresh(self) -> None:
        if self.closed:
            return
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        self.generation += 1
        generation, query, fresh_only = self.generation, self.query.get().strip(), self.fresh_only.get()
        quality = "verified_current" if fresh_only else QUALITY_FILTERS.get(self.quality.get(), "all")
        self.status.set("Searching local evidence…")
        self.copy_button.configure(state="disabled")
        def work():
            try:
                rows = self.store.search_claims(query, limit=100, lexical_only=True, quality=quality)
                if fresh_only:
                    rows = [row for row in rows if not row.get("stale") and row.get("valid_until") and row.get("status") == "SUPPORTED" and row.get("evidence") and not row.get("has_unresolved_conflict")]
                control = getattr(self.gui.runtime, "metacognition", None)
                try:
                    awareness = control.status() if control is not None else {}
                except Exception as exc:
                    awareness = {"observation_errors": {"self-check": type(exc).__name__}}
                self.changes.put(("search", generation, (rows, self.store.stats(), awareness), None))
            except Exception as exc:
                self.changes.put(("search", generation, None, redact(str(exc))))
        if self._search_future:
            self._search_future.cancel()
        self._search_future = self._executor.submit(work)

    def _poll(self) -> None:
        if self.closed:
            return
        while True:
            try:
                kind, generation, value, error = self.changes.get_nowait()
            except queue.Empty:
                break
            if kind == "learn":
                self._learning_busy = False
                self.learn_button.configure(state="normal")
                self.status.set(error or value)
            elif kind == "import":
                self.import_button.configure(state="normal")
                self.status.set(error or value)
            elif generation == self.generation:
                if error:
                    self.rows = {}
                    self.tree.delete(*self.tree.get_children())
                    self._set_detail("Evidence could not be read. Refresh to try again.")
                    self._set_self_check("Self-check unavailable: evidence could not be read.")
                    self.summary.set("Evidence measurement unavailable.")
                    self.copy_button.configure(state="disabled")
                    self.status.set(f"Could not read evidence: {error}")
                else:
                    self._paint(*value)
        self._poll_id = self.after(100, self._poll)

    def _paint(self, rows: list[dict[str, Any]], stats: dict[str, Any], awareness: dict[str, Any] | None = None) -> None:
        selection = self.tree.selection()
        scroll_position = self.tree.yview()[0]
        self.tree.delete(*self.tree.get_children())
        self.rows = {str(row["id"]): row for row in rows}
        for identifier, row in self.rows.items():
            tag = "disputed" if row.get("has_unresolved_conflict") or row.get("status") == "DISPUTED" else "stale" if row.get("stale") else ""
            freshness = "Needs refresh" if row.get("stale") else "Fresh" if row.get("valid_until") else "Undated"
            self.tree.insert("", "end", iid=identifier, text=row.get("claim_text", ""),
                             values=(row.get("status", "UNKNOWN"), freshness), tags=(tag,))
        current = stats.get("verified_current_claims", "Unknown")
        self.summary.set(f"{stats.get('claims', 0):,} claims   ·   {stats.get('sources', 0):,} sources   ·   {current} verified and current   ·   {stats.get('stale', 0):,} summaries due for refresh   ·   {stats.get('unresolved_conflicts', 0):,} conflicts")
        self._set_self_check(self_check_text(stats, awareness))
        self.status.set(f"{len(rows)} results (up to 100). Select a fact to inspect its evidence." if rows else "No matching evidence. Try another phrase or queue a public topic.")
        empty_library = not rows and not stats.get("claims")
        self._set_detail("Select a fact to see its sources." if rows else
                         "Nothing learned yet. Type a public topic above and press Learn this topic: HAYAT reads public sources "
                         "in the background and keeps each fact with its evidence." if empty_library else
                         "No matching evidence is stored for this search.")
        if empty_library:
            self.status.set("The library is empty: queue a topic to start it.")
        from app.gui_empty import show_empty
        show_empty(self.tree, "Nothing learned yet." if empty_library else "No fact matches this search.", self.palette)
        self.copy_button.configure(state="disabled")
        if rows:
            retained = bool(selection and selection[0] in self.rows)
            self.tree.selection_set(selection[0] if retained else str(rows[0]["id"]))
            self.tree.yview_moveto(scroll_position if retained else 0.0)
            self.show_selected()

    def _set_self_check(self, text: str) -> None:
        self.self_check.configure(state="normal")
        self.self_check.delete("1.0", "end")
        self.self_check.insert("end", text)
        self.self_check.configure(state="disabled")

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text)
        self.detail.configure(state="disabled")

    def show_selected(self) -> None:
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            self._set_detail(evidence_text(self.rows[selected[0]]))
            self.copy_button.configure(state="normal")

    def copy_selected(self) -> None:
        selected = self.tree.selection()
        if selected and selected[0] in self.rows:
            self.clipboard_clear()
            self.clipboard_append(evidence_text(self.rows[selected[0]]))
            self.status.set("Copied the fact, freshness, and source evidence.")

    def learn_topic(self) -> None:
        topic = self.query.get().strip()
        if not topic or self._learning_busy:
            return
        self._learning_busy = True
        self.learn_button.configure(state="disabled")
        self.status.set("Queuing topic…")
        def work():
            try:
                self.changes.put(("learn", 0, queue_public_topic(self.gui.runtime, topic), None))
            except Exception as exc:
                self.changes.put(("learn", 0, None, redact(str(exc))))
        threading.Thread(target=work, daemon=True, name="hayat-knowledge-queue").start()

    def import_chat_history(self, folder: str | None = None) -> None:
        """Choose an unzipped ChatGPT or Claude export; each conversation becomes one memory."""
        if folder is None:
            from tkinter import filedialog

            folder = filedialog.askdirectory(parent=self, title="Choose the unzipped ChatGPT or Claude export folder")
        if not folder:
            return
        self.import_button.configure(state="disabled")
        self.status.set("Reading your exported conversations on this computer…")

        def work():
            try:
                from app.chat_import import import_conversations

                result = import_conversations(self.gui.runtime.memory, folder)
                origins = ", ".join(f"{count} from {origin}" for origin, count in result["by_origin"].items())
                text = (f"Imported {result['imported']} conversation{'s' if result['imported'] != 1 else ''}"
                        + (f" ({origins})" if origins else "") + f"; {result['already_imported']} were already here. "
                        "Your questions were kept, not the other assistant's answers.")
                self.changes.put(("import", 0, text, None))
            except Exception as exc:
                self.changes.put(("import", 0, None, "Could not import: " + redact(str(exc))))
        threading.Thread(target=work, daemon=True, name="hayat-chat-import").start()

    def close(self) -> None:
        self.destroy()

    def destroy(self) -> None:
        if self.closed:
            return
        self.closed = True
        for callback in (self._poll_id, self._debounce):
            if callback:
                self.after_cancel(callback)
        self._executor.shutdown(wait=False, cancel_futures=True)
        super().destroy()
