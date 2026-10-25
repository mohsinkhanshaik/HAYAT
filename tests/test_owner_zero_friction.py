"""Zero friction for the owner's real work, and no movement in the line that matters.

Widening what runs without asking is only safe if the widening is bounded by
something a future edit cannot quietly step over. These tests are that bound:
they assert both halves -- that everyday work never prompts, and that nothing
outward-facing, irreversible, or self-authorising came along for the ride.
"""
from __future__ import annotations

import pytest

from app.safety.authority import (
    CATEGORY_TIERS,
    NEVER_AUTO_CATEGORIES,
    OWNER_AUTO_CATEGORIES,
    ActionRequest,
    AuthorityManager,
    Decision,
    RiskTier,
)


def _request(category: str, **kwargs) -> ActionRequest:
    return ActionRequest(tool=kwargs.pop("tool", category), category=category, **kwargs)


# ------------------------------------------------------------- the wider door


#: The work the owner actually does all day, including what used to prompt.
EVERYDAY = [
    "filesystem_read", "filesystem_write_project", "filesystem_delete",
    "application_launch", "application_discover", "computer_input", "computer_observe",
    "camera_use", "camera_status", "clipboard_read", "clipboard_write",
    "web_search", "public_web_read", "bounded_crawl", "public_api_get",
    "browser_read", "browser_click", "browser_type", "browser_download",
    "public_download", "python_project", "powershell_safe", "powershell_unknown",
    "install_package_project", "install_system_software", "system_read",
    "dependency_inspect",
]


@pytest.mark.parametrize("category", EVERYDAY)
def test_everyday_work_never_stops_to_ask(category):
    authority = AuthorityManager(owner_mode=True)
    decision = authority.evaluate(_request(category))
    assert decision.allowed, f"{category} interrupted the owner"


def test_a_long_working_session_produces_no_interruptions_at_all():
    """The fatigue benchmark, at the widened boundary: 200 real operations."""
    authority = AuthorityManager(owner_mode=True)
    interruptions = [
        category for category in EVERYDAY * 8
        if not authority.evaluate(_request(category)).allowed
    ]
    assert interruptions == [], f"{len(interruptions)} interruptions: {sorted(set(interruptions))}"


def test_deleting_and_installing_no_longer_prompt():
    """Explicitly named because these are the ones that changed."""
    authority = AuthorityManager(owner_mode=True)
    for category in ("filesystem_delete", "install_system_software", "powershell_unknown", "browser_download"):
        assert authority.evaluate(_request(category)).allowed


# ------------------------------------------------------ the line that held


#: Acting as the owner toward other people and services. Never inferred.
STILL_ASKS = ["login_account", "submit_form", "external_post", "send_message",
              "powershell_admin", "admin_elevate"]

#: Irreversible, spends money, or removes the owner's ability to supervise.
STILL_REFUSED = ["credential_access", "financial_transaction", "purchase",
                 "modify_system", "delete_system_file", "bypass_access_control",
                 "modify_own_permissions"]


@pytest.mark.parametrize("category", STILL_ASKS)
def test_outward_facing_actions_still_stop_for_a_human(category):
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request(category)).decision is Decision.REQUIRE_APPROVAL


@pytest.mark.parametrize("category", STILL_REFUSED)
def test_irreversible_actions_are_still_refused_outright(category):
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request(category)).decision is Decision.DENY


def test_owner_mode_cannot_pre_authorise_anything_above_the_line():
    """The invariant as a test, not only as an import-time assertion."""
    assert not (OWNER_AUTO_CATEGORIES & NEVER_AUTO_CATEGORIES)
    for category in OWNER_AUTO_CATEGORIES:
        assert CATEGORY_TIERS[category] <= RiskTier.EXTENDED


def test_every_category_is_classified():
    """An unclassified category defaults to EXTENDED, which is now auto-approved
    -- so a missing entry would silently become zero-friction. Catch it here."""
    for category in (*EVERYDAY, *STILL_ASKS, *STILL_REFUSED):
        assert category in CATEGORY_TIERS, f"{category} has no explicit risk tier"


