"""The deep business review: cash ahead by each client's habit, real hourly rate, dependence, pipeline at the
owner's own win rate, goal pacing, spending, the market from collected jobs, and actions ranked by money."""
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.business_deep import report_lines, review
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger

TODAY = date(2026, 10, 13)


def day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def books(tmp_path):
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    ledger.add_client("Acme", hourly_rate="50", currency="USD")
    ledger.add_client("Beta", hourly_rate="40", currency="USD")

    def invoice(client, amount, issued, due, paid_on=None, hours=None, rate=None):
        if hours:
            ledger.log_time(client, str(hours), "work", work_date=issued)
            number = ledger.create_invoice(client, include_unbilled_time=True)["number"]
        else:
            number = ledger.create_invoice(client, [{"description": "work", "quantity": "1", "unit_price": str(amount)}])["number"]
        with sqlite3.connect(ledger.path) as db:
            db.execute("UPDATE documents SET issued=?, due=? WHERE number=?", (issued, due, number))
        if paid_on:
            ledger.record_payment(number, paid_on=paid_on)
        return number

    # Acme pays about 12 days late, on 10-hour invoices that it pays in full ($500 for 10 h = $50 an hour).
    invoice("Acme", None, day(-150), day(-136), day(-124), hours=10)
    invoice("Acme", None, day(-100), day(-86), day(-74), hours=10)
    invoice("Acme", None, day(-60), day(-46), day(-34), hours=10)
    # Beta pays on time, but two of its 10-hour invoices were billed at a discount: $300 for 10 h = $30 an hour.
    number = invoice("Beta", 300, day(-90), day(-76), day(-76))
    ledger.log_time("Beta", "10", "audit", work_date=day(-95))
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE time_entries SET invoice_number=? WHERE invoice_number IS NULL AND client_id=(SELECT id FROM clients WHERE name='Beta')",
                   (number,))
    # Unpaid: Acme due in 5 days (expected 12 days after), Beta overdue by 20 days, and an old Acme one 70 days overdue.
    invoice("Acme", 800, day(-9), day(5))
    invoice("Beta", 400, day(-34), day(-20))
    invoice("Acme", 250, day(-84), day(-70))
    # Unbilled: 6 Beta hours from 21 days ago.
    ledger.log_time("Beta", "6", "reports", work_date=day(-21))
    # Spending: software $100 a month before, $400 this month.
    for offset in (-75, -45, -15):
        ledger.record_expense("100", "software", "tools", spent_on=day(offset))
    ledger.record_expense("400", "software", "new licence", spent_on=day(-2))
    # Leads: 5 closed (2 won), 2 open; one untouched for 20 days.
    for index, status in enumerate(("won", "won", "lost", "lost", "lost")):
        lead = ledger.add_lead(f"closed {index}", "upwork", value="1000")
        ledger.update_lead(lead["lead_id"], status=status)
    fresh = ledger.add_lead("New site", "upwork", value="1500")
    stale = ledger.add_lead("Audit retainer", "referral", value="900")
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE leads SET updated=? WHERE id=?", (day(-20) + "T09:00:00", stale["lead_id"]))
        db.execute("UPDATE leads SET updated=? WHERE id=?", (day(-1) + "T09:00:00", fresh["lead_id"]))
    ledger.set_goal("3000", "USD")
    return ledger


def test_cash_ahead_follows_each_clients_own_habit(books):
    value = review(books, today=TODAY)
    assert value["currency"] == "USD"
    invoices = {row["client"] + str(row["balance_cents"]): row for row in value["cash"]["invoices"]}
    assert invoices["Acme80000"]["expected"] == day(5 + 12)  # due in 5 days, Acme pays 12 days late
    assert invoices["Beta40000"]["overdue_days"] == 20
    buckets = {item["when"]: item["cents"] for item in value["cash"]["buckets"]}
    assert buckets["next 30 days"] == 80000 and buckets["at risk (over 60 days overdue)"] == 25000
    assert buckets["overdue, still expected"] == 40000
    assert value["cash"]["monthly_spend_cents"] == 10000
    habits = {row["client"]: row for row in value["collection"]}
    assert habits["Acme"]["median_days_late"] == 12 and habits["Acme"]["habit"] == "usually pays about 12 days late"
    assert habits["Beta"]["habit"] == "pays on time" and habits["Beta"]["overdue"] == "400.00 USD"


