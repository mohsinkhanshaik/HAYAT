"""The recorded review of the static-analysis findings in HAYAT's own code (reviewed 2026-11-10).

Bandit flags patterns. Each entry here is a conclusion a reviewer reached by reading the flagged code, with the
reason, so the open list can show what still needs eyes instead of 421 undifferentiated rows. A finding that matches
no entry is unreviewed; tests/test_code_review.py fails when a medium or high finding in the source is unreviewed,
so a new one cannot arrive unnoticed.

Two depths, stated on every entry:

* ``line``: every flagged line of that rule in that file was read (all 76 medium findings).
* ``pattern``: the rule was reviewed as a pattern across the code, not line by line (the low findings).

Nothing here changes a finding's status: marking one as deliberate stays the owner's decision in the Research lab.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from typing import Any

REVIEWED_ON = "2026-11-10"


@dataclass(frozen=True)
class Review:
    rule: str
    paths: tuple[str, ...]          # fnmatch patterns on the project-relative path; ("*",) is everywhere
    verdict: str                    # "false positive" | "deliberate" | "safe by construction"
    reason: str
    depth: str = "line"
    contains: str = ""              # the flagged code must contain this, so an unrelated new line is not covered


_SQL_PLACEHOLDERS = ("Only '?' placeholders, fixed clause text or a column chosen in code from two constants is put "
                     "into the statement; every value is bound as a parameter.")

REVIEWS: tuple[Review, ...] = (
    # ---- B608: SQL built from strings (50 lines read)
    Review("B608", ("app/anpr/store.py", "app/automation/jobs.py", "app/brain_history.py", "app/intelligence/store.py",
                    "app/jobs/store.py", "app/learning/continuous.py", "app/market_intelligence.py",
                    "app/memory/database.py", "app/memory/plasticity.py", "app/memory/retention.py",
                    "app/money_desk.py", "app/recovery.py", "app/research/knowledge.py", "app/runtime.py",
                    "app/topics.py", "app/training/pipeline.py", "app/graph_network.py"),
           "safe by construction", _SQL_PLACEHOLDERS),
    Review("B608", ("app/intelligence/discovery.py",), "safe by construction",
           "The status filter is fixed text. The UPDATE's column list comes from keyword names, which since this "
           "review must be plain identifiers (letters, digits, underscore) or the call is refused."),
    # ---- B615: Hugging Face downloads without a pinned revision (12 lines read)
    Review("B615", ("app/models/hayat_open.py", "scripts/*.py"), "false positive",
           "Every call loads from a folder on this computer with local_files_only=True (or the 'json' loader over "
           "local files) and trust_remote_code=False: nothing is downloaded, so there is no revision to pin."),
    # ---- B104: binding to all interfaces (7 lines read)
    Review("B104", ("app/failures.py", "app/models/local.py", "app/outbound.py", "app/web/server.py"), "false positive",
           "The address is a string in a list used to recognise local endpoints, or a check that warns about it; "
           "nothing is bound here."),
    Review("B104", ("app/csi.py",), "deliberate",
           "The Wi-Fi sensing receiver listens for UDP from an ESP32 on the local network, so its default is every "
           "interface. Packets are accepted only from private, loopback or link-local addresses (or the one sender "
           "named in allow_from) and are parsed as numbers. Set a host to narrow it."),
    # ---- single findings
    Review("B108", ("app/safety/container_sandbox.py",), "false positive",
           "'/tmp' is the mount point of a size-limited tmpfs inside the sandbox container, not a file on this computer."),
    Review("B307", ("app/invention_sim.py",), "deliberate",
           "eval runs a formula whose syntax tree was checked node by node against an allow-list (numbers, names, "
           "arithmetic, a fixed set of functions) with no builtins. Keep any change to that check under review."),
    Review("B310", ("app/launcher.py", "app/network.py"), "safe by construction",
           "The address is the local model server's or the router's own; since this review both refuse anything "
           "that is not http or https, so a file: or custom scheme cannot be opened."),
    Review("B604", ("app/tools/terminal.py",), "deliberate",
           "This is the terminal tool: running a shell command is its purpose. Each command passes the command "
           "policy and the authority gate first, and the owner approves what the policy does not allow outright."),
    # ---- low findings: reviewed as patterns, not line by line
    Review("B404", ("*",), "deliberate", "HAYAT starts local programs (git, ffmpeg, the model server, analysers); "
           "importing subprocess is how.", "pattern"),
    Review("B603", ("*",), "safe by construction", "Arguments are passed as a list with no shell, so they are not "
           "re-parsed as a command line. Where an argument comes from input, that call site has its own checks; "
           "those were not re-read one by one in this review.", "pattern"),
    Review("B607", ("*",), "deliberate", "Programs such as git and netsh are found on PATH, as on any workstation. "
           "A machine whose PATH an attacker controls is already lost.", "pattern"),
    Review("B606", ("*",), "safe by construction", "Started without a shell: no command-line parsing.", "pattern"),
    Review("B605", ("*",), "safe by construction", "A fixed command with no input in it.", "pattern"),
    Review("B311", ("*",), "false positive", "Random numbers here drive sampling, jitter, test data and simulations, "
           "not secrets. Tokens and keys use the secrets module.", "pattern"),
    Review("B110", ("*",), "deliberate", "Each silent except carries a written reason or logs through "
           "log_swallowed; the lint gate (S110) refuses a bare one.", "pattern"),
    Review("B112", ("*",), "deliberate", "A loop that skips an item it cannot use and carries on.", "pattern"),
    Review("B105", ("*",), "false positive", "Labels, setting names and help text that contain words like 'token' "
           "or 'password'; no credential is written in the source.", "pattern"),
    Review("B106", ("*",), "false positive", "Provider web addresses and field labels passed as arguments whose "
           "names contain 'token'; not credentials.", "pattern"),
    Review("B107", ("*",), "false positive", "Empty-string defaults for optional credential parameters.", "pattern"),
    Review("B405", ("*",), "false positive", "Only the ParseError name is imported, to catch it.", "pattern"),
    Review("B406", ("*",), "false positive", "xml.sax.saxutils.escape escapes output; it parses nothing.", "pattern"),
)
#: Reviewed as a pattern and deliberately NOT concluded: these need a person to read each one.
UNREVIEWED_RULES = {"B101": "24 assert statements in application code. An assert disappears under python -O, so "
                            "each needs checking that it guards an invariant, not a security decision."}


def review_of(finding: dict[str, Any]) -> Review | None:
    """The recorded conclusion that covers this finding, or None when nobody has reviewed it."""
    rule, path, code = str(finding.get("rule") or ""), str(finding.get("path") or "").replace("\\", "/"), \
        str(finding.get("evidence") or "")
    for review in REVIEWS:
        if review.rule == rule and any(fnmatch(path, pattern) for pattern in review.paths) \
                and (not review.contains or review.contains in code):
            if review.depth == "line" and not _line_is_current(rule, path, code):
                return None
            return review
    return None


@lru_cache(maxsize=1)
def _snapshot() -> dict:
    try:
        value = json.loads(Path(__file__).with_name("code_review_snapshot.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("version") == 1 else {}
    except (OSError, ValueError):
        # A missing/corrupt applicability record withdraws coverage; it never fabricates a review.
        return {}


def _source_digest(path: str) -> str | None:
    from app.config import PROJECT_ROOT

    try:
        resolved = (PROJECT_ROOT / path).resolve()
        if not resolved.is_relative_to(PROJECT_ROOT.resolve()):
            return None
        return hashlib.sha256(resolved.read_text(encoding="utf-8-sig").encode()).hexdigest()
    except (OSError, ValueError):
        # An unreadable or invalid source cannot establish that an old review still applies.
        return None


def _line_is_current(rule: str, path: str, code: str) -> bool:
    """Old file/rule matches must not silently approve new code or changed surrounding guards."""
    snapshot = _snapshot()
    key = hashlib.sha256(f"{rule}|{path}|{' '.join(code.split())}".encode()).hexdigest()
    record = (snapshot.get("findings") or {}).get(key)
    expected = (snapshot.get("files") or {}).get(path)
    return bool(record == {"rule": rule, "path": path} and expected and _source_digest(path) == expected)


def summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """How many findings a recorded review covers, by depth, and what is left for a person."""
    covered = {"line": 0, "pattern": 0}
    left: list[dict[str, Any]] = []
    for finding in findings:
        review = review_of(finding)
        if review is None:
            left.append(finding)
        else:
            covered[review.depth] += 1
    return {"reviewed_on": REVIEWED_ON, "findings": len(findings), "read_line_by_line": covered["line"],
            "reviewed_as_a_pattern": covered["pattern"], "unreviewed": len(left),
            "unreviewed_by_rule": {rule: sum(1 for item in left if item.get("rule") == rule)
                                   for rule in sorted({str(item.get("rule")) for item in left})},
            "needs_a_person": dict(UNREVIEWED_RULES),
            "scope": "Recorded line reviews require the same finding and complete source-file fingerprint; changed "
                     "files need review again. Pattern notes are general guidance, not individual code approval. "
                     "It is not a security audit and does not show "
                     "the code is free of defects."}
