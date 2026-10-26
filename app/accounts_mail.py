"""Email with a regular login: your address and password, the way any mail app connects (IMAP and SMTP).

No developer app, client ID or API key: for most providers (Zoho, GMX, AOL, Yandex, Fastmail, your company or
your own domain) the normal password works. Where the provider requires it, an *app password* is used instead,
made in a minute in the account's security settings, and the window says which:

* Gmail, Yahoo, iCloud and AOL refuse a normal password from mail apps; they take an app password.
* Outlook, Hotmail and Live no longer accept any password from mail apps (Microsoft turned it off in 2024); they
  connect with the Microsoft sign-in above instead.
* Proton Mail is end-to-end encrypted and only reachable through its Bridge app.

The password is checked with a real IMAP login before anything is kept, then stored in Windows Credential Manager,
never in a file or the chat. Reading is direct (newest mail, unread mail, a search); sending waits for your
confirmation like every other account. Mixed into AccountManager (app/accounts.py).
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import re
import smtplib
import socket
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

#: (IMAP host, SMTP host, SMTP port, SMTP security) by the address's domain.
SERVERS: dict[tuple[str, ...], tuple[str, str, int, str]] = {
    ("gmail.com", "googlemail.com"): ("imap.gmail.com", "smtp.gmail.com", 587, "starttls"),
    ("yahoo.com", "ymail.com", "rocketmail.com"): ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465, "ssl"),
    ("icloud.com", "me.com", "mac.com"): ("imap.mail.me.com", "smtp.mail.me.com", 587, "starttls"),
    ("aol.com",): ("imap.aol.com", "smtp.aol.com", 465, "ssl"),
    ("zoho.com", "zohomail.com", "zohomail.in"): ("imap.zoho.com", "smtp.zoho.com", 587, "starttls"),
    ("gmx.com", "gmx.net", "gmx.de", "gmx.us"): ("imap.gmx.com", "mail.gmx.com", 587, "starttls"),
    ("mail.com",): ("imap.mail.com", "smtp.mail.com", 587, "starttls"),
    ("yandex.com", "yandex.ru"): ("imap.yandex.com", "smtp.yandex.com", 465, "ssl"),
    ("fastmail.com", "fastmail.fm"): ("imap.fastmail.com", "smtp.fastmail.com", 465, "ssl"),
    ("rediffmail.com",): ("imap.rediffmail.com", "smtp.rediffmail.com", 465, "ssl"),
}
APP_PASSWORD = {"gmail.com": "https://myaccount.google.com/apppasswords", "googlemail.com": "https://myaccount.google.com/apppasswords",
                "yahoo.com": "https://login.yahoo.com/account/security", "ymail.com": "https://login.yahoo.com/account/security",
                "icloud.com": "https://account.apple.com", "me.com": "https://account.apple.com", "mac.com": "https://account.apple.com",
                "aol.com": "https://login.aol.com/account/security"}
MICROSOFT = ("outlook.com", "hotmail.com", "live.com", "msn.com", "outlook.in", "hotmail.co.uk", "live.in")
PROTON = ("proton.me", "protonmail.com", "pm.me")
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


def mail_provider(Provider: Any, Operation: Any) -> Any:
    return Provider(
        "mail", "Email (any provider)", "Your inbox with your normal email login: read and search mail, and send once you "
        "confirm. No developer setup.", ("token",), group="Mail & calendar", token_label="password",
        token_title="Sign in with your email address and password",
        token_fields=(("address", "Your email address"),),
        token_steps=("Type your email address and your email password, then press Enter.",
                     "Gmail, Yahoo, iCloud and AOL do not let mail apps use your normal password: make an app password in your "
                     "account's security settings (Gmail: Google Account → Security → 2-Step Verification → App passwords) "
                     "and use that here.",
                     "Outlook and Hotmail no longer accept passwords from mail apps: connect Microsoft above instead.",
                     "HAYAT logs in once to check it, then keeps the password in Windows Credential Manager; it never "
                     "appears in a file or the chat."),
        operations=(Operation("inbox", "Newest mail"), Operation("unread", "Unread mail"),
                    Operation("search", "Search my mail", fields=(("query", "Words, a sender or a subject", True),)),
                    Operation("send", "Send an email", True, (("to", "To (an email address)", True), ("subject", "Subject", True),
                                                              ("body", "Message", True)))))


def servers_for(address: str) -> tuple[str, str, int, str]:
    domain = address.rsplit("@", 1)[-1].lower()
    for domains, servers in SERVERS.items():
        if domain in domains:
            return servers
    return (f"imap.{domain}", f"smtp.{domain}", 587, "starttls")  # most hosts and company domains


def _decode(value: Any) -> str:
    parts = []
    for text, charset in email.header.decode_header(str(value or "")):
        parts.append(text.decode(charset or "utf-8", "replace") if isinstance(text, bytes) else text)
    return " ".join("".join(parts).split())[:300]


class MailAccounts:
    """IMAP/SMTP for the "mail" account; the manager supplies broker, records and the lock."""

    imap_factory = staticmethod(lambda host: imaplib.IMAP4_SSL(host, 993, timeout=25))
    smtp_factory = staticmethod(lambda host, port, security: (smtplib.SMTP_SSL(host, port, timeout=25) if security == "ssl"
                                                              else smtplib.SMTP(host, port, timeout=25)))

    def _mail_login(self, address: str, password: str) -> dict[str, Any]:
        """Check the login with IMAP, then keep it. Nothing is kept when the server refuses."""
        address = address.strip().lower()
        if not _ADDRESS.match(address):
            raise ValueError("Type your whole email address, such as name@example.com.")
        domain = address.rsplit("@", 1)[-1]
        if domain in MICROSOFT:
            raise PermissionError("Outlook, Hotmail and Live no longer accept passwords from mail apps (Microsoft turned it off "
                                  "in 2024). Connect Microsoft in this window instead: Sign in with a code.")
        if domain in PROTON:
            raise PermissionError("Proton Mail is end-to-end encrypted and only reachable through Proton's Bridge app, not "
                                  "with a password here.")
        password = password.strip()
        imap_host, smtp_host, smtp_port, security = servers_for(address)
        try:
            connection = self.imap_factory(imap_host)
            try:
                connection.login(address, password)
            finally:
                try:
                    connection.logout()
                except Exception:  # noqa: S110 (closing after a refusal)
                    pass
        except imaplib.IMAP4.error:
            hint = (f" {domain} needs an app password, not your normal one: make it at {APP_PASSWORD[domain]} and use that."
                    if domain in APP_PASSWORD else " Check the password (and that IMAP is turned on in your mail settings).")
            raise PermissionError(f"{imap_host} refused the login.{hint}") from None
        except (OSError, socket.timeout) as exc:
            raise RuntimeError(f"Could not reach {imap_host} ({type(exc).__name__}); for a company or own domain, the mail "
                               "server may have another name.") from None
        self._need_broker()
        self.broker.store("account.mail", json.dumps({"access_token": "", "method": "token", "address": address,
                                                      "password": password, "imap": imap_host, "smtp": smtp_host,
                                                      "smtp_port": smtp_port, "security": security}), scopes=["account:mail"])
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            records = self._records()
            record = records.setdefault("mail", {})
            record.update(connected=True, connected_as=address, method="token", connected_at=now, checked_at=now,
                          check_ok=True, check_error="", settings={"address": address})
            self._save_records(records)
        # Sending from chat and Business uses the same account (app/messaging.EmailSender).
        sender = getattr(self, "email_sender", None)
        if sender is not None:
            try:
                sender.configure_account(address, password, smtp_host=smtp_host, smtp_port=smtp_port, security=security)
            except Exception:  # reading works without it; sending says what is missing
                from app.logging import log_swallowed
                import logging

                log_swallowed(logging.getLogger(__name__), "configuring email sending from the mail account")
        return self.status("mail")

    def _mail_secret(self) -> dict[str, Any]:
        return json.loads(self._secret("account.mail", "mail"))

    def _mail_who(self) -> str:
        """The check: a fresh IMAP login with the kept password."""
        secret = self._mail_secret()
        connection = self.imap_factory(secret["imap"])
        try:
            try:
                connection.login(secret["address"], secret["password"])
            except imaplib.IMAP4.error:
                raise PermissionError(f"{secret['imap']} refused the kept password (changed, or the app password was "
                                      "removed); sign in again.") from None
        finally:
            try:
                connection.logout()
            except Exception:  # noqa: S110 (closing after a refusal)
                pass
        return secret["address"]

    def _read_mail(self, operation: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        secret = self._mail_secret()
        if operation == "profile":
            return [{"title": secret["address"], "detail": f"IMAP {secret['imap']} · SMTP {secret['smtp']}"}]
        connection = self.imap_factory(secret["imap"])
        try:
            connection.login(secret["address"], secret["password"])
            connection.select("INBOX", readonly=True)  # reading never marks anything as read
            if operation == "unread":
                criteria: list[str] = ["UNSEEN"]
            elif operation == "search":
                words = str(params.get("query") or "").replace('"', "").strip()[:100]
                if not words:
                    raise ValueError("Give words, a sender or a subject to search for.")
                criteria = ["OR", "OR", "SUBJECT", f'"{words}"', "FROM", f'"{words}"', "BODY", f'"{words}"']
            else:
                criteria = ["ALL"]
            status, data = connection.search("UTF-8" if operation == "search" else None, *criteria)
            numbers = (data[0] or b"").split()[-15:] if status == "OK" else []
            rows = []
            for number in reversed(numbers):
                status, fetched = connection.fetch(number, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                flags = fetched[0][0].decode("ascii", "replace")
                headers = email.message_from_bytes(fetched[0][1])
                when = headers.get("Date", "")
                try:
                    when = email.utils.parsedate_to_datetime(when).isoformat(timespec="minutes")
                except (TypeError, ValueError):
                    pass
                rows.append({"title": _decode(headers.get("Subject")) or "(no subject)", "detail": _decode(headers.get("From")),
                             "when": when, "unread": "\\Seen" not in flags})
            return rows
        finally:
            try:
                connection.logout()
            except Exception:  # noqa: S110 (closing)
                pass

    def _write_mail(self, operation: str, params: dict[str, str]) -> dict[str, Any]:
        if operation != "send":
            raise ValueError(f"Email cannot {operation}.")
        to = params["to"].strip()
        if not _ADDRESS.match(to):
            raise ValueError("The recipient must be one email address.")
        secret = self._mail_secret()
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = secret["address"], to, params["subject"][:300]
        message["Date"] = email.utils.formatdate(localtime=True)
        message["Message-ID"] = email.utils.make_msgid()
        message.set_content(params["body"])
        server = self.smtp_factory(secret["smtp"], int(secret["smtp_port"]), secret["security"])
        try:
            if secret["security"] != "ssl":
                server.starttls()
            server.login(secret["address"], secret["password"])
            server.send_message(message)
        finally:
            try:
                server.quit()
            except Exception:  # noqa: S110 (closing)
                pass
        return {"sent": True, "detail": f"Sent from {secret['address']} to {to}."}
