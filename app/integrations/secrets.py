"""Portable secrets with scoped, expiring, single-use leases.

Secret material never enters SQLite, events, model context, tool output, or the
connector catalog.  SQLite holds only identifiers and scope metadata; the
value lives in the host keyring when one is usable. Headless/minimal systems
fall back to an encrypted, permission-restricted store inside ``data/security``.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol

from app.config import PROJECT_ROOT


_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")


class SecretBackend(Protocol):
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def get_password(self, service: str, username: str) -> str | None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class KeyringBackend:
    def set_password(self, service: str, username: str, password: str) -> None:
        import keyring
        keyring.set_password(service, username, password)

    def get_password(self, service: str, username: str) -> str | None:
        import keyring
        return keyring.get_password(service, username)

    def delete_password(self, service: str, username: str) -> None:
        import keyring
        try:
            keyring.delete_password(service, username)
        except keyring.errors.PasswordDeleteError:
            pass


class EncryptedFileBackend:
    """Fernet-encrypted fallback for hosts without a desktop keyring.

    ``HAYAT_SECRET_MASTER_KEY`` can provide the key without writing it to disk.
    Otherwise a random, owner-readable key is kept beside the ciphertext. That
    keeps secrets out of logs/databases/plaintext while making a headless source
    folder operational; filesystem permissions remain the local trust boundary.
    """

    def __init__(self, directory: Path) -> None:
        self.path = Path(directory) / "portable-secrets.enc"
        self.key_path = Path(directory) / "portable-secrets.key"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _key(self) -> bytes:
        supplied = os.environ.get("HAYAT_SECRET_MASTER_KEY", "").encode("utf-8")
        if supplied:
            return base64.urlsafe_b64encode(hashlib.sha256(supplied).digest())
        if not self.key_path.exists():
            from cryptography.fernet import Fernet

            self.key_path.write_bytes(Fernet.generate_key())
            try:
                self.key_path.chmod(0o600)
            except OSError:
                pass
        return self.key_path.read_bytes().strip()

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        from cryptography.fernet import Fernet, InvalidToken

        try:
            raw = Fernet(self._key()).decrypt(self.path.read_bytes())
            value = json.loads(raw.decode("utf-8"))
            return {str(key): str(item) for key, item in value.items()}
        except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("Portable secret store cannot be decrypted on this host.") from exc

    def _write(self, values: dict[str, str]) -> None:
        from cryptography.fernet import Fernet

        payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(Fernet(self._key()).encrypt(payload))
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.path)

    @staticmethod
    def _identifier(service: str, username: str) -> str:
        return f"{service}\0{username}"

    def set_password(self, service: str, username: str, password: str) -> None:
        with self._lock:
            values = self._read()
            values[self._identifier(service, username)] = password
            self._write(values)

    def get_password(self, service: str, username: str) -> str | None:
        with self._lock:
            return self._read().get(self._identifier(service, username))

    def delete_password(self, service: str, username: str) -> None:
        with self._lock:
            values = self._read()
            values.pop(self._identifier(service, username), None)
            self._write(values)


class FallbackSecretBackend:
    """Prefer the native keyring; degrade to encrypted local storage."""

    def __init__(self, primary: SecretBackend, fallback: SecretBackend) -> None:
        self.primary, self.fallback = primary, fallback

    def set_password(self, service: str, username: str, password: str) -> None:
        try:
            self.primary.set_password(service, username, password)
        except Exception:
            self.fallback.set_password(service, username, password)

    def get_password(self, service: str, username: str) -> str | None:
        try:
            value = self.primary.get_password(service, username)
        except Exception:
            value = None
        return value if value is not None else self.fallback.get_password(service, username)

    def delete_password(self, service: str, username: str) -> None:
        try:
            self.primary.delete_password(service, username)
        except Exception:
            # An unavailable keyring never held it (the secret went to the
            # fallback). A keyring that still returns it failed to delete, and
            # that must not be reported as a deletion.
            try:
                remaining = self.primary.get_password(service, username)
            except Exception:
                remaining = None
            if remaining is not None:
                self.fallback.delete_password(service, username)
                raise
        self.fallback.delete_password(service, username)


@dataclass(slots=True)
class _Lease:
    secret_name: str
    scope: str
    expires_at: float
    used: bool = False


class SecretBroker:
    """Broker secret values to trusted integration code, never to the model."""

    def __init__(self, path: Path | None = None, *, backend: SecretBackend | None = None,
                 event_bus: Any = None, service: str = "HAYAT") -> None:
        self.path = path or PROJECT_ROOT / "data/security/secrets.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = backend or FallbackSecretBackend(
            KeyringBackend(), EncryptedFileBackend(self.path.parent))
        self.events = event_bus
        self.service = service
        self._leases: dict[str, _Lease] = {}
        self._lock = threading.RLock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS secret_metadata(
                name TEXT PRIMARY KEY, scopes TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, expires_at TEXT, value_digest TEXT NOT NULL)"""
            )

    def store(self, name: str, value: str, *, scopes: list[str] | tuple[str, ...],
              expires_at: str | None = None) -> dict[str, Any]:
        normalized = self._valid_name(name)
        clean_scopes = sorted({self._valid_scope(item) for item in scopes})
        if not value or not clean_scopes:
            raise ValueError("A non-empty value and at least one scope are required.")
        self.backend.set_password(self.service, normalized, value)
        now = datetime.now(UTC).isoformat()
        digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:12]
        with self._connect() as db:
            db.execute(
                "INSERT INTO secret_metadata(name,scopes,created_at,updated_at,expires_at,value_digest) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET scopes=excluded.scopes,"
                "updated_at=excluded.updated_at,expires_at=excluded.expires_at,value_digest=excluded.value_digest",
                (normalized, "\n".join(clean_scopes), now, now, expires_at, digest),
            )
        self._event("SECRET_STORED", {"name": normalized, "scopes": clean_scopes, "digest": digest})
        return {"name": normalized, "scopes": clean_scopes, "stored": True, "expires_at": expires_at}

    def issue_lease(self, name: str, scope: str, *, ttl_seconds: int = 30) -> str:
        normalized, requested = self._valid_name(name), self._valid_scope(scope)
        metadata = self._metadata(normalized)
        if metadata is None:
            raise KeyError(f"Unknown secret: {normalized}")
        if requested not in metadata["scopes"]:
            raise PermissionError(f"Secret {normalized} is not permitted for scope {requested}.")
        if metadata.get("expired"):
            raise PermissionError(f"Secret {normalized} has expired.")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge()
            self._leases[token] = _Lease(normalized, requested, time.monotonic() + max(1, min(ttl_seconds, 300)))
        self._event("SECRET_LEASE_ISSUED", {"name": normalized, "scope": requested, "ttl_seconds": ttl_seconds})
        return token

    def redeem(self, lease_token: str, scope: str) -> str:
        requested = self._valid_scope(scope)
        with self._lock:
            lease = self._leases.get(lease_token)
            if lease is None or lease.used or lease.expires_at < time.monotonic():
                self._leases.pop(lease_token, None)
                raise PermissionError("Secret lease is invalid, expired, or already used.")
            if lease.scope != requested:
                raise PermissionError("Secret lease scope does not match this operation.")
            lease.used = True
            self._leases.pop(lease_token, None)
        value = self.backend.get_password(self.service, lease.secret_name)
        if value is None:
            raise RuntimeError("The credential entry is unavailable on this host.")
        self._event("SECRET_LEASE_USED", {"name": lease.secret_name, "scope": requested})
        return value

    def delete(self, name: str) -> bool:
        normalized = self._valid_name(name)
        existed = self._metadata(normalized) is not None
        self.backend.delete_password(self.service, normalized)
        with self._connect() as db:
            db.execute("DELETE FROM secret_metadata WHERE name=?", (normalized,))
        if existed:
            self._event("SECRET_DELETED", {"name": normalized})
        return existed

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM secret_metadata ORDER BY name").fetchall()
        return [self._public(row) for row in rows]

    def status(self) -> dict[str, Any]:
        items = self.list()
        return {"backend": type(self.backend).__name__, "stored": len(items), "secrets": items,
                "active_leases": len(self._leases), "values_exposed": False}

    def _metadata(self, name: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM secret_metadata WHERE name=?", (name,)).fetchone()
        return self._public(row) if row else None

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        expires = row["expires_at"]
        expired = False
        if expires:
            try:
                expired = datetime.fromisoformat(expires).astimezone(UTC) <= datetime.now(UTC)
            except ValueError:
                expired = True
        return {"name": row["name"], "scopes": row["scopes"].splitlines(),
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "expires_at": expires, "expired": expired, "digest": row["value_digest"]}

    @staticmethod
    def _valid_name(value: str) -> str:
        name = str(value or "").strip()
        if not _NAME.fullmatch(name):
            raise ValueError("Secret names may contain letters, numbers, dot, colon, underscore and dash.")
        return name

    @staticmethod
    def _valid_scope(value: str) -> str:
        scope = str(value or "").strip().casefold()
        if not _NAME.fullmatch(scope):
            raise ValueError("Invalid secret scope.")
        return scope

    def _purge(self) -> None:
        now = time.monotonic()
        for token in [token for token, lease in self._leases.items() if lease.used or lease.expires_at < now]:
            self._leases.pop(token, None)

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(name, "secret_broker", payload)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def register_secret_tools(registry: Any, broker: SecretBroker) -> None:
    # Deliberately no model-callable get/store tool. Secret entry belongs to a
    # local setup surface; models can inspect only redacted metadata.
    registry.register("secret_broker_status", "Show secret names, scopes and expiry without values.",
                      {"type": "object", "properties": {}}, "low", "system_read", broker.status)
