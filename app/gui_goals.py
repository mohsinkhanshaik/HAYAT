"""The Mind window's "Goals & facts" page: what the dialogue record holds about the owner, and his controls over it.

Everything shown is read from data/memory/dialogue.db (app/dialogue_state.py):

* the goals he stated, with amount, year, how often he said it, and the sentence it came from;
* facts about him in their two classes: permanent facts, and circumstances with the date he said each one (an old
  circumstance is marked as possibly out of date, because it is a starting point that can change);
* the answers he refused (the kind of complaint, what produced the answer, when), which are never sent again;
* how often replies repeated an earlier one, were refused, or were sent again after being refused.

The controls change only that record: a fact read wrongly is forgotten, a goal no longer wanted is dropped. Nothing
here is estimated; an empty record says it is empty.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import UTC, datetime
from typing import Any

#: A circumstance older than this is shown as possibly out of date.
STALE_DAYS = 60


def _money(value: Any) -> str:
    from app.dialogue_state import _money as money

    return money(value)


def _age_days(moment: Any, now: datetime | None = None) -> int | None:
    try:
        stamp = datetime.fromisoformat(str(moment))
    except (TypeError, ValueError):
        return None
    stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - stamp).days


class GoalsPage:
    """Built inside a MindWindow page; uses the window's tree, button and note helpers so it looks the same."""

    def __init__(self, window: Any, page: tk.Frame) -> None:
        self.window, self.page = window, page
        p = window.palette
        self.summary = tk.Label(page, text="", bg=p.BLACK, fg=p.MUTED, font=("Segoe UI", 9), anchor="w",
                                justify="left", wraplength=980)
        self.summary.pack(fill="x", pady=(6, 0))
        # Wrap to the page's real width: a fixed wrap length cut the sentence off in a narrow window.
        page.bind("<Configure>", lambda event: self.summary.configure(wraplength=max(300, event.width - 12)), add="+")
        # The first column of each table is the row's own text (#0); the rest are its values.
        self._heading("Goals you stated")
        self.goal_tree = window._tree(page, (("statement", "In your words", 470), ("amount", "Amount", 100),
                                             ("year", "By", 60), ("times", "Said", 50), ("last", "Last said", 110)),
                                      height=2)
        # The controls sit on the heading row: a row of their own pushed the last table out of an 820x600 window.
        row = self._heading("What I know about you — permanent facts, and circumstances that can change")

        for label, command in (("Drop selected goal", self.drop_goal), ("Forget selected fact", self.forget_fact)):
            ttk.Button(row, text=label, command=command, style="Hayat.TButton").pack(side="right", padx=(6, 0))
        self.fact_tree = window._tree(page, (("value", "Fact", 300), ("kind", "Kind", 100), ("said", "Said on", 100),
                                             ("note", "Note", 260), ("from", "From your words", 300)), height=3)
        self._heading("Answers you refused (never sent again)")
        self.refused_tree = window._tree(page, (("reply", "The answer", 380), ("why", "Why", 130),
                                                ("asked", "It answered", 260), ("source", "Made by", 150),
                                                ("when", "Refused", 100)), height=3)
        # Only the last table takes spare height; the others keep their row count, so all three fit at 820x600.
        for tree in (self.goal_tree, self.fact_tree):
            tree.master.pack_configure(expand=False)
        # In a short window (the 820x600 minimum) the upper tables give up a row each so the refused answers stay
        # visible; rows are 32 px at 150% display scaling.
        page.bind("<Configure>", self._fit_height, add="+")
        self.goal_rows: dict[str, dict[str, Any]] = {}
        self.fact_rows: dict[str, dict[str, Any]] = {}

    def _fit_height(self, event: Any) -> None:
        short = event.height < 470
        wanted = (1, 2) if short else (2, 3)
        for tree, rows in zip((self.goal_tree, self.fact_tree), wanted):
            if int(tree.cget("height")) != rows:
                tree.configure(height=rows)
            # The sideways scrollbar (grid row 1 of the table's frame) is the next thing to give up.
            for bar in tree.master.grid_slaves(row=1):
                if short and bar.winfo_manager() == "grid":
                    bar.grid_remove()
            if not short and not tree.master.grid_slaves(row=1):
                for bar in tree.master.winfo_children():
                    if isinstance(bar, ttk.Scrollbar) and str(bar.cget("orient")) == "horizontal":
                        bar.grid()

    def _heading(self, text: str) -> tk.Frame:
        p = self.window.palette
        row = tk.Frame(self.page, bg=p.BLACK)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text=text, bg=p.BLACK, fg=p.WHITE, font=("Segoe UI Semibold", 10), anchor="w").pack(side="left")
        return row

    @property
    def state(self) -> Any:
        return getattr(self.window.runtime, "dialogue", None)

    def refresh(self) -> None:
        for tree in (self.goal_tree, self.fact_tree, self.refused_tree):
            tree.delete(*tree.get_children())
        self.goal_rows, self.fact_rows = {}, {}
        state = self.state
        if state is None:
            self.summary.configure(text="The dialogue record isn't available in this runtime.")
            return
        from app.dialogue_state import CIRCUMSTANCE, LABELS, PERMANENT

        report = state.report(self.window._session_id())
        for goal in report["goals"]:
            iid = f"g{goal['id']}"
            self.goal_rows[iid] = goal
            self.goal_tree.insert("", "end", iid=iid, text=goal["statement"][:200], values=(
                _money(goal.get("amount")), goal.get("horizon") or "—", goal["times"], str(goal["last_at"])[:10]))
        facts = report["facts"]
        for kind, label in ((PERMANENT, "permanent"), (CIRCUMSTANCE, "can change")):
            for row in facts.get(kind, []):
                iid = f"f{row['id']}"
                self.fact_rows[iid] = row
                age = _age_days(row["at"])
                note = ("may be out of date — tell me if it changed" if kind == CIRCUMSTANCE and age is not None
                        and age > STALE_DAYS else "a starting point, not a limit" if kind == CIRCUMSTANCE else "")
                self.fact_tree.insert("", "end", iid=iid,
                                      text=f"{LABELS.get(row['attribute'], row['attribute'])}: {row['value']}",
                                      values=(label, str(row["at"])[:10], note, row["statement"][:160]))
        for row in report["refused"]:
            self.refused_tree.insert("", "end", iid=f"r{row['id']}", text=" ".join(str(row["reply"]).split())[:200],
                                     values=(str(row["rejected_as"]).replace(",", ", "), row["request"][:160],
                                             row["source"] or "—", str(row["rejected_at"])[:10]))
        measures = report["measures"]
        held = (f"{len(report['goals'])} goal(s), {len(facts.get(PERMANENT, []))} permanent fact(s), "
                f"{len(facts.get(CIRCUMSTANCE, []))} circumstance(s), {len(report['refused'])} refused answer(s).")
        counted = (f" Last {measures['days']} days: {measures['replies']} replies, "
                   f"{measures['repeats_of_an_earlier_reply']} repeated an earlier one word for word, "
                   f"{measures['refused']} refused, {measures['sent_again_after_being_refused']} sent again after "
                   "being refused." if measures.get("replies") else " No replies recorded yet.")
        self.summary.configure(text="Read from what you said, with the words each came from. " + held + counted)

    def _selected(self, tree: Any, rows: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        chosen = tree.selection()
        return rows.get(chosen[0]) if chosen else None

    def forget_fact(self) -> None:
        row = self._selected(self.fact_tree, self.fact_rows)
        if row is None or self.state is None:
            self.window._say("Choose a fact first.")
            return
        removed = self.state.forget_fact(int(row["id"]))
        self.window._say(f"Forgot “{row['value']}”." if removed else "That fact was already gone.")
        self.refresh()

    def drop_goal(self) -> None:
        goal = self._selected(self.goal_tree, self.goal_rows)
        if goal is None or self.state is None:
            self.window._say("Choose a goal first.")
            return
        dropped = self.state.close_goal(str(goal["key"]))
        self.window._say("Dropped that goal; I won't weigh answers against it any more." if dropped
                         else "That goal was already closed.")
        self.refresh()
