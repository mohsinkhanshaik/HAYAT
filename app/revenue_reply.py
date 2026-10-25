"""Deliver verified revenue results in chat without a second model call."""
from __future__ import annotations

from typing import Any

from app.revenue_catalog import BY_NAME


def _value(value: Any) -> str:
    if value is None:
        return 'Unknown / unavailable'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def _details(value: Any, depth: int = 0) -> list[str]:
    indent = '  ' * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            label = str(key).replace('_', ' ').capitalize()
            if isinstance(item, (dict, list)) and item:
                lines.append(f'{indent}{label}:')
                lines.extend(_details(item, depth + 1))
            else:
                lines.append(f'{indent}{label}: {_value(item) if item not in ([], {}) else "None recorded"}')
        return lines
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value[:10], 1):
            if isinstance(item, (dict, list)):
                lines.append(f'{indent}{index}.')
                lines.extend(_details(item, depth + 1))
            else:
                lines.append(f'{indent}- {_value(item)}')
        if len(value) > 10:
            lines.append(f'{indent}… {len(value) - 10} more; open Revenue workspace for records.')
        return lines
    return [indent + _value(value)]


def report(history: list[dict[str, Any]]) -> str | None:
    sections = []
    for event in history or []:
        name = event.get('tool')
        result = event.get('result') or {}
        output = result.get('output')
        if (name not in BY_NAME and name != 'revenue_tool_catalog') or not isinstance(output, dict):
            continue
        if not result.get('success') or not (event.get('verification') or {}).get('passed'):
            continue
        if name == 'revenue_tool_catalog':
            section = (f"{output.get('count', 0)} revenue workflows are available. "
                       'Open Chat actions → Revenue workspace to search them and prepare a request.\n'
                       + str(output.get('outbound', '')))
        elif output.get('text'):
            # Keep the full artifact in its saved file, and its opening in the chat.
            preview = str(output.get('preview') or '').strip()
            section = (preview[:4500] + ('\n… Full draft is in the saved file.' if len(preview) > 4500 else '')
                       + '\n\n' if preview else '') + str(output['text'])
            for field in ('validation', 'missing_fields', 'limits', 'gaps'):
                if output.get(field):
                    section += '\n' + '\n'.join(_details({field: output[field]}))
        else:
            from app.observed_reply import render
            section = render(output) or (' '.join(str(name).split('_')).capitalize() + '\n'
                                         + '\n'.join(_details(output)))
            if len(section) > 7000:
                section = section[:7000] + '\n… Open Revenue workspace for the saved records.'
        sections.append(section)
    return '\n\n'.join(sections) or None
