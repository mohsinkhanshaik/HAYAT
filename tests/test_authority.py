from __future__ import annotations

from pathlib import Path

import pytest

from app.safety.authority import (
    ActionRequest,
    AuthorityManager,
    AuthoritySource,
    Decision,
    Principal,
    RiskTier,
)
from app.safety.permissions import PermissionManager
from app.tools.registry import ToolRegistry


def _request(category: str, **kwargs) -> ActionRequest:
    return ActionRequest(tool=kwargs.pop("tool", category), category=category, **kwargs)


# ------------------------------------------------------------------- tiers


def test_observation_is_always_allowed_without_a_grant():
    authority = AuthorityManager()
    decision = authority.evaluate(_request("camera_status"))
    assert decision.allowed
    assert decision.tier is RiskTier.PASSIVE


def test_ordinary_work_still_asks_when_nothing_authorises_it():
    """Owner mode off, no grant, not explicitly requested: the safe default."""
    authority = AuthorityManager(owner_mode=False)
    decision = authority.evaluate(_request("camera_use"))
    assert decision.decision is Decision.REQUIRE_APPROVAL
    assert decision.source is AuthoritySource.NO_GRANT


def test_owner_mode_pre_authorises_ordinary_local_work():
    """The approval-fatigue fix: routine reversible actions stop prompting."""
    authority = AuthorityManager(owner_mode=True)
    for category in ("camera_use", "application_launch", "computer_input", "filesystem_write_project", "browser_type"):
        decision = authority.evaluate(_request(category))
        assert decision.allowed, f"{category} should be pre-authorised in owner mode"
        assert decision.source is AuthoritySource.OWNER_POLICY


def test_asking_for_something_plainly_authorises_one_ordinary_action():
    """"Look at me" is the request and the consent; approving it again is
    asking the same question twice."""
    authority = AuthorityManager(owner_mode=False)
    decision = authority.evaluate(_request("camera_use", explicitly_requested=True))
    assert decision.allowed
    assert decision.source is AuthoritySource.EXPLICIT_CURRENT_REQUEST


def test_explicit_request_cannot_authorise_beyond_ordinary():
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request("external_post", explicitly_requested=True))
    assert decision.decision is Decision.REQUIRE_APPROVAL


# --------------------------------------------------- non-negotiable boundary


@pytest.mark.parametrize("category", ["login_account", "submit_form", "external_post", "send_message"])
def test_owner_mode_cannot_bypass_the_protected_boundary(category):
    """Trusted Owner Mode reduces friction; it does not widen authority."""
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request(category))
    assert decision.decision is Decision.REQUIRE_APPROVAL
    assert decision.source is AuthoritySource.PROTECTED_BOUNDARY


@pytest.mark.parametrize(
    "category",
    ["credential_access", "financial_transaction", "purchase", "modify_system",
     "delete_system_file", "bypass_access_control", "modify_own_permissions"],
)
def test_prohibited_actions_are_denied_even_in_owner_mode(category):
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request(category))
    assert decision.decision is Decision.DENY
    assert decision.tier is RiskTier.PROHIBITED


@pytest.mark.parametrize("category", ["powershell_admin", "admin_elevate"])
def test_administrator_work_is_possible_but_always_asks(category):
    """The owner asked for Administrator access; refusing outright just moved
    the work to another window with no record. So it is available -- and it
    stays one human answer away forever, because an elevated shell can switch
    off the controls that supervise the agent."""
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request(category))

    assert decision.decision is Decision.REQUIRE_APPROVAL
    assert decision.tier is RiskTier.HIGH_CONSEQUENCE


@pytest.mark.parametrize("category", ["powershell_admin", "admin_elevate"])
def test_administrator_work_can_never_be_pre_granted_away(category):
    """Not even by a grant, an envelope, or owner mode."""
    from app.safety.authority import OWNER_AUTO_CATEGORIES

    authority = AuthorityManager(owner_mode=True)
    assert category not in OWNER_AUTO_CATEGORIES
    with pytest.raises(PermissionError):
        authority.grant(category)

    authority.open_envelope({category}, turn_id="turn-1")
    decision = authority.evaluate(_request(category, turn_id="turn-1"))
    assert decision.decision is not Decision.ALLOW


