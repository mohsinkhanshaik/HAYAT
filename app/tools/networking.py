"""Owner-local networking tools. No tool sends or uploads a message or record."""
from __future__ import annotations

from typing import Any

from app.networking import CHANNEL_KINDS, CHANNEL_STATUSES


TOOL_NAMES = ("networking_save", "networking_list", "networking_detail", "networking_match",
              "networking_draft", "networking_revoke", "networking_delete", "networking_export", "networking_restore")
#: Largest export file read for a restore (the encrypted store itself is bounded the same way).
MAX_EXPORT_BYTES = 8 * 1024 * 1024


def choosing(call: Any) -> Any:
    """Return "which one?" as a result, not a failure.

    Live, 2026-10-24: "draft an introduction to saved contact Sam Example …" with two Sams raised the
    namesake question as an error; the agent replanned with the model for 111 s and the question was lost.
    """
    import functools

    @functools.wraps(call)  # the contract audit reads the real parameters through __wrapped__
    def run(*positional: Any, **arguments: Any) -> Any:
        try:
            return call(*positional, **arguments)
        except ValueError as exc:
            message = str(exc)
            if message.startswith(("More than one saved contact", "No saved contact is named", "That short ID")):
                return {"choice_needed": True, "message": message, "sent": False}
            raise
    return run


def restore_file(book: Any, path: str) -> dict[str, Any]:
    """Read an export file and add its contacts (see NetworkingBook.restore)."""
    import json
    from pathlib import Path

    file = Path(str(path or "").strip().strip('"'))
    if not file.is_file():
        raise FileNotFoundError("Choose an existing HAYAT networking export file.")
    if file.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError("That file is too large to be a HAYAT networking export.")
    try:
        envelope = json.loads(file.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("That file is not a HAYAT networking export.") from exc
    return book.restore(envelope)


def register_networking_tools(registry: Any, book: Any) -> None:
    def obj(props: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
        return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}

    text = lambda limit: {"type": "string", "maxLength": limit}
    identity = {"contact_id": text(32)}
    # Reading and drafting also take a unique short ID or a name only one saved contact has.
    reference = {"contact_id": {**text(160), "description": "Saved contact ID, unique short ID, or exact saved name"}}
    topics = {"type": "array", "maxItems": 40, "items": text(120)}
    channels = {"type": "array", "maxItems": 12, "items": obj({
        "kind": {"enum": list(CHANNEL_KINDS)}, "value": text(2000),
        "status": {"enum": list(CHANNEL_STATUSES)}, "source_url": text(2000), "consent": {"type": "boolean"}}, ("kind", "value"))}
    registry.register("networking_save", "Save or edit a contact in the encrypted local private networking workspace. "
                      "Use only owner-provided facts or publicly listed professional details with source URLs. "
                      "Public channels require a source; consent channels require explicit consent=true. "
                      "Updates replace editable fields. No discovery request or message is sent.",
                      obj({**identity, "name": text(160), "organization": text(200), "role": text(200),
                           "expertise": topics, "goals": topics, "source_url": text(2000), "channels": channels,
                           "notes": text(8000), "follow_up": text(10)}, ("name",)),
                      "low", "filesystem_write_project", book.save)
    registry.register("networking_list", "Search active contacts in the owner's private local workspace. "
                      "Optional goal adds transparent topic overlap, never a personality or success prediction.",
                      obj({"query": text(300), "goal": text(1000)}), "low", "filesystem_read",
                      lambda query="", goal="": {"contacts": book.all(query=query, goal=goal)})
    registry.register("networking_detail", "Read one active owner-saved networking contact and its provenance.",
                      obj(reference, ("contact_id",)), "low", "filesystem_read", choosing(book.get))
    registry.register("networking_match", "Rank saved contacts by literal overlap between the stated goal and "
                      "owner-entered expertise/shared goals. Returns evidence and missing terms; not a prediction of help.",
                      obj({"goal": text(1000), "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, ("goal",)),
                      "low", "filesystem_read", lambda goal, limit=20: {"contacts": book.match(goal, limit)})
    registry.register("networking_draft", "Create an unsent outreach draft from a saved contact and the owner's stated purpose. "
                      "Use shared_context only when supplied by the owner. It never sends or opens a contact channel.",
                      obj({**reference, "purpose": text(1500), "sender": text(160), "shared_context": text(1000)}, ("contact_id", "purpose")),
                      "low", "filesystem_read", choosing(book.draft))
    registry.register("networking_revoke", "Revoke access to a saved contact, excluding it from reading, matching, drafts and exports. "
                      "The encrypted retained record can be deleted; a new entry requires fresh owner authorization.",
                      obj(identity, ("contact_id",)), "medium", "filesystem_write_project", book.revoke)
    registry.register("networking_delete", "Delete one saved networking contact including its contact details and notes.",
                      obj(identity, ("contact_id",)), "medium", "filesystem_write_project",
                      lambda contact_id: {"deleted": book.delete(contact_id)})
    registry.register("networking_export", "Export active networking contacts as an encrypted envelope for the current Windows account. "
                      "Only set plaintext=true when the owner explicitly asks for readable sensitive JSON. Sends nothing.",
                      obj({**identity, "plaintext": {"type": "boolean"}}),
                      "medium", "filesystem_write_project", book.export)
    registry.register("networking_restore", "Add contacts from a HAYAT networking export file made by this Windows account. "
                      "Never overwrites or merges saved contacts; each restored record is re-checked like a new entry. Sends nothing.",
                      obj({"path": text(1000)}, ("path",)), "medium", "filesystem_write_project",
                      lambda path: restore_file(book, path))
