"""Private, account-encrypted relationship notes and unsent outreach.

This is an owner-maintained workspace, not a people scraper or a social network.
Entered facts and URLs are attributed to the owner and never represented as
independently verified evidence. Nothing here opens a network connection.
"""
from __future__ import annotations

import base64
from datetime import UTC, date, datetime
import ipaddress
import json
from pathlib import Path
import re
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import uuid

from app.contacts import ContactError, normalize_email, normalize_phone
from app.research.record_vault import dpapi


MAX_RECORDS = 2500
MAX_STORE_BYTES = 24 * 1024 * 1024
CHANNEL_KINDS = ("email", "phone", "profile", "website")
CHANNEL_STATUSES = ("provided", "public_professional", "consent")


def _text(value: Any, field: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{field} is required.")
    if len(value) > limit or any(ord(char) < 32 and char not in "\n\t" for char in value):
        raise ValueError(f"{field} must be at most {limit} characters, without control characters.")
    return value


def _topics(value: Any, field: str) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or len(value) > 40:
        raise ValueError(f"{field} must contain at most 40 topics.")
    result = []
    for item in value:
        topic = _text(item, field, 120)
        if topic and topic.casefold() not in {entry.casefold() for entry in result}:
            result.append(topic)
    return result


def _url(value: Any, field: str = "Source URL", *, required: bool = False) -> str:
    value = _text(value, field, 2000, required=required)
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").rstrip(".").casefold()
        # No requests are made, but avoid exporting local file/credential links.
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            raise ValueError
        if parsed.port not in {None, 80, 443} or any(char.isspace() for char in value) or "\\" in value:
            raise ValueError
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if re.fullmatch(r"[0-9.:]+", host):
                raise ValueError from None
        else:
            if not address.is_global:
                raise ValueError
        if "." not in host:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{field} must be a public HTTP(S) URL without credentials or custom ports.") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _tokens(value: str) -> set[str]:
    return {word for word in re.findall(r"[\w+#.-]+", value.casefold())
            if len(word) > 1 and word not in {"and", "the", "for", "with", "from", "help", "need", "want", "looking", "to", "in", "of", "my", "me"}}


class NetworkingBook:
    """Encrypted local owner store; codec injection is for isolated tests only."""

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, directory: Path | str, *, codec: Any = dpapi, principal: str = "local_owner") -> None:
        if principal != "local_owner":
            raise PermissionError("Only the local owner can open the networking workspace.")
        self.directory = Path(directory)
        self.path = self.directory / "networking.dpapi"
        self.codec = codec
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.directory.resolve()).casefold(), threading.RLock())

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "records": {}, "audit": []}
        if self.path.stat().st_size > MAX_STORE_BYTES:
            raise ValueError("Networking workspace exceeds its safe storage limit.")
        state = json.loads(self.codec(self.path.read_bytes(), decrypt=True))
        if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("records"), dict) or not isinstance(state.get("audit"), list):
            raise ValueError("Networking workspace format is invalid; the existing file was preserved.")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        raw = json.dumps(state, ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_STORE_BYTES:
            raise ValueError("Networking workspace exceeds its safe storage limit.")
        encrypted = self.codec(raw)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".networking-{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_bytes(encrypted)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _audit(state: dict[str, Any], action: str, contact_id: str = "") -> None:
        state["audit"].append({"at": datetime.now(UTC).isoformat(), "action": action, "contact_id": contact_id})
        state["audit"] = state["audit"][-5000:]

    @staticmethod
    def _id(contact_id: str) -> str:
        if not isinstance(contact_id, str) or not re.fullmatch(r"[0-9a-f]{32}", contact_id):
            raise ValueError("Choose a saved contact with a valid ID.")
        return contact_id

    @staticmethod
    def _active(state: dict[str, Any], contact_id: str) -> dict[str, Any]:
        record = state["records"].get(NetworkingBook._id(contact_id))
        if not record:
            raise LookupError("Contact was not found.")
        if record.get("revoked"):
            raise PermissionError("Access to this contact was revoked. Delete it or create a newly authorized record.")
        return record

    def save(self, name: str, organization: str = "", role: str = "", expertise: list[str] | str | None = None,
             goals: list[str] | str | None = None, source_url: str = "", channels: list[dict[str, Any]] | None = None,
             notes: str = "", follow_up: str = "", contact_id: str = "") -> dict[str, Any]:
        """Save owner-entered facts. An update replaces the editable fields in full."""
        now = datetime.now(UTC).isoformat()
        record = {"name": _text(name, "Name", 160, required=True),
                  "organization": _text(organization, "Organization", 200), "role": _text(role, "Role", 200),
                  "expertise": _topics([] if expertise is None else expertise, "Expertise"),
                  "goals": _topics([] if goals is None else goals, "Goals"),
                  "source_url": _url(source_url), "notes": _text(notes, "Notes", 8000),
                  "follow_up": _text(follow_up, "Follow-up date", 10), "channels": [],
                  "basis": "Owner-entered information; public source links are not independently verified.",
                  "source_recorded_at": now if source_url else "", "updated_at": now, "revoked": False}
        if follow_up:
            try:
                if date.fromisoformat(follow_up).isoformat() != follow_up:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("Follow-up date must be a valid YYYY-MM-DD date.") from exc
        if channels is not None and (not isinstance(channels, list) or len(channels) > 12):
            raise ValueError("A contact may have at most 12 contact channels.")
        for channel in channels or []:
            if not isinstance(channel, dict) or set(channel) - {"kind", "value", "status", "source_url", "consent", "recorded_at"}:
                raise ValueError("Contact channels must contain only kind, value, status, source_url and consent.")
            kind, status = channel.get("kind", ""), channel.get("status", "provided")
            if kind not in CHANNEL_KINDS or status not in CHANNEL_STATUSES:
                raise ValueError("Choose a supported contact channel and authorization status.")
            value = _text(channel.get("value", ""), "Contact channel", 2000, required=True)
            source = _url(channel.get("source_url", "") or source_url)
            if status == "public_professional" and not source:
                raise PermissionError("A publicly listed professional channel needs its public source URL.")
            if status == "consent" and channel.get("consent") is not True:
                raise PermissionError("Consent-based contact details require an explicit consent confirmation.")
            try:
                if kind == "email":
                    value = normalize_email(value)
                elif kind == "phone":
                    value = normalize_phone(value)
                else:
                    value = _url(value, "Contact channel", required=True)
            except ContactError as exc:
                raise ValueError(str(exc)) from exc
            clean = {"kind": kind, "value": value, "status": status, "source_url": source,
                     "consent": status == "consent", "recorded_at": now}
            if not any(row["kind"] == kind and row["value"].casefold() == value.casefold() for row in record["channels"]):
                record["channels"].append(clean)
        with self._lock:
            state = self._read()
            if contact_id:
                previous = self._active(state, contact_id)
                record["created_at"] = previous["created_at"]
            else:
                if len(state["records"]) >= MAX_RECORDS:
                    raise ValueError("The workspace is full; delete old records before adding more.")
                contact_id = uuid.uuid4().hex
                record["created_at"] = now
            record["id"] = contact_id
            state["records"][contact_id] = record
            self._audit(state, "contact_saved", contact_id)
            self._write(state)
        return record

    def resolve(self, reference: str) -> str:
        """The ID for a full ID, a unique short ID (6+ characters) or a name only one active contact has.

        Drafting by a 32-character ID was the only way in from chat. A name now works when it is
        unambiguous; namesakes are listed with short IDs and never guessed between.
        """
        if not isinstance(reference, str) or not reference.strip() or re.search(r"[\\/]|\.\.", reference):
            raise ValueError("Choose a saved contact by its name or ID.")
        value = " ".join(reference.split()).strip("\"' ")
        lowered = value.lower()
        if re.fullmatch(r"[0-9a-f]{32}", lowered):
            return lowered
        with self._lock:
            active = [row for row in self._read()["records"].values() if not row["revoked"]]
        if re.fullmatch(r"[0-9a-f]{6,31}", lowered):
            hits = [row for row in active if row["id"].startswith(lowered)]
            if len(hits) == 1:
                return hits[0]["id"]
            if hits:
                raise ValueError("That short ID matches more than one saved contact; use a few more characters.")
        named = [row for row in active if row["name"].casefold() == value.casefold()]
        if len(named) == 1:
            return named[0]["id"]
        if not named:
            raise ValueError(f"No saved contact is named “{value}”. Say “search saved contacts for {value}” "
                              "to see close matches.")
        choices = "; ".join(f"{row['name']}" + (f" | {row['organization']}" if row["organization"] else "")
                            + f" (ID {row['id'][:8]})" for row in named)
        raise ValueError(f"More than one saved contact is named “{value}”: {choices}. Say which one, for example "
                          f"“draft an introduction to saved contact {named[0]['id'][:8]} about …”.")

    def get(self, contact_id: str) -> dict[str, Any]:
        contact_id = self.resolve(contact_id)
        with self._lock:
            return dict(self._active(self._read(), contact_id))

    @staticmethod
    def relevance(record: dict[str, Any], goal: str) -> dict[str, Any]:
        """Explain literal topic overlap, never a forecast of somebody's intentions."""
        wanted = _tokens(goal)
        expertise = [topic for topic in record["expertise"] if wanted & _tokens(topic)]
        goals = [topic for topic in record["goals"] if wanted & _tokens(topic)]
        covered = wanted & _tokens(" ".join([*expertise, *goals]))
        score = round(100 * len(covered) / len(wanted)) if wanted else 0
        return {"score": score, "matched_expertise": expertise, "matched_goals": goals,
                "unmatched_terms": sorted(wanted - covered),
                "explanation": ("Topic overlap with owner-entered expertise and shared goals. "
                                "This is not a probability of help, friendship, hiring or personal compatibility."),
                "evidence_status": "owner_entered_unverified", "source_url": record["source_url"]}

    def all(self, query: str = "", goal: str = "", include_revoked: bool = False) -> list[dict[str, Any]]:
        query, goal = _text(query, "Search", 300).casefold(), _text(goal, "Goal", 1000)
        with self._lock:
            records = list(self._read()["records"].values())
        result = []
        for record in records:
            if record["revoked"]:
                if include_revoked and not query and not goal:
                    result.append({"id": record["id"], "name": "Revoked contact", "revoked": True,
                                   "revoked_at": record.get("revoked_at", "")})
                continue
            searchable = " ".join([record["name"], record["organization"], record["role"], record["notes"],
                                   *record["expertise"], *record["goals"]]).casefold()
            if query and query not in searchable:
                continue
            row = dict(record)
            if goal:
                row["relevance"] = self.relevance(row, goal)
            result.append(row)
        return sorted(result, key=lambda row: (-row.get("relevance", {}).get("score", 0), row["name"].casefold(), row["id"]))

    def match(self, goal: str, limit: int = 20) -> list[dict[str, Any]]:
        goal = _text(goal, "Goal", 1000, required=True)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Match limit must be between 1 and 100.")
        return [row for row in self.all(goal=goal) if row["relevance"]["score"] > 0][:limit]

    def draft(self, contact_id: str, purpose: str, sender: str = "", shared_context: str = "") -> dict[str, Any]:
        purpose = _text(purpose, "Purpose", 1500, required=True)
        sender = _text(sender, "Sender", 160)
        context = _text(shared_context, "Shared context", 1000)
        record = self.get(contact_id)
        contact_id = record["id"]
        relevance = self.relevance(record, purpose)
        lines = [f"Hello {record['name']},", ""]
        if context:
            lines += [context, ""]
        lines += [f"I am reaching out about {purpose.rstrip('.')}." ]
        topics = relevance["matched_expertise"]
        if topics:
            lines += [f"I would be interested in learning about your work on {', '.join(topics)}."]
        lines += ["If this is relevant and you are open to it, would a short conversation be convenient? "
                  "Please feel free to decline.", "", f"Thank you{',' if sender else '.'}"]
        if sender:
            lines.append(sender)
        return {"contact_id": contact_id, "subject": f"A conversation about {purpose}"[:180],
                "body": "\n".join(lines), "channels": record["channels"], "sent": False,
                "review_required": True, "basis": "Draft from owner-entered facts; verify identity and channels before sending.",
                "relevance": relevance}

    def revoke(self, contact_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            record = self._active(state, contact_id)
            record["revoked"], record["revoked_at"] = True, datetime.now(UTC).isoformat()
            self._audit(state, "contact_revoked", contact_id)
            self._write(state)
        return {"id": contact_id, "revoked": True}

    def delete(self, contact_id: str) -> bool:
        self._id(contact_id)
        with self._lock:
            state = self._read()
            removed = state["records"].pop(contact_id, None) is not None
            self._audit(state, "contact_deleted", contact_id)
            self._write(state)
        return removed

    def export(self, contact_id: str = "", plaintext: bool = False) -> dict[str, Any]:
        if type(plaintext) is not bool:
            raise ValueError("Plaintext export must be explicitly true or false.")
        with self._lock:
            state = self._read()
            rows = [dict(self._active(state, contact_id))] if contact_id else [dict(row) for row in state["records"].values() if not row["revoked"]]
            payload = {"version": 1, "exported_at": datetime.now(UTC).isoformat(), "records": rows,
                       "warning": "Sensitive local relationship data. Share only with permission; this export sends nothing."}
            if plaintext:
                result = {"format": "hayat-networking-json-v1", "encrypted": False, **payload}
            else:
                encrypted = self.codec(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                result = {"format": "hayat-networking-dpapi-v1", "encrypted": True,
                          "ciphertext_b64": base64.b64encode(encrypted).decode("ascii"),
                          "warning": "Encrypted for the current Windows account; this export sends nothing."}
            self._audit(state, "plaintext_export" if plaintext else "encrypted_export", contact_id)
            self._write(state)
        return result

    def restore(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Add contacts from an export this account made, never overwriting what is already saved.

        Each record goes through save(), so a restored contact obeys the same rules as one typed in:
        public routes need their source, consent routes their consent. A contact with the same name and
        organization as an active one is skipped and named, not merged.
        """
        if not isinstance(envelope, dict):
            raise ValueError("Choose a HAYAT networking export file.")
        kind = envelope.get("format")
        if kind == "hayat-networking-dpapi-v1":
            try:
                payload = json.loads(self.codec(base64.b64decode(str(envelope.get("ciphertext_b64", ""))), decrypt=True))
            except Exception as exc:
                raise PermissionError("This export cannot be opened by this Windows account, or it is damaged.") from exc
        elif kind == "hayat-networking-json-v1":
            payload = envelope
        else:
            raise ValueError("This is not a HAYAT networking export.")
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list) or len(records) > MAX_RECORDS:
            raise ValueError("The export holds no readable contact list.")
        existing = {(row["name"].casefold(), row["organization"].casefold())
                    for row in self.all()}
        added, skipped, rejected = [], [], []
        for row in records:
            if not isinstance(row, dict):
                rejected.append("an unreadable entry")
                continue
            key = (str(row.get("name", "")).casefold(), str(row.get("organization", "")).casefold())
            if key in existing:
                skipped.append(str(row.get("name", "")))
                continue
            channels = [{field: channel[field] for field in ("kind", "value", "status", "source_url", "consent")
                         if field in channel} for channel in row.get("channels", []) if isinstance(channel, dict)]
            try:
                saved = self.save(row.get("name", ""), row.get("organization", ""), row.get("role", ""),
                                  row.get("expertise") or [], row.get("goals") or [], row.get("source_url", ""),
                                  channels, row.get("notes", ""), row.get("follow_up", ""))
            except (ValueError, PermissionError) as exc:
                rejected.append(f"{row.get('name') or 'unnamed'} ({exc})")
                continue
            existing.add(key)
            added.append(saved["name"])
        return {"restored": len(added), "added": added, "skipped_existing": skipped, "rejected": rejected,
                "basis": "Restored records are re-checked like new entries; existing contacts were not changed."}

    def audit(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["audit"])