def test_high_consequence_capabilities_cannot_be_pre_granted():
    """A grant is a promise made in advance; these must be decided in the
    moment, so the grant API refuses to create one."""
    authority = AuthorityManager(owner_mode=True)
    for category in ("credential_access", "external_post", "financial_transaction"):
        with pytest.raises(PermissionError):
            authority.grant(category)


# ------------------------------------------------------------------ grants


def test_a_grant_authorises_its_capability_and_records_why():
    authority = AuthorityManager()
    grant = authority.grant("camera_use", scope="*")
    decision = authority.evaluate(_request("camera_use"))
    assert decision.allowed
    assert decision.grant_id == grant.grant_id


def test_revoking_a_grant_takes_effect_immediately():
    authority = AuthorityManager()
    authority.grant("camera_use")
    assert authority.evaluate(_request("camera_use")).allowed
    assert authority.revoke(capability="camera_use") == 1
    assert not authority.evaluate(_request("camera_use")).allowed


def test_an_expired_grant_cannot_execute():
    authority = AuthorityManager()
    authority.grant("camera_use", ttl_seconds=-1)
    assert not authority.evaluate(_request("camera_use")).allowed


def test_a_grant_on_one_root_does_not_authorise_another(tmp_path):
    """Scope is a boundary, not a suggestion."""
    granted = tmp_path / "project"
    other = tmp_path / "documents"
    granted.mkdir()
    other.mkdir()
    authority = AuthorityManager()
    authority.grant("filesystem_write_project", scope=str(granted))

    inside = authority.evaluate(_request("filesystem_write_project", resource=str(granted / "a.txt")))
    outside = authority.evaluate(_request("filesystem_write_project", resource=str(other / "b.txt")))
    assert inside.allowed
    assert not outside.allowed


def test_path_traversal_cannot_escape_a_granted_scope(tmp_path):
    granted = tmp_path / "project"
    granted.mkdir()
    (tmp_path / "secrets").mkdir()
    authority = AuthorityManager()
    authority.grant("filesystem_write_project", scope=str(granted))

    escape = str(granted / ".." / "secrets" / "keys.txt")
    assert not authority.evaluate(_request("filesystem_write_project", resource=escape)).allowed


def test_a_session_grant_does_not_leak_into_another_session():
    authority = AuthorityManager()
    authority.grant("camera_use", session_id="session-a")
    assert authority.evaluate(_request("camera_use", session_id="session-a")).allowed
    assert not authority.evaluate(_request("camera_use", session_id="session-b")).allowed


# ------------------------------------------------------------- privacy mode


def test_privacy_mode_overrides_existing_grants():
    """A privacy switch that existing grants could out-vote would be theatre."""
    authority = AuthorityManager(owner_mode=True)
    authority.grant("camera_use", persistent=True)
    assert authority.evaluate(_request("camera_use")).allowed

    authority.set_privacy_mode(True)
    decision = authority.evaluate(_request("camera_use"))
    assert decision.decision is Decision.DENY
    assert decision.source is AuthoritySource.PRIVACY_MODE
    assert not authority.evaluate(_request("web_search")).allowed

    authority.set_privacy_mode(False)
    assert authority.evaluate(_request("camera_use")).allowed


# -------------------------------------------------- integration via dispatch


def _registry(tmp_path, authority=None):
    registry = ToolRegistry(PermissionManager({"default": "ASK", "permissions": {"camera_use": "ASK", "credential_access": "ASK"}}, Path(tmp_path)))
    registry.authority = authority
    return registry


def test_owner_mode_runs_an_ordinary_tool_without_any_approval_prompt(tmp_path):
    """Tested through real dispatch, not by asking the policy object directly:
    the point is that ToolRegistry.execute consults authority."""
    prompts: list[str] = []
    authority = AuthorityManager(owner_mode=True)
    registry = _registry(tmp_path, authority)
    registry.approval_callback = lambda tool, reason, arguments: prompts.append(tool) or True
    registry.register("camera_observe", "Observe", {"type": "object", "properties": {}}, "medium", "camera_use", lambda: "observed")

    result = registry.execute("camera_observe", {})

    assert result.success and result.output == "observed"
    assert prompts == [], "owner-authorised work must not raise an approval prompt"


