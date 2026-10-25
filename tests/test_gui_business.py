import time
from types import SimpleNamespace

import pytest
import tkinter as tk

from app.gui_business import BusinessStudio, summary_text
from app.gui_theme import Palette
from app.safety.permissions import PermissionManager
from app.tools.business import BusinessLedger


class FakeGUI(Palette):
    def __init__(self, root, ledger):
        self.root = root
        self.runtime = SimpleNamespace(business=ledger)


def test_acquisition_form_keeps_scope_and_requires_real_budget(studio, monkeypatch):
    requests = []
    monkeypatch.setattr(studio, 'ask_hayat', lambda request, note: requests.append(request))
    dialog = studio.acquisition_form()
    with pytest.raises(ValueError, match='country or region'):
        dialog.submit({'location': '', 'sector': 'laundromat', 'budget': '', 'currency': 'USD'})
    with pytest.raises(ValueError, match='positive and finite'):
        dialog.submit({'location': 'Ohio, US', 'sector': 'laundromat', 'budget': 'NaN', 'currency': 'USD'})
    dialog.submit({'location': 'Ohio, US', 'sector': 'laundromat', 'budget': '250,000', 'currency': 'USD'})
    assert 'Ohio, US' in requests[0] and 'USD 250000' in requests[0] and 'valuation' in requests[0]


def pump(window, predicate, timeout=3):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        window.update()
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def studio(shared_tk, tmp_path):
    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "business", permissions)
    ledger.add_client("Acme Ltd", hourly_rate="40")
    ledger.create_invoice("Acme Ltd", [{"description": "Work", "unit_price": "100"}])
    ledger.add_lead("Theme gig", "Upwork", value="500")
    ledger.record_expense("10", "software", "Domain")
    window = BusinessStudio(FakeGUI(shared_tk, ledger))
    window.withdraw()
    pump(window, lambda: bool(window.invoices.get_children()))
    yield window
    window.destroy()


def test_window_lists_books_and_marks_an_invoice_paid(studio):
    number = studio.invoices.get_children()[0]
    assert studio.invoices.item(number, "values")[4] == "100.00 USD"
    assert len(studio.clients.get_children()) == 1 and len(studio.leads.get_children()) == 1
    assert len(studio.expenses.get_children()) == 1
    studio.invoices.selection_set(number)
    studio.mark_paid()
    assert studio.invoices.item(number, "values")[4] == "0.00 USD"
    assert "Earned 100.00 USD" in studio.summary_var.get()


def test_actions_without_a_selection_explain_themselves(studio):
    studio.invoices.selection_remove(*studio.invoices.selection())
    studio.mark_paid()
    assert "Select an invoice" in studio.status_var.get()


def test_export_reports_where_the_file_went(studio):
    studio.export()
    assert ".xlsx" in studio.status_var.get()


def test_summary_text_for_empty_books():
    assert summary_text({"period": "October 2026", "by_currency": []}) == "October 2026: nothing recorded yet."


def test_pilot_form_uses_current_draft_and_keeps_frozen_comparison(studio, request):
    from tests.test_reference_upgrade import assumptions
    from tests.test_business_pilot import observations
    tab = studio.model_tab
    import os
    if os.getenv('HAYAT_SHOTS') == '1':
        from tkinter import ttk
        from app.gui_theme import style_controls, style_hayat_buttons
        style = ttk.Style(studio)
        previous = style.theme_use()
        style.theme_use('clam')
        style_controls(studio, studio.palette)
        style_hayat_buttons(studio, studio.palette)
        request.addfinalizer(lambda: style.theme_use(previous))
        studio.gui.root.deiconify()
        request.addfinalizer(studio.gui.root.withdraw)
        studio.geometry('1000x760+20+20')
        studio.deiconify()
        studio.show_tab('Business model')
        studio.update()
    for key, value in assumptions().items():
        tab.variables[key].set(value)
    dialog = tab.pilot_form()
    assert dialog
    if os.getenv('HAYAT_SHOTS') == '1':
        from tests.test_gui_screenshots import _grab, _pump
        dialog.geometry('+30+30')
        dialog.deiconify()
        for key, value in observations().items():
            dialog.values[key].set(value)
        _pump(SimpleNamespace(root=dialog), .2)
        _grab(studio.gui, '93-business-pilot-observations', widget=dialog)
    tab.variables['price'].set('900')
    before = studio.ledger.report('all')
    dialog.submit(observations())
    dialog.destroy()
    assert studio.ledger.business_pilot()['assumptions']['price'] == '500'
    assert '1,300.00 USD' in tab.pilot_detail.get()
    assert studio.ledger.report('all') == before
    assert tab.variables['price'].get() == '900'


