"""Deterministic authority: the single answer to "may this action execute?".

The previous model had two states -- ALLOW or ASK -- so anything not on the
allow list produced a prompt. Saying "look at me" and being asked to approve
the camera, then approve the capture, is not security; it is friction that
trains the owner to approve reflexively without reading. Meanwhile the genuinely
consequential operations sat in the same undifferentiated ASK bucket as taking
a screenshot.

This separates *risk* from *authorization*. Ordinary local operations the owner
just asked for are pre-authorized by policy; a small set of consequential
operations always stops and asks, and Trusted Owner Mode cannot widen that set.

Authority is decided here and nowhere else. The model proposes actions; it never
grants them. A web page is evidence, never instruction. A tool cannot vote on
whether it should be permitted to run.
"""
from __future__ import annotations

import fnmatch
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class RiskTier(IntEnum):
    """How much a class of action can cost if it is wrong."""

    PASSIVE = 0        # observing state; no effect on the world
    ORDINARY = 1       # everyday reversible local work
    EXTENDED = 2       # broader local reach; still recoverable
    HIGH_CONSEQUENCE = 3  # security, credentials, money, mass loss, exposure
    PROHIBITED = 4     # never permitted by this runtime


class Decision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class Principal(StrEnum):
    """Who is asking. Not a logging label -- it changes the decision.

    Owner policy, grants, and envelopes belong to LOCAL_OWNER. The runtime's
    own maintenance (SYSTEM_INTERNAL) gets a small fixed allowance and nothing
    the owner configured for themselves. Text that arrived from outside --
    web pages, documents, search results, anything a prompt injection rides in
    on -- is UNTRUSTED_EXTERNAL_CONTENT and can never exercise authority at
    all: it is evidence to reason about, not a requester.
    """

    LOCAL_OWNER = "LOCAL_OWNER"
    SYSTEM_INTERNAL = "SYSTEM_INTERNAL"
    BACKGROUND_LEARNER = "BACKGROUND_LEARNER"
    UNTRUSTED_EXTERNAL_CONTENT = "UNTRUSTED_EXTERNAL_CONTENT"
    # The owner, but reaching in from a phone or another device over the private
    # mesh rather than sitting at the console. Still the owner -- reading the
    # world and reversible local work they ask for directly are fine -- but with
    # owner-mode pre-authorisation deliberately out of reach: anything that
    # deletes, runs unknown shell, installs, acts outward, spends, or touches
    # Windows itself stays for the console, because an ASK cannot be answered
    # from a phone and must not run unattended just because a mode is on.
    REMOTE_OWNER = "REMOTE_OWNER"


# The complete authority of SYSTEM_INTERNAL: keeping the runtime itself alive.
# Deliberately does not overlap with anything the owner grants for their own
# work -- internal maintenance never inherits owner file, browser, sensor, or
# input authority.
SYSTEM_INTERNAL_CATEGORIES = frozenset({"system_read", "dependency_inspect"})

# The complete authority of the always-on learner. Most entries are PASSIVE
# reads. `knowledge_research` is the one narrow ORDINARY capability: it may
# write only the verified, provenance-bearing result produced by the bounded
# ResearchManager into its own knowledge database. It cannot write arbitrary
# files, click, type, download, open an application, touch a sensor, or reach
# another machine.
#
# This is the answer to "it must not go wild", expressed where it can be
# enforced rather than promised. An unattended loop running for days is the
# worst possible holder of broad authority: nobody is watching any individual
# action, so the authority itself has to be small. If the learner is ever
# wanted for something outside this set, that is a change the owner makes
# deliberately here -- not something a background process can grow into.
BACKGROUND_LEARNER_CATEGORIES = frozenset({
    "web_search", "public_web_read", "bounded_crawl", "public_api_get",
    "filesystem_read", "system_read", "knowledge_research",
})
BACKGROUND_LEARNER_WRITE_CATEGORIES = frozenset({"knowledge_research"})


class AuthoritySource(StrEnum):
    """Why a decision came out the way it did -- recorded on every decision."""

    SYSTEM_POLICY = "SYSTEM_POLICY"
    OWNER_POLICY = "OWNER_POLICY"
    EXPLICIT_CURRENT_REQUEST = "EXPLICIT_CURRENT_REQUEST"
    SESSION_GRANT = "SESSION_GRANT"
    PERSISTENT_GRANT = "PERSISTENT_GRANT"
    PRIVACY_MODE = "PRIVACY_MODE"
    PROTECTED_BOUNDARY = "PROTECTED_BOUNDARY"
    NO_GRANT = "NO_GRANT"