def test_a_protected_tool_still_reaches_the_human_in_owner_mode(tmp_path):
    prompts: list[str] = []
    authority = AuthorityManager(owner_mode=True)
    registry = _registry(tmp_path, authority)
    registry.approval_callback = lambda tool, reason, arguments: prompts.append(reason) or False
    registry.register("read_secrets", "Read", {"type": "object", "properties": {}}, "high", "credential_access", lambda: "secret")

    result = registry.execute("read_secrets", {})

    assert not result.success
    # Denied outright at tier 4, so it never even reaches the approval prompt.
    assert "not permitted" in result.error.lower()


def test_without_an_authority_manager_behaviour_is_unchanged(tmp_path):
    prompts: list[str] = []
    registry = _registry(tmp_path, None)
    registry.approval_callback = lambda tool, reason, arguments: prompts.append(tool) or True
    registry.register("camera_observe", "Observe", {"type": "object", "properties": {}}, "medium", "camera_use", lambda: "observed")

    assert registry.execute("camera_observe", {}).success
    assert prompts == ["camera_observe"], "the legacy approval path must still work"


def test_denials_are_audited_with_their_reason():
    authority = AuthorityManager(owner_mode=True)
    authority.evaluate(_request("credential_access"))
    entry = authority.audit(5)[-1]
    assert entry["category"] == "credential_access"
    assert entry["decision"] == Decision.DENY.value
    assert entry["tier_name"] == RiskTier.PROHIBITED.name


# ------------------------------------------------------- persistence


def _store(tmp_path):
    from app.safety.authority_store import AuthorityStore
    return AuthorityStore(tmp_path / "authority.db")


def test_owner_mode_survives_a_restart_only_when_made_persistent(tmp_path):
    """Enabling for one session must not silently become permanent, and
    enabling persistently must actually outlive the process."""
    store = _store(tmp_path)

    session_only = AuthorityManager(store=store)
    session_only.set_owner_mode(True, persistent=False)
    assert AuthorityManager(store=_store(tmp_path)).owner_mode is False

    durable = AuthorityManager(store=store)
    durable.set_owner_mode(True, persistent=True)
    assert AuthorityManager(store=store).owner_mode is True


def test_a_persistent_grant_is_restored_after_restart(tmp_path):
    store = _store(tmp_path)
    first = AuthorityManager(store=store)
    first.grant("camera_use", persistent=True)

    restarted = AuthorityManager(store=store)
    assert restarted.evaluate(_request("camera_use")).allowed


def test_a_session_grant_does_not_survive_restart(tmp_path):
    store = _store(tmp_path)
    AuthorityManager(store=store).grant("camera_use", persistent=False)
    assert not AuthorityManager(store=store).evaluate(_request("camera_use")).allowed


def test_a_revoked_persistent_grant_stays_revoked_after_restart(tmp_path):
    store = _store(tmp_path)
    first = AuthorityManager(store=store)
    first.grant("camera_use", persistent=True)
    first.revoke(capability="camera_use")

    assert not AuthorityManager(store=store).evaluate(_request("camera_use")).allowed


def test_an_expired_persistent_grant_is_not_restored(tmp_path):
    store = _store(tmp_path)
    AuthorityManager(store=store).grant("camera_use", persistent=True, ttl_seconds=-1)
    assert not AuthorityManager(store=store).evaluate(_request("camera_use")).allowed


def test_grants_from_an_older_policy_version_are_not_grandfathered(tmp_path):
    """Tier meanings can change between versions. Honouring a grant written
    under different rules could hand over more authority than was agreed."""
    from app.safety import authority_store as store_module

    store = _store(tmp_path)
    AuthorityManager(store=store).grant("camera_use", persistent=True)
    assert AuthorityManager(store=store).evaluate(_request("camera_use")).allowed

    original = store_module.POLICY_VERSION
    try:
        store_module.POLICY_VERSION = original + 1
        assert store.stale_grant_count() == 1
        assert not AuthorityManager(store=store).evaluate(_request("camera_use")).allowed
    finally:
        store_module.POLICY_VERSION = original


