"""Dependency installation as a registered tool.

`install_package_project` has been a permission category since the authority
kernel was written and nothing ever used it -- the tier table described a
capability that did not exist. It exists now, and it goes through the same
dispatch as everything else: ORDINARY tier, so it does not interrupt Mohsin,
and denied outright to the background learner and to anything that arrived
off a web page. An unattended loop installing packages is the one version of
this that is genuinely dangerous.
"""
from __future__ import annotations

from typing import Any

from app.packages import PackageInstaller
from app.tools.registry import ToolRegistry


def register_package_tools(registry: ToolRegistry, installer: PackageInstaller | None = None) -> PackageInstaller:
    packages = installer or PackageInstaller()

    def install_package(name: str, reason: str = "") -> dict[str, Any]:
        result = packages.install(name, reason)
        payload = result.to_dict()
        if result.refused_because:
            payload["error"] = result.refused_because
        elif not result.ok:
            payload["error"] = result.detail or "the install did not succeed"
        return payload

    def check_package(name: str) -> dict[str, Any]:
        """Look a package up without installing it."""
        return packages.review(name)

    def uninstall_package(name: str) -> dict[str, Any]:
        return packages.uninstall(name).to_dict()

    def package_status() -> dict[str, Any]:
        return packages.status()

    registry.register(
        "install_package",
        "Install a Python package into this project's environment, after checking the name "
        "is real and not a near-miss of a popular package.",
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The package name on the index."},
                "reason": {"type": "string", "description": "Why it is needed."},
            },
            "required": ["name"],
        },
        "medium", "install_package_project", install_package,
    )
    registry.register(
        "check_package",
        "Check whether a package exists and is safe to install, without installing it.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "low", "dependency_inspect", check_package,
    )
    registry.register(
        "uninstall_package",
        "Remove a package previously installed into this project's environment.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "medium", "install_package_project", uninstall_package,
    )
    registry.register(
        "package_status",
        "Report what has been installed, what was refused, and which environment is used.",
        {"type": "object", "properties": {}},
        "low", "dependency_inspect", package_status,
    )
    return packages
