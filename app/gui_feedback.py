"""👍 / 👎 buttons under each HAYAT reply in the chat, kept in the owner's feedback record.

Typing "that's wrong" after a reply already counts (app/owner_feedback.py), but a
verdict should take one click. Each reply, typed or spoken, gets a small bar:
Helpful, or Wrong with an optional correction. The verdict is stored with the
request and the reply it judges, and the correction is applied when a similar
question comes up. The bar is an embedded widget, so copying, searching and
saving the transcript see only the conversation.

2026-11-11: Wrong now also marks that reply *refused* in the dialogue record (app/dialogue_state.py), so it is never
sent again. The newest reply also offers **Try another way**: one click instead of typing "that's not what I asked,
look again deeper". It sends that sentence as the owner's message (shown in the chat as sent), and the assistant
answers the same question again by a different route (app/dialogue_turn.py). Older replies lose the button, because
the complaint is about the last answer.
"""
from __future__ import annotations

import logging
import tkinter as tk
from typing import Any

from app.gui_a11y import activatable
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
#: Bars kept on screen; older ones are removed so a long session stays light.
MAX_BARS = 60
#: What "Try another way" sends: words the complaint reader understands (off topic + deeper), shown as sent.
TRY_ANOTHER_WAY = "That's not what I asked. Look again, deeper, and try another way."


class ReplyFeedback:
    def __init__(self, gui: Any):
        self.gui = gui
        self.bars: list[tk.Frame] = []
        self.last_request = ""

    def note_request(self, text: str) -> None:
        self.last_request = str(text or "").strip()

    def attach(self, reply: str) -> tk.Frame | None:
        """Put a bar under the reply just inserted; the chat must be editable."""
        gui, chat = self.gui, self.gui.chat
        reply = str(reply or "").strip()
        if not reply:
            return None
        background = chat.cget("background")
        bar = tk.Frame(chat, bg=background)
        bar.request, bar.reply = self.last_request, reply
        bar.status = tk.Label(bar, text="", bg=background, fg=gui.MUTED, font=("Segoe UI", 9))
        for symbol, label, rating in (("👍", "Helpful", 1), ("👎", "Wrong", -1)):
            button = tk.Label(bar, text=f" {label}", bg=background, fg=gui.MUTED, font=("Segoe UI", 9),
                              cursor="hand2", padx=4, compound="left")
            try:
                from app.gui_emoji import _photo

                picture = _photo(chat, symbol, 14)
            except Exception:
                picture = None
            if picture is not None:
                button.configure(image=picture)
                button.image = picture
            else:
                button.configure(text=f"{symbol} {label}")
            activatable(button, lambda b=bar, r=rating: self.mark(b, r), gui)
            button.bind("<Enter>", lambda _event, w=button: w.configure(fg=gui.GOLD_SOFT))
            button.bind("<Leave>", lambda _event, w=button: w.configure(fg=gui.MUTED))
            button.pack(side="left")
            setattr(bar, "up" if rating > 0 else "down", button)
        bar.again = tk.Label(bar, text="↻ Try another way", bg=background, fg=gui.MUTED, font=("Segoe UI", 9),
                             cursor="hand2", padx=8)
        activatable(bar.again, lambda b=bar: self.try_again(b), gui)
        bar.again.bind("<Enter>", lambda _event, w=bar.again: w.configure(fg=gui.GOLD_SOFT))
        bar.again.bind("<Leave>", lambda _event, w=bar.again: w.configure(fg=gui.MUTED))
        bar.again.pack(side="left")
        bar.status.pack(side="left", padx=(8, 0))
        chat.window_create("end", window=bar, padx=10)
        chat.insert("end", "\n")
        # Only the newest reply can be answered again: the complaint is about the last answer.
        for older in self.bars:
            self._retire_again(older)
        self.bars.append(bar)
        while len(self.bars) > MAX_BARS:
            self.bars.pop(0).destroy()
        return bar

    @staticmethod
    def _retire_again(bar: tk.Frame) -> None:
        again = getattr(bar, "again", None)
        if again is not None and again.winfo_exists():
            again.pack_forget()

    def _refuse(self, bar: tk.Frame, kinds: set[str], complaint: str) -> bool:
        """Mark this reply refused in the dialogue record; False when there is no record or no such reply."""
        runtime = self.gui.runtime
        state = getattr(runtime, "dialogue", None)
        session = getattr(getattr(runtime, "session", None), "session_id", None)
        if state is None or not session:
            return False
        try:
            return state.reject_reply(session, bar.reply, kinds, complaint) is not None
        except Exception:
            log_swallowed(_log, "marking a reply refused from the chat", level=logging.WARNING)
            return False

    def try_again(self, bar: tk.Frame) -> str:
        """Send the complaint as the owner's own message; the assistant answers the question again, another way."""
        gui = self.gui
        if getattr(gui, "_working_since", None) is not None:
            bar.status.configure(text="A reply is still running — stop it first, then try again.", fg=gui.MUTED)
            return "break"
        try:
            gui.input.delete("1.0", "end")
            gui.input.insert("1.0", TRY_ANOTHER_WAY)
            gui.send()
        except Exception:
            log_swallowed(_log, "sending 'try another way' from the chat", level=logging.WARNING)
            bar.status.configure(text="Couldn't send that — type “look again, another way” instead.", fg=gui.ALERT)
            return "break"
        self._retire_again(bar)
        bar.status.configure(text="Asked again, another way.", fg=gui.MUTED)
        return "break"

    def ask_correction(self) -> str | None:
        """The right answer, if the owner wants to give one (None = cancelled)."""
        from tkinter import simpledialog

        return simpledialog.askstring(
            "What was wrong?", "What is the right answer? (optional — leave empty to just mark it wrong)",
            parent=self.gui.root)

    def mark(self, bar: tk.Frame, rating: int) -> str:
        correction = ""
        if rating < 0:
            answer = self.ask_correction()
            if answer is None:
                return "break"
            correction = answer.strip()
        runtime = self.gui.runtime
        try:
            from app.owner_feedback import OwnerFeedback

            session = getattr(getattr(runtime, "session", None), "session_id", None)
            OwnerFeedback.beside(runtime.memory).record(
                rating, "right" if rating > 0 else "wrong", correction, bar.request, bar.reply, session)
        except Exception:
            log_swallowed(_log, "saving reply feedback", level=logging.WARNING)
            bar.status.configure(text="Couldn't save that — try typing \"that's wrong\" instead.", fg=self.gui.ALERT)
            return "break"
        refused = rating < 0 and self._refuse(bar, {"wrong"}, correction or "marked wrong in the chat")
        for button in (bar.up, bar.down):
            button.unbind("<Button-1>")
            button.configure(cursor="")
        (bar.up if rating > 0 else bar.down).configure(fg=self.gui.GOLD_SOFT)
        if rating > 0:
            text = "Thanks — marked helpful."
        elif correction:
            text = "Marked wrong. I'll use your correction on similar questions."
        else:
            text = "Marked wrong. Tell me the right answer any time and I'll use it."
        if refused:
            text += " I won't give this answer again."
        bar.status.configure(text=text, fg=self.gui.MUTED)
        return "break"
