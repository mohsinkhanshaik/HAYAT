"""Durable diagnostics may retain outcomes, never private networking contents.

Live tool results remain available to the local caller. This is deliberately a
tool/task-boundary policy, not an attempt to infer private names in free prose.
Ordinary chat transcripts and explicit plaintext exports are separate stores.
"""
from __future__ import annotations

import re
from typing import Any


PRIVATE_TEXT = "[Private workspace content omitted from durable diagnostics]"
_TOOLS = frozenset({"networking_save", "networking_list", "networking_detail", "networking_match",
                    "networking_draft", "networking_revoke", "networking_delete", "networking_export",
                    "networking_restore", "public_contact_search", "face_enroll", "face_recognize",
                    "face_list_enrolled", "face_forget", "face_clear_all", "face_recognition_status",
                    "local_image_inspect", "local_image_compare", "image_source_search", "image_source_check_page"})
_SAFE_STRINGS = {"permission": {"ALLOW", "ASK", "DENY", "BUDGET", "LOCATION_PRIVACY"},
                 "status": {"running", "complete", "failed", "cancelled"},
                 "state": {"RUNNING", "COMPLETE", "FAILED", "CANCELLED"},
                 "resume_policy": {"manual_review_required"}}


def is_private_tool(name: Any) -> bool:
    return isinstance(name, str) and name in _TOOLS


def contains_private_tools(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("private_networking") is True or any(contains_private_tools(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_private_tools(item) for item in value)
    return is_private_tool(value)


def redact_private_payload(value: Any) -> Any:
    """Redact every free-text field in an identified private tool/task payload.

No digest of personal data is retained. Scalar success/duration counters and
recognized tool names remain useful for diagnostics without copying contacts.
"""
    def clean(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {str(name): clean(child, str(name)) for name, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        if isinstance(item, str):
            if (is_private_tool(item) or item in _SAFE_STRINGS.get(key, set())
                    or (key == "call_id" and re.fullmatch(r"call-[0-9a-f]{32}", item))):
                return item
            return PRIVATE_TEXT
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return PRIVATE_TEXT
    result = clean(value)
    if isinstance(result, dict):
        result = {**result, "private_networking": True, "redacted": True}
    return result
