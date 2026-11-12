"""Research & advanced intelligence: one place that wires and routes the four laboratories.

==========================  ======================================  ==========================================
Laboratory                  Derived from                            Runs on
==========================  ======================================  ==========================================
AI Robustness Laboratory    DARPA GARD (ART installed)              app/robustness.py
AI Cyber Defense Center     DARPA AIxCC (pipeline reproduced)       app/code_defense.py
Software Analysis Lab       NSA Ghidra (installed, hash-pinned)     app/reverse_engineer.py (already in HAYAT)
Model Discovery Center      DARPA D3M (rebuilt on scikit-learn)     app/model_discovery.py
==========================  ======================================  ==========================================

``lab_request`` reads a plain request and names the laboratory tool that answers it, so "how robust are you?",
"scan your code for security problems" and "can you predict price from D:/data/sales.csv" each reach the right
one. The laboratories feed each other where that is real: the Robustness Laboratory attacks the model that Model
Discovery last fitted, and its injection and misspelling suites are part of the keep-only-gains gate.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

_YOU = r"(?:you|u|your|ur|hayat|hayat's|yourself|your\s+own|its\s+own)"
_ROBUST = re.compile(
    rf"(?i)\bhow\s+robust\s+(?:are|is)\s+{_YOU}\b|\b(?:run|start|do)\s+(?:the\s+|your\s+|a\s+)?robustness\s+(?:tests?|suite|check|lab)"
    rf"|\b(?:test|check|measure)\s+{_YOU}\s+(?:own\s+)?robustness\b|\brobustness\s+(?:report|results|history)\b"
    r"|\b(?:test|check)\s+(?:your|the)\s+(?:prompt[- ])?injection\s+(?:guard|defen[cs]es?)\b")
_CODE = re.compile(
    rf"(?i)\b(?:scan|check|review|audit|inspect)\s+{_YOU}\s+(?:own\s+)?(?:source\s+)?code\s+for\s+"
    r"(?:security|vulnerabilit\w+|weakness\w*|flaws|bugs|defects)"
    rf"|\bsecurity\s+(?:review|scan|audit)\s+of\s+{_YOU}\s+(?:own\s+)?(?:source\s+)?code\b"
    r"|\b(?:show|list)\s+(?:me\s+)?(?:the\s+|your\s+)?(?:code\s+)?security\s+findings\b")
_PACKAGES = re.compile(
    rf"(?i)\b(?:check|scan|audit)\s+{_YOU}\s+(?:own\s+)?(?:packages|dependencies|libraries)\s+for\s+"
    r"(?:known\s+)?(?:vulnerabilit\w+|security|advisories)"
    r"|\bare\s+(?:any\s+of\s+)?your\s+(?:packages|dependencies|libraries)\s+vulnerable\b")
_TABLE = re.compile(
    r"(?i)\b(?:predict|model|forecast)\s+(?:the\s+)?(?P<target>[\w][\w ]{0,40}?)\s+(?:from|in|using|with)\s+"
    r"[\"'“]?(?P<source>[^\"'”\s][^\"'”]*?\.(?:csv|tsv))")
_MARKET = re.compile(
    # Not "is it predictable", "is that predictable": a symbol is not a pronoun or a filler word.
    r"(?i)\b(?:is|are)\s+(?!(?:it|this|that|they|these|those|he|she|life|so|very|not|more|less|really|still)\b)"
    r"(?P<symbol>[a-z]{2,10})\s+(?:prices?\s+|returns?\s+)?predictable\b"
    r"|\b(?:discover|find|build|search\s+for)\s+a\s+(?:predictive\s+)?model\s+for\s+(?P<symbol2>[a-z]{2,10})\b")
_FILE = r"[\"'“]?(?P<source>[^\"'”\s][^\"'”]*?\.(?:csv|tsv))"
_CAUSAL = re.compile(
    # Only with a table named: "does coffee cause cancer" is a question for research, not for this laboratory.
    r"(?i)\b(?:does|do|did)\s+(?P<treatment>[\w][\w ]{0,40}?)\s+(?:cause|causes|affect|affects|change|changes|"
    r"increase|increases|reduce|reduces|improve|improves)\s+(?P<outcome>[\w][\w ]{0,40}?)\s+(?:in|from|using|with)\s+" + _FILE
    + r"|\b(?:causal\s+)?(?:effect|impact)\s+of\s+(?P<treatment2>[\w][\w ]{0,40}?)\s+on\s+(?P<outcome2>[\w][\w ]{0,40}?)\s+"
    r"(?:in|from|using|with)\s+" + _FILE.replace("source", "source2"))
_RECENT = re.compile(r"(?i)\b(?:show|list)\s+(?:me\s+)?(?:your\s+|the\s+)?(?:model\s+discover\w+|discovered\s+models)\b")


def lab_request(text: str) -> tuple[str, dict[str, Any]] | None:
    """(tool, arguments) for a request one of the laboratories answers, or None."""
    value = " ".join(str(text or "").split())
    if _ROBUST.search(value):
        asks_report = bool(re.search(r"(?i)\b(?:report|results|history)\b", value))
        return "robustness_lab", {"action": "report"} if asks_report else {"action": "run", "suite": "all"}
    if _PACKAGES.search(value):
        return "code_defense", {"action": "dependencies"}
    if _CODE.search(value):
        return "code_defense", {"action": "findings"} if re.search(r"(?i)\b(?:show|list)\b", value) else {"action": "scan"}
    causal = _CAUSAL.search(value)
    if causal:
        def column(text: str) -> str:
            text = re.sub(r"(?i)^(?:the|a|an|column|field)\s+", "", text.strip())
            return text.replace(" ", "_") if " " in text else text
        return "causal_analyze", {"action": "estimate", "source": (causal.group("source") or causal.group("source2")).strip(),
                                  "treatment": column(causal.group("treatment") or causal.group("treatment2")),
                                  "outcome": column(causal.group("outcome") or causal.group("outcome2"))}
    table = _TABLE.search(value)
    if table:
        target = re.sub(r"(?i)^(?:column|field)\s+", "", table.group("target").strip())
        return "model_discover", {"action": "discover", "source": table.group("source").strip(),
                                  "target": target.replace(" ", "_") if " " in target else target}
    market = _MARKET.search(value)
    if market:
        return "model_discover", {"action": "discover",
                                  "source": "market:" + (market.group("symbol") or market.group("symbol2")).upper()}
    if _RECENT.search(value):
        return "model_discover", {"action": "recent"}
    return None


def direct_lab_plan(text: str, names: Any) -> list[dict[str, Any]] | None:
    found = lab_request(text)
    if found is None or found[0] not in set(names):
        return None
    tool, arguments = found
    return [{"description": "Run the laboratory tool that answers this.", "tool": tool, "arguments": arguments,
             "expected": {"type": "tool_success"}, "max_retries": 0}]


#: Read from the project's published advisories on the date given; re-check when upgrading.
GHIDRA_ADVISORIES = {
    "checked": "2026-11-09", "published": "2026-10-22", "fixed_in": (12, 2),
    "source": "https://github.com/NationalSecurityAgency/ghidra/security/advisories",
    "examples": "GHSA-qm7x-8wxx-mpr5, a file write through a bundled script (high); GHSA-896f-pqhq-j5v9, memory "
                "exhaustion from a crafted Mach-O file (moderate)",
}

PROGRAM_REFERENCES = {
    "GARD": "https://www.darpa.mil/research/programs/guaranteeing-ai-robustness-against-deception",
    "AIxCC": "https://www.darpa.mil/news/2025/aixcc-results",
    "D3M": "https://www.darpa.mil/research/programs/data-driven-discovery-of-models",
    "Ghidra": "https://github.com/NationalSecurityAgency/ghidra/blob/master/GhidraDocs/GettingStarted.md",
}


def evidence_lines(result: dict[str, Any]) -> list[str]:
    """Compact recorded evidence for the laboratory UI, never a claim of certification."""
    lines = []
    evaluation = result.get("evaluation") or {}
    if evaluation:
        lines.append("Evaluation: " + str(evaluation.get("protocol", "recorded protocol")))
        for key, label in (("dataset_sha256", "Dataset"), ("split_sha256", "Validation split"),
                           ("test_sha256", "Test inputs"), ("patch_sha256", "Patch"), ("base_commit", "Base commit")):
            if evaluation.get(key):
                lines.append(f"{label}: {str(evaluation[key])[:16]}…")
        if evaluation.get("seed") is not None:
            lines.append(f"Random seed: {evaluation['seed']}")
        if evaluation.get("reproducibility"):
            lines.append(str(evaluation["reproducibility"]))
        installed = evaluation.get("environment") or {}
        if installed:
            lines.append("Environment: Python " + str(installed.get("python", "?")) + "; "
                         + ", ".join(f"{name} {value}" for name, value in (installed.get("packages") or {}).items()))
    comparison = result.get("comparison") or {}
    if comparison:
        if comparison.get("comparable"):
            lines.append(f"Prior compatible run: {comparison['before']} → {comparison['after']}; "
                         f"change {comparison['delta']:+g} ({comparison['direction']} is better). "
                         "Descriptive comparison, not independent confirmation.")
        else:
            lines.append("Run comparison unavailable: " + str(comparison.get("reason", "inputs differ")))
    if "baseline_tests_passed" in result:
        label = lambda value: "passed" if value is True else "failed" if value is False else "not measured"
        lines.append(f"Named tests: before {label(result['baseline_tests_passed'])}; after {label(result.get('tests_passed'))}.")
    return lines


def ghidra_status(runtime: Any, check_engine: bool = False) -> dict[str, Any]:
    """What is installed for binary analysis, from the files on disk and the engine's own status."""
    folder = PROJECT_ROOT / "runtime" / "ghidra"
    found: dict[str, Any] = {"installed": (folder / "support" / "analyzeHeadless.bat").is_file(),
                             "folder": str(folder), "tool": "Ghidra headless analyzer via the local REA engine"}
    try:
        record = json.loads((folder / "hayat-provenance.json").read_text(encoding="utf-8"))
        found["source"] = record.get("source")
        found["sha256"] = record.get("sha256")
        match = re.search(r"ghidra_([\d.]+)_PUBLIC_(\d{8})", str(record.get("source") or ""))
        if match:
            found["version"], found["released"] = match.group(1), match.group(2)
    except (OSError, ValueError):
        found["source"] = "no provenance record"
    # The installed engine's own properties are authoritative; a download URL
    # can be missing, stale, or describe a different installation.
    try:
        properties = dict(line.split('=', 1) for line in
                          (folder / 'Ghidra' / 'application.properties').read_text(encoding='utf-8').splitlines()
                          if '=' in line and not line.startswith('#'))
        if properties.get('application.version'):
            found['version'] = properties['application.version'].strip()
        found['required_java'] = properties.get('application.java.min', '').strip()
    except OSError:
        pass  # noqa: S110 -- absence is reported by installed/version status below
    version = tuple(int(part) for part in str(found.get("version") or "0").split(".") if part.isdigit())
    if found["installed"] and version and version < GHIDRA_ADVISORIES["fixed_in"]:
        found["advisory_warning"] = (
            f"Ghidra {found.get('version', 'of unknown version')} is older than {'.'.join(map(str, GHIDRA_ADVISORIES['fixed_in']))}, which fixes "
            f"security advisories published on {GHIDRA_ADVISORIES['published']} ({GHIDRA_ADVISORIES['examples']}). "
            f"Until it is upgraded, analyse only files you trust. Checked {GHIDRA_ADVISORIES['checked']} at "
            f"{GHIDRA_ADVISORIES['source']}; two of the ten listed advisories were opened and both name this fix.")
        found['upgrade_next'] = ('Run python -m scripts.setup_native_analysis all --upgrade, then '
                                 'python -m scripts.setup_rea, then python -m scripts.verify_native_analysis.')
    found["java"] = (PROJECT_ROOT / "runtime" / "java" / "bin" / "java.exe").is_file()
    try:
        release = (PROJECT_ROOT / 'runtime/java/release').read_text(encoding='utf-8')
        match = re.search(r'^JAVA_VERSION="([^"]+)"', release, re.M)
        if match:
            found['java_version'] = match.group(1)
    except OSError:
        pass  # noqa: S110 -- java availability is already returned
    compatibility = PROJECT_ROOT / 'runtime/rea/node_modules/rea-agents/hayat-ghidra-compatibility.json'
    try:
        found['compatibility'] = json.loads(compatibility.read_text(encoding='utf-8'))
        found['compatibility_note'] = ('Ghidra 12.2 uses HAYAT’s local compatibility patch for REA 6.8.0. '
                                       'Upstream verifies 12.1.4; local checks cover the generated x64 PE fixture.')
    except (OSError, ValueError):
        pass  # noqa: S110 -- upstream installations have no local compatibility patch
    qualification_path = PROJECT_ROOT / ".test-runs/native-analysis/verification.json"
    found["qualification"] = {"available": False, "current_installation_retested": False,
                              "scope": "No saved generated-x64 qualification was found; installation is not an analysis test."}
    try:
        if qualification_path.stat().st_size > 4 * 1024**2:
            raise ValueError("The saved native qualification exceeds the report size limit.")
        qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
        if not isinstance(qualification, dict):
            raise ValueError("The saved qualification is not a report object.")
        from app.reverse_engineer import installed_version

        versions_match = (all(qualification.get(key) for key in ("ghidra_version", "java_version", "rea_version"))
                          and qualification.get("ghidra_version") == found.get("version")
                          and qualification.get("java_version") == found.get("java_version")
                          and qualification.get("rea_version") == installed_version())
        recorded_success = all(qualification.get(key) is True for key in
                               ("passed", "source_unchanged", "decompiled_return_42")) and qualification.get("executed") is False
        found["qualification"] = {"available": True, "checked_at": qualification.get("checked_at"),
                                  "recorded_success": recorded_success, "versions_match": versions_match,
                                  "current_installation_retested": False, "path": str(qualification_path),
                                  "scope": qualification.get("qualification_limits") or "Generated x64 PE fixture only.",
                                  "limits": "Saved local evidence only; matching version labels do not prove current file integrity or qualify other formats."}
    except (OSError, ValueError):
        pass  # noqa: S110 -- missing/invalid saved evidence remains explicitly unavailable
    engineer = getattr(runtime, "reverse_engineer", None)
    if engineer is not None:
        try:
            found["engine"] = engineer.status(check_engine=check_engine)
        except Exception as error:
            log_swallowed(_log, "reading the reverse-engineering engine's status")
            found["engine"] = {"error": type(error).__name__}
    found["limits"] = ("Static analysis only: a file is disassembled and decompiled, never run. Decompiled code is a "
                       "reconstruction and can be wrong about names and types.")
    found["next"] = "Open Reverse engineering, choose a file you are authorised to analyse, and start with its overview."
    found["reference"] = PROGRAM_REFERENCES["Ghidra"]
    return found


