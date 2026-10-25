from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.contacts import ContactBook, ContactError, normalize_phone
from app.intent.router import IntentRouter
from app.messaging import WhatsAppSender
from app.session import SessionState


@pytest.fixture
def book(tmp_path):
    return ContactBook(tmp_path / "contacts.db")


# ---------------------------------------------------------------- contacts


def test_a_number_is_stored_in_international_form():
    assert normalize_phone("+44 7700 900123") == "+447700900123"
    assert normalize_phone("0044 (7700) 900-123") == "+447700900123"


def test_a_number_without_a_country_code_is_refused_rather_than_guessed():
    """Guessing a country for 07700 900123 could message a stranger."""
    with pytest.raises(ContactError, match="country code"):
        normalize_phone("07700 900123")


def test_mom_is_found_by_name_alias_and_my(book):
    book.save("Mom", "+44 7700 900123", aliases=["mother", "ammi"])
    for said in ("mom", "Mom", "my mom", "mom's", "ammi", "Mother"):
        assert book.find(said)["phone"] == "+447700900123", said


def test_a_first_name_finds_the_one_full_name_it_belongs_to(book):
    book.save("Ali Khan", "+923001234567")
    assert book.find("Ali")["name"] == "Ali Khan"


def test_an_ambiguous_name_asks_instead_of_choosing(book):
    book.save("Ali Khan", "+923001234567")
    book.save("Ali Raza", "+923007654321")
    with pytest.raises(ContactError, match="Which one"):
        book.find("Ali")


def test_an_unknown_contact_says_how_to_add_them(book):
    with pytest.raises(ContactError, match="save Sara's number"):
        book.find("Sara")


def test_saving_again_updates_without_losing_the_other_details(book):
    book.save("Ahmed", "+441234567890")
    book.save("Ahmed", email="ahmed@example.com")
    ahmed = book.find("ahmed")
    assert ahmed["phone"] == "+441234567890" and ahmed["email"] == "ahmed@example.com"


def test_the_contact_book_is_never_committed():
    from pathlib import Path

    ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "data/contacts/" in ignore


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize(("said", "recipient", "text"), [
    ("Send mom a message on WhatsApp saying I'm running late", "mom", "I'm running late"),
    ("Open WhatsApp and send Ali hi", "Ali", "hi"),
    ("text Ali I'm running late on whatsapp", "Ali", "I'm running late"),
    ("message Ahmed on WhatsApp: meeting at 5", "Ahmed", "meeting at 5"),
    ("send a whatsapp message to my brother saying call me", "my brother", "call me"),
])
def test_a_whatsapp_request_is_planned_straight_to_the_send_tool(said, recipient, text):
    from app.agent.planner import Planner

    intent = IntentRouter().route(said, SessionState())
    assert intent.entities["recipient"] == recipient
    assert intent.entities["message_text"] == text
    registry = SimpleNamespace(names=lambda: ["whatsapp_send", "contact_save", "launch_application"])
    planner = Planner.__new__(Planner)
    planner.registry = registry
    plan = planner._deterministic_plan(intent.goal, intent)
    assert [step.tool for step in plan] == ["whatsapp_send"]
    assert plan[0].arguments == {"to": recipient, "message": text}


@pytest.mark.parametrize(("said", "field", "value"), [
    ("Save mom's number as +44 7700 900123", "contact_phone", "+44 7700 900123"),
    ("mom's number is +447700900123", "contact_phone", "+447700900123"),
    ("remember Ahmed's email is ahmed@example.com", "contact_email", "ahmed@example.com"),
])
def test_saving_a_contact_is_understood(said, field, value):
    intent = IntentRouter().route(said, SessionState())
    assert intent.entities[field] == value


def test_a_local_number_is_not_mistaken_for_a_contact_to_save():
    assert IntentRouter.contact_details("mom's number is 07700 900123") is None


# ---------------------------------------------------------------- sending


class _Windows:
    def __init__(self, *, focused=True, window=True):
        self.focused, self.window = focused, window

    def wait_for_window(self, application, timeout=0):
        return {"handle": 7, "pid": 1} if self.window else None

    def focus(self, application, handle=None):
        return {"focused": self.focused}


def _sender(book, **windows):
    calls = SimpleNamespace(uris=[], enters=0)
    sender = WhatsAppSender(
        book, _Windows(**windows), open_uri=calls.uris.append,
        press_enter=lambda: setattr(calls, "enters", calls.enters + 1), sleep=lambda seconds: None,
    )
    return sender, calls


