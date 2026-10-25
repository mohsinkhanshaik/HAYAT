"""Governed Model Context Protocol client gateway.

Servers are explicitly registered, launched without a shell, receive only
their declared secret environment variables, and expose separate read and
write tool paths to HAYAT's authority kernel.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import anyio

from app.config import PROJECT_ROOT
from app.integrations.mcp_catalog import MCPToolError, checked_result, discover_tools, safe_catalog, search_tools
from app.safety.sandbox import ProcessSandbox


_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def register_builtin_web_research(gateway: "MCPGateway", *, enabled: bool = True) -> dict[str, Any]:
    """Register HAYAT's audited project-local read-only research server."""
    from app.integrations.web_research_mcp import READ_ONLY_TOOLS, SERVER_ID

    return gateway.register(MCPServerManifest(
        server_id=SERVER_ID,
        name="HAYAT Zero-Cost Web Research",
        command=sys.executable,
        arguments=["-m", "app.integrations.web_research_mcp"],
        read_only_tools=list(READ_ONLY_TOOLS),
    ), enable=enabled)


@dataclass(slots=True)
class MCPServerManifest:
    server_id: str
    name: str
    command: str
    arguments: list[str] = field(default_factory=list)
    read_only_tools: list[str] = field(default_factory=list)
    secret_environment: dict[str, str] = field(default_factory=dict)
    enabled: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MCPServerManifest":
        return cls(
            server_id=str(value.get("server_id") or value.get("id") or ""),
            name=str(value.get("name") or ""), command=str(value.get("command") or ""),
            arguments=[str(item) for item in value.get("arguments") or []],
            read_only_tools=[str(item) for item in value.get("read_only_tools") or []],
            secret_environment={str(key): str(item) for key, item in (value.get("secret_environment") or {}).items()},
            enabled=bool(value.get("enabled", False)),
        )

    def validate(self) -> None:
        if not _ID.fullmatch(self.server_id):
            raise ValueError("Invalid MCP server ID.")
        if not self.name.strip() or not self.command.strip():
            raise ValueError("MCP server name and command are required.")
        if any("\x00" in item or "\n" in item or "\r" in item for item in [self.command, *self.arguments]):
            raise ValueError("MCP commands may not contain control characters.")
        if any(not _ENV.fullmatch(name) for name in self.secret_environment):
            raise ValueError("MCP secret environment names must be uppercase identifiers.")