def install(runtime: Any, registry: Any, folder: Path, events: Any) -> None:
    """Create the three new laboratories, register their tools and connect their events."""
    from app.code_defense import CodeDefense, register_code_defense_tools
    from app.model_discovery import ModelDiscovery, register_model_discovery_tools
    from app.robustness import RobustnessLab, register_robustness_tools

    runtime.model_discovery = ModelDiscovery(folder / "model-discovery.db", runtime=lambda: runtime)
    runtime.robustness = RobustnessLab(folder / "robustness.db", runtime.model_discovery)
    runtime.code_defense = CodeDefense(folder / "code-defense.db", runtime=lambda: runtime)
    for part in (runtime.model_discovery, runtime.robustness, runtime.code_defense):
        part.events = events
    register_model_discovery_tools(registry, runtime.model_discovery)
    register_robustness_tools(registry, runtime.robustness)
    register_code_defense_tools(registry, runtime.code_defense)
    from app.causal_lab import CausalLab, register_causal_tools

    # The same table loader as model discovery, so its size limits and market tables apply to both.
    runtime.causal_lab = CausalLab(folder / "causal-lab.db", tables=runtime.model_discovery.table_for)
    runtime.causal_lab.events = events
    register_causal_tools(registry, runtime.causal_lab)
    from app.graph_network import GraphLab, register_graph_tools

    # Reads the memory store's links read-only, wherever this runtime keeps it; never writes a link.
    runtime.graph_lab = GraphLab(folder / "graph-lab.db",
                                 memory_path=lambda: getattr(getattr(runtime, "memory", None), "path", folder / "agent.db"))
    runtime.graph_lab.events = events
    register_graph_tools(registry, runtime.graph_lab)
    from app.fuzz_lab import FuzzLab, register_fuzz_tools

    runtime.fuzz_lab = FuzzLab(folder / "fuzz-lab.db")
    runtime.fuzz_lab.events = events
    register_fuzz_tools(registry, runtime.fuzz_lab)
    from app.skill_lab import SkillLab, register_skill_tools

    runtime.skill_lab = SkillLab(folder / "skill-lab.db")
    runtime.skill_lab.events = events
    register_skill_tools(registry, runtime.skill_lab)
