"""Finding employers' own career boards for the owner's search terms, and remembering them (2026-11-08).

HAYAT's search engine (the same open-source search chain research uses) is asked for postings on the
public board hosts of Greenhouse, Lever, Ashby and Workable that mention a term. Each result address
names a company's board; HAYAT then reads that board through its public, keyless JSON API, so every job
shown comes from the employer's own current listing, not from the search snippet.

Every board found is written to data/jobs/boards.json with the terms that found it and what reading it
gave, so the directory grows with each search: later searches for the same kind of role read the known
boards again (within the hourly limit) even when the search engine returns different results, and
boards that keep giving nothing relevant are tried less often.

Only public board hosts are searched and read; no sign-in, no challenge pages, nothing a robots rule
forbids (the fetcher checks robots.txt on every read).
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.jobs.sources import board_from_url
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: Board hosts searched for each term, one search per group.
HOSTS = (("greenhouse", ("job-boards.greenhouse.io", "boards.greenhouse.io")), ("lever", ("jobs.lever.co",)),
         ("ashby", ("jobs.ashbyhq.com",)), ("workable", ("apply.workable.com",)))
#: Boards read in one search round: newly found ones first, then known ones that matched these terms before.
BOARDS_PER_ROUND = 12
#: A board that gave nothing relevant this many times running is skipped for a week.
BARREN_LIMIT = 3


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9+#]+", str(text).casefold()) if len(word) > 1}


class BoardDirectory:
    """data/jobs/boards.json: every company board HAYAT has found, with what it was found for and what it gave."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.replace(self.path)

    def add(self, found: dict[str, dict[str, Any]]) -> list[str]:
        """Record boards found by a search; returns the names that were new."""
        data, now, new = self.load(), datetime.now(UTC).isoformat(timespec="seconds"), []
        for name, item in found.items():
            entry = data.get(name)
            if entry is None:
                entry = data[name] = {"first_seen": now, "terms": [], "example": item.get("example", ""),
                                      "reads": 0, "relevant": 0, "barren": 0}
                new.append(name)
            for term in item.get("terms", []):
                if term not in entry["terms"]:
                    entry["terms"] = (entry["terms"] + [term])[-20:]
            entry["last_found"] = now
        self._save(data)
        return new

    def record(self, name: str, read: int, kept: int, error: str = "") -> None:
        data = self.load()
        entry = data.get(name)
        if entry is None:
            return
        entry["last_read"] = datetime.now(UTC).isoformat(timespec="seconds")
        entry["reads"] = int(entry.get("reads", 0)) + 1
        entry["jobs"] = read
        entry["relevant"] = int(entry.get("relevant", 0)) + kept
        entry["barren"] = 0 if kept else int(entry.get("barren", 0)) + (0 if error else 1)
        entry["error"] = error[:200]
        self._save(data)

    def known_for(self, terms: list[str], now: datetime | None = None) -> list[str]:
        """Known boards whose finding terms share words with these terms, best first; resting boards left out."""
        now = now or datetime.now(UTC)
        wanted = set().union(*(_words(term) for term in terms)) if terms else set()
        ranked = []
        for name, entry in self.load().items():
            if int(entry.get("barren", 0)) >= BARREN_LIMIT:
                try:
                    if datetime.fromisoformat(entry.get("last_read") or "") > now - timedelta(days=7):
                        continue
                except ValueError:
                    pass
            overlap = len(wanted & set().union(*(_words(term) for term in entry.get("terms", [])))) if wanted else 1
            if overlap:
                ranked.append((overlap, int(entry.get("relevant", 0)), name))
        return [name for _overlap, _relevant, name in sorted(ranked, reverse=True)]

    def summary(self) -> dict[str, Any]:
        data = self.load()
        by_ats: dict[str, int] = {}
        for name in data:
            by_ats[name.split(":", 1)[0]] = by_ats.get(name.split(":", 1)[0], 0) + 1
        return {"boards": len(data), "by_ats": by_ats,
                "productive": sorted((name for name, entry in data.items() if entry.get("relevant")),
                                     key=lambda name: -int(data[name].get("relevant", 0)))[:20]}


def search_boards(search: Any, terms: list[str], *, per_host: int = 20) -> dict[str, dict[str, Any]]:
    """Company boards whose public postings a web search finds for each term ({"greenhouse:acme": {...}})."""
    found: dict[str, dict[str, Any]] = {}
    for term in [term for term in terms if term.strip()][:3]:
        for _ats, hosts in HOSTS:
            try:
                results = search.search(f'"{term}"', limit=per_host, domains=list(hosts))
            except Exception:  # one engine or host failing must not stop the others
                log_swallowed(_log, f"searching {hosts[0]} for {term!r}")
                continue
            for result in results:
                board = board_from_url(getattr(result, "url", "") or "")
                if not board:
                    continue
                name = f"{board[0]}:{board[1]}"
                entry = found.setdefault(name, {"terms": [], "example": getattr(result, "url", "")})
                if term not in entry["terms"]:
                    entry["terms"].append(term)
    return found


#: Companies checked for a public board after one round, and how long an answer is trusted.
PROBES_PER_ROUND = 6
PROBE_DAYS = 30


def probe_companies(fetcher: Any, directory: BoardDirectory, companies: list[str], terms: list[str],
                    limit: int = PROBES_PER_ROUND) -> list[str]:
    """Employers seen hiring for these terms on other feeds: do they have a public board? New board names.

    This needs no search engine: a company on Hacker News or Himalayas often lists more roles on its own
    Greenhouse, Lever, Ashby or Workable board. Each company is checked at most once a month (the answer,
    board or none, is kept in data/jobs/board-probes.json). A name match is a guess: the board's own
    company name is shown on every job it gives, so the owner can see who it really is.
    """
    from app.jobs.sources import discover

    probes_path = directory.path.with_name("board-probes.json")
    try:
        probes = json.loads(probes_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        probes = {}
    now = datetime.now(UTC)
    fresh = now - timedelta(days=PROBE_DAYS)
    due = []
    for company in companies:
        key = " ".join(str(company).casefold().split())
        if not key or key in due or len(key) > 80:
            continue
        try:
            if key in probes and datetime.fromisoformat(probes[key]["at"]) > fresh:
                continue
        except (KeyError, ValueError, TypeError):
            pass
        due.append(key)
    found: dict[str, dict[str, Any]] = {}
    for key in due[:limit]:
        try:
            boards = discover(fetcher, key, limit=8)
        except Exception:  # an unreachable host is "not known", tried again next month
            log_swallowed(_log, f"checking {key!r} for a public job board")
            boards = []
        probes[key] = {"at": now.isoformat(timespec="seconds"), "board": boards[0]["follow"] if boards else None}
        for board in boards:
            found[board["follow"]] = {"terms": list(terms), "example": f"company seen hiring for {terms[0] if terms else 'these terms'}"}
    probes_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = probes_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(probes, ensure_ascii=False, indent=1), encoding="utf-8")
    temporary.replace(probes_path)
    return directory.add(found) if found else []


def boards_for_round(directory: BoardDirectory, search: Any, terms: list[str],
                     limit: int = BOARDS_PER_ROUND) -> dict[str, Any]:
    """Search for boards, remember them, and choose which to read now: new ones first, then known ones."""
    found = search_boards(search, terms) if search is not None else {}
    new = directory.add(found) if found else []
    known = [name for name in directory.known_for(terms) if name not in new]
    chosen = list(dict.fromkeys(new + [name for name in found if name not in new] + known))[:limit]
    return {"boards": chosen, "found": sorted(found), "new": new, "directory": directory.summary()["boards"],
            "searched": search is not None}
