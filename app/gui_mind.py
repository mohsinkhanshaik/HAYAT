"""The Mind window: HAYAT's topics, goals and facts, development stages, scorecard, forecasts and memory, all measured.

Every number here is read from a store HAYAT keeps (topics.db, development.db, scorecard.db, forecasts.db,
retention.db, the learner's queue and budget); nothing is drawn for effect. The controls change those records the
way the owner would by talking: make a topic current, join or separate a message, merge two topics, judge a
forecast, run a retention review, run the keep-only-gains gate. Slow reads run off the window's thread.
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from app.gui_fonts import DISPLAY
from app.gui_windows import place_over
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def _fmt(value: Any) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


class MindWindow(tk.Toplevel):
    TABS = ("Topics", "Goals & facts", "Development", "Scorecard", "Forecasts", "Memory & learning", "System")
    #: How often the System tab re-reads while it is the tab in view (never while hidden).
    SYSTEM_REFRESH_MS = 4000

    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.runtime, self.palette = gui, gui.runtime, type(gui)
        self._closed = False
        p = self.palette
        self.title("HAYAT · Mind")
        self.configure(bg=p.BLACK)
        self.transient(gui.root)
        self.minsize(820, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: self.close())
        header = tk.Frame(self, bg=p.BLACK, padx=18, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Mind", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 20)).pack(anchor="w")
        intro = tk.Label(header, text="Topics, your goals and facts, development, scorecard, forecasts and memory, each "
                         "read from HAYAT's own records. Nothing here is estimated or drawn for effect.", bg=p.BLACK,
                         fg=p.MUTED, font=("Segoe UI", 9), wraplength=980, justify="left")
        intro.pack(anchor="w", pady=(2, 0))
        # A fixed wrap of 980 px clipped the sentence at the window's smallest width (820 px); follow the real width.
        header.bind("<Configure>", lambda event: intro.configure(wraplength=max(300, event.width - 40)), add="+")
        self.status = tk.Label(self, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=18, pady=(0, 8))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=18, pady=(4, 6))
        self.pages: dict[str, tk.Frame] = {}
        for name in self.TABS:
            page = tk.Frame(self.tabs, bg=p.BLACK)
            self.tabs.add(page, text=name)
            self.pages[name] = page
        self._build_topics()
        # Goals, permanent facts vs circumstances, refused answers (app/gui_goals.py, app/dialogue_state.py).
        from app.gui_goals import GoalsPage

        self.goals = GoalsPage(self, self.pages["Goals & facts"])
        self._build_development()
        self._build_scorecard()
        self._build_forecasts()
        self._build_memory()
        self._build_system()
        self._system_timer = None
        self._system_busy = False
        self.tabs.bind("<<NotebookTabChanged>>", lambda _event: self._tab_changed(), add="+")
        self.refresh()
        place_over(self, gui.root, (1080, 760))
        self.deiconify()

    # ---------------------------------------------------------------- helpers

    def _tree(self, parent: tk.Misc, columns: tuple[tuple[str, str, int], ...], height: int = 8) -> ttk.Treeview:
        frame = tk.Frame(parent, bg=self.palette.BLACK)
        frame.pack(fill="both", expand=True, pady=(4, 4))
        tree = ttk.Treeview(frame, columns=[key for key, _label, _width in columns[1:]], height=height,
                            selectmode="browse")
        tree.heading("#0", text=columns[0][1])
        tree.column("#0", width=columns[0][2], stretch=True)
        for key, label, width in columns[1:]:
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=False, anchor="w")
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Hayat.Vertical.TScrollbar")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=bar.set, xscrollcommand=horizontal.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        bar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        return tree

    def _buttons(self, parent: tk.Misc, items: tuple[tuple[str, Callable[[], Any], bool], ...]) -> tk.Frame:
        row = tk.Frame(parent, bg=self.palette.BLACK)
        row.pack(fill="x", pady=(2, 6))
        for label, command, main in items:
            ttk.Button(row, text=label, command=command,
                       style="HayatAccent.TButton" if main else "Hayat.TButton").pack(side="left", padx=(0, 6))
        return row

    def _note(self, parent: tk.Misc, height: int = 5) -> tk.Text:
        p = self.palette
        frame = tk.Frame(parent, bg=p.BLACK)
        frame.pack(fill="both", expand=False, pady=(2, 4))
        text = tk.Text(frame, height=height, bg=p.PANEL, fg=p.WHITE, insertbackground=p.WHITE, relief="flat",
                       wrap="word", font=("Segoe UI", 9), padx=10, pady=8)
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview, style="Hayat.Vertical.TScrollbar")
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        text.configure(state="disabled")
        return text

    @staticmethod
    def _write(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _say(self, message: str) -> None:
        if not self._closed:
            self.status.configure(text=message)

    def _background(self, work: Callable[[], Any], done: Callable[[Any], None], doing: str) -> None:
        """Run ``work`` off the window's thread and hand its result to ``done`` on it."""
        self._say(doing)

        def run() -> None:
            try:
                result = work()
            except Exception as error:
                log_swallowed(_log, f"Mind window: {doing}", level=logging.WARNING)
                result = error
            self.gui._post(self._finish, done, result)

        threading.Thread(target=run, name="mind-window", daemon=True).start()

    def _finish(self, done: Callable[[Any], None], result: Any) -> None:
        if self._closed:
            return
        if isinstance(result, Exception):
            self._say(f"That didn't work: {type(result).__name__}. Details are in data/logs/hayat.log.")
            return
        done(result)

    # ---------------------------------------------------------------- topics

    def _build_topics(self) -> None:
        page = self.pages["Topics"]
        self.topic_stats = tk.Label(page, text="", bg=self.palette.BLACK, fg=self.palette.MUTED, font=("Segoe UI", 9),
                                    anchor="w", justify="left", wraplength=980)
        self.topic_stats.pack(fill="x", pady=(6, 0))
        self.topic_tree = self._tree(page, (("title", "Topic", 420), ("messages", "Messages", 80),
                                            ("status", "Status", 90), ("updated", "Last talked about", 150),
                                            ("words", "Its words", 230)), height=7)
        self.topic_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_topic())
        self._buttons(page, (("Make current", self.make_current, True),
                             ("Merge into the current topic", self.merge_into_current, False)))
        self.topic_messages = self._note(page, 5)
        tk.Label(page, text="Recent messages and how each was placed", bg=self.palette.BLACK, fg=self.palette.WHITE,
                 font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x", pady=(6, 0))
        self.decision_tree = self._tree(page, (("text", "Your message", 420), ("relation", "Placed as", 110),
                                               ("topic", "Topic", 300), ("confidence", "Sure", 60)), height=6)
        self.decision_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_decision())
        self.decisions: dict[str, dict[str, Any]] = {}
        self._buttons(page, (("Separate this message", self.separate_message, False),
                             ("Same topic as before", self.join_message, False)))

    def _session_id(self) -> str:
        return str(getattr(getattr(self.runtime, "session", None), "session_id", "") or "")

    def refresh_topics(self) -> None:
        topics = getattr(self.runtime, "topics", None)
        for tree in (self.topic_tree, self.decision_tree):
            tree.delete(*tree.get_children())
        if topics is None:
            self.topic_stats.configure(text="Topic tracking isn't available in this runtime.")
            return
        stats = topics.stats()
        checked = stats.get("owner_checked_accuracy")
        self.topic_stats.configure(text=(
            f"{stats['decisions']} messages placed in {stats['topics']} topics across {stats['conversations']} "
            f"conversations · {stats['anchored_follow_ups']} follow-ups read in their topic · "
            f"{stats['topics_returned_to']} topics returned to · you corrected {stats['flagged_wrong']} placements"
            + (f" (right {checked:.0%} of the time by your checks)" if checked is not None else "")))
        for item in topics.topics(None, 40):
            mark = "● " if item["current"] else ""
            self.topic_tree.insert("", "end", iid=item["topic_id"], text=mark + item["title"], values=(
                item["messages"], item["status"] + (" · work running" if item["open_task"] else ""),
                str(item["updated_at"])[:16].replace("T", " "), ", ".join(item["words"])))
        self.decisions = {f"m{item['id']}": item for item in topics.recent_decisions(None, 30)}
        for item in self.decisions.values():
            self.decision_tree.insert("", "end", iid=f"m{item['id']}", text=item["text"], values=(
                item["relation"].replace("_", " "), item["topic"] or "", f"{float(item['confidence'] or 0):.0%}"))

    def _selected(self, tree: ttk.Treeview) -> str:
        chosen = tree.selection()
        return chosen[0] if chosen else ""

    def _show_topic(self) -> None:
        topics = getattr(self.runtime, "topics", None)
        topic_id = self._selected(self.topic_tree)
        if topics is None or not topic_id:
            return
        lines = [f"{'You' if row['role'] == 'user' else 'HAYAT'}: {row['text'][:220]}"
                 + (f"   (read as: {row['anchored']})" if row.get("anchored") else "")
                 for row in topics.messages(topic_id, 14)]
        self._write(self.topic_messages, "\n".join(lines) or "No messages kept for this topic.")

    def _show_decision(self) -> None:
        """Why this message was placed where it was: the reasons recorded when it was decided."""
        item = self.decisions.get(self._selected(self.decision_tree))
        if item:
            self._write(self.topic_messages, (
                f"“{item['text']}” was placed as {item['relation'].replace('_', ' ')} in “{item['topic'] or ''}”.\n"
                f"Why: {item['reasons'] or 'no reason recorded'}."
                + (f"\nRead as: {item['anchored']}" if item.get("anchored") else "")))

    def make_current(self) -> None:
        topics, topic_id = getattr(self.runtime, "topics", None), self._selected(self.topic_tree)
        if topics is None or not topic_id:
            self._say("Choose a topic first.")
            return
        result = topics.reactivate(self._session_id(), topic_id)
        self._say(f"“{result.get('title', '')}” is the current topic: your next message continues it."
                  if result.get("reactivated") else result.get("reason", "Couldn't do that."))
        self.refresh_topics()

    def merge_into_current(self) -> None:
        topics, topic_id = getattr(self.runtime, "topics", None), self._selected(self.topic_tree)
        current = next((item["topic_id"] for item in (topics.topics(None, 80) if topics else []) if item["current"]), "")
        if topics is None or not topic_id or not current:
            self._say("Choose a topic, and have a current topic to merge it into.")
            return
        result = topics.merge(topic_id, current)
        self._say(f"Merged into “{result.get('title', '')}”." if result.get("merged") else result.get("reason", ""))
        self.refresh_topics()

    def _message_id(self) -> int:
        chosen = self._selected(self.decision_tree)
        return int(chosen[1:]) if chosen.startswith("m") else 0

    def separate_message(self) -> None:
        topics, message_id = getattr(self.runtime, "topics", None), self._message_id()
        if topics is None or not message_id:
            self._say("Choose a message first.")
            return
        result = topics.separate(message_id)
        self._say(f"Kept apart as “{result.get('title', '')}”." if result.get("separated") else result.get("reason", ""))
        self.refresh_topics()

    def join_message(self) -> None:
        topics, message_id = getattr(self.runtime, "topics", None), self._message_id()
        if topics is None or not message_id:
            self._say("Choose a message first.")
            return
        result = topics.same_topic(message_id)
        self._say(f"Joined to “{result.get('title', '')}”." if result.get("merged") else result.get("reason", ""))
        self.refresh_topics()

    # ---------------------------------------------------------------- development

    def _build_development(self) -> None:
        page = self.pages["Development"]
        self.stage_summary = tk.Label(page, text="", bg=self.palette.BLACK, fg=self.palette.WHITE,
                                      font=("Segoe UI Semibold", 11), anchor="w")
        self.stage_summary.pack(fill="x", pady=(6, 0))
        self.stage_tree = self._tree(page, (("stage", "Stage", 240), ("measure", "Measured by", 330),
                                            ("value", "Now", 110), ("needs", "Needs", 80), ("passed", "Passed", 70)),
                                     height=12)
        self.stage_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_stage())
        self._buttons(page, (("Measure now", self.measure_development, True),))
        self.stage_detail = self._note(page, 5)
        self.stages: dict[str, dict[str, Any]] = {}

    def measure_development(self) -> None:
        development = getattr(self.runtime, "development", None)
        if development is None:
            self._say("Development stages aren't available in this runtime.")
            return
        self._background(development.assess, self._show_development, "Measuring development stages…")

    def _show_development(self, report: dict[str, Any]) -> None:
        self.stage_tree.delete(*self.stage_tree.get_children())
        self.stages = {str(stage["number"]): stage for stage in report["stages"]}
        for stage in report["stages"]:
            self.stage_tree.insert("", "end", iid=str(stage["number"]), text=f"{stage['number']}. {stage['name']}",
                                   values=(stage["measure"], _fmt(stage["value"]), _fmt(stage["threshold"]),
                                           "yes" if stage["passed"] else "no"))
        self.stage_summary.configure(text=f"Stage reached: {report['stage_reached']} of {len(report['stages'])} "
                                          f"({report['stage_name']})"
                                          + (f" · working on {report['working_on']}" if report["working_on"] else ""))
        self._say(report["rule"])

    def _show_stage(self) -> None:
        stage = self.stages.get(self._selected(self.stage_tree))
        if stage:
            self._write(self.stage_detail, f"{stage['detail']}\n\nFrom: {stage['source']}\nWhat moves it: {stage['next_step']}")

    # ---------------------------------------------------------------- scorecard

    def _build_scorecard(self) -> None:
        page = self.pages["Scorecard"]
        self.score_tree = self._tree(page, (("metric", "Measured", 330), ("now", "Now", 140), ("before", "Last time", 140)),
                                     height=12)
        self._buttons(page, (("Measure now", self.measure_scorecard, True),
                             ("Run the keep-only-gains gate", self.run_gate, False)))
        self.gate_result = self._note(page, 7)

    def measure_scorecard(self) -> None:
        scorecard = getattr(self.runtime, "scorecard", None)
        if scorecard is None:
            self._say("The scorecard isn't available in this runtime.")
            return
        self._background(scorecard.measure, self._show_scorecard, "Measuring the scorecard…")

    def _show_scorecard(self, snapshot: dict[str, Any]) -> None:
        self.score_tree.delete(*self.score_tree.get_children())
        before = (snapshot.get("previous") or {}).get("metrics") or {}
        for name, value in snapshot["metrics"].items():
            self.score_tree.insert("", "end", iid=name, text=name.replace("_", " "),
                                   values=(_fmt(value), _fmt(before.get(name)) if before else "—"))
        self._say(f"Measured {snapshot['measured_at'][:16].replace('T', ' ')} UTC from your records.")

    def run_gate(self) -> None:
        from app.cognitive_scorecard import benchmarks, compare, load_baseline

        self._background(lambda: compare(load_baseline(), benchmarks()), self._show_gate, "Running the gate benchmarks…")

    def _show_gate(self, result: dict[str, Any]) -> None:
        lines = [("PASSED: nothing regressed against the accepted baseline." if result["passed"]
                  else "FAILED: " + ", ".join(result["regressed"]) + " regressed.")]
        lines += [f"{row['verdict']:>7}  {row['metric']}: {row.get('before')} → {row.get('after')}" for row in result["rows"]]
        self._write(self.gate_result, "\n".join(lines))
        self._say("A change is kept only when this passes; a better result becomes the baseline by "
                  "scripts/cognitive_gate.py --accept.")

    # ---------------------------------------------------------------- forecasts

    def _build_forecasts(self) -> None:
        page = self.pages["Forecasts"]
        self.forecast_stats = tk.Label(page, text="", bg=self.palette.BLACK, fg=self.palette.MUTED, font=("Segoe UI", 9),
                                       anchor="w", justify="left", wraplength=980)
        self.forecast_stats.pack(fill="x", pady=(6, 0))
        self.forecast_tree = self._tree(page, (("question", "What was asked", 430), ("made", "Made", 100),
                                               ("check", "Check on", 100), ("verdict", "Your verdict", 110)), height=10)
        self.forecast_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_forecast())
        self._buttons(page, (("Right", lambda: self.judge("right"), True), ("Partly", lambda: self.judge("partly"), False),
                             ("Wrong", lambda: self.judge("wrong"), False),
                             ("Can't tell", lambda: self.judge("unclear"), False)))
        self.forecast_text = self._note(page, 6)
        self.forecasts: dict[str, dict[str, Any]] = {}

    def refresh_forecasts(self) -> None:
        ledger = getattr(self.runtime, "forecasts", None)
        self.forecast_tree.delete(*self.forecast_tree.get_children())
        if ledger is None:
            self.forecast_stats.configure(text="The forecast ledger isn't available in this runtime.")
            return
        stats = ledger.stats()
        rate = stats["hit_rate"]
        self.forecast_stats.configure(text=(
            f"{stats['kept']} forecasts kept · {stats['due_now']} due to be checked · {stats['judged']} judged by you"
            + (f" · right {rate:.0%} of the time (partly counts half)" if rate is not None else "")
            + ". " + stats["rule"]))
        self.forecasts = {str(item["id"]): item for item in ledger.recent(60)}
        for key, item in self.forecasts.items():
            self.forecast_tree.insert("", "end", iid=key, text=item["question"][:160], values=(
                item["made_at"][:10], item["check_on"], item["verdict"] or "waiting"))

    def _show_forecast(self) -> None:
        item = self.forecasts.get(self._selected(self.forecast_tree))
        if item:
            self._write(self.forecast_text, item["forecast"][:1500]
                        + ("\n\nA look-up of what actually happened has been queued." if item["lookup_queued"] else ""))

    def judge(self, verdict: str) -> None:
        ledger, key = getattr(self.runtime, "forecasts", None), self._selected(self.forecast_tree)
        if ledger is None or not key:
            self._say("Choose a forecast first.")
            return
        ledger.judge(int(key), verdict)
        self._say(f"Recorded: {verdict}.")
        self.refresh_forecasts()

    # ---------------------------------------------------------------- memory & learning

    def _build_memory(self) -> None:
        page = self.pages["Memory & learning"]
        self.retention_text = self._note(page, 5)
        self._buttons(page, (("Review 12 memories now", self.review_memories, True),
                             ("Show forgetting candidates", self.show_forgetting, False)))
        ask = tk.Frame(page, bg=self.palette.BLACK)
        ask.pack(fill="x", pady=(2, 2))
        tk.Label(ask, text="Why would a memory be recalled for:", bg=self.palette.BLACK, fg=self.palette.WHITE,
                 font=("Segoe UI", 9)).pack(side="left")
        self.recall_question = ttk.Entry(ask)
        self.recall_question.pack(side="left", fill="x", expand=True, padx=8)
        self.recall_question.bind("<Return>", lambda _event: self.explain_recall())
        ttk.Button(ask, text="Explain", style="Hayat.TButton", command=self.explain_recall).pack(side="left")
        self.memory_detail = self._note(page, 8)
        self.learner_text = self._note(page, 7)

    def refresh_memory(self) -> None:
        retention = getattr(self.runtime, "retention", None)
        if retention is None:
            self._write(self.retention_text, "Retention practice isn't available in this runtime.")
        else:
            report = retention.report()
            if not report["reviews"]:
                self._write(self.retention_text, "No retention reviews yet. They run while the machine is idle, or "
                            "now with the button below.")
            else:
                self._write(self.retention_text, (
                    f"Recalled from their own key words: {report['retention']:.0%} of the last {report['reviews']} reviews "
                    f"(mean reciprocal rank {report['mean_reciprocal_rank']}). {report['scheduled']} memories on the "
                    f"schedule, {report['due']} due, {report['held_three_reviews']} held through three reviews or more. "
                    f"Median recall time {report['median_ms']} ms. Forgetting candidates: {report['forgetting_candidates']}."))
        learner = getattr(self.runtime, "learner", None)
        if learner is None:
            self._write(self.learner_text, "The background learner isn't running in this runtime.")
            return
        try:
            budget = learner.budget.to_dict()
            allowed, reason = learner.preview_admission()
        except Exception:
            log_swallowed(_log, "reading the learner's limits for the Mind window")
            return
        self._write(self.learner_text, (
            f"Background learning: {'may run now' if allowed else 'waiting'} ({reason}).\n"
            f"Limits: {budget['max_cycles_per_hour']} cycles and {budget['max_requests_per_hour']} web requests an hour, "
            f"{budget['max_seconds_per_cycle']:g} s a cycle, {budget['max_bytes_per_day'] / 1024**3:.1f} GB a day, "
            f"CPU under {budget['max_cpu_percent']:g}%, at least {budget.get('min_free_ram_gb', 2):g} GB RAM free, "
            f"at least {budget.get('min_obtainable_vram_gb', 0):g} GB VRAM obtainable. Change them in "
            "config/settings.yaml (learning → budget)."))

    def review_memories(self) -> None:
        retention = getattr(self.runtime, "retention", None)
        if retention is None:
            self._say("Retention practice isn't available in this runtime.")
            return
        self._background(lambda: retention.review(12), self._reviewed, "Reviewing memories…")

    def _reviewed(self, result: dict[str, Any]) -> None:
        self._say(f"Reviewed {result.get('reviewed', 0)}: recalled {result.get('recalled', 0)}.")
        self.refresh_memory()

    def explain_recall(self) -> None:
        """Run the assistant's own retrieval for a question and show what each result's rank was made of."""
        question = self.recall_question.get().strip()
        memory = getattr(self.runtime, "memory", None)
        if not question or memory is None:
            self._say("Type a question first.")
            return
        from app.cognitive_telemetry import explain_recall

        self._background(lambda: explain_recall(memory, question), self._show_recall, "Running recall…")

    def _show_recall(self, rows: list[dict[str, Any]]) -> None:
        self._write(self.memory_detail, "\n\n".join(
            f"#{row['memory_id']} ({row['type']}, score {row['score']}): {row['text']}\n   because: "
            + ("; ".join(row["because"]) or "no single part stood out") for row in rows)
            or "Nothing would be recalled for that.")
        self._say(f"{len(rows)} memories would be recalled; each line shows the ranking's own score parts.")

    def show_forgetting(self) -> None:
        retention = getattr(self.runtime, "retention", None)
        rows = retention.forgetting_candidates(20) if retention is not None else []
        self._write(self.memory_detail, "\n".join(f"#{row['memory_id']} ({row['type']}, importance {row['importance']:.1f}, "
                                                  f"missed {row['misses_in_row']} reviews running): {row['text']}"
                                                  for row in rows)
                    or "None: no low-importance memory has been missed three reviews running. Nothing is ever "
                       "deleted automatically; you decide with “forget …”.")

    # ---------------------------------------------------------------- system

    def _build_system(self) -> None:
        page = self.pages["System"]
        tk.Label(page, text="Read from the model server, the hardware, the turn log, the search jobs, the learner "
                 "and the failure log. Re-read every few seconds only while this tab is in view.",
                 bg=self.palette.BLACK, fg=self.palette.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=980).pack(fill="x", pady=(6, 0))
        self.system_tree = self._tree(page, (("what", "Reading", 220), ("value", "Now", 800)), height=16)
        self._buttons(page, (("Read now", self.read_system, True),))

    def read_system(self) -> None:
        if self._system_busy:
            return
        self._system_busy = True
        from app.cognitive_telemetry import lines, snapshot

        def run() -> None:
            try:
                rows: Any = lines(snapshot(self.runtime))
            except Exception as error:
                log_swallowed(_log, "Mind window: reading the system", level=logging.WARNING)
                rows = error
            self.gui._post(self._show_system, rows)

        threading.Thread(target=run, name="mind-system", daemon=True).start()

    def _show_system(self, rows: Any) -> None:
        self._system_busy = False
        if self._closed or isinstance(rows, Exception):
            return
        self.system_tree.delete(*self.system_tree.get_children())
        for index, (label, value) in enumerate(rows):
            self.system_tree.insert("", "end", iid=f"s{index}", text=label, values=(value,))

    def _system_in_view(self) -> bool:
        try:
            # Minimised or hidden: no readings. (state(), not winfo_viewable: that is false under a hidden owner.)
            return self.tabs.tab(self.tabs.select(), "text") == "System" and self.state() == "normal"
        except tk.TclError:
            return False

    def _tab_changed(self) -> None:
        if self._closed:
            return
        if self._system_timer is not None:
            self.after_cancel(self._system_timer)
            self._system_timer = None
        if self._system_in_view():
            self._system_tick()

    def _system_tick(self) -> None:
        self._system_timer = None
        if self._closed or not self._system_in_view():
            return
        self.read_system()
        self._system_timer = self.after(self.SYSTEM_REFRESH_MS, self._system_tick)

    # ---------------------------------------------------------------- whole window

    def refresh(self) -> None:
        self.refresh_topics()
        self.goals.refresh()
        self.refresh_forecasts()
        self.refresh_memory()
        self.measure_development()

    def close(self) -> None:
        self._closed = True
        if self._system_timer is not None:
            self.after_cancel(self._system_timer)
            self._system_timer = None
        self.destroy()
