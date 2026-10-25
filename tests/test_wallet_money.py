"""Money in the HAYAT wallet: methods by nickname (secrets refused), money the owner added (never income), and pay-now
QR codes and links that send money straight to the owner. Nothing here can move money."""
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.safety.money_requests import money_request as money_guard
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger
from app.wallet_money import WalletMoney, money_request, qr_matrix, refuse_secrets, report_lines


@pytest.fixture
def books(tmp_path):
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "out", PermissionManager({"default": "ALLOW"}, tmp_path))
    ledger.set_profile(business_name="Mohsin Analytics", default_currency="INR")
    ledger.add_client("Acme", hourly_rate="2000", currency="INR")
    return ledger, WalletMoney(ledger)


@pytest.mark.parametrize("bad, words", [
    ("4242 4242 4242 4242", "full card number"), ("Visa 4111-1111-1111-1111", "full card number"),
    ("HDFC 50100234567890", "account or card numbers"), ("card cvv 123", "password, PIN, CVV"),
    ("my pin is 4321", "password, PIN, CVV"), ("visa exp 12/27", "expiry"), ("netbanking password hunter2", "password, PIN, CVV"),
])
def test_secrets_are_refused_before_anything_is_saved(books, bad, words):
    ledger, wallet = books
    with pytest.raises(ValueError, match=words):
        refuse_secrets(bad)
    with pytest.raises(ValueError):
        wallet.add_method("card", bad, "4242")
    assert wallet.methods()["methods"] == []


def test_methods_are_kept_by_nickname_and_last_four(books):
    _ledger, wallet = books
    bank = wallet.add_method("bank", "HDFC savings", "1234", "HDFC")
    assert bank["label"] == "HDFC savings ····1234" and "can't charge or move money" in bank["note"]
    upi = wallet.add_method("upi", "", receive="Mohsin@OKHDFCBANK")
    assert upi["receive"] == "mohsin@okhdfcbank" and upi["receives"]
    assert wallet.add_method("paypal", "PayPal", receive="https://paypal.me/MohsinKhan")["receive"] == "MohsinKhan"
    assert wallet.add_method("stripe", "Stripe", receive="https://buy.stripe.com/test_abc")["receive"].startswith("https://")
    for kind, receive, words in (("upi", "not-a-vpa", "name@bank"), ("stripe", "http://buy.stripe.com/x", "https://"),
                                 ("bank", "mohsin@okhdfcbank", "no receiving handle")):
        with pytest.raises(ValueError, match=words):
            wallet.add_method(kind, "x", receive=receive)
    with pytest.raises(ValueError, match="last 4"):
        wallet.add_method("card", "visa", "42424")
    assert wallet.find_method("my hdfc")["id"] == bank["id"] and wallet.find_method("1234")["id"] == bank["id"]
    wallet.remove_method(bank["id"])
    assert all(item["id"] != bank["id"] for item in wallet.methods()["methods"])
    with pytest.raises(ValueError, match="No money method"):
        wallet.method(bank["id"])


def test_added_money_fills_the_wallet_but_is_never_income(books):
    ledger, wallet = books
    wallet.add_method("bank", "HDFC savings", "1234", "HDFC")
    before = ledger.report("this month")
    result = wallet.add_money("5000", "hdfc", "starting float", reference="HDFC-TXN-9")
    assert result["added"] and result["from"] == "HDFC savings ····1234" and "HAYAT moved nothing" in result["note"]
    balance = ledger.wallet(activity=10)
    row = next(item for item in balance["balances"] if item["currency"] == "INR")
    assert row["added"] == "5,000.00 INR" and row["available"] == "5,000.00 INR" and row["earned"] == "0.00 INR"
    assert balance["activity"][0]["kind"] == "Money you added" and "HDFC savings" in balance["activity"][0]["description"]
    after = ledger.report("this month")
    assert after["by_currency"] == before["by_currency"]  # not income, not profit
    from app.business_deep import review
    assert review(ledger)["income_shares"] == []
    assert wallet.add_money("5000", "hdfc", reference="HDFC-TXN-9")["duplicate"]
    with pytest.raises(ValueError, match="future"):
        wallet.add_money("10", added_on=(date.today() + timedelta(days=2)).isoformat())
    with pytest.raises(ValueError, match="No money method matches"):
        wallet.add_money("10", "kotak")
    with pytest.raises(ValueError, match="CVV"):
        wallet.add_money("10", note="cvv 123")


