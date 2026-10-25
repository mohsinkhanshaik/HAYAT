from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.config import PROJECT_ROOT
from app.security.scanner import DefenderFileScanner


TRUSTED_HOST_SUFFIXES = (
    "pypi.org", "pythonhosted.org", "github.com", "githubusercontent.com",
    "ollama.com", "huggingface.co", "microsoft.com", "python.org",
    "docker.com", "kubernetes.io", "nvidia.com", "tanzil.net",
)


class DownloadManager:
    """HTTPS-only, quota-aware downloads with hashes and an audit registry."""

    def __init__(self, storage, root: Path | None = None, scanner: Any = None) -> None:
        self.storage = storage
        self.root = root or PROJECT_ROOT / "data/downloads"
        self.root.mkdir(parents=True, exist_ok=True)
        self.scanner = scanner or DefenderFileScanner()
        self.registry = self.root / "downloads.db"
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS downloads(
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, url TEXT NOT NULL,
                path TEXT NOT NULL, source_host TEXT NOT NULL, bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL, expected_sha256 TEXT, verified INTEGER NOT NULL,
                publisher_trusted INTEGER NOT NULL, status TEXT NOT NULL)"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(downloads)").fetchall()}
            for name in ("purpose", "version", "license", "used_by", "scan_status", "scan_engine"):
                if name not in columns:
                    db.execute(f"ALTER TABLE downloads ADD COLUMN {name} TEXT")

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            total, bytes_total, verified = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(bytes),0),COALESCE(SUM(verified),0) FROM downloads WHERE status='complete'"
            ).fetchone()
            blocked = db.execute("SELECT COUNT(*) FROM downloads WHERE status='blocked'").fetchone()[0]
        return {
            "directory": str(self.root), "records": int(total), "bytes": int(bytes_total),
            "verified_records": int(verified), "https_required": True,
            "trusted_sources": list(TRUSTED_HOST_SUFFIXES), "automatic_execution": False,
            "blocked_records": int(blocked), "antimalware": self.scanner.status(),
        }

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [dict(row) for row in rows]

    def models(self, limit: int = 50) -> list[dict[str, Any]]:
        return [row for row in self.list(200) if "model" in str(row.get("purpose") or "").lower() or str(row.get("path") or "").lower().endswith((".gguf", ".safetensors"))][:limit]

    def cleanup(self) -> dict[str, Any]:
        removed = []
        for path in self.root.glob("*.partial"):
            removed.append({"path": str(path), "bytes": path.stat().st_size})
            path.unlink(missing_ok=True)
        return {"removed": removed, "files": len(removed), "bytes": sum(item["bytes"] for item in removed), "scope": "incomplete partial downloads only"}

    @staticmethod
    def _trusted_url(url: str) -> bool:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        return parsed.scheme == "https" and any(
            host == suffix or host.endswith("." + suffix) for suffix in TRUSTED_HOST_SUFFIXES)

    @contextmanager
    def _stream_trusted(self, url: str):
        current = url
        for _ in range(6):
            if not self._trusted_url(current):
                raise PermissionError("The download redirected outside the authoritative HTTPS allowlist.")
            with httpx.stream("GET", current, timeout=60, follow_redirects=False,
                              headers={"User-Agent": "AI-Downloads/1.0"}) as response:
                if getattr(response, "status_code", 200) in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Download redirect did not provide a destination.")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                if not self._trusted_url(str(response.url)):
                    raise PermissionError("The download response came from an untrusted host.")
                yield response
                return
        raise ValueError("Download exceeded the redirect limit.")

    def download(self, url: str, filename: str = "", expected_sha256: str = "", max_bytes: int = 536_870_912, purpose: str = "", version: str = "", license: str = "", used_by: str = "") -> dict[str, Any]:
        """Download into data/downloads; the transfer log shows it live and keeps it in history."""
        from app.transfers import transfers
        log = transfers()
        ticket = log.start("file", purpose or filename or Path(unquote(urlsplit(url).path)).name or url, url, str(self.root))
        try:
            result = self._download(url, filename, expected_sha256, max_bytes, purpose, version, license, used_by,
                                    progress=lambda done, total: log.progress(ticket, done, total))
        except Exception as exc:
            log.finish(ticket, "blocked" if "blocked by the antimalware" in str(exc) else "failed", error=f"{type(exc).__name__}: {exc}")
            raise
        log.finish(ticket, "done", done=result["bytes"], destination=result["path"])
        return result

    def _download(self, url: str, filename: str, expected_sha256: str, max_bytes: int, purpose: str, version: str, license: str,
                  used_by: str, progress: Any = None) -> dict[str, Any]:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            raise ValueError("Public downloads must use an absolute HTTPS URL.")
        trusted = self._trusted_url(url)
        if not trusted:
            raise PermissionError("The download host is not in the configured authoritative-source allowlist.")
        name = self._safe_name(filename or Path(unquote(parsed.path)).name or "download.bin")
        destination = (self.root / name).resolve()
        destination.relative_to(self.root.resolve())
        if destination.exists():
            stem, suffix = destination.stem, destination.suffix
            for index in range(2, 10002):
                candidate = self.root / f"{stem}-{index}{suffix}"
                if not candidate.exists():
                    destination = candidate
                    break
            else:
                raise FileExistsError(f"No free download filename for {name}.")
        partial = destination.with_suffix(destination.suffix + ".partial")
        digest = hashlib.sha256(); written = 0
        try:
            with self._stream_trusted(url) as response:
                declared = int(response.headers.get("content-length") or 0)
                if declared:
                    self.storage.ensure_capacity(declared)
                if declared > max_bytes:
                    raise OSError(f"Download declares {declared} bytes, above the {max_bytes}-byte request limit.")
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise OSError(f"Download exceeded the {max_bytes}-byte request limit.")
                        handle.write(chunk); digest.update(chunk)
                        if progress is not None:
                            progress(written, declared or None)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        self.storage.ensure_capacity(written)
        actual = digest.hexdigest()
        verified = not expected_sha256 or actual.lower() == expected_sha256.lower()
        if not verified:
            partial.unlink(missing_ok=True)
            raise ValueError("Downloaded SHA-256 does not match the expected value.")
        try:
            scan = self.scanner.scan(partial)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        if not scan.get("clean"):
            blocked = destination.with_suffix(destination.suffix + ".blocked")
            partial.replace(blocked)
            with self._connect() as db:
                db.execute(
                    "INSERT INTO downloads(timestamp,url,path,source_host,bytes,sha256,expected_sha256,verified,publisher_trusted,status,purpose,version,license,used_by,scan_status,scan_engine) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (datetime.now(UTC).isoformat(), url, str(blocked), host, written, actual,
                     expected_sha256 or None, int(verified), int(trusted), "blocked",
                     purpose[:500] or None, version[:100] or None, license[:100] or None,
                     used_by[:200] or None, str(scan.get("status")), str(scan.get("provider"))),
                )
            raise PermissionError(
                f"Download retained as {blocked.name} but blocked by the antimalware gate: {scan.get('reason')}"
            )
        partial.replace(destination)
        with self._connect() as db:
            db.execute(
                "INSERT INTO downloads(timestamp,url,path,source_host,bytes,sha256,expected_sha256,verified,publisher_trusted,status,purpose,version,license,used_by,scan_status,scan_engine) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(UTC).isoformat(), url, str(destination), host, written, actual, expected_sha256 or None, int(verified), int(trusted), "complete", purpose[:500] or None, version[:100] or None, license[:100] or None, used_by[:200] or None, str(scan.get("status")), str(scan.get("provider"))),
            )
        return {"path": str(destination), "bytes": written, "bytes_downloaded": written,
                "sha256": actual, "verified": verified, "trusted_source": trusted,
                "executed": False, "antimalware_scan": scan, "complete": True}

    @staticmethod
    def _safe_name(value: str) -> str:
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(value).name).strip(".-")
        if not name:
            raise ValueError("A safe output filename could not be derived.")
        return name[:180]

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.registry); connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


