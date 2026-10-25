"""Any tool result, said in plain lines instead of code.

Replies and command-line output used to paste Python dictionaries and JSON
straight to the owner: "Final evidence: {'application': 'notepad', 'pid':
48848, 'running': True}". A purpose-built sentence (app/observed_reply.py)
is still preferred; this is the fallback that every other result gets, so no
answer is ever braces, quotes and True/False.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

#: Keys whose names read badly when merely de-underscored.
_LABELS = {
    "pid": "Process ID", "url": "Web address", "urls": "Web addresses", "id": "ID", "ok": "OK",
    "path": "Location", "dir": "Folder", "ms": "Time (ms)", "elapsed_ms": "Time taken (ms)",
    "sha256": "SHA-256", "md5": "MD5", "ip": "IP address", "gpu": "GPU", "cpu": "CPU", "ram": "RAM",
    "vram": "Video memory", "os": "Operating system", "api": "API", "fps": "Frames per second",
    "num_ctx": "Context window", "tokens": "Tokens", "llm": "Language model", "tts": "Speech output",
    "stt": "Speech input", "ocr": "Text reading (OCR)", "size": "Size", "bytes": "Size",
}
#: Plumbing a person never needs to read.
_HIDDEN = {"schema_version", "handle", "hwnd", "_raw", "raw", "trace_id", "request_id", "revision", "etag",
           "cache_key", "internal", "debug"}
_SIZE_KEYS = {"size", "bytes", "size_bytes", "bytes_downloaded", "file_size"}
_SHARE_KEYS = {"confidence", "score", "probability", "relevance", "similarity"}


@dataclass(frozen=True)
class _Limits:
    depth: int = 2
    items: int = 8
    text: int = 400


_SHORT = _Limits()
#: Reviews where nothing may be cut: a training example before approval, a saved report.
_FULL = _Limits(depth=8, items=10**6, text=10**9)


def label(key: Any) -> str:
    """'target_application' -> 'Target application'."""
    text = str(key)
    if text.lower() in _LABELS:
        return _LABELS[text.lower()]
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).replace("_", " ").replace("-", " ").split()
    if not words:
        return text
    return " ".join([words[0].capitalize(), *[w if w.isupper() else w.lower() for w in words[1:]]])


def _size(value: int) -> str:
    amount = float(value)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{int(amount)} bytes" if unit == "bytes" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} bytes"


def scalar(value: Any, key: str = "", limit: int = _SHORT.text) -> str:
    """One value as a person would write it."""
    if value is None:
        return "not available"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int) and key.lower() in _SIZE_KEYS:
        return _size(value)
    if isinstance(value, float):
        if key.lower() in _SHARE_KEYS and 0 <= value <= 1:
            return f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    text = str(value).strip() if isinstance(value, str) and "\n" in value else " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _item_title(item: Any, limits: _Limits) -> str:
    if isinstance(item, dict):
        # Why an entry failed is the part a person needs: "https://x.onion/ — robots.txt disallows ..."
        why = next((item[key] for key in ("error", "reason", "problem", "message")
                    if isinstance(item.get(key), str) and item.get(key)), None)
        for key in ("title", "name", "label", "summary", "path", "url", "query", "message", "text", "value", "id"):
            if item.get(key) not in (None, ""):
                head = scalar(item[key], key, limits.text)
                extra = item.get("url") if key == "title" and item.get("url") else None
                head = f"{head} ({extra})" if extra else head
                return f"{head} — {scalar(why, '', limits.text)}" if why and why != item[key] else head
        parts = [f"{label(k)}: {scalar(v, str(k), limits.text)}" for k, v in item.items()
                 if k not in _HIDDEN and not isinstance(v, (dict, list))][:3]
        return "; ".join(parts) or "an entry"
    if isinstance(item, (list, tuple)):
        return ", ".join(scalar(part, "", limits.text) for part in item[:4])
    return scalar(item, "", limits.text)


def _lines(value: Any, depth: int, indent: str, limits: _Limits) -> list[str]:
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in _HIDDEN or str(key).startswith("_"):
                continue
            name = label(key)
            if isinstance(item, dict):
                if not item:
                    continue
                if depth >= limits.depth:
                    lines.append(f"{indent}{name}: {_item_title(item, limits)}")
                else:
                    lines.append(f"{indent}{name}:")
                    lines += _lines(item, depth + 1, indent + "  ", limits)
            elif isinstance(item, (list, tuple)):
                if not item:
                    lines.append(f"{indent}{name}: none")
                    continue
                if all(not isinstance(part, (dict, list)) for part in item) and len(item) <= limits.items:
                    lines.append(f"{indent}{name}: " + ", ".join(scalar(part, "", limits.text) for part in item))
                    continue
                lines.append(f"{indent}{name} ({len(item)}):")
                for part in item[:limits.items]:
                    if isinstance(part, dict) and depth < limits.depth and limits is _FULL:
                        lines.append(f"{indent}  •")
                        lines += _lines(part, depth + 1, indent + "    ", limits)
                    else:
                        lines.append(f"{indent}  • {_item_title(part, limits)}")
                if len(item) > limits.items:
                    lines.append(f"{indent}  … and {len(item) - limits.items} more")
            else:
                text = scalar(item, str(key), limits.text)
                if "\n" in text:
                    lines.append(f"{indent}{name}:")
                    rows = text.splitlines()
                    lines += [f"{indent}  {row}" for row in (rows if limits is _FULL else rows[:20])]
                else:
                    lines.append(f"{indent}{name}: {text}")
    elif isinstance(value, (list, tuple)):
        lines += [f"{indent}• {_item_title(part, limits)}" for part in value[:limits.items]]
        if len(value) > limits.items:
            lines.append(f"{indent}… and {len(value) - limits.items} more")
    elif value not in (None, ""):
        lines.append(indent + scalar(value, "", limits.text))
    return lines


def describe(value: Any) -> str:
    """The plain-language form of a tool result: a sentence if one is written for it, labelled lines otherwise."""
    if isinstance(value, str):
        parsed = _parse(value)
        if parsed is None:
            return value
        value = parsed
    if isinstance(value, dict):
        try:
            from app.observed_reply import render

            written = render(value)
        except Exception:
            written = None
        if written:
            return written
        error = value.get("error")
        if isinstance(error, str) and error and len(value) <= 2:
            return f"It failed: {error}"
    return "\n".join(_lines(value, 0, "", _SHORT)) or "Nothing to report."


def describe_full(value: Any) -> str:
    """Every value and list entry in full, for reviews where nothing may be cut."""
    if isinstance(value, str):
        parsed = _parse(value)
        if parsed is None:
            return value
        value = parsed
    return "\n".join(_lines(value, 0, "", _FULL)) or "Nothing to report."


_SCHEMA_TYPES = {"string": "text", "integer": "whole number", "number": "number", "boolean": "yes/no",
                 "array": "list", "object": "group of settings", "null": "nothing"}


def describe_schema(schema: Any) -> str:
    """A tool's input contract as a list a person can read, instead of its JSON schema.

    "input_path (text, required)", "format: one of directory, asar", "limit (whole number, 1 to 50)".
    """
    if not isinstance(schema, dict):
        return describe(schema)
    properties = schema.get("properties") or {}
    if not properties:
        return "No inputs needed."
    required = set(schema.get("required") or ())
    lines = []
    for name, spec in properties.items():
        spec = spec if isinstance(spec, dict) else {}
        kind = spec.get("type")
        kinds = kind if isinstance(kind, list) else [kind] if kind else []
        words = [_SCHEMA_TYPES.get(str(item), str(item)) for item in kinds]
        if "required" not in words:
            words.append("required" if name in required else "optional")
        if "minimum" in spec and "maximum" in spec:
            words.append(f"{spec['minimum']} to {spec['maximum']}")
        elif "minimum" in spec:
            words.append(f"at least {spec['minimum']}")
        elif "maximum" in spec:
            words.append(f"at most {spec['maximum']}")
        if spec.get("maxLength"):
            words.append(f"up to {spec['maxLength']} characters")
        line = f"• {name} ({', '.join(words)})"
        if spec.get("enum"):
            line += ": one of " + ", ".join(str(item) for item in spec["enum"])
        if spec.get("default") is not None:
            line += f"; default {scalar(spec['default'])}"
        if spec.get("description"):
            line += " — " + " ".join(str(spec["description"]).split())
        lines.append(line)
    return "\n".join(lines)


def _parse(text: str) -> Any:
    """A JSON or Python-literal dictionary/list that reached us as text, or None for ordinary prose."""
    stripped = text.strip()
    if not stripped.startswith(("{", "[")):
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    try:
        import ast

        parsed = ast.literal_eval(stripped)
        return parsed if isinstance(parsed, (dict, list)) else None
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