# Risk tier per permission category. This is the security-critical table: it
# decides what Trusted Owner Mode may pre-authorize and what it may never touch.
CATEGORY_TIERS: dict[str, RiskTier] = {
    # Tier 0 -- observation only.
    "filesystem_read": RiskTier.PASSIVE, "system_read": RiskTier.PASSIVE,
    "browser_read": RiskTier.PASSIVE, "web_search": RiskTier.PASSIVE,
    "public_web_read": RiskTier.PASSIVE, "dependency_inspect": RiskTier.PASSIVE,
    "bounded_crawl": RiskTier.PASSIVE, "public_api_get": RiskTier.PASSIVE,
    "computer_observe": RiskTier.PASSIVE, "camera_status": RiskTier.PASSIVE,
    "application_discover": RiskTier.PASSIVE,
    "connector_read": RiskTier.PASSIVE, "mcp_read": RiskTier.PASSIVE,
    # Tier 1 -- ordinary reversible local work the owner routinely asks for.
    "filesystem_write_project": RiskTier.ORDINARY, "application_launch": RiskTier.ORDINARY,
    "computer_input": RiskTier.ORDINARY, "camera_use": RiskTier.ORDINARY,
    "clipboard_read": RiskTier.ORDINARY, "clipboard_write": RiskTier.ORDINARY,
    "browser_click": RiskTier.ORDINARY, "browser_type": RiskTier.ORDINARY,
    "python_project": RiskTier.ORDINARY, "powershell_safe": RiskTier.ORDINARY,
    "install_package_project": RiskTier.ORDINARY, "public_download": RiskTier.ORDINARY,
    "knowledge_research": RiskTier.ORDINARY,
    # Tier 2 -- broader reach, still recoverable, needs an explicit scope.
    "filesystem_delete": RiskTier.EXTENDED, "browser_download": RiskTier.EXTENDED,
    "powershell_unknown": RiskTier.EXTENDED, "install_system_software": RiskTier.EXTENDED,
    # Tier 3 -- always stops for a human answer, whatever the mode. These act
    # as the owner toward the outside world, or reach past the user account
    # into Windows itself, and neither is something to infer from context.
    "login_account": RiskTier.HIGH_CONSEQUENCE, "submit_form": RiskTier.HIGH_CONSEQUENCE,
    "external_post": RiskTier.HIGH_CONSEQUENCE, "send_message": RiskTier.HIGH_CONSEQUENCE,
    "connector_write": RiskTier.HIGH_CONSEQUENCE, "mcp_write": RiskTier.HIGH_CONSEQUENCE,
    "automation_manage": RiskTier.HIGH_CONSEQUENCE,
    # Administrator work is available rather than forbidden: the owner asked
    # for it, it is their machine, and refusing outright just pushes them to do
    # it in another window with no record. But it stays one approval away
    # forever. An elevated shell can disable the very controls on this table,
    # so "yes, elevate, for this" is a sentence a person has to say.
    "powershell_admin": RiskTier.HIGH_CONSEQUENCE,
    "admin_elevate": RiskTier.HIGH_CONSEQUENCE,
    # Tier 4 -- refused outright. Not friction to be tuned away: each of these
    # is either irreversible, spends money, or removes the owner's ability to
    # supervise what the agent does next.
    "delete_system_file": RiskTier.PROHIBITED, "modify_system": RiskTier.PROHIBITED,
    "financial_transaction": RiskTier.PROHIBITED, "purchase": RiskTier.PROHIBITED,
    "credential_access": RiskTier.PROHIBITED, "bypass_access_control": RiskTier.PROHIBITED,
    # The agent changing its own authority is the one thing that would make
    # every other line here meaningless.
    "modify_own_permissions": RiskTier.PROHIBITED,
}

MONEY_CATEGORIES = frozenset({"financial_transaction", "purchase"})

