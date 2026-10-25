"""Languages & persona: what HAYAT speaks, how it talks, and what it may install.

Four things in one window, because they answer one question -- "how should
HAYAT talk and what can it pick up?":

* the persona switch (talk like a real person, or like an assistant);
* the reply language, from English to any of the 53 languages it has voices for;
* the languages it can speak, with a Learn button that shows the plan first;
* a software installer: find a program, read what would be installed, then install.

Pressing Install after reading a plan is the approval; nothing installs from a
search alone. Worker threads queue their results and this window polls them,
so Tk is only ever touched on its own thread.
"""
from __future__ import annotations

import queue
import threading
from app.gui_resources import BooleanVar, StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_theme import DarkScrolledText
from app.gui_icons import apply_icons
from app.gui_windows import place_over


class LanguagesStudio(tk.Toplevel):
    def __init__(self, gui: Any) -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette = gui, type(gui)
        self.runtime = gui.runtime
        self.results: queue.Queue = queue.Queue()
        self.busy = False
        self._language_plan: str | None = None
        self._software_plan: dict[str, Any] | None = None
        self.title("HAYAT · Languages & persona")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.bind("<Escape>", lambda _event: (self.destroy(), "break")[1])
        self._build()
        apply_icons(self, self.palette)
        place_over(self, gui.root, (880, 900))
        self.deiconify()
        self.lift()
        self.refresh()
        self._poll()

    # ------------------------------------------------------------ layout
    def _section(self, parent, title: str, note: str) -> tk.Frame:
        p = self.palette
        frame = tk.Frame(parent, bg=p.PANEL, padx=14, pady=10, highlightthickness=1, highlightbackground=p.LINE)
        frame.pack(fill="x", padx=16, pady=(0, 10))
        tk.Label(frame, text=title, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI Semibold", 12), anchor="w").pack(fill="x")
        tk.Label(frame, text=note, bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=780).pack(fill="x", pady=(0, 6))
        return frame

    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Languages & persona", bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.destroy).pack(side="right")

        persona = self._section(self, "Persona", "On: HAYAT talks with you like a real person -- natural, warm, with "
                                "opinions and follow-up questions, and no \"as an AI\" disclaimers. Asked sincerely, it "
                                "still says it is your AI, and anything sent to other people still needs your approval.")
        self.persona_var = BooleanVar(self, bool(getattr(getattr(self.runtime, "persona", None), "enabled", False)))
        tk.Checkbutton(persona, text="Talk like a real person", variable=self.persona_var, command=self.toggle_persona,
                       bg=p.PANEL, fg=p.WHITE, selectcolor=p.BLACK, activebackground=p.PANEL, activeforeground=p.GOLD,
                       font=("Segoe UI", 11), anchor="w").pack(fill="x")

        reply = self._section(self, "Reply language", "English unless you choose otherwise. In another language HAYAT "
                              "writes, speaks and listens in it; \"English please\" switches back from anywhere.")
        row = tk.Frame(reply, bg=p.PANEL)
        row.pack(fill="x")
        from app.languages import VOICES

        self._names = {name: code for code, (name, _voice, _size) in VOICES.items()}
        self.reply_var = StringVar(self, "English")
        self.reply_box = ttk.Combobox(row, textvariable=self.reply_var, state="readonly", width=28,
                                      values=sorted(self._names))
        self.reply_box.pack(side="left")
        ttk.Button(row, text="Use this language", style="HayatAccent.TButton", command=self.set_reply_language).pack(side="left", padx=8)
        self.reply_note = StringVar(self, "")
        tk.Label(reply, textvariable=self.reply_note, bg=p.PANEL, fg=p.GOLD_SOFT, font=("Segoe UI", 9), anchor="w",
                 justify="left", wraplength=780).pack(fill="x", pady=(6, 0))

        learn = self._section(self, "Languages HAYAT can speak", "Installed languages have a voice. Choose one and "
                              "press Learn to see what would be downloaded and studied; press Install to go ahead.")
        table = tk.Frame(learn, bg=p.PANEL)
        table.pack(fill="x")
        self.tree = ttk.Treeview(table, columns=("status", "size", "noticed"), show="tree headings", height=5,
                                 selectmode="browse")
        for column, title, width in (("#0", "Language", 220), ("status", "Status", 200), ("size", "Download", 90),
                                     ("noticed", "Messages seen", 110)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")
        buttons = tk.Frame(learn, bg=p.PANEL)
        buttons.pack(fill="x", pady=(6, 0))
        ttk.Button(buttons, text="Learn selected language", style="Hayat.TButton", command=self.plan_language).pack(side="left")
        self.language_install = ttk.Button(buttons, text="Install", style="HayatAccent.TButton", command=self.install_language)

        software = self._section(self, "Install software", "Type a program (for example VLC, 7-Zip, FFmpeg). HAYAT "
                                 "finds it in winget, Windows' package manager, and explains exactly what it would "
                                 "install. Nothing installs until you press Install.")
        find = tk.Frame(software, bg=p.PANEL)
        find.pack(fill="x")
        self.software_entry = tk.Entry(find, font=("Segoe UI", 11), bg=p.BLACK, fg=p.WHITE, insertbackground=p.GOLD, relief="flat")
        self.software_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.software_entry.bind("<Return>", lambda _e: self.plan_software())
        ttk.Button(find, text="Find", style="Hayat.TButton", command=self.plan_software).pack(side="left", padx=(8, 0))
        self.software_install = ttk.Button(find, text="Install", style="HayatAccent.TButton", command=self.install_software)

        self.plan_text = DarkScrolledText(self, height=5, wrap="word", bg=p.PANEL, fg=p.WHITE,
                                                   font=("Segoe UI", 10), padx=12, pady=8, relief="flat", state="disabled")
        self.plan_text.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        footer = tk.Frame(self, bg=p.BLACK, padx=16, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = StringVar(self, "")
        tk.Label(footer, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=140, style="Hayat.Horizontal.TProgressbar")

    # ------------------------------------------------------------ work off the UI thread
    def run(self, title: str, work, done) -> None:
        if self.busy:
            self.status_var.set("One moment -- still working on the last step.")
            return
        self.busy = True
        self.status_var.set(title + "…")
        self.progress.pack(side="right")
        self.progress.start(15)

        def job() -> None:
            try:
                self.results.put((done, work(), None))
            except Exception as exc:
                self.gui._record_failure(title, exc)
                self.results.put((done, None, exc))

        threading.Thread(target=job, name="hayat-languages", daemon=True).start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        while True:
            try:
                done, value, error = self.results.get_nowait()
            except queue.Empty:
                break
            self.busy = False
            self.progress.stop()
            self.progress.pack_forget()
            if error is not None:
                self.status_var.set(f"That didn't work: {type(error).__name__}: {error}")
            else:
                self.status_var.set("")
                done(value)
        self.after(80, self._poll)

    def show_plan(self, text: str) -> None:
        self.plan_text.configure(state="normal")
        self.plan_text.delete("1.0", "end")
        self.plan_text.insert("end", text)
        self.plan_text.configure(state="disabled")

    # ------------------------------------------------------------ actions
    def refresh(self) -> None:
        languages = getattr(self.runtime, "languages", None)
        if languages is None:
            self.show_plan("Language skills aren't available in this runtime.")
            return
        status = languages.status()
        noticed = status.get("noticed", {})
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for row in status["available"]:
            state = "Installed · can speak it" if row["installed"] else "Reads and writes it"
            self.tree.insert("", "end", iid=row["code"], text=row["name"],
                             values=(state, "" if row["installed"] else f"{row['size_mb']} MB", noticed.get(row["code"], "")))
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        session = self.runtime.session
        from app.languages import language_name

        self.reply_var.set(language_name(getattr(session, "reply_language", "en")) or "English")
        voices = ", ".join(row["name"] for row in status["installed"]) or "none yet"
        hearing = ("I can hear other languages too." if status.get("speech_model_ready")
                   else "To hear other languages, install the speech model: python -m scripts.setup_arabic")
        self.reply_note.set(f"Voices installed: {voices}. {hearing}")

    def toggle_persona(self) -> None:
        persona = getattr(self.runtime, "persona", None)
        if persona is None:
            return
        enabled = persona.set(self.persona_var.get())
        self.status_var.set("Persona mode is on." if enabled else "Back to assistant mode.")

    def set_reply_language(self) -> None:
        code = self._names.get(self.reply_var.get(), "en")
        self.runtime.session.reply_language = code
        languages = getattr(self.runtime, "languages", None)
        speaks = code == "en" or (languages is not None and languages.voice_file(code) is not None)
        self.status_var.set(f"Replies are now in {self.reply_var.get()}."
                            + ("" if speaks else " Its voice isn't installed yet: press Learn to add it."))

    def plan_language(self) -> None:
        selected = self.tree.selection()
        if not selected:
            self.status_var.set("Choose a language in the list first.")
            return
        code = selected[0]
        plan = self.runtime.languages.plan(code)
        self._language_plan = code
        self.show_plan(plan["text"] + "\n\nPress Install to go ahead.")
        self.language_install.pack(side="left", padx=8)

    def install_language(self) -> None:
        code = self._language_plan
        if not code:
            return
        self.language_install.pack_forget()
        self._language_plan = None

        def done(report):
            from app.languages import language_name

            lines = [f"{language_name(code)}: " + ("voice installed, I can speak it." if report.get("voice_installed")
                                                     else "the voice did not install.")]
            if report.get("study_queued"):
                lines.append("Queued study: " + "; ".join(report["study_queued"]) + ".")
            self.show_plan("\n".join(lines))
            self.refresh()

        self.run("Downloading and setting up", lambda: self.runtime.languages.install(code), done)

    def plan_software(self) -> None:
        query = self.software_entry.get().strip()
        if not query:
            self.status_var.set("Type the program to install first.")
            return
        self.software_install.pack_forget()

        def done(result):
            plan = result.output if result.success else {"found": False, "text": result.error}
            self._software_plan = plan if plan.get("found") else None
            self.show_plan(plan.get("text", "") + ("\n\nPress Install to go ahead." if plan.get("found") else ""))
            if plan.get("found"):
                self.software_install.pack(side="left", padx=(8, 0))

        self.run("Searching winget", lambda: self.runtime.registry.execute("find_software", {"query": query}), done)

    def install_software(self) -> None:
        plan = self._software_plan
        if not plan:
            return
        self.software_install.pack_forget()
        self._software_plan = None
        self.runtime.software.approve(plan["id"])          # pressing Install after reading the plan is the yes

        def done(result):
            output = result.output if isinstance(result.output, dict) else {}
            if result.success and output.get("ok"):
                self.show_plan(f"{plan['name']} " + ("was already installed." if output.get("already_installed")
                                                     else f"installed in {output.get('seconds', '?')} s."))
            else:
                self.show_plan(f"{plan['name']} didn't install: {output.get('error') or result.error}")

        self.run(f"Installing {plan['name']}", lambda: self.runtime.registry.execute(
            "install_software", {"package_id": plan["id"]}), done)