def test_business_model_editor_calculates_saves_and_preserves_unsaved_draft(studio):
    tab = studio.model_tab
    studio.show_tab("Business model")
    tab.variables["price"].set("500")
    tab.variables["capacity_hours"].set("40")
    tab.variables["hours_per_sale"].set("4")
    tab.variables["monthly_leads"].set("100")
    tab.variables["conversion_percent"].set("20")
    assert tab.save()
    assert len(tab.results.get_children()) == 3
    assert len(tab.sensitivity.get_children()) == 11
    assert 'Maximum acquisition cost' in tab.thresholds.get()
    tab.variables["price"].set("600")
    studio.refresh()
    assert tab.variables["price"].get() == "600"
    assert studio.ledger.business_model()["assumptions"]["price"] == "500"
    tab.variables["price"].set("nan")
    assert not tab.save()
    assert "finite" in tab.notice.get()


def test_business_model_compact_controls_and_scroll_reach_thresholds(studio, request):
    import os
    from tests.test_gui_screenshots import _grab, _pump
    from tests.test_reference_upgrade import assumptions
    from app.business_model import evaluate
    from app.gui_theme import style_controls, style_hayat_buttons
    from tkinter import ttk
    style = ttk.Style(studio)
    theme = style.theme_use()
    style.theme_use('clam')
    style_controls(studio, studio.palette)
    style_hayat_buttons(studio, studio.palette)
    request.addfinalizer(lambda: style.theme_use(theme))
    studio.gui.root.deiconify()
    request.addfinalizer(studio.gui.root.withdraw)
    studio.geometry('1000x740+0+0')
    studio.deiconify()
    studio.show_tab('Business model')
    tab = studio.model_tab
    tab._show(evaluate(assumptions()))
    _pump(SimpleNamespace(root=studio), .3)
    if os.environ.get('HAYAT_SHOTS') == '1':
        _grab(SimpleNamespace(root=studio), '79-business-model-compact')
    assert tab.scroll.canvas.yview()[1] < 1
    for button in tab.model_buttons.values():
        assert button.winfo_ismapped(), [(str(w), w.winfo_geometry(), w.winfo_ismapped()) for w in
                                       (studio, tab.frame, button.master.master, button.master, button)]
        assert button.winfo_x() + button.winfo_width() <= button.master.winfo_width()
    studio.geometry('1200x820+0+0')
    _pump(SimpleNamespace(root=studio), .2)
    studio.split.sash_place(0, 580, 0)
    _pump(SimpleNamespace(root=studio), .2)
    assert tab._compact_form
    tab.results.xview_moveto(1)
    assert tab.results.xview()[0] > 0 and tab.results.xview()[1] >= .99
    if os.environ.get('HAYAT_SHOTS') == '1':
        _grab(SimpleNamespace(root=studio), '81-business-model-narrow-pane')
    tab.scroll.canvas.yview_moveto(1)
    _pump(SimpleNamespace(root=studio), .2)
    assert tab.scroll.canvas.yview()[1] >= .99
    assert tab.sensitivity.winfo_ismapped()
    tab.sensitivity.xview_moveto(1)
    tab.sensitivity.yview_moveto(1)
    assert tab.sensitivity.xview()[0] > 0 and tab.sensitivity.xview()[1] >= .99
    assert tab.sensitivity.yview()[1] >= .99
    if os.environ.get('HAYAT_SHOTS') == '1':
        _grab(SimpleNamespace(root=studio), '80-business-model-stress-tests')
    studio.geometry('1440x920+0+0')
    studio.split.sash_place(0, 1000, 0)
    _pump(SimpleNamespace(root=studio), .2)
    assert not tab._compact_form


def test_tiles_show_the_headline_numbers_and_the_goal(studio):
    studio.ledger.set_goal("400")
    studio.refresh()
    assert studio.tile_vars["owed"].get() == "100.00 USD"
    assert studio.tile_vars["expenses"].get() == "10.00 USD"
    assert "Monthly goal 400.00 USD" in studio.goal_var.get()
    assert studio.goal_bar.winfo_manager() == "pack"