def test_an_invoice_gets_a_upi_qr_and_links_that_pay_the_owner_directly(books):
    ledger, wallet = books
    with pytest.raises(ValueError, match="Add a way to be paid first"):
        number = ledger.create_invoice("Acme", [{"description": "Dashboard", "quantity": "1", "unit_price": "12000"}])["number"]
        wallet.payment_request(number=number)
    wallet.add_method("upi", "UPI", receive="mohsin@okhdfcbank")
    wallet.add_method("paypal", "PayPal", receive="mohsinkhan")
    number = ledger.create_invoice("Acme", [{"description": "Report", "quantity": "1", "unit_price": "1200"}])["number"]
    request = wallet.payment_request(number=number)
    upi = next(option for option in request["options"] if option["kind"] == "upi")
    query = parse_qs(urlparse(upi["link"]).query)
    assert upi["link"].startswith("upi://pay?") and query["pa"] == ["mohsin@okhdfcbank"] and query["am"] == ["1200.00"]
    assert query["cu"] == ["INR"] and query["tn"] == [number] and query["pn"] == ["Mohsin Analytics"]
    paypal = next(option for option in request["options"] if option["kind"] == "paypal")
    assert paypal["link"] == "https://paypal.me/mohsinkhan/1200.00INR"
    assert Path(request["qr_picture"]).is_file() and "HAYAT never touches it" in request["note"]
    assert len(qr_matrix(upi["link"])) >= 21
    # The invoice PDF itself carries the pay-now block.
    from pypdf import PdfReader
    pdf = ledger.document(number)["path"]
    text = "".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
    assert "How to pay" in text and "UPI to mohsin@okhdfcbank" in text and "paypal.me/mohsinkhan" in text
    lines = "\n".join(report_lines(request))
    assert lines.startswith(f"Pay-now for {number}") and "QR picture:" in lines
    ledger.record_payment(number)
    with pytest.raises(ValueError, match="already paid"):
        wallet.payment_request(number=number)
    loose = wallet.payment_request(amount="1500", note="Workshop seat")
    assert parse_qs(urlparse(loose["options"][0]["link"]).query)["am"] == ["1500.00"]


@pytest.mark.parametrize("text, tool, arguments", [
    ("add 5000 to my wallet from hdfc savings", "wallet_add_money", {"amount": "5000.00", "method": "hdfc savings"}),
    ("load ₹2,000 into my hayat wallet using my visa", "wallet_add_money", {"amount": "2000.00", "currency": "INR", "method": "my visa"}),
    ("add my hdfc savings account ending 1234", "money_method_add",
     {"kind": "bank", "nickname": "hdfc savings account", "institution": "hdfc", "last4": "1234"}),
    ("add my sbi debit card ending 9876 called salary card", "money_method_add",
     {"kind": "card", "nickname": "salary card", "institution": "sbi", "last4": "9876"}),
    ("my upi id is mohsin@okhdfcbank", "money_method_add", {"kind": "upi", "nickname": "UPI", "receive": "mohsin@okhdfcbank"}),
    ("show my money methods", "money_methods", {}),
    ("create a payment qr for INV-2026-0003", "payment_request", {"number": "INV-2026-0003"}),
    ("generate a upi qr for 1500 rupees", "payment_request", {"amount": "1500.00", "currency": "INR"}),
])
def test_wallet_phrases_route_and_real_money_moves_are_still_stopped(text, tool, arguments):
    assert money_request(text) == {"tool": tool, "arguments": arguments}
    assert not money_guard(text)


def test_moving_money_is_still_stopped_by_the_guard():
    for text in ("make a payment of 500 to ali", "pay my electricity bill", "transfer 500 rupees to my friend",
                 "send 1000 dollars to ali on paypal"):
        assert money_request(text) is None and money_guard(text), text


def test_the_wallet_windows(shared_tk, books):
    from types import SimpleNamespace

    from app.gui_theme import Palette
    from app.gui_wallet_money import MoneyMethodsWindow, PayNowWindow
    ledger, wallet = books
    wallet.add_method("upi", "UPI", receive="mohsin@okhdfcbank")
    number = ledger.create_invoice("Acme", [{"description": "Report", "quantity": "1", "unit_price": "900"}])["number"]

    class Studio(Palette):
        pass
    studio = Studio()
    root_window = __import__("tkinter").Toplevel(shared_tk)
    studio.ledger, studio.refresh, studio.status_var = ledger, lambda: None, SimpleNamespace(set=lambda text: None)
    root_window.palette = studio
    root_window.ledger, root_window.refresh = ledger, lambda: None
    try:
        window = MoneyMethodsWindow(root_window)
        window.fields["kind"].set("card")
        window.fields["nickname"].set("4111 1111 1111 1111")
        window.save()
        assert "full card number" in window.message.get() and len(window.tree.get_children()) == 1
        window.fields["nickname"].set("Salary card")
        window.fields["last4"].set("9876")
        window.save()
        assert len(window.tree.get_children()) == 2
        window.destroy()
        pay = PayNowWindow(root_window, number)
        shared_tk.update()
        assert pay.qr_canvas.find_all() and "900.00" in pay.value["amount"]
        pay.copy(pay.value["options"][0]["link"])
        assert shared_tk.clipboard_get().startswith("upi://pay?")
        pay.destroy()
        time.sleep(.05)
    finally:
        root_window.destroy()
