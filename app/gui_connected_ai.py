"""A usable, explicitly connected AI desk with evidence and visible execution state."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from app.connected_ai import ConnectedAI, ConnectedAIError, PROVIDERS
from app.connected_ai_tools import ResearchTools
from app.connected_research import ResearchConnections
from app.gui_resources import BooleanVar, StringVar
from app.gui_windows import place_over


class ConnectedAIStudio(tk.Toplevel):
    def __init__(self, gui, *, manager=None):
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        runtime = getattr(gui, "runtime", None)
        self.manager = manager or ConnectedAI(
            runtime.memory.path.parent,
            private=lambda: bool({"PRIVATE", "LOCAL_ONLY"}.intersection(
                getattr(getattr(runtime, "session", None), "effective_modes", ()),
            )) or bool({"PRIVATE", "LOCAL_ONLY"}.intersection(
                getattr(getattr(runtime, "modes", None), "effective", ()))),
            halted=lambda: bool(getattr(getattr(getattr(runtime, "registry", None), "interlock", None), "halted", False)))
        self.research = ResearchTools()
        self.history, self.records = [], []
        self._attachment_paths: set[Path] = set()
        self._queue = queue.Queue()
        self._cancel = threading.Event()
        self._closed = self._busy = False
        self._poll_handle = None
        self._controls = []
        self.title("HAYAT · Connected AI")
        self.configure(bg=self.palette.BLACK, padx=16, pady=12)
        self.minsize(660, 500)
        self.transient(gui.root)
        self.settings = self.manager.settings()
        self.provider = StringVar(self, value=self.settings["provider"])
        self.model = StringVar(self, value=self.settings["models"].get(self.provider.get(), ""))
        self.key = StringVar(self)
        self.enabled = BooleanVar(self, value=self.settings["enabled"])
        self.tools_enabled = BooleanVar(self, value=self.settings["tools"])
        self.web_enabled = BooleanVar(self, value=False)
        self.recall_enabled = BooleanVar(self, value=False)
        self.budget = StringVar(self, value=str(self.settings["max_tokens"]))
        self.status = StringVar(self, value="Choose a provider, save your key and select a model in Connections.")
        self.identity = StringVar(self)
        self.key_status = StringVar(self)
        self._build()
        self._identity()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._poll_handle = self.after(60, self._poll)
        place_over(self, gui.root, (1100, 790))
        self.deiconify()

    def _label(self, parent, text="", **kwargs):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=self.palette.WHITE,
                        font=("Segoe UI", 10), anchor="w", justify="left", **kwargs)

    def _button(self, parent, text, command, *, accent=False):
        button = ttk.Button(parent, text=text, command=command,
                            style="HayatAccent.TButton" if accent else "Hayat.TButton")
        self._controls.append((button, "normal"))
        return button

    def _text(self, parent, *, height=8, readonly=False):
        frame = tk.Frame(parent, bg=self.palette.PANEL)
        widget = tk.Text(frame, height=height, wrap="word", bg=self.palette.PANEL,
                         fg=self.palette.WHITE, insertbackground=self.palette.WHITE,
                         selectbackground=self.palette.ROYAL, relief="flat", padx=12, pady=10,
                         font=("Segoe UI", 11), undo=not readonly)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        widget.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        widget.pack(fill="both", expand=True)
        if readonly:
            widget.configure(state="disabled")
        return frame, widget

    def _build(self):
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK)
        header.pack(fill="x", pady=(0, 10))
        label = self._label(header, "CONNECTED AI  /  RESEARCH DESK")
        label.configure(fg=p.GOLD_SOFT, font=("Segoe UI", 10, "bold"))
        label.pack(anchor="w")
        self.identity_label = self._label(header, textvariable=self.identity, wraplength=620)
        self.identity_label.pack(fill="x", pady=(4, 0))
        self.status_label = self._label(self, textvariable=self.status, wraplength=620)
        self.status_label.configure(fg=p.GOLD_SOFT)
        self.status_label.pack(side="bottom", fill="x", pady=(8, 0))
        self.bind("<Configure>", self._resize, add="+")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        self.pages = {}
        for name in ("Research", "Connections", "Evidence"):
            page = tk.Frame(self.tabs, bg=p.BLACK, padx=10, pady=10)
            self.pages[name] = page
            self.tabs.add(page, text=name)
        page = self.pages["Research"]
        actions = tk.Frame(page, bg=p.BLACK)
        actions.pack(fill="x", pady=(0, 8))
        for title, command in (("New conversation", self.clear), ("Attach document", self.attach),
                               ("Export", self.export)):
            self._button(actions, title, command).pack(side="left", padx=(0, 6))
        composer = tk.Frame(page, bg=p.BLACK)
        composer.pack(side="bottom", fill="x", pady=(8, 0))
        bar = tk.Frame(composer, bg=p.BLACK)
        bar.pack(side="bottom", fill="x", pady=(6, 0))
        self.send_button = self._button(bar, "Send to selected model", self.send, accent=True)
        self.send_button.pack(side="left")
        self.stop_button = ttk.Button(bar, text="Stop", command=self.stop, state="disabled", style="Hayat.TButton")
        self.stop_button.pack(side="left", padx=8)
        self._label(bar, "Ctrl+Enter to send").pack(side="right")
        frame, self.prompt = self._text(composer, height=3)
        frame.pack(fill="x")
        self.prompt.bind("<Control-Return>", lambda _event: (self.send(), "break")[1])
        frame, self.transcript = self._text(page, readonly=True)
        frame.pack(fill="both", expand=True)
        self._append("HAYAT", "Connect an account to research, reason and work with your documents.\n"
                     "Try: Compare the evidence in my attached documents, cite the relevant lines, and list the uncertainties.\n"
                     "Enable public web research or local-memory sharing in Connections when needed.\n"
                     "Try forecasts with evenly spaced numeric data; HAYAT reports backtest errors and a baseline comparison.")
        self._build_connections()
        page = self.pages["Evidence"]
        self._label(page, "Attached sources · excerpts may be sent when you press Send").pack(fill="x")
        row = tk.Frame(page, bg=p.BLACK)
        row.pack(fill="x", pady=8)
        self._button(row, "Attach document", self.attach).pack(side="left")
        self._button(row, "Remove all sources", self.remove_sources).pack(side="left", padx=8)
        self.sources = ttk.Treeview(page, columns=("id", "name", "size"), show="headings", height=4)
        for column, title, width in (("id", "ID", 50), ("name", "Document", 380), ("size", "Characters", 100)):
            self.sources.heading(column, text=title)
            self.sources.column(column, width=width, minwidth=40)
        self.sources.pack(fill="x", pady=(0, 10))
        self._label(page, "Execution record · actual tool arguments, results and source hashes").pack(fill="x")
        frame, self.evidence = self._text(page, height=10, readonly=True)
        frame.pack(fill="both", expand=True, pady=(6, 0))

    def _build_connections(self):
        outer = self.pages["Connections"]
        actions = tk.Frame(outer, bg=self.palette.BLACK)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        self.save_button = self._button(actions, "Save selection", self.save_settings, accent=True)
        self.save_button.pack(side="left")
        self._button(actions, "Forget this key", self.forget_key).pack(side="left", padx=8)
        scroll = ttk.Scrollbar(outer, orient="vertical")
        scroll.pack(side="right", fill="y")
        self.connection_canvas = tk.Canvas(outer, bg=self.palette.BLACK, highlightthickness=0,
                                           yscrollcommand=scroll.set)
        self.connection_canvas.pack(fill="both", expand=True)
        scroll.configure(command=self.connection_canvas.yview)
        page = tk.Frame(self.connection_canvas, bg=self.palette.BLACK)
        interior = self.connection_canvas.create_window(0, 0, window=page, anchor="nw")
        page.bind("<Configure>", lambda _e: self.connection_canvas.configure(scrollregion=self.connection_canvas.bbox("all")))
        self.connection_canvas.bind("<Configure>", lambda e: self.connection_canvas.itemconfigure(interior, width=e.width))
        page.columnconfigure(1, weight=1)
        for row, label in enumerate(("Provider", "API key", "Model ID", "Output tokens / call")):
            self._label(page, label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
        provider = ttk.Combobox(page, textvariable=self.provider, values=list(PROVIDERS), state="readonly", width=16)
        provider.grid(row=0, column=1, sticky="ew")
        provider.bind("<<ComboboxSelected>>", self._provider_changed)
        self._controls.append((provider, "readonly"))
        entry = ttk.Entry(page, textvariable=self.key, show="•")
        entry.grid(row=1, column=1, sticky="ew")
        self._controls.append((entry, "normal"))
        self.model_box = ttk.Combobox(page, textvariable=self.model)
        self.model_box.grid(row=2, column=1, sticky="ew")
        self._controls.append((self.model_box, "normal"))
        spin = ttk.Spinbox(page, textvariable=self.budget, from_=256, to=32768, increment=1024)
        spin.grid(row=3, column=1, sticky="ew")
        self._controls.append((spin, "normal"))
        self._button(page, "Discover models", self.discover).grid(row=2, column=2, padx=(8, 0))
        self._button(page, "Save key", self.save_key).grid(row=1, column=2, padx=(8, 0))
        self._label(page, textvariable=self.key_status).grid(row=4, column=0, columnspan=3, sticky="w", pady=6)
        for row, text, variable in ((5, "Enable external API access (provider billing may apply)", self.enabled),
                                    (6, "Allow scientific tools, forecasts and document retrieval", self.tools_enabled)):
            check = ttk.Checkbutton(page, text=text, variable=variable, command=self.save_settings)
            check.grid(row=row, column=0, columnspan=3, sticky="w", pady=6)
            self._controls.append((check, "normal"))
        for row, text, variable in ((7, "Allow public web search and page reading for this window", self.web_enabled),
                                   (8, "Share relevant local memories with the selected AI for this window", self.recall_enabled)):
            check = ttk.Checkbutton(page, text=text, variable=variable)
            check.grid(row=row, column=0, columnspan=3, sticky="w", pady=6)
            self._controls.append((check, "normal"))
        info = self._label(page, "Select the exact model you want; model discovery never selects a cheaper replacement.\n"
                           "Keys use the system keyring with encrypted local fallback.\n"
                           "Each answer allows up to 6 API calls and 16 local tool calls. Token usage is shown;\n"
                           "this is not a dollar spending cap. PRIVATE / LOCAL_ONLY and Emergency stop block calls.", wraplength=600)
        info.configure(fg=self.palette.MUTED)
        info.grid(row=10, column=0, columnspan=3, sticky="nw", pady=4)
        page.bind("<Configure>", lambda e: info.configure(wraplength=max(250, e.width - 16)), add="+")

    def _resize(self, event):
        if event.widget is self:
            width = max(300, event.width - 36)
            self.status_label.configure(wraplength=width)
            self.identity_label.configure(wraplength=width)

    def _identity(self):
        self.identity.set(f"{PROVIDERS[self.provider.get()][0]}  ·  {self.model.get() or 'No model selected'}  ·  "
                          f"{'API enabled' if self.enabled.get() else 'API disabled'}  ·  No automatic fallback")
        self.key_status.set("Key saved locally" if self.manager.has_key(self.provider.get()) else "No saved key for this provider")

    def _provider_changed(self, _event=None):
        self.key.set("")
        self.model.set(self.settings["models"].get(self.provider.get(), ""))
        self.model_box.configure(values=())
        self._identity()

    def save_settings(self):
        try:
            settings = {"provider": self.provider.get(), "enabled": self.enabled.get(),
                        "models": {**self.settings["models"], self.provider.get(): self.model.get().strip()},
                        "max_tokens": int(self.budget.get()), "tools": self.tools_enabled.get()}
            self.manager.save(settings)
            self.settings = settings
            self._identity()
            self.status.set("Settings saved. Send explicitly to use the selected provider and model.")
            return True
        except (ValueError, ConnectedAIError):
            self.status.set("Settings not saved. Check the model ID and token budget (256–32768).")
            return False

    def _start(self, operation, work, done):
        if self._busy or self._closed:
            return False
        self._busy = True
        self._cancel = threading.Event()
        for widget, _state in self._controls:
            widget.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set(operation)

        def worker():
            try:
                result = work()
                self._queue.put(("done", done, result))
            except ConnectedAIError as exc:
                self._queue.put(("error", str(exc), {"requests": exc.requests, "usage": exc.usage}))
            except Exception:
                self._queue.put(("error", "Operation failed. Check the file, credentials or local storage permissions.", {}))

        threading.Thread(target=worker, daemon=True, name="hayat-connected-ai").start()
        return True

    def _poll(self):
        if self._closed:
            return
        while not self._queue.empty():
            kind, value, result = self._queue.get_nowait()
            if kind == "progress":
                self.status.set(value)
                continue
            self._busy = False
            for widget, state in self._controls:
                widget.configure(state=state)
            self.stop_button.configure(state="disabled")
            if kind == "done":
                value(result)
            else:
                self.status.set(value)
                self._append("ERROR", value + ("\n" + json.dumps(result) if result else ""))
        self._poll_handle = self.after(60, self._poll)

    def save_key(self):
        provider, key = self.provider.get(), self.key.get()
        if not key.strip():
            self.status.set("Enter your provider API key in the masked field.")
            return
        self.key.set("")
        self._start("Storing key locally…", lambda: self.manager.store_key(provider, key),
                    lambda _result: (self._identity(), self.status.set("Key stored locally. It is never included in prompts or exports.")))

    def forget_key(self):
        provider = self.provider.get()
        self.key.set("")
        self._start("Removing saved key…", lambda: self.manager.forget_key(provider),
                    lambda _result: (self._identity(), self.status.set("Saved key removed for this provider.")))

    def discover(self):
        if self._busy or not self.save_settings():
            return
        provider = self.provider.get()

        def done(models):
            self.model_box.configure(values=models)
            self.status.set(f"Discovered {len(models)} models. Choose one from the Model ID list; your selection was not changed.")

        self._start("Discovering account models…", lambda: self.manager.discover(provider, self._cancel), done)

    def send(self):
        if self._busy or not self.save_settings():
            return False
        question = self.prompt.get("1.0", "end").strip()
        if not question:
            self.status.set("Write a question first.")
            return False
        provider, model = self.provider.get(), self.model.get().strip()
        runtime = getattr(self.gui, "runtime", None)
        self.research.connections = ResearchConnections(
            web=self.web_enabled.get(), recall=self.recall_enabled.get(), memory=getattr(runtime, "memory", None),
            session_id=getattr(getattr(runtime, "session", None), "session_id", None))
        messages = self.history + [{"role": "user", "content": question}]

        def progress(event):
            status = (f"{provider} / {model} · API request {event['request']} of 6" if event["phase"] == "request"
                      else f"Tool: {event['name']} · {'completed' if event['ok'] else 'invalid input reported'}")
            self._queue.put(("progress", status, None))

        def done(answer):
            history_text = (f"[Incomplete response: {answer.finish_reason}]\n" if answer.incomplete else "") + answer.text
            self.history = messages + [{"role": "assistant", "content": history_text}]
            self.records.append({"question": question, **asdict(answer)})
            self._append("YOU", question)
            label = f"{PROVIDERS[answer.provider][0]} / {answer.returned_model}"
            if answer.incomplete:
                label += " · INCOMPLETE RESPONSE"
            self._append(label, answer.text)
            self._set_text(self.evidence, json.dumps({"sources": self.research.catalog(), **asdict(answer)}, indent=2, ensure_ascii=False))
            outcome = f"Incomplete response ({answer.finish_reason})" if answer.incomplete else "Answer received"
            self.status.set(f"{outcome} · "
                            f"{answer.requests} API calls · {len(answer.tools)} tool calls · Usage: {json.dumps(answer.usage)}")
            if self.prompt.get("1.0", "end").strip() == question:
                self.prompt.delete("1.0", "end")

        return self._start(f"Connecting to {provider} / {model}…",
                           lambda: self.manager.ask(provider, model, messages, research=self.research,
                                                    cancel=self._cancel, progress=progress), done)

    def stop(self):
        self._cancel.set()
        self.status.set("Stopping after the current network call. A request already sent may still be billed.")

    def attach(self):
        if self._busy:
            return
        path = filedialog.askopenfilename(parent=self, title="Attach a document for Connected AI",
                                          filetypes=[("Text documents", "*.txt *.md *.csv *.json *.rst")])
        if path:
            self.attach_path(Path(path))

    def attach_path(self, path):
        def done(doc):
            self._attachment_paths.add(Path(path).resolve())
            self.sources.insert("", "end", iid=doc.id, values=(doc.id, doc.name, f"{len(doc.text):,}"))
            self.status.set(f"Attached {doc.id}: {doc.name}. Excerpts may be sent with your next question.")
            self._set_text(self.evidence, json.dumps({"sources": self.research.catalog()}, indent=2))
        return self._start("Reading selected document…", lambda: self.research.attach(path), done)

    def remove_sources(self):
        if self._busy:
            return
        self.research.documents.clear()
        self._attachment_paths.clear()
        self.sources.delete(*self.sources.get_children())
        self.status.set("Sources removed. Previous answers remain in conversation; New conversation clears them.")
        self._set_text(self.evidence, "No attached sources.")

    def clear(self):
        if self._busy:
            return
        self.history.clear()
        self.records.clear()
        self.prompt.delete("1.0", "end")
        self._set_text(self.transcript, "")
        self._set_text(self.evidence, json.dumps({"sources": self.research.catalog()}, indent=2))
        self.status.set("New conversation. Attached sources are retained; remove them in Evidence if needed.")

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Export conversation and evidence",
                                           defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                self.export_to(Path(path))
                self.status.set("Exported conversation, usage and tool evidence. Credentials are excluded.")
            except (ValueError, OSError):
                self.status.set("Export failed. Choose a writable file other than an attached source or app settings.")

    def export_to(self, path):
        path = Path(path).resolve()
        if path in self._attachment_paths or path.is_relative_to(self.manager.directory.resolve()):
            raise ValueError("Export cannot overwrite attached sources or application data.")
        path.write_text(json.dumps({"sources": self.research.catalog(), "answers": self.records},
                                   indent=2, ensure_ascii=False), encoding="utf-8")

    def _append(self, title, text):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"{title}\n{text}\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    @staticmethod
    def _set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.configure(state="disabled")

    def _destroyed(self, event):
        if event.widget is self:
            self._shutdown()

    def _shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        if self._poll_handle is not None:
            self.after_cancel(self._poll_handle)
            self._poll_handle = None
        self.key.set("")

    def close(self):
        self._shutdown()
        self.destroy()
