"""Numbers behind the Business dashboard, read from the owner's own books.

Everything here is a plain calculation over recorded invoices, payments,
expenses and wallet activity: period-over-period change, a six-month
revenue/expense series, invoice counts and what falls due next. Nothing is
estimated, forecast or invented; an empty book gives empty answers.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

#: The period compared against each picker choice ("vs last month").
PREVIOUS = {"this month": "last month", "this year": "last year"}


def previous_period(period: str, today: date | None = None) -> str | None:
    today = today or date.today()
    if period in PREVIOUS:
        return PREVIOUS[period]
    if period == "last month":
        first = today.replace(day=1) - timedelta(days=1)
        return (first.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    if period == "last year":
        return str(today.year - 2)
    return None


def months_back(count: int, today: date | None = None) -> list[str]:
    """The last ``count`` calendar months as YYYY-MM, oldest first, ending this month."""
    cursor = (today or date.today()).replace(day=1)
    months = []
    for _ in range(count):
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    return months[::-1]


def main_currency(report: dict, fallback: str) -> str:
    """The goal's currency, else the busiest one, else the owner's default."""
    goal = report.get("goal")
    if goal:
        return goal["target"].rsplit(" ", 1)[1]
    rows = report["by_currency"]
    if rows:
        return max(rows, key=lambda row: row["income_cents"])["currency"]
    return fallback


def line_for(report: dict, currency: str) -> dict | None:
    return next((row for row in report["by_currency"] if row["currency"] == currency), None)


def change(current: int, before: int | None) -> float | None:
    """Percent change, or None when there is nothing earlier to compare with."""
    if before is None or before == 0:
        return None
    return round(100 * (current - before) / abs(before), 1)


def money(cents: int, currency: str) -> str:
    return f"{cents / 100:,.2f} {currency}"


def expense_cents(row: dict | None) -> int:
    if not row:
        return 0
    return row["income_cents"] - row["profit_cents"]


def dashboard(ledger: Any, period: str, today: date | None = None) -> dict:
    """KPI values with trends, invoice counts, a six-month series and what is due next."""
    today = today or date.today()
    report = ledger.report(period)
    currency = main_currency(report, ledger.profile()["default_currency"])
    current = line_for(report, currency)
    earlier_name = previous_period(period, today)
    earlier = line_for(ledger.report(earlier_name), currency) if earlier_name else None
    income = current["income_cents"] if current else 0
    profit = current["profit_cents"] if current else 0
    spent = expense_cents(current)
    kpis = {
        "income": {"value": money(income, currency), "change": change(income, earlier["income_cents"] if earlier else None)},
        "profit": {"value": money(profit, currency), "change": change(profit, earlier["profit_cents"] if earlier else None)},
        "expenses": {"value": money(spent, currency), "change": change(spent, expense_cents(earlier) if earlier else None)},
        "margin": round(100 * profit / income, 1) if income > 0 else None,
        "compared_with": earlier_name,
    }
    series = []
    for month in months_back(6, today):
        line = line_for(ledger.report(month), currency)
        series.append({"month": month, "label": date.fromisoformat(month + "-01").strftime("%b"),
                       "income_cents": line["income_cents"] if line else 0, "expense_cents": expense_cents(line)})
    from app.tools.business import period_bounds
    start, end, _label = period_bounds(period)
    documents = ledger.invoices("all")["documents"]
    in_period = [d for d in documents if start <= d["issued"] < end]
    invoices = {
        "count": len(in_period),
        "paid": sum(d["status"] == "paid" for d in in_period),
        "pending": sum(d["status"] == "unpaid" and not d["overdue"] for d in in_period),
        "overdue": sum(bool(d["overdue"]) for d in in_period),
        "invoiced": money(sum(d["total_cents"] for d in in_period if d["currency"] == currency and d["status"] != "void"), currency),
    }
    upcoming = []
    for document in sorted((d for d in documents if d["status"] == "unpaid"), key=lambda d: d["due"]):
        days = (date.fromisoformat(document["due"]) - today).days
        when = (f"Overdue by {-days} day{'s' if days != -1 else ''}" if days < 0 else
                "Due today" if days == 0 else f"Due in {days} day{'s' if days != 1 else ''}")
        upcoming.append({"title": document["number"], "detail": f"{document['balance']} · {document['client']}",
                         "when": when, "date": document["due"], "state": "overdue" if days < 0 else "soon" if days <= 7 else "later",
                         "number": document["number"]})
    for lead in report["follow_ups_due"]:
        upcoming.append({"title": lead["title"], "detail": "Lead follow-up", "when": "Follow up now",
                         "date": lead["follow_up"] or "", "state": "overdue", "number": None})
    clients = ledger.clients()["clients"]
    return {"report": report, "currency": currency, "kpis": kpis, "series": series, "invoices": invoices,
            "upcoming": upcoming[:8], "transactions": ledger.wallet(activity=6)["activity"],
            "clients": len(clients), "suggestions": report["next_actions"]}
