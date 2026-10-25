"""Detect a break-in attempt on the remote-access server, then lock it out.

This is defence, not counterattack. It never touches or damages whoever is
attacking -- doing that would be illegal, would hit the innocent third party
whose machine was hijacked to route the attack, and would give a real attacker
something to turn back on this machine. What it does instead:

* watches each source for the signs of an attack -- wrong passphrases, path
  traversal, scanner probes for files like ``/.env`` or ``/wp-login``,
  injection markers, oversized bodies, request floods;
* scores those offences and, past a threshold, bans the source for a time that
  doubles with each new ban and survives a restart;
* answers secret-file theft probes and banned reads with synthetic decoy data.
  No private files, credentials or runtime data are consulted. Other banned
  requests receive a tarpit. No code is sent or executed on another machine.

The owner's own network is never scored (see ``trust``), so this cannot lock
Mohsin out of his own assistant.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import re
import secrets
import threading
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote_plus

from app.logging import log_swallowed

_log = logging.getLogger(__name__)

#: An offence and what it counts for. A ban lands at BAN_THRESHOLD points.
OFFENCE_WEIGHTS = {
    "failed_login": 1,        # six wrong passphrases (the 5-try limiter already slows this) -> ban
    "unauthorized": 1,
    "path_traversal": 6,      # unambiguously malicious: one probe -> ban
    "scanner_probe": 3,       # two scanner probes -> ban
    "secret_probe": 6,        # an attempt to retrieve credentials -> immediate ban
    "injection_probe": 6,     # one injection marker -> ban
    "oversized": 2,
    "flood": 6,               # 40+ requests in 10s -> ban
    "bad_origin": 2,
}
BAN_THRESHOLD = 6
#: Ban length by how many times this source has been banned before: 15 min, 1 h, 6 h, 24 h, then 7 days.
BAN_LADDER = (900, 3600, 21600, 86400, 604800)
SCORE_WINDOW = 600.0        # offences older than this stop counting toward a ban
FLOOD_WINDOW = 10.0
FLOOD_LIMIT = 40            # requests per FLOOD_WINDOW from one source before it is a flood
TARPIT_SECONDS = 8.0
MAX_SOURCES = 4096
MAX_STORE_BYTES = 8_000_000

#: Paths a scanner asks for that a real client never would.
_SCANNER = re.compile(
    r"(?i)(?:^|/)(?:\.env|\.git|\.aws|\.ssh|wp-login|wp-admin|xmlrpc\.php|phpmyadmin|"
    r"administrator|admin\.php|config\.php|\.htaccess|id_rsa|boot\.ini|web\.config|"
    r"actuator|\.well-known/security|shell|cgi-bin|vendor/phpunit)")
_TRAVERSAL = re.compile(r"(?:\.\.[/\\]|%2e%2e|%252e|/etc/passwd|\\windows\\win|/proc/self)")
_SECRET_PATH = re.compile(r"(?i)(?:^|/)(?:\.env(?:[./]|$)|\.git(?:/|$)|\.aws(?:/|$)|\.ssh(?:/|$)|id_rsa(?:[./]|$))")
_INJECTION = re.compile(
    r"(?i)(?:union\s+select|<script|onerror\s*=|javascript:|\$\{jndi:|;\s*(?:cat|ls|whoami|curl|wget)\s|"
    r"\|\s*(?:cat|nc|bash)\b|\bexec\s*\(|/bin/sh|%00)")
#: The endpoints a legitimate client actually uses. Anything else is a probe.
_KNOWN_PATHS = frozenset({"/", "/health", "/api/login", "/api/logout", "/api/status", "/api/chat", "/ws"})


@dataclass(frozen=True)
class Decision:
    action: str            # "allow" | "block" | "tarpit"
    status: int = 200
    reason: str = ""
    retry_after: int = 0
    delay: float = 0.0

    @property
    def blocked(self) -> bool:
        return self.action != "allow"


@dataclass
class _Record:
    score: float = 0.0
    events: list = None            # (timestamp, kind) within SCORE_WINDOW
    hits: list = None              # request timestamps within FLOOD_WINDOW
    bans: int = 0
    banned_until: float = 0.0
    reasons: list = None
    decoy_sent: bool = False
    last_seen: float = 0.0

    def __post_init__(self) -> None:
        self.events = self.events or []
        self.hits = self.hits or []
        self.reasons = self.reasons or []


class IntrusionShield:
    def __init__(self, store_path: Path | None = None, *, events: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall_clock: Callable[[], float] = time.time,
                 trusted_networks: tuple[Any, ...] = (), max_sources: int = MAX_SOURCES) -> None:
        self.store_path = Path(store_path) if store_path else None
        self.events = events
        self.clock = clock
        self.wall_clock = wall_clock
        self.trusted = tuple(trusted_networks)
        self._records: dict[str, _Record] = {}
        self.max_sources = max(1, min(MAX_SOURCES, int(max_sources)))
        self._decoys_served = 0
        self.store_error = ""
        # Created locally; unrelated to every real credential in the application.
        self._decoy_key = secrets.token_hex(24)
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------ trust
    def trust(self, host: str | None) -> bool:
        """The owner's own machine and LAN are never scored or banned."""
        if not host:
            return False
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        if address.is_loopback:
            return True
        return any(address in network for network in self.trusted)

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.store_path or not self.store_path.is_file():
            return
        try:
            with self.store_path.open("rb") as stream:
                raw = stream.read(MAX_STORE_BYTES + 1)
            if len(raw) > MAX_STORE_BYTES:
                raise ValueError("ban store exceeds size limit")
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get("bans"), dict):
                raise ValueError("invalid ban store")
        except (OSError, ValueError):
            self.store_error = "Saved bans could not be loaded; inspect the local ban store."
            return
        now = self.wall_clock()
        for host, entry in data.get("bans", {}).items():
            if len(self._records) >= self.max_sources:
                self.store_error = "Saved bans exceed the source limit; not all entries were loaded."
                break
            try:
                if not isinstance(entry, dict) or not isinstance(host, str) or len(host) > 255:
                    raise ValueError("invalid entry")
                until_wall = float(entry.get("until_wall", 0))
                bans = max(1, min(len(BAN_LADDER), int(entry.get("bans", 1))))
                reasons = entry.get("reasons", [])
                if not math.isfinite(until_wall) or not isinstance(reasons, list):
                    raise ValueError("invalid entry")
            except (ValueError, TypeError, OverflowError):
                self.store_error = "Some invalid saved bans were skipped."
                continue
            if until_wall <= now:
                continue                                  # expired while we were off
            record = _Record(bans=bans, reasons=[r[:240] for r in reasons[-6:] if isinstance(r, str)],
                             last_seen=self.clock())
            # Stored as wall-clock; translate the remaining time onto the monotonic clock.
            record.banned_until = self.clock() + min(until_wall - now, BAN_LADDER[-1])
            self._records[host] = record

    def _save(self) -> None:
        if not self.store_path:
            return
        now_mono, now_wall = self.clock(), self.wall_clock()
        bans = {host: {"until_wall": now_wall + (rec.banned_until - now_mono), "bans": rec.bans,
                       "reasons": rec.reasons[-6:]}
                for host, rec in self._records.items() if rec.banned_until > now_mono}
        temporary = None
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.store_path.parent,
                                             prefix=".bans-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                json.dump({"bans": bans}, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.store_path)
            self.store_error = ""
        except OSError:
            self.store_error = "Ban persistence failed; current bans are held in memory only."
            log_swallowed(_log, "saving the intrusion ban list", level=logging.WARNING)
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    log_swallowed(_log, "removing a temporary ban store")

    # ------------------------------------------------------------ scoring
    def _record_for(self, host: str) -> _Record | None:
        record = self._records.get(host)
        if record is None:
            if len(self._records) >= self.max_sources:
                now = self.clock()
                stale = [source for source, item in self._records.items()
                         if item.banned_until <= now and now - item.last_seen > SCORE_WINDOW]
                for source in stale:
                    del self._records[source]
            if len(self._records) >= self.max_sources:
                return None  # Fail closed for new sources; never evict an active ban.
            record = self._records[host] = _Record()
        record.last_seen = self.clock()
        return record

    def _decay(self, record: _Record) -> None:
        cutoff = self.clock() - SCORE_WINDOW
        record.events = [(ts, kind) for ts, kind in record.events if ts >= cutoff]
        record.score = sum(OFFENCE_WEIGHTS.get(kind, 1) for _ts, kind in record.events)

    def offence(self, host: str | None, kind: str, detail: str = "") -> Decision:
        """Record an offence by a source and ban it once it has done enough."""
        if not host or self.trust(host):
            return Decision("allow")
        weight = OFFENCE_WEIGHTS.get(kind, 1)
        with self._lock:
            record = self._record_for(host)
            if record is None:
                return Decision("block", status=503, reason="source capacity", retry_after=10)
            if record.banned_until > self.clock():
                return Decision("block", status=403, reason="banned")
            self._decay(record)
            record.events.append((self.clock(), kind))
            record.score += weight
            reason = f"{kind}{': ' + detail[:200] if detail else ''}"
            if record.score >= BAN_THRESHOLD and record.banned_until <= self.clock():
                return self._ban(host, record, reason)
            self._emit("INTRUSION_OFFENCE", host, kind=kind, detail=detail[:200], score=record.score)
        return Decision("allow")

    def _ban(self, host: str, record: _Record, reason: str) -> Decision:
        duration = BAN_LADDER[min(record.bans, len(BAN_LADDER) - 1)]
        record.bans += 1
        record.banned_until = self.clock() + duration
        record.reasons.append(reason)
        record.reasons = record.reasons[-6:]
        record.events.clear()
        record.score = 0.0
        record.decoy_sent = False
        self._save()
        self._emit("INTRUSION_BANNED", host, reason=reason, seconds=duration, bans=record.bans)
        _log.warning("Blocked %s for %ss after %s (ban #%s)", host, duration, reason, record.bans)
        return Decision("block", status=403, reason=reason, retry_after=int(duration))

    # ------------------------------------------------------------ per-request
    def banned_for(self, host: str) -> float:
        record = self._records.get(host)
        if record is None:
            return 0.0
        remaining = record.banned_until - self.clock()
        return remaining if remaining > 0 else 0.0

    def inspect(self, host: str | None, method: str, path: str, *, query: str = "") -> Decision:
        """The gate every request passes. Ban check, flood check, probe check."""
        if not host or self.trust(host):
            return Decision("allow")
        remaining = self.banned_for(host)
        if remaining > 0:
            # A banned source is not told it is banned: it gets the tarpit.
            return Decision("tarpit", status=503, reason="banned", retry_after=int(remaining), delay=TARPIT_SECONDS)
        # ASGI already decodes paths once, while queries retain percent escapes.
        # Normalize a bounded copy only for inspection, never for routing.
        if len(path) + len(query) > 16_384:
            return Decision("block", status=414, reason="request target too large")
        target = f"{path}?{query}" if query else path
        for _ in range(3):
            decoded = unquote_plus(target)
            if decoded == target:
                break
            target = decoded
        target = target.lower()
        if _TRAVERSAL.search(target):
            return self.offence(host, "path_traversal", path[:120]) or Decision("allow")
        if _INJECTION.search(target):
            return self.offence(host, "injection_probe", path[:120])
        if _SECRET_PATH.search(target.split("?", 1)[0]):
            return self.offence(host, "secret_probe", path[:120])
        inspected_path = target.split("?", 1)[0]
        if inspected_path not in _KNOWN_PATHS and (_SCANNER.search(inspected_path) or _looks_like_probe(inspected_path)):
            return self.offence(host, "scanner_probe", path[:120])
        with self._lock:
            record = self._record_for(host)
            if record is None:
                return Decision("block", status=503, reason="source capacity", retry_after=10)
            now = self.clock()
            if record.banned_until > now:
                return Decision("tarpit", status=503, reason="banned",
                                retry_after=int(record.banned_until - now), delay=TARPIT_SECONDS)
            record.hits = [ts for ts in record.hits if ts >= now - FLOOD_WINDOW]
            record.hits.append(now)
            if len(record.hits) > FLOOD_LIMIT:
                record.hits.clear()
                return self.offence(host, "flood", f"{FLOOD_LIMIT}+ requests in {int(FLOOD_WINDOW)}s")
        return Decision("allow")

    def decoy_response(self, host: str | None, method: str, path: str):
        """Return inert synthetic evidence only for a source already locked out.

        This method cannot reach files, models, tools, sessions or credentials.
        The fake key cannot authenticate anywhere. No callback or executable
        content is included, and POST/WS traffic never enters a fake session.
        """
        if not host or self.trust(host) or method not in {'GET', 'HEAD'}:
            return None
        with self._lock:
            record = self._records.get(host)
            if record is None or record.banned_until <= self.clock():
                return None
            self._decoys_served += 1
            if not record.decoy_sent:
                record.decoy_sent = True
                self._emit('INTRUSION_DECOY', host, response='synthetic_data', real_data_access=False)
        if _SECRET_PATH.search(path):
            return 'text/plain', ('APP_ENV=production\nAPI_KEY=' + self._decoy_key
                                  + '\nDATABASE_HOST=db.example.invalid\nDATABASE_USER=service\n')
        return 'application/json', json.dumps({'ok': True, 'records': [], 'session': self._decoy_key,
                                               'service': 'gateway'})

    def note_failed_login(self, host: str | None) -> Decision:
        return self.offence(host, "failed_login")

    def note_success(self, host: str | None) -> None:
        """A correct login clears a source's offence score (not an existing ban)."""
        if not host:
            return
        with self._lock:
            record = self._records.get(host)
            if record is not None and record.banned_until <= self.clock():
                record.events.clear()
                record.score = 0.0

    # ------------------------------------------------------------ owner controls
    def unban(self, host: str) -> bool:
        with self._lock:
            record = self._records.get(host)
            if record is None or record.banned_until <= self.clock():
                return False
            record.banned_until = 0.0
            self._save()
        return True

    def status(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            for rec in self._records.values():
                self._decay(rec)
            active = [{"source": host, "seconds_left": int(rec.banned_until - now), "bans": rec.bans,
                       "reasons": rec.reasons[-3:], "decoy_sent": rec.decoy_sent}
                      for host, rec in self._records.items() if rec.banned_until > now]
            watched = sum(1 for rec in self._records.values() if rec.score > 0 and rec.banned_until <= now)
        return {"banned": sorted(active, key=lambda item: -item["seconds_left"]),
                "banned_count": len(active), "watched_sources": watched,
                "tracked_sources": len(self._records), "source_limit": self.max_sources,
                "decoy_responses": self._decoys_served, "store_error": self.store_error}

    def _emit(self, event_type: str, host: str, **fields: Any) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(event_type, "security", {"source": host, **fields})
        except Exception:
            log_swallowed(_log, "publishing an intrusion event")


def _looks_like_probe(path: str) -> bool:
    """An unknown path ending in a code/config extension is almost always a probe."""
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return suffix in {"php", "asp", "aspx", "jsp", "cgi", "env", "sql", "bak", "old", "yml", "yaml", "ini"}
