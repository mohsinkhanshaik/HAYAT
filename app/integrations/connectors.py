"""Capability-scoped personal service connectors and local marketplace."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import yaml

from app.config import PROJECT_ROOT
from app.integrations.mcp_catalog import safe_output
from app.research.safety import validate_public_url


_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ACTION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_./-]{0,127}$")


@dataclass(slots=True)
class ConnectorManifest:
    connector_id: str
    name: str
    transport: str
    endpoint: str = ""
    scopes: list[str] = field(default_factory=list)
    read_actions: list[str] = field(default_factory=list)
    secret_name: str = ""
    enabled: bool = False
    description: str = ""
    source: str = "local"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConnectorManifest":
        return cls(
            connector_id=str(value.get("connector_id") or value.get("id") or ""),
            name=str(value.get("name") or ""), transport=str(value.get("transport") or "http"),
            endpoint=str(value.get("endpoint") or ""), scopes=list(value.get("scopes") or []),
            read_actions=list(value.get("read_actions") or []), secret_name=str(value.get("secret_name") or ""),
            enabled=bool(value.get("enabled", False)), description=str(value.get("description") or ""),
            source=str(value.get("source") or "local"),
        )

    def validate(self) -> None:
        if not _ID.fullmatch(self.connector_id):
            raise ValueError("Connector IDs must be lowercase letters, numbers, dash or underscore.")
        if self.transport not in {"http", "mcp", "browser_extension"}:
            raise ValueError("Connector transport must be http, mcp, or browser_extension.")
        if self.transport == "http":
            validate_public_url(self.endpoint)
        if not self.name.strip():
            raise ValueError("Connector name is required.")
        for action in self.read_actions:
            if not _ACTION.fullmatch(action):
                raise ValueError(f"Invalid connector action: {action}")


class ConnectorRegistry:
    def __init__(self, path: Path | None = None, *, catalog: Path | None = None, event_bus: Any = None) -> None:
        self.path = path or PROJECT_ROOT / "data/integrations/connectors.db"
        self.catalog_path = catalog or PROJECT_ROOT / "config/connectors.yaml"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = event_bus
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS connectors(
                connector_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0, installed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
            )

    def catalog(self, query: str = "") -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            return []
        raw = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8")) or {}
        items = list(raw.get("connectors") or [])
        terms = {item for item in re.findall(r"[a-z0-9]+", query.casefold()) if len(item) > 1}
        if terms:
            items = [item for item in items if terms & set(re.findall(
                r"[a-z0-9]+", " ".join(str(value) for value in item.values()).casefold()))]
        return items

    def install(self, manifest: ConnectorManifest | dict[str, Any], *, enable: bool = False) -> dict[str, Any]:
        item = manifest if isinstance(manifest, ConnectorManifest) else ConnectorManifest.from_dict(manifest)
        item.enabled = bool(enable)
        item.validate()
        now = datetime.now(UTC).isoformat()
        payload = json.dumps(asdict(item), sort_keys=True)
        with self._connect() as db:
            db.execute(
                "INSERT INTO connectors(connector_id,manifest_json,enabled,installed_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(connector_id) DO UPDATE SET manifest_json=excluded.manifest_json,"
                "enabled=excluded.enabled,updated_at=excluded.updated_at",
                (item.connector_id, payload, int(item.enabled), now, now),
            )
        self._event("CONNECTOR_INSTALLED", {"connector_id": item.connector_id, "enabled": item.enabled})
        return self.describe(item.connector_id)

    def install_from_catalog(self, connector_id: str, *, enable: bool = False) -> dict[str, Any]:
        item = next((row for row in self.catalog() if row.get("id") == connector_id), None)
        if item is None:
            raise KeyError(f"Unknown marketplace connector: {connector_id}")
        return self.install(item, enable=enable)

    def set_enabled(self, connector_id: str, enabled: bool) -> dict[str, Any]:
        item = self.get(connector_id)
        item.enabled = bool(enabled)
        item.validate()
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE connectors SET enabled=?,manifest_json=?,updated_at=? WHERE connector_id=?",
                (int(enabled), json.dumps(asdict(item), sort_keys=True), datetime.now(UTC).isoformat(), connector_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(connector_id)
        self._event("CONNECTOR_ENABLED" if enabled else "CONNECTOR_DISABLED", {"connector_id": connector_id})
        return self.describe(connector_id)

    def get(self, connector_id: str) -> ConnectorManifest:
        with self._connect() as db:
            row = db.execute("SELECT manifest_json,enabled FROM connectors WHERE connector_id=?", (connector_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown connector: {connector_id}")
        item = ConnectorManifest.from_dict(json.loads(row["manifest_json"]))
        item.enabled = bool(row["enabled"])
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT connector_id FROM connectors ORDER BY connector_id")]
        return [self.describe(item) for item in ids]

    def describe(self, connector_id: str) -> dict[str, Any]:
        item = self.get(connector_id)
        value = asdict(item)
        value["configured_secret"] = bool(item.secret_name)
        value.pop("secret_name", None)
        return value

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(name, "connector_registry", payload)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


class ConnectorGateway:
    """Execute one declared connector action with least-privilege credentials."""

    MAX_RESPONSE_BYTES = 4 * 1024 * 1024
    CALL_TIMEOUT = 45.0
    RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(self, registry: ConnectorRegistry, secrets: Any = None, *, event_bus: Any = None,
                 zero_cost_only: bool = False,
                 request: Callable[..., httpx.Response] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.registry, self.secrets, self.events = registry, secrets, event_bus
        self.zero_cost_only = zero_cost_only
        self._request = request
        self._clock, self._sleep = clock, sleep

    def _stream_response(self, method, url, *, deadline, **kwargs):
        chunks = bytearray()
        with httpx.stream(method, url, **kwargs) as response:
            length = response.headers.get('content-length', '')
            if length.isdigit() and int(length) > self.MAX_RESPONSE_BYTES:
                raise ValueError('Connector response exceeds the 4 MiB limit; use provider pagination.')
            for chunk in response.iter_bytes():
                if self._clock() >= deadline:
                    raise httpx.ReadTimeout('Connector download exceeded its overall deadline.', request=response.request)
                if len(chunks) + len(chunk) > self.MAX_RESPONSE_BYTES:
                    raise ValueError('Connector response exceeds the 4 MiB limit; use provider pagination.')
                chunks.extend(chunk)
            # iter_bytes has already decoded compression; do not decode it again.
            headers = {key: value for key, value in response.headers.items()
                       if key.lower() not in {'content-encoding', 'content-length'}}
            return httpx.Response(response.status_code, headers=headers, content=bytes(chunks), request=response.request)

    @staticmethod
    def _retry_delay(response, attempt):
        hint = response.headers.get('retry-after') if response is not None else None
        if hint:
            try:
                return max(0.0, float(hint))
            except ValueError:
                try:
                    return max(0.0, (parsedate_to_datetime(hint) - datetime.now(UTC)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return float(2 ** attempt)

    def _exchange(self, url, headers, parameters, write):
        deadline = self._clock() + self.CALL_TIMEOUT
        for attempt in range(1 if write else 3):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise httpx.ReadTimeout('Connector call exceeded its overall deadline.')
            response = None
            try:
                kwargs = dict(headers=headers, json=parameters if write else None,
                              params=None if write else parameters, timeout=min(10.0, remaining), follow_redirects=False)
                response = (self._request('POST' if write else 'GET', url, **kwargs) if self._request
                            else self._stream_response('POST' if write else 'GET', url, deadline=deadline, **kwargs))
                if len(response.content) > self.MAX_RESPONSE_BYTES:
                    raise ValueError('Connector response exceeds the 4 MiB limit; use provider pagination.')
                if write or response.status_code not in self.RETRY_STATUSES or attempt == 2:
                    return response, attempt + 1
            except (httpx.TimeoutException, httpx.NetworkError):
                if write or attempt == 2:
                    raise
            except BaseException:
                if response is not None:
                    response.close()
                raise
            delay = self._retry_delay(response, attempt)
            if response is not None:
                # A long publisher cooldown is reported instead of retried early.
                if delay > min(10.0, deadline - self._clock()):
                    response.close()
                    response.raise_for_status()
                response.close()
            if delay >= deadline - self._clock():
                raise httpx.ReadTimeout('Connector retry would exceed its overall deadline.')
            self._sleep(delay)

    def call(self, connector_id: str, action: str, parameters: dict[str, Any] | None = None,
             *, write: bool = False) -> dict[str, Any]:
        if self.zero_cost_only:
            raise PermissionError("ZERO-COST: external connector billing is unverified. Use the service manually in your browser.")
        connector = self.registry.get(connector_id)
        if not connector.enabled:
            raise PermissionError(f"Connector {connector_id} is installed but disabled.")
        if connector.transport != "http":
            raise ValueError(f"Connector {connector_id} is handled by its {connector.transport} gateway.")
        if not _ACTION.fullmatch(action) or ".." in action:
            raise ValueError("Invalid connector action.")
        declared_read = action in connector.read_actions
        if not write and not declared_read:
            raise PermissionError("That action is not declared read-only; use the approval-gated write path.")
        url = validate_public_url(connector.endpoint.rstrip("/") + "/" + action.lstrip("/"))
        headers = {"Accept": "application/json", "User-Agent": "HAYAT-Connector/1.0"}
        scoped_secrets = []
        if connector.secret_name:
            if self.secrets is None:
                raise RuntimeError("The secret broker is unavailable.")
            scope = f"connector:{connector.connector_id}"
            lease = self.secrets.issue_lease(connector.secret_name, scope)
            credential = self.secrets.redeem(lease, scope)
            scoped_secrets.append(credential)
            headers["Authorization"] = "Bearer " + credential
        response, attempts = self._exchange(url, headers, dict(parameters or {}), write)
        try:
            response.raise_for_status()
            try:
                output: Any = response.json()
            except ValueError:
                output = response.text[:200_000]
            safe = safe_output(output, scoped_secrets)
        finally:
            response.close()
        if self.events is not None:
            self.events.publish("CONNECTOR_CALLED", "connector_gateway", {
                "connector_id": connector_id, "action": action, "write": write,
                "status_code": response.status_code,
                'attempts': attempts,
            })
        return {"connector_id": connector_id, "action": action, "write": write,
                "status_code": response.status_code, 'attempts': attempts, "output": safe}


def register_connector_tools(tool_registry: Any, connectors: ConnectorRegistry,
                             gateway: ConnectorGateway) -> None:
    object_schema = {"type": "object", "additionalProperties": True}
    tool_registry.register(
        "connector_list", "List installed personal-service connectors without exposing credentials.",
        {"type": "object", "properties": {}}, "low", "system_read", lambda: connectors.list(),
    )
    tool_registry.register(
        "connector_marketplace_search", "Search HAYAT's local connector marketplace catalog.",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        "low", "system_read", lambda query="": connectors.catalog(query),
    )
    tool_registry.register(
        "connector_install", "Install a marketplace connector in disabled state for owner review.",
        {"type": "object", "properties": {"connector_id": {"type": "string"}},
         "required": ["connector_id"]}, "high", "automation_manage",
        lambda connector_id: connectors.install_from_catalog(connector_id, enable=False),
    )
    tool_registry.register(
        "connector_enable", "Enable or disable an installed connector after owner approval.",
        {"type": "object", "properties": {"connector_id": {"type": "string"},
         "enabled": {"type": "boolean"}}, "required": ["connector_id", "enabled"]},
        "high", "automation_manage", connectors.set_enabled,
    )
    tool_registry.register(
        "connector_read", "Read through an enabled connector using a declared read-only action.",
        {"type": "object", "properties": {"connector_id": {"type": "string"},
         "action": {"type": "string"}, "parameters": object_schema},
         "required": ["connector_id", "action"]},
        "medium", "connector_read", lambda connector_id, action, parameters=None: gateway.call(
            connector_id, action, parameters, write=False),
    )
    tool_registry.register(
        "connector_action", "Perform an outward-facing connector action after explicit approval.",
        {"type": "object", "properties": {"connector_id": {"type": "string"},
         "action": {"type": "string"}, "parameters": object_schema},
         "required": ["connector_id", "action"]},
        "high", "connector_write", lambda connector_id, action, parameters=None: gateway.call(
            connector_id, action, parameters, write=True),
    )
