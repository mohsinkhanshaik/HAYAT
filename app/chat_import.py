"""Bringing the owner's conversations with other assistants into HAYAT's memory.

Venice.ai lets its users import memories from other AI assistants. Mohsin has used ChatGPT, Claude and others,
and what he discussed there is part of what HAYAT should know about him. Both services let a user download
their own data, and both exports contain a ``conversations.json``:

* **ChatGPT** (Settings → Data controls → Export data): a list of conversations, each with a ``title``,
  ``create_time`` and a ``mapping`` of message nodes (``author.role``, ``content.parts``).
* **Claude** (Settings → Privacy → Export data): a list of conversations, each with a ``name``, ``created_at``
  and ``chat_messages`` (``sender`` "human"/"assistant", ``text``).

:func:`import_conversations` reads the file on this computer (nothing is uploaded) and keeps one memory per
conversation: where it came from, its title and date, and what the owner asked in it, at most 1,500 characters.
The assistant's replies are not kept as facts, because another model's answers are not evidence. Each memory
is episodic, so recall can find it and the memory network links it to conversations with the same purpose. The
same conversation is never imported twice, and secrets are redacted on the way in as with every memory.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

MAX_FILE_BYTES = 512 * 1024 * 1024
MEMORY_CHARS = 1500


def _when(value: Any) -> str:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), UTC).date().isoformat()
        if isinstance(value, str) and value:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return ""


def _chatgpt(conversation: dict[str, Any]) -> tuple[str, str, str, list[str]] | None:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return None
    messages = []
    for node in mapping.values():
        message = (node or {}).get("message") or {}
        if ((message.get("author") or {}).get("role")) != "user":
            continue
        parts = (message.get("content") or {}).get("parts") or []
        text = " ".join(str(part) for part in parts if isinstance(part, str)).strip()
        if text:
            messages.append((message.get("create_time") or 0, text))
    messages.sort(key=lambda item: item[0] or 0)
    return ("ChatGPT", str(conversation.get("id") or conversation.get("conversation_id") or ""),
            str(conversation.get("title") or "Untitled"), [text for _, text in messages]) if messages else None


def _claude(conversation: dict[str, Any]) -> tuple[str, str, str, list[str]] | None:
    messages = conversation.get("chat_messages")
    if not isinstance(messages, list):
        return None
    asked = []
    for message in messages:
        if isinstance(message, dict) and message.get("sender") == "human":
            text = str(message.get("text") or "").strip()
            if not text:
                text = " ".join(str(part.get("text") or "") for part in message.get("content") or []
                                if isinstance(part, dict)).strip()
            if text:
                asked.append(text)
    return ("Claude", str(conversation.get("uuid") or ""), str(conversation.get("name") or "Untitled"),
            asked) if asked else None


def read_export(path: Path | str) -> Iterator[dict[str, Any]]:
    """Each conversation in a ChatGPT or Claude export, as {origin, id, title, date, asked}."""
    file = Path(path)
    if file.is_dir():
        file = file / "conversations.json"
    if not file.is_file():
        raise FileNotFoundError(f"No conversations.json at {file}. Unzip the export and choose its folder.")
    if file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("That export is larger than 512 MB; split it before importing.")
    data = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("This is not a ChatGPT or Claude conversations.json (expected a list of conversations).")
    for conversation in data:
        if not isinstance(conversation, dict):
            continue
        parsed = _chatgpt(conversation) or _claude(conversation)
        if parsed is None:
            continue
        origin, identifier, title, asked = parsed
        date = _when(conversation.get("create_time") or conversation.get("created_at"))
        yield {"origin": origin, "id": identifier, "title": " ".join(title.split())[:200], "date": date,
               "asked": asked}


def memory_text(item: dict[str, Any]) -> str:
    head = f"Conversation with {item['origin']}" + (f" on {item['date']}" if item["date"] else "") + f": {item['title']}."
    body = []
    used = len(head)
    for question in item["asked"]:
        line = " ".join(question.split())
        line = line if len(line) <= 300 else line[:299].rstrip() + "…"
        if used + len(line) + 12 > MEMORY_CHARS:
            break
        body.append("- " + line)
        used += len(line) + 3
    more = len(item["asked"]) - len(body)
    return "\n".join([head, "What I asked:", *body] + ([f"(+{more} more messages)"] if more > 0 else []))


def import_conversations(memory: Any, path: Path | str, *, limit: int = 5000) -> dict[str, Any]:
    """Store one memory per new conversation; return how many were read, imported and already there."""
    with memory.connect() as db:
        known = {str(row[0]) for row in db.execute(
            "SELECT json_extract(metadata_json,'$.imported_id') FROM memories WHERE source='imported_chat' "
            "AND json_valid(metadata_json)").fetchall() if row[0]}
    read = imported = skipped = 0
    origins: dict[str, int] = {}
    for item in read_export(path):
        read += 1
        key = f"{item['origin']}:{item['id'] or item['title'] + item['date']}"
        if key in known:
            skipped += 1
            continue
        if imported >= limit:
            break
        memory.store_memory("episodic", memory_text(item), 0.6, "imported_chat", None,
                            {"imported_id": key, "origin": item["origin"], "goal": item["title"],
                             "date": item["date"], "messages": len(item["asked"])}, defer_neural=True)
        known.add(key)
        imported += 1
        origins[item["origin"]] = origins.get(item["origin"], 0) + 1
    return {"conversations_read": read, "imported": imported, "already_imported": skipped, "by_origin": origins,
            "note": "Your questions were kept, not the other assistant's answers. Recall by meaning covers them "
                    "after the next start, when new memories are embedded."}


def register_chat_import_tools(registry: Any, memory: Any) -> None:
    def import_chat_history(path: str) -> dict[str, Any]:
        return import_conversations(memory, path)

    registry.register(
        "import_chat_history",
        "Import the owner's own ChatGPT or Claude data export (the conversations.json file, or the unzipped export "
        "folder) into HAYAT's memory: one memory per conversation with its title, date and what the owner asked. "
        "Read on this computer; nothing is uploaded; the same conversation is never imported twice.",
        {"type": "object", "properties": {"path": {"type": "string", "minLength": 1}}, "required": ["path"],
         "additionalProperties": False},
        "medium", "filesystem_read", import_chat_history,
    )
