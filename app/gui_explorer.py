"""Searchable workspace pages backed by the existing stores and services."""
from __future__ import annotations

import json
import queue
import threading
import time
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk

from app.logging import redact


PAGE_SIZE = 30


def workspace_page(runtime, kind: str, query: str = "", page: int = 1):
    page = max(1, min(int(page), 1000))
    query = query.strip()[:500]
    offset = (page - 1) * PAGE_SIZE
    rows = []
    summary = ""
    metrics = {}
    if kind == "memory":
        if query:
            result = runtime.memory.retrieve_page(query, page=page, page_size=PAGE_SIZE,
                strategy="lexical", session_id=runtime.session.session_id, include_foreign_transient=True)
            records, more = result["items"], result["has_more"]
        else:
            with runtime.memory.connect() as db:
                records = [dict(row) for row in db.execute(
                    "SELECT id,timestamp,memory_type,content,source,metadata_json FROM memories "
                    "ORDER BY id DESC LIMIT ? OFFSET ?", (PAGE_SIZE + 1, offset)).fetchall()]
            more, records = len(records) > PAGE_SIZE, records[:PAGE_SIZE]
        for row in records:
            metadata = row.get("metadata") or json.loads(row.get("metadata_json") or "{}")
            provenance = metadata.get("trust_level") or ("verified" if metadata.get("verified") else "source not independently verified")
            rows.append({"title": str(row["content"]).replace("\n", " ")[:110],
                "state": row["memory_type"], "source": row.get("source", ""),
                "detail": f"Memory #{row['id']} · {row.get('timestamp', '')}\n"
                          f"Source: {row.get('source', '')}\nTrust: {provenance}\n\n{row['content']}"})
        summary = "Stored evidence and history. Historical observations do not describe the current screen."
    elif kind in {"tasks", "history"}:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with runtime.memory.connect() as db:
            if kind == "tasks":
                records = db.execute(
                    "SELECT id,goal,status,outcome,updated_at FROM tasks WHERE goal LIKE ? ESCAPE '\\' "
                    "ORDER BY updated_at DESC LIMIT ? OFFSET ?", (f"%{escaped}%", PAGE_SIZE + 1, offset)).fetchall()
                for row in records[:PAGE_SIZE]:
                    rows.append({"title": row["goal"][:110], "state": row["status"], "source": row["updated_at"],
                                 "detail": f"Task {row['id']}\nStatus: {row['status']}\nUpdated: {row['updated_at']}\n\n"
                                           f"{row['goal']}\n\n{row['outcome'] or 'No final outcome recorded.'}"})
            else:
                records = db.execute(
                    "SELECT session_id,role,content,model,timestamp FROM conversations "
                    "WHERE content LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ? OFFSET ?",
                    (f"%{escaped}%", PAGE_SIZE + 1, offset)).fetchall()
                for row in records[:PAGE_SIZE]:
                    rows.append({"title": row["content"].replace("\n", " ")[:110], "state": row["role"],
                        "source": row["timestamp"], "detail": f"Conversation: {row['session_id']}\n"
                        f"{row['role']} · {row['timestamp']}\nModel: {row['model'] or 'none'}\n\n{row['content']}"})
        more = len(records) > PAGE_SIZE
    elif kind == "models":
        records = [dict(row) for row in runtime.storage.model_inventory().get("models", [])]
        router = getattr(runtime, "router", None)
        colibri = getattr(router, "providers", {}).get("colibri")
        summary = "Installed models and answer quality are different measurements."
        if colibri is None:
            summary += " Colibri is disabled; enable its local connection in model settings after setup and benchmarking."
        else:
            try:
                ids = colibri.list_models()
                summary += f" Colibri advertises {len(ids)} model(s); inference quality has not been established."
                records.extend({"model": model, "provider": "colibri", "advertised": True,
                                "context": colibri.context_length} for model in ids)
            except Exception as exc:
                summary += f" Colibri is enabled but its inventory is unavailable ({type(exc).__name__})."
        records = [row for row in records if query.casefold() in
                   f"{row.get('provider', 'local')} {row.get('model', '')}".casefold()]
        for row in records[offset:offset + PAGE_SIZE]:
            advertised = row.get("advertised") is True
            detail = (f"{row['model']}\nProvider: {row.get('provider', 'local')}\n"
                      f"Capabilities: {', '.join(row.get('capabilities', [])) or 'not reported'}\n"
                      f"Context: {row.get('context') or 'not reported'} tokens"
                      + (" (client configured, not detected)" if advertised else "") + "\n")
            if advertised:
                detail += (f"Endpoint: {colibri.base_url}\nText connection only. Model size, loaded state, tool support "
                           "and quality have not been verified.\nSee docs/colibri-integration-plan.md.")
            else:
                detail += (f"Model size: {int(row.get('size_bytes') or 0) / 1024**3:.2f} GiB\n"
                           "Installed status does not establish answer quality.")
            rows.append({"title": row["model"], "state": "Advertised" if advertised else
                         "Loaded" if row.get("loaded") else "Installed",
                         "source": row.get("provider", "local"), "detail": detail})
        more = len(records) > offset + PAGE_SIZE
    elif kind == "background":
        learner = runtime.learner
        status = learner.status()
        summary = (f"{status.get('worker_state', 'unknown').replace('_', ' ').title()} · "
                   f"{status.get('waiting_because') or status.get('reason') or 'Within existing limits'}\n"
                   f"This hour: {status['spent']['cycles_this_hour']}/{status['budget']['max_cycles_per_hour']} cycles · "
                   f"{status['spent']['requests_this_hour']}/{status['budget']['max_requests_per_hour']} requests")
        counts = status['queue'].get('counts', {})
        metrics = {key: counts.get(key, 0) for key in ('learned', 'pending', 'learning', 'failed')}
        summary += (f"\nVerified topics: {counts.get('learned', 0)} · Waiting: {counts.get('pending', 0)} · "
                    f"Running: {counts.get('learning', 0)} · Needs attention: {counts.get('failed', 0)}")
        records = [row for row in status["queue"]["topics"] if query.casefold() in row["topic"].casefold()]
        for row in records:
            rows.append({"title": row["topic"], "state": row["status"], "source": row["source"],
                "detail": f"{row['topic']}\n\nStatus: {row['status']}\nSource: {row['source']}\n"
                          f"Attempts: {row['attempts']}\nUpdated: {row['updated_at']}\n\n{row.get('last_error') or ''}\n\n"
                          "Evidence recovery tries primary sources first. Conversation refusals remain excluded. "
                          "A topic is learned only after supported, cited evidence is retained."})
        more = False
    else:
        raise ValueError("Unknown workspace page")
    return redact({"rows": rows, "has_more": more, "summary": summary,
                   "page": page, "kind": kind, "metrics": metrics})


