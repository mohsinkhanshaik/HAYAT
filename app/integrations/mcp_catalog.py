"""Complete, bounded MCP discovery shared by the generic gateway and REA."""
from __future__ import annotations

import json
from typing import Any

from app.safety.sensitive import Redactor, redact, redact_text


class MCPCatalogError(RuntimeError):
    pass


class MCPToolError(RuntimeError):
    """The server answered, but its tool did not complete successfully."""


async def discover_tools(session: Any, *, max_pages: int = 100, max_tools: int = 2000,
                         max_bytes: int = 8 * 1024 * 1024) -> list[dict[str, Any]]:
    from mcp.types import PaginatedRequestParams

    tools: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    cursor = None
    size = 0
    for _ in range(max_pages):
        page = await session.list_tools(**({'params': PaginatedRequestParams(cursor=cursor)} if cursor else {}))
        for tool in page.tools:
            item = tool.model_dump(mode='json')
            name = item.get('name')
            if not isinstance(name, str) or not name:
                raise MCPCatalogError('MCP discovery returned a tool without a name.')
            if name in tools:
                if tools[name] != item:
                    raise MCPCatalogError('MCP discovery returned conflicting definitions for one tool.')
                continue
            size += len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            if len(tools) >= max_tools or size > max_bytes:
                raise MCPCatalogError('MCP tool catalog exceeds the discovery limit; use a smaller server catalog.')
            tools[name] = item
        cursor = getattr(page, 'next_cursor', None) or getattr(page, 'nextCursor', None)
        if not cursor:
            return list(tools.values())
        if not isinstance(cursor, str) or cursor in seen:
            raise MCPCatalogError('MCP discovery repeated an invalid pagination cursor.')
        seen.add(cursor)
    raise MCPCatalogError('MCP discovery exceeded its page limit before completing the tool catalog.')


def safe_output(payload: Any, extra_secrets=()) -> Any:
    """Also remove literal scoped credentials, even for nonstandard token formats."""
    def scoped(value):
        if isinstance(value, str):
            return redact_text(value, extra_values=extra_secrets)[0]
        if isinstance(value, dict):
            return {key: scoped(item) for key, item in value.items()}
        if isinstance(value, list):
            return [scoped(item) for item in value]
        return value
    return scoped(redact(payload)) if extra_secrets else redact(payload)


def safe_catalog(payload: Any, extra_secrets=()) -> Any:
    """Redact catalog text without destroying schemas for token/password inputs."""
    redactor = Redactor()
    def clean(value):
        if isinstance(value, str):
            return redact_text(redactor.text(value), extra_values=extra_secrets)[0]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    return clean(payload)


def checked_result(payload: dict[str, Any], *, extra_secrets=()) -> dict[str, Any]:
    if len(json.dumps(payload).encode('utf-8')) > 8 * 1024 * 1024:
        raise MCPToolError('MCP tool result exceeds the 8 MiB limit; request a smaller result.')
    safe = safe_output(payload, extra_secrets)
    if payload.get('isError', payload.get('is_error', False)):
        parts = [str(block.get('text', '')) for block in safe.get('content', [])
                 if isinstance(block, dict) and block.get('type') == 'text']
        detail = '\n'.join(parts).strip()[:2000] or 'The MCP server reported a tool error.'
        raise MCPToolError(detail)
    return safe


def search_tools(tools: list[dict[str, Any]], query: str = '', limit: int | None = None) -> list[dict[str, Any]]:
    """Rank names before descriptions; a blank query preserves server order."""
    import re

    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000):
        raise ValueError('Tool discovery limit must be between 1 and 2000.')
    terms = set(re.findall(r'\w+', query.casefold()))
    if terms:
        def score(tool):
            name = str(tool['name']).casefold().replace('_', ' ').replace('.', ' ').replace('-', ' ')
            description = str(tool.get('description') or '').casefold()
            return sum(4 * (term in name) + (term in description) for term in terms)
        tools = sorted((item for item in tools if score(item)), key=lambda item: -score(item))
    return tools[:limit]