def test_privacy_mode_persists_so_a_crash_cannot_re_enable_sensors(tmp_path):
    store = _store(tmp_path)
    AuthorityManager(store=store).set_privacy_mode(True)
    assert AuthorityManager(store=store).privacy_mode is True


def test_withheld_decisions_are_durably_audited_and_routine_allows_are_not(tmp_path):
    store = _store(tmp_path)
    authority = AuthorityManager(owner_mode=True, store=store)
    authority.evaluate(_request("camera_use"))          # routine allow
    authority.evaluate(_request("credential_access"))    # denied

    kinds = [row["kind"] for row in store.audit(20)]
    assert "AUTHORITY_DENIED" in kinds
    assert "AUTHORITY_APPROVAL_REQUIRED" not in kinds
    assert not any(row["capability"] == "camera_use" and row["kind"].startswith("AUTHORITY") for row in store.audit(20))


# --------------------------------------------------------- task envelopes


def test_an_envelope_covers_the_substeps_of_one_requested_task():
    """"Open Notepad and write hello" is one request and several tool calls;
    it should prompt zero times, not once per tool."""
    authority = AuthorityManager(owner_mode=False)
    authority.open_envelope(
        {"application_launch", "computer_input", "computer_observe"}, turn_id="turn-1",
    )
    for category in ("application_launch", "computer_input", "computer_observe"):
        decision = authority.evaluate(_request(category, turn_id="turn-1"))
        assert decision.allowed, f"{category} should be covered by the envelope"


def test_an_envelope_cannot_carry_protected_authority():
    """Asking for something ordinary must not smuggle a protected capability
    in alongside it, however the caller phrases the request."""
    authority = AuthorityManager()
    envelope = authority.open_envelope(
        {"application_launch", "credential_access", "login_account", "powershell_admin", "purchase"},
        turn_id="turn-1",
    )
    assert envelope.capabilities == frozenset({"application_launch"})
    for category in ("credential_access", "login_account", "powershell_admin", "purchase"):
        assert not authority.evaluate(_request(category, turn_id="turn-1")).allowed, category


@pytest.mark.parametrize("category", ["submit_form", "send_message", "external_post", "connector_write", "mcp_write"])
def test_the_owner_asking_to_send_submit_or_post_is_the_approval(category):
    """"Send it" is the approval; a second prompt is asking twice. Only for the
    turn that asked, and only through an envelope from the owner's message."""
    authority = AuthorityManager(owner_mode=True)
    authority.open_envelope({category}, turn_id="turn-1")
    decision = authority.evaluate(_request(category, turn_id="turn-1"))
    assert decision.allowed
    assert decision.source is AuthoritySource.EXPLICIT_CURRENT_REQUEST
    # Another turn, or a request carrying no turn at all, borrows nothing.
    assert not authority.evaluate(_request(category, turn_id="turn-2")).allowed
    assert not authority.evaluate(_request(category)).allowed


@pytest.mark.parametrize(
    "principal", [Principal.UNTRUSTED_EXTERNAL_CONTENT, Principal.BACKGROUND_LEARNER,
                  Principal.REMOTE_OWNER, Principal.SYSTEM_INTERNAL],
)
def test_an_outward_envelope_never_reaches_anyone_but_the_owner(principal):
    """A web page, the background learner, a phone or internal maintenance
    cannot send, whatever the owner asked for in the same turn."""
    authority = AuthorityManager(owner_mode=True)
    authority.open_envelope({"send_message"}, turn_id="turn-1")
    decision = authority.evaluate(_request("send_message", turn_id="turn-1", principal=principal))
    assert not decision.allowed


def test_an_envelope_does_not_cover_another_turn():
    authority = AuthorityManager()
    authority.open_envelope({"application_launch"}, turn_id="turn-1")
    assert not authority.evaluate(_request("application_launch", turn_id="turn-2")).allowed


