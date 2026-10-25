"""Review collected training records one at a time: keep, reject or skip.

Collection gathers records automatically, but none of them moves toward
training without the owner deciding. The pipeline reads and writes happen on
a worker thread; Tk only paints finished results, as in the gallery.
"""
import queue
import threading
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk


def describe(sample):
    """A record's fields as readable lines, longest values last."""
    if not isinstance(sample, dict):
        return str(sample)
    labels = {"input": "Request", "ideal_intent": "Should be understood as", "ideal_plan": "Should be done with",
              "result": "Result", "bad_response": "Reply given", "good_response": "Better reply",
              "reason": "Why", "success_criteria": "Checked by", "ideal_response": "Correct response",
              "messages": "Conversation example"}
    lines = []
    for key in sorted(sample, key=lambda item: (item not in labels, len(str(sample[item])))):
        value = sample[key]
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            value = ", ".join(value)
        elif isinstance(value, (dict, list)):
            from app.plain_output import describe_full

            value = "\n" + describe_full(value)
        lines.append(f"{labels.get(key, key.replace('_', ' ').capitalize())}: {value}")
    return "\n\n".join(lines)


class TrainingReview(tk.Frame):
    def __init__(self, parent, palette, runtime, compose):
        super().__init__(parent, bg=palette.BLACK)
        self.palette, self.runtime, self.compose = palette, runtime, compose
        self.kind = "training"
        self._results = queue.Queue()
        self._pending = False
        self.offset = 0
        self.current = None
        self.status = StringVar(self, "Collected examples waiting for your decision")
        self.heading = StringVar(self, "")
        self.query = StringVar(self, "")
        self.category = StringVar(self, "All categories")
        self.scope = StringVar(self, "All examples")
        self.order = StringVar(self, "oldest")
        self._filters = {}

        from app.gui_intelligence_parts import FlowRow
        tk.Label(self, text="Review collected examples", bg=palette.BLACK, fg=palette.WHITE,
                 anchor="w", font=("Segoe UI Semibold", 12)).pack(fill="x", pady=(8, 4))
        self.readiness = StringVar(self, "Checking fine-tuning readiness…")
        self.readiness_label = tk.Label(self, textvariable=self.readiness, bg=palette.PANEL_2, fg=palette.WHITE,
                                       justify="left", anchor="w", padx=12, pady=9, font=("Segoe UI", 9),
                                       wraplength=640)
        self.readiness_label.pack(fill="x", pady=(4, 4))
        self.bind("<Configure>", self._resize_labels)
        filters = FlowRow(self, palette.BLACK, gap=6, height=34)
        filters.pack(fill="x", pady=(6, 0))
        filters.add(ttk.Label(filters, text="Find examples"))
        search = filters.add(ttk.Entry(filters, textvariable=self.query, width=22))
        search.bind("<Return>", lambda _event: self.refresh())
        self.filter_controls = [search]
        for variable, values, width in ((self.category, ["All categories", *sorted(runtime.training.CATEGORIES)], 14),
                                        (self.scope, ["All examples", "Standard", "Private (local)"], 15),
                                        (self.order, ["oldest", "newest"], 9)):
            control = ttk.Combobox(filters, textvariable=variable, values=values, state="readonly", width=width)
            filters.add(control)
            control.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
            self.filter_controls.append(control)
        self.clear_filters = filters.add(ttk.Button(filters, text="Clear filters", command=self.reset_filters))
        self.filter_controls.append(self.clear_filters)

        self.status_label = ttk.Label(self, textvariable=self.status, wraplength=640)
        self.status_label.pack(fill="x", pady=(8, 4))
        self.heading_label = ttk.Label(self, textvariable=self.heading, wraplength=640)
        self.heading_label.pack(fill="x")
        # Reserve actions before the expanding document, even in a short pane.
        buttons = FlowRow(self, palette.BLACK, gap=6, height=38)
        buttons.pack(side="bottom", fill="x", pady=(4, 8))
        self.keep = buttons.add(ttk.Button(buttons, text="Keep for training", style="HayatAccent.TButton",
                                          command=lambda: self.decide(True)))
        self.reject = buttons.add(ttk.Button(buttons, text="Reject", command=lambda: self.decide(False)))
        self.skip = buttons.add(ttk.Button(buttons, text="Skip", command=self.next_record))
        self.reload = buttons.add(ttk.Button(buttons, text="Refresh", command=self.refresh))
        self.compare_runs = buttons.add(ttk.Button(buttons, text="Compare runs", command=self.open_comparison))
        self.review_actions = buttons
        body = tk.Frame(self, bg=palette.PANEL_2, padx=12, pady=10)
        body.pack(fill="both", expand=True, pady=6)
        self.text = tk.Text(body, wrap="word", bg=palette.PANEL_2, fg=palette.WHITE, relief="flat",
                            insertbackground=palette.WHITE, height=14, font=("Segoe UI", 10),
                            selectbackground=palette.ROYAL, selectforeground=palette.WHITE, spacing3=4)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set, state="disabled")
        scroll.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        from app.gui_motion import attach
        attach(self.text)
        self._set_buttons(False)

    def _resize_labels(self, event):
        if event.widget is not self:
            return
        for label in (self.readiness_label, self.status_label, self.heading_label):
            label.configure(wraplength=max(160, event.width - 30))

    def reset_filters(self):
        if self._pending:
            return
        self.query.set("")
        self.category.set("All categories")
        self.scope.set("All examples")
        self.order.set("oldest")
        self.refresh()

    def open_comparison(self):
        from app.gui_training_compare import CompareRunsWindow
        return CompareRunsWindow(self, self.palette)

    # ------------------------------------------------------------ workers

    def _run(self, work):
        if self._pending:
            return
        self._pending = True
        self.status.set("Loading examples…")
        self._set_buttons(False)

        def run():
            try:
                payload = work()
            except Exception as exc:
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            self._results.put(payload)
        threading.Thread(target=run, name="hayat-training-review", daemon=True).start()

    def _load(self):
        training = self.runtime.training
        page = training.pending_review(limit=1, offset=self.offset, **self._filters)
        if not page["records"] and self.offset:
            self.offset = 0
            page = training.pending_review(limit=1, offset=0, **self._filters)
        status = training.status()
        lifecycle = getattr(self.runtime, "training_lifecycle", None)
        gate = lifecycle.gate(profile="conversation", method="QLORA", private_only=True) if lifecycle else None
        from app.training.public_status import public_training_summary
        return {"page": page, "eligible": status.get("eligible", 0),
                "fine_tuning": gate,
                "public_training": public_training_summary(),
                "required": (status.get("training_gate") or {}).get("minimum_validated_samples", 0)}

    def refresh(self):
        if self._pending:
            return
        filters = {"query": self.query.get().strip(), "category": "" if self.category.get() == "All categories" else self.category.get(),
                   "privacy_scope": {"All examples": "", "Standard": "STANDARD", "Private (local)": "PRIVATE_LOCAL"}[self.scope.get()],
                   "order": self.order.get()}
        if filters != self._filters:
            self.offset = 0
            self._filters = filters
        self._run(self._load)

    def next_record(self):
        if self._pending or not self.current:
            return
        self.offset += 1
        self.refresh()

    def decide(self, approved):
        if not self.current:
            return
        identifier = self.current["id"]

        def work():
            decision = self.runtime.training.review(identifier, approved=approved)
            return {**self._load(), "decided": decision}
        self._run(work)

    # ------------------------------------------------------------ painting

    def _set_buttons(self, enabled):
        for button in (self.keep, self.reject, self.skip):
            button.configure(state="normal" if enabled else "disabled")
        for control in self.filter_controls:
            control.state(["disabled"] if self._pending else ["!disabled"])
        self.reload.state(["disabled"] if self._pending else ["!disabled"])

    def _show(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")

    def drain(self):
        try:
            payload = self._results.get_nowait()
        except queue.Empty:
            return
        self._pending = False
        if "error" in payload:
            self.status.set(payload["error"])
            self.current = None
            self.heading.set("Review unavailable")
            self._show("Could not load the next example. Use Refresh to try again.")
            self._set_buttons(False)
            return
        page = payload["page"]
        from app.training.readiness import explain_gate
        self.readiness.set(explain_gate(payload["fine_tuning"]) if payload.get("fine_tuning") else
                           "Fine-tuning readiness is unavailable. Collected examples are separate from trained weights.")
        if payload.get("public_training"):
            self.readiness.set(self.readiness.get() + "\n" + payload["public_training"])
        decided = payload.get("decided")
        done = ""
        if decided:
            done = "Kept. " if decided.get("state") in {"TRAINING_READY", "HELD_OUT"} else "Rejected. "
        position = f"Example {self.offset + 1} of {page['total']}" if page["records"] else "0 examples"
        self.status.set(f"{done}{position} waiting for review. Keep approves the displayed example; it does not start training.")
        self.current = page["records"][0] if page["records"] else None
        if self.current is None:
            self.heading.set("")
            filtered = any(self._filters.get(key) for key in ("query", "category", "privacy_scope"))
            self._show("No examples match these filters. Clear filters to see all pending examples." if filtered else
                       "Nothing is waiting. New examples are collected as you use HAYAT.")
            self._set_buttons(False)
            return
        record = self.current
        private = " · private, stays on this PC" if record["privacy_scope"] == "PRIVATE_LOCAL" else ""
        self.heading.set(f"{record['category'].capitalize()} example · from {record['source'] or 'unknown'}{private}")
        self._show(describe(record["sample"]))
        self._set_buttons(True)