#: Said every time money movement is stopped, so the owner is never left with
#: a bare category name. The block is fixed in code; nothing here asks the
#: model to weigh it.
MONEY_REASON = (
    "Payments, purchases and transfers are blocked in my code in every mode and from every device, "
    "and approval cannot switch that off. A payment cannot be undone, and one misread page, a fake "
    "checkout or an instruction hidden in a website would spend your real money."
)
MONEY_EXPLANATION = (
    "I stopped before any money moved: nothing was paid, bought, sent or transferred. "
    + MONEY_REASON + " "
    "What I can do: find and compare the item, invoice or payee, fill in everything except the payment, "
    "and stop on the payment page with the amount and recipient shown, so you check them and pay yourself."
)


def refusal_reason(category: str, fallback: str) -> str:
    """The plain explanation for a money category, else the given reason."""
    return MONEY_EXPLANATION if category in MONEY_CATEGORIES else fallback

# Categories Trusted Owner Mode may pre-authorize.
#
# The line sits above EXTENDED, not above ORDINARY. Deleting a file, running a
# script, downloading something, installing a tool -- these are the everyday
# acts of a person working on their own computer, and stopping to ask about
# each one trains the owner to click yes without reading, which is worse for
# safety than not asking. What is above the line is not "risky", it is
# *irreversible or outward-facing*: acting as the owner toward other people and
# services, spending money, touching credentials, or changing Windows itself.
# Those still stop, every time, whatever the mode.
#
# Derived from the tier table rather than hand-listed, so a category added
# later inherits the right answer instead of depending on someone remembering
# to classify it.
OWNER_AUTO_CATEGORIES = frozenset(
    name for name, tier in CATEGORY_TIERS.items() if tier <= RiskTier.EXTENDED
)

# The boundary itself, as an executable fact rather than a comment. If a future
# edit reclassifies something dangerous downward, or widens the threshold
# above, importing this module fails immediately instead of shipping a system
# that silently pre-authorises credential access.
NEVER_AUTO_CATEGORIES = frozenset(
    name for name, tier in CATEGORY_TIERS.items() if tier >= RiskTier.HIGH_CONSEQUENCE
)
assert not (OWNER_AUTO_CATEGORIES & NEVER_AUTO_CATEGORIES), (
    "Trusted Owner Mode must never pre-authorise a high-consequence or prohibited category: "
    f"{sorted(OWNER_AUTO_CATEGORIES & NEVER_AUTO_CATEGORIES)}"
)

# Outward actions the owner's own words can authorise for one request, at
# Mohsin's instruction (2026-09-27): "send this", "submit the form", "post it"
# is the approval, and a second prompt is asking twice. Only an envelope opened
# from the owner's current message carries them -- owner mode, grants, remote
# devices, background learning and web content still cannot. Logging in and
# Administrator work are deliberately not here.
OWNER_REQUESTABLE_CATEGORIES = frozenset({
    "submit_form", "send_message", "external_post", "connector_write", "mcp_write",
})
assert all(CATEGORY_TIERS[name] is RiskTier.HIGH_CONSEQUENCE for name in OWNER_REQUESTABLE_CATEGORIES)

# Sensor categories a privacy switch must be able to override regardless of
# any grant that exists. computer_observe is here deliberately: it carries
# screenshots and screen description, and a privacy switch that leaves the
# screen observable is not a privacy switch. (Microphone capture runs through
# the voice pipeline rather than a tool category; VoiceManager consults
# privacy_mode directly.)
SENSOR_CATEGORIES = frozenset({"camera_use", "clipboard_read", "clipboard_write", "computer_observe"})
NETWORK_CATEGORIES = frozenset({
    "web_search", "public_web_read", "public_download", "bounded_crawl",
    "public_api_get", "knowledge_research", "browser_read", "browser_click", "browser_type", "browser_download",
})


@dataclass(slots=True)
class ActionRequest:
    """What a tool wants to do, described so policy can judge it."""

    tool: str
    category: str
    arguments: dict[str, Any] = field(default_factory=dict)
    resource: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    explicitly_requested: bool = False
    principal: Principal = Principal.LOCAL_OWNER

    @property
    def tier(self) -> RiskTier:
        return CATEGORY_TIERS.get(self.category, RiskTier.EXTENDED)


@dataclass(slots=True)
class PolicyDecision:
    decision: Decision
    tier: RiskTier
    source: AuthoritySource
    reason: str
    grant_id: str | None = None
    scope: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value, "tier": int(self.tier), "tier_name": self.tier.name,
            "source": self.source.value, "reason": self.reason,
            "grant_id": self.grant_id, "scope": self.scope,
        }


