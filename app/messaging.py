"""Sending a message the owner asked HAYAT to send.

WhatsApp Desktop accepts ``whatsapp://send?phone=<number>&text=<message>``:
it opens the chat with that person and puts the message in the compose box.
What is left is pressing Enter, and that is the one dangerous keystroke -- if
WhatsApp is not the window in front, Enter lands in whatever is. So Enter is
pressed only after the WhatsApp window is confirmed focused, and otherwise the
message is left typed and unsent, with the reason.

Authority: ``whatsapp_send`` is ``send_message``, a high-consequence category.
It runs without a second prompt only when the owner's own message asked to
send (see app/safety/authority.py, OWNER_REQUESTABLE_CATEGORIES); from a web
page, a schedule or background learning it cannot run at all.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from app.contacts import ContactBook, ContactError, normalize_phone
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


def _press_enter() -> None:
    from pywinauto import keyboard

    keyboard.send_keys("{ENTER}")


def _open_uri(uri: str) -> None:
    if os.name != "nt":
        raise RuntimeError("WhatsApp sending is only wired for Windows.")
    os.startfile(uri)  # noqa: S606 -- a fixed whatsapp:// scheme built below, never user-chosen


class WhatsAppSender:
    #: How long the chat takes to switch after the URI opens, when WhatsApp is
    #: already running. Pressing Enter sooner sends into the previous chat.
    CHAT_SETTLE_SECONDS = 2.5

    def __init__(self, book: ContactBook, windows: Any, *, open_uri: Callable[[str], None] = _open_uri,
                 press_enter: Callable[[], None] = _press_enter, sleep: Callable[[float], None] = time.sleep) -> None:
        self.book = book
        self.windows = windows
        self.open_uri = open_uri
        self.press_enter = press_enter
        self.sleep = sleep

    def recipient(self, to: str) -> dict[str, str]:
        """A contact name, an alias, or a number given directly."""
        if any(ch.isdigit() for ch in str(to)) and not any(ch.isalpha() for ch in str(to)):
            return {"name": str(to).strip(), "phone": normalize_phone(to)}
        contact = self.book.find(to)
        if not contact.get("phone"):
            raise ContactError(f"I have {contact['name']}'s email but no phone number for WhatsApp.")
        return {"name": contact["name"], "phone": contact["phone"]}

    def send(self, to: str, message: str, press_send: bool = True) -> dict[str, Any]:
        text = str(message or "").strip()
        if not text:
            raise ValueError("What should the message say?")
        person = self.recipient(to)
        self.open_uri(f"whatsapp://send?phone={person['phone'].lstrip('+')}&text={quote(text)}")
        window = self.windows.wait_for_window("whatsapp", timeout=15.0)
        if not window:
            raise RuntimeError("WhatsApp did not open, so nothing was sent.")
        self.sleep(self.CHAT_SETTLE_SECONDS)
        result = {"to": person["name"], "number_ending": person["phone"][-4:], "message": text,
                  "sent": False, "delivery_confirmed": False}
        if not press_send:
            return {**result, "detail": f"The message to {person['name']} is typed in WhatsApp and waiting for you to send it."}
        focused = self.windows.focus("whatsapp", window["handle"])
        if not focused.get("focused"):
            return {**result, "detail": (f"The message to {person['name']} is typed in WhatsApp but not sent: "
                                         "WhatsApp was not the window in front, so I did not press Enter.")}
        self.press_enter()
        return {**result, "sent": True,
                "detail": f"Sent to {person['name']} (number ending {person['phone'][-4:]}). "
                          "WhatsApp shows the ticks once it is delivered; I did not check them."}


class EmailSender:
    """Send mail from the owner's own account over SMTP.

    Free with any provider that offers SMTP (Gmail needs an app password).
    The password is never in settings or in a prompt: it lives in the
    Windows credential store and is leased for one send at a time.
    """

    SECRET = "email_smtp_password"
    SCOPE = "email:send"
    MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

    def __init__(self, book: ContactBook, secrets: Any, config: dict[str, Any] | None = None,
                 permissions: Any = None, *, smtp: Callable[..., Any] | None = None,
                 smtp_ssl: Callable[..., Any] | None = None) -> None:
        import smtplib

        self.book = book
        self.secrets = secrets
        self.config = dict(config or {})
        self.permissions = permissions
        self.smtp = smtp or smtplib.SMTP
        self.smtp_ssl = smtp_ssl or smtplib.SMTP_SSL

    #: Mail servers for common providers, by the address's domain, so the
    #: owner only has to give an address and an app password.
    PROVIDERS = {
        ("gmail.com", "googlemail.com"): ("smtp.gmail.com", 587, "starttls"),
        ("outlook.com", "hotmail.com", "live.com", "msn.com"): ("smtp-mail.outlook.com", 587, "starttls"),
        ("yahoo.com", "ymail.com"): ("smtp.mail.yahoo.com", 465, "ssl"),
        ("icloud.com", "me.com", "mac.com"): ("smtp.mail.me.com", 587, "starttls"),
        ("zoho.com",): ("smtp.zoho.com", 587, "starttls"),
    }

    @classmethod
    def provider_for(cls, address: str) -> tuple[str, int, str] | None:
        domain = address.rsplit("@", 1)[-1].lower()
        return next((server for domains, server in cls.PROVIDERS.items() if domain in domains), None)

    @staticmethod
    def account_path() -> Path:
        """Where the address and server live: data/memory, which git ignores. Never the password."""
        from app.config import PROJECT_ROOT

        return PROJECT_ROOT / "data" / "memory" / "email-account.json"

    @classmethod
    def merged_config(cls, settings: dict[str, Any] | None, path: Path | None = None) -> dict[str, Any]:
        """settings.yaml wins when it names a server; otherwise the account set up in the window."""
        import json

        config = dict(settings or {})
        if str(config.get("smtp_host") or "").strip():
            return config
        path = path or cls.account_path()
        try:
            account = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            log_swallowed(_log, "reading the saved email account")
            account = {}
        config.update({key: account[key] for key in ("smtp_host", "smtp_port", "security", "username", "from_address")
                       if account.get(key)})
        return config

    def configure_account(self, address: str, password: str, *, smtp_host: str = "", smtp_port: int | str = 0,
                          security: str = "", path: Path | None = None) -> dict[str, Any]:
        """Save the owner's own sending account: address and server to a file, password to the credential store."""
        import json

        from app.contacts import normalize_email

        address = normalize_email(address)
        if self.secrets is None:
            raise ContactError("Email can't be set up in safe mode: there is no credential store.")
        known = self.provider_for(address)
        host = (smtp_host or (known[0] if known else "")).strip()
        if not host:
            raise ContactError("I don't know that provider's mail server. Give its SMTP host, e.g. smtp.example.com.")
        port = int(smtp_port or (known[1] if known else 587))
        mode = (security or (known[2] if known else "starttls")).strip().lower()
        if mode not in {"starttls", "ssl"}:
            raise ContactError("Security must be starttls or ssl.")
        secret = "".join(str(password or "").split())  # Google shows app passwords in groups of four.
        if not secret:
            raise ContactError("Paste the app password. It is kept in the Windows credential store, not in a file.")
        self.secrets.store(self.SECRET, secret, scopes=[self.SCOPE])
        account = {"smtp_host": host, "smtp_port": port, "security": mode, "username": address, "from_address": address}
        target = path or self.account_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(account, indent=2), encoding="utf-8")
        self.config.update(account)
        return {**self.status(), "saved": True}

    def status(self) -> dict[str, Any]:
        stored = False
        if self.secrets is not None:
            try:
                stored = any(item.get("name") == self.SECRET for item in self.secrets.list())
            except Exception:
                log_swallowed(_log, "listing stored secrets for email status")
        problem = self.setup_problem()
        if problem is None and not stored:
            problem = "The app password has not been stored yet."
        return {"ready": problem is None, "address": str(self.config.get("from_address") or self.config.get("username") or ""),
                "server": f"{self.config.get('smtp_host', '')}:{self.config.get('smtp_port', '')}".strip(":"),
                "password_stored": stored, "problem": problem}

    def check_login(self) -> dict[str, Any]:
        """Log in to the mail server and log out again, sending nothing."""
        problem = self.setup_problem()
        if problem:
            raise ContactError(problem)
        token = self.secrets.issue_lease(self.SECRET, self.SCOPE, ttl_seconds=30)
        password = self.secrets.redeem(token, self.SCOPE)
        host, port = str(self.config["smtp_host"]), int(self.config.get("smtp_port") or 587)
        if str(self.config.get("security", "starttls")).lower() == "ssl":
            server = self.smtp_ssl(host, port, timeout=30)
        else:
            server = self.smtp(host, port, timeout=30)
            server.starttls()
        try:
            server.login(str(self.config["username"]), password)
        finally:
            try:
                server.quit()
            except Exception:
                log_swallowed(_log, "closing the SMTP connection after a login check")
        return {"ok": True, "detail": f"Logged in to {host} as {self.config['username']}. Nothing was sent."}

    def setup_problem(self) -> str | None:
        missing = [key for key in ("smtp_host", "username") if not str(self.config.get(key) or "").strip()]
        if missing:
            return ("Email is not set up yet. Open Business -> Email setup and give your address and an app "
                    "password (Gmail: Google Account -> Security -> App passwords). Or fill in email.smtp_host and "
                    "email.username in config/settings.yaml and store the password with "
                    f"`python main.py --secret-set {self.SECRET} --secret-scope {self.SCOPE}`.")
        if self.secrets is None:
            return "Email is unavailable in safe mode, which has no credential store."
        return None

    def recipient(self, to: str) -> dict[str, str]:
        from app.contacts import normalize_email

        if "@" in str(to):
            address = normalize_email(to)
            return {"name": address, "email": address}
        contact = self.book.find(to)
        if not contact.get("email"):
            raise ContactError(f"I have {contact['name']}'s phone number but no email address. "
                               f"Tell me it: save {contact['name']}'s email as name@example.com.")
        return {"name": contact["name"], "email": contact["email"]}

    def send(self, to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict[str, Any]:
        from email.message import EmailMessage
        import mimetypes

        if problem := self.setup_problem():
            raise RuntimeError(problem)
        if not str(body or "").strip():
            raise ValueError("What should the email say?")
        person = self.recipient(to)
        message = EmailMessage()
        message["From"] = self.config.get("from_address") or self.config["username"]
        message["To"] = person["email"]
        message["Subject"] = str(subject or "").strip() or " ".join(str(body).split())[:60]
        message.set_content(str(body))
        attached = []
        for item in attachments or []:
            path = self.permissions.validate_project_path(item) if self.permissions else Path(item)
            if not path.is_file():
                raise FileNotFoundError(f"{path.name} does not exist, so the email was not sent.")
            if path.stat().st_size > self.MAX_ATTACHMENT_BYTES:
                raise ValueError(f"{path.name} is over 20 MB, which most mail servers refuse.")
            kind, _ = mimetypes.guess_type(path.name)
            main, _, sub = (kind or "application/octet-stream").partition("/")
            message.add_attachment(path.read_bytes(), maintype=main, subtype=sub, filename=path.name)
            attached.append(path.name)

        password = self.secrets.redeem(self.secrets.issue_lease(self.SECRET, self.SCOPE), self.SCOPE)
        host, port = str(self.config["smtp_host"]), int(self.config.get("smtp_port") or 587)
        if str(self.config.get("security", "starttls")).lower() == "ssl":
            server = self.smtp_ssl(host, port, timeout=30)
        else:
            server = self.smtp(host, port, timeout=30)
            server.starttls()
        try:
            server.login(str(self.config["username"]), password)
            refused = server.send_message(message) or {}
        finally:
            try:
                server.quit()
            except Exception:
                log_swallowed(_log, "closing the SMTP connection")
        if refused:
            raise RuntimeError(f"The mail server refused {', '.join(refused)}.")
        return {"sent": True, "to": person["name"], "address": person["email"],
                "subject": message["Subject"], "attachments": attached,
                "detail": f"Sent to {person['name']}. The server accepted it; I cannot see whether it reached the inbox."}


def register_messaging_tools(registry: Any, book: ContactBook, windows: Any, *, secrets: Any = None,
                             email_config: dict[str, Any] | None = None, permissions: Any = None,
                             mailer: EmailSender | None = None) -> WhatsAppSender:
    mailer = mailer or EmailSender(book, secrets, email_config, permissions)
    registry.register(
        "email_send",
        "Send an email from the owner's own account to a saved contact or an address, with optional file "
        "attachments. Needs email set up in config/settings.yaml and the SMTP password stored as a secret.",
        {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            "attachments": {"type": "array", "items": {"type": "string"}}},
         "required": ["to", "body"], "additionalProperties": False},
        "high", "send_message",
        lambda to, body, subject="", attachments=None: mailer.send(to, subject, body, attachments),
    )
    sender = WhatsAppSender(book, windows)
    registry.register(
        "whatsapp_send",
        "Send a WhatsApp message to a saved contact (name or alias such as 'mom') or an international number. "
        "Opens the chat in WhatsApp Desktop with the message filled in and presses Enter only if WhatsApp is in front.",
        {"type": "object", "properties": {
            "to": {"type": "string"}, "message": {"type": "string"},
            "press_send": {"type": "boolean"}},
         "required": ["to", "message"], "additionalProperties": False},
        "high", "send_message", sender.send,
    )
    return sender