class DependencyManager:
    """Read-only project environment inspection; installation remains a permissioned operation."""

    def __init__(self, requirements: Path | None = None) -> None:
        self.requirements = requirements or PROJECT_ROOT / "requirements.txt"

    def status(self) -> dict[str, Any]:
        rows = []
        for raw in self.requirements.read_text(encoding="utf-8").splitlines():
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            package = re.split(r"[<>=!~\[]", value, maxsplit=1)[0].strip()
            try:
                version = importlib.metadata.version(package)
                state = "installed"
            except importlib.metadata.PackageNotFoundError:
                version = None; state = "missing"
            rows.append({"requirement": value, "package": package, "installed_version": version, "status": state})
        return {
            "python_environment": str(Path(__import__("sys").executable)),
            "requirements": str(self.requirements), "packages": rows,
            "missing": [row["package"] for row in rows if row["status"] == "missing"],
            "automatic_system_install": False,
        }

    def check(self) -> dict[str, Any]:
        completed = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, timeout=60)
        return {"passed": completed.returncode == 0, "output": (completed.stdout or completed.stderr).strip(), "changed": False}

    def outdated(self) -> dict[str, Any]:
        completed = subprocess.run([sys.executable, "-m", "pip", "list", "--outdated", "--format=json"], capture_output=True, text=True, timeout=120)
        return {"passed": completed.returncode == 0, "packages": json.loads(completed.stdout or "[]") if completed.returncode == 0 else [], "error": completed.stderr.strip(), "upgraded": False}


def register_download_tools(registry, manager: DownloadManager) -> None:
    schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string"}, "filename": {"type": "string"},
            "expected_sha256": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 536870912},
            "purpose": {"type": "string", "maxLength": 500}, "version": {"type": "string", "maxLength": 100},
            "license": {"type": "string", "maxLength": 100}, "used_by": {"type": "string", "maxLength": 200},
        },
        "required": ["url"], "additionalProperties": False,
    }
    registry.register("download_public_file", "Download one public file from an authoritative HTTPS source, verify its hash, and never execute it.", schema, "medium", "public_download", manager.download)