@dataclass
class CapabilityGrant:
    """A scoped, expiring, revocable permission the owner actually gave."""

    capability: str
    scope: str = "*"
    grant_id: str = field(default_factory=lambda: f"grant-{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: float | None = None
    persistent: bool = False
    session_id: str | None = None
    source: str = "owner"
    use_count: int = 0
    revoked: bool = False

    def matches(self, request: ActionRequest, now: float) -> bool:
        if self.revoked or self.capability != request.category:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if self.session_id and request.session_id and self.session_id != request.session_id:
            return False
        if self.scope in {"*", ""}:
            return True
        resource = request.resource or ""
        return bool(resource) and _within_scope(resource, self.scope)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id, "capability": self.capability, "scope": self.scope,
            "created_at": self.created_at, "persistent": self.persistent,
            "session_id": self.session_id, "source": self.source,
            "use_count": self.use_count, "revoked": self.revoked,
            "expires_in_seconds": None if self.expires_at is None else max(0, round(self.expires_at - time.time())),
        }


def _within_scope(resource: str, scope: str) -> bool:
    """Whether a resource genuinely sits inside a granted scope.

    Paths are resolved before comparison so `..`, symlinks and alternate
    spellings cannot walk out of a granted root. A grant on D:\\AI must not
    authorise C:\\Users\\...\\Documents.
    """
    try:
        resolved = Path(resource).resolve()
        root = Path(scope).resolve()
    except (OSError, ValueError):
        return fnmatch.fnmatch(resource.lower(), scope.lower())
    if root == resolved:
        return True
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return fnmatch.fnmatch(str(resolved).lower(), scope.lower())


@dataclass
class TaskAuthorityEnvelope:
    """The authority a single explicitly-requested task carries.

    "Open Notepad and write hello" is one request but several tool calls --
    launch, focus, type, read back. Asking about each in turn is the same
    approval fatigue the tier system exists to remove, so the envelope is
    derived once from what the owner actually asked for and the substeps run
    inside it.

    It can only ever narrow. `maximum_tier` is the ceiling regardless of what
    the planner decides it needs later, so a task that starts ordinary and
    discovers it wants a protected action stops at exactly that point rather
    than inheriting permission it was never given.
    """

    envelope_id: str = field(default_factory=lambda: f"env-{uuid.uuid4().hex[:12]}")
    session_id: str | None = None
    turn_id: str | None = None
    task_id: str | None = None
    capabilities: frozenset[str] = frozenset()
    maximum_tier: RiskTier = RiskTier.ORDINARY
    source: str = "explicit_request"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: float | None = None
    revoked: bool = False

    def covers(self, request: ActionRequest, now: float) -> bool:
        if self.revoked:
            return False
        if self.expires_at is not None and now >= self.expires_at:
            return False
        if request.tier > self.maximum_tier:
            return False
        if self.turn_id and request.turn_id and self.turn_id != request.turn_id:
            return False
        return request.category in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_id": self.envelope_id, "session_id": self.session_id,
            "turn_id": self.turn_id, "task_id": self.task_id,
            "capabilities": sorted(self.capabilities), "maximum_tier": self.maximum_tier.name,
            "source": self.source, "created_at": self.created_at, "revoked": self.revoked,
        }


