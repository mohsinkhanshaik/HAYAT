from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class StorageManager:
    """Quota-aware project storage accounting and conservative cleanup."""

    def __init__(self, settings: dict[str, Any] | None = None, project_root: Path = PROJECT_ROOT) -> None:
        self.settings = settings or {}
        allowed_root = project_root.resolve()
        configured = Path(str(self.settings.get("root", allowed_root)))
        # A relative root ("." in settings.yaml) is the project folder wherever it now lives, so the folder can be
        # copied to another drive or machine and still start; an absolute one must still name this very folder.
        self.root = (configured if configured.is_absolute() else allowed_root / configured).resolve()
        if self.root != allowed_root:
            raise ValueError("Storage root must remain the current AI project root.")
        self.maximum = int(self.settings.get("maximum_total_bytes", 3_000_000_000_000))
        self.reserve = int(self.settings.get("reserve_free_space_bytes", 300_000_000_000))
        self.category_quotas = {
            str(name): max(0, int(value))
            for name, value in (self.settings.get("category_quotas", {}) or {}).items()
        }
        self.warn_percent = float(self.settings.get("warn_at_percent", 80))
        self.critical_percent = float(self.settings.get("critical_at_percent", 95))
        self._cache: dict[str, dict[str, Any]] | None = None
        self._cache_monotonic: float | None = None
        self._cache_at: str | None = None
        self._scan_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._model_metadata_cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}

    def status(self, max_age_seconds: float = 60.0, force_refresh: bool = False) -> dict[str, Any]:
        """Return category storage usage, backed by a short-lived cache.

        A full-project scan (`.venv` alone is 10k+ files) is too expensive to
        redo on every call: it previously ran on every world-state/observability
        snapshot, which is slow enough on a real filesystem to look like a
        hang. Callers that need a guaranteed-fresh number should pass
        `force_refresh=True`; everyone else gets a value that is at most
        `max_age_seconds` old, with `last_scanned_at`/`cache_age_seconds`
        always reported so staleness is visible rather than silently assumed
        current.
        """
        snapshot = self._cache_snapshot()
        if force_refresh or self._stale(snapshot, max_age_seconds):
            # A background status refresh and a file save can arrive together.
            # Only one walks the project; waiters recheck its completed result.
            with self._scan_lock:
                snapshot = self._cache_snapshot()
                if force_refresh or self._stale(snapshot, max_age_seconds):
                    categories = self._scan()
                    with self._cache_lock:
                        self._cache = categories
                        self._cache_monotonic = time.monotonic()
                        self._cache_at = datetime.now(UTC).isoformat()
                    snapshot = self._cache_snapshot()
        return self._status_from_snapshot(snapshot)

    def _cache_snapshot(self):
        with self._cache_lock:
            if self._cache is None or self._cache_monotonic is None:
                return None
            return ({name: dict(value) for name, value in self._cache.items()},
                    self._cache_at, self._cache_monotonic)

    @staticmethod
    def _stale(snapshot, max_age_seconds):
        return snapshot is None or max_age_seconds <= 0 or time.monotonic() - snapshot[2] > max_age_seconds

    def _disk_free(self) -> int:
        disk = os.statvfs(self.root) if hasattr(os, "statvfs") else None
        if disk:
            return int(disk.f_bavail * disk.f_frsize)
        return shutil.disk_usage(self.root).free

    def _refresh_in_background(self) -> threading.Thread | None:
        """Start one background rescan unless one is already running (then None)."""
        if self._scan_lock.locked():
            return None

        def refresh() -> None:
            try:
                self.status()
            except Exception:
                log_swallowed(_log, "refreshing storage usage in the background")

        thread = threading.Thread(target=refresh, name="hayat-storage-scan", daemon=True)
        thread.start()
        return thread

    def _status_from_snapshot(self, snapshot) -> dict[str, Any]:
        categories, scanned_at, scanned_monotonic = snapshot
        used = sum(item["used_bytes"] for item in categories.values())
        disk_free = self._disk_free()
        usable_free = max(0, disk_free - self.reserve)
        remaining = max(0, min(self.maximum - used, usable_free))
        percent = used / max(1, self.maximum) * 100
        return {
            "root": str(self.root), "used_bytes": used, "maximum_total_bytes": self.maximum,
            "remaining_budget_bytes": remaining, "disk_free_bytes": disk_free,
            "reserve_free_space_bytes": self.reserve, "percent_used": round(percent, 4),
            "state": "critical" if percent >= self.critical_percent else "warning" if percent >= self.warn_percent else "healthy",
            "categories": categories, "last_scanned_at": scanned_at,
            "cache_age_seconds": round(time.monotonic() - scanned_monotonic, 3),
            "learning_allocation_bytes": self.category_quotas.get("learning"),
            "learning_used_bytes": categories.get("learning", {}).get("used_bytes", 0),
        }

    def quick_status(self) -> dict[str, Any]:
        """Use the last measured scan without forcing a recursive project walk."""
        snapshot = self._cache_snapshot()
        if snapshot is not None:
            return self._status_from_snapshot(snapshot)
        disk = shutil.disk_usage(self.root)
        return {
            "root": str(self.root), "used_bytes": None,
            "maximum_total_bytes": self.maximum, "remaining_budget_bytes": None,
            "disk_free_bytes": disk.free, "reserve_free_space_bytes": self.reserve,
            "percent_used": None, "state": "not_scanned_in_this_runtime",
            "categories": {}, "last_scanned_at": None, "cache_age_seconds": None,
            "learning_allocation_bytes": self.category_quotas.get("learning"),
            "learning_used_bytes": None,
        }

    def largest(self, limit: int = 20) -> dict[str, Any]:
        files = []
        for path in self._files():
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append({"path": str(path), "bytes": stat.st_size, "last_accessed": datetime.fromtimestamp(stat.st_atime, UTC).isoformat(), "category": self._category(path)})
        files.sort(key=lambda item: item["bytes"], reverse=True)
        return {"root": str(self.root), "largest": files[: max(1, min(limit, 100))]}

    def clean(self) -> dict[str, Any]:
        if not bool(self.settings.get("automatic_cleanup", True)):
            return {"cleaned": False, "reason": "Automatic cleanup is disabled."}
        removed: list[dict[str, Any]] = []
        cutoff = datetime.now(UTC) - timedelta(days=int(self.settings.get("disposable_screenshot_days", 30)))
        work_root = (self.root / "data/work").resolve()
        safe_roots = [
            (work_root / name).resolve() for name in ("tmp", "temp", "scratch")
        ] + [(self.root / "data/artifacts/downloads/quarantine").resolve()]
        candidates: list[Path] = []
        for safe_root in safe_roots:
            if safe_root.exists():
                candidates.extend(path for path in safe_root.rglob("*") if path.is_file())
        # Only well-known disposable top-level work files are eligible. Durable
        # lab databases and generated work products under data/work must not be
        # mistaken for a cache merely because of their parent directory.
        if work_root.exists():
            candidates.extend(
                path for path in work_root.iterdir()
                if path.is_file() and path.suffix.lower() in {".tmp", ".temp", ".cache"}
            )
        artifact_root = (self.root / "data/artifacts").resolve()
        if artifact_root.exists():
            for path in artifact_root.glob("*.png"):
                try:
                    if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                        candidates.append(path)
                except OSError:
                    continue
        seen_hashes: set[str] = set()
        for path in sorted(set(candidates), key=lambda item: str(item)):
            resolved = path.resolve()
            if self.root not in resolved.parents or not resolved.is_file():
                continue
            try:
                size = resolved.stat().st_size
                if "downloads" in resolved.parts:
                    digest = self._hash(resolved)
                    if digest not in seen_hashes:
                        seen_hashes.add(digest)
                        continue
                resolved.unlink()
                removed.append({"path": str(resolved), "bytes": size})
            except OSError:
                continue
        expired = self._prune_expired_research_cache()
        return {"cleaned": True, "files_removed": len(removed), "bytes_reclaimed": sum(item["bytes"] for item in removed), "expired_cache_rows_removed": expired, "removed": removed[:100], "protected_categories": ["knowledge", "learning", "projects", "models", "datasets", "backups", "generated images", "user artifacts"]}

    def model_inventory(self) -> dict[str, Any]:
        models: list[dict[str, Any]] = []
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        local = urlsplit(base_url).hostname in {"localhost", "127.0.0.1", "::1"}
        def inspect(item):
            key = (base_url, str(item.get("name")), str(item.get("digest") or ""))
            with self._cache_lock:
                cached = self._model_metadata_cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            try:
                shown = httpx.post(base_url + "/api/show", json={"model": item.get("name")},
                                   timeout=3, trust_env=not local)
                shown.raise_for_status()
                show = shown.json()
                with self._cache_lock:
                    self._model_metadata_cache[key] = (time.monotonic(), show)
                    if len(self._model_metadata_cache) > 256:
                        oldest = min(self._model_metadata_cache, key=lambda k: self._model_metadata_cache[k][0])
                        del self._model_metadata_cache[oldest]
                return show
            except Exception:
                log_swallowed(_log, "reading Ollama model details")
                return {}
        try:
            try:
                response = httpx.get(base_url + "/api/tags", timeout=3, trust_env=not local)
            except httpx.ConnectError:
                # Nothing listening: the brain would look "not installed" and every reply would be "main model
                # unavailable" without a request ever reaching the recovery in the provider. Bring the service
                # back here, where the outage is first seen, and read the list once more (app/models/local.py).
                from app.models import local as local_models
                recover = local_models.SERVICE_RECOVERY
                if recover is None or not local or not recover()[0]:
                    raise
                response = httpx.get(base_url + "/api/tags", timeout=3, trust_env=not local)
            response.raise_for_status()
            items = response.json().get("models", [])
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="model-metadata") as pool:
                shows = list(pool.map(inspect, items))
            for item, show in zip(items, shows, strict=True):
                details = item.get("details") or {}
                model_info = show.get("model_info") or {}
                context = next((value for key, value in model_info.items() if key.endswith(".context_length")), None)
                capabilities = list(show.get("capabilities") or [])
                models.append({
                    "model": item.get("name"), "provider": "ollama", "size_bytes": item.get("size"),
                    "capabilities": capabilities, "quantization": details.get("quantization_level"),
                    "context": context, "vision_support": "vision" in capabilities, "benchmark_score": self._model_score(str(item.get("name") or "")),
                    "last_used": item.get("modified_at"),
                })
        except Exception:
            log_swallowed(_log, "listing Ollama models")
        try:
            loaded_response = httpx.get(base_url + "/api/ps", timeout=3, trust_env=not local)
            loaded_response.raise_for_status()
            loaded_rows = {str(item.get("name")): item for item in loaded_response.json().get("models", [])}
            for item in models:
                loaded = loaded_rows.get(str(item["model"]))
                item["loaded"] = loaded is not None
                item["warmth"] = "warm" if loaded is not None else "cold"
                item["loaded_vram_bytes"] = int(loaded.get("size_vram") or 0) if loaded else 0
                item["loaded_memory_bytes"] = int(loaded.get("size") or 0) if loaded else 0
                item["ram_bytes_estimate"] = max(0, item["loaded_memory_bytes"] - item["loaded_vram_bytes"]) if loaded else None
                benchmark = item.get("benchmark_score") or {}
                item["measured_failure_rate"] = min(1.0, len(benchmark.get("errors", [])) / 8) if benchmark else None
        except Exception:
            for item in models:
                item["loaded"] = None
        return {"models": models, "downloads_automatic": False}

    #: Writes up to this size never wait for a full walk of the project (568,670 files took 11.7 s idle on
    #: 2026-11-09 and over 20 s under load, so saving one note looked hung). They use the last completed scan,
    #: refreshed in the background, while free disk space is always read live.
    SMALL_WRITE_BYTES = 64 * 1024 * 1024
    #: With no completed scan at all, a small write waits this long for one before relying on the disk reserve.
    COLD_SCAN_WAIT_SECONDS = 3.0

    def ensure_capacity(self, additional_bytes: int, category: str | None = None) -> None:
        additional = max(0, int(additional_bytes))
        if additional <= self.SMALL_WRITE_BYTES:
            snapshot = self._cache_snapshot()
            if snapshot is None or self._stale(snapshot, 60.0):
                scan = self._refresh_in_background()
                if snapshot is None:
                    if scan is not None:
                        scan.join(self.COLD_SCAN_WAIT_SECONDS)  # a small tree finishes well inside this
                    elif self._scan_lock.acquire(timeout=self.COLD_SCAN_WAIT_SECONDS):  # another scan is running
                        self._scan_lock.release()
                    snapshot = self._cache_snapshot()
            if snapshot is None:
                # No scan has finished yet: the physical-disk reserve is still enforced now; the project budget
                # applies from the first completed scan (at most one small write can precede it).
                if additional > max(0, self._disk_free() - self.reserve):
                    raise OSError(f"The write would violate the configured {self.human(self.reserve)} physical-disk reserve.")
                return
            status = self._status_from_snapshot(snapshot)
        else:
            status = self.status()
        quota_remaining = max(0, status["maximum_total_bytes"] - status["used_bytes"])
        disk_remaining = max(0, status["disk_free_bytes"] - status["reserve_free_space_bytes"])
        if additional > quota_remaining:
            raise OSError(f"The write would exceed the configured {self.human(self.maximum)} AI project budget.")
        if additional > disk_remaining:
            raise OSError(f"The write would violate the configured {self.human(self.reserve)} physical-disk reserve.")
        if category and category in self.category_quotas:
            used = int(status["categories"].get(category, {}).get("used_bytes", 0))
            limit = self.category_quotas[category]
            if used + additional > limit:
                raise OSError(
                    f"The write would exceed the configured {self.human(limit)} {category} allocation."
                )

    def category_capacity(self, category: str) -> dict[str, Any]:
        """Report a named allocation without pretending unallocated space is reserved."""
        status = self.status()
        used = int(status["categories"].get(category, {}).get("used_bytes", 0))
        limit = self.category_quotas.get(category)
        remaining = None if limit is None else max(0, limit - used)
        return {"category": category, "used_bytes": used, "quota_bytes": limit, "remaining_bytes": remaining}

    def _model_score(self, model: str) -> dict[str, Any] | None:
        path = self.root / "data/benchmarks/models/results.json"
        try:
            rows = __import__("json").loads(path.read_text(encoding="utf-8")).get("models", [])
            return next((row for row in rows if row.get("model") == model), None)
        except (OSError, ValueError):
            return None

    @staticmethod
    def format_result(result: dict[str, Any], mode: str = "NORMAL") -> str:
        if "used_bytes" in result:
            used = StorageManager.human(result["used_bytes"])
            maximum = StorageManager.human(result["maximum_total_bytes"])
            remaining = StorageManager.human(result["remaining_budget_bytes"])
            summary = f"Storage is {result['state']}: {used} used of the {maximum} project budget; {remaining} remains available after the disk reserve."
            if mode in {"DETAILED", "DEBUG"}:
                rows = sorted(result["categories"].items(), key=lambda item: item[1]["used_bytes"], reverse=True)
                summary += "\n" + "\n".join(f"- {name}: {StorageManager.human(data['used_bytes'])} in {data['files']} files" for name, data in rows)
            return summary
        if "largest" in result:
            return "Largest project files:\n" + "\n".join(f"- {StorageManager.human(item['bytes'])}: {item['path']}" for item in result["largest"][:10])
        if result.get("cleaned"):
            return f"Cleanup reclaimed {StorageManager.human(result['bytes_reclaimed'])} from {result['files_removed']} disposable files and removed {result['expired_cache_rows_removed']} expired cache records."
        return str(result.get("reason", "No storage action was taken."))

    @staticmethod
    def human(value: int) -> str:
        number = float(value)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if number < 1000 or unit == "TB":
                return f"{number:.2f} {unit}"
            number /= 1000
        return f"{number:.2f} TB"

    def _scan(self) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        last_access: dict[str, float] = {}
        prefix_length = len(str(self.root).rstrip("\\/")) + 1
        priorities = {"temporary": 1, "web_cache": 2, "screenshots": 3, "logs": 4, "research_sources": 5, "task_history": 6, "artifacts": 7, "knowledge": 8, "learning": 9, "models": 9, "datasets": 9, "backups": 10, "dependencies": 8, "projects": 10}
        for file_entry in self._file_entries():
            # Every entry comes directly from our traversal under the resolved
            # root. Avoid rebuilding/normalizing a WindowsPath per file.
            relative = file_entry.path[prefix_length:].replace("\\", "/").lower()
            category = self._category_relative(relative)
            try:
                # On Windows scandir already has size/time metadata from the
                # directory enumeration. Path.stat discards it and opens every
                # file again, making a cold quota check take tens of seconds.
                stat = file_entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entry = values.setdefault(category, {"used_bytes": 0, "files": 0, "last_accessed": None, "quota_bytes": self.category_quotas.get(category), "cleanup_priority": priorities.get(category, 8)})
            entry["used_bytes"] += stat.st_size
            entry["files"] += 1
            last_access[category] = max(last_access.get(category, stat.st_atime), stat.st_atime)
        for category, accessed in last_access.items():
            values[category]["last_accessed"] = datetime.fromtimestamp(accessed, UTC).isoformat()
        return values

    def _files(self):
        for entry in self._file_entries():
            yield Path(entry.path)

    def _file_entries(self):
        """Walk the tree with os.scandir rather than Path.rglob.

        rglob does one stat-equivalent call per path component per entry;
        scandir's DirEntry caches file-type from the directory read itself,
        so this is materially faster over tens of thousands of files (most
        of them under `.venv`). Symlinks are skipped without following them,
        matching the previous behavior of never counting or descending into
        a symlink.
        """
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                entries = os.scandir(current)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            yield entry
                    except OSError:
                        continue

    def _category(self, path: Path) -> str:
        # `path` is always built as a direct descendant of `self.root` by
        # `_files()`'s own traversal (never through a symlink), so it shares
        # `self.root`'s already-resolved prefix -- no per-file .resolve()
        # syscall needed here.
        relative = path.relative_to(self.root).as_posix().lower()
        return self._category_relative(relative)

    @staticmethod
    def _category_relative(relative: str) -> str:
        if relative.startswith(".venv/"):
            return "dependencies"
        if relative.startswith("data/models/"):
            return "models"
        if relative.startswith("data/datasets/"):
            return "datasets"
        if relative.startswith("data/backups/"):
            return "backups"
        if relative.startswith(("data/knowledge/", "data/learning/", "data/training/", "data/skills/")) or relative == "data/memory/learning.db":
            return "learning"
        if relative.startswith("data/logs/"):
            return "logs"
        if relative.startswith(("data/memory/", "data/tasks/")):
            return "task_history"
        if relative.startswith("data/knowledge/sources/"):
            return "learning"
        if relative.endswith((".png", ".jpg", ".jpeg")) and relative.startswith("data/artifacts/"):
            return "screenshots"
        if relative.startswith("data/artifacts/"):
            return "artifacts"
        if relative.startswith(("data/work/", "data/browser-profiles/")) or relative.endswith(".tmp"):
            return "temporary"
        return "projects"

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _prune_expired_research_cache(self) -> int:
        database = self.root / "data/knowledge/knowledge.db"
        if not database.is_file():
            return 0
        now = datetime.now(UTC).isoformat()
        try:
            with sqlite3.connect(database) as db:
                query = db.execute("DELETE FROM query_cache WHERE expires_at<=?", (now,)).rowcount
                sources = db.execute("DELETE FROM source_cache WHERE expires_at<=?", (now,)).rowcount
            return max(0, query) + max(0, sources)
        except (sqlite3.Error, OSError):
            return 0