def test_the_real_hourly_rate_shows_discounted_time(books):
    value = review(books, today=TODAY)
    rates = {row["client"]: row for row in value["rates"]}
    assert rates["Acme"]["effective_cents"] == 5000 and rates["Acme"]["gap_pct"] == 0
    assert rates["Beta"]["effective_cents"] == 3000 and rates["Beta"]["gap_pct"] == -25
    assert value["effective_rate_cents"] == round((150000 + 30000) / 40)
    unbilled = value["unbilled"][0]
    assert (unbilled["client"], unbilled["hours"], unbilled["oldest_days"]) == ("Beta", 6, 21)


def test_dependence_pipeline_goal_and_spending(books):
    value = review(books, today=TODAY)
    shares = {row["client"]: row["share_pct"] for row in value["income_shares"]}
    assert shares["Acme"] == pytest.approx(83.3, abs=.1) and value["dependence"].startswith("Acme brought 83.3%")
    pipeline = value["pipeline"]
    assert pipeline["win_rate_pct"] == 40 and pipeline["by_source"] == {"upwork": 40}
    assert pipeline["value_cents"] == 240000 and pipeline["expected"] == "960.00 USD"  # 1500 × 40 % + 900 × 40 %
    assert [row["title"] for row in pipeline["stale"]] == ["Audit retainer"]
    goal = value["goal"]
    assert goal["earned"] == "0.00 USD" and goal["gap"] == "3,000.00 USD" and goal["days_left"] == 19
    # Acme's $800 (due in 5 days, paid ~12 days late: the 30th) and Beta's overdue $400 are expected this month;
    # the 70-days-overdue $250 is not counted on.
    assert goal["expected_this_month"] == "1,200.00 USD" and goal["after_expected_cents"] == 180000
    assert goal["in_hours"] == 40 and goal["in_invoices"] == 4 and goal["typical_invoice"] == "500.00 USD"
    rising = [row for row in value["categories"] if row["rising"]]
    assert rising and rising[0]["category"] == "software"


def test_actions_are_ranked_by_the_money_at_stake(books):
    value = review(books, today=TODAY)
    actions = value["actions"]
    assert actions[0].startswith("Chase") and "400.00 USD" in actions[0] and "20 days overdue" in actions[0]
    assert any(text.startswith("Invoice 6 unbilled hours for Beta") and "21 days old" in text for text in actions)
    assert any("Beta pays an effective 30.00 USD an hour against the agreed 40.00 USD" in text for text in actions)
    assert any(text.startswith("Follow up lead") and "Audit retainer" in text for text in actions)
    assert any(text.startswith("Acme brought 83.3%") for text in actions)
    text = "\n".join(report_lines(value))
    assert text.startswith("Deep business review (USD, 2026-10-13)") and "Do first, by the money at stake:" in text
    assert "not verified" in text


def test_an_empty_book_says_how_to_start(tmp_path):
    ledger = BusinessLedger(tmp_path / "b.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    value = review(ledger, today=TODAY)
    assert value["empty"] and "Your books are empty" in report_lines(value)[0]


def test_the_market_from_collected_jobs(books):
    rows = [{"number": 1, "title": "Freelance Data Analyst (contract)", "company": "Acme", "score": 80, "verdict": "strong",
             "source_name": "Remote OK", "employment_type": "contract", "description": "",
             "reading": {"salary": {"min": 60000, "max": 80000, "currency": "USD", "period": "year"},
                         "skills": {"required": ["SQL", "Python"]}}},
            {"number": 2, "title": "Data Analyst", "company": "Beta", "score": 70, "verdict": "good", "source_name": "Jobicy",
             "employment_type": "full-time", "description": "",
             "reading": {"salary": {"min": 80000, "max": 100000, "currency": "USD", "period": "year"}, "skills": {"required": ["SQL"]}}},
            {"number": 3, "title": "Analyst", "company": "Gamma", "score": 65, "verdict": "good", "source_name": "Himalayas",
             "employment_type": "", "description": "Part-time, 20 hours a week.",
             "reading": {"salary": {"min": 40, "max": 40, "currency": "USD", "period": "hour"}, "skills": {"required": ["Excel"]}}}]
    jobs = SimpleNamespace(store=SimpleNamespace(matches=lambda limit: rows),
                           profile=SimpleNamespace(load=lambda: {}, skills=lambda data: ["SQL", "Python"]))
    market = review(books, jobs, today=TODAY)["market"]
    assert market["pay"]["postings"] == 3 and market["pay"]["median"] == "40.00 USD"
    assert market["comparison"].startswith("Your real rate is +12%")  # $45 against $40
    assert [row["number"] for row in market["contract_jobs"]] == [1, 3]
    assert market["services"][0] == {"skill": "SQL", "postings": 2}
    assert "a floor, not a price" in market["note"]
