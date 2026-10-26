"""The Learn window: a language course you can read, write, hear and speak.

It drives the same lesson as the chat -- one open exercise per course, kept by
the tutor -- so an answer typed here or in the conversation moves the same
lesson forward, and this window follows whichever was used.

Arabic is drawn by Tk through Windows' own text shaping, which joins the
letters and lays the line out right to left; nothing here reorders text.
"""
from __future__ import annotations

import queue
import threading
from app.gui_resources import StringVar

import tkinter as tk
from tkinter import ttk
from typing import Any

from app.gui_icons import apply_icons
from app.gui_windows import place_over
from app.gui_fonts import DISPLAY

ARABIC_FONT = ("Segoe UI", 54)
CARD_FONT = ("Segoe UI", 26)
#: The on-screen keyboard: the alphabet in order, then the hamza and ending forms.
KEYBOARD_ROWS = (
    "ا ب ت ث ج ح خ د ذ ر".split(),
    "ز س ش ص ض ط ظ ع غ ف".split(),
    "ق ك ل م ن ه و ي ء ة".split(),
    "أ إ آ ى ؤ ئ".split(),
)
SKILL_TITLES = {"reading": "Reading", "writing": "Writing", "listening": "Listening", "speaking": "Speaking"}


class LearnStudio(tk.Toplevel):
    def __init__(self, gui: Any, course: str = "arabic") -> None:
        super().__init__(gui.root)
        self.withdraw()
        self.gui, self.palette, self.course = gui, type(gui), course
        self.learning = gui.runtime.learning
        self.tutor = self.learning.language
        self.title("HAYAT · Learn Arabic")
        self.transient(gui.root)
        self.configure(bg=gui.BLACK)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _event: (self.close(), "break")[1])
        self.busy = False
        self.exercise: dict[str, Any] | None = None
        # Worker threads never touch Tk: they queue results and this window
        # polls the queue on its own thread (the ControlStudio pattern).
        self.results: queue.Queue = queue.Queue()
        self._alphabet: tk.Toplevel | None = None
        self._keyboard_pinned = False          # opened by hand: stays until closed by hand
        self._build()
        self._unsubscribe = self.tutor.add_listener(lambda course, step: self.results.put(("follow", course, step)))
        apply_icons(self, self.palette)
        place_over(self, gui.root, (900, 760))
        self.deiconify()
        self.lift()
        self.run("Opening the lesson", lambda: self.tutor.start(self.course))
        self._poll()

    # ------------------------------------------------------------ layout
    def _build(self) -> None:
        p = self.palette
        header = tk.Frame(self, bg=p.BLACK, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Learn Arabic", bg=p.BLACK, fg=p.WHITE, font=(DISPLAY, 16)).pack(side="left")
        self.unit_var = StringVar(self, "")
        tk.Label(header, textvariable=self.unit_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 10)).pack(side="left", padx=12)
        ttk.Button(header, text="Close", style="Hayat.TButton", command=self.close).pack(side="right")
        ttk.Button(header, text="Alphabet", style="Hayat.TButton", command=self.show_alphabet).pack(side="right", padx=4)
        self.setup_button = ttk.Button(header, text="Set up voice", style="HayatAccent.TButton",
                                       command=self.install_speech)

        skills = tk.Frame(self, bg=p.BLACK, padx=16)
        skills.pack(fill="x")
        self.skill_bars: dict[str, ttk.Progressbar] = {}
        self.skill_notes: dict[str, StringVar] = {}
        for column, (key, title) in enumerate(SKILL_TITLES.items()):
            cell = tk.Frame(skills, bg=p.BLACK)
            cell.grid(row=0, column=column, sticky="ew", padx=4)
            skills.grid_columnconfigure(column, weight=1, uniform="skill")
            tk.Label(cell, text=title, bg=p.BLACK, fg=p.GOLD_SOFT, font=("Segoe UI Semibold", 9), anchor="w").pack(fill="x")
            bar = ttk.Progressbar(cell, maximum=100, style="Hayat.Horizontal.TProgressbar")
            bar.pack(fill="x", pady=2)
            self.skill_bars[key] = bar
            self.skill_notes[key] = StringVar(self, "")
            tk.Label(cell, textvariable=self.skill_notes[key], bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 8), anchor="w").pack(fill="x")

        card = tk.Frame(self, bg=p.PANEL, padx=20, pady=14, highlightthickness=1, highlightbackground=p.LINE)
        card.pack(fill="both", expand=True, padx=16, pady=10)
        self.new_strip = tk.Frame(card, bg=p.PANEL)
        self.skill_var = StringVar(self, "")
        tk.Label(card, textvariable=self.skill_var, bg=p.PANEL, fg=p.GOLD, font=("Segoe UI Semibold", 10), anchor="w").pack(fill="x")
        self.display_var = StringVar(self, "")
        self.display = tk.Label(card, textvariable=self.display_var, bg=p.PANEL, fg=p.WHITE, font=ARABIC_FONT)
        self.display.pack(fill="x", pady=(6, 2))
        self.prompt_var = StringVar(self, "Opening the lesson…")
        self.prompt = tk.Label(card, textvariable=self.prompt_var, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 12),
                               wraplength=800, justify="center")
        self.prompt.pack(fill="x", pady=4)

        controls = tk.Frame(card, bg=p.PANEL)
        controls.pack(pady=6)
        self.listen_button = ttk.Button(controls, text="Listen", style="Hayat.TButton", command=self.listen)
        self.speak_button = ttk.Button(controls, text="Speak", style="HayatAccent.TButton", command=self.speak)
        self.hint_button = ttk.Button(controls, text="Hint", style="Hayat.TButton", command=lambda: self.send("hint"))
        self.skip_button = ttk.Button(controls, text="Skip", style="Hayat.TButton", command=lambda: self.send("skip"))
        for button in (self.listen_button, self.speak_button, self.hint_button, self.skip_button):
            button.pack(side="left", padx=4)

        entry_row = tk.Frame(card, bg=p.PANEL)
        entry_row.pack(fill="x", pady=(8, 4))
        self.answer = tk.Entry(entry_row, font=("Segoe UI", 20), bg=p.BLACK, fg=p.WHITE, insertbackground=p.GOLD,
                               relief="flat", justify="center")
        self.answer.pack(side="left", fill="x", expand=True, ipady=6)
        self.answer.bind("<Return>", lambda _event: self.check())
        self.check_button = ttk.Button(entry_row, text="Check", style="HayatAccent.TButton", command=self.check)
        self.check_button.pack(side="left", padx=(8, 0))
        self.keyboard_button = ttk.Button(entry_row, text="⌨", width=3, style="Hayat.TButton", command=self.toggle_keyboard)
        self.keyboard_button.pack(side="left", padx=(6, 0))

        self.keyboard = tk.Frame(card, bg=p.PANEL)
        for row_keys in KEYBOARD_ROWS:
            row = tk.Frame(self.keyboard, bg=p.PANEL)
            row.pack()
            for key in row_keys:                          # packed from the right: the script runs right to left
                tk.Button(row, text=key, width=3, font=("Segoe UI", 16), bg=p.PANEL_2, fg=p.WHITE, relief="flat",
                          activebackground=p.BLACK, activeforeground=p.GOLD, cursor="hand2",
                          command=lambda char=key: self._type(char)).pack(side="right", padx=2, pady=2)
        tail = tk.Frame(self.keyboard, bg=p.PANEL)
        tail.pack(pady=(2, 0))
        for text, command in (("space", lambda: self._type(" ")), ("⌫ delete", self._backspace), ("clear", lambda: self.answer.delete(0, "end"))):
            tk.Button(tail, text=text, font=("Segoe UI", 10), bg=p.PANEL_2, fg=p.WHITE, relief="flat",
                      activebackground=p.BLACK, activeforeground=p.GOLD, cursor="hand2", command=command).pack(side="left", padx=3)

        self.feedback_var = StringVar(self, "")
        self.feedback = tk.Label(card, textvariable=self.feedback_var, bg=p.PANEL, fg=p.OLIVE_SOFT, font=("Segoe UI", 11),
                                 wraplength=800, justify="left", anchor="w")
        self.feedback.pack(fill="x", pady=(8, 0))

        footer = tk.Frame(self, bg=p.BLACK, padx=16, pady=6)
        footer.pack(fill="x", side="bottom")
        self.status_var = StringVar(self, "")
        tk.Label(footer, textvariable=self.status_var, bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w").pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=120, style="Hayat.Horizontal.TProgressbar")

    # ------------------------------------------------------------ actions
    def run(self, title: str, work, *, record: bool = True) -> None:
        """Run tutor work off the UI thread; recording can take several seconds."""
        if self.busy:
            self.status_var.set("One moment -- still working on the last step.")
            return
        self.busy = True
        self.status_var.set(title + "…")
        self.progress.pack(side="right")
        self.progress.start(15)

        def job() -> None:
            try:
                step = work()
                if record and step is not None:
                    self.learning.language_reply(step, self.gui.runtime.session)
                self.results.put(("done", step, None))
            except Exception as exc:
                self.gui._record_failure(title, exc)
                self.results.put(("done", None, exc))

        threading.Thread(target=job, name="hayat-learn", daemon=True).start()

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        while True:
            try:
                kind, first, second = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "done":
                self._done(first, second)
            elif kind == "follow":
                self._followed(first, second)
            elif kind == "status":
                self.status_var.set(first)
            elif kind == "feedback":
                self.feedback_var.set(first)
        self.after(60, self._poll)

    def _done(self, step, error) -> None:
        if not self.winfo_exists():
            return
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        if error is not None:
            self.status_var.set(f"That didn't work: {type(error).__name__}: {error}")
            return
        self.status_var.set("")
        if step is not None:
            self.show_step(step)

    def send(self, text: str) -> None:
        self.run("Working", lambda: self.tutor.answer(self.course, text))

    def check(self) -> None:
        value = self.answer.get().strip()
        if not value:
            self.feedback_var.set("Type an answer first, or press Hint.")
            return
        self.answer.delete(0, "end")
        self.run("Checking", lambda: self.tutor.answer(self.course, value) or self.tutor.start(self.course))

    def speak(self) -> None:
        self.status_var.set("Listening -- say it now…")
        self.run("Listening", lambda: self.tutor.record_answer(self.course))

    def listen(self) -> None:
        if not self.tutor.channels()["listen"]:
            self.feedback_var.set("The Arabic voice isn't installed yet. Press \"Install voice & speech\".")
            return
        threading.Thread(target=lambda: self.tutor.replay(self.course), daemon=True, name="hayat-learn-say").start()

    def install_speech(self) -> None:
        def work():
            from scripts.setup_arabic import install_speech_model, install_voice

            self.results.put(("status", "Downloading the Arabic voice and speech model (about 550 MB)…", None))
            lines = [install_voice(), install_speech_model("small")]
            if hasattr(self.tutor.listener, "_checked"):
                self.tutor.listener._checked = (0.0, False)
            self.results.put(("feedback", "\n".join(lines), None))
            return self.tutor.start(self.course)
        self.run("Installing", work, record=False)

    def toggle_keyboard(self) -> None:
        if self.keyboard.winfo_manager():
            self.keyboard.pack_forget()
            self._keyboard_pinned = False
        else:
            self.keyboard.pack(before=self.feedback, pady=(4, 0))
            self._keyboard_pinned = True

    def _type(self, char: str) -> None:
        self.answer.insert("insert", char)
        self.answer.focus_set()

    def _backspace(self) -> None:
        position = self.answer.index("insert")
        if position > 0:
            self.answer.delete(position - 1)

    def close(self) -> None:
        try:
            self._unsubscribe()
        except Exception:  # noqa: S110 -- closing; the tutor may already be gone
            pass
        if self._alphabet is not None and self._alphabet.winfo_exists():
            self._alphabet.destroy()
        self.destroy()

    # ------------------------------------------------------------ painting
    def _followed(self, course: str, step) -> None:
        """A step taken from the chat: show it here too."""
        if course == self.course and self.winfo_exists() and not self.busy:
            self.show_step(step)

    def show_step(self, step) -> None:
        p = self.palette
        verdict = step.verdict or {}
        lead = step.text.split("\n\n", 1)[0] if step.verdict is not None else ""
        colour = p.OLIVE_SOFT if verdict.get("correct") else p.GOLD if verdict.get("close") else p.ALERT if step.verdict else p.MUTED
        self.feedback.configure(fg=colour)
        self.feedback_var.set(lead if step.verdict is not None else ("" if step.exercise else step.text))
        self._paint_new(step.introduced)
        exercise = step.exercise
        self.exercise = exercise
        if exercise is None:
            self.skill_var.set("")
            self.display_var.set("")
            self.prompt_var.set(step.text if step.finished else "")
        else:
            self.skill_var.set(SKILL_TITLES[exercise["skill"]].upper())
            self.display_var.set(exercise["display"] if exercise["display"] else "✍")
            self.prompt_var.set(exercise["prompt"].replace(" Press Speak in the Learn window, or type \"record\".", ""))
            self.answer.configure(justify="right" if exercise["skill"] == "writing" else "center")
            if exercise["skill"] == "writing" and not self.keyboard.winfo_manager():
                self.keyboard.pack(before=self.feedback, pady=(4, 0))
            elif exercise["skill"] != "writing" and self.keyboard.winfo_manager() and not self._keyboard_pinned:
                self.keyboard.pack_forget()
            if exercise["skill"] == "speaking":
                self.speak_button.state(["!disabled"])
                self.answer.configure(state="disabled")
            else:
                self.answer.configure(state="normal")
                self.answer.focus_set()
        self._paint_progress()

    def _paint_new(self, cards: list[dict]) -> None:
        for child in self.new_strip.winfo_children():
            child.destroy()
        if not cards:
            self.new_strip.pack_forget()
            return
        p = self.palette
        tk.Label(self.new_strip, text="NEW", bg=p.PANEL, fg=p.ROYAL_SOFT, font=("Segoe UI Semibold", 9)).pack(side="right", padx=(8, 0))
        for card in cards:
            cell = tk.Frame(self.new_strip, bg=p.PANEL_2, padx=10, pady=6)
            cell.pack(side="right", padx=4)
            tk.Label(cell, text=card["arabic"], bg=p.PANEL_2, fg=p.WHITE, font=CARD_FONT).pack()
            tk.Label(cell, text=f"{card['transliteration']} · {card['meaning']}", bg=p.PANEL_2, fg=p.GOLD_SOFT,
                     font=("Segoe UI", 9)).pack()
            cell.bind("<Button-1>", lambda _e, text=card["arabic"]: self.tutor.say(text))
        self.new_strip.pack(fill="x", pady=(0, 8), before=self.display)

    def _paint_progress(self) -> None:
        data = self.tutor.progress(self.course)
        # Left-to-right marks fence the Arabic in a unit title, so Windows'
        # bidi layout does not pull the numbers after it into the Arabic run.
        mark = chr(0x200E)       # left-to-right mark
        self.unit_var.set(f"Unit {data['unit']} of {data['units']} · {mark}{data['unit_title']}{mark} · "
                          f"{data['introduced']} of {data['items']} items met")
        for skill, row in data["skills"].items():
            self.skill_bars[skill].configure(value=round(row["strength"] * 100))
            accuracy = "" if row["accuracy"] is None else f" · {row['accuracy']:.0%} right"
            self.skill_notes[skill].set(f"{row['learned']} learned{accuracy}")
        channels = data["channels"]
        self.listen_button.state(["!disabled"] if channels["listen"] else ["disabled"])
        speaking = channels["speak"] and (self.exercise or {}).get("skill") == "speaking"
        self.speak_button.state(["!disabled"] if speaking else ["disabled"])
        self.speak_button.configure(style="HayatChosen.TButton" if speaking else "Hayat.TButton")
        self.check_button.configure(style="Hayat.TButton" if speaking else "HayatAccent.TButton")
        if channels["listen"] and channels["speak"]:
            self.setup_button.pack_forget()
        elif not self.setup_button.winfo_manager():
            self.setup_button.pack(side="right", padx=4)

    # ------------------------------------------------------------ alphabet
    def show_alphabet(self) -> None:
        from app.learning.courses.arabic import LETTERS, letter_forms

        if self._alphabet is not None and self._alphabet.winfo_exists():
            self._alphabet.lift()
            return
        p = self.palette
        window = self._alphabet = tk.Toplevel(self)
        window.title("HAYAT · The Arabic alphabet")
        window.configure(bg=p.BLACK)
        window.bind("<Escape>", lambda _e: window.destroy())
        tk.Label(window, text="Click a letter to hear its name. Forms: alone · start · middle · end",
                 bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9)).pack(fill="x", padx=12, pady=(10, 4))
        grid = tk.Frame(window, bg=p.BLACK)
        grid.pack(padx=12, pady=8)
        for index, letter in enumerate(LETTERS):
            forms = letter_forms(letter)
            cell = tk.Frame(grid, bg=p.PANEL, padx=8, pady=6, cursor="hand2")
            cell.grid(row=index // 7, column=6 - index % 7, padx=3, pady=3, sticky="nsew")   # right to left
            parts = [tk.Label(cell, text=letter.arabic, bg=p.PANEL, fg=p.WHITE, font=("Segoe UI", 28)),
                     tk.Label(cell, text=f"{letter.meanings[0]} · {letter.transliteration[0]}", bg=p.PANEL, fg=p.GOLD_SOFT,
                              font=("Segoe UI", 8)),
                     tk.Label(cell, text="  ".join((forms["final"], forms["medial"], forms["initial"], forms["isolated"])),
                              bg=p.PANEL, fg=p.MUTED, font=("Segoe UI", 12))]
            for widget in (cell, *parts):
                widget.bind("<Button-1>", lambda _e, text=letter.spoken: self.tutor.say(text))
            for part in parts:
                part.pack()
        ttk.Button(window, text="Close", style="Hayat.TButton", command=window.destroy).pack(pady=(0, 10))
        place_over(window, self)