class WorkspaceExplorer(tk.Frame):
    def __init__(self, parent, palette, runtime, kind, actions=()):
        super().__init__(parent, bg=palette.BLACK)
        style = ttk.Style(self)
        style.configure("Workspace.TFrame", background=palette.BLACK)
        style.configure("Workspace.TLabel", background=palette.BLACK, foreground=palette.WHITE,
                        font=("Segoe UI", 10))
        style.configure("Workspace.Treeview", background=palette.PANEL_2, fieldbackground=palette.PANEL_2,
                        foreground=palette.WHITE, rowheight=32, font=("Segoe UI", 10), borderwidth=0)
        style.configure("Workspace.Treeview.Heading", background=palette.PANEL, foreground=palette.WHITE,
                        font=("Segoe UI Semibold", 10), padding=8)
        style.map("Workspace.Treeview", background=[("selected", palette.GOLD)],
                  foreground=[("selected", palette.BLACK)])
        self.runtime, self.kind = runtime, kind
        self.page = 1
        self._pending = False
        self._refresh_again = False
        self._generation = 0
        self._results = queue.Queue(maxsize=1)
        self._last_refresh = 0.0
        self.query = StringVar(self)
        self.status = StringVar(self, value="Loading…")
        self.metric_vars = {}
        if kind == 'background':
            tk.Label(self, text='Background intelligence', bg=palette.BLACK, fg=palette.WHITE,
                     font=('Segoe UI Semibold', 22), anchor='w').pack(fill='x', pady=(12, 6))
            cards = tk.Frame(self, bg=palette.BLACK)
            cards.pack(fill='x', pady=(0, 8))
            for index, (key, label) in enumerate((('learned', 'VERIFIED TOPICS'), ('pending', 'WAITING'),
                                                ('learning', 'RUNNING'), ('failed', 'NEEDS ATTENTION'))):
                card = tk.Frame(cards, bg=palette.PANEL_2, padx=12, pady=9,
                                highlightthickness=1, highlightbackground=palette.LINE)
                card.grid(row=0, column=index, sticky='nsew', padx=(0, 5))
                cards.columnconfigure(index, weight=1, uniform='learning')
                tk.Label(card, text=label, bg=palette.PANEL_2, fg=palette.MUTED,
                         font=('Segoe UI Semibold', 8)).pack(anchor='w')
                variable = self.metric_vars[key] = StringVar(self, '—')
                tk.Label(card, textvariable=variable, bg=palette.PANEL_2,
                         fg=palette.ALERT if key == 'failed' else palette.GOLD,
                         font=('Segoe UI Semibold', 23)).pack(anchor='w')
        search = tk.Frame(self, bg=palette.BLACK)
        search.pack(fill="x", pady=(8, 5))
        ttk.Label(search, text="Search " + kind, style="Workspace.TLabel").pack(side="left", padx=(0, 8))
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda event: self.search())
        ttk.Button(search, text="Search", style="Hayat.TButton", command=self.search).pack(side="left", padx=5)
        ttk.Button(search, text="Refresh", style="Hayat.TButton", command=self.refresh).pack(side="left")
        if actions:
            from app.gui_intelligence_parts import FlowRow
            action_row = FlowRow(self, palette.BLACK, gap=6, height=36)
            action_row.pack(fill='x')
            for label, callback in actions:
                action_row.add(ttk.Button(action_row, text=label, style="Hayat.TButton", command=callback))
        tk.Label(self, textvariable=self.status, bg=palette.BLACK, fg=palette.MUTED,
                 justify="left", anchor="w", wraplength=680).pack(fill="x", pady=5)
        split = ttk.Panedwindow(self, orient="vertical")
        split.pack(fill="both", expand=True)
        table = ttk.Frame(split, style="Workspace.TFrame")
        self.tree = ttk.Treeview(table, columns=("title", "state", "source"), show="headings", height=8,
                                 style="Workspace.Treeview")
        for name, width in (("title", 330), ("state", 100), ("source", 130)):
            self.tree.heading(name, text=name.title())
            self.tree.column(name, width=width, minwidth=70)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        split.add(table, weight=1)
        self.detail = tk.Text(split, wrap="word", state="disabled", bg=palette.PANEL_2,
                              fg=palette.WHITE, relief="flat", padx=12, pady=10,
                              font=("Segoe UI", 11), height=8)
        split.add(self.detail, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        navigation = ttk.Frame(self)
        navigation.pack(fill="x", pady=6)
        self.previous = ttk.Button(navigation, text="Previous", style="Hayat.TButton", command=lambda: self.change_page(-1))
        self.previous.pack(side="left")
        self.next = ttk.Button(navigation, text="Next", style="Hayat.TButton", command=lambda: self.change_page(1))
        self.next.pack(side="right")
        self._rows = []

    def search(self):
        self.page = 1
        self.refresh()

    def change_page(self, direction):
        if self._pending:
            return
        self.page = max(1, self.page + direction)
        self.refresh()

    def refresh(self):
        if self._pending:
            self._refresh_again = True
            return
        self._pending = True
        self._generation += 1
        generation, query, page = self._generation, self.query.get(), self.page
        self.status.set("Loading local records…")
        self._last_refresh = time.monotonic()
        def load():
            try:
                result = workspace_page(self.runtime, self.kind, query, page)
            except Exception as exc:
                result = {"error": f"Local records unavailable ({type(exc).__name__})"}
            self._results.put((generation, result))
        threading.Thread(target=load, name=f"hayat-workspace-{self.kind}", daemon=True).start()

    def drain(self):
        try:
            generation, result = self._results.get_nowait()
        except queue.Empty:
            if self.kind == "background" and self.winfo_ismapped() and time.monotonic() - self._last_refresh > 10:
                self.refresh()
            return
        self._pending = False
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()
            return
        if generation != self._generation:
            return
        if "error" in result:
            self.status.set(result["error"])
            return
        self._rows = result["rows"]
        for key, variable in self.metric_vars.items():
            variable.set(str(result.get('metrics', {}).get(key, 0)))
        self.tree.delete(*self.tree.get_children())
        for index, row in enumerate(self._rows):
            self.tree.insert("", "end", iid=str(index), values=(row["title"], row["state"], row["source"]))
        self.status.set(f"Page {result['page']} · {len(self._rows)} records\n{result['summary']}")
        self.previous.configure(state="normal" if self.page > 1 else "disabled")
        self.next.configure(state="normal" if result["has_more"] else "disabled")
        if self._rows:
            self.tree.selection_set("0")
        else:
            self._show_detail("No matching records.")

    def select(self, event=None):
        selected = self.tree.selection()
        if selected:
            self._show_detail(self._rows[int(selected[0])]["detail"])

    def _show_detail(self, text):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