def test_closing_an_envelope_ends_the_authority_it_carried():
    authority = AuthorityManager()
    envelope = authority.open_envelope({"application_launch"}, turn_id="turn-1")
    assert authority.evaluate(_request("application_launch", turn_id="turn-1")).allowed
    authority.close_envelope(envelope.envelope_id)
    assert not authority.evaluate(_request("application_launch", turn_id="turn-1")).allowed


def test_an_expired_envelope_stops_covering_substeps():
    authority = AuthorityManager()
    authority.open_envelope({"application_launch"}, turn_id="turn-1", ttl_seconds=-1)
    assert not authority.evaluate(_request("application_launch", turn_id="turn-1")).allowed


def test_privacy_mode_still_overrides_an_open_envelope():
    authority = AuthorityManager()
    authority.open_envelope({"camera_use"}, turn_id="turn-1")
    authority.set_privacy_mode(True)
    decision = authority.evaluate(_request("camera_use", turn_id="turn-1"))
    assert decision.decision is Decision.DENY
    assert decision.source is AuthoritySource.PRIVACY_MODE


# ------------------------------------------------- remote owner (phone/mesh)

from app.safety.authority import Principal  # noqa: E402


def _remote(category: str, **kwargs) -> ActionRequest:
    return _request(category, principal=Principal.REMOTE_OWNER, **kwargs)


def test_remote_owner_may_read_and_do_reversible_local_work_it_asks_for():
    """From a phone the owner can still read the world and do ordinary

    reversible local work -- chat, research, generating documents."""
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_remote("filesystem_read")).decision is Decision.ALLOW
    assert authority.evaluate(_remote("web_search")).decision is Decision.ALLOW
    assert authority.evaluate(_remote("knowledge_research", explicitly_requested=True)).decision is Decision.ALLOW
    assert authority.evaluate(_remote("filesystem_write_project", explicitly_requested=True)).decision is Decision.ALLOW


def test_remote_owner_cannot_delete_or_run_unknown_shell_even_in_owner_mode():
    """The exact hole to avoid: owner-mode pre-authorises delete/shell/install

    for the owner at the console, and it must NOT reach a remote device."""
    authority = AuthorityManager(owner_mode=True)
    for category in ("filesystem_delete", "powershell_unknown", "install_system_software"):
        decision = authority.evaluate(_remote(category, explicitly_requested=True))
        assert decision.decision is Decision.REQUIRE_APPROVAL, category


def test_remote_owner_high_consequence_needs_approval_at_the_console():
    authority = AuthorityManager(owner_mode=True)
    for category in ("login_account", "send_message", "external_post", "powershell_admin"):
        decision = authority.evaluate(_remote(category, explicitly_requested=True))
        assert decision.decision is Decision.REQUIRE_APPROVAL, category


def test_remote_owner_is_denied_prohibited_actions():
    authority = AuthorityManager(owner_mode=True)
    for category in ("purchase", "credential_access", "modify_own_permissions", "bypass_access_control"):
        decision = authority.evaluate(_remote(category, explicitly_requested=True))
        assert decision.decision is Decision.DENY, category


def test_remote_owner_respects_privacy_mode():
    authority = AuthorityManager(owner_mode=True)
    authority.set_privacy_mode(True)
    assert authority.evaluate(_remote("camera_use", explicitly_requested=True)).decision is Decision.DENY


def test_owner_at_the_console_is_unchanged_by_the_remote_principal():
    """Regression: the local owner still gets owner-mode pre-authorisation."""
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request("filesystem_delete")).decision is Decision.ALLOW



def test_privacy_mode_publishes_a_change_only_when_it_changes():
    published = []

    class Bus:
        def publish(self, kind, source, payload):
            published.append((kind, payload))

    authority = AuthorityManager(event_bus=Bus())
    for value in (False, True, True, False, False):
        authority.set_privacy_mode(value)
    assert published == [("PRIVACY_MODE_CHANGED", {"privacy_mode": True}),
                         ("PRIVACY_MODE_CHANGED", {"privacy_mode": False})]
