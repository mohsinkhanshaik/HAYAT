"""A local CSV instrument: measured summaries, distributions and relationships.

The worker owns file parsing and statistics; Tk only receives finished values
through a queue. Opening this window never reads a file or starts a model.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any, Callable

from app.gui_resources import StringVar
from app.gui_windows import place_over
from app.scientific_workbench import AnalysisCancelled, demo_profile, profile_csv, report_markdown


def _number(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:,.5g}" if isinstance(value, float) and math.isfinite(value) else str(value)


class ScientificWorkbench(tk.Toplevel):
    """Inspect one explicitly chosen table without sending its contents anywhere."""

    def __init__(self, gui: Any):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        p = self.palette
        self.title("HAYAT · Scientific Workbench")
        self.transient(gui.root)
        self.configure(bg=p.BLACK, padx=18, pady=14)
        self.minsize(660, 480)
        self.result: dict[str, Any] | None = None
        self.status = StringVar(self, value="Choose a CSV table or explore the built-in demo.")
        self.source = StringVar(self, value="NO DATA LOADED  /  local CSV analysis")
        self.selected = StringVar(self, value="Choose a column to inspect")
        self.selection_note = StringVar(self, value="The chart and measurements will appear here.")
        self._queue: queue.Queue = queue.Queue()
        self._closed = self._busy = False
        self._generation = 0
        self._cancel_event = threading.Event()
        self._poll_handle: str | None = None
        self._selected_column: dict[str, Any] | None = None
        self._bars: list[tuple[float, float, dict[str, Any]]] = []
        self._compact: bool | None = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.bind("<Configure>", self._fit, add="+")
        self.bind("<Escape>", self._escape)
        self.bind("<Control-o>", lambda _event: (self.choose_csv(), "break")[1])
        self.bind("<Control-e>", lambda _event: (self.choose_export(), "break")[1])
        self._poll_handle = self.after(60, self._poll)
        place_over(self, gui.root, (1140, 760))
        self.deiconify()

    def _label(self, parent, text="", *, colour=None, size=10, weight="normal", **kw):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=colour or self.palette.WHITE,
                        font=("Segoe UI", size, weight), anchor="w", justify="left", **kw)

    def _build(self):
        p = self.palette
        self.header = tk.Frame(self, bg=p.BLACK)
        self.header.pack(fill="x")
        self.eyebrow = self._label(self.header, "SCIENTIFIC WORKBENCH", colour=p.GOLD_SOFT,
                                    size=9, weight="bold")
        self.eyebrow.pack(fill="x")
        self.title_label = self._label(self.header, "Understand your data.", size=24, weight="bold")
        self.title_label.pack(fill="x", pady=(2, 2))
        self.subtitle = self._label(self.header, "Inspect quality. Explore distributions. Follow the evidence.",
                                   colour=p.MUTED)
        self.subtitle.pack(fill="x", pady=(0, 10))
        self.actions = tk.Frame(self, bg=p.BLACK)
        self.actions.pack(fill="x", pady=(4, 12))
        self.open_button = ttk.Button(self.actions, text="Open CSV…", command=self.choose_csv,
                                     style="HayatAccent.TButton")
        self.open_button.pack(side="left", padx=(0, 8))
        self.demo_button = ttk.Button(self.actions, text="Load demo", command=self.load_demo,
                                     style="Hayat.TButton")
        self.demo_button.pack(side="left", padx=(0, 8))
        self.cancel_button = ttk.Button(self.actions, text="Cancel", command=self.cancel,
                                       style="Hayat.TButton", state="disabled")
        self.cancel_button.pack(side="left")
        self.export_button = ttk.Button(self.actions, text="Export report…", command=self.choose_export,
                                       style="Hayat.TButton", state="disabled")
        self.export_button.pack(side="right")
        self.metrics = tk.Frame(self, bg=p.BLACK)
        self.metrics.pack(fill="x", pady=(0, 10))
        self.metric_values: dict[str, StringVar] = {}
        self.metric_cards, self.metric_numbers, self.metric_labels = [], [], []
        for index, (key, label, accent) in enumerate((
                ("rows", "ROWS INSPECTED", p.WHITE), ("columns", "COLUMNS", p.WHITE),
                ("numeric", "NUMERIC COLUMNS", p.ROYAL_SOFT), ("missing", "MISSING CELLS", p.GOLD_SOFT))):
            card = tk.Frame(self.metrics, bg=p.PANEL, highlightthickness=1, highlightbackground=p.LINE,
                            padx=12, pady=8)
            card.grid(row=0, column=index, sticky="ew", padx=(0, 7 if index < 3 else 0))
            self.metrics.columnconfigure(index, weight=1, uniform="metric")
            value = self.metric_values[key] = StringVar(self, value="—")
            number = tk.Label(card, textvariable=value, bg=p.PANEL, fg=accent,
                              font=("Segoe UI Semibold", 22), anchor="w")
            number.pack(fill="x")
            caption = self._label(card, label, colour=p.MUTED, size=8)
            caption.pack(fill="x")
            self.metric_cards.append(card)
            self.metric_numbers.append(number)
            self.metric_labels.append(caption)

        source_line = self.source_line = tk.Frame(self, bg=p.BLACK)
        source_line.pack(fill="x", pady=(0, 7))
        self.source_label = self._label(source_line, textvariable=self.source, colour=p.MUTED, size=9, height=1)
        self.source_label.pack(fill="x")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.pages = {}
        for title in ("Profile", "Relationships", "Preview"):
            page = self.pages[title] = tk.Frame(self.tabs, bg=p.BLACK)
            self.tabs.add(page, text=title)
        self._build_profile()
        self._build_relationships()
        self._build_preview()
        footer = tk.Frame(self, bg=p.BLACK)
        footer.pack(side="bottom", fill="x", pady=(8, 0), before=self.tabs)
        self.status_label = self._label(footer, textvariable=self.status, colour=p.MUTED, size=9,
                                         wraplength=1050, height=2)
        self.status_label.pack(fill="x")
        self.progress = ttk.Progressbar(self, mode="indeterminate", style="Hayat.Horizontal.TProgressbar")

    def _tree(self, parent, columns, *, height=6):
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=tuple(name for name, _label, _width in columns),
                            show="headings", selectmode="browse", height=height)
        for name, label, width in columns:
            tree.heading(name, text=label)
            tree.column(name, width=width, minwidth=min(70, width), stretch=True,
                        anchor="w" if name in {"name", "x", "y", "kind"} else "e")
        tree.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        xbar.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        return frame, tree

    def _text(self, parent, *, height=4):
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL)
        text = tk.Text(frame, bg=p.PANEL, fg=p.WHITE, insertbackground=p.GOLD,
                       selectbackground=p.ROYAL, selectforeground="#FFFFFF", relief="flat",
                       wrap="word", height=height, width=24, padx=12, pady=9,
                       font=("Segoe UI", 10), state="disabled", takefocus=1)
        text.pack(side="left", fill="both", expand=True)
        ttk.Scrollbar(frame, orient="vertical", command=text.yview).pack(side="right", fill="y")
        text.configure(yscrollcommand=frame.winfo_children()[-1].set)
        return frame, text

    @staticmethod
    def _set_text(widget, words):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", words)
        widget.configure(state="disabled")

    def _build_profile(self):
        p, page = self.palette, self.pages["Profile"]
        self.profile_split = tk.PanedWindow(page, orient="horizontal", bg=p.BLACK,
                                           sashwidth=8, bd=0, opaqueresize=True)
        self.profile_split.pack(fill="both", expand=True, pady=(7, 0))
        left = tk.Frame(self.profile_split, bg=p.BLACK)
        self.profile_split.add(left, minsize=280, stretch="always")
        self._label(left, "COLUMN HEALTH", colour=p.ROYAL_SOFT, size=9, weight="bold").pack(
            fill="x", pady=(0, 6))
        frame, self.columns_tree = self._tree(left, (("name", "Column", 160), ("kind", "Type", 80),
                                                    ("missing", "Missing", 80), ("mean", "Mean", 100)))
        frame.pack(fill="both", expand=True)
        self.columns_tree.bind("<<TreeviewSelect>>", self._select_column)
        right = tk.Frame(self.profile_split, bg=p.BLACK)
        self.profile_split.add(right, minsize=270, stretch="always")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=3, minsize=110)
        right.rowconfigure(3, weight=2, minsize=57)
        self.selected_label = self._label(right, textvariable=self.selected, colour=p.GOLD_SOFT,
                                          size=14, weight="bold", width=1, height=1)
        self.selected_label.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        self.selected_label.bind("<Configure>", lambda _event: self._fit_column_title())
        self.selection_label = self._label(right, textvariable=self.selection_note, colour=p.MUTED, size=9,
                                           wraplength=400)
        self.selection_label.grid(row=1, column=0, sticky="ew", pady=(0, 5))
        self.distribution = tk.Canvas(right, bg=p.PANEL, bd=0, highlightthickness=1,
                                      highlightbackground=p.LINE, height=100, takefocus=1)
        self.distribution.grid(row=2, column=0, sticky="nsew")
        self.distribution.bind("<Configure>", lambda _event: self._draw_distribution())
        self.distribution.bind("<Motion>", self._hover_bin)
        detail_frame, self.column_detail = self._text(right, height=4)
        detail_frame.grid(row=3, column=0, sticky="nsew", pady=(7, 0))
        self._set_text(self.column_detail, "Open a CSV or load the demo.\n\nSelect any column to inspect its measurements. No model or network connection is needed.")

    def _build_relationships(self):
        p, page = self.palette, self.pages["Relationships"]
        self.relationship_note = self._label(
            page, "Pearson correlation · strongest absolute coefficients first · pairwise complete rows",
            colour=p.MUTED, size=9, wraplength=1000)
        self.relationship_note.pack(fill="x", pady=(10, 8))
        frame, self.relationships_tree = self._tree(page, (("x", "Column A", 240), ("y", "Column B", 240),
                                                          ("r", "Pearson r", 110), ("n", "Paired rows", 110)))
        frame.pack(fill="both", expand=True)
        self.relationships_tree.bind("<<TreeviewSelect>>", self._select_relationship)
        detail, self.relationship_detail = self._text(page, height=4)
        detail.pack(fill="x", pady=(7, 0))
        self._set_text(self.relationship_detail, "A correlation describes linear association, not cause and effect. Select a pair to inspect the coefficient and the number of usable rows.")

    def _build_preview(self):
        p, page = self.palette, self.pages["Preview"]
        self.preview_note = self._label(page, "A bounded preview of the first rows; source files stay unchanged.",
                                        colour=p.MUTED, size=9, wraplength=1000)
        self.preview_note.pack(fill="x", pady=(10, 8))
        frame, self.preview_tree = self._tree(page, (("empty", "Load a table to preview its rows", 600),))
        frame.pack(fill="both", expand=True)
        details, self.method_detail = self._text(page, height=5)
        details.pack(fill="x", pady=(7, 0))
        self._set_text(self.method_detail, "METHOD\nSample standard deviation; linear-interpolated quartiles; equal-width histogram bins.\n\nAnalysis limits and input-quality notes appear here after loading.")

    def _fit(self, event=None):
        if event is not None and event.widget is not self:
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2:
            return
        compact = height < 620
        if compact != self._compact:
            self._compact = compact
            self.configure(pady=10 if compact else 14)
            self.title_label.configure(font=("Segoe UI", 20 if compact else 24, "bold"))
            if compact:
                self.subtitle.pack_forget()
                self.eyebrow.pack_forget()
            else:
                self.eyebrow.pack(fill="x", before=self.title_label)
                self.subtitle.pack(fill="x", pady=(0, 10))
            self.actions.pack_configure(pady=(3 if compact else 4, 5 if compact else 12))
            self.metrics.pack_configure(pady=(0, 6 if compact else 10))
            self.source_line.pack_configure(pady=(0, 4 if compact else 7))
            for index, card in enumerate(self.metric_cards):
                card.configure(pady=3 if compact else 8)
                self.metric_labels[index].configure(text=("ROWS", "COLUMNS", "NUMERIC", "MISSING")[index]
                                                    if compact else ("ROWS INSPECTED", "COLUMNS", "NUMERIC COLUMNS", "MISSING CELLS")[index])
                self.metric_numbers[index].pack_configure(side="left" if compact else "top", fill="none" if compact else "x")
                self.metric_labels[index].pack_configure(side="left" if compact else "top", fill="x", expand=compact,
                                                        padx=(8, 0) if compact else 0)
            if compact:
                self.selection_label.grid_remove()
            else:
                self.selection_label.grid()
            self.status_label.configure(height=1 if compact else 2)
            self._update_metrics()
        wrap = max(240, width - 45)
        for widget in (self.status_label, self.relationship_note, self.preview_note):
            widget.configure(wraplength=wrap)
        self.selection_label.configure(wraplength=max(180, self.distribution.winfo_width() - 8))
        heading_font = ttk.Style(self).lookup("Treeview.Heading", "font") or "TkDefaultFont"
        for tree in (self.columns_tree, self.relationships_tree):
            for key in tree.cget("columns"):
                title = tree.heading(key, "text")
                needed = int(self.tk.call("font", "measure", heading_font, title)) + 18
                tree.column(key, minwidth=needed, width=max(needed, int(tree.column(key, "width"))))

    def _update_metrics(self):
        if self.result is None:
            return
        columns = self.result["columns"]
        values = {"rows": self.result["rows"], "columns": len(columns),
                  "numeric": sum(column["kind"] == "numeric" for column in columns),
                  "missing": sum(column["missing"] for column in columns)}
        for key, value in values.items():
            if self._compact and value >= 10_000:
                display = f"{value / 1_000_000:.3g}M" if value >= 1_000_000 else f"{value / 1_000:.3g}k"
            else:
                display = f"{value:,}"
            self.metric_values[key].set(display)

    def _fit_column_title(self):
        if self._selected_column is None:
            return
        name = str(self._selected_column["name"])
        available = max(60, self.selected_label.winfo_width() - 6)
        font = self.selected_label.cget("font")
        measure = lambda text: int(self.tk.call("font", "measure", font, text))
        if measure(name) <= available:
            self.selected.set(name)
            return
        low, high = 0, min(len(name), 160)
        while low < high:
            middle = (low + high + 1) // 2
            if measure(name[:middle] + "…") <= available:
                low = middle
            else:
                high = middle - 1
        self.selected.set(name[:low].rstrip() + "…")

    def choose_csv(self):
        if self._busy:
            return
        path = filedialog.askopenfilename(parent=self, title="Inspect a local CSV table",
                                          filetypes=(("Delimited tables", "*.csv *.tsv *.txt"), ("All files", "*.*")))
        if path:
            self.analyze_path(path)

    def analyze_path(self, path: str | Path) -> bool:
        source = Path(path)
        return self._start(lambda cancelled: profile_csv(source, cancelled=cancelled),
                           "Inspecting the local table…")

    def load_demo(self) -> bool:
        return self._start(lambda _cancelled: demo_profile(), "Preparing the built-in demo…")

    def _start(self, work: Callable, message: str) -> bool:
        if self._closed or self._busy:
            return False
        self._generation += 1
        generation, outbox = self._generation, self._queue
        cancelled = self._cancel_event = threading.Event()
        self._busy = True
        self.status.set(message)
        self._set_busy(True)

        def worker():
            try:
                value = work(cancelled.is_set)
                outbox.put((generation, "cancelled" if cancelled.is_set() else "result", value))
            except AnalysisCancelled:
                outbox.put((generation, "cancelled", None))
            except Exception as error:
                outbox.put((generation, "error", f"{type(error).__name__}: {error}"))

        threading.Thread(target=worker, name="hayat-scientific-workbench", daemon=True).start()
        return True

    def _set_busy(self, busy: bool):
        for button in (self.open_button, self.demo_button):
            button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.export_button.configure(state="disabled" if busy or self.result is None else "normal")
        if busy:
            self.progress.pack(fill="x", pady=(4, 0))
            self.progress.start(18)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _poll(self):
        self._poll_handle = None
        if self._closed:
            return
        try:
            while True:
                generation, kind, value = self._queue.get_nowait()
                if generation != self._generation:
                    continue
                self._busy = False
                if kind == "result" and not self._cancel_event.is_set():
                    self._show_result(value)
                elif kind == "cancelled" or self._cancel_event.is_set():
                    self.status.set("Analysis cancelled. The previous result is still available." if self.result else "Analysis cancelled. Choose a table when ready.")
                else:
                    self.status.set("Could not inspect this table. " + value)
                self._set_busy(False)
        except queue.Empty:
            pass
        self._poll_handle = self.after(60, self._poll)

    def cancel(self):
        if self._busy:
            self._cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status.set("Cancelling analysis…")

    def _show_result(self, result):
        self.result = result
        columns, rows = result["columns"], result["rows"]
        self._update_metrics()
        if result.get("demo"):
            self.source.set(f"DEMO DATA  /  {rows:,} synthetic rows  /  no file loaded")
        else:
            self.source.set(f"LOCAL FILE  /  {result['name']}  /  {result['path']}")
        for tree in (self.columns_tree, self.relationships_tree, self.preview_tree):
            tree.delete(*tree.get_children())
        for index, column in enumerate(columns):
            percentage = 100 * column["missing"] / rows if rows else 0
            self.columns_tree.insert("", "end", iid=f"column-{index}", values=(
                column["name"], column["kind"], f"{percentage:.1f}%", _number(column.get("mean"))))
        for index, pair in enumerate(result.get("correlations", [])):
            self.relationships_tree.insert("", "end", iid=f"pair-{index}", values=(
                pair["x"], pair["y"], f"{pair['r']:+.4f}", f"{pair['n']:,}"))
        headers = tuple(f"c{index}" for index in range(len(columns)))
        self.preview_tree.configure(columns=headers)
        for index, column in enumerate(columns):
            key = headers[index]
            self.preview_tree.heading(key, text=column["name"])
            self.preview_tree.column(key, width=160, minwidth=90, stretch=False, anchor="w")
        for row in result.get("preview", []):
            self.preview_tree.insert("", "end", values=tuple(str(value) for value in row))
        warnings = result.get("warnings", [])
        notes = "\n".join("• " + str(note) for note in warnings) or "No input-quality warnings were recorded."
        delimiter = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}.get(result.get("delimiter"), str(result.get("delimiter", "—")))
        self._set_text(self.method_detail,
                       f"INPUT & SCOPE\n{rows:,} rows inspected · {len(columns)} columns · delimiter: {delimiter}\n"
                       f"{notes}\n\nMETHOD\nMean and median use finite numeric values. Standard deviation is the sample estimate. "
                       "Quartiles use linear interpolation; histogram bins have equal widths. Correlations use pairwise complete rows "
                       "and require at least three pairs. Missing and invalid numeric cells are excluded from numeric measurements. "
                       "At least 80% of nonmissing values must be finite numbers for a numeric column. "
                       "Blank, NA, N/A, null, none and NaN are missing.\n\n"
                       "LIMITS\nAt most 50,000 rows, 128 columns and one million cells; files over 32 MiB are rejected. "
                       "Correlations cover the first 16 numeric columns. A bounded prefix is not a random sample. "
                       "A correlation is an association, never a causal conclusion.")
        if result.get("source_sha256"):
            self._set_text(self.method_detail, self.method_detail.get("1.0", "end-1c") +
                           "\n\nSOURCE SHA-256\n" + result["source_sha256"])
        self.preview_note.configure(text=f"First {len(result.get('preview', []))} inspected rows · values may be shortened for display · horizontal scrolling available")
        if columns:
            numeric = next((index for index, item in enumerate(columns) if item["kind"] == "numeric"), 0)
            key = f"column-{numeric}"
            self.columns_tree.selection_set(key)
            self.columns_tree.see(key)
            self._select_column()
        else:
            self._selected_column = None
            self.selected.set("No columns found")
            self._draw_distribution()
        self._set_text(self.relationship_detail,
                       f"{len(result.get('correlations', []))} usable column pairs. Choose a row to inspect its paired observations.\n\n"
                       "Coefficients range from −1 to +1. Missing or invalid values are excluded pair by pair; different pairs can use different rows. "
                       "Ranked exploratory correlations do not establish statistical significance or causation.")
        label = "Built-in demo" if result.get("demo") else result["name"]
        suffix = f" · {len(warnings)} input note{'s' if len(warnings) != 1 else ''}" if warnings else ""
        self.status.set(f"{label} · {rows:,} rows inspected in {result.get('elapsed_seconds', 0):.2f}s{suffix}")

    def _select_column(self, _event=None):
        chosen = self.columns_tree.selection()
        if not self.result or not chosen:
            return
        index = int(chosen[0].split("-")[-1])
        column = self._selected_column = self.result["columns"][index]
        name = column["name"]
        self.selected.set(name if len(name) <= 64 else name[:61].rstrip() + "…")
        self.selection_note.set(f"{column['kind'].capitalize()} · {column['present']:,} present · {column['missing']:,} missing · {column.get('invalid', 0):,} invalid")
        words = (f"Column: {column['name']}\nType: {column['kind']}\nUnique nonmissing values: {_number(column.get('unique'))}\n"
                 f"Present: {column['present']:,} · missing: {column['missing']:,} · invalid numeric: {column.get('invalid', 0):,}")
        if column["kind"] == "numeric":
            words += (f"\n\nFinite numbers: {_number(column.get('numeric_count'))}"
                      f"\nMean: {_number(column.get('mean'))}    Median: {_number(column.get('median'))}"
                      f"\nSample standard deviation: {_number(column.get('stdev'))}"
                      f"\nMinimum: {_number(column.get('min'))}    Maximum: {_number(column.get('max'))}"
                      f"\n25th percentile: {_number(column.get('q1'))}    75th percentile: {_number(column.get('q3'))}")
        else:
            words += "\n\nText column. Numeric distributions and correlations are available for numeric columns."
        self._set_text(self.column_detail, words)
        self._draw_distribution()

    def _draw_distribution(self):
        canvas, p = self.distribution, self.palette
        canvas.delete("all")
        self._bars.clear()
        width, height = max(20, canvas.winfo_width()), max(20, canvas.winfo_height())
        self._fit_column_title()
        column = self._selected_column
        bins = column.get("histogram", []) if column else []
        if not bins:
            canvas.create_text(width / 2, height / 2, text="Select a numeric column\nto explore its distribution." if column else "Load a table to reveal its distribution.",
                               width=max(140, width - 34), fill=p.MUTED, font=("Segoe UI", 10), justify="center")
            return
        left, top, right, bottom = 43, 25, width - 16, height - 30
        if right <= left or bottom <= top + 10:
            return
        maximum = max(1, max(item["count"] for item in bins))
        for fraction in ((0, .5, 1) if bottom - top >= 45 else (0, 1)):
            y = bottom - (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill=p.LINE, dash=(2, 4))
            canvas.create_text(left - 8, y, text=_number(maximum * fraction), anchor="e",
                               fill=p.MUTED, font=("Segoe UI", 8))
        span = (right - left) / len(bins)
        for index, item in enumerate(bins):
            x1, x2 = left + index * span + 2, left + (index + 1) * span - 2
            y = bottom - (bottom - top) * item["count"] / maximum
            canvas.create_rectangle(x1, y, max(x1 + 1, x2), bottom, fill=p.GOLD if index % 2 == 0 else p.ROYAL_SOFT,
                                    outline="", tags=("bar", f"bin-{index}"))
            self._bars.append((x1, x2, item))
        canvas.create_text(left, 10, text="OBSERVATIONS / BIN", anchor="w", fill=p.MUTED, font=("Segoe UI", 8))
        canvas.create_text(left, bottom + 16, text=_number(bins[0]["low"]), anchor="w", fill=p.MUTED,
                           font=("Segoe UI", 8))
        canvas.create_text(right, bottom + 16, text=_number(bins[-1]["high"]), anchor="e", fill=p.MUTED,
                           font=("Segoe UI", 8))

    def _hover_bin(self, event):
        for left, right, item in self._bars:
            if left <= event.x <= right:
                self.status.set(f"{self.selected.get()} · bin {_number(item['low'])} to {_number(item['high'])} · {item['count']:,} observations")
                return

    def _select_relationship(self, _event=None):
        chosen = self.relationships_tree.selection()
        if not self.result or not chosen:
            return
        pair = self.result["correlations"][int(chosen[0].split("-")[-1])]
        self._set_text(self.relationship_detail,
                       f"{pair['x']} ↔ {pair['y']}\nPearson r = {pair['r']:+.5f} from {pair['n']:,} paired rows.\n\n"
                       "Positive values indicate that larger values tend to occur together; negative values indicate an inverse linear association. "
                       "The coefficient does not measure causal effect or establish significance. Inspect the source data and its context before drawing a conclusion.")

    def choose_export(self):
        if self.result is None or self._busy:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Export scientific analysis", defaultextension=".md",
                                            initialfile="scientific-analysis.md",
                                            filetypes=(("Markdown report", "*.md"), ("JSON measurements", "*.json")))
        if path:
            try:
                self.export_to(path, format="json" if Path(path).suffix.lower() == ".json" else "markdown")
            except (OSError, ValueError) as error:
                self.status.set(f"Could not export the report: {error}")

    def export_to(self, path: str | Path, format="markdown") -> Path:
        if self.result is None:
            raise ValueError("Load a table before exporting a report.")
        if self._busy:
            raise ValueError("Wait for analysis to finish before exporting.")
        if format not in {"markdown", "json"}:
            raise ValueError("Choose markdown or json export.")
        text = (json.dumps(self.result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                if format == "json" else report_markdown(self.result))
        target = Path(path)
        if not self.result.get("demo"):
            original = Path(self.result["path"])
            if target.resolve() == original.resolve() or (target.exists() and original.exists() and target.samefile(original)):
                raise ValueError("Choose another filename so the source CSV stays unchanged.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent,
                                             prefix=".hayat-science-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(text)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.status.set(f"Exported report: {target}")
        return target

    def refresh_theme(self):
        self._draw_distribution()

    def _escape(self, _event):
        if self._busy:
            self.cancel()
        else:
            self.close()
        return "break"

    def _on_destroy(self, event):
        if event.widget is self:
            self._shutdown()

    def _shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_event.set()
        self._generation += 1
        if self._poll_handle is not None:
            self.after_cancel(self._poll_handle)
            self._poll_handle = None
        try:
            self.progress.stop()
        except tk.TclError:
            pass  # destroy() can reach the parent's binding after its children were removed

    def close(self):
        self._shutdown()
        self.destroy()
