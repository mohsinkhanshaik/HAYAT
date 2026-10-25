"""Every download HAYAT makes, in one place: what, from where, saved where, how big, how fast, and how it ended.

Four kinds:
* **file:** the governed downloads (DownloadManager), saved under data/downloads;
* **model:** AI models pulled into Ollama;
* **video:** videos and clips downloaded for the owner;
* **web:** every request the public fetcher makes (feeds, prices, maps, news, research pages).

Files, models and videos are kept as history across restarts (data/logs/transfers.jsonl, newest
last). Web requests are many and small, so they are kept for this session only (the last 500),
with a running total per site. Nothing in here is ever sent anywhere; addresses keep their host and
path but lose their query, so a key in a query string never reaches the log.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

HISTORY_KINDS = ("file", "model", "video")
WEB_LIMIT = 500
HISTORY_LIMIT = 2000


def where(url: str) -> tuple[str, str]:
    """(host, path) of an address, without its query: keys and tokens in a query string are never kept."""
    parts = urlsplit(str(url or ""))
    return parts.hostname or "", parts.path or "/"


class TransferLog:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._active: dict[int, dict[str, Any]] = {}
        self._web: deque[dict[str, Any]] = deque(maxlen=WEB_LIMIT)
        self._sites: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._next = 1
        self._loaded = False
        self.listeners: list[Callable[[], Any]] = []

    # ------------------------------------------------------------------ files, models, videos
    def start(self, kind: str, what: str, source: str = "", destination: str = "", total: int | None = None) -> int:
        host, path = where(source) if source.startswith(("http://", "https://")) else (source, "")
        with self._lock:
            identifier = self._next
            self._next += 1
            self._active[identifier] = {"id": identifier, "kind": kind, "what": str(what)[:200], "host": host, "path": path[:300],
                                        "destination": str(destination or ""), "total": total, "done": 0, "status": "active",
                                        "started": datetime.now(UTC).isoformat(timespec="seconds"), "_t0": time.monotonic()}
        self._changed()
        return identifier

    def progress(self, identifier: int, done: int, total: int | None = None) -> None:
        with self._lock:
            item = self._active.get(identifier)
            if item is None:
                return
            item["done"] = int(done)
            if total:
                item["total"] = int(total)
        # Progress is frequent: listeners poll instead of being told each chunk.

    def finish(self, identifier: int, status: str = "done", *, done: int | None = None, destination: str = "", error: str = "") -> dict[str, Any] | None:
        with self._lock:
            item = self._active.pop(identifier, None)
            if item is None:
                return None
            seconds = max(.001, time.monotonic() - item.pop("_t0"))
            if done is not None:
                item["done"] = int(done)
            item.update(status=status, finished=datetime.now(UTC).isoformat(timespec="seconds"), seconds=round(seconds, 2),
                        speed=round(item["done"] / seconds) if item["done"] else 0, error=str(error)[:300])
            if destination:
                item["destination"] = str(destination)
            self._load()
            self._history.append(item)
            del self._history[:-HISTORY_LIMIT]
            self._append(item)
        self._changed()
        return item

    def active(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            rows = []
            for item in self._active.values():
                seconds = max(.001, now - item["_t0"])
                rows.append({**{key: value for key, value in item.items() if key != "_t0"}, "seconds": round(seconds, 1),
                             "speed": round(item["done"] / seconds) if item["done"] else 0})
            return rows

    def history(self, limit: int = 200, kind: str = "") -> list[dict[str, Any]]:
        with self._lock:
            self._load()
            rows = [row for row in reversed(self._history) if not kind or row["kind"] == kind]
            return rows[:limit]

    # ------------------------------------------------------------------ web requests
    def web(self, url: str, *, purpose: str, size: int, ms: int, status: str = "done", error: str = "") -> None:
        host, path = where(url)
        row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "host": host, "path": path[:200], "purpose": purpose,
               "bytes": int(size), "ms": int(ms), "status": status, "error": str(error)[:160]}
        with self._lock:
            self._web.append(row)
            site = self._sites.setdefault(host, {"host": host, "requests": 0, "bytes": 0, "failed": 0, "ms": 0, "last": "",
                                                 "purposes": set()})
            site["requests"] += 1
            site["bytes"] += int(size)
            site["ms"] += int(ms)
            site["failed"] += status != "done"
            site["last"] = row["at"]
            site["purposes"].add(purpose)

    def web_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._web))[:limit]

    def sites(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = [{**site, "purposes": sorted(site["purposes"]), "average_ms": round(site["ms"] / site["requests"])}
                    for site in self._sites.values()]
        return sorted(rows, key=lambda row: -row["bytes"])

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {"active": len(self._active), "web_requests": sum(site["requests"] for site in self._sites.values()),
                    "web_bytes": sum(site["bytes"] for site in self._sites.values()), "sites": len(self._sites)}

    # ------------------------------------------------------------------ storage
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path or not self.path.exists():
            return
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()[-HISTORY_LIMIT:]
            self._history = [json.loads(line) for line in lines if line.strip()]
        except (OSError, ValueError):
            self._history = []

    def _append(self, item: dict[str, Any]) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:  # the in-memory history still shows it
            log_swallowed(_log, "writing the transfer history")

    def _changed(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:  # a closed window must not break a download
                log_swallowed(_log, "telling a window about a transfer")


_LOG = TransferLog()


def transfers() -> TransferLog:
    """The process-wide transfer log (the runtime gives it a file for history)."""
    return _LOG


def use_file(path: Path | str) -> None:
    with _LOG._lock:
        _LOG.path = Path(path)
        _LOG._loaded = False
        _LOG._history = []
