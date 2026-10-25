from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.cli import HELP
from app.config import PROJECT_ROOT


DOCS = PROJECT_ROOT / "docs"


def markdown_files() -> list[Path]:
    return [PROJECT_ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]


def test_no_document_is_empty():
    """docs/models.md was once committed empty by a write that truncated before it read."""
    empty = [str(path.relative_to(PROJECT_ROOT)) for path in markdown_files() if not path.read_text(encoding="utf-8").strip()]
    assert not empty, empty


def test_internal_markdown_links_resolve():
    missing: list[str] = []
    pattern = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
    for document in markdown_files():
        for raw in pattern.findall(document.read_text(encoding="utf-8")):
            target = raw.strip().strip("<>").split("#", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (document.parent / target).resolve().exists():
                missing.append(f"{document.relative_to(PROJECT_ROOT)} -> {target}")
    assert not missing, "Broken documentation links:\n" + "\n".join(missing)


def test_documented_primary_commands_exist_in_cli_help():
    command_doc = (DOCS / "commands.md").read_text(encoding="utf-8")
    primary = set(re.findall(r"(?m)(?<![A-Za-z0-9])(/[-a-z]+)", command_doc))
    missing = sorted(command for command in primary if command not in HELP)
    assert not missing, f"Documented commands missing from CLI help: {missing}"


def test_documented_scripts_and_entrypoints_exist():
    expected = [
        "setup.ps1", "start.ps1", "main.py", "scripts/doctor.py",
        "scripts/camera_hardware_check.py", "scripts/generate_reference.py",
        "scripts/release_gate.py",
        "app/voice/test_microphone.py", "app/voice/test_speaker.py",
    ]
    missing = [value for value in expected if not (PROJECT_ROOT / value).is_file()]
    assert not missing, f"Documented files missing: {missing}"


def test_yaml_configuration_parses():
    for name in ("settings.yaml", "models.yaml", "permissions.yaml"):
        parsed = yaml.safe_load((PROJECT_ROOT / "config" / name).read_text(encoding="utf-8"))
        assert isinstance(parsed, dict) and parsed


def test_security_doc_does_not_describe_a_stricter_live_policy_than_config():
    """Stale examples in security.md previously said deletion/input/typing ASK while YAML ALLOW'd them."""
    live = yaml.safe_load((PROJECT_ROOT / "config" / "permissions.yaml").read_text(encoding="utf-8"))
    document = (DOCS / "security.md").read_text(encoding="utf-8")
    if live.get("permissions", {}).get("filesystem_delete") == "ALLOW":
        assert "Project file deletion: ask." not in document
    if live.get("permissions", {}).get("computer_input") == "ALLOW":
        assert "Mouse and keyboard input: ask." not in document
    assert "High-consequence categories" in document


def test_readme_status_is_grounded_in_implementation():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    for module in (
        "app/policy/cost.py", "app/perception/camera/manager.py",
        "app/perception/displays.py", "app/perception/clipboard.py",
        "app/observability/manager.py", "app/events.py",
    ):
        assert (PROJECT_ROOT / module).is_file(), module
    assert "Camera OCR | Working" in readme
    assert "Fine-tuning | Gated" in readme


def test_generated_reference_is_current():
    from scripts.generate_reference import TARGET, render

    assert TARGET.is_file()
    assert TARGET.read_text(encoding="utf-8") == render()


def test_hayat_model_provenance_agrees_across_executable_and_metadata():
    import json

    models = yaml.safe_load((PROJECT_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    manifest = json.loads((PROJECT_ROOT / "models" / "hayat" / "manifest.json").read_text(encoding="utf-8"))
    modelfile = (PROJECT_ROOT / "models" / "hayat" / "Modelfile").read_text(encoding="utf-8")
    declared = next(
        line.strip().split(maxsplit=1)[1]
        for line in modelfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    )
    configured = models["identity_model"]["base_model"]
    assert declared == configured == manifest["base_model"]
