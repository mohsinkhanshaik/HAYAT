"""Bounded, read-only source monitoring for HAYAT's registered projects."""
from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import threading
import tokenize
import tomllib
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.config import PROJECT_ROOT
from app.safety.sensitive import redact


SOURCE_SUFFIXES = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".ps1", ".json", ".toml",
    ".yaml", ".yml", ".go", ".rs", ".java", ".cs", ".c", ".cpp",
    ".h", ".html", ".css", ".sh",
})
IGNORED_FOLDERS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".test-runs", ".tmp", ".next", "dist", "build", "target",
    "vendor", "models", "runtime", "data", "browser-profiles",
})
MAX_FILES = 20_000
MAX_PARSE_BYTES = 1_000_000
_PYTHON_FRAME = re.compile(r"File\s+[\"'](?P<path>.+?)[\"'],\s+line\s+(?P<line>\d+)")
_LOCATION = re.compile(r"(?P<path>(?:[A-Za-z]:[\\/])?[^\s():]+\.(?:py|js|jsx|ts|tsx|ps1|go|rs|java|cs|cpp))"
                       r"[:(](?P<line>\d+)(?:[:,](?P<column>\d+))?")


class CodeMonitor:
    """Track changes and syntax errors without executing project code."""

    def __init__(self, projects: Any, *, path: Path | None = None,
                 interval_seconds: float = 180, notify: Any = None) -> None:
        self.projects = projects
        self.path = path or PROJECT_ROOT / "data/memory/code-monitor.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.interval_seconds = max(10.0, float(interval_seconds))
        self.notify = notify
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS code_files(
                project TEXT NOT NULL, path TEXT NOT NULL, mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL, issue_json TEXT,
                PRIMARY KEY(project, path))""")
            db.execute("""CREATE TABLE IF NOT EXISTS code_scans(
                project TEXT PRIMARY KEY, scanned_at TEXT NOT NULL,
                files INTEGER NOT NULL, changed INTEGER NOT NULL,
                removed INTEGER NOT NULL, truncated INTEGER NOT NULL,
                error TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="hayat-code-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)
        return not self.running

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_all()
            except Exception as exc:
                if self.notify:
                    self.notify(f"Code monitor could not scan: {type(exc).__name__}: {exc}")
            self._stop.wait(self.interval_seconds)

    def scan_all(self) -> dict[str, Any]:
        with self._lock:
            return {name: self.scan_project(name) for name in sorted(self.projects.projects)}

    def scan_project(self, name: str) -> dict[str, Any]:
        with self._lock:
            project = self.projects.projects.get(name)
            if project is None:
                raise KeyError(f"Project {name!r} is not registered.")
            root = Path(project.root).expanduser().resolve()
            if not root.is_dir():
                report = {"project": name, "root": str(root), "files": 0,
                          "changed": 0, "removed": 0, "truncated": False,
                          "issues": [], "error": "Project folder is unavailable."}
                self._save_scan(report)
                return report
            with self._connect() as db:
                previous = {row["path"]: row for row in db.execute(
                    "SELECT path,mtime_ns,size,issue_json FROM code_files WHERE project=?", (name,))}
            seen: set[str] = set()
            changes: list[tuple[str, int, int, str | None]] = []
            issues: list[dict[str, Any]] = []
            new_issues: list[dict[str, Any]] = []
            changed = 0
            truncated = False
            for current, folders, files in os.walk(root, followlinks=False):
                folders[:] = sorted(folder for folder in folders
                                    if folder not in IGNORED_FOLDERS
                                    and not (Path(current) / folder).is_symlink())
                for filename in sorted(files):
                    path = Path(current) / filename
                    if path.suffix.lower() not in SOURCE_SUFFIXES or path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    relative = path.relative_to(root).as_posix()
                    seen.add(relative)
                    if len(seen) > MAX_FILES:
                        seen.remove(relative)
                        truncated = True
                        break
                    old = previous.get(relative)
                    if old and old["mtime_ns"] == stat.st_mtime_ns and old["size"] == stat.st_size:
                        issue = json.loads(old["issue_json"]) if old["issue_json"] else None
                    else:
                        changed += 1
                        issue = self._check_file(path, relative, stat.st_size)
                        encoded = json.dumps(issue) if issue else None
                        changes.append((relative, stat.st_mtime_ns, stat.st_size, encoded))
                        if issue and (not old or old["issue_json"] != encoded):
                            new_issues.append(issue)
                    if issue:
                        issues.append(issue)
                if truncated:
                    break
            removed = sorted(set(previous) - seen) if not truncated else []
            with self._connect() as db:
                db.executemany(
                    "INSERT INTO code_files(project,path,mtime_ns,size,issue_json) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(project,path) DO UPDATE SET mtime_ns=excluded.mtime_ns,"
                    "size=excluded.size,issue_json=excluded.issue_json",
                    [(name, *row) for row in changes],
                )
                db.executemany("DELETE FROM code_files WHERE project=? AND path=?",
                               [(name, path) for path in removed])
            report = {"project": name, "root": str(root), "files": len(seen),
                      "changed": changed, "removed": len(removed), "truncated": truncated,
                      "issues": issues[:20], "issue_count": len(issues),
                      "new_issues": new_issues[:10], "error": ""}
            self._save_scan(report)
            if new_issues and self.notify:
                summary = ", ".join(f"{item['path']}:{item['line']} {item['message']}"
                                    for item in new_issues[:3])
                self.notify(f"Code monitor found {len(new_issues)} new issue(s) in {name}: {summary}")
            return report

    def _save_scan(self, report: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO code_scans(project,scanned_at,files,changed,removed,truncated,error)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(project) DO UPDATE SET
                scanned_at=excluded.scanned_at,files=excluded.files,changed=excluded.changed,
                removed=excluded.removed,truncated=excluded.truncated,error=excluded.error""",
                (report["project"], datetime.now(UTC).isoformat(), report["files"],
                 report["changed"], report["removed"], int(report["truncated"]), report["error"]),
            )

    @staticmethod
    def _check_file(path: Path, relative: str, size: int) -> dict[str, Any] | None:
        if size > MAX_PARSE_BYTES or path.suffix.lower() not in {".py", ".json", ".toml"}:
            return None
        try:
            if path.suffix.lower() == ".py":
                with tokenize.open(path) as handle:
                    content = handle.read()
                ast.parse(content, filename=relative)
            elif path.suffix.lower() == ".json":
                json.loads(path.read_text(encoding="utf-8"))
            else:
                tomllib.loads(path.read_text(encoding="utf-8"))
        except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError,
                UnicodeError, OSError) as exc:
            line = int(getattr(exc, "lineno", 0) or 0)
            return {"path": relative, "line": line,
                    "column": int(getattr(exc, "offset", 0) or getattr(exc, "colno", 0) or 0),
                    "kind": type(exc).__name__, "message": str(redact(str(exc)))[:240]}
        return None

    def status(self) -> dict[str, Any]:
        with self._lock, self._connect() as db:
            scans = {row["project"]: dict(row) for row in db.execute("SELECT * FROM code_scans")}
            result = []
            for name, project in sorted(self.projects.projects.items()):
                row = scans.get(name)
                issues = [json.loads(item[0]) for item in db.execute(
                    "SELECT issue_json FROM code_files WHERE project=? AND issue_json IS NOT NULL "
                    "ORDER BY path LIMIT 20", (name,))]
                count = db.execute("SELECT COUNT(*) FROM code_files WHERE project=? AND issue_json IS NOT NULL",
                                   (name,)).fetchone()[0]
                result.append({"project": name, "root": project.root,
                               "scanned_at": row["scanned_at"] if row else None,
                               "files": row["files"] if row else 0,
                               "changed": row["changed"] if row else 0,
                               "issues": issues, "issue_count": count,
                               "error": row["error"] if row else ""})
            return {"running": self.running, "interval_seconds": self.interval_seconds,
                    "projects": result}

    def explain_error(self, error_text: str) -> dict[str, Any]:
        text = str(error_text).strip()[:8000]
        if not text:
            return {"summary": "Paste the code error or traceback to inspect it.", "context": []}
        frames = list(_PYTHON_FRAME.finditer(text)) or list(_LOCATION.finditer(text))
        source = None
        for frame in reversed(frames):
            candidate = frame.group("path")
            for project in sorted(self.projects.projects.values(),
                                  key=lambda item: len(Path(item.root).parts), reverse=True):
                root = Path(project.root).expanduser().resolve()
                path = Path(candidate)
                resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
                if resolved.is_file() and (resolved == root or root in resolved.parents):
                    source = (project.name, root, resolved, int(frame.group("line")))
                    break
            if source:
                break
        last = next((line.strip() for line in reversed(text.splitlines()) if line.strip() and line.strip() != "^"), "")
        kind = next((word for word in ("SyntaxError", "ModuleNotFoundError", "ImportError",
                                       "NameError", "TypeError", "AttributeError", "FileNotFoundError",
                                       "JSONDecodeError") if word in text), "Code error")
        guidance = {
            "SyntaxError": "The parser could not read the code at the reported line. Check that line and the one before it.",
            "ModuleNotFoundError": "The import is unavailable in the current environment. Check the package name and interpreter.",
            "ImportError": "A module loaded but the requested symbol was unavailable. Check its exports and version.",
            "NameError": "A name was used before it was defined in this scope. Check spelling and control flow.",
            "TypeError": "An operation received an incompatible value or call signature. Inspect the arguments at the failing call.",
            "AttributeError": "An object does not have the requested attribute. Check its actual type at that line.",
            "FileNotFoundError": "The requested file was not found. Check the resolved path and working directory.",
            "JSONDecodeError": "The JSON parser found invalid syntax near the reported location.",
        }.get(kind, "Inspect the failing frame and the preceding operation before changing code.")
        result: dict[str, Any] = {"kind": kind, "message": str(redact(last))[:300],
                                  "summary": guidance, "context": []}
        if source:
            name, root, path, line = source
            result.update(project=name, path=path.relative_to(root).as_posix(), line=line)
            try:
                if path.stat().st_size <= MAX_PARSE_BYTES:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                    result["context"] = [f"{index}: {str(redact(lines[index - 1]))[:300]}"
                                         for index in range(max(1, line - 2), min(len(lines), line + 2) + 1)]
            except OSError:
                pass
        return result

    @staticmethod
    def format_status(data: dict[str, Any]) -> str:
        projects = data.get("projects", [])
        if not projects:
            return "No projects are registered for code monitoring."
        lines = ["Code monitor: " + ("running" if data.get("running") else "available on demand")]
        for project in projects:
            lines.append(f"{project['project']} - {project['root']}: {project['files']} source files, "
                         f"{project['issue_count']} syntax issue(s), last scan "
                         f"{project['scanned_at'] or 'pending'}")
            if project.get("error"):
                lines.append(f"  {project['error']}")
            for issue in project.get("issues", [])[:5]:
                lines.append(f"  {issue['path']}:{issue['line']} {issue['kind']}: {issue['message']}")
        return "\n".join(lines)


def register_code_monitor_tools(registry: Any, monitor: CodeMonitor) -> None:
    registry.register("code_monitor_status", "Show the monitored registered projects and their source issues.",
                      {"type": "object", "properties": {}, "additionalProperties": False},
                      "low", "system_read", monitor.status)
    registry.register("code_scan", "Scan every registered project for changed source and syntax errors without running it.",
                      {"type": "object", "properties": {}, "additionalProperties": False},
                      "low", "filesystem_read", monitor.scan_all)
    registry.register("code_explain_error", "Explain a pasted code error using source context from registered projects.",
                      {"type": "object", "properties": {"error_text": {"type": "string"}},
                       "required": ["error_text"], "additionalProperties": False},
                      "low", "filesystem_read", monitor.explain_error)
