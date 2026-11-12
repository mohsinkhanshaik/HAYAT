"""The Research lab window: four laboratories in one place, each showing what its tool really returned.

Robustness (from DARPA GARD), Cyber defence (after DARPA AIxCC), Software analysis (NSA Ghidra, already part of
HAYAT's reverse-engineering window) and Model discovery (after DARPA D3M). Every result carries the tool that
produced it, its source, the time it took, its limits and the next step, and every run happens off the window's
thread. The window shares the Mind window's parts so the two look and behave the same.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from app.gui_fonts import DISPLAY
from app.gui_lab_insight import InsightTabs
from app.gui_mind import MindWindow
from app.gui_windows import place_over


def provenance(result: dict[str, Any]) -> str:
    """'Tool: … · Source: … · Took: … s' with the limits and the next step, from the result itself."""
    parts = [f"Tool: {result['tool']}" if result.get("tool") else "", f"Source: {result['source']}" if result.get("source") else "",
             f"Took: {result['seconds']} s" if result.get("seconds") is not None else ""]
    lines = [" · ".join(part for part in parts if part)]
    if result.get("limits"):
        lines.append(f"Limits: {result['limits']}")
    if result.get("next"):
        lines.append(f"Next: {result['next']}")
    return "\n".join(line for line in lines if line)


class ResearchLab(InsightTabs, tk.Toplevel):
    TABS = ("Overview", "Robustness", "Cyber defence", "Software analysis", "Model discovery",
            "Cause & effect", "Graph network")
    # The same building blocks as the Mind window.
    _tree, _note, _write = MindWindow._tree, MindWindow._note, staticmethod(MindWindow._write)
    _say, _background, _finish, _selected = MindWindow._say, MindWindow._background, MindWindow._finish, MindWindow._selected

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.runtime, self.palette = gui, gui.runtime, type(gui)
        self._closed = False
        p = self.palette
        self.title("HAYAT · Research lab")
        self.configure(bg=p.BLACK)
        self.transient(gui.root)
        self.minsize(840, 620)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Research lab", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 20)).pack(anchor="w")
        subtitle = tk.Label(header, text="Robustness, code security, software analysis and model discovery. Each result names "
                 "the tool that produced it, its limits and the next step. Nothing is applied or sent without you.",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), wraplength=760, justify="left")
        subtitle.pack(anchor="w", pady=(2, 0))
        self.bind("<Configure>", lambda event: subtitle.configure(wraplength=max(250, event.width - 36))
                  if event.widget is self else None, add="+")
        self.status = tk.Label(self, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=18, pady=(0, 8))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        self.pages: dict[str, tk.Frame] = {}
        for name in self.TABS:
            page = tk.Frame(self.tabs, bg=p.BLACK)
            # Seven full titles are wider than the smallest window, and a notebook then cuts every one of them
            # short; the two newest carry a shorter label (their pages keep the full name).
            self.tabs.add(page, text={"Cause & effect": "Causes", "Graph network": "Graph"}.get(name, name))
            self.pages[name] = page
        self._build_overview()
        self._build_robustness()
        self._build_defense()
        self._build_software()
        self._build_discovery()
        self._build_causal()
        self._build_graph()
        self.refresh()
        place_over(self, gui.root, (1080, 760))
        self.deiconify()

    def _part(self, name: str) -> Any:
        part = getattr(self.runtime, name, None)
        if part is None:
            self._say("That laboratory isn't available in this runtime.")
        return part

    def _buttons(self, parent: tk.Misc, items: tuple) -> tk.Frame:
        """Actions remain reachable at compact widths and with larger system text."""
        from app.gui_intelligence_parts import FlowRow

        row = FlowRow(parent, self.palette.BLACK, gap=6, height=32)
        row.pack(fill="x", pady=(2, 6))
        for label, command, main in items:
            row.add(ttk.Button(row, text=label, command=command,
                               style="HayatAccent.TButton" if main else "Hayat.TButton"))
        return row

    def _build_overview(self) -> None:
        from app.gui_3d_controls import camera_menu
        from app.gui_space3d import OrbitScene

        page = self.pages["Overview"]
        controls = self._buttons(page, (("Refresh status snapshot", self._refresh_overview, False),
                                        ("Run the learning experiment", self.run_learning_experiment, False)))
        self.lab_scene = OrbitScene(page, self.palette, height=300, on_select=self._overview_selected,
                                    on_open=self._overview_open)
        self.lab_scene.zoom = 1.5
        camera_menu(controls, self.lab_scene).pack(side="right")
        self.lab_scene.pack(fill="both", expand=True)
        self.overview_text = self._note(page, 4)

    def _refresh_overview(self) -> None:
        """A navigable map of real handoffs; state comes from local records, never a simulated activity feed."""
        snapshots = {}
        for name in ("model_discovery", "robustness", "code_defense", "causal_lab", "graph_lab", "fuzz_lab", "skill_lab"):
            part = getattr(self.runtime, name, None)
            status = getattr(part, "status", None)
            try:
                snapshots[name] = status() if callable(status) else {}
            except Exception:
                snapshots[name] = {"status": "Status unavailable; open the laboratory for details."}
        software = getattr(self, "_software_status", {})
        definitions = (
            ("Model discovery", -.8, .4, .25, "Fits models against a baseline; its latest in-memory model is available to Robustness.", snapshots['model_discovery']),
            ("Robustness", .05, .4, .25, "Measures injection defence and typing noise, and tests the last discovered model: a black-box attack on its inputs, and refits with wrong training labels.", snapshots['robustness']),
            ("Regression gate", .85, .6, -.1, "The test suite gates measured injection and noisy-routing benchmarks. Opening Robustness shows measurements, not a continuously running gate.", {}),
            ("Software analysis", -.8, -.6, .25, "Installed Ghidra is driven through the local REA engine.", {'version': software.get('version', 'checking'), 'installed': software.get('installed', 'checking')}),
            ("REA engine", .05, -.6, .25, "Disassembly, decompilation, functions and call graphs are in Reverse engineering.", {}),
            ("Cyber defence", -.8, 0.0, .7, "Reviews local source and packages; proposed patches are validated on an isolated copy. Fuzzing tries generated inputs on HAYAT's own text handlers in a child process.",
             {**snapshots['code_defense'], **{f"fuzzing {key}": value for key, value in snapshots['fuzz_lab'].items()}}),
            ("Patch validation", .7, -.05, -.8, "Compares findings before/after and runs the tests you name. Passing does not apply the patch.", {}),
            ("Cause & effect", .05, 1.0, -.35, "Estimates how much one column changes another after allowing for the rest, with refutation tests. It loads tables through Model discovery's loader, under the same limits.", snapshots['causal_lab']),
            ("Graph network", .85, -.75, .45, "Trains a graph convolutional network to find hidden links and scores it against simple rules. Reads the links between memories; never writes one.", snapshots['graph_lab']),
            ("Skill laboratory", -.05, -1.05, -.3, "Measures three things on problems it never saw: whether practised strategies transfer, whether a small trained network improves the search with no language model, and whether discovered concepts are needed. Press “Run the learning experiment”.", snapshots['skill_lab']),
        )
        self.overview_nodes = {}
        points = []
        labels = {'Model discovery': 'Discovery', 'Regression gate': 'Gate', 'Software analysis': 'Ghidra',
                  'REA engine': 'REA', 'Cyber defence': 'Code defence', 'Patch validation': 'Patch tests',
                  'Cause & effect': 'Causes', 'Graph network': 'Graph net', 'Skill laboratory': 'Skills'}
        for name, x, y, z, explanation, state in definitions:
            detail = '\n'.join(f"{key.replace('_', ' ')}: {value}" for key, value in state.items())
            tip = name + '\n' + explanation + ('\n\nLocal status snapshot:\n' + detail if detail else '')
            point = {'id': name, 'label': labels.get(name, name), 'x': x, 'y': y, 'z': z, 'tip': tip,
                     'colour': self.palette.GOLD if name in self.pages else '#7FB3FF', 'size': 1.2}
            points.append(point)
            self.overview_nodes[name] = point
        links = (('Model discovery', 'Robustness'), ('Robustness', 'Regression gate'),
                 ('Software analysis', 'REA engine'), ('Cyber defence', 'Patch validation'),
                 ('Model discovery', 'Cause & effect'))
        segments = [{'a': tuple(self.overview_nodes[a][key] for key in ('x', 'y', 'z')),
                     'b': tuple(self.overview_nodes[b][key] for key in ('x', 'y', 'z')),
                     'colour': '#7FB3FF', 'width': 2} for a, b in links]
        self.lab_scene.set_scene(points=points, segments=segments,
                                 note='Laboratory relationships · local status snapshot · double-click a node to open')
        self._write(self.overview_text, 'Discovery → Robustness → regression gate; Discovery → Cause & effect (same table loader)\n'
                    'Ghidra → REA engine; Cyber defence → isolated patch validation; Graph network reads memory links\n'
                    'Select a node for its recorded status and purpose. These links describe software handoffs; motion is a viewing aid.')

    def run_learning_experiment(self) -> None:
        """The three learning measurements (app/skill_lab.py), off the window's thread; about ten seconds."""
        lab = self._part("skill_lab")
        if lab is None:
            return

        def done(result: dict[str, Any]) -> None:
            transfer, model, found = result["strategy_transfer"], result["trained_model"], result["concept_discovery"]
            lines = ["1. " + transfer["question"], "   " + transfer["answer"]]
            if transfer["still_unsolved"]:
                lines.append("   Still unsolved: " + "; ".join(transfer["still_unsolved"]) + ".")
            lines += ["2. " + model["question"], "   " + model["answer"], f"   Network: {model['network']}; {model['inference']}.",
                      "3. " + found["question"], "   " + found["answer"]]
            lines += [f"   {'needed' if row['useful'] else 'not needed'}: {row['concept']}" for row in found["concepts"]]
            lines.append(f"   Programs that fitted the examples and were refuted by the checks: {found['refuted_hypotheses']}.")
            kept = result["kept_for_use"]
            lines.append("Kept for later use: " + (", ".join(kept["concepts"]) or "no concept")
                         + ("; the trained network." if kept["network"] else "; no network."))
            lines.append(provenance(result))
            self._refresh_overview()
            self._write(self.overview_text, "\n".join(lines))
            self._say(transfer["answer"])

        self._background(lab.run_experiment, done, "Practising, then meeting unfamiliar problems (about ten seconds)…")

    def _overview_selected(self, key: str | None) -> None:
        point = self.overview_nodes.get(key)
        if point:
            self._write(self.overview_text, point['tip'])

    def _overview_open(self, key: str) -> None:
        tab = {'Regression gate': 'Robustness', 'REA engine': 'Software analysis',
               'Patch validation': 'Cyber defence'}.get(key, key)
        if tab in self.pages:
            self.tabs.select(self.pages[tab])

    # ---------------------------------------------------------------- robustness

    def _build_robustness(self) -> None:
        page = self.pages["Robustness"]
        self._buttons(page, (("Run all suites", lambda: self.run_robustness("all"), True),
                             ("Typing noise", lambda: self.run_robustness("text"), False),
                             ("Injection guard", lambda: self.run_robustness("injection"), False),
                             ("Attack the last model", lambda: self.run_robustness("model"), False),
                             ("Wrong training labels", lambda: self.run_robustness("poisoning"), False)))
        self.robust_tree = self._tree(page, (("headline", "Result", 640), ("suite", "Suite", 90), ("at", "When (UTC)", 150)),
                                      height=9)
        self.robust_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_robust())
        self.robust_text = self._note(page, 12)

    def refresh_robustness(self) -> None:
        lab = getattr(self.runtime, "robustness", None)
        self.robust_tree.delete(*self.robust_tree.get_children())
        if lab is None:
            return
        for row in lab.history(40):
            self.robust_tree.insert("", "end", iid=str(row["id"]), text=row["headline"],
                                    values=(row["suite"], row["at"][:16].replace("T", " ")))
        report = lab.report()
        lines = [f"Not run yet: {', '.join(report['not_run_yet'])}." if report["not_run_yet"] else "All suites have run."]
        if report["failure_categories"]:
            lines.append("Failure categories found: " + "; ".join(report["failure_categories"]) + ".")
        lines += [f"{name}: {advice}" for name, advice in report["recommendations"].items()]
        self._write(self.robust_text, "\n".join(lines))

    def run_robustness(self, suite: str) -> None:
        lab = self._part("robustness")
        if lab is not None:
            self._background(lambda: lab.run(suite), self._robust_done, f"Running the {suite} robustness suite…")

    def _robust_done(self, outcome: dict[str, Any]) -> None:
        self.refresh_robustness()
        self._refresh_overview()
        self._say("; ".join(result["headline"] for result in outcome["results"]))

    def _show_robust(self) -> None:
        from app.research_lab import evidence_lines

        lab, chosen = getattr(self.runtime, "robustness", None), self._selected(self.robust_tree)
        result = lab.detail(int(chosen)) if lab is not None and chosen else None
        if not result:
            return
        lines = [result["headline"], f"Recommendation: {result.get('recommendation', '')}"]
        if result.get("suite") == "poisoning":
            score = (result.get("evaluation") or {}).get("score", "score")
            if result.get("clean_score") is not None:
                lines.append(f"  No wrong labels: {score} {result['clean_score']:.4g}")
            for level in result.get("levels", []):
                lines.append(f"  {level['corrupted_share']:.0%} of training labels wrong: {score} {level['score']:.4g} "
                             f"(average of {level['refits']} refits)")
            if result.get("not_measured_shares"):
                lines.append("  Not measured (time budget): " + ", ".join(f"{share:.0%}" for share in result["not_measured_shares"]))
        for level in result.get("levels", []) if result.get("suite") != "poisoning" else ():
            lines.append(f"  {level['misspelt_share']:.0%} of words misspelt: routing {level['relation_accuracy']:.0%}, "
                         f"grouping {level['grouping_accuracy']:.0%}" + ("".join(f"\n      {example}" for example in level["examples"])))
        for label in ("missed", "false_alarms"):
            if result.get(label):
                lines.append(f"  {label.replace('_', ' ')}: " + " | ".join(result[label]))
        expanded = result.get("expanded_regression") or {}
        if expanded:
            lines.append(f"Expanded known regressions: detected {expanded['detection_rate']:.0%}; "
                         f"false alarms {expanded['false_alarm_rate']:.0%}. These examples were used to improve the guard.")
        lines += evidence_lines(result)
        lines.append(provenance(result))
        self._write(self.robust_text, "\n".join(lines))

    # ---------------------------------------------------------------- cyber defence

    def _build_defense(self) -> None:
        outer = self.pages["Cyber defence"]
        self.defense_stats = tk.Label(outer, text="", bg=self.palette.BLACK, fg=self.palette.MUTED, font=("Segoe UI", 9),
                                      anchor="w", justify="left", wraplength=980)
        self.defense_stats.pack(fill="x", pady=(6, 0))
        self.defense_stats.bind('<Configure>', lambda event: self.defense_stats.configure(wraplength=max(200, event.width - 8)))
        self.defense_views = ttk.Notebook(outer)
        self.defense_views.pack(fill="both", expand=True, pady=(4, 0))
        page = tk.Frame(self.defense_views, bg=self.palette.BLACK)
        patch_page = tk.Frame(self.defense_views, bg=self.palette.BLACK)
        self.defense_views.add(page, text="Findings & fuzzing")
        self.defense_views.add(patch_page, text="Patch validation & evidence")
        self._buttons(page, (("Scan HAYAT's own code", self.scan_code, True),
                             ("Check installed packages", self.check_packages, False),
                             ("Mark as deliberate", self.accept_finding, False),
                             ("Export findings (SARIF)…", self.export_findings, False)))
        self._buttons(page, (("Fuzz text handlers", self.fuzz_handlers, False),))
        self.finding_tree = self._tree(page, (("where", "Where", 330), ("severity", "Severity", 80), ("confidence", "Tool sure", 80),
                                              ("rule", "Rule", 60), ("title", "What", 470)), height=6)
        self.finding_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_finding())
        # Shared result area stays visible when a patch finishes or saved evidence is opened from either tab.
        self.finding_text = self._note(outer, 5)
        self.finding_text.master.pack_configure(side="bottom", before=self.defense_views)
        tk.Label(patch_page, text="Test a patch on an isolated copy (paste a unified diff; name tests, one path per line "
                 "after a line that says TESTS). It is never applied here.", bg=self.palette.BLACK, fg=self.palette.WHITE,
                 font=("Segoe UI", 9), anchor="w", justify="left", wraplength=700).pack(fill="x", pady=(8, 6))
        self._buttons(patch_page, (("Test this patch", self.test_patch, False),
                                   ("Suggest a patch for the chosen finding", self.suggest_patch, False),
                                   ("Saved scan / patch evidence", self.show_defense_history, False)))
        p = self.palette
        editor = tk.Frame(patch_page, bg=p.BLACK)
        editor.pack(fill="both", expand=True, pady=(2, 6))
        self.patch_text = tk.Text(editor, height=8, bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE, relief="flat",
                                  wrap="none", font=("Consolas", 9), padx=8, pady=6, highlightthickness=1,
                                  highlightbackground=p.EDGE, highlightcolor=p.GOLD)
        vertical = ttk.Scrollbar(editor, orient="vertical", command=self.patch_text.yview)
        horizontal = ttk.Scrollbar(editor, orient="horizontal", command=self.patch_text.xview)
        self.patch_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.patch_text.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        editor.rowconfigure(0, weight=1)
        editor.columnconfigure(0, weight=1)
        self.findings: dict[str, dict[str, Any]] = {}

    def refresh_defense(self) -> None:
        defense = getattr(self.runtime, "code_defense", None)
        self.finding_tree.delete(*self.finding_tree.get_children())
        if defense is None:
            return
        status = defense.status()
        self.defense_stats.configure(text=(
            "Not scanned yet." if status.get("scanned") == "never" else
            f"Last: {status['last_scan']} · open: {status['open_high']} high, {status['open_medium']} medium, "
            f"{status['open_low']} low · {status['accepted']} marked deliberate · {status['fixed']} no longer present · "
            f"{status['patches_tested']} patches tested"))
        # Unreviewed findings first: what still needs a person leads the list.
        rows = sorted(defense.findings(400), key=lambda row: "review" in row)[:80]
        self.findings = {row["fingerprint"]: row for row in rows}
        if status.get("scanned") != "never":
            covered = defense.review_summary()
            if covered["findings"]:
                self.defense_stats.configure(text=self.defense_stats.cget("text") + (
                    f"\nOf HAYAT's own {covered['findings']} open findings: {covered['read_line_by_line']} read line by "
                    f"line, {covered['reviewed_as_a_pattern']} reviewed as a pattern, {covered['unreviewed']} still need "
                    "a person (listed first)."))
        for key, row in self.findings.items():
            self.finding_tree.insert("", "end", iid=key, text=f"{row['path']}:{row['line']}", values=(
                row["severity"].lower(), row["confidence"].lower(), row["rule"], row["title"]))

    def scan_code(self) -> None:
        defense = self._part("code_defense")
        if defense is not None:
            self._background(defense.scan, self._scanned, "Scanning HAYAT's own code (about 15 seconds)…")

    def _scanned(self, result: dict[str, Any]) -> None:
        self.refresh_defense()
        self._refresh_overview()
        self._write(self.finding_text, (
            f"{result['confirmed_in_source']} findings confirmed in the source ({result['new']} new, "
            f"{result['no_longer_present']} no longer present).\n" + provenance(result)))
        self._say("Scan finished.")

    def check_packages(self) -> None:
        defense = self._part("code_defense")
        if defense is not None:
            self._background(defense.dependencies, self._packages_checked,
                             "Checking installed packages against the public advisory database…")

    def _packages_checked(self, result: dict[str, Any]) -> None:
        if not result.get("checked"):
            self._write(self.finding_text, f"Not checked: {result.get('reason')}")
            return
        lines = [f"{result['packages_checked']} packages checked, {result['vulnerable_packages']} with a published advisory."]
        if result.get("packages_skipped"):
            lines.append(f"{result['packages_skipped']} packages could not be checked; no clean bill of health is implied for them.")
        for item in result["vulnerable"][:12]:
            for flaw in item["advisories"][:3]:
                lines.append(f"  {item['package']} {item['version']}: {flaw['id']}, fixed in {flaw['fixed_in'] or 'no fix listed'}")
        self._write(self.finding_text, "\n".join(lines) + "\n" + provenance(result))
        self._say("Package check finished.")

    def _show_finding(self) -> None:
        row = self.findings.get(self._selected(self.finding_tree))
        if row:
            review = row.get("review")
            reviewed = (f"\nRecorded review ({'this line was read' if review['depth'] == 'line' else 'reviewed as a pattern, not line by line'}): "
                        f"{review['verdict']}. {review['reason']}\nMarking it as deliberate is still your decision."
                        if review else "\nNo recorded review covers this finding: it needs a person to read it.")
            self._write(self.finding_text, f"{row['title']} ({row['rule']})\n{row['path']}:{row['line']}\n{row['evidence']}\n"
                                           f"First seen {row['first_seen'][:10]} · more: {row['more']}" + reviewed)

    def accept_finding(self) -> None:
        defense, key = self._part("code_defense"), self._selected(self.finding_tree)
        if defense is None or not key:
            self._say("Choose a finding first.")
            return
        defense.set_status(key, "accepted", "marked deliberate by the owner")
        self.refresh_defense()
        self._say("Marked as deliberate; it stays on record and leaves the open list.")

    def export_findings(self) -> None:
        """Save the recorded open findings as SARIF where the owner chooses; no scan runs and nothing is sent."""
        defense = self._part("code_defense")
        if defense is None:
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save the recorded findings as SARIF",
                                            defaultextension=".sarif", initialfile="hayat-findings.sarif",
                                            filetypes=(("SARIF 2.1.0", "*.sarif"), ("JSON", "*.json")))
        if not path:
            return

        def write() -> dict[str, Any]:
            import json
            from pathlib import Path

            log = defense.sarif()
            Path(path).write_text(json.dumps(log, indent=2), encoding="utf-8")
            return {"path": path, "findings": len(log["runs"][0]["results"])}

        def saved(done: dict[str, Any]) -> None:
            self._write(self.finding_text, (
                f"{done['findings']} open findings saved to {done['path']}.\n"
                "SARIF 2.1.0, readable by code-scanning viewers. It holds the saved findings with project-relative "
                "paths and the flagged lines already on record; findings you marked as deliberate are left out. "
                "No scan was run for this export."))
            self._say(f"Saved {done['findings']} findings as SARIF.")

        self._background(write, saved, "Saving the recorded findings…")

    def fuzz_handlers(self) -> None:
        """Generated inputs against HAYAT's own declared text handlers, in a child process (app/fuzz_lab.py)."""
        lab = self._part("fuzz_lab")
        if lab is None:
            return

        def done(outcome: dict[str, Any]) -> None:
            lines = [outcome["verdict"]]
            for result in outcome["results"]:
                ran = "ended early" if result["executions"] is None else f"{result['executions']:,} inputs"
                lines.append(f"{result['target']}: {ran}"
                             + (f", {result['lines_reached']} lines reached" if result.get("lines_reached") else ""))
                for finding in result["findings"]:
                    lines.append(f"  {finding['kind']} (finding {finding['finding_id']}): {finding['message']}")
                    lines.append(f"    smallest input kept ({finding['input_length']} characters): {finding['input_shown']}")
                    lines += [f"    at {place}" for place in finding["where"]]
            lines.append(provenance(outcome))
            self._write(self.finding_text, "\n".join(lines))
            self._refresh_overview()
            self._say(outcome["verdict"])

        self._background(lambda: lab.run("all", 5.0), done,
                         f"Fuzzing {len(lab.targets)} text handlers for 5 seconds each in a child process…")

    def suggest_patch(self) -> None:
        """Fill the patch box with a mechanical fix when one is known; it still has to be tested and decided."""
        defense, key = self._part("code_defense"), self._selected(self.finding_tree)
        if defense is None or not key:
            self._say("Choose a finding first.")
            return
        result = defense.suggest_patch(key)
        if not result.get("suggested"):
            self._say(result.get("reason", "No suggestion."))
            return
        self.patch_text.delete("1.0", "end")
        self.patch_text.insert("1.0", result["diff"])
        self.defense_views.select(1)
        self._write(self.finding_text, "A draft is in the Patch validation editor.\n" + provenance(result))
        self._say("Suggested. Test it in isolation before deciding.")

    def test_patch(self) -> None:
        defense = self._part("code_defense")
        text = self.patch_text.get("1.0", "end").strip()
        if defense is None or not text:
            self._say("Paste a unified diff first.")
            return
        diff, _mark, tests = text.partition("\nTESTS")
        names = [line.strip() for line in tests.splitlines() if line.strip()]
        self._background(lambda: defense.validate_patch(diff + "\n", names), self._patch_tested,
                         "Testing the patch on an isolated copy…")

    def _patch_tested(self, result: dict[str, Any]) -> None:
        from app.research_lab import evidence_lines

        lines = [result["verdict"]]
        lines += evidence_lines(result)
        if result.get("files_changed"):
            lines.append("Files: " + ", ".join(result["files_changed"]))
        if result.get("findings_in_changed_files"):
            counts = result["findings_in_changed_files"]
            lines.append(f"Findings in those files: {counts['before']} before, {counts['after']} after.")
        if result.get("test_output"):
            lines.append("Tests: " + result["test_output"].splitlines()[-1])
        if result.get("apply_error"):
            lines.append(result["apply_error"])
        self._write(self.finding_text, "\n".join(lines) + "\n" + provenance(result))
        self._say(result["verdict"])

    def show_defense_history(self) -> None:
        defense = self._part("code_defense")
        if defense is None:
            return

        def display(history: dict[str, Any]) -> None:
            from app.research_lab import evidence_lines

            lines = ["Saved local security evidence (no scan or patch was run):"]
            for row in history["scans"][:5]:
                lines.append(f"Scan {row['id']} · {row['kind']} · {row['at']} · {row['seconds']}s")
            for row in history["patches"][:5]:
                lines.append(f"Patch {row['id']} · {row['at']} · {row['verdict']}")
            if history["patches"]:
                last = defense.patch_detail(history["patches"][0]["id"])
                lines += ["", "Most recent patch:", *(evidence_lines(last) if last else [])]
            if len(lines) == 1:
                lines.append("No saved scans or patch validations yet.")
            self._write(self.finding_text, "\n".join(lines))
            self._say("Saved scan and patch evidence; current files were not modified.")

        self._background(defense.history, display, "Reading saved security evidence…")

    # ---------------------------------------------------------------- software analysis

    def _build_software(self) -> None:
        page = self.pages["Software analysis"]
        self.software_text = self._note(page, 14)
        self._buttons(page, (("Open Reverse engineering", self.gui._open_reverse_engineer, True),
                             ("Check the engine now", lambda: self.refresh_software(True), False)))

    def refresh_software(self, check_engine: bool = False) -> None:
        from app.research_lab import ghidra_status

        self._background(lambda: ghidra_status(self.runtime, check_engine), self._show_software,
                         "Checking the analysis engine…" if check_engine else "Reading what is installed…")

    def _show_software(self, found: dict[str, Any]) -> None:
        self._software_status = found
        self._refresh_overview()
        lines = [f"Ghidra {found.get('version', '?')} (release {found.get('released', '?')}): "
                 + ("installed" if found["installed"] else "not installed"),
                 f"Downloaded from: {found.get('source')}", f"SHA-256 recorded at install: {found.get('sha256', 'none')}",
                 f"Bundled Java: {'present' if found.get('java') else 'missing'}"]
        if found.get("advisory_warning"):
            lines.insert(0, "⚠ " + found["advisory_warning"])
        if found.get("compatibility_note"):
            lines.append("Compatibility: " + found["compatibility_note"])
        qualification = found.get("qualification") or {}
        if qualification.get("available"):
            lines.append(f"Saved x64 qualification: {qualification.get('checked_at') or 'date unknown'}; "
                         f"recorded success {qualification.get('recorded_success')}; version labels match {qualification.get('versions_match')}.")
            lines.append(str(qualification.get("scope", "")) + " " + str(qualification.get("limits", "")))
        else:
            lines.append("No saved native-analysis qualification. An installed engine has not been tested by this status read.")
        engine = found.get("engine")
        if isinstance(engine, dict):
            lines.append("Engine: " + "; ".join(f"{key.replace('_', ' ')}: {str(value)[:70]}" for key, value in list(engine.items())[:6]))
        lines.append("Disassembly, decompilation, functions, strings, call graphs and notes are in the Reverse "
                     "engineering window, which drives this Ghidra through the local REA engine.")
        self._write(self.software_text, "\n".join(lines) + "\n" + provenance(found))
        self._say("Read from the installed files and the engine's own status.")

    # ---------------------------------------------------------------- model discovery

    def _build_discovery(self) -> None:
        page = self.pages["Model discovery"]
        form = tk.Frame(page, bg=self.palette.BLACK)
        form.pack(fill="x", pady=(8, 2))
        self.discovery_fields: dict[str, ttk.Entry] = {}
        form.columnconfigure(1, weight=1)
        for row, (label, key, width) in enumerate((("Table (CSV path, or market:BTC)", "source", 44),
                                                  ("Predict column", "target", 18),
                                                  ("Time column (if any)", "time_column", 16))):
            tk.Label(form, text=label, bg=self.palette.BLACK, fg=self.palette.WHITE,
                     font=("Segoe UI", 9)).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entry = ttk.Entry(form, width=width)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self.discovery_fields[key] = entry
        ttk.Button(form, text="Browse…", command=self._browse_table, style="Hayat.TButton").grid(
            row=0, column=2, padx=(8, 0))
        self._buttons(page, (("Discover", self.discover, True), ("Describe the table", self.describe_table, False)))
        self.discovery_views = ttk.Notebook(page)
        self.discovery_views.pack(fill="both", expand=True)
        self.discovery_results = tk.Frame(self.discovery_views, bg=self.palette.BLACK)
        self.discovery_history = tk.Frame(self.discovery_views, bg=self.palette.BLACK)
        self.discovery_views.add(self.discovery_results, text="Results")
        self.discovery_views.add(self.discovery_history, text="Earlier runs · select to reopen")
        self.candidate_tree = self._tree(self.discovery_results, (("model", "Model", 300), ("validation", "Validation", 120), ("test", "Unseen rows", 120),
                                                ("folds", "Folds beating baseline", 170)), height=4)
        self.discovery_text = self._note(self.discovery_results, 5)
        # Split the available height instead of allowing the first packed tree
        # to consume all room and reduce the evidence pane to one unreadable line.
        for widget in (self.candidate_tree, self.discovery_text):
            widget.master.pack_forget()
        for row, widget in enumerate((self.candidate_tree, self.discovery_text)):
            widget.master.grid(row=row, column=0, sticky="nsew", pady=3)
            self.discovery_results.rowconfigure(row, weight=1, minsize=70)
        self.discovery_results.columnconfigure(0, weight=1)
        self.experiment_tree = self._tree(self.discovery_history, (("verdict", "Earlier runs", 700), ("source", "Table", 200), ("at", "When (UTC)", 130)),
                                          height=4)
        self.experiment_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_experiment())

    def _browse_table(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose a prediction table",
                                          filetypes=(("CSV tables", "*.csv"),))
        if path:
            entry = self.discovery_fields["source"]
            entry.delete(0, "end")
            entry.insert(0, path)

    def _show_experiment(self) -> None:
        discovery, key = getattr(self.runtime, "model_discovery", None), self._selected(self.experiment_tree)
        if discovery is None or not key:
            return
        result = discovery.detail(int(key))
        if result:
            self._display_discovery(result)
            self._say(f"Saved experiment {key} · {result.get('at', '')[:16].replace('T', ' ')} UTC")

    def _discovery_inputs(self) -> dict[str, str]:
        return {key: entry.get().strip() for key, entry in self.discovery_fields.items()}

    def refresh_discovery(self) -> None:
        discovery = getattr(self.runtime, "model_discovery", None)
        self.experiment_tree.delete(*self.experiment_tree.get_children())
        for row in (discovery.recent(30) if discovery is not None else []):
            self.experiment_tree.insert("", "end", iid=str(row["id"]), text=row["verdict"] or "",
                                        values=(row["source"], row["at"][:16].replace("T", " ")))

    def discover(self) -> None:
        discovery, inputs = self._part("model_discovery"), self._discovery_inputs()
        if discovery is None or not inputs["source"]:
            self._say("Give a CSV path or market:SYMBOL.")
            return
        self._background(lambda: discovery.run(inputs["source"], inputs["target"], inputs["time_column"]),
                         self._discovered, "Comparing models against the baseline…")

    def _discovered(self, result: dict[str, Any]) -> None:
        self._display_discovery(result)
        self.refresh_discovery()
        self._refresh_overview()
        self._say(result["verdict"])

    def _display_discovery(self, result: dict[str, Any]) -> None:
        from app.research_lab import evidence_lines

        self.discovery_views.select(self.discovery_results)
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        for index, row in enumerate(result["candidates"]):
            self.candidate_tree.insert("", "end", iid=f"c{index}", text=row["model"], values=(
                row["validation"] if row["validation"] is not None else row.get("error") or "", row["test"],
                row["folds_beating_baseline"]))
        lines = [result["verdict"], f"Measured by {result['metric']}; {result['validation']}."]
        lines += evidence_lines(result)
        lines += result["integrity"]
        if result.get("what_matters"):
            lines.append("What matters most: " + ", ".join(f"{item['feature']} ({item['importance']})" for item in result["what_matters"]))
        lines.append("Uncertainty: " + "; ".join(f"{key.replace('_', ' ')} {value}" for key, value in result["uncertainty"].items()))
        if result.get("unusual_rows"):
            lines.append(f"Unusual rows: {result['unusual_rows']['count']} (first: {result['unusual_rows']['first']}).")
        self._write(self.discovery_text, "\n".join(lines) + "\n" + provenance(result))

    def describe_table(self) -> None:
        discovery, inputs = self._part("model_discovery"), self._discovery_inputs()
        if discovery is None or not inputs["source"]:
            self._say("Give a CSV path or market:SYMBOL.")
            return
        self._background(lambda: discovery.describe(inputs["source"], inputs["target"]), self._described, "Reading the table…")

    def _described(self, facts: dict[str, Any]) -> None:
        self.discovery_views.select(self.discovery_results)
        columns = ", ".join(f"{column['name']} ({column['kind']}, {column['missing']} missing)" for column in facts["columns"][:20])
        self._write(self.discovery_text, f"{facts['rows']} rows. Columns: {columns}\n" + ("\n".join(facts["warnings"]) or
                                                                                         "No integrity problems found."))
        self._say("Table described.")

    # ---------------------------------------------------------------- whole window

    def refresh(self) -> None:
        self._refresh_overview()
        self.refresh_robustness()
        self.refresh_defense()
        self.refresh_discovery()
        self.refresh_causal()
        self.refresh_graph()
        self.refresh_software()

    def close(self) -> None:
        self._closed = True
        self.destroy()