def test_business_model_repeated_resize_scroll_and_recalculate_keeps_draft(studio, request):
    studio.gui.root.deiconify()
    request.addfinalizer(studio.gui.root.withdraw)
    studio.geometry('1200x820-4000-4000')
    studio.show_tab('Business model')
    studio.deiconify()
    tab = studio.model_tab
    tab.variables['price'].set('537.25')
    def widget_count(widget):
        return 1 + sum(widget_count(child) for child in widget.winfo_children())
    count = widget_count(tab.frame)
    for index in range(30):
        studio.geometry(f'{1200 if index % 2 else 1440}x820-4000-4000')
        studio.update()
        studio.split.sash_place(0, 580 if index % 2 else 1000, 0)
        studio.update()
        tab.scroll.canvas.yview_moveto(index % 2)
        tab.sensitivity.yview_moveto(index % 2)
        assert tab.calculate()
        studio.update()
        assert tab.variables['price'].get() == '537.25'
        assert len(tab.sensitivity.get_children()) == 11
    assert widget_count(tab.frame) == count


def test_tab_buttons_switch_tables_and_mark_the_current_one(studio):
    studio.show_tab("Leads")
    assert studio.tab_frames["Leads"].winfo_manager() == "pack"
    assert not studio.tab_frames["Invoices"].winfo_manager()
    assert str(studio.tab_buttons["Leads"].cget("style")) == "HayatAccent.TButton"
    studio.mark_paid()  # nothing selected: it returns to the invoices to choose one
    assert studio.current_tab == "Invoices"


def test_paid_invoices_are_dimmed_and_money_is_right_aligned(studio):
    number = studio.invoices.get_children()[0]
    studio.invoices.selection_set(number)
    studio.mark_paid()
    assert "done" in studio.invoices.item(number, "tags")
    assert str(studio.invoices.column("total", "anchor")) == "e"


def test_wallet_tab_sets_up_a_method_withdraws_and_marks_it_done(studio):
    studio.ledger.record_income("50", "Fiverr", "Gig payout")
    studio.refresh()
    studio.show_tab("Wallet")
    assert "Recorded balance: 40.00 USD" in studio.wallet_var.get()  # 50 earned - 10 expense
    assert "No withdrawal method yet" in studio.payout_var.get()
    assert studio.tile_vars["wallet"].get() == "40.00 USD"
    assert studio.withdraw_form() is None and "Set up a withdrawal method" in studio.status_var.get()

    form = studio.payout_form()
    form.values["name"].set("Card 4111111111111111")
    assert not form.save() and "account or card numbers" in form.error.get()
    form.values["method"].set("paypal")
    form.values["name"].set("me@example.com")
    assert form.save()
    assert "PayPal: me@example.com" in studio.payout_var.get()

    form = studio.withdraw_form()
    form.values["amount"].set("25")
    assert form.save()
    withdrawal = studio.withdrawal_tree.get_children()[0]
    assert studio.withdrawal_tree.item(withdrawal, "values")[3] == "pending"
    assert "15.00 USD" in studio.wallet_var.get()
    studio.withdrawal_tree.selection_set(withdrawal)
    studio.complete_withdrawal()
    assert studio.withdrawal_tree.item(withdrawal, "values")[3] == "done"
    kinds = [studio.activity_tree.item(item, "values")[0] for item in studio.activity_tree.get_children()]
    assert "Withdrawal" in kinds and "Income" in kinds


def test_record_income_form_adds_to_the_wallet(studio):
    form = studio.record_income_form()
    for key, value in (("amount", "30"), ("source", "Upwork"), ("description", "Milestone")):
        form.values[key].set(value)
    assert form.save()
    assert "20.00 USD" in studio.wallet_var.get()  # 30 earned - 10 expense


def test_the_window_refreshes_the_header_wallet_badge(studio):
    calls = []
    studio.gui.refresh_wallet_badge = lambda: calls.append(1)
    studio.refresh()
    assert calls


class _Email:
    def __init__(self):
        self.ready, self.saved = False, None

    def status(self):
        return {"ready": self.ready, "address": "me@gmail.com" if self.ready else "", "server": "smtp.gmail.com:587",
                "password_stored": self.ready, "problem": None if self.ready else "not set up"}

    def configure_account(self, address, password, smtp_host=""):
        self.saved, self.ready = (address, password), True
        return self.status()

    def check_login(self):
        return {"detail": "Logged in. Nothing was sent."}


