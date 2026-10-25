"""The HAYAT Wallet desk: HAYAT proposes what to do with the wallet's money, and only the owner approves. Inside the
wallet an approval acts; outside it, the owner pays and HAYAT records it. Nothing here can pay or buy."""
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.agent.planner import Planner
from app.execution import execution_scope
from app.intent.router import IntentRouter
from app.money_desk import TOOL_NAMES, MoneyDesk, _advance, desk_request, owner_word, register_money_desk_tools, report_lines
from app.safety.money_requests import money_request as money_guard
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.tools.registry import ToolRegistry

TODAY = date.today()


def soon(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture
def desk(tmp_path):
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    ledger.set_profile(business_name="Studio", default_currency="USD")
    ledger.add_client("Acme", hourly_rate="50")
    number = ledger.create_invoice("Acme", [{"description": "Dashboard", "quantity": "1", "unit_price": "4000"}])["number"]
    ledger.record_payment(number)
    ledger.record_expense("600", "software", "Tools")
    return MoneyDesk(ledger)


def pay(desk, amount):
    number = desk.ledger.create_invoice("Acme", [{"description": "Work", "quantity": "1", "unit_price": amount}])["number"]
    desk.ledger.record_payment(number)
    return number


def waiting(desk, kind=None):
    return [item for item in desk.decisions("proposed")["decisions"] if kind in (None, item["kind"])]


def test_pots_hold_parts_of_the_wallet(desk):
    assert desk.unallocated() == 340000
    desk.create_pot("Tax", "2000")
    desk.create_pot("Emergency fund", "3000")
    moved = desk.move("500", to_pot="tax")
    assert moved["to"] == "Tax" and moved["from"] == "unallocated" and desk.unallocated() == 290000
    with pytest.raises(ValueError, match="Only 2,900.00 USD is unallocated"):
        desk.move("5000", to_pot="tax")
    desk.move("200", to_pot="emergency", from_pot="tax")
    pots = {pot["name"]: pot for pot in desk.overview()["pots"]}
    assert pots["Tax"]["balance_cents"] == 30000 and pots["Emergency fund"]["balance_cents"] == 20000
    assert pots["Emergency fund"]["purpose"] == "emergency" and pots["Tax"]["purpose"] == "tax"
    with pytest.raises(ValueError, match="still holds"):
        desk.remove_pot("tax")
    desk.move("all", from_pot="tax")
    assert desk.remove_pot("tax")["pot_removed"] and desk.unallocated() == 320000
    with pytest.raises(ValueError, match="already have a pot"):
        desk.create_pot("emergency fund")
    with pytest.raises(ValueError, match="No pot called"):
        desk.move("10", to_pot="holiday")


def test_hayat_proposes_and_only_the_owner_approves(desk):
    desk.create_pot("Tax", "1000")
    desk.create_pot("Emergency fund", "1800")
    desk.add_bill("Rent", "1200", soon(20))  # no pot: kept free, not shared out
    thought = desk.think()
    fill = waiting(desk, "allocate")[0]
    assert fill["title"] == "Put 2,200.00 USD to work in your pots"
    assert [split["cents"] for split in fill["payload"]["splits"]] == [180000, 40000]  # emergency fund before tax
    assert any("stays free for bills due in 30 days" in reason for reason in fill["reasons"])
    assert "Nothing changes until you approve" in thought["note"]
    with pytest.raises(PermissionError):
        desk.approve(fill["id"], by_owner=False)
    assert not set(TOOL_NAMES) & {"wallet_approve", "wallet_decline", "wallet_paid"}  # no tool can approve
    done = desk.approve(fill["id"], by_owner=True)
    assert done["status"] == "done" and desk.unallocated() == 120000
    with pytest.raises(ValueError, match="only be approved once"):
        desk.approve(fill["id"], by_owner=True)
    desk.think()
    assert not waiting(desk, "allocate")  # the gap left is under the bill buffer
    lines = "\n".join(report_lines(desk.think()))
    assert "Nothing needs a decision" in lines or "Decision" in lines


def test_a_declined_proposal_is_not_proposed_again(desk):
    desk.create_pot("Savings", "500")
    desk.think()
    proposal = waiting(desk, "allocate")[0]
    assert desk.decline(proposal["id"], by_owner=True)["declined"]
    desk.think()
    assert not waiting(desk, "allocate")
    pay(desk, "300")  # new money: a new proposal
    desk.think()
    assert waiting(desk, "allocate")


def test_set_aside_rules_apply_only_to_new_income(desk):
    desk.create_pot("Tax")
    desk.add_rule("tax", "25", auto=True)
    desk.think()
    assert desk.overview()["pots"][0]["balance_cents"] == 0  # the payment before the rule is left alone
    pay(desk, "1000")
    thought = desk.think()
    assert thought["applied"] and "250.00 USD" in thought["applied"][0]
    assert desk.overview()["pots"][0]["balance_cents"] == 25000
    desk.think()
    assert desk.overview()["pots"][0]["balance_cents"] == 25000  # once per payment
    desk.create_pot("Savings")
    desk.add_rule("savings", "10")
    pay(desk, "500")
    desk.think()
    share = waiting(desk, "rule_share")
    assert len(share) == 1 and share[0]["amount_cents"] == 5000
    desk.approve(share[0]["id"], by_owner=True)
    desk.think()
    assert not waiting(desk, "rule_share")
    with pytest.raises(ValueError, match="already set aside"):
        desk.add_rule("savings", "70")


def test_bills_are_held_then_paid_by_the_owner_and_recorded(desk):
    desk.create_pot("Rent", "1500")
    desk.add_bill("Rent", "1200", soon(4), "month", pot="rent", pay_url="https://landlord.example/pay")
    desk.add_bill("Adobe", "55", soon(2), "month", business=True, category="software")
    desk.think()
    topup = next(item for item in waiting(desk, "allocate") if "Top up Rent" in item["title"])
    assert topup["urgency"] == "high" and topup["amount_cents"] == 120000
    rent_pot = topup["payload"]["splits"][0]["pot_id"]
    into_rent = sum(split["cents"] for item in waiting(desk, "allocate") for split in item["payload"]["splits"] if split["pot_id"] == rent_pot)
    assert into_rent == 150000  # the top-up and the share-out together fill the 1,500 target, never more
    rent = next(item for item in waiting(desk, "pay_bill") if item["payload"]["what"] == "Rent")
    with pytest.raises(ValueError, match="move money into it first"):
        desk.approve(rent["id"], by_owner=True)
    desk.approve(topup["id"], by_owner=True)
    approved = desk.approve(rent["id"], by_owner=True)
    assert approved["status"] == "approved" and approved["open"] == "https://landlord.example/pay" and "decision" in approved["next"]
    assert desk.overview()["pots"][0]["free_cents"] == 0  # held for the bill
    available = desk.ledger._balances()["USD"]["available"]
    paid = desk.paid(rent["id"], by_owner=True, reference="TX-1")
    assert "personal" in paid["recorded"] and "did not see" in paid["source"]
    assert desk.ledger._balances()["USD"]["available"] == available - 120000
    bill = next(row for row in desk.overview()["bills"] if row["name"] == "Rent")
    assert bill["next_due"] == _advance(date.fromisoformat(soon(4)), "month").isoformat()
    assert desk.overview()["pots"][0]["balance_cents"] == 0
    adobe = next(item for item in waiting(desk, "pay_bill") if item["payload"]["what"] == "Adobe")
    desk.approve(adobe["id"], by_owner=True)
    desk.paid(adobe["id"], by_owner=True)
    assert any(row["category"] == "software" and row["description"] == "Adobe" for row in desk.ledger.expenses()["expenses"])
    with pytest.raises(ValueError, match="only an approved one"):
        desk.paid(adobe["id"], by_owner=True)
    desk.think()
    assert not [item for item in waiting(desk, "pay_bill") if item["payload"]["what"] == "Rent"]  # next one is a month away


def test_spend_requests_get_hayats_verdict(desk):
    desk.create_pot("Gear", "1000")
    desk.move("400", to_pot="gear")
    fits = desk.request_spend("300", "a laptop", pot="gear")
    assert fits["verdict"] == "Yes, it fits" and "Gear has 400.00 USD free." in fits["reasons"]
    short = desk.request_spend("900", "a camera", pot="gear")
    assert short["verdict"] == "Not enough" and any("Short by 500.00 USD" in reason for reason in short["reasons"])
    desk.create_pot("Emergency fund", "20000")
    wait = desk.request_spend("100", "a game")
    assert wait["verdict"] == "Better to wait"
    desk.approve(fits["decision_id"], by_owner=True)
    assert desk.overview()["pots"][0]["free_cents"] == 10000
    desk.paid(fits["decision_id"], by_owner=True)
    assert desk.overview()["pots"][0]["balance_cents"] == 10000
    with pytest.raises(ValueError, match="https"):
        desk.request_spend("10", "x", url="http://shop.example")
    assert "HAYAT says Yes, it fits" in "\n".join(report_lines(fits)).replace(":", "")


def test_hayat_suggests_an_emergency_fund_and_a_tax_pot(desk):
    desk.think()
    created = {item["payload"]["purpose"]: item for item in waiting(desk, "create_pot")}
    assert set(created) == {"emergency", "tax"}
    assert created["emergency"]["amount_cents"] == 60000  # 3 x (600 spent over 90 days / 3)
    desk.approve(created["tax"]["id"], by_owner=True)
    overview = desk.overview()
    assert overview["pots"][0]["name"] == "Tax" and overview["rules"][0]["percent"] == "25" and not overview["rules"][0]["auto"]
    desk.decline(created["emergency"]["id"], by_owner=True)
    desk.think()
    assert not waiting(desk, "create_pot")


def test_a_drifted_portfolio_gets_a_rebalance_plan(desk):
    desk.portfolio = SimpleNamespace(view=lambda: {"net_worth_usd": 10000, "by_kind": {"cash": 1000, "stock": 3000, "crypto": 6000}})
    with pytest.raises(ValueError, match="add up to 100"):
        desk.set_targets("20", "60", "30")
    desk.set_targets("20", "60", "20")
    desk.think()
    plan = waiting(desk, "rebalance")[0]
    assert {trade["kind"]: trade["usd"] for trade in plan["payload"]["trades"]} == {"cash": 1000.0, "stock": 3000.0, "crypto": -4000.0}
    assert "Sell about $4,000.00 of crypto" in plan["reasons"]
    assert desk.approve(plan["id"], by_owner=True)["status"] == "approved"
    assert desk.paid(plan["id"], by_owner=True)["recorded"] == "Marked done."


def test_the_owner_word_and_a_remote_device(desk):
    desk.create_pot("Savings", "500")
    desk.think()
    proposal = waiting(desk, "allocate")[0]
    from app.assistant import AssistantService
    service = SimpleNamespace(runtime=SimpleNamespace(money_desk=desk))
    with execution_scope(principal="REMOTE_OWNER"):
        assert "not from a remote device" in AssistantService._owner_money_decision(service, f"approve decision {proposal['id']}")
    assert waiting(desk, "allocate")
    reply = AssistantService._owner_money_decision(service, f"approve decision {proposal['id']}")
    assert reply.startswith("Approved and done")
    assert "No decision 999" in owner_word(desk, "decline decision 999")
    intent = IntentRouter().route(f"approve decision {proposal['id']}")
    assert intent.control == "MONEY_DECISION"


@pytest.mark.parametrize("text, tool, arguments", [
    ("what should i do with my money", "wallet_think", {}),
    ("show my pots", "wallet_desk", {}),
    ("create a pot called Tax with target 2000", "wallet_pot_create", {"name": "Tax", "target": "2000"}),
    ("move 300 to tax", "wallet_pot_move", {"amount": "300", "to_pot": "tax"}),
    ("move 200 from savings to tax", "wallet_pot_move", {"amount": "200", "to_pot": "tax", "from_pot": "savings"}),
    ("set aside 25% of income for tax automatically", "wallet_rule_add", {"pot": "tax", "percent": "25", "auto": True}),
    ("add bill rent 1200 monthly due 2026-11-01", "wallet_bill_add",
     {"name": "rent", "amount": "1200", "next_due": "2026-11-01", "every": "month"}),
    ("can i afford a 300 dollar laptop", "wallet_spend_request", {"amount": "300", "what": "laptop", "currency": "USD"}),
    ("i want to spend 20 on a domain from marketing", "wallet_spend_request", {"amount": "20", "what": "domain", "pot": "marketing"}),
])
def test_wallet_phrases_route_and_are_not_real_payments(tmp_path, desk, text, tool, arguments):
    assert desk_request(text) == {"tool": tool, "arguments": arguments}
    assert not money_guard(text)
    assert IntentRouter().route(text).entities.get("modality") == "money_desk"
    registry = ToolRegistry(PermissionManager({"default": "ALLOW"}, tmp_path))
    register_money_desk_tools(registry, desk)
    step = Planner(registry).create_plan(text)[0]
    assert (step.tool, step.arguments) == (tool, arguments)


def test_the_daily_brief_lists_waiting_decisions_without_making_any(desk):
    from app.tools.brief import BusinessBrief
    brief = BusinessBrief(desk.ledger, desk=desk)
    assert not any("Money decision" in item for item in brief.brief()["all_items"])
    desk.create_pot("Savings", "500")
    assert not desk.decisions("proposed")["decisions"]  # reading the brief proposed nothing
    desk.think()
    proposal = waiting(desk, "allocate")[0]
    assert any(item.startswith(f"Money decision {proposal['id']} waits for you") for item in brief.brief()["needs_attention"])


def test_money_leaving_the_wallet_is_not_a_pot_move():
    for text in ("move 500 to my bank account", "move 100 to my friend", "pay my rent", "buy a laptop for me"):
        assert desk_request(text) is None, text


def test_the_wallet_window(shared_tk, desk):
    from app.gui_money_desk import MoneyDeskWindow
    from app.gui_theme import Palette
    desk.create_pot("Tax", "1000")
    desk.add_bill("Internet", "60", soon(3), pay_url="https://isp.example/pay")

    class Gui(Palette):
        pass
    gui = Gui()
    gui.root, gui.runtime = shared_tk, SimpleNamespace(money_desk=desk)
    window = MoneyDeskWindow(gui)

    def until(check):
        for _ in range(80):
            shared_tk.update()
            if check():
                return True
            time.sleep(.05)
        return False
    try:
        assert until(lambda: window.value is not None)
        assert window.card_values["available"].cget("text") == "3,400.00 USD" and window.card_values["waiting"].cget("text") != "0"
        bill = next(item for item in window.value["waiting"] if item["kind"] == "pay_bill")
        window.decision_buttons[(bill["id"], "approve")].invoke()
        assert until(lambda: (bill["id"], "paid") in window.decision_buttons)
        assert "Approved" in window.message.get()
        window.decision_buttons[(bill["id"], "paid")].invoke()
        assert until(lambda: "as paid" in window.message.get())
        window.pot_form["name"].set("Gear")
        window.pot_form["target"].set("500")
        window.create_pot()
        assert until(lambda: len(window.pots.get_children()) == 2)
        window.move_form["amount"].set("100")
        window.move_form["target"].set(next(name for name in window.move_form["target_widget"].cget("values") if name.endswith("Gear")))
        window.move()
        assert until(lambda: "Moved 100.00 USD" in window.message.get())
    finally:
        window.close()
