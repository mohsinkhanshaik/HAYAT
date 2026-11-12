"""'I read “rember” as “remember”': how a misspelt message was read, under the reply it shaped.

HAYAT repairs misspelt words only when the repaired words reach something specific (app/intent/router.py), and
that reading was recorded for every turn but shown only if the owner asked "how did you get that?". When the
owner had meant the word as written (a name, a slang word, a product), there was no way to say so short of typing
it three more times. Now the chat says how it read the message, and one click keeps the owner's spelling for good
(app/intent/spelling.keep_words, saved beside the memory database) and offers to ask again with it.
"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from typing import Any

from app.gui_a11y import activatable
from app.logging import log_swallowed

_log = logging.getLogger(__name__)
#: Notes kept on screen; older ones are removed so a long session stays light.
MAX_NOTES = 30


def changes_of(reply: Any) -> list[tuple[str, str]]:
    """The (typed, read as) pairs the reply's request was read with."""
    entities = getattr(getattr(reply, "intent", None), "entities", None) or {}
    pairs = []
    for change in entities.get("spelling") or []:
        if isinstance(change, (list, tuple)) and len(change) == 2 and str(change[0]) != str(change[1]):
            pairs.append((str(change[0]), str(change[1])))
    return pairs


def sentence(changes: list[tuple[str, str]]) -> str:
    """'I read “rember” as “remember” and “reserach” as “research”.'"""
    parts = [f"“{typed}” as “{read}”" for typed, read in changes[:6]]
    if not parts:
        return ""
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    more = f" (and {len(changes) - 6} more)" if len(changes) > 6 else ""
    return f"I read {joined}{more}."


def topic_of(reply: Any) -> dict[str, Any] | None:
    """The topic decision the assistant put on the reply (app/topics.py), when there is one."""
    metadata = getattr(getattr(reply, "response", None), "metadata", None) or {}
    topic = metadata.get("topic")
    return topic if isinstance(topic, dict) and topic.get("topic_id") else None


def topic_line(topic: dict[str, Any] | None) -> tuple[str, str]:
    """('↪ Read in the topic “GDP …” as “…”.', fix-link label) -- ('', '') when the placement is unremarkable.

    Ordinary continuations, small talk, status questions and the first message say nothing: a note on every
    reply would be noise. Only the placements that change what the reply saw are shown, each with the one click
    that undoes it.
    """
    if not topic:
        return "", ""
    relation, title = str(topic.get("relation") or ""), str(topic.get("title") or "")
    if topic.get("anchored"):
        return f"↪ Read in the topic “{title}” as “{topic['anchored']}”.", "Not this topic"
    if relation == "new_topic" and topic.get("set_aside"):
        return f"↪ New topic: “{title}”. Earlier topics were set aside for this reply.", "Same topic as before"
    if relation == "return":
        restored = int(topic.get("restored") or 0)
        return (f"↩ Back to “{title}”" + (", with its earlier exchange restored." if restored else "."), "Not that topic")
    if relation == "connect":
        return f"⇄ Connected with an earlier topic; both were in view for “{title}”.", ""
    if relation == "other_person" or topic.get("person"):
        return f"👤 About your {topic.get('person') or 'contact'}: kept apart from facts about you.", ""
    if relation == "correction":
        return f"✎ Taken as a correction in “{title}”.", ""
    return "", ""


def words_file(runtime: Any) -> Path | None:
    path = getattr(getattr(runtime, "memory", None), "path", None)
    return Path(path).parent / "owner-words.json" if path else None