def test_email_setup_masks_the_password_and_checks_the_login(studio):
    email = _Email()
    studio.gui.runtime.email = email
    form = studio.email_setup_form()
    entries = [child for child in form.winfo_children() if child.winfo_class() == "TEntry"]
    assert [str(entry.cget("show")) for entry in entries] == ["", "•", ""]
    form.values["address"].set("me@gmail.com")
    form.values["password"].set("abcd efgh ijkl mnop")
    assert form.save()
    assert email.saved == ("me@gmail.com", "abcd efgh ijkl mnop")
    pump(studio, lambda: "Nothing was sent" in studio.status_var.get())


def test_email_invoice_attaches_the_pdf_and_goes_through_the_tool_path(studio):
    email = _Email()
    email.ready = True
    studio.gui.runtime.email = email
    sent = []
    studio.gui._tool = lambda name, arguments: sent.append((name, arguments)) or "sent"
    studio.gui._background = lambda title, work: work()
    number = studio.invoices.get_children()[0]
    studio.invoices.selection_set(number)
    form = studio.email_invoice_form()
    assert form.values["subject"].get().startswith(f"Invoice {number}")
    form.values["to"].set("ap@acme.test")
    assert form.save()
    name, arguments = sent[0]
    assert name == "email_send" and arguments["to"] == "ap@acme.test"
    assert arguments["attachments"][0].endswith(f"{number}.pdf")


def test_email_invoice_without_email_set_up_opens_the_setup(studio):
    studio.gui.runtime.email = _Email()
    studio.invoices.selection_set(studio.invoices.get_children()[0])
    form = studio.email_invoice_form()
    assert form is not None and form.title().endswith("Email setup")


@pytest.fixture
def launch_studio(shared_tk, tmp_path):
    from app.tools.launch import Launchpad

    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    ledger = BusinessLedger(tmp_path / "business.db", tmp_path / "business", permissions)
    gui = FakeGUI(shared_tk, ledger)
    gui.runtime.launch = Launchpad(ledger)
    window = BusinessStudio(gui)
    window.withdraw()
    opened = []
    window._open = opened.append
    window.opened = opened
    yield window
    window.destroy()


def test_a_new_owner_starts_on_the_launch_tab_and_sees_the_steps(launch_studio):
    assert launch_studio.current_tab == "Launch"
    tab = launch_studio.launch_tab
    assert tab.current is None and "The steps:" in tab.intro.cget("text")
    assert "Send your first invoice" in tab.intro.cget("text")


def test_ideas_lead_to_a_started_business_with_a_plan(launch_studio):
    tab = launch_studio.launch_tab
    form = tab.ideas_form()
    form.values["skills"].set("graphic design")
    assert form.save()
    window = tab.ideas_window
    try:
        assert window.start_buttons and "Graphic design" in window.start_buttons[0].cget("text")
        window.start_buttons[0].invoke()
    finally:
        if window.winfo_exists():
            window.destroy()
    start = launch_studio._form_dialog
    assert start.values["offer"].get().startswith("A logo")
    start.values["name"].set("Mohsin Designs")
    start.values["audience"].set("small shops")
    assert start.save()
    assert tab.current == "Mohsin Designs" and "of 12 steps done" in tab.progress_var.get()
    assert tab.steps.item("offer", "text") == "✓" and tab.steps.item("research", "text") == "○"


def test_services_rate_website_and_marketing_from_the_tab(launch_studio):
    tab = launch_studio.launch_tab
    launch_studio.gui.runtime.launch.start("Studio", "Logos")
    launch_studio.refresh()
    form = tab.rate_form()
    form.values["goal"].set("2000")
    assert form.save() and "31.25 USD" in launch_studio.status_var.get()  # 2000 / 0.8 / 80 h
    form = tab.service_form()
    for key, value in (("name", "Logo design"), ("price", "150"), ("days", "5"), ("includes", "3 concepts; 2 revisions")):
        form.values[key].set(value)
    assert form.save()
    assert tab.services.item(tab.services.get_children()[0], "values")[0] == "150.00 USD per project"
    form = tab.quote_form()
    form.values["client"].set("Acme")
    assert form.save() and "Quote Q-" in launch_studio.status_var.get()
    tab.make_website()
    tab.make_marketing()
    assert launch_studio.opened[0].endswith("index.html") and launch_studio.opened[1].endswith("marketing-kit.md")
    tab.steps.selection_set("research")
    tab.mark_done()
    assert tab.steps.item("research", "text") == "✓"
    tab.steps.selection_set("research")
    tab.undo_step()
    assert tab.steps.item("research", "text") == "○"


