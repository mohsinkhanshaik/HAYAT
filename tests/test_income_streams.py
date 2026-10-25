"""Several income streams from real records, a daily brief that only reads, and money facts that are researched."""
from datetime import date, timedelta

import pytest

from app.agent.planner import Planner
from app.intent.models import IntentKind
from app.intent.router import IntentRouter
from app.observed_reply import render
from app.safety.permissions import PermissionManager
from app.tools.brief import BusinessBrief, register_brief_tools
from app.tools.business import BusinessLedger, register_business_tools
from app.tools.launch import Launchpad
from app.tools.products import ProductStore
from app.tools.registry import ToolRegistry


@pytest.fixture
def books(tmp_path):
    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    products = ProductStore(tmp_path / "p.db", tmp_path / "bundles", permissions)
    ledger = BusinessLedger(tmp_path / "b.db", tmp_path / "out", permissions, products=products)
    registry = ToolRegistry(permissions)
    register_business_tools(registry, ledger)
    return ledger, products, registry


def test_streams_cover_clients_products_and_other_sources_from_records_only(books):
    ledger, products, _ = books
    assert ledger.income_streams()["streams"] == []
    assert "No income recorded" in ledger.income_streams()["notes"][0]
    ledger.add_client("Acme")
    invoice = ledger.create_invoice("Acme", [{"description": "Logo", "unit_price": "300"}])
    ledger.record_payment(invoice["number"])
    ledger.record_income("50", "Fiverr", "Gig")
    ledger.record_income("30", "fiverr", "Second gig")  # the same source, however it is spelled
    product = products.create("Planner", "x", "x", "10", "Personal use")
    products.record_sale(product["id"], "o1", "10", "1")
    result = ledger.income_streams()
    rows = {(s["kind"], s["source"]): (s["amount"], s["share"]) for s in result["streams"]}
    assert rows == {("Client work", "Acme"): ("300.00 USD", 77.1), ("Other income", "Fiverr"): ("80.00 USD", 20.6),
                    ("Digital products", "Planner"): ("9.00 USD", 2.3)}
    assert result["totals"] == {"USD": "389.00 USD"} and result["stream_count"] == 3
    assert "less dependent" in result["notes"][0] and "estimated" in result["basis"]


def test_streams_compare_with_last_month(books):
    ledger, _, _ = books
    last_month = (date.today().replace(day=1) - timedelta(days=1)).isoformat()
    ledger.record_income("100", "Upwork", "Old", received_on=last_month)
    ledger.record_income("150", "Upwork", "New")
    ledger.record_income("20", "Etsy", "First sale")
    streams = {s["source"]: s for s in ledger.income_streams("this month")["streams"]}
    assert streams["Upwork"]["change"] == "50.00 USD" and not streams["Upwork"]["new"]
    assert streams["Etsy"]["new"] and streams["Etsy"]["change"] is None


def test_the_brief_only_reads_and_shows_once_a_day(books):
    ledger, products, registry = books
    launchpad = Launchpad(ledger, products)
    brief = BusinessBrief(ledger, launchpad)
    register_brief_tools(registry, brief)
    assert not brief.due_today()  # nothing needs attention yet
    ledger.add_client("Acme")
    invoice = ledger.create_invoice("Acme", [{"description": "Logo", "unit_price": "300"}], due_days=0)
    with ledger._db() as db:
        db.execute("UPDATE documents SET due=? WHERE number=?", ((date.today() - timedelta(days=2)).isoformat(),
                                                                 invoice["number"]))
    launchpad.start("Studio", "Logos")
    result = brief.brief()
    assert any("overdue" in item for item in result["needs_attention"])
    assert any("Studio launch" in item for item in result["needs_attention"])
    assert brief.due_today()
    brief.mark_shown()
    assert not brief.due_today()
    text = registry.execute("business_brief", {}).output["text"]
    assert text.startswith("Business brief for") and "Needs your attention:" in text
    before = ledger.invoices("all")
    brief.brief()
    assert ledger.invoices("all") == before  # reading the brief changes nothing


@pytest.mark.parametrize("text", [
    "how much can a translator earn?", "how much do logo designers charge", "what do freelance writers charge",
    "what is the average rate for social media managers", "is there demand for dog walking in my area",
    "is selling planners on etsy profitable?", "how much should I charge for a logo",
])
def test_money_facts_are_researched_with_sources_not_guessed(text):
    intent = IntentRouter().route(text)
    assert intent.intent is IntentKind.RESEARCH and intent.entities.get("money_fact")


@pytest.mark.parametrize("text", ["how much did I earn this month?", "how much do I have", "how much can I withdraw?"])
def test_questions_about_my_own_books_stay_with_the_books(text):
    assert IntentRouter().route(text).intent is not IntentKind.RESEARCH


@pytest.mark.parametrize("goal, tool, arguments", [
    ("show my income streams", "income_streams", {}),
    ("where does my money come from", "income_streams", {}),
    ("show my income streams for last month", "income_streams", {"period": "last month"}),
    ("show my business brief", "business_brief", {}),
    ("today's brief", "business_brief", {}),
])
def test_stream_and_brief_requests_plan_directly(books, goal, tool, arguments):
    ledger, products, registry = books
    register_brief_tools(registry, BusinessBrief(ledger, Launchpad(ledger, products)))
    assert IntentRouter().route(goal).requires_tools
    plan = Planner(registry).create_plan(goal)
    assert (plan[0].tool, plan[0].arguments) == (tool, arguments)


def test_streams_and_brief_read_as_plain_replies(books):
    ledger, _, _ = books
    ledger.record_income("80", "Fiverr", "Gig")
    text = render(ledger.income_streams())
    assert "Fiverr (other income): 80.00 USD, 100%" in text and "Nothing here is estimated" in text
    brief = BusinessBrief(ledger)
    assert render({**brief.brief(), "text": brief.text(brief.brief())}).startswith("Business brief for")


def test_the_model_is_told_not_to_guess_money_facts():
    from app.agent.conversation import _capability_primer

    primer = _capability_primer()
    assert "Never state earnings, rates, prices" in primer and "Never promise income" in primer


def test_the_streams_tab_and_brief_window(shared_tk, tmp_path):
    from types import SimpleNamespace

    from app.gui_business import BusinessStudio
    from app.gui_theme import Palette

    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    ledger = BusinessLedger(tmp_path / "b.db", tmp_path / "out", permissions)
    ledger.record_income("80", "Fiverr", "Gig")

    class GUI(Palette):
        root = shared_tk
        runtime = SimpleNamespace(business=ledger, brief=BusinessBrief(ledger))

    studio = BusinessStudio(GUI())
    try:
        studio.withdraw()
        studio.show_tab("Income streams")
        assert "80.00 USD from 1 source" in studio.streams_var.get()
        row = studio.streams_tree.get_children()[0]
        assert studio.streams_tree.item(row, "text") == "Fiverr"
        window = studio.show_brief()
        assert window.winfo_exists()
        window.destroy()
    finally:
        studio.destroy()