class ReadingNotes:
    def __init__(self, gui: Any):
        self.gui = gui
        self.notes: list[tk.Frame] = []

    def attach(self, request: str, changes: list[tuple[str, str]]) -> tk.Frame | None:
        """Put the note under the reply just shown; nothing when the message was read as typed."""
        text = sentence(changes)
        if not text or getattr(self.gui, "_closed", False):
            return None
        gui, chat = self.gui, self.gui.chat
        background = chat.cget("background")
        note = tk.Frame(chat, bg=background)
        note.request, note.changes = str(request or ""), list(changes)
        note.label = tk.Label(note, text="✎ " + text, bg=background, fg=gui.MUTED, font=("Segoe UI", 9))
        note.label.pack(side="left")
        note.keep = self._link(note, "Keep my spelling", lambda: self.keep(note))
        note.keep.pack(side="left", padx=(8, 0))
        note.again = self._link(note, "Ask again as I wrote it", lambda: self.ask_again(note))
        note.status = tk.Label(note, text="", bg=background, fg=gui.MUTED, font=("Segoe UI", 9))
        note.status.pack(side="left", padx=(8, 0))
        from app.gui_motion import reading_position

        with reading_position(chat, follow=gui._chat_follows()):
            chat.configure(state="normal")
            chat.window_create("end", window=note, padx=10)
            chat.insert("end", "\n")
            gui._transcript.write("✎ " + text + "\n")
            chat.configure(state="disabled")
        self.notes.append(note)
        while len(self.notes) > MAX_NOTES:
            self.notes.pop(0).destroy()
        return note

    def attach_topic(self, topic: dict[str, Any] | None) -> tk.Frame | None:
        """Say how the message was placed among the conversation's topics, with the click that undoes it."""
        text, fix = topic_line(topic)
        if not text or getattr(self.gui, "_closed", False):
            return None
        gui, chat = self.gui, self.gui.chat
        background = chat.cget("background")
        note = tk.Frame(chat, bg=background)
        note.topic = dict(topic or {})
        note.label = tk.Label(note, text=text, bg=background, fg=gui.MUTED, font=("Segoe UI", 9),
                              wraplength=max(320, int(chat.winfo_width() * 0.75) or 640), justify="left")
        note.label.pack(side="left")
        note.fix = self._link(note, fix, lambda: self.fix_topic(note)) if fix else None
        if note.fix is not None:
            note.fix.pack(side="left", padx=(8, 0))
        note.status = tk.Label(note, text="", bg=background, fg=gui.MUTED, font=("Segoe UI", 9))
        note.status.pack(side="left", padx=(8, 0))
        from app.gui_motion import reading_position

        with reading_position(chat, follow=gui._chat_follows()):
            chat.configure(state="normal")
            chat.window_create("end", window=note, padx=10)
            chat.insert("end", "\n")
            gui._transcript.write(text + "\n")
            chat.configure(state="disabled")
        self.notes.append(note)
        while len(self.notes) > MAX_NOTES:
            self.notes.pop(0).destroy()
        return note

    def fix_topic(self, note: tk.Frame) -> str:
        """Undo the placement: a wrongly joined message gets its own topic; a wrongly split one rejoins."""
        topics = getattr(self.gui.runtime, "topics", None)
        message_id = int(note.topic.get("message_id") or 0)
        if topics is None or not message_id:
            note.status.configure(text="The topic record isn't available.", fg=self.gui.ALERT)
            return "break"
        try:
            if note.topic.get("relation") == "new_topic":
                result = topics.same_topic(message_id)
                done = result.get("merged")
                said = f"Joined to “{result.get('title', '')}”. I'll keep it with that topic." if done else ""
            else:
                result = topics.separate(message_id)
                done = result.get("separated")
                said = f"Kept apart as its own topic, “{result.get('title', '')}”." if done else ""
        except Exception:
            log_swallowed(_log, "changing a message's topic from the chat", level=logging.WARNING)
            done, said, result = False, "", {}
        if note.fix is not None:
            note.fix.pack_forget()
        note.status.configure(text=said or result.get("reason") or "Couldn't change that.",
                              fg=self.gui.MUTED if done else self.gui.ALERT)
        return "break"

    def _link(self, note: tk.Frame, label: str, action) -> tk.Label:
        gui = self.gui
        link = tk.Label(note, text=label, bg=note.cget("bg"), fg=gui.GOLD_SOFT, font=("Segoe UI", 9, "underline"),
                        cursor="hand2", padx=2)
        activatable(link, lambda: (action(), "break")[1], gui)
        return link

    def keep(self, note: tk.Frame) -> str:
        from app.intent.spelling import keep_words

        typed = [word for word, _ in note.changes]
        try:
            kept = keep_words(typed, words_file(self.gui.runtime))
        except Exception:
            log_swallowed(_log, "keeping the owner's spelling", level=logging.WARNING)
            note.status.configure(text="Couldn't save that; the words will still be read as before.", fg=self.gui.ALERT)
            return "break"
        note.keep.pack_forget()
        listed = ", ".join(f"“{word}”" for word in kept)
        note.status.configure(text=f"Kept {listed}: I'll read it as you wrote it from now on.", fg=self.gui.MUTED)
        if note.request.strip():
            note.again.pack(side="left", padx=(8, 0))
        return "break"

    def ask_again(self, note: tk.Frame) -> str:
        """Send the original message again; with the spelling kept it is now read as written."""
        gui = self.gui
        if getattr(gui, "_working_since", None) is not None:
            note.status.configure(text="A reply is running; ask again when it finishes.")
            return "break"
        gui.input.delete("1.0", "end")
        gui.input.insert("1.0", note.request)
        note.again.pack_forget()
        gui.send()
        return "break"
