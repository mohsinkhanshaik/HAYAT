"""The people HAYAT can message on the owner's behalf.

"Message mom" needs to know who mom is. The book is local SQLite under
data/contacts/, never synced and never committed: it holds phone numbers and
email addresses, which are other people's personal data.

A number is stored in international form. WhatsApp needs the country code,
and guessing one for a local number would message the wrong person, so a
number without it is refused with the reason rather than stored.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT


class ContactError(LookupError):
    """A contact could not be saved or found, with a reason worth saying."""


def normalize_phone(phone: str) -> str:
    """"+44 7700 900123" or "0044 7700 900123" -> "+447700900123"."""
    raw = str(phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        pass
    elif raw.startswith("00"):
        digits = digits[2:]
    else:
        raise ContactError(
            f"{raw or 'That number'} has no country code. Give it as +<country code><number>, "
            "for example +44 7700 900123, so the message cannot reach the wrong person."
        )
    if not 8 <= len(digits) <= 15:
        raise ContactError(f"{raw} is not a valid international phone number.")
    return "+" + digits


def normalize_email(email: str) -> str:
    value = str(email or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ContactError(f"{value or 'That'} is not an email address.")
    return value


def _key(name: str) -> str:
    """"My Mom", "mom", "Mom's" -> "mom"."""
    value = " ".join(str(name or "").casefold().split())
    value = re.sub(r"^(?:my|our)\s+", "", value)
    return re.sub(r"'s$", "", value).strip(" .,!?")


class ContactBook:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else PROJECT_ROOT / "data" / "contacts" / "contacts.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    aliases TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)

    def save(self, name: str, phone: str = "", email: str = "", aliases: list[str] | str = "") -> dict[str, Any]:
        key = _key(name)
        if not key:
            raise ContactError("A contact needs a name.")
        existing = self._get(key) or {}
        phone = normalize_phone(phone) if phone else existing.get("phone", "")
        email = normalize_email(email) if email else existing.get("email", "")
        if not (phone or email):
            raise ContactError(f"Give {name} a phone number or an email address.")
        names = aliases.split(",") if isinstance(aliases, str) else list(aliases or [])
        merged = sorted({_key(item) for item in [*names, *existing.get("aliases", [])] if _key(item)} - {key})
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO contacts (key, name, phone, email, aliases, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET name=excluded.name, phone=excluded.phone, email=excluded.email, "
                "aliases=excluded.aliases, updated_at=excluded.updated_at",
                (key, " ".join(str(name).split()), phone, email, ",".join(merged), datetime.now(UTC).isoformat()),
            )
        return self._get(key) or {}

    def find(self, name: str) -> dict[str, Any]:
        """The one contact this name means, or a ContactError saying why not."""
        key = _key(name)
        if not key:
            raise ContactError("Who should it go to?")
        contacts = self.all()
        exact = [item for item in contacts if key == item["key"] or key in item["aliases"]]
        if len(exact) == 1:
            return exact[0]
        partial = [item for item in contacts
                   if any(part == key for part in item["key"].split()) or item["key"].startswith(key + " ")]
        if len(partial) == 1:
            return partial[0]
        if len(exact) > 1 or len(partial) > 1:
            names = ", ".join(item["name"] for item in (exact or partial))
            raise ContactError(f"'{name}' could be {names}. Which one?")
        raise ContactError(
            f"I don't have {name} in your contacts. Tell me their number with the country code, "
            f"for example: save {name}'s number as +44 7700 900123."
        )

    def remove(self, name: str) -> bool:
        contact = self.find(name)
        with self._lock, self._db() as db:
            return db.execute("DELETE FROM contacts WHERE key = ?", (contact["key"],)).rowcount > 0

    def all(self) -> list[dict[str, Any]]:
        with self._lock, self._db() as db:
            rows = db.execute("SELECT key, name, phone, email, aliases FROM contacts ORDER BY name").fetchall()
        return [self._row(row) for row in rows]

    def _get(self, key: str) -> dict[str, Any] | None:
        with self._lock, self._db() as db:
            row = db.execute("SELECT key, name, phone, email, aliases FROM contacts WHERE key = ?", (key,)).fetchone()
        return self._row(row) if row else None

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        key, name, phone, email, aliases = row
        return {"key": key, "name": name, "phone": phone, "email": email,
                "aliases": [item for item in aliases.split(",") if item]}

    @contextmanager
    def _db(self) -> Any:
        db = sqlite3.connect(self.path, timeout=15.0)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


def register_contact_tools(registry: Any, book: ContactBook) -> None:
    obj = lambda props, required=None: {"type": "object", "properties": props,
                                        "required": required or [], "additionalProperties": False}
    registry.register(
        "contact_save", "Save or update a contact's phone number (with country code) and/or email, with optional aliases such as 'mom'.",
        obj({"name": {"type": "string"}, "phone": {"type": "string"}, "email": {"type": "string"},
             "aliases": {"type": "array", "items": {"type": "string"}}}, ["name"]),
        "low", "filesystem_write_project",
        lambda name, phone="", email="", aliases=None: book.save(name, phone, email, aliases or []),
    )
    registry.register(
        "contact_find", "Look up one contact by name or alias.",
        obj({"name": {"type": "string"}}, ["name"]), "low", "filesystem_read", book.find,
    )
    registry.register(
        "contact_list", "List the contacts saved in HAYAT's own address book, with their email addresses and phone numbers.", obj({}), "low", "filesystem_read", lambda: {"contacts": book.all()},
    )
    registry.register(
        "contact_delete", "Remove one saved contact.", obj({"name": {"type": "string"}}, ["name"]),
        "medium", "filesystem_write_project", lambda name: {"removed": book.remove(name)},
    )