class MCPGateway:
    def __init__(self, path: Path | None = None, *, secrets: Any = None, event_bus: Any = None,
                 zero_cost_only: bool = False) -> None:
        self.zero_cost_only = zero_cost_only
        self.path = path or PROJECT_ROOT / "data/integrations/mcp.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets, self.events = secrets, event_bus
        self.sandbox = ProcessSandbox()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS mcp_servers(
                server_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0, registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
            )

    def register(self, manifest: MCPServerManifest | dict[str, Any], *, enable: bool = False) -> dict[str, Any]:
        item = manifest if isinstance(manifest, MCPServerManifest) else MCPServerManifest.from_dict(manifest)
        item.enabled = bool(enable)
        item.validate()
        executable = shutil.which(item.command) if not Path(item.command).is_absolute() else item.command
        if executable is None or not Path(executable).is_file():
            raise FileNotFoundError(f"MCP executable is not installed: {item.command}")
        item.command = str(Path(executable).resolve())
        now = datetime.now(UTC).isoformat()
        with self._connect() as db:
            db.execute(
                "INSERT INTO mcp_servers(server_id,manifest_json,enabled,registered_at,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(server_id) DO UPDATE SET manifest_json=excluded.manifest_json,"
                "enabled=excluded.enabled,updated_at=excluded.updated_at",
                (item.server_id, json.dumps(asdict(item), sort_keys=True), int(item.enabled), now, now),
            )
        self._event("MCP_SERVER_REGISTERED", {"server_id": item.server_id, "enabled": item.enabled})
        return self.describe(item.server_id)

    def get(self, server_id: str) -> MCPServerManifest:
        with self._connect() as db:
            row = db.execute("SELECT manifest_json,enabled FROM mcp_servers WHERE server_id=?", (server_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown MCP server: {server_id}")
        item = MCPServerManifest.from_dict(json.loads(row["manifest_json"]))
        item.enabled = bool(row["enabled"])
        return item

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT server_id FROM mcp_servers ORDER BY server_id")]
        return [self.describe(item) for item in ids]

    def describe(self, server_id: str) -> dict[str, Any]:
        item = self.get(server_id)
        return {
            "server_id": item.server_id, "name": item.name, "command": item.command,
            "arguments": item.arguments, "read_only_tools": item.read_only_tools,
            "secret_environment": sorted(item.secret_environment), "enabled": item.enabled,
        }

    def set_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        item = self.get(server_id)
        item.enabled = bool(enabled)
        with self._connect() as db:
            db.execute("UPDATE mcp_servers SET manifest_json=?,enabled=?,updated_at=? WHERE server_id=?",
                       (json.dumps(asdict(item), sort_keys=True), int(enabled), datetime.now(UTC).isoformat(), server_id))
        return self.describe(server_id)

    def tools(self, server_id: str, query: str = '', limit: int | None = None) -> list[dict[str, Any]]:
        search_tools([], query, limit)  # validate before starting a server
        item, environment = self._prepared(server_id)
        catalog = anyio.run(self._tools_async, (item, environment))
        return safe_catalog(search_tools(catalog, query, limit), [environment[name] for name in item.secret_environment])

    def call(self, server_id: str, tool: str, arguments: dict[str, Any] | None = None,
             *, write: bool = False) -> dict[str, Any]:
        item, environment = self._prepared(server_id)
        if not write and tool not in item.read_only_tools:
            raise PermissionError("That MCP tool is not declared read-only; use the approval-gated write path.")
        output = anyio.run(self._call_async, (item, environment), tool, dict(arguments or {}))
        try:
            safe = checked_result(output, extra_secrets=[environment[name] for name in item.secret_environment])
        except MCPToolError:
            self._event('MCP_TOOL_CALLED', {'server_id': server_id, 'tool': tool, 'write': write, 'success': False})
            raise
        self._event('MCP_TOOL_CALLED', {'server_id': server_id, 'tool': tool, 'write': write, 'success': True})
        return {"server_id": server_id, "tool": tool, "write": write, "output": safe}

    def _prepared(self, server_id: str) -> tuple[MCPServerManifest, dict[str, str]]:
        item = self.get(server_id)
        if self.zero_cost_only:
            from app.integrations.web_research_mcp import READ_ONLY_TOOLS, SERVER_ID
            if (item.server_id != SERVER_ID or item.command != sys.executable
                    or item.arguments != ["-m", "app.integrations.web_research_mcp"]
                    or item.secret_environment or not set(item.read_only_tools) <= set(READ_ONLY_TOOLS)):
                raise PermissionError("ZERO-COST: only the built-in public research MCP is admitted; use other services manually.")
        if not item.enabled:
            raise PermissionError(f"MCP server {server_id} is disabled.")
        extra: dict[str, str] = {}
        if item.secret_environment:
            if self.secrets is None:
                raise RuntimeError("The secret broker is unavailable.")
            scope = f"mcp:{item.server_id}"
            for environment_name, secret_name in item.secret_environment.items():
                lease = self.secrets.issue_lease(secret_name, scope)
                extra[environment_name] = self.secrets.redeem(lease, scope)
        return item, self.sandbox.environment(extra)

    @staticmethod
    async def _tools_async(prepared: tuple[MCPServerManifest, dict[str, str]]) -> list[dict[str, Any]]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        item, environment = prepared
        parameters = StdioServerParameters(command=item.command, args=item.arguments, env=environment)
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    return await discover_tools(session)

    @staticmethod
    async def _call_async(prepared: tuple[MCPServerManifest, dict[str, str]], tool: str,
                          arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        item, environment = prepared
        parameters = StdioServerParameters(command=item.command, args=item.arguments, env=environment)
        with anyio.fail_after(60):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
                    return result.model_dump(mode="json")

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(name, "mcp_gateway", payload)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def register_mcp_tools(registry: Any, gateway: MCPGateway) -> None:
    registry.register(
        "mcp_servers", "List registered MCP servers without exposing secret values.",
        {"type": "object", "properties": {}}, "low", "system_read", lambda: gateway.list(),
    )
    registry.register(
        "mcp_register_server", "Register a local MCP stdio server in disabled state for owner review.",
        {"type": "object", "properties": {"server_id": {"type": "string"}, "name": {"type": "string"},
         "command": {"type": "string"}, "arguments": {"type": "array", "items": {"type": "string"}},
         "read_only_tools": {"type": "array", "items": {"type": "string"}},
         "secret_environment": {"type": "object", "additionalProperties": {"type": "string"}}},
         "required": ["server_id", "name", "command"]}, "high", "automation_manage",
        lambda server_id, name, command, arguments=None, read_only_tools=None, secret_environment=None:
        gateway.register(MCPServerManifest(server_id, name, command, arguments or [],
                                           read_only_tools or [], secret_environment or {}, False), enable=False),
    )
    registry.register(
        "mcp_enable_server", "Enable or disable a registered MCP server after owner approval.",
        {"type": "object", "properties": {"server_id": {"type": "string"},
         "enabled": {"type": "boolean"}}, "required": ["server_id", "enabled"]},
        "high", "automation_manage", gateway.set_enabled,
    )
    registry.register(
        "mcp_tools", "Discover all pages of an enabled MCP server's tools; optionally search names/descriptions and limit results.",
        {"type": "object", "properties": {"server_id": {"type": "string"}, 'query': {'type': 'string'},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 2000}}, "required": ["server_id"]},
        "medium", "mcp_read", gateway.tools,
    )
    schema = {"type": "object", "properties": {"server_id": {"type": "string"},
              "tool": {"type": "string"}, "arguments": {"type": "object", "additionalProperties": True}},
              "required": ["server_id", "tool"]}
    registry.register("mcp_read_tool", "Call a manifest-declared read-only MCP tool.", schema, "medium", "mcp_read",
                      lambda server_id, tool, arguments=None: gateway.call(server_id, tool, arguments, write=False))
    registry.register("mcp_write_tool", "Call a mutating MCP tool after exact-action approval.", schema, "high", "mcp_write",
                      lambda server_id, tool, arguments=None: gateway.call(server_id, tool, arguments, write=True))
    registry.register(
        "mcp_zero_cost_web_research",
        "Use HAYAT's built-in read-only MCP server for bounded zero-API-cost public web search and evidence extraction.",
        {"type": "object", "properties": {
            "query": {"type": "string"}, "max_queries": {"type": "integer", "minimum": 1, "maximum": 4},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 8},
        }, "required": ["query"]},
        "medium", "mcp_read",
        lambda query, max_queries=3, max_pages=5: gateway.call(
            "zero-cost-web-research", "deep_web_research",
            {"query": query, "max_queries": max_queries, "max_pages": max_pages},
            write=False,
        ),
    )
