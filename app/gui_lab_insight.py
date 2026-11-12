"""Research lab tabs for cause-and-effect estimates and the graph network.

Each tab draws its chart from the measured result and nothing else: a point is an estimate, a bar is its 95%
interval or a score, and a row that was not measured is not drawn. Clicking a row explains it in words.
Mixed into app/gui_research_lab.ResearchLab, which supplies the shared window parts.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

CHART_FONT, SMALL_FONT = ("Segoe UI", 9), ("Segoe UI", 8)


class InsightTabs:
    # ---------------------------------------------------------------- shared

    def _form(self, page: tk.Misc, fields: tuple[tuple[str, str], ...], browse: str) -> dict[str, ttk.Entry]:
        form = tk.Frame(page, bg=self.palette.BLACK)
        form.pack(fill="x", pady=(8, 2))
        form.columnconfigure(1, weight=1)
        entries: dict[str, ttk.Entry] = {}
        for row, (label, key) in enumerate(fields):
            tk.Label(form, text=label, bg=self.palette.BLACK, fg=self.palette.WHITE, font=CHART_FONT).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entries[key] = ttk.Entry(form)
            entries[key].grid(row=row, column=1, sticky="ew", pady=2)

        def choose() -> None:
            path = filedialog.askopenfilename(parent=self, title=browse, filetypes=(("CSV tables", "*.csv *.tsv"),))
            if path:
                entries["source"].delete(0, "end")
                entries["source"].insert(0, path)
        ttk.Button(form, text="Browse…", command=choose, style="Hayat.TButton").grid(row=0, column=2, padx=(8, 0))
        return entries

    def _result_views(self, page: tk.Misc, history: tuple[tuple[str, str, int], ...]) -> tuple[tk.Canvas, tk.Text, ttk.Treeview, ttk.Notebook]:
        """Result (chart above its explanation) and earlier runs, sharing the page's height."""
        views = ttk.Notebook(page)
        views.pack(fill="both", expand=True)
        result = tk.Frame(views, bg=self.palette.BLACK)
        earlier = tk.Frame(views, bg=self.palette.BLACK)
        views.add(result, text="Result")
        views.add(earlier, text="Earlier runs · select to reopen")
        note = self._note(result, 6)   # the shared part packs itself; it is placed on the grid with the chart instead
        note.master.pack_forget()
        canvas = tk.Canvas(result, bg=self.palette.PANEL, highlightthickness=1, highlightbackground=self.palette.EDGE,
                           height=150, takefocus=1)
        canvas.grid(row=0, column=0, sticky="nsew", pady=(4, 2))
        note.master.grid(row=1, column=0, sticky="nsew", pady=(2, 4))
        result.columnconfigure(0, weight=1)
        result.rowconfigure(0, weight=3, minsize=110)
        result.rowconfigure(1, weight=2, minsize=70)
        tree = self._tree(earlier, history, height=4)
        return canvas, note, tree, views

    @staticmethod
    def _empty_chart(canvas: tk.Canvas, colour: str, text: str) -> None:
        canvas.delete("all")
        canvas.create_text(14, 14, anchor="nw", text=text, fill=colour, font=CHART_FONT,
                           width=max(200, canvas.winfo_width() - 28))

    # ---------------------------------------------------------------- cause and effect

    def _build_causal(self) -> None:
        page = self.pages["Cause & effect"]
        self.causal_fields = self._form(page, (("Table (CSV path)", "source"), ("What is changed (treatment column)", "treatment"),
                                               ("What it may change (outcome column)", "outcome"),
                                               ("Allow for (columns, commas; empty = all others)", "confounders")),
                                        "Choose a table")
        self._buttons(page, (("Estimate the effect", self.estimate_effect, True),))
        self.causal_canvas, self.causal_text, self.causal_tree, self.causal_views = self._result_views(
            page, (("verdict", "Earlier estimates", 420), ("effect", "Effect", 90), ("source", "Table", 200), ("at", "When (UTC)", 130)))
        self.causal_tree.bind("<<TreeviewSelect>>", lambda _event: self._reopen_causal())
        self.causal_canvas.bind("<Configure>", lambda _event: self._draw_causal())
        self.causal_canvas.bind("<Button-1>", self._pick_causal)
        self.causal_result: dict[str, Any] | None = None
        self._causal_rows: list[tuple[float, float, str]] = []
        self._empty_chart(self.causal_canvas, self.palette.MUTED,
                          "Name a table, what is changed and what it may change. The chart will show the plain "
                          "comparison beside the adjusted estimates, each with its 95% interval.")

    def refresh_causal(self) -> None:
        lab = getattr(self.runtime, "causal_lab", None)
        self.causal_tree.delete(*self.causal_tree.get_children())
        for row in (lab.recent(30) if lab is not None else []):
            self.causal_tree.insert("", "end", iid=str(row["id"]), values=(
                "" if row["effect"] is None else f"{row['effect']:+.4g}", row["source"], row["at"][:16].replace("T", " ")),
                text=f"{row['treatment']} → {row['outcome']}: {row['verdict_kind'].replace('_', ' ')}")

    def estimate_effect(self) -> None:
        lab = self._part("causal_lab")
        values = {key: entry.get().strip() for key, entry in self.causal_fields.items()}
        if lab is None:
            return
        if not values["source"] or not values["treatment"] or not values["outcome"]:
            self._say("Give the table, the treatment column and the outcome column.")
            return
        chosen = [name.strip() for name in values["confounders"].split(",") if name.strip()] or None
        self._background(lambda: lab.run(values["source"], values["treatment"], values["outcome"], chosen),
                         self._causal_done, "Estimating the effect and running its refutation tests…")

    def _causal_done(self, result: dict[str, Any]) -> None:
        self._show_causal(result)
        self.refresh_causal()
        self._refresh_overview()
        self._say(result["verdict"])

    def _reopen_causal(self) -> None:
        lab, key = getattr(self.runtime, "causal_lab", None), self._selected(self.causal_tree)
        result = lab.detail(int(key)) if lab is not None and key else None
        if result:
            self._show_causal(result)
            self._say(f"Saved estimate {key} · {result.get('at', '')[:16].replace('T', ' ')} UTC · nothing was refitted")

    def _show_causal(self, result: dict[str, Any]) -> None:
        from app.gui_research_lab import provenance
        from app.research_lab import evidence_lines

        self.causal_result = result
        self.causal_views.select(0)
        lines = [result["verdict"], f"Measured as: {result['unit']}. Method: {result['method']}; {result['rows']} rows."]
        if result.get("adjusted_for"):
            lines.append("Allowed for: " + ", ".join(result["adjusted_for"][:20]))
        if result.get("left_out_columns"):
            lines.append("Not usable: " + "; ".join(result["left_out_columns"][:8]))
        lines += result.get("notes", [])
        for name, check in (result.get("refutation") or {}).items():
            lines.append(f"{'✓' if check['passed'] else '✗'} {name.replace('_', ' ')}: {check['what']}")
        overlap = result.get("overlap")
        if overlap:
            lines.append(f"Overlap: {overlap['share_with_extreme_propensity']:.0%} of rows have no comparable counterpart; "
                         f"effective sample {overlap['effective_sample_size']:.0f} of {result['rows']}.")
        strength = result.get("e_value")
        if strength and strength.get("estimate"):
            lines.append(f"E-value {strength['estimate']} (interval bound {strength['interval_bound']}): "
                         + strength["meaning"])
        elif strength and strength.get("meaning"):
            lines.append("E-value unavailable: " + strength["meaning"])
        lines += evidence_lines(result)
        lines.append(provenance(result))
        self._write(self.causal_text, "\n".join(lines))
        self._draw_causal()

    def _causal_estimates(self) -> list[dict[str, Any]]:
        """The rows of the chart: only what was measured, each with the words that explain it."""
        result = self.causal_result or {}
        if "effect" not in result:
            return []
        rows = [{"label": "Plain comparison", "value": result["naive"]["effect"], "interval": result["naive"]["interval_95"],
                 "about": "The difference with nothing allowed for. Any common cause of treatment and outcome distorts it."},
                {"label": "Regression-adjusted", "value": result["regression_adjusted"], "interval": None,
                 "about": "A straight-line model of the outcome on the treatment and the allowed-for columns."},
                {"label": "Doubly robust", "value": result["effect"], "interval": result["interval_95"], "main": True,
                 "about": f"The reported estimate ({result['method']}) with its 95% interval."}]
        placebo = (result.get("refutation") or {}).get("placebo_treatment")
        if placebo:
            rows.append({"label": "Shuffled treatment", "value": placebo["average_effect"], "interval": None,
                         "about": f"{placebo['what']} Largest of the runs: {placebo['largest']:.4g}; share as large as "
                                  f"the estimate: {placebo['share_as_large_as_estimate']:.0%}."})
        return rows

    def _draw_causal(self) -> None:
        canvas, p = self.causal_canvas, self.palette
        rows = self._causal_estimates()
        if not rows:
            return
        canvas.delete("all")
        self._causal_rows = []
        width, height = max(320, canvas.winfo_width()), max(110, canvas.winfo_height())
        balance = (self.causal_result or {}).get("balance") or []
        beside = bool(balance) and width >= 700            # room for the balance panel without touching the figures
        left, right = 150, width - (310 if beside else 60)
        numbers = [row["value"] for row in rows] + [end for row in rows for end in (row["interval"] or [])] + [0.0]
        low, high = min(numbers), max(numbers)
        pad = (high - low) * 0.12 or 1.0
        low, high = low - pad, high + pad

        def x(value: float) -> float:
            return left + (value - low) / (high - low) * (right - left)
        step = min(26, (height - 34) / len(rows))
        zero = x(0.0)
        canvas.create_line(zero, 8, zero, 12 + step * len(rows), fill=p.MUTED, dash=(3, 3))
        canvas.create_text(zero, 14 + step * len(rows), text="no effect (0)", fill=p.MUTED, font=SMALL_FONT, anchor="n")
        for index, row in enumerate(rows):
            y = 18 + step * index
            colour = p.GOLD if row.get("main") else p.WHITE
            canvas.create_text(10, y, text=row["label"], fill=colour, font=CHART_FONT, anchor="w")
            if row["interval"]:
                canvas.create_line(x(row["interval"][0]), y, x(row["interval"][1]), y, fill=colour, width=3 if row.get("main") else 1)
            canvas.create_oval(x(row["value"]) - 4, y - 4, x(row["value"]) + 4, y + 4, fill=colour, outline=p.PANEL)
            canvas.create_text(right + 6, y, text=f"{row['value']:+.3g}", fill=colour, font=SMALL_FONT, anchor="w")
            self._causal_rows.append((y - step / 2, y + step / 2, f"{row['label']}: {row['value']:+.5g}"
                                      + (f" (95% interval {row['interval'][0]:.4g} to {row['interval'][1]:.4g})"
                                         if row["interval"] else "") + ". " + row["about"]))
        if beside:
            # How different the two groups were on each allowed-for column, before and after weighting.
            x0, scale = width - 150, 60.0
            canvas.create_text(width - 250, 8, text="Group difference: before → after weighting", fill=p.MUTED,
                               font=SMALL_FONT, anchor="nw")
            for index, row in enumerate(balance[:max(1, int((height - 30) / 16))]):
                y = 30 + 16 * index
                before, after = abs(row["difference_before"]), abs(row["difference_after_weighting"])
                canvas.create_text(x0 - 6, y, text=row["column"][:16], fill=p.WHITE, font=SMALL_FONT, anchor="e")
                canvas.create_line(x0, y - 2, x0 + min(1.5, before) * scale, y - 2, fill=p.MUTED, width=4)
                canvas.create_line(x0, y + 3, x0 + min(1.5, after) * scale, y + 3, width=4,
                                   fill=p.ALERT if after > 0.25 else p.GOLD)

    def _pick_causal(self, event: tk.Event) -> None:
        for top, bottom, words in self._causal_rows:
            if top <= event.y <= bottom and event.x < self.causal_canvas.winfo_width() - 250:
                self._say(words)
                return

    # ---------------------------------------------------------------- graph network

    def _build_graph(self) -> None:
        page = self.pages["Graph network"]
        self.graph_fields = self._form(page, (("Graph (memory, or a CSV of links)", "source"),), "Choose a table of links")
        self.graph_fields["source"].insert(0, "memory")
        controls = tk.Frame(page, bg=self.palette.BLACK)
        controls.pack(fill="x", pady=6)
        self.graph_train_button = ttk.Button(controls, text="Train and measure", command=self.train_graph,
                                             style="HayatAccent.TButton")
        self.graph_train_button.pack(side="left")
        tk.Label(controls, text="Capacity", font=CHART_FONT, bg=self.palette.BLACK,
                 fg=self.palette.WHITE).pack(side="left", padx=(16, 6))
        self.graph_profile = ttk.Combobox(controls, values=("balanced", "expanded"), state="readonly", width=12)
        self.graph_profile.set("balanced")
        self.graph_profile.pack(side="left")
        tk.Label(controls, text="Expanded: ≥10× parameters", font=SMALL_FONT, bg=self.palette.BLACK,
                 fg=self.palette.MUTED).pack(side="left", padx=8)
        self._graph_pending = False
        self.graph_canvas, self.graph_text, self.graph_tree, self.graph_views = self._result_views(
            page, (("source", "Earlier runs", 420), ("auc", "Network AUC", 100), ("beats", "Beat the simple rules", 150),
                   ("at", "When (UTC)", 130)))
        self.graph_tree.bind("<<TreeviewSelect>>", lambda _event: self._reopen_graph())
        self.graph_canvas.bind("<Configure>", lambda _event: self._draw_graph())
        self.graph_canvas.bind("<Button-1>", self._pick_graph)
        self.graph_result: dict[str, Any] | None = None
        self._graph_rows: list[tuple[float, float, str]] = []
        self._empty_chart(self.graph_canvas, self.palette.MUTED,
                          "A share of the links is hidden, a graph network is trained on the rest, and it is scored "
                          "on finding the hidden ones, beside three simple rules. \"memory\" uses the links between "
                          "HAYAT's own memories, read-only.")

    def refresh_graph(self) -> None:
        lab = getattr(self.runtime, "graph_lab", None)
        self.graph_tree.delete(*self.graph_tree.get_children())
        for row in (lab.recent(30) if lab is not None else []):
            self.graph_tree.insert("", "end", iid=str(row["id"]), text=row["source"], values=(
                "" if row["auc"] is None else f"{row['auc']:.3f}", "yes" if row["beats"] else "no",
                row["at"][:16].replace("T", " ")))

    def train_graph(self) -> None:
        if self._graph_pending:
            return
        lab, source = self._part("graph_lab"), self.graph_fields["source"].get().strip() or "memory"
        if lab is not None:
            profile = self.graph_profile.get()
            self._graph_pending = True
            self.graph_train_button.configure(state="disabled")

            def work() -> tuple[Any, Exception | None]:
                try:
                    return lab.run(source, profile=profile), None
                except Exception as error:
                    return None, error

            def finished(outcome: tuple[Any, Exception | None]) -> None:
                self._graph_pending = False
                self.graph_train_button.configure(state="normal")
                result, error = outcome
                if error is not None:
                    # Validation errors name the concrete fix; arbitrary engine errors stay out of the display.
                    self._say(f"That didn't work: {error if isinstance(error, ValueError) else type(error).__name__}.")
                    return
                self._graph_done(result)

            self._background(work, finished, "Separating evaluation pairs, training and measuring…")

    def _graph_done(self, result: dict[str, Any]) -> None:
        self._show_graph(result)
        self.refresh_graph()
        self._refresh_overview()
        self._say(result["verdict"])

    def _reopen_graph(self) -> None:
        lab, key = getattr(self.runtime, "graph_lab", None), self._selected(self.graph_tree)
        result = lab.detail(int(key)) if lab is not None and key else None
        if result:
            self._show_graph(result)
            self._say(f"Saved run {key} · {result.get('at', '')[:16].replace('T', ' ')} UTC · nothing was retrained")

    def _show_graph(self, result: dict[str, Any]) -> None:
        from app.gui_research_lab import provenance
        from app.research_lab import evidence_lines

        self.graph_result = result
        self.graph_views.select(0)
        network = result["network"]
        lines = [result["verdict"],
                 f"{result['nodes']} nodes, {result['links']} links; {result['hidden_links_tested']} hidden links tested "
                 f"against {result.get('test_nonlinks', result['hidden_links_tested'])} unlinked pairs. "
                 f"{network['model']}; {network['parameters']:,} weights, "
                 f"{network['epochs_run']} passes, node features {network['node_features']}."]
        if network.get("profile"):
            lines.append(f"Capacity: {network['profile']}, {network['capacity_ratio']:g}× balanced parameters. "
                         "A larger model is not a measured increase in intelligence or accuracy.")
        if result.get("selection"):
            selection = result["selection"]
            lines.append(f"Selected on validation: {selection['predictor']}; validation network AUC "
                         f"{selection['network']['auc']:.3f}. The chart shows the independent test scores.")
        if result.get("candidates", {}).get("truncated"):
            lines.append(f"Candidate search reached its work budget: {result['candidates']['pairs_scored']:,} pairs scored. "
                         "These suggestions are not an exhaustive graph search.")
        if result.get("suggested_links"):
            lines.append(f"Suggested links (by {result['suggested_by']}; none is created):")
            for row in result["suggested_links"][:10]:
                lines.append(f"  {row.get('a_label') or row['a']}  ↔  {row.get('b_label') or row['b']}  (score {row['score']})")
        lines += evidence_lines(result)
        lines.append(provenance(result))
        self._write(self.graph_text, "\n".join(lines))
        self._draw_graph()

    def _draw_graph(self) -> None:
        canvas, p, result = self.graph_canvas, self.palette, self.graph_result
        if not result:
            return
        canvas.delete("all")
        self._graph_rows = []
        width, height = max(320, canvas.winfo_width()), max(110, canvas.winfo_height())
        rows = [("Graph network", result["network"], True)] + [(name[0].upper() + name[1:], scores, False)
                                                                for name, scores in result["baselines"].items()]
        best = max(rows, key=lambda row: row[1]["auc"])[0]
        left, right = 170, width - 60
        step = min(28, (height - 30) / len(rows))

        def x(value: float) -> float:
            return left + value * (right - left)
        chance = x(0.5)
        canvas.create_line(chance, 6, chance, 12 + step * len(rows), fill=p.MUTED, dash=(3, 3))
        canvas.create_text(chance, 14 + step * len(rows), text="chance (0.5)", fill=p.MUTED, font=SMALL_FONT, anchor="n")
        for index, (label, scores, network) in enumerate(rows):
            y = 18 + step * index
            colour = p.GOLD if label == best else p.WHITE if network else p.MUTED
            canvas.create_text(10, y, text=label, fill=p.WHITE if network else colour, font=CHART_FONT, anchor="w")
            canvas.create_rectangle(left, y - 6, x(scores["auc"]), y + 6, fill=colour, outline="")
            canvas.create_text(x(scores["auc"]) + 6, y, text=f"{scores['auc']:.3f}", fill=colour, font=SMALL_FONT, anchor="w")
            self._graph_rows.append((y - step / 2, y + step / 2,
                                     f"{label}: AUC {scores['auc']:.3f}, average precision {scores['average_precision']:.3f} "
                                     f"on {result['hidden_links_tested']} hidden links. 0.5 is chance; 1.0 is every hidden "
                                     "link ranked above every unlinked pair."))

    def _pick_graph(self, event: tk.Event) -> None:
        for top, bottom, words in self._graph_rows:
            if top <= event.y <= bottom:
                self._say(words)
                return
