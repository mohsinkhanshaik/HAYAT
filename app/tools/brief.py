"""Today's business brief: what needs the owner's attention, read from the books.

It runs by itself (the window checks a few times a day) but only reads and
reminds: overdue invoices, unbilled hours, lead follow-ups, the next launch
step, actions waiting for confirmation, and how this month's income compares
with the goal. It sends, publishes and changes nothing.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.tools.registry import ToolRegistry


class BusinessBrief:
    def __init__(self, ledger: Any, launchpad: Any = None, gate: Any = None, desk: Any = None) -> None:
        self.ledger, self.launchpad, self.gate, self.desk = ledger, launchpad, gate, desk

    def brief(self) -> dict[str, Any]:
        report = self.ledger.report("this month")
        streams = self.ledger.income_streams("this month")
        items: list[str] = list(report["next_actions"])
        if self.launchpad is not None:
            try:
                ventures = self.launchpad.ventures()
                if ventures:
                    status = self.launchpad.status(ventures[-1]["id"])
                    if status["done"] < status["total"]:
                        items.append(f"{status['business']} launch ({status['progress']}): next, {status['next_step']}")
            except Exception:
                items.append("I couldn't read the launch plan this time.")
        if self.gate is not None:
            for action in self.gate.pending():
                items.append(f"Action {action['action_id']} is waiting for your confirmation: {action['title']}.")
        if self.desk is not None:
            # Read only: the brief shows the HAYAT Wallet decisions already waiting, it doesn't make new ones.
            try:
                for item in self.desk.decisions("proposed")["decisions"][:3]:
                    items.append(f"Money decision {item['id']} waits for you: {item['title']}. Say \"approve decision {item['id']}\" "
                                 f"or open the HAYAT Wallet.")
                for item in self.desk.decisions("approved")["decisions"][:3]:
                    items.append(f"You approved decision {item['id']} ({item['title']}): pay it, then say \"decision {item['id']} is paid\".")
            except Exception:  # the brief stands without the wallet desk
                items.append("I couldn't read the HAYAT Wallet decisions this time.")
        from app.tools.business import money as format_money

        money = [f"earned {format_money(row['income_cents'], row['currency'])}, profit before tax "
                 f"{row['profit_before_tax']}" for row in report["by_currency"]]
        try:
            from app.business_deep import review
            deep = review(self.ledger)
            if deep["actions"] and not deep["empty"]:
                items.insert(0, "Most money at stake: " + deep["actions"][0])
        except Exception:  # the brief stands without the deep review
            items.append("I couldn't read the deep review this time.")
        attention = [item for item in items if not item.startswith("No income or leads recorded yet")]
        return {"date": date.today().isoformat(), "needs_attention": attention, "all_items": items,
                "this_month": money or ["Nothing earned yet this month."],
                "streams": [f"{s['source']} ({s['kind'].lower()}): {s['amount']}, {s['share']:g}%" for s in
                            streams["streams"][:5]],
                "goal": report.get("goal"), "notes": streams["notes"],
                "basis": "Read from your records. Nothing was sent or changed."}

    @staticmethod
    def text(brief: dict[str, Any]) -> str:
        lines = [f"Business brief for {brief['date']}"]
        lines.append("This month: " + "; ".join(brief["this_month"]) + ".")
        goal = brief.get("goal")
        if goal:
            pace = "" if "on_pace" not in goal else (" On pace." if goal["on_pace"] else " Behind pace.")
            lines.append(f"Goal {goal['target']}: {goal['percent']:g}% reached, {goal['remaining']} to go.{pace}")
        if brief["streams"]:
            lines.append("Income streams: " + "; ".join(brief["streams"]) + ".")
        lines.extend(brief["notes"])
        if brief["needs_attention"]:
            lines.append("Needs your attention:")
            lines.extend(f"  • {item}" for item in brief["needs_attention"])
        else:
            lines.append("Nothing needs your attention today.")
        return "\n".join(lines)

    # ------------------------------------------------------------------ automatic, once a day
    def due_today(self) -> bool:
        """True when there is something to act on and today's brief was not shown yet."""
        shown = self.ledger._run(lambda db: db.execute(
            "SELECT value FROM profile WHERE key='brief_shown_on'").fetchone())
        if shown and shown[0] == date.today().isoformat():
            return False
        return bool(self.brief()["needs_attention"])

    def mark_shown(self) -> None:
        self.ledger._run(lambda db: db.execute(
            "INSERT INTO profile VALUES ('brief_shown_on', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (date.today().isoformat(),)))


def register_brief_tools(registry: ToolRegistry, brief: BusinessBrief) -> None:
    registry.register(
        "business_brief",
        "Today's business brief: income this month and by stream, goal progress, and what needs attention "
        "(overdue invoices, unbilled hours, lead follow-ups, next launch step, actions awaiting confirmation). "
        "Read-only.", {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low", "filesystem_read", lambda: (lambda result: {**result, "text": brief.text(result)})(brief.brief()))