def test_dashboard_numbers_trends_series_and_upcoming(tmp_path):
    from datetime import date, timedelta
    from app.gui_business_insights import change, dashboard, months_back, previous_period
    permissions = PermissionManager({"default": "ALLOW"}, tmp_path)
    ledger = BusinessLedger(tmp_path / "books.db", tmp_path / "books", permissions)
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=10).isoformat()
    ledger.record_income("200", "Upwork", "Earlier work", received_on=last_month)
    ledger.record_income("300", "Upwork", "This month")
    ledger.record_expense("50", "software", "Tools")
    ledger.add_client("Acme Ltd")
    first = ledger.create_invoice("Acme Ltd", [{"description": "Work", "unit_price": "100"}])
    ledger.create_invoice("Acme Ltd", [{"description": "More", "unit_price": "40"}])
    ledger.record_payment(first["number"])
    value = dashboard(ledger, "this month", today)
    assert value["currency"] == "USD"
    assert value["kpis"]["income"]["value"] == "400.00 USD"  # 300 other income + 100 paid invoice
    assert value["kpis"]["income"]["change"] == 100.0 and value["kpis"]["compared_with"] == "last month"
    assert value["kpis"]["margin"] == 87.5
    assert value["invoices"] == {"count": 2, "paid": 1, "pending": 1, "overdue": 0, "invoiced": "140.00 USD"}
    assert [month["month"] for month in value["series"]] == months_back(6, today)
    assert value["series"][-1]["income_cents"] == 40000 and value["series"][-1]["expense_cents"] == 5000
    assert value["upcoming"][0]["title"].startswith("INV-") and value["upcoming"][0]["state"] in ("soon", "later")
    assert value["clients"] == 1 and value["transactions"]
    assert previous_period("all") is None and previous_period("this year") == "last year"
    assert change(10, 0) is None and change(15, 10) == 50.0 and change(5, 10) == -50.0


def test_dashboard_cards_filter_and_quick_actions(studio):
    from datetime import date, timedelta
    late = studio.ledger.create_invoice("Acme Ltd", [{"description": "Audit", "unit_price": "45"}])
    studio.ledger._run(lambda db: db.execute("UPDATE documents SET due=? WHERE number=?",
                                             ((date.today() - timedelta(days=2)).isoformat(), late["number"])))
    studio.refresh()
    assert studio.invoice_stat_vars["count"].get() == "2" and studio.invoice_stat_vars["overdue"].get() == "1"
    assert studio.trend_vars["owed"].get() == "2 unpaid invoices"
    assert studio.insight_vars["clients"].get() == "1"
    assert studio.invoices.item(late["number"], "values")[5] == "● Overdue"
    studio.invoice_filter.set("Overdue")
    assert studio.invoices.get_children() == (late["number"],)
    studio.invoice_filter.set("All status")
    studio.invoice_search.set("no such client")
    assert studio.invoices.get_children() == ()
    studio.invoice_search.set("")
    texts = []
    stack = [studio.upcoming_frame]
    while stack:
        widget = stack.pop()
        stack.extend(widget.winfo_children())
        if isinstance(widget, tk.Label):
            texts.append(str(widget.cget("text")))
    assert any("Overdue by 2 days" in text for text in texts)
    studio.select_invoice(late["number"])
    assert studio.invoices.selection() == (late["number"],)
    studio.send_reminder()
    assert "Open the main HAYAT window" in studio.status_var.get() and late["number"] in studio.status_var.get()
    form = studio.add_client_form()
    form.values["name"].set("Beta Corp")
    assert form.save() and len(studio.clients.get_children()) == 2
    form = studio.add_expense_form()
    form.values["amount"].set("25")
    form.values["category"].set("travel")
    form.values["description"].set("Train")
    assert form.save() and len(studio.expenses.get_children()) == 2
    assert set(studio.quick_buttons) >= {"Create invoice", "Mark as paid", "Send reminder", "Add client", "Export for taxes",
                                         "Open documents folder", "Add expense", "Generate report"}
    assert len(studio.chart.find_all()) > 5


def test_quick_create_invoice_prefills_chat_without_sending(studio):
    calls = []
    studio.gui.use_example = calls.append
    studio.create_invoice()
    assert calls == ["Create an invoice for CLIENT for AMOUNT for DESCRIPTION"] and "press Send" in studio.status_var.get()
