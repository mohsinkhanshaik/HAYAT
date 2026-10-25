"""Lifecycle controller for Off, Same Wi-Fi, and mTLS Anywhere access.

This module deliberately does not modify Windows Firewall, UPnP, or the
router.  "Anywhere" means the listener is cryptographically ready to accept
internet-routed clients; actual routing remains an explicit owner/network
operation and is reported separately.
"""
from __future__ import annotations

import os
import ipaddress
import secrets
import socket
import threading
from enum import StrEnum
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, load_settings
from app.web.pki import LocalPKI
from app.web.server import WebServer, _resolve_bind_host


class AccessMode(StrEnum):
    OFF = "off"
    SAME_WIFI = "same_wifi"
    ANYWHERE = "anywhere"


class RemoteAccessManager:
    """Own the one mobile server and make every mode transition fail closed."""

    def __init__(self, runtime: Any, settings: dict[str, Any] | None = None,
                 *, pki_root: Path | None = None, pki: LocalPKI | None = None) -> None:
        self.runtime = runtime
        loaded = settings if settings is not None else load_settings()
        self.config = loaded.get("web", {}) if isinstance(loaded, dict) else {}
        self.host = str(self.config.get("host") or "lan")
        self.port = int(self.config.get("port") or 8770)
        self.public_endpoint = str(self.config.get("public_endpoint") or "").strip()
        self._pki = pki
        self._pki_root = pki_root or PROJECT_ROOT / "data/security/remote_access"
        self._server: WebServer | None = None
        self._mode = AccessMode.OFF
        self._lock = threading.RLock()
        self._last_error = ""
        self._session_token = ""

    @property
    def server(self) -> WebServer | None:
        return self._server

    @property
    def mode(self) -> AccessMode:
        return self._mode

    def start_configured(self) -> dict[str, Any]:
        if not self.config.get("enabled", False):
            return self.set_mode(AccessMode.OFF)
        requested = str(self.config.get("mode") or AccessMode.SAME_WIFI.value)
        return self.set_mode(requested)

    def set_mode(self, mode: AccessMode | str) -> dict[str, Any]:
        target = AccessMode(str(mode))
        with self._lock:
            self._stop_locked()
            if target is AccessMode.OFF:
                self._mode = target
                self._last_error = ""
                return self.status()
            token = self._token()
            if not token:
                self._last_error = "A protected local access passphrase could not be loaded or created."
                raise RuntimeError(self._last_error)
            try:
                if target is AccessMode.ANYWHERE:
                    pki = self._pki_manager()
                    if pki.status()["active_devices"] < 1:
                        raise RuntimeError("Pair at least one device before enabling Anywhere mode.")
                    hosts = [_resolve_bind_host(self.host)]
                    if self.public_endpoint:
                        hosts.append(self.public_endpoint.split(":", 1)[0])
                    tls = pki.ensure(hosts)
                    server = WebServer(
                        self.runtime, host=self.host, port=self.port, token=token,
                        tls=tls, allow_remote=True,
                    )
                else:
                    server = WebServer(self.runtime, host=self.host, port=self.port, token=token)
                server.start()
            except Exception as exc:
                self._mode = AccessMode.OFF
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise
            self._server = server
            self._mode = target
            self._last_error = ""
            return self.status()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._mode = AccessMode.OFF

    def pair_device(self, name: str) -> dict[str, Any]:
        result = self._pki_manager().pair_device(name)
        # The server certificate may have been generated while pairing. Its
        # key remains encrypted and its password never enters this result.
        return result

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            revoked = self._pki_manager().revoke(str(device_id).strip())
            if revoked and self._mode is AccessMode.ANYWHERE:
                # Rebuild the SSL context immediately so the CRL takes effect;
                # existing sessions are disconnected as part of the restart.
                self.set_mode(AccessMode.ANYWHERE)
            return revoked

    def disconnect_all(self) -> None:
        if self._server is not None:
            self._server.sessions.clear()
            if self._mode is not AccessMode.OFF:
                self.set_mode(self._mode)

    def active_users(self) -> int:
        return self._server.active_users() if self._server is not None else 0

    def messages_handled(self) -> int:
        return self._server.messages_handled() if self._server is not None else 0

    def status(self) -> dict[str, Any]:
        pki_status = self._pki.status() if self._pki is not None else {
            "ready": False, "active_devices": 0, "revoked_devices": 0, "devices": [],
        }
        network = self._network_readiness(pki_status)
        return {
            "mode": self._mode.value,
            "serving": self._server is not None,
            "url": self._server.url() if self._server is not None else "",
            "users": self.active_users(),
            "messages": self.messages_handled(),
            "mutual_tls": self._mode is AccessMode.ANYWHERE and self._server is not None,
            "minimum_tls": "1.3" if self._mode is AccessMode.ANYWHERE else None,
            "pki": pki_status,
            "public_endpoint": self.public_endpoint or None,
            "external_route_configured": network["external_route_configured"],
            "different_network_ready": network["different_network_ready"],
            "network_readiness": network,
            "route_note": (
                "No firewall, router, UPnP, relay, or paid tunnel was changed."
                if self._mode is AccessMode.ANYWHERE else ""
            ),
            "last_error": self._last_error,
            "authority": "REMOTE_OWNER",
            "zero_cost": True,
            "local_processing": True,
            "manual_token_setup_required": False,
        }

    def _network_readiness(self, pki_status: dict[str, Any]) -> dict[str, Any]:
        """Describe reachable paths using local evidence only.

        This deliberately makes no public-IP web request and never edits the
        firewall, router or DNS.  A configured endpoint is evidence of intent,
        not proof that an outside phone can reach it, so the report keeps the
        two facts separate.
        """
        addresses: set[str] = set()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                addresses.add(str(info[4][0]).split("%", 1)[0])
        except OSError:
            pass
        private_v4: list[str] = []
        global_v6: list[str] = []
        for value in sorted(addresses):
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                continue
            if address.version == 4 and address.is_private and not address.is_loopback:
                private_v4.append(value)
            elif address.version == 6 and address.is_global:
                global_v6.append(value)
        paired = int(pki_status.get("active_devices", 0) or 0)
        endpoint_configured = bool(self.public_endpoint)
        cryptographic_ready = paired > 0
        ready = bool(
            self._mode is AccessMode.ANYWHERE and self._server is not None
            and cryptographic_ready and endpoint_configured
        )
        return {
            "same_wifi_ready": self._server is not None and bool(private_v4),
            "different_network_ready": ready,
            "cryptographic_ready": cryptographic_ready,
            "endpoint_configured": endpoint_configured,
            "external_route_configured": endpoint_configured,
            "outside_reachability_verified": False,
            "private_ipv4": private_v4,
            "global_ipv6_candidates": global_v6,
            "path": (
                "configured_endpoint" if endpoint_configured
                else "public_ipv6_candidate" if global_v6
                else "router_port_forward_or_relay_required"
            ),
            "cgnat_note": (
                "CGNAT cannot be detected reliably from local-only evidence. If the router has no public address, a relay is required."
            ),
            "privacy": "No public-IP lookup, firewall edit, UPnP request, DNS change, or relay was performed.",
        }

    def owner_access_credentials(self) -> dict[str, str]:
        """Reveal login details only to an explicit local owner GUI action."""
        token = self._token(create=True)
        if not token:
            raise RuntimeError("A protected local access passphrase could not be created.")
        return {"url": self._server.url() if self._server is not None else "", "passphrase": token}

    def _pki_manager(self) -> LocalPKI:
        if self._pki is None:
            broker = getattr(self.runtime, "secret_broker", None)
            self._pki = LocalPKI(self._pki_root, secret_broker=broker)
        return self._pki

    def _token(self, *, create: bool = True) -> str:
        # Keep compatibility with current installations. The token is never
        # copied into status, logs, certificates, or persistent GUI state.
        explicit = str(os.environ.get("HAYAT_WEB_TOKEN") or self.config.get("token") or "")
        if explicit:
            return explicit
        if self._session_token:
            return self._session_token
        broker = getattr(self.runtime, "secret_broker", None)
        if broker is None:
            return ""
        name, scope = "remote-access-passphrase", "remote_access:web"
        try:
            known = any(item.get("name") == name for item in broker.list())
            if not known:
                if not create:
                    return ""
                broker.store(name, secrets.token_urlsafe(32), scopes=[scope])
            lease = broker.issue_lease(name, scope, ttl_seconds=15)
            self._session_token = broker.redeem(lease, scope)
            return self._session_token
        except Exception:
            return ""

    def _stop_locked(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