def test_the_chat_opens_with_the_message_filled_in_and_enter_is_pressed(book):
    book.save("Mom", "+44 7700 900123")
    sender, calls = _sender(book)
    result = sender.send("mom", "I'm running late & sorry")
    assert calls.uris == ["whatsapp://send?phone=447700900123&text=I%27m%20running%20late%20%26%20sorry"]
    assert calls.enters == 1
    assert result["sent"] is True and result["delivery_confirmed"] is False


def test_enter_is_never_pressed_into_another_window(book):
    """If WhatsApp is not in front, Enter would land in whatever is."""
    book.save("Mom", "+44 7700 900123")
    sender, calls = _sender(book, focused=False)
    result = sender.send("mom", "hi")
    assert calls.enters == 0
    assert result["sent"] is False and "did not press Enter" in result["detail"]


def test_an_unknown_contact_sends_nothing(book):
    sender, calls = _sender(book)
    with pytest.raises(ContactError):
        sender.send("Sara", "hi")
    assert calls.uris == [] and calls.enters == 0


def test_whatsapp_not_opening_is_a_failure_not_a_send(book):
    book.save("Mom", "+44 7700 900123")
    sender, calls = _sender(book, window=False)
    with pytest.raises(RuntimeError, match="nothing was sent"):
        sender.send("mom", "hi")
    assert calls.enters == 0


def test_a_number_can_be_messaged_directly(book):
    sender, calls = _sender(book)
    sender.send("+44 7700 900123", "hi", press_send=False)
    assert calls.uris[0].startswith("whatsapp://send?phone=447700900123&")
    assert calls.enters == 0, "press_send=False leaves it typed for the owner"


def test_sending_needs_the_owners_own_request():
    """send_message: runs on the owner's say-so, never from a web page."""
    from app.safety.authority import ActionRequest, AuthorityManager, Principal

    authority = AuthorityManager(owner_mode=True)
    assert not authority.evaluate(ActionRequest(tool="whatsapp_send", category="send_message", turn_id="t1")).allowed
    authority.open_envelope({"send_message"}, turn_id="t1")
    assert authority.evaluate(ActionRequest(tool="whatsapp_send", category="send_message", turn_id="t1")).allowed
    assert not authority.evaluate(ActionRequest(
        tool="whatsapp_send", category="send_message", turn_id="t1",
        principal=Principal.UNTRUSTED_EXTERNAL_CONTENT)).allowed


# ------------------------------------------------------------------ email


class _Secrets:
    def __init__(self):
        self.leases = []

    def issue_lease(self, name, scope, ttl_seconds=30):
        self.leases.append((name, scope))
        return "token"

    def redeem(self, token, scope):
        return "app-password"


class _SMTP:
    instances = []

    def __init__(self, host, port, timeout=0):
        self.host, self.port, self.calls = host, port, []
        _SMTP.instances.append(self)

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.message = message
        return {}

    def quit(self):
        self.calls.append("quit")


_GMAIL = {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "security": "starttls", "username": "me@gmail.com"}


def _mailer(book, config=_GMAIL, **kwargs):
    from app.messaging import EmailSender

    _SMTP.instances.clear()
    return EmailSender(book, _Secrets(), config, smtp=_SMTP, smtp_ssl=_SMTP, **kwargs)


def test_an_email_goes_through_the_owners_account_with_the_password_leased(book):
    book.save("Ahmed", email="ahmed@example.com")
    mailer = _mailer(book)
    result = mailer.send("Ahmed", "", "The invoice is attached.")
    server = _SMTP.instances[0]
    assert (server.host, server.port) == ("smtp.gmail.com", 587)
    assert server.calls[0] == "starttls" and server.calls[1] == ("login", "me@gmail.com", "app-password")
    assert server.message["To"] == "ahmed@example.com" and server.message["From"] == "me@gmail.com"
    assert server.message["Subject"] == "The invoice is attached."
    assert mailer.secrets.leases == [("email_smtp_password", "email:send")]
    assert result["sent"] and "cannot see whether it reached the inbox" in result["detail"]


def test_email_that_is_not_set_up_says_how_rather_than_failing_vaguely(book):
    with pytest.raises(RuntimeError, match="--secret-set email_smtp_password"):
        _mailer(book, config={}).send("a@example.com", "hi", "hi")


def test_a_contact_with_only_a_phone_number_cannot_be_emailed(book):
    book.save("Mom", "+44 7700 900123")
    with pytest.raises(ContactError, match="no email address"):
        _mailer(book).send("mom", "", "hi")


