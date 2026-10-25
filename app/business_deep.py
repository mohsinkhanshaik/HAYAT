"""A deep review of the owner's own books: where the money is, when it will come, and what to do first.

Read-only, from the records in business.db, with the market around it from the jobs HAYAT has collected:

* **Cash ahead:** unpaid invoices by when they will probably arrive. Each client's own record moves the date: a
  client who paid their last invoices 12 days late is expected 12 days after the due date. Set against the
  average of the last three months' expenses.
* **Collection:** per client, how many days after issue and after the due date they pay, and what is overdue now.
* **Real hourly rate:** money actually paid for invoices built from logged time, divided by those hours, per
  client and overall, next to the rate agreed. Unbilled hours are listed by age; old ones are the easiest money.
* **Dependence:** each client's share of the last twelve months' income; one client over half is a risk.
* **Pipeline:** open leads, valued at the owner's own win rate once at least five leads have closed (by source
  when a source has five); leads untouched for two weeks.
* **The goal:** what is still needed this month, per remaining day, and in hours at the real rate or invoices of
  the usual size, after the money already expected this month.
* **Spending:** this month's categories against the three months before; six months of income against expenses.
* **Pricing against the market:** the real hourly rate next to what similar job postings pay (yearly pay ÷ 2,080
  hours), when HAYAT has seen enough postings with pay.
* **Earning opportunities:** contract, freelance and part-time postings that fit the owner's CV, and services
  the CV supports, priced from those postings.
* **Actions, ranked by the money at stake.**

Everything is a reading of records the owner made: payments are as recorded, not verified, and nothing here is a
promise of income. Nothing is sent or changed.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from app.tools.business import money

OPEN_LEADS = ("new", "contacted", "proposal")
MIN_CLOSED = 5  # closed leads needed before a win rate is used
STALE_DAYS = 14
CONTRACT = re.compile(r"(?i)\b(?:contract(?:or)?|freelance|part[\s-]?time|hourly|temporary|temp|gig|project[\s-]based|1099|consultant)\b")


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _month(day: date) -> str:
    return day.strftime("%Y-%m")


def _months_back(today: date, count: int) -> list[str]:
    months, year, month = [], today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}-{month:02d}")
        month -= 1
        if not month:
            year, month = year - 1, 12
    return list(reversed(months))


def review(ledger: Any, jobs: Any = None, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    with ledger._db() as db:
        clients = {row["id"]: dict(row) for row in db.execute("SELECT * FROM clients")}
        documents = [dict(row) for row in db.execute("SELECT * FROM documents WHERE kind='invoice' AND status!='void'")]
        payments = [dict(row) for row in db.execute("SELECT * FROM payments")]
        entries = [dict(row) for row in db.execute("SELECT * FROM time_entries WHERE running_since IS NULL")]
        expenses = [dict(row) for row in db.execute("SELECT * FROM expenses")]
        leads = [dict(row) for row in db.execute("SELECT * FROM leads")]
        other = [dict(row) for row in db.execute("SELECT * FROM other_income")]
    goal = ledger._goal()
    used = Counter(row["currency"] for row in documents) + Counter(row["currency"] for row in expenses) + \
        Counter(row["currency"] for row in other)
    currency = (goal[1] if goal else None) or (used.most_common(1)[0][0] if used else ledger.profile().get("currency") or "USD")

    paid_by_invoice: dict[str, int] = defaultdict(int)
    last_paid: dict[str, date] = {}
    for payment in payments:
        paid_by_invoice[payment["number"]] += payment["amount_cents"]
        day = _day(payment["paid_on"])
        if day and (payment["number"] not in last_paid or day > last_paid[payment["number"]]):
            last_paid[payment["number"]] = day

    # ------------------------------------------------------------------ collection, per client
    lateness: dict[int, list[int]] = defaultdict(list)
    to_pay: dict[int, list[int]] = defaultdict(list)
    for document in documents:
        if paid_by_invoice[document["number"]] >= document["total_cents"] and document["number"] in last_paid:
            paid = last_paid[document["number"]]
            lateness[document["client_id"]].append((paid - _day(document["due"])).days)
            to_pay[document["client_id"]].append((paid - _day(document["issued"])).days)
    collection = []
    for client_id, client in clients.items():
        owed = [doc for doc in documents if doc["client_id"] == client_id and paid_by_invoice[doc["number"]] < doc["total_cents"]]
        overdue = sum(doc["total_cents"] - paid_by_invoice[doc["number"]] for doc in owed if _day(doc["due"]) < today)
        if not lateness[client_id] and not owed:
            continue
        late = _median(lateness[client_id])
        collection.append({"client": client["name"], "paid_invoices": len(lateness[client_id]),
                           "median_days_to_pay": _median(to_pay[client_id]), "median_days_late": late,
                           "overdue": money(overdue, client["currency"]) if overdue else "", "overdue_cents": overdue,
                           "currency": client["currency"],
                           "habit": ("no paid invoices yet" if late is None else "pays on time" if late <= 2 else
                                     f"usually pays about {round(late)} days late")})
    collection.sort(key=lambda row: (-row["overdue_cents"], -(row["median_days_late"] or 0)))

    # ------------------------------------------------------------------ cash ahead
    buckets = {"next 30 days": 0, "31–60 days": 0, "61–90 days": 0, "overdue, still expected": 0, "at risk (over 60 days overdue)": 0}
    expected_this_month = 0
    month_end = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    unpaid = []
    for document in documents:
        balance = document["total_cents"] - paid_by_invoice[document["number"]]
        if balance <= 0 or document["currency"] != currency:
            continue
        due = _day(document["due"])
        habit = _median(lateness[document["client_id"]]) if len(lateness[document["client_id"]]) >= 2 else 0
        expected = due + timedelta(days=max(0, round(habit or 0)))
        overdue_days = (today - due).days
        if overdue_days > 60:
            bucket = "at risk (over 60 days overdue)"
        elif expected < today:
            bucket = "overdue, still expected"
        else:
            ahead = (expected - today).days
            bucket = "next 30 days" if ahead <= 30 else "31–60 days" if ahead <= 60 else "61–90 days"
        buckets[bucket] = buckets.get(bucket, 0) + balance
        if bucket != "at risk (over 60 days overdue)" and expected <= month_end:
            expected_this_month += balance
        unpaid.append({"number": document["number"], "client": clients[document["client_id"]]["name"], "balance_cents": balance,
                       "due": document["due"], "expected": expected.isoformat(), "overdue_days": max(0, overdue_days)})
    months = _months_back(today, 4)
    spent = defaultdict(int)
    for expense in expenses:
        if expense["currency"] == currency:
            spent[expense["spent_on"][:7]] += expense["amount_cents"]
    previous = [spent[month] for month in months[:3]]
    monthly_spend = round(sum(previous) / 3) if any(previous) else 0
    cash = {"buckets": [{"when": key, "amount": money(value, currency), "cents": value} for key, value in buckets.items() if value],
            "monthly_spend": money(monthly_spend, currency), "monthly_spend_cents": monthly_spend,
            "net_next_30": money(buckets["next 30 days"] + buckets["overdue, still expected"] - monthly_spend, currency),
            "expected_this_month": money(expected_this_month, currency), "expected_this_month_cents": expected_this_month,
            "invoices": sorted(unpaid, key=lambda row: row["expected"])}

    # ------------------------------------------------------------------ real hourly rate and unbilled time
    hours_by_invoice: dict[str, float] = defaultdict(float)
    for entry in entries:
        if entry["invoice_number"]:
            hours_by_invoice[entry["invoice_number"]] += entry["minutes"] / 60
    rate_rows, total_paid, total_hours = [], 0, 0.0
    by_client: dict[int, list[float]] = defaultdict(lambda: [0, 0.0])
    for document in documents:
        hours = hours_by_invoice.get(document["number"])
        if not hours or document["currency"] != currency or document["number"] not in last_paid:
            continue
        paid = min(paid_by_invoice[document["number"]], document["total_cents"] - document["tax_cents"])
        by_client[document["client_id"]][0] += paid
        by_client[document["client_id"]][1] += hours
        total_paid += paid
        total_hours += hours
    for client_id, (paid, hours) in by_client.items():
        client = clients[client_id]
        effective = round(paid / hours) if hours else 0
        rate_rows.append({"client": client["name"], "hours": round(hours, 1), "effective": money(effective, currency),
                          "effective_cents": effective, "agreed_cents": client["rate_cents"],
                          "agreed": money(client["rate_cents"], currency) if client["rate_cents"] else "",
                          "gap_pct": round((effective / client["rate_cents"] - 1) * 100) if client["rate_cents"] else None})
    rate_rows.sort(key=lambda row: row["effective_cents"])
    effective_rate = round(total_paid / total_hours) if total_hours >= 1 else None
    unbilled = []
    for entry in entries:
        if entry["invoice_number"]:
            continue
        client = clients[entry["client_id"]]
        age = (today - (_day(entry["work_date"]) or today)).days
        unbilled.append({"client": client["name"], "hours": entry["minutes"] / 60, "value_cents": round(entry["minutes"] * entry["rate_cents"] / 60),
                         "currency": client["currency"], "age_days": age})
    grouped: dict[str, dict[str, Any]] = {}
    for item in unbilled:
        row = grouped.setdefault(item["client"], {"client": item["client"], "hours": 0.0, "value_cents": 0, "oldest_days": 0,
                                                  "currency": item["currency"]})
        row["hours"] += item["hours"]
        row["value_cents"] += item["value_cents"]
        row["oldest_days"] = max(row["oldest_days"], item["age_days"])
    unbilled_rows = sorted(({**row, "hours": round(row["hours"], 2), "value": money(row["value_cents"], row["currency"])}
                            for row in grouped.values()), key=lambda row: -row["value_cents"])

    # ------------------------------------------------------------------ dependence on one client
    year_ago = today - timedelta(days=365)
    income_by_client: dict[str, int] = defaultdict(int)
    for payment in payments:
        day = _day(payment["paid_on"])
        document = next((doc for doc in documents if doc["number"] == payment["number"]), None)
        if day and day >= year_ago and document and document["currency"] == currency:
            income_by_client[clients[document["client_id"]]["name"]] += payment["amount_cents"]
    for item in other:
        if item["currency"] == currency and (_day(item["received_on"]) or today) >= year_ago:
            income_by_client[f"{item['source']} (other income)"] += item["amount_cents"]
    year_total = sum(income_by_client.values())
    shares = [{"client": name, "share_pct": round(value / year_total * 100, 1), "amount": money(value, currency)}
              for name, value in sorted(income_by_client.items(), key=lambda item: -item[1])] if year_total else []
    dependence = None
    if shares and shares[0]["share_pct"] >= 50:
        dependence = (f"{shares[0]['client']} brought {shares[0]['share_pct']:g}% of the last twelve months' income: losing them would "
                      f"cut it by that much. A second steady client is the cheapest insurance.")

    # ------------------------------------------------------------------ pipeline at the owner's own win rate
    closed = [lead for lead in leads if lead["status"] in ("won", "lost")]
    won = [lead for lead in closed if lead["status"] == "won"]
    win_rate = len(won) / len(closed) if len(closed) >= MIN_CLOSED else None
    by_source: dict[str, list[str]] = defaultdict(list)
    for lead in closed:
        by_source[lead["source"].strip().lower() or "unknown"].append(lead["status"])
    source_rates = {source: sum(status == "won" for status in statuses) / len(statuses)
                    for source, statuses in by_source.items() if len(statuses) >= MIN_CLOSED}
    open_rows = []
    for lead in leads:
        if lead["status"] not in OPEN_LEADS:
            continue
        rate = source_rates.get(lead["source"].strip().lower(), win_rate)
        updated = _day(lead["updated"]) or today
        open_rows.append({"id": lead["id"], "title": lead["title"], "source": lead["source"], "status": lead["status"],
                          "value_cents": lead["value_cents"] if lead["currency"] == currency else 0,
                          "value": money(lead["value_cents"], lead["currency"]) if lead["value_cents"] else "",
                          "expected_cents": round(lead["value_cents"] * rate) if rate is not None and lead["currency"] == currency else None,
                          "stale_days": (today - updated).days, "follow_up": lead["follow_up"] or ""})
    pipeline_value = sum(row["value_cents"] for row in open_rows)
    expected_value = sum(row["expected_cents"] or 0 for row in open_rows) if win_rate is not None or source_rates else None
    pipeline = {"open": len(open_rows), "value": money(pipeline_value, currency), "value_cents": pipeline_value,
                "win_rate_pct": round(win_rate * 100) if win_rate is not None else None, "closed": len(closed),
                "by_source": {source: round(rate * 100) for source, rate in source_rates.items()},
                "expected": money(expected_value, currency) if expected_value is not None else "",
                "stale": [row for row in open_rows if row["stale_days"] >= STALE_DAYS],
                "note": (f"Valued at your own win rate: {round(win_rate * 100)}% of {len(closed)} closed leads." if win_rate is not None else
                         f"Only {len(closed)} closed lead{'s' if len(closed) != 1 else ''}: too few to know your win rate, so open leads "
                         "are not valued (mark leads won or lost as they close).")}

    # ------------------------------------------------------------------ income and spending over six months
    six = _months_back(today, 6)
    income_month: dict[str, int] = defaultdict(int)
    for payment in payments:
        document = next((doc for doc in documents if doc["number"] == payment["number"]), None)
        if document and document["currency"] == currency:
            income_month[payment["paid_on"][:7]] += payment["amount_cents"]
    for item in other:
        if item["currency"] == currency:
            income_month[item["received_on"][:7]] += item["amount_cents"]
    trend = [{"month": month, "income": money(income_month[month], currency), "expenses": money(spent[month], currency),
              "income_cents": income_month[month], "expense_cents": spent[month],
              "margin_pct": round((income_month[month] - spent[month]) / income_month[month] * 100) if income_month[month] else None}
             for month in six]
    this_month = _month(today)
    categories = []
    for category in sorted({expense["category"] for expense in expenses if expense["currency"] == currency}):
        now = sum(expense["amount_cents"] for expense in expenses if expense["category"] == category and expense["spent_on"][:7] == this_month
                  and expense["currency"] == currency)
        before = [sum(expense["amount_cents"] for expense in expenses if expense["category"] == category and expense["spent_on"][:7] == month
                      and expense["currency"] == currency) for month in months[:3]]
        average = sum(before) / 3
        if now or average:
            categories.append({"category": category, "this_month": money(now, currency), "average": money(round(average), currency),
                               "rising": bool(average and now > average * 1.5 and now - average >= 2000),
                               "this_month_cents": now})
    categories.sort(key=lambda row: -row["this_month_cents"])

    # ------------------------------------------------------------------ the goal
    pace = None
    if goal and goal[1] == currency:
        target = goal[0]
        earned = income_month[this_month]
        days_left = (month_end - today).days + 1
        gap = max(0, target - earned)
        after_expected = max(0, gap - expected_this_month)
        invoice_sizes = [doc["total_cents"] for doc in documents if doc["currency"] == currency]
        typical = round(_median(invoice_sizes)) if invoice_sizes else None
        pace = {"target": money(target, currency), "earned": money(earned, currency), "gap": money(gap, currency),
                "per_day": money(round(gap / days_left), currency) if gap else money(0, currency), "days_left": days_left,
                "expected_this_month": money(expected_this_month, currency),
                "after_expected": money(after_expected, currency), "after_expected_cents": after_expected,
                "in_hours": round(after_expected / effective_rate, 1) if effective_rate and after_expected else None,
                "in_invoices": -(-after_expected // typical) if typical and after_expected else None,
                "typical_invoice": money(typical, currency) if typical else ""}

    # ------------------------------------------------------------------ the market, from the jobs HAYAT collected
    market = earning_market(jobs, currency, effective_rate) if jobs is not None else None

    # ------------------------------------------------------------------ actions, by the money at stake
    actions: list[dict[str, Any]] = []
    for row in cash["invoices"]:
        if row["overdue_days"] > 0:
            habit = next((item["habit"] for item in collection if item["client"] == row["client"]), "")
            actions.append({"cents": row["balance_cents"], "text": f"Chase {row['number']} from {row['client']}: {money(row['balance_cents'], currency)}, "
                                                                     f"{row['overdue_days']} days overdue" + (f" ({habit})" if habit else "") + "."})
    for row in unbilled_rows:
        if row["currency"] == currency:
            actions.append({"cents": row["value_cents"], "text": f"Invoice {row['hours']:g} unbilled hours for {row['client']} ({row['value']})"
                                                                + (f"; the oldest is {row['oldest_days']} days old" if row["oldest_days"] >= 14 else "") + "."})
    for row in pipeline["stale"]:
        actions.append({"cents": row["expected_cents"] or round(row["value_cents"] * .2),
                        "text": f"Follow up lead {row['id']} ({row['title'][:50]}{', ' + row['value'] if row['value'] else ''}): "
                                f"no update for {row['stale_days']} days."})
    for row in rate_rows:
        if row["gap_pct"] is not None and row["gap_pct"] <= -15:
            actions.append({"cents": round((row["agreed_cents"] - row["effective_cents"]) * row["hours"]),
                            "text": f"{row['client']} pays an effective {row['effective']} an hour against the agreed {row['agreed']} "
                                    f"({row['gap_pct']}%): unpaid or discounted time. Check the invoices or the rate."})
    if dependence:
        actions.append({"cents": 0, "text": dependence})
    for row in categories:
        if row["rising"]:
            actions.append({"cents": row["this_month_cents"], "text": f"{row['category']} spending is {row['this_month']} this month against "
                                                                     f"about {row['average']} usually."})
    actions.sort(key=lambda item: -item["cents"])
    basis = ("From your own records: payments as you recorded them, not verified; expected dates follow each client's past "
             "habit. Market figures come from job postings HAYAT collected, not the whole market. Nothing was sent or changed.")
    return {"business_deep": True, "currency": currency, "today": today.isoformat(), "cash": cash, "collection": collection,
            "rates": rate_rows, "effective_rate": money(effective_rate, currency) if effective_rate else "",
            "effective_rate_cents": effective_rate, "hours_behind_rate": round(total_hours, 1), "unbilled": unbilled_rows,
            "income_shares": shares[:6], "dependence": dependence, "pipeline": pipeline, "trend": trend, "categories": categories,
            "goal": pace, "market": market, "actions": [item["text"] for item in actions[:10]], "empty": not (documents or expenses or leads or other),
            "basis": basis}


def _hourly(pay: dict[str, Any]) -> float | None:
    factor = {"year": 2080, "month": 2080 / 12, "week": 40, "day": 8, "hour": 1}.get(pay.get("period") or "year")
    top = pay.get("max") or pay.get("min")
    if not factor or not top:
        return None
    middle = (pay["min"] + pay["max"]) / 2 if pay.get("min") and pay.get("max") else top
    return middle / factor


def earning_market(jobs: Any, currency: str, effective_rate: int | None) -> dict[str, Any]:
    """Hourly pay in matching postings, contract and part-time work that fits, and services the CV supports."""
    try:
        rows = jobs.store.matches(limit=400)
        data = jobs.profile.load()
        skills = jobs.profile.skills(data)
    except Exception:  # the review stands without the jobs side
        return {"available": False, "reason": "no job data yet"}
    hourly = sorted(value for value in (_hourly((row.get("reading") or {}).get("salary") or {}) for row in rows
                                        if ((row.get("reading") or {}).get("salary") or {}).get("currency") == currency) if value)
    pay = None
    if len(hourly) >= 3:
        pay = {"low": money(round(hourly[len(hourly) // 4] * 100), currency), "median": money(round(statistics.median(hourly) * 100), currency),
               "high": money(round(hourly[(3 * len(hourly)) // 4] * 100), currency), "postings": len(hourly),
               "median_cents": round(statistics.median(hourly) * 100)}
    comparison = ""
    if pay and effective_rate:
        ratio = effective_rate / pay["median_cents"]
        comparison = (f"Your real rate is {round((ratio - 1) * 100):+d}% against the median of {pay['postings']} matching postings with pay "
                      f"({pay['median']} an hour, yearly pay ÷ 2,080 hours).")
    contracts = [{"number": row["number"], "title": row["title"], "company": row["company"], "score": row["score"],
                  "verdict": row["verdict"], "source_name": row["source_name"]}
                 for row in rows if row.get("verdict") in ("strong", "good") and
                 CONTRACT.search(" ".join([row.get("employment_type", ""), row.get("title", ""), row.get("description", "")[:1500]]))][:6]
    demand = Counter(name for row in rows for name in ((row.get("reading") or {}).get("skills") or {}).get("required", []))
    mine = {skill.lower() for skill in skills}
    services = [{"skill": name, "postings": count} for name, count in demand.most_common(30) if name.lower() in mine][:5]
    return {"available": True, "pay": pay, "comparison": comparison, "contract_jobs": contracts, "services": services,
            "note": "Postings show what employers pay employees; freelancers usually set their own rate above that to cover gaps, "
                    "tools and tax. Use it as a floor, not a price."}


def report_lines(value: dict[str, Any]) -> list[str]:
    if value.get("empty"):
        return ["Your books are empty, so there is nothing to review yet. Record a client, an invoice, an expense or a lead "
                "(\"add client Acme at 40 an hour\", \"log 3 hours for Acme\"), and the review fills in from there.", value["basis"]]
    lines = [f"Deep business review ({value['currency']}, {value['today']})"]
    if value["actions"]:
        lines.append("Do first, by the money at stake:")
        lines += [f"  {index}. {text}" for index, text in enumerate(value["actions"][:6], 1)]
    cash = value["cash"]
    if cash["buckets"]:
        lines.append("Money expected: " + "; ".join(f"{item['when']} {item['amount']}" for item in cash["buckets"])
                     + f". Usual monthly spending {cash['monthly_spend']}; net over the next 30 days about {cash['net_next_30']}.")
    goal = value["goal"]
    if goal:
        lines.append(f"Goal {goal['target']}: {goal['earned']} earned, {goal['gap']} to go in {goal['days_left']} days ({goal['per_day']} a day). "
                     f"{goal['expected_this_month']} is expected in by month end"
                     + (f"; after that {goal['after_expected']} remains" + (f", about {goal['in_hours']:g} hours at your real rate" if goal["in_hours"] else "")
                        + (f" or {goal['in_invoices']} invoice{'s' if goal['in_invoices'] != 1 else ''} of your usual {goal['typical_invoice']}" if goal["in_invoices"] else "")
                        if goal["after_expected_cents"] else ", which covers it") + ".")
    if value["effective_rate"]:
        lines.append(f"Real hourly rate: {value['effective_rate']} over {value['hours_behind_rate']:g} invoiced hours"
                     + "".join(f"; {row['client']} {row['effective']}" + (f" (agreed {row['agreed']}, {row['gap_pct']:+d}%)" if row["agreed"] else "")
                               for row in value["rates"][:4]) + ".")
    late = [row for row in value["collection"] if row["median_days_late"] is not None]
    if late:
        lines.append("Paying habits: " + "; ".join(f"{row['client']} {row['habit']}" + (f", {row['overdue']} overdue" if row["overdue"] else "")
                                                 for row in late[:5]) + ".")
    if value["income_shares"]:
        lines.append("Where the last 12 months' income came from: " + "; ".join(f"{row['client']} {row['share_pct']:g}%" for row in value["income_shares"][:4]) + ".")
    pipeline = value["pipeline"]
    if pipeline["open"]:
        lines.append(f"Pipeline: {pipeline['open']} open lead{'s' if pipeline['open'] != 1 else ''} worth {pipeline['value']}"
                     + (f", expected {pipeline['expected']}" if pipeline["expected"] else "") + f". {pipeline['note']}")
    months = [row for row in value["trend"] if row["income_cents"] or row["expense_cents"]]
    if months:
        lines.append("Six months: " + "; ".join(f"{row['month']} in {row['income']} out {row['expenses']}" for row in months) + ".")
    market = value.get("market") or {}
    if market.get("available"):
        if market.get("pay"):
            pay = market["pay"]
            lines.append(f"Matching postings pay {pay['low']}–{pay['high']} an hour (median {pay['median']}, {pay['postings']} postings). "
                         + (market["comparison"] + " " if market["comparison"] else "") + market["note"])
        if market["contract_jobs"]:
            lines.append("Contract and part-time work that fits you: " + "; ".join(f"job {row['number']} {row['title']} at {row['company']} ({row['score']})"
                                                                               for row in market["contract_jobs"][:4]) + ".")
        if market["services"]:
            lines.append("Skills employers ask for that you have, to sell as a service: "
                         + ", ".join(f"{item['skill']} ({item['postings']} postings)" for item in market["services"]) + ".")
    lines.append(value["basis"])
    return lines


# ---------------------------------------------------------------------- tool, chat routes, reply
TOOL_NAMES = ("business_deep_review",)


def register_business_deep_tools(registry: Any, ledger: Any, jobs: Any = None) -> None:
    registry.register("business_deep_review", "A deep review of the owner's own books: money expected and when (by each client's paying "
                      "habit), collection and late payers, the real hourly rate against agreed rates, unbilled hours by age, dependence "
                      "on one client, the pipeline at the owner's own win rate, goal pacing, spending trends, pay in matching job "
                      "postings, contract work that fits, and actions ranked by the money at stake. Read-only; records, not verified.",
                      {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                      "low", "filesystem_read", lambda: review(ledger, jobs))


_LEAD = r"^(?:(?:hey\s+)?hayat[\s,.!:-]*)?(?:please\s+)?(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?"
_ROUTES = re.compile(_LEAD + r"(?:"
                     r"(?:do\s+(?:a\s+)?)?(?:deep|full|detailed|in[\s-]depth)\s+(?:business\s+)?(?:review|analysis|check)(?:\s+of\s+my\s+business)?"
                     r"|(?:review|analy[sz]e|audit|check)\s+my\s+business(?:\s+(?:in\s+depth|deeply|in\s+detail|properly))?"
                     r"|how\s+is\s+my\s+business\s+(?:really\s+)?doing"
                     r"|business\s+health(?:\s+check)?"
                     r"|(?:show\s+(?:me\s+)?)?my\s+cash\s*flow(?:\s+forecast)?|cash\s*flow\s+forecast"
                     r"|when\s+will\s+i\s+(?:get|be)\s+paid"
                     r"|who\s+pays\s+(?:me\s+)?late|(?:show\s+)?(?:my\s+)?late\s+payers"
                     r"|what(?:'s|\s+is)\s+my\s+(?:real|effective|actual)\s+(?:hourly\s+)?rate|my\s+(?:real|effective)\s+hourly\s+rate"
                     r"|am\s+i\s+on\s+(?:track|pace)\s+(?:for|to\s+(?:hit|reach|make))\s+my\s+(?:monthly\s+)?(?:income\s+)?goal"
                     r"|how\s+much\s+is\s+my\s+pipeline\s+worth|what(?:'s|\s+is)\s+my\s+pipeline\s+worth"
                     r"|where\s+(?:is|does)\s+my\s+money\s+(?:come\s+from|going)"
                     r"|what\s+should\s+i\s+do\s+(?:first\s+)?to\s+(?:earn|make)\s+more(?:\s+money)?\s+(?:this\s+month|from\s+my\s+business)"
                     r"|(?:show\s+)?(?:my\s+)?earning\s+opportunities"
                     r")\s*[?.!]*$", re.IGNORECASE)


def deep_request(text: str) -> dict[str, Any] | None:
    value = " ".join(str(text or "").split())
    if value and len(value) <= 160 and _ROUTES.match(value):
        return {"tool": "business_deep_review", "arguments": {}}
    return None


def direct_plan(text: str, names: Any) -> dict[str, Any] | None:
    found = deep_request(text)
    if not found or found["tool"] not in names:
        return None
    return {"description": "Review the owner's own books in depth and rank what to do by the money at stake.",
            "tool": found["tool"], "arguments": {}, "expected": {"type": "tool_success"}, "max_retries": 0}


def report(history: list[dict[str, Any]]) -> str | None:
    for event in history or []:
        if event.get("tool") != "business_deep_review":
            continue
        result = event.get("result") or {}
        if result.get("success") and (event.get("verification") or {}).get("passed"):
            return "\n".join(report_lines(result.get("output") or {}))
    return None