# ---------------------------------------------- the agent's own authority


def test_the_agent_cannot_widen_its_own_authority():
    """The one property that would make every other line meaningless."""
    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(_request("modify_own_permissions")).decision is Decision.DENY
    with pytest.raises(PermissionError):
        authority.grant("modify_own_permissions")


def test_external_content_gains_nothing_from_the_wider_door():
    """Owner mode is about the owner. A scraped page is still not a requester
    -- and it now has more categories it could have tried to reach."""
    from app.safety.authority import Principal

    authority = AuthorityManager(owner_mode=True)
    for category in EVERYDAY:
        decision = authority.evaluate(_request(category, principal=Principal.UNTRUSTED_EXTERNAL_CONTENT))
        assert decision.decision is Decision.DENY, f"web content reached {category}"


def test_privacy_mode_still_overrides_the_wider_door():
    authority = AuthorityManager(owner_mode=True)
    authority.set_privacy_mode(True)
    for category in ("camera_use", "clipboard_read", "computer_observe"):
        assert not authority.evaluate(_request(category)).allowed


# ------------------------------------------------------------- where it works


def _permissions():
    from app.safety.permissions import PermissionManager

    return PermissionManager()


def test_the_agent_can_work_outside_its_own_directory_now():
    """`allowed_roots` used to sit unread in the config while the code
    hard-coded the project directory -- so the config described a behaviour it
    did not have, and the agent could not open a file on the owner's Desktop."""
    import os

    permissions = _permissions()
    home = os.path.expandvars("%USERPROFILE%")
    for path in (rf"{home}\Desktop\note.txt", rf"{home}\Documents\report.md", r"D:\AI\app\runtime.py"):
        assert permissions.validate_project_path(path)


@pytest.mark.parametrize(
    "path",
    [
        r"C:\Windows\System32\drivers\etc\hosts",
        r"C:\Program Files\anything.exe",
        r"C:\Users\Public\somebody_elses.txt",
    ],
)
def test_widening_where_did_not_widen_into_windows(path):
    with pytest.raises(PermissionError):
        _permissions().validate_project_path(path)


@pytest.mark.parametrize(
    "name",
    [r"D:\AI\.env", r"D:\AI\.env.production", r"D:\AI\key.pem", r"D:\AI\id_rsa",
     r"D:\AI\.git-credentials"],
)
def test_credential_files_are_refused_wherever_they_live(name):
    with pytest.raises(PermissionError):
        _permissions().validate_project_path(name)


def test_the_agents_own_authority_store_is_out_of_reach_of_file_tools():
    """Changing what the agent may do is a permission change, not a file edit."""
    with pytest.raises(PermissionError):
        _permissions().validate_project_path(r"D:\AI\data\memory\authority.db")


# ------------------------------------------------------------------ elevation


def test_elevation_is_observed_rather_than_assumed():
    from app.safety.elevation import is_elevated, status

    report = status()
    assert report["elevated"] is is_elevated()
    assert report["integrity_level"] in {"HIGH", "MEDIUM", "ROOT", "USER"}
    assert report["explanation"]


def test_commands_needing_administrator_are_identified_before_running():
    from app.safety.elevation import requires_elevation

    assert requires_elevation("New-Service -Name Foo -BinaryPathName C:/x.exe")
    assert requires_elevation("reg add HKLM\\Software\\Test /v A /d 1")
    assert requires_elevation("netsh advfirewall set allprofiles state off")
    assert not requires_elevation("Get-ChildItem D:/AI")
    assert not requires_elevation("python -m pytest -q")


def test_preflight_explains_the_remedy_instead_of_failing_late():
    from app.safety.elevation import is_elevated, preflight

    runnable, explanation = preflight("bcdedit /set testsigning on")
    assert runnable is is_elevated()
    if not runnable:
        assert "elevated terminal" in explanation