def test_attachments_are_read_through_the_permission_check(book, tmp_path):
    from app.safety.permissions import PermissionManager

    report = tmp_path / "report.pdf"
    report.write_bytes(b"%PDF-1.4 test")
    mailer = _mailer(book, permissions=PermissionManager({"default": "ALLOW", "permissions": {}}, tmp_path))
    result = mailer.send("ahmed@example.com", "Report", "Attached.", [str(report)])
    assert result["attachments"] == ["report.pdf"]
    assert _SMTP.instances[0].message.get_payload()[1].get_filename() == "report.pdf"
    with pytest.raises(PermissionError):
        mailer.send("ahmed@example.com", "x", "y", ["C:/Windows/System32/config/SAM"])


@pytest.mark.parametrize(("said", "planned"), [
    ("send an email to ahmed@example.com saying the invoice is attached", True),
    ("email Ahmed: meeting moved to 5", True),
    ("Please email Ahmed the report", False),  # attach the report -- the model plans that
])
def test_email_is_planned_directly_only_when_the_words_to_send_are_explicit(said, planned):
    from app.agent.planner import Planner

    intent = IntentRouter().route(said, SessionState())
    planner = Planner.__new__(Planner)
    planner.registry = SimpleNamespace(names=lambda: ["email_send", "whatsapp_send"])
    plan = planner._deterministic_plan(intent.goal, intent)
    assert bool(plan and plan[0].tool == "email_send") is planned


class _Store(_Secrets):
    """A credential store that remembers what was stored, like the real broker."""

    def __init__(self):
        super().__init__()
        self.stored = {}

    def store(self, name, value, *, scopes):
        self.stored[name] = (value, list(scopes))
        return {"name": name, "stored": True}

    def list(self):
        return [{"name": name} for name in self.stored]

    def redeem(self, token, scope):
        return self.stored["email_smtp_password"][0]


def test_email_setup_from_the_window_needs_only_an_address_and_app_password(book, tmp_path):
    import json

    from app.messaging import EmailSender

    store = _Store()
    mailer = EmailSender(book, store, {"smtp_port": 587}, smtp=_SMTP, smtp_ssl=_SMTP)
    assert not mailer.status()["ready"]
    result = mailer.configure_account("me@gmail.com", "abcd efgh ijkl mnop", path=tmp_path / "account.json")
    assert result["ready"] and result["server"] == "smtp.gmail.com:587"
    # Google shows the app password in groups of four; the spaces are not part of it.
    assert store.stored["email_smtp_password"] == ("abcdefghijklmnop", ["email:send"])
    saved = (tmp_path / "account.json").read_text(encoding="utf-8")
    assert "abcd" not in saved and json.loads(saved)["username"] == "me@gmail.com"
    # A later start reads the saved account, unless settings.yaml names a server itself.
    assert EmailSender.merged_config({"smtp_host": ""}, tmp_path / "account.json")["smtp_host"] == "smtp.gmail.com"
    assert EmailSender.merged_config({"smtp_host": "mail.me.org"}, tmp_path / "account.json")["smtp_host"] == "mail.me.org"


def test_checking_the_email_login_sends_nothing(book, tmp_path):
    from app.messaging import EmailSender

    _SMTP.instances.clear()
    store = _Store()
    mailer = EmailSender(book, store, {}, smtp=_SMTP, smtp_ssl=_SMTP)
    mailer.configure_account("me@outlook.com", "pw", path=tmp_path / "a.json")
    assert "Nothing was sent" in mailer.check_login()["detail"]
    server = _SMTP.instances[0]
    assert server.host == "smtp-mail.outlook.com"
    assert ("login", "me@outlook.com", "pw") in server.calls and not hasattr(server, "message")


def test_unknown_providers_need_their_server_and_bad_input_is_refused(book, tmp_path):
    from app.contacts import ContactError
    from app.messaging import EmailSender

    mailer = EmailSender(book, _Store(), {}, smtp=_SMTP, smtp_ssl=_SMTP)
    with pytest.raises(ContactError, match="mail server"):
        mailer.configure_account("me@mybusiness.example", "pw", path=tmp_path / "a.json")
    assert mailer.configure_account("me@mybusiness.example", "pw", smtp_host="smtp.mybusiness.example",
                                    path=tmp_path / "a.json")["ready"]
    with pytest.raises(ContactError):
        mailer.configure_account("not-an-address", "pw", path=tmp_path / "a.json")
    with pytest.raises(ContactError, match="app password"):
        mailer.configure_account("me@gmail.com", "  ", path=tmp_path / "a.json")
    with pytest.raises(ContactError, match="safe mode"):
        EmailSender(book, None, {}).configure_account("me@gmail.com", "pw", path=tmp_path / "a.json")
