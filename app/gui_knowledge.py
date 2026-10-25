"""Searchable evidence library; database work stays off the Tk thread."""
from __future__ import annotations

import queue
import threading
from app.gui_resources import BooleanVar, StringVar

import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk
from typing import Any

from app.gui_theme import DarkScrolledText
from app.gui_windows import place_over
from app.logging import redact


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
        self.minsize(620, 440)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        self._build()
        place_over(self, gui.root, (920, 720))
        self.deiconify()
        self.refresh()
        self._poll()

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="Knowledge Library", bg=p.BLACK, fg=p.WHITE,
                 font=("Segoe UI Semibold", 18)).pack(side="left")
        ttk.Button(header, text="Brain 5D", command=self.open_brain).pack(side="right")
        self.summary = StringVar(self, "Reading stored evidence…")
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
        self.entry.pack(fill="x", pady=(5, 8), ipady=7)
        self.entry.bind("<Return>", lambda _event: self.refresh())
        self.entry.bind("<Control-a>", lambda _event: (self.entry.select_range(0, "end"), "break")[1])
        self.query.trace_add("write", self._schedule_search)
        actions = tk.Frame(controls, bg=p.BLACK)
        actions.pack(fill="x")
        self.fresh_only = BooleanVar(self, False)
        ttk.Checkbutton(actions, text="Fresh evidence only", variable=self.fresh_only, command=self.refresh).pack(side="left")
        ttk.Button(actions, text="Refresh list", command=self.refresh).pack(side="right")
        self.learn_button = ttk.Button(actions, text="Learn this topic", command=self.learn_topic)
        self.learn_button.pack(side="right", padx=6)
        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        table = ttk.Frame(panes)
        self.tree = ttk.Treeview(table, columns=("status", "freshness"), show="tree headings", height=8, selectmode="browse")
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
        self.detail = DarkScrolledText(panes, height=10, bg=p.PANEL, fg=p.WHITE, relief="flat",
                                               padx=12, pady=12, wrap="word", font=("Segoe UI", 10), state="disabled")
        panes.add(self.detail, weight=1)
        footer = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        footer.pack(fill="x")
        self.status = StringVar(self, "")
        tk.Label(footer, textvariable=self.status, bg=p.BLACK, fg=p.MUTED, anchor="w", wraplength=540).pack(side="left", fill="x", expand=True)
        self.copy_button = ttk.Button(footer, text="Copy with sources", command=self.copy_selected, state="disabled")
        self.copy_button.pack(side="right", padx=(8, 0))

    def _schedule_search(self, *_args) -> None:
        if self._debounce:
            self.after_cancel(self._debounce)
        self.generation += 1  # invalidate old responses even during debounce
        self._debounce = self.after(300, self.refresh)

    def open_brain(self) -> None:
        self.gui.show_view("operator")
        self.withdraw()

    def refresh(self) -> None:
        if self.closed:
            return
        if self._debounce:
            self.after_cancel(self._debounce)
            self._debounce = None
        self.generation += 1
        generation, query, fresh_only = self.generation, self.query.get().strip(), self.fresh_only.get()
        self.status.set("Searching local evidence…")
        self.copy_button.configure(state="disabled")
        def work():
            try:
                rows = self.store.search_claims(query, limit=100, lexical_only=True)
                if fresh_only:
                    rows = [row for row in rows if not row.get("stale") and row.get("valid_until") and row.get("status") == "SUPPORTED" and not row.get("has_unresolved_conflict")]
                self.changes.put(("search", generation, (rows, self.store.stats()), None))
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
            elif generation == self.generation:
                if error:
                    self.status.set(f"Could not read evidence: {error}")
                else:
                    self._paint(*value)
        self._poll_id = self.after(100, self._poll)

    def _paint(self, rows: list[dict[str, Any]], stats: dict[str, Any]) -> None:
        self.tree.delete(*self.tree.get_children())
        self.rows = {str(row["id"]): row for row in rows}
        for identifier, row in self.rows.items():
            tag = "disputed" if row.get("has_unresolved_conflict") or row.get("status") == "DISPUTED" else "stale" if row.get("stale") else ""
            freshness = "Needs refresh" if row.get("stale") else "Fresh" if row.get("valid_until") else "Undated"
            self.tree.insert("", "end", iid=identifier, text=row.get("claim_text", ""),
                             values=(row.get("status", "UNKNOWN"), freshness), tags=(tag,))
        self.summary.set(f"{stats.get('claims', 0):,} claims   ·   {stats.get('sources', 0):,} sources   ·   {stats.get('unverified_claims', 0):,} unverified   ·   {stats.get('stale', 0):,} summaries due for refresh   ·   {stats.get('unresolved_conflicts', 0):,} conflicts")
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
            self.tree.selection_set(str(rows[0]["id"]))
            self.show_selected()

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
