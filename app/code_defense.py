"""Code defence: find weaknesses in HAYAT's own code and packages, rank them, and test a fix before anyone applies it.

Inspired by DARPA's AI Cyber Challenge (AIxCC). Its finalist systems -- Team Atlanta's Atlantis, Trail of Bits'
Buttercup, and the OSS-CRS framework -- were not installed (and their repositories were not read when this was
written): as generally described they are multi-container systems driven by commercial language-model APIs, built
to fuzz C and Java projects. HAYAT is Python on one PC with a no-paid-services rule. What such systems have in
common is a pipeline, and that is what is reproduced here with maintained local tools:

    find  ->  confirm  ->  rank  ->  propose a patch  ->  test the patch in isolation  ->  a person decides

* **Find.** Bandit (static security review of Python source) and pip-audit (installed packages against the public
  vulnerability database; the only step that uses the network, and it sends package names and versions only).
* **Confirm.** A finding is kept only if the flagged line is still in the file as reported. Tool output is never
  taken on trust.
* **Rank.** Severity times the tool's own confidence; test code is ranked last.
* **Propose a patch.** A patch is a unified diff, written by the owner or by HAYAT's coding agent. This module does
  not write patches itself.
* **Test in isolation.** The diff is applied to a throwaway copy of the repository (a git worktree), the named
  tests are run there, and the touched files are re-scanned. The real tree is never touched.
* **A person decides.** Nothing here applies a patch. A validated patch is reported with the exact command; the
  owner runs it.

Only folders the owner has authorised are scanned: HAYAT's own project folder, or a project registered with HAYAT.
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

LEVEL = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
SCAN_TIMEOUT = 420
TEST_TIMEOUT = 900
#: Folders that are not HAYAT's own source.
EXCLUDED = ("runtime", "data", ".venv", ".test-runs", "download", "node_modules", ".git")
LIMITS = ("Static analysis flags patterns, not proven exploits: many low-severity findings are deliberate and safe. "
          "A clean scan does not prove the code is secure.")


def _run(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,  # noqa: S603
                          encoding="utf-8", errors="replace",
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def fingerprint(rule: str, path: str, code: str) -> str:
    """Stable across line-number shifts: the rule, the file and the flagged code itself."""
    flagged = " ".join(str(code).split())
    return hashlib.sha256(f"{rule}|{path}|{flagged}".encode("utf-8")).hexdigest()[:16]


def _source_path(root: Path, name: str) -> Path:
    """Keep source, report and test paths inside the authorised project."""
    name = str(name).replace("\\", "/")
    relative = Path(name)
    if not name or name.startswith("-") or relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise ValueError("Use a relative path inside the authorised project.")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("A source or test path leaves the authorised project.")
    return resolved


def confirm(root: Path, path: str, line: int, code: str) -> bool:
    """Is the flagged code really at (or right next to) that line of the file now?"""
    try:
        lines = _source_path(root, path).read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return False
    if line < 1 or line > len(lines):
        return False
    numbered = {int(match[1]): match[2].strip() for part in str(code).splitlines()
                if (match := re.match(r"^\s*(\d+)\s+(.*)$", part))}
    # Bandit includes neighbouring lines for context. Confirm the flagged line
    # itself, rather than mistaking a surviving neighbour for the finding.
    wanted = [numbered[line]] if line in numbered else [part.strip() for part in str(code).splitlines() if part.strip()]
    window = {text.strip() for text in lines[max(0, line - 3): line + 2]}
    return bool(wanted) and all(part and part in window for part in wanted)


def bandit_findings(root: Path, targets: list[str]) -> list[dict[str, Any]]:
    for target in targets:
        _source_path(root, target)
    command = [sys.executable, "-m", "bandit", "-r", *targets, "-f", "json", "-q",
               "-x", ",".join(f"./{name}" for name in EXCLUDED)]
    done = _run(command, root, SCAN_TIMEOUT)
    try:
        data = json.loads(done.stdout or "")
    except ValueError as error:
        raise RuntimeError(f"Bandit did not return a report: {(done.stderr or '')[:300]}") from error
    if done.returncode not in (0, 1) or not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise RuntimeError(f"Bandit failed or returned an invalid report (exit {done.returncode}).")
    if data.get("errors"):
        raise RuntimeError(f"Bandit could not inspect every selected file ({len(data['errors'])} errors). "
                           "Existing findings were kept; fix the scan errors and run again.")
    found = []
    for item in data.get("results", []):
        if not isinstance(item, dict):
            raise RuntimeError("Bandit returned an invalid finding.")
        try:
            source = _source_path(root, str(item.get("filename", "")))
        except ValueError as error:
            raise RuntimeError("Bandit reported a file outside the authorised project.") from error
        path = source.relative_to(root.resolve()).as_posix()
        code = str(item.get("code", ""))
        found.append({
            "tool": "bandit", "rule": item.get("test_id", ""), "title": item.get("issue_text", ""),
            "path": path, "line": int(item.get("line_number") or 0),
            "severity": item.get("issue_severity", "LOW"), "confidence": item.get("issue_confidence", "LOW"),
            "cwe": (item.get("issue_cwe") or {}).get("id"), "more": item.get("more_info", ""),
            "evidence": code.strip()[:600], "fingerprint": fingerprint(item.get("test_id", ""), path, code),
        })
    return found


def rank(finding: dict[str, Any]) -> int:
    score = LEVEL.get(finding["severity"], 1) * LEVEL.get(finding["confidence"], 1)
    return 0 if finding["path"].startswith("tests/") else score


class CodeDefense:
    SOURCE = "code_defense"

    def __init__(self, path: Path | str, runtime: Any = None, root: Path = PROJECT_ROOT) -> None:
        self.path = Path(path)
        self.runtime = runtime or (lambda: None)
        self.root = Path(root)
        self._lock = threading.Lock()
        self.events: Any = None

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE IF NOT EXISTS findings(
                fingerprint TEXT PRIMARY KEY, project TEXT NOT NULL, tool TEXT NOT NULL, rule TEXT NOT NULL,
                title TEXT NOT NULL, path TEXT NOT NULL, line INTEGER NOT NULL, severity TEXT NOT NULL,
                confidence TEXT NOT NULL, priority INTEGER NOT NULL, evidence TEXT NOT NULL, more TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '', first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, project TEXT NOT NULL, kind TEXT NOT NULL,
                seconds REAL NOT NULL, summary_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS patches(
                id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, project TEXT NOT NULL, diff TEXT NOT NULL,
                tests TEXT NOT NULL, applies INTEGER NOT NULL, tests_passed INTEGER, result_json TEXT NOT NULL);
        """)
        return db

    # ---- authorisation

    def authorised(self, folder: str | Path = "") -> Path:
        """The folder to work on, only if it is HAYAT's own or a project the owner registered."""
        wanted = Path(folder).resolve() if str(folder or "").strip() else self.root.resolve()
        allowed = [self.root.resolve()]
        projects = getattr(self.runtime(), "projects", None)
        try:
            for project in dict(getattr(projects, "projects", {}) or {}).values():
                location = getattr(project, "root", None)
                if location:
                    allowed.append(Path(location).resolve())
        except Exception:
            log_swallowed(_log, "listing registered projects for code defence")
        if wanted not in allowed:
            raise PermissionError("Code defence only inspects HAYAT's own folder or a project you have registered "
                                  f"with HAYAT. {wanted} is neither.")
        return wanted

    # ---- find, confirm, rank

    def scan(self, folder: str | Path = "") -> dict[str, Any]:
        root = self.authorised(folder)
        started = time.perf_counter()
        targets = [name for name in ("app", "scripts", "main.py") if (root / name).exists()] or ["."]
        raw = bandit_findings(root, targets)
        confirmed = [item for item in raw if confirm(root, item["path"], item["line"], item["evidence"])]
        now = datetime.now(UTC).isoformat(timespec="seconds")
        project = str(root)
        with self._lock, closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            # Findings historically used rule/file/code only. Two registered
            # projects can contain the same file; preserve existing ids while
            # assigning a project-specific id when that would collide.
            for item in confirmed:
                owner = db.execute("SELECT project FROM findings WHERE fingerprint=?", (item["fingerprint"],)).fetchone()
                if owner is not None and owner["project"] != project:
                    item["fingerprint"] = hashlib.sha256(f"{project}|{item['fingerprint']}".encode("utf-8")).hexdigest()[:16]
            known = {row["fingerprint"]: row["status"] for row in db.execute(
                "SELECT fingerprint, status FROM findings WHERE project=? AND tool='bandit'", (project,))}
            seen = set()
            for item in confirmed:
                seen.add(item["fingerprint"])
                db.execute(
                    "INSERT INTO findings(fingerprint,project,tool,rule,title,path,line,severity,confidence,priority,"
                    "evidence,more,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fingerprint) DO "
                    "UPDATE SET line=excluded.line, last_seen=excluded.last_seen, priority=excluded.priority, "
                    "status=CASE WHEN findings.status='fixed' THEN 'open' ELSE findings.status END",
                    (item["fingerprint"], project, "bandit", item["rule"], item["title"], item["path"], item["line"],
                     item["severity"], item["confidence"], rank(item), item["evidence"], item["more"], now, now))
            gone = [key for key, status in known.items() if key not in seen and status == "open"]
            for key in gone:
                db.execute("UPDATE findings SET status='fixed', last_seen=? WHERE fingerprint=?", (now, key))
            new = [key for key in seen if key not in known]
            seconds = round(time.perf_counter() - started, 2)
            summary = {"reported_by_tool": len(raw), "confirmed_in_source": len(confirmed), "new": len(new),
                       "no_longer_present": len(gone),
                       "open_by_severity": {level: sum(1 for item in confirmed if item["severity"] == level
                                                       and known.get(item["fingerprint"], "open") != "accepted")
                                            for level in ("HIGH", "MEDIUM", "LOW")}}
            from app.lab_evidence import digest, environment

            summary["evaluation"] = {"protocol": "hayat-local-source-review-v2", "environment": environment("bandit"),
                                     "findings_sha256": digest(sorted(item["fingerprint"] for item in confirmed)),
                                     "scope": "Confirmed static findings; this output digest is not a fingerprint of all source files or proof of security."}
            db.execute("INSERT INTO scans(at,project,kind,seconds,summary_json) VALUES(?,?,?,?,?)",
                       (now, project, "source", seconds, json.dumps(summary)))
        self._publish({"kind": "source", **{key: summary[key] for key in ("confirmed_in_source", "new")}})
        return {"project": project, "tool": "bandit (static security review)", "source": ", ".join(targets),
                "seconds": seconds, **summary, "top": self.findings(limit=8, project=project), "limits": LIMITS,
                "next": "Review the top findings; mark deliberate ones as accepted, and test a patch for the rest."}

    def dependencies(self) -> dict[str, Any]:
        """Installed packages against the public vulnerability database (sends package names and versions only)."""
        runtime = self.runtime()
        if bool(getattr(getattr(runtime, "authority", None), "privacy_mode", False)):
            return {"checked": False, "reason": "Privacy mode is on, so the vulnerability database was not contacted."}
        started = time.perf_counter()
        done = _run([sys.executable, "-m", "pip_audit", "--format", "json", "--progress-spinner", "off"],
                    self.root, SCAN_TIMEOUT)
        try:
            data = json.loads(done.stdout or "")
        except ValueError:
            return {"checked": False, "reason": f"pip-audit gave no report: {(done.stderr or '').strip()[-300:]}"}
        packages = data.get("dependencies") if isinstance(data, dict) else data
        if (done.returncode not in (0, 1) or not isinstance(packages, list)
                or any(not isinstance(item, dict) for item in packages)):
            return {"checked": False, "reason": f"pip-audit failed or returned an invalid report (exit {done.returncode})."}
        skipped = [{"package": item.get("name"), "reason": str(item["skip_reason"])[:300]}
                   for item in packages if item.get("skip_reason")]
        packages = [item for item in packages if not item.get("skip_reason")]
        vulnerable = [{"package": item.get("name"), "version": item.get("version"),
                       "advisories": [{"id": flaw.get("id"), "fixed_in": flaw.get("fix_versions"),
                                       "summary": str(flaw.get("description") or "")[:240]}
                                      for flaw in item.get("vulns", [])]}
                      for item in packages if item.get("vulns")]
        seconds = round(time.perf_counter() - started, 2)
        summary = {"packages_checked": len(packages), "packages_skipped": len(skipped),
                   "vulnerable_packages": len(vulnerable)}
        with self._lock, closing(self._db()) as db, db:
            db.execute("INSERT INTO scans(at,project,kind,seconds,summary_json) VALUES(?,?,?,?,?)",
                       (datetime.now(UTC).isoformat(timespec="seconds"), str(self.root), "dependencies", seconds,
                        json.dumps({**summary, "vulnerable": vulnerable, "skipped": skipped})))
        self._publish({"kind": "dependencies", **summary})
        return {"checked": True, "tool": "pip-audit", "source": "installed packages vs the PyPI advisory database",
                "seconds": seconds, **summary, "vulnerable": vulnerable, "skipped": skipped,
                "limits": "Lists published advisories for the installed versions; whether HAYAT uses the affected "
                          "code path is not checked.",
                "next": "Upgrade to a fixed version where one is listed, then run the tests." if vulnerable
                        else "Review the packages skipped by the audit." if skipped
                        else "No published advisories found for the packages checked; check again after upgrades."}

    def findings(self, limit: int = 50, status: str = "open", project: str = "") -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._lock, closing(self._db()) as db:
            rows = db.execute("SELECT * FROM findings WHERE status=? AND (?='' OR project=?) "
                              "ORDER BY priority DESC, path, line LIMIT ?", (status, project, project, limit)).fetchall()
        from app.code_review import review_of

        found = []
        for row in rows:
            item = dict(row)
            # The recorded review applies to HAYAT's own code only; another project's files were never read.
            review = review_of(item) if Path(item["project"]).resolve() == PROJECT_ROOT.resolve() else None
            if review is not None:
                item["review"] = {"verdict": review.verdict, "reason": review.reason, "depth": review.depth}
            found.append(item)
        return found

    def review_summary(self) -> dict[str, Any]:
        """How much of the open list a recorded review covers, and what is left for a person (HAYAT's own code)."""
        from app.code_review import summary

        mine = [item for item in self.findings(limit=100_000) if Path(item["project"]).resolve() == PROJECT_ROOT.resolve()]
        return summary(mine)

    def sarif(self, project: str = "", include_accepted: bool = False) -> dict[str, Any]:
        """The recorded findings as a SARIF 2.1.0 log, the interchange format code-scanning viewers read.

        Built from the saved findings only: nothing is scanned, and no source text beyond the flagged line already on
        record is included. Paths stay relative to the project, so the file names no folder on this computer.
        A finding marked as accepted is left out unless asked for, and is then carried as a suppression with its note.
        """
        rows = self.findings(limit=100_000, project=project)
        if include_accepted:
            rows += self.findings(limit=100_000, status="accepted", project=project)
        levels = {"HIGH": "error", "MEDIUM": "warning", "LOW": "note"}
        rules: dict[str, dict[str, Any]] = {}
        results = []
        for row in rows:
            rule = str(row["rule"] or "unknown")
            rules.setdefault(rule, {"id": rule, "shortDescription": {"text": str(row["title"])[:200] or rule},
                                    **({"helpUri": row["more"]} if str(row["more"]).startswith("https://") else {})})
            result = {"ruleId": rule, "level": levels.get(row["severity"], "note"),
                      "message": {"text": str(row["title"]) or rule},
                      "locations": [{"physicalLocation": {
                          "artifactLocation": {"uri": str(row["path"]), "uriBaseId": "SRCROOT"},
                          "region": {"startLine": max(1, int(row["line"] or 1)),
                                     "snippet": {"text": str(row["evidence"])}}}}],
                      "partialFingerprints": {"hayatFinding/v1": row["fingerprint"]},
                      "properties": {"severity": row["severity"], "confidence": row["confidence"],
                                     "firstSeen": row["first_seen"], "lastSeen": row["last_seen"]}}
            if row["status"] == "accepted":
                result["suppressions"] = [{"kind": "external", "status": "accepted",
                                           "justification": str(row["note"] or "Marked as deliberate by the owner.")}]
            results.append(result)
        return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
                "runs": [{"tool": {"driver": {"name": "HAYAT code defence (bandit)",
                                              "informationUri": "https://bandit.readthedocs.io/",
                                              "rules": [rules[name] for name in sorted(rules)]}},
                          "results": results,
                          "properties": {"scope": LIMITS, "generated": datetime.now(UTC).isoformat(timespec="seconds"),
                                         "source": "saved findings; no scan was run for this export"}}]}

    def set_status(self, finding: str, status: str, note: str = "") -> dict[str, Any]:
        if status not in {"open", "accepted"}:
            raise ValueError("status must be open or accepted")
        with self._lock, closing(self._db()) as db, db:
            changed = db.execute("UPDATE findings SET status=?, note=? WHERE fingerprint=?", (status, note[:300], finding)).rowcount
        return {"changed": bool(changed), "fingerprint": finding, "status": status}

    # ---- suggest a patch (mechanical fixes only)

    def suggest_patch(self, finding: str) -> dict[str, Any]:
        """A unified diff for a finding whose fix is mechanical; anything needing judgement is left to a person.

        Only one class is handled: a weak hash (MD5/SHA-1) flagged by rule B324. The suggestion marks the call
        ``usedforsecurity=False``, which keeps every stored value identical. That is right only when the hash is
        a cache key, fingerprint or name -- never when it protects something -- so the suggestion says so and
        still has to pass the isolated test and the owner's decision.
        """
        import difflib

        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT * FROM findings WHERE fingerprint=?", (finding,)).fetchone()
        if row is None:
            return {"suggested": False, "reason": "No finding with that id."}
        if row["rule"] != "B324":
            return {"suggested": False, "reason": f"No mechanical fix is known for {row['rule']} ({row['title']}); this "
                                                  "one needs a person or the coding agent."}
        root = self.authorised(row["project"])
        path = _source_path(root, row["path"])
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeError):
            return {"suggested": False, "reason": "The source is unavailable or cannot be parsed; scan it again."}
        if not confirm(root, row["path"], int(row["line"]), row["evidence"]):
            return {"suggested": False, "reason": "The finding no longer matches the source; scan it again."}
        original = source.splitlines(keepends=True)
        index = int(row["line"]) - 1
        line = original[index] if 0 <= index < len(original) else ""
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                 and node.func.value.id == "hashlib" and node.func.attr in {"sha1", "md5"}
                 and node.lineno == index + 1 and node.end_lineno == index + 1
                 and not any(keyword.arg == "usedforsecurity" for keyword in node.keywords)]
        if len(calls) != 1:
            return {"suggested": False, "reason": "The flagged call is not on one line as reported; fix it by hand."}
        call = calls[0]
        # AST columns are UTF-8 byte offsets. Counting parentheses by hand can
        # corrupt a string argument containing ')' or a call with a trailing comma.
        end = len(line.encode("utf-8")[:int(call.end_col_offset) - 1].decode("utf-8"))
        start = len(line.encode("utf-8")[:call.col_offset].decode("utf-8"))
        changed = list(original)
        inside = line[line.index("(", start) + 1:end].strip()
        separator = " " if inside.endswith(",") else ", " if inside else ""
        changed[index] = line[:end] + separator + "usedforsecurity=False" + line[end:]
        diff = "".join(difflib.unified_diff(original, changed, f"a/{row['path']}", f"b/{row['path']}"))
        return {"suggested": True, "diff": diff, "finding": finding, "tool": "mechanical fix for B324",
                "source": f"{row['path']}:{row['line']}",
                "limits": "Correct only if this hash is a cache key, fingerprint or name. If it protects anything "
                          "(passwords, signatures, tokens), use SHA-256 or better instead.",
                "next": "Test this patch in isolation, then decide."}

    # ---- test a patch in isolation

    def validate_patch(self, diff: str, tests: list[str] | None = None, folder: str | Path = "") -> dict[str, Any]:
        """Apply a unified diff to a throwaway copy of the repository, run the named tests and re-scan there."""
        root = self.authorised(folder)
        tests = [str(item) for item in (tests or []) if str(item).strip()]
        if not str(diff or "").strip():
            raise ValueError("Give the patch as a unified diff.")
        if len(diff) > 200_000:
            raise ValueError("The patch is too large for this review (limit: 200,000 characters).")
        for name in tests:
            _source_path(root, name.split("::", 1)[0])
        started = time.perf_counter()
        from app.lab_evidence import environment

        commit = _run(["git", "rev-parse", "HEAD"], root, 60)
        if commit.returncode != 0 or not (commit.stdout or "").strip():
            raise ValueError("Patch validation requires an authorised git repository with a commit.")
        base_commit = commit.stdout.strip()
        result: dict[str, Any] = {"applies": False, "tests_passed": None, "tool": "git worktree + pytest + bandit",
                                  "source": str(root), "baseline_tests_passed": None,
                                  "evaluation": {"protocol": "hayat-local-patch-validation-v2", "base_commit": base_commit,
                                                 "patch_sha256": hashlib.sha256((diff if diff.endswith("\n") else diff + "\n").encode("utf-8")).hexdigest(),
                                                 "tests": tests, "environment": environment("bandit", "pytest"),
                                                 "scope": "Authorised source in a git worktree; no exploit generation or DARPA certification."}}
        with tempfile.TemporaryDirectory(prefix="hayat-patch-") as holder:
            copy = Path(holder) / root.name
            added = _run(["git", "worktree", "add", "--detach", str(copy), base_commit], root, 180)
            if added.returncode != 0:
                raise RuntimeError(f"Could not make an isolated copy: {(added.stderr or '').strip()[-300:]}")
            try:
                patch = Path(holder) / "change.diff"
                patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8", newline="\n")
                paths = _run(["git", "apply", "--numstat", "-z", str(patch)], copy, 120)
                if paths.returncode != 0:
                    raise ValueError(f"The unified diff is invalid: {(paths.stderr or '').strip()[-300:]}")
                touched = [entry.split("\t", 2)[2] for entry in paths.stdout.split("\0") if entry]
                if not touched:
                    raise ValueError("The patch does not contain any file changes.")
                for name in touched:
                    _source_path(copy, name)
                # The same files scanned before the patch: only what the patch adds counts against it.
                existing = [name for name in touched if name.endswith(".py") and (copy / name).is_file()]
                before = {item["fingerprint"] for item in bandit_findings(copy, existing)} if existing else set()
                baseline_tests = [test for test in tests if _source_path(copy, test.split("::", 1)[0]).is_file()]
                if tests and baseline_tests == tests:
                    baseline = _run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                     f"--basetemp={Path(holder) / 'pytest-before'}", *tests], copy, TEST_TIMEOUT)
                    result["baseline_tests_passed"] = baseline.returncode == 0
                    result["baseline_test_output"] = (baseline.stdout or "").strip()[-600:]
                elif tests:
                    result["baseline_test_note"] = "Some named tests are new in this patch; a complete before/after test comparison is unavailable."
                applied = _run(["git", "apply", "--whitespace=nowarn", str(patch)], copy, 120)
                result["applies"] = applied.returncode == 0
                if not result["applies"]:
                    result["apply_error"] = (applied.stderr or "").strip()[-400:]
                else:
                    result["files_changed"] = touched
                    # A patch can introduce a symlink. Re-check resolved paths
                    # after applying it, before opening sources or starting tests.
                    for name in [*touched, *(test.split("::", 1)[0] for test in tests)]:
                        _source_path(copy, name)
                    if tests:
                        ran = _run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                    f"--basetemp={Path(holder) / 'pytest'}", *tests], copy, TEST_TIMEOUT)
                        result["tests_passed"] = ran.returncode == 0
                        result["test_output"] = (ran.stdout or "").strip()[-600:]
                    python = [name for name in touched if name.endswith(".py") and (copy / name).is_file()]
                    if python:
                        try:
                            after = bandit_findings(copy, python)
                        except RuntimeError as error:
                            result["scan_error"] = str(error)
                        else:
                            result["findings_in_changed_files"] = {"before": len(before), "after": len(after)}
                            result["findings_removed"] = len(before - {item["fingerprint"] for item in after})
                            result["new_findings_introduced"] = [
                                {"rule": item["rule"], "path": item["path"], "line": item["line"], "title": item["title"]}
                                for item in after if item["fingerprint"] not in before][:10]
            finally:
                _run(["git", "worktree", "remove", "--force", str(copy)], root, 120)
                _run(["git", "worktree", "prune"], root, 60)
        result["seconds"] = round(time.perf_counter() - started, 2)
        result["validated"] = bool(result["applies"] and result["tests_passed"] is True
                                   and not result.get("scan_error") and not result.get("new_findings_introduced"))
        result["verdict"] = ("The patch does not apply to the current code." if not result["applies"] else
                             "The tests failed with this patch." if result["tests_passed"] is False else
                             "The source scan could not finish; the patch is not validated." if result.get("scan_error") else
                             "The patch introduces new findings." if result.get("new_findings_introduced") else
                             "The patch applies cleanly" + (", the named tests pass" if tests else " (no tests were named)")
                             + " and it introduces no new findings.")
        result["next"] = ("To apply it yourself: save the diff and run  git apply <file>  in the project folder, then "
                          "run the full tests. HAYAT does not apply patches." if result["validated"] else
                          "Name the relevant tests and validate the patch again before applying it." if not tests else
                          "Revise the patch and test it again.")
        result["limits"] = ("Tested on a copy of the last commit; uncommitted changes in your working folder are not in it. "
                            "The worktree isolates file changes, not the test process or its network access.")
        with self._lock, closing(self._db()) as db, db:
            cursor = db.execute("INSERT INTO patches(at,project,diff,tests,applies,tests_passed,result_json) VALUES(?,?,?,?,?,?,?)",
                                (datetime.now(UTC).isoformat(timespec="seconds"), str(root), diff[:200_000],
                                 json.dumps(tests), int(result["applies"]),
                                 None if result["tests_passed"] is None else int(result["tests_passed"]),
                                 json.dumps(result)))
            result["patch_id"] = int(cursor.lastrowid or 0)
        self._publish({"kind": "patch", "applies": result["applies"], "tests_passed": result["tests_passed"]})
        return result

    # ---- reading

    def history(self, limit: int = 30) -> dict[str, Any]:
        """Bounded saved scan/patch evidence, without rescanning or applying anything."""
        limit = max(1, min(100, int(limit)))
        if not self.path.is_file():
            return {"scans": [], "patches": []}
        with self._lock, closing(self._db()) as db:
            scans = db.execute("SELECT id,at,project,kind,seconds,summary_json FROM scans ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            patches = db.execute("SELECT id,at,project,applies,tests_passed,result_json FROM patches ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {"scans": [{"id": row["id"], "at": row["at"], "project": row["project"], "kind": row["kind"],
                           "seconds": row["seconds"], "summary": json.loads(row["summary_json"])} for row in scans],
                "patches": [{"id": row["id"], "at": row["at"], "project": row["project"],
                             "applies": bool(row["applies"]), "tests_passed": None if row["tests_passed"] is None else bool(row["tests_passed"]),
                             "verdict": json.loads(row["result_json"]).get("verdict")} for row in patches]}

    def patch_detail(self, patch_id: int) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT at,result_json FROM patches WHERE id=?", (int(patch_id),)).fetchone()
        return {**json.loads(row["result_json"]), "patch_id": int(patch_id), "at": row["at"]} if row else None

    def _publish(self, payload: dict[str, Any]) -> None:
        if self.events is None:
            return
        try:
            self.events.publish("LAB_RUN_COMPLETED", self.SOURCE, payload)
        except Exception:
            log_swallowed(_log, "publishing a code defence run")

    def last_scan_age_hours(self) -> float | None:
        if not self.path.is_file():
            return None
        with self._lock, closing(self._db()) as db:
            row = db.execute("SELECT at FROM scans WHERE kind='source' ORDER BY id DESC LIMIT 1").fetchone()
        return (datetime.now(UTC) - datetime.fromisoformat(row[0])).total_seconds() / 3600 if row else None

    def status(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"scanned": "never"}
        with self._lock, closing(self._db()) as db:
            counts = {row[0]: int(row[1]) for row in db.execute(
                "SELECT severity, COUNT(*) FROM findings WHERE status='open' AND priority>0 GROUP BY severity")}
            states = {row[0]: int(row[1]) for row in db.execute("SELECT status, COUNT(*) FROM findings GROUP BY status")}
            last = db.execute("SELECT at, kind FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            patches = int(db.execute("SELECT COUNT(*) FROM patches").fetchone()[0])
        return {"last_scan": f"{last['at']} ({last['kind']})" if last else "never",
                "open_high": counts.get("HIGH", 0), "open_medium": counts.get("MEDIUM", 0), "open_low": counts.get("LOW", 0),
                "accepted": states.get("accepted", 0), "fixed": states.get("fixed", 0), "patches_tested": patches}


def register_code_defense_tools(registry: Any, defense: CodeDefense) -> None:
    def run(action: str = "findings", folder: str = "", finding: str = "", note: str = "", diff: str = "",
            tests: list[str] | None = None, record_id: int = 0) -> dict[str, Any]:
        if action == "scan":
            return defense.scan(folder)
        if action == "dependencies":
            return defense.dependencies()
        if action == "accept":
            return defense.set_status(finding, "accepted", note)
        if action == "reopen":
            return defense.set_status(finding, "open", note)
        if action == "validate_patch":
            return defense.validate_patch(diff, tests, folder)
        if action == "suggest_patch":
            return defense.suggest_patch(finding)
        if action == "history":
            return defense.history()
        if action == "patch_detail":
            return {"patch": defense.patch_detail(record_id)}
        return {"findings": defense.findings(30), "status": defense.status()}

    registry.register(
        "code_defense",
        "Security review of HAYAT's own code or a registered project: scan the source (Bandit), check installed "
        "packages for known vulnerabilities (pip-audit), list confirmed findings by priority, mark deliberate ones "
        "accepted, and test a patch (a unified diff) on an isolated copy with named tests and a re-scan. Never applies "
        "a patch: the owner does.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["findings", "scan", "dependencies", "accept", "reopen", "validate_patch",
                                                  "suggest_patch", "history", "patch_detail"]},
            "record_id": {"type": "integer", "minimum": 1},
            "folder": {"type": "string"}, "finding": {"type": "string"}, "note": {"type": "string"},
            "diff": {"type": "string"}, "tests": {"type": "array", "items": {"type": "string"}}},
         "additionalProperties": False},
        "medium", "python_project", run)