class AuthorityManager:
    """Evaluates every consequential action against owner policy and grants."""

    def __init__(self, *, owner_mode: bool = False, event_bus: Any = None, store: Any = None) -> None:
        self._grants: list[CapabilityGrant] = []
        self._audit: list[dict[str, Any]] = []
        self._envelopes: dict[str, TaskAuthorityEnvelope] = {}
        self._lock = threading.RLock()
        self._events = event_bus
        self._store = store
        # Persisted state wins over the constructor default, so owner mode and
        # grants survive a restart without the owner re-authorising everything.
        self._owner_mode = bool(store.get_setting("owner_mode", owner_mode)) if store else owner_mode
        self._privacy_mode = bool(store.get_setting("privacy_mode", False)) if store else False
        if store is not None:
            self._restore(store)

    def _restore(self, store: Any) -> None:
        """Rebuild persistent grants, dropping anything expired or stale."""
        now = time.time()
        restored: list[CapabilityGrant] = []
        for row in store.load_grants():
            expires_at = row.get("expires_at")
            if expires_at is not None and now >= float(expires_at):
                continue
            tier = CATEGORY_TIERS.get(row["capability"], RiskTier.EXTENDED)
            # Re-evaluated against the *current* tier table rather than the tier
            # stored with the grant, so a capability that has since been
            # reclassified upward cannot be grandfathered into more authority.
            if tier >= RiskTier.HIGH_CONSEQUENCE:
                continue
            restored.append(CapabilityGrant(
                capability=row["capability"], scope=row.get("scope") or "*",
                grant_id=row["grant_id"], created_at=row["created_at"],
                expires_at=None if expires_at is None else float(expires_at),
                persistent=True, source=row.get("source", "owner"),
                use_count=int(row.get("use_count") or 0),
            ))
        with self._lock:
            self._grants.extend(restored)

    # ------------------------------------------------------------------ modes

    @property
    def owner_mode(self) -> bool:
        with self._lock:
            return self._owner_mode

    def set_owner_mode(self, enabled: bool, *, source: str = "owner", persistent: bool = False) -> None:
        """Changing policy is more consequential than using it, so this is only
        ever called from an explicit owner action -- never from model output."""
        with self._lock:
            self._owner_mode = bool(enabled)
        if self._store is not None and persistent:
            self._store.set_setting("owner_mode", bool(enabled))
        if self._store is not None:
            self._store.record_audit(
                "OWNER_MODE_CHANGED", decision="ALLOW", source=source,
                reason=f"owner_mode={'on' if enabled else 'off'} persistent={persistent}",
            )
        self._publish("PRIVACY_MODE_CHANGED", {"owner_mode": bool(enabled), "source": source, "persistent": persistent})

    @property
    def privacy_mode(self) -> bool:
        with self._lock:
            return self._privacy_mode

    def set_privacy_mode(self, enabled: bool) -> None:
        """Privacy mode persists across restarts, deliberately.

        If the owner turned privacy on and the process later crashed, silently
        coming back with sensors live would be the wrong failure direction: the
        safe default is to stay private until explicitly turned off.
        """
        with self._lock:
            changed = self._privacy_mode != bool(enabled)
            self._privacy_mode = bool(enabled)
        if self._store is not None:
            self._store.set_setting("privacy_mode", bool(enabled))
            self._store.record_audit("PRIVACY_MODE_CHANGED", decision="ALLOW", reason=f"privacy={'on' if enabled else 'off'}")
        # The audit keeps every request; the event bus, which the brain's
        # timeline is drawn from, hears only real changes. Re-asserting the
        # current value is not one; logged as one, it had put thousands of
        # "changed" events in the owner's log.
        if changed:
            self._publish("PRIVACY_MODE_CHANGED", {"privacy_mode": bool(enabled)})

    # ----------------------------------------------------------------- grants

    def grant(self, capability: str, *, scope: str = "*", ttl_seconds: float | None = None,
              persistent: bool = False, session_id: str | None = None, source: str = "owner") -> CapabilityGrant:
        """Record an owner grant. Tier 3+ can never be pre-granted."""
        tier = CATEGORY_TIERS.get(capability, RiskTier.EXTENDED)
        if tier >= RiskTier.HIGH_CONSEQUENCE:
            raise PermissionError(
                f"{capability} is tier {tier.name} and must be authorised at the moment of use, not pre-granted"
            )
        record = CapabilityGrant(
            capability=capability, scope=scope, persistent=persistent,
            session_id=session_id, source=source,
            expires_at=None if ttl_seconds is None else time.time() + ttl_seconds,
        )
        with self._lock:
            self._grants.append(record)
        if self._store is not None:
            if persistent:
                self._store.save_grant({**record.to_dict(), "risk_tier": int(tier), "expires_at": record.expires_at})
            self._store.record_audit(
                "GRANT_CREATED", capability=capability, scope=scope, tier=int(tier),
                grant_id=record.grant_id, source=source, decision="ALLOW",
                reason=f"granted {capability} on {scope}",
            )
        self._publish("GRANT_CREATED", record.to_dict())
        return record

    def revoke(self, *, capability: str | None = None, grant_id: str | None = None) -> int:
        with self._lock:
            targets = [
                item for item in self._grants
                if not item.revoked
                and (grant_id is None or item.grant_id == grant_id)
                and (capability is None or item.capability == capability)
            ]
            for item in targets:
                item.revoked = True
        for item in targets:
            if self._store is not None:
                self._store.mark_revoked(item.grant_id)
                self._store.record_audit(
                    "GRANT_REVOKED", capability=item.capability, scope=item.scope,
                    grant_id=item.grant_id, decision="DENY", reason="revoked by owner",
                )
            self._publish("GRANT_REVOKED", item.to_dict())
        return len(targets)

    def active_grants(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                item.to_dict() for item in self._grants
                if not item.revoked and (item.expires_at is None or now < item.expires_at)
            ]

    # -------------------------------------------------------------- envelopes

    def open_envelope(self, capabilities: set[str], *, turn_id: str | None = None,
                      session_id: str | None = None, task_id: str | None = None,
                      ttl_seconds: float = 300.0, source: str = "explicit_request") -> TaskAuthorityEnvelope:
        """Derive an envelope for one explicitly requested compound task.

        Capabilities above tier 1 are dropped rather than rejected: asking for
        something ordinary must never quietly carry protected authority along
        with it. The one exception is OWNER_REQUESTABLE_CATEGORIES, which the
        caller passes only when the owner's own message asked to send, submit
        or post. The caller cannot widen this by passing a bigger set.
        """
        permitted = frozenset(
            name for name in capabilities
            if CATEGORY_TIERS.get(name, RiskTier.EXTENDED) <= RiskTier.ORDINARY
            or name in OWNER_REQUESTABLE_CATEGORIES
        )
        envelope = TaskAuthorityEnvelope(
            capabilities=permitted, session_id=session_id, turn_id=turn_id, task_id=task_id,
            maximum_tier=RiskTier.HIGH_CONSEQUENCE if permitted & OWNER_REQUESTABLE_CATEGORIES else RiskTier.ORDINARY,
            source=source, expires_at=time.time() + ttl_seconds,
        )
        with self._lock:
            self._envelopes[envelope.envelope_id] = envelope
            # One live envelope per turn; a new request supersedes the old.
            for other_id, other in list(self._envelopes.items()):
                if other_id != envelope.envelope_id and turn_id and other.turn_id == turn_id:
                    other.revoked = True
        return envelope

    def close_envelope(self, envelope_id: str) -> None:
        with self._lock:
            envelope = self._envelopes.get(envelope_id)
            if envelope is not None:
                envelope.revoked = True

    def active_envelopes(self) -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            return [
                item.to_dict() for item in self._envelopes.values()
                if not item.revoked and (item.expires_at is None or now < item.expires_at)
            ]

    # --------------------------------------------------------------- decision

    def evaluate(self, request: ActionRequest) -> PolicyDecision:
        tier = request.tier
        now = time.time()

        # Principal gating comes before everything else, because every path
        # below this point -- owner mode, grants, envelopes, explicit request --
        # is LOCAL_OWNER authority and must be unreachable for anyone else.
        if request.principal is Principal.UNTRUSTED_EXTERNAL_CONTENT:
            # External content never exercises authority. Not "requires
            # approval" -- denied, so a prompt injection cannot even generate
            # an approval dialog for the owner to mis-click through.
            return self._record(request, PolicyDecision(
                Decision.DENY, tier, AuthoritySource.SYSTEM_POLICY,
                "External content cannot request actions. It is evidence, not a requester.",
            ))
        if request.principal is Principal.BACKGROUND_LEARNER:
            # Read-only, and only within its own list. Owner mode, grants and
            # envelopes are all irrelevant here: an unattended process does not
            # inherit authority the owner granted for work they were watching.
            if request.category in BACKGROUND_LEARNER_CATEGORIES and (
                tier <= RiskTier.PASSIVE or request.category in BACKGROUND_LEARNER_WRITE_CATEGORIES
            ):
                return self._record(request, PolicyDecision(
                    Decision.ALLOW, tier, AuthoritySource.SYSTEM_POLICY,
                    "Background learning within its fixed read/verified-knowledge allowance.",
                ))
            return self._record(request, PolicyDecision(
                Decision.DENY, tier, AuthoritySource.SYSTEM_POLICY,
                f"Background learning may not {request.category}. It reads; it does not act. "
                "Anything else waits for you.",
            ))
        if request.principal is Principal.REMOTE_OWNER:
            # The same tier table, read more conservatively than for the owner
            # at the keyboard. Prohibited stays prohibited; privacy mode still
            # wins; reading is fine; a reversible local action the owner asked
            # for directly is fine. Everything above ORDINARY -- delete, unknown
            # shell, install, and every outward-facing or Windows-level act --
            # needs approval at the computer itself, which is unreachable from a
            # phone, so it does not run unattended. Owner-mode auto-approval is
            # never consulted here, because this branch returns first.
            if tier is RiskTier.PROHIBITED:
                return self._record(request, PolicyDecision(
                    Decision.DENY, tier, AuthoritySource.SYSTEM_POLICY,
                    refusal_reason(request.category, f"{request.category} is never permitted, from any device."),
                ))
            if self.privacy_mode and request.category in (SENSOR_CATEGORIES | NETWORK_CATEGORIES):
                return self._record(request, PolicyDecision(
                    Decision.DENY, tier, AuthoritySource.PRIVACY_MODE,
                    f"Privacy mode is on, which blocks {request.category}.",
                ))
            if tier <= RiskTier.PASSIVE:
                return self._record(request, PolicyDecision(
                    Decision.ALLOW, tier, AuthoritySource.SYSTEM_POLICY,
                    "Read-only observation, safe to serve to the owner's own remote device.",
                ))
            if request.explicitly_requested and tier <= RiskTier.ORDINARY:
                return self._record(request, PolicyDecision(
                    Decision.ALLOW, tier, AuthoritySource.EXPLICIT_CURRENT_REQUEST,
                    "An ordinary reversible local action the owner asked for directly.",
                ))
            return self._record(request, PolicyDecision(
                Decision.REQUIRE_APPROVAL, tier, AuthoritySource.PROTECTED_BOUNDARY,
                f"{request.category} needs approval at the computer itself; it does not "
                "run unattended from a remote device.",
            ))
        if request.principal is Principal.SYSTEM_INTERNAL:
            if request.category in SYSTEM_INTERNAL_CATEGORIES:
                return self._record(request, PolicyDecision(
                    Decision.ALLOW, tier, AuthoritySource.SYSTEM_POLICY,
                    "Internal runtime maintenance within its fixed allowance.",
                ))
            # Internal maintenance does not inherit the owner's grants, owner
            # mode, or envelopes. Anything outside its allowance stops here.
            return self._record(request, PolicyDecision(
                Decision.REQUIRE_APPROVAL if tier < RiskTier.HIGH_CONSEQUENCE else Decision.DENY,
                tier, AuthoritySource.SYSTEM_POLICY,
                f"{request.category} is outside the runtime's internal maintenance allowance.",
            ))

        if tier is RiskTier.PROHIBITED:
            return self._record(request, PolicyDecision(
                Decision.DENY, tier, AuthoritySource.SYSTEM_POLICY,
                refusal_reason(request.category, f"{request.category} is not permitted by this runtime under any mode."),
            ))

        # Privacy mode overrides grants for sensors and network rather than
        # being a cosmetic toggle -- a grant made earlier must not survive it.
        if self.privacy_mode and request.category in (SENSOR_CATEGORIES | NETWORK_CATEGORIES):
            return self._record(request, PolicyDecision(
                Decision.DENY, tier, AuthoritySource.PRIVACY_MODE,
                f"Privacy mode is on, which blocks {request.category} regardless of existing grants.",
            ))

        if tier is RiskTier.PASSIVE:
            return self._record(request, PolicyDecision(
                Decision.ALLOW, tier, AuthoritySource.SYSTEM_POLICY,
                "Read-only observation with no effect on the world.",
            ))

        # Tier 3 stops here unless the owner's current message asked for this
        # exact kind of outward action. Owner mode deliberately cannot reach it.
        if tier is RiskTier.HIGH_CONSEQUENCE and request.category in OWNER_REQUESTABLE_CATEGORIES:
            with self._lock:
                # Bound to exactly the turn that asked: a request carrying no
                # turn cannot borrow an outward envelope from one that did.
                envelope = next((
                    item for item in self._envelopes.values()
                    if item.turn_id and item.turn_id == request.turn_id and item.covers(request, now)
                ), None)
            if envelope is not None:
                return self._record(request, PolicyDecision(
                    Decision.ALLOW, tier, AuthoritySource.EXPLICIT_CURRENT_REQUEST,
                    f"You asked for this in your message (envelope {envelope.envelope_id}).",
                    scope=envelope.envelope_id,
                ))
        if tier is RiskTier.HIGH_CONSEQUENCE:
            return self._record(request, PolicyDecision(
                Decision.REQUIRE_APPROVAL, tier, AuthoritySource.PROTECTED_BOUNDARY,
                f"{request.category} has consequences outside this machine or cannot be undone, "
                "so it needs your explicit approval each time.",
            ))

        # An envelope from a task the owner explicitly asked for covers its
        # substeps, so "open Notepad and write hello" prompts zero times
        # instead of once per tool.
        with self._lock:
            envelope = next((item for item in self._envelopes.values() if item.covers(request, now)), None)
        if envelope is not None:
            return self._record(request, PolicyDecision(
                Decision.ALLOW, tier, AuthoritySource.EXPLICIT_CURRENT_REQUEST,
                f"Part of the task you asked for (envelope {envelope.envelope_id}).",
                scope=envelope.envelope_id,
            ))

        with self._lock:
            match = next((item for item in self._grants if item.matches(request, now)), None)
            if match is not None:
                match.use_count += 1
        if match is not None:
            return self._record(request, PolicyDecision(
                Decision.ALLOW, tier,
                AuthoritySource.PERSISTENT_GRANT if match.persistent else AuthoritySource.SESSION_GRANT,
                f"Covered by your grant for {match.capability} ({match.scope}).",
                grant_id=match.grant_id, scope=match.scope,
            ))

        if self.owner_mode and request.category in OWNER_AUTO_CATEGORIES:
            return self._record(request, PolicyDecision(
                Decision.ALLOW, tier, AuthoritySource.OWNER_POLICY,
                f"Trusted Owner Mode pre-authorises {request.category} for ordinary local work.",
            ))

        # Asking for something plainly is consent for one reversible instance of
        # it. Approving the exact thing you just asked for is asking twice.
        if request.explicitly_requested and tier <= RiskTier.ORDINARY:
            return self._record(request, PolicyDecision(
                Decision.ALLOW, tier, AuthoritySource.EXPLICIT_CURRENT_REQUEST,
                "You asked for this directly, and it is an ordinary reversible local action.",
            ))

        return self._record(request, PolicyDecision(
            Decision.REQUIRE_APPROVAL, tier, AuthoritySource.NO_GRANT,
            f"No active grant covers {request.category}"
            + (f" for {request.resource}" if request.resource else "") + ".",
        ))

    # ------------------------------------------------------------------ audit

    def _record(self, request: ActionRequest, decision: PolicyDecision) -> PolicyDecision:
        entry = {
            "at": datetime.now(UTC).isoformat(), "tool": request.tool,
            "category": request.category, "resource": request.resource,
            **decision.to_dict(),
        }
        with self._lock:
            self._audit.append(entry)
            del self._audit[:-500]
        # Only decisions that withhold something are worth an event; allowing
        # ordinary work is the common case and would flood the bus.
        if decision.decision is not Decision.ALLOW:
            self._publish(
                "AUTHORITY_DENIED" if decision.decision is Decision.DENY else "AUTHORITY_APPROVAL_REQUIRED",
                entry,
            )
            # Withheld decisions are durable; routine allows stay in the
            # bounded in-memory ring so the audit file does not fill with
            # every keystroke the owner already authorised.
            if self._store is not None:
                self._store.record_audit(
                    "AUTHORITY_DENIED" if decision.decision is Decision.DENY else "AUTHORITY_APPROVAL_REQUIRED",
                    capability=request.category, scope=request.resource,
                    decision=decision.decision.value, tier=int(decision.tier),
                    source=decision.source.value, reason=decision.reason,
                    grant_id=decision.grant_id,
                )
        return decision

    def audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._audit[-max(1, limit):])

    def status(self) -> dict[str, Any]:
        return {
            "owner_mode": self.owner_mode, "privacy_mode": self.privacy_mode,
            "active_grants": len(self.active_grants()),
            "auto_categories": sorted(OWNER_AUTO_CATEGORIES),
            "always_protected": sorted(
                name for name, tier in CATEGORY_TIERS.items() if tier >= RiskTier.HIGH_CONSEQUENCE
            ),
        }

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._events is None:
            return
        try:
            self._events.publish(event_type, "authority", payload)
        except Exception:
            log_swallowed(_log, "publishing an authority event")
