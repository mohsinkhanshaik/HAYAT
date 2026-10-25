"""Tools: confirm the owner is present with Windows Hello.

Two read-only tools. Neither enrols anyone, stores a template, or looks any
person up -- they only ask Windows whether the signed-in owner is at the
keyboard right now. See app/owner_presence.py.
"""
from __future__ import annotations

from typing import Any

from app.owner_presence import OwnerPresence, owner_presence
from app.tools.registry import ToolRegistry


def register_owner_verification_tools(registry: ToolRegistry, presence: OwnerPresence | None = None) -> OwnerPresence:
    presence = presence or owner_presence()

    def verify_owner_present(reason: str = "") -> dict[str, Any]:
        return presence.verify(reason)

    def owner_verification_status() -> dict[str, Any]:
        return presence.status()

    registry.register(
        "verify_owner_present",
        "Ask Windows Hello to confirm the signed-in owner is physically present, using the fingerprint, "
        "face or PIN they enrolled with Windows. Returns only a yes/no plus a plain reason; HAYAT never sees "
        "the biometric itself and no one but the owner is enrolled. 'reason' is the single line Windows shows "
        "the owner. Use before a sensitive step, not to identify or search for anyone.",
        {"type": "object", "properties": {"reason": {"type": "string"}}, "required": [], "additionalProperties": False},
        "low",
        "system_read",
        verify_owner_present,
    )
    registry.register(
        "owner_verification_status",
        "Report whether Windows Hello can prompt on this device (set up, no device, not configured, disabled by "
        "policy, or busy) without prompting the owner.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "low",
        "system_read",
        owner_verification_status,
    )
    return presence
