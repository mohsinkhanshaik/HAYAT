"""Authenticated localhost bridge for the optional HAYAT browser extension."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import secrets
import threading
import time
import uuid
from typing import Any

from app.safety.sensitive import redact
from app.logging import log_swallowed

_log = logging.getLogger(__name__)


class BrowserExtensionBridge:
    def __init__(self, secret_broker: Any, *, event_bus: Any = None, host: str = "127.0.0.1",
                 port: int = 8765, maximum_message_bytes: int = 8 * 1024 * 1024) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("The browser extension bridge may listen only on localhost.")
        self.secrets, self.events = secret_broker, event_bus
        self.host, self.port, self.maximum_message_bytes = host, int(port), int(maximum_message_bytes)
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._socket_lock = threading.RLock()
        self._pending: dict[str, tuple[Any, queue.Queue]] = {}
        self._latest: dict[str, Any] = {}
        self._token_name = "browser-extension-bridge"
        self._token_error = ""
        try:
            self._ensure_token()
        except Exception as exc:
            # An unavailable OS credential backend disables this optional
            # bridge; it must not prevent the rest of HAYAT from starting.
            self._token_error = f"{type(exc).__name__}: {exc}"

    def _ensure_token(self) -> None:
        if not any(item["name"] == self._token_name for item in self.secrets.list()):
            self.secrets.store(self._token_name, secrets.token_urlsafe(48), scopes=["browser:bridge"])

    def pairing_token(self) -> str:
        """Return the token only to an explicit local setup surface."""
        if self._token_error:
            raise RuntimeError(f"Browser bridge credential setup failed: {self._token_error}")
        lease = self.secrets.issue_lease(self._token_name, "browser:bridge", ttl_seconds=10)
        return self.secrets.redeem(lease, "browser:bridge")

    def start(self) -> dict[str, Any]:
        if self._token_error:
            raise RuntimeError(f"Browser bridge credential setup failed: {self._token_error}")
        if self._thread and self._thread.is_alive():
            return self.status()
        ready: queue.Queue = queue.Queue(maxsize=1)

        def run() -> None:
            try:
                from websockets.sync.server import serve
                # Loopback binding plus the pairing token is the security
                # boundary. Browser extension origins contain a generated ID,
                # so an exact origin allowlist would reject every fresh load.
                with serve(self._handle, self.host, self.port,
                           max_size=self.maximum_message_bytes) as server:
                    self._server = server
                    ready.put(None)
                    server.serve_forever()
            except Exception as exc:
                ready.put(exc)
            finally:
                self._server = None

        self._thread = threading.Thread(target=run, name="hayat-browser-extension", daemon=True)
        self._thread.start()
        result = ready.get(timeout=5)
        if isinstance(result, Exception):
            raise result
        self._event("BROWSER_BRIDGE_STARTED", {"host": self.host, "port": self.port})
        return self.status()

    def stop(self) -> None:
        with self._socket_lock:
            socket = self._socket
            self._socket = None
            self._fail_pending(socket, 'Browser extension bridge stopped.')
        if socket is not None:
            socket.close(code=1001, reason='bridge stopped')
        if self._server is not None:
            self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=3)

    def status(self) -> dict[str, Any]:
        return {"running": bool(self._thread and self._thread.is_alive()),
                "connected": self._socket is not None, "host": self.host, "port": self.port,
                "latest_page": {key: self._latest.get(key) for key in ("url", "title", "captured_at")},
                "authentication": "pairing-token", "token_ready": not bool(self._token_error),
                "error": self._token_error, "remote_connections_allowed": False}

    def command(self, command: str, arguments: dict[str, Any] | None = None,
                *, timeout: float = 20) -> dict[str, Any]:
        request_id = f"cmd-{uuid.uuid4().hex[:12]}"
        response: queue.Queue = queue.Queue(maxsize=1)
        with self._socket_lock:
            if self._socket is None:
                raise RuntimeError("The HAYAT browser extension is not connected.")
            self._pending[request_id] = (self._socket, response)
            try:
                self._socket.send(json.dumps({"type": "command", "id": request_id,
                                              "command": command, "arguments": arguments or {}}))
            except Exception:
                self._pending.pop(request_id, None)
                raise
        try:
            result = response.get(timeout=max(1, min(timeout, 60)))
        except queue.Empty as exc:
            raise TimeoutError(f"Browser extension did not answer {command}.") from exc
        finally:
            with self._socket_lock:
                self._pending.pop(request_id, None)
        if not result.get("ok", False):
            raise RuntimeError(str(result.get("error") or "Browser extension command failed."))
        payload = result.get('result')
        if payload is not None and not isinstance(payload, dict):
            raise RuntimeError('Browser extension returned an invalid command result.')
        return redact(payload or {})

    def _fail_pending(self, socket, reason):
        """Called under the socket lock; wake only requests owned by this connection."""
        for request_id, (owner, waiting) in list(self._pending.items()):
            if owner is socket:
                self._pending.pop(request_id, None)
                try:
                    waiting.put_nowait({'ok': False, 'error': reason})
                except queue.Full:
                    pass  # a completed response already belongs to its caller

    def _deliver_response(self, socket, message):
        with self._socket_lock:
            pending = self._pending.get(str(message.get('id') or ''))
            if pending is not None and pending[0] is socket:
                try:
                    pending[1].put_nowait(message)
                except queue.Full:
                    pass  # duplicate replies must never block the WebSocket reader

    def snapshot(self) -> dict[str, Any]:
        result = self.command("snapshot")
        self._accept_snapshot(result)
        return result

    def scroll(self, direction: str = "down", amount: int = 700) -> dict[str, Any]:
        if direction not in {"up", "down", "top", "bottom"}:
            raise ValueError("direction must be up, down, top, or bottom")
        return self.command("scroll", {"direction": direction, "amount": max(1, min(int(amount), 5000))})

    def _handle(self, websocket: Any) -> None:
        try:
            authentication = json.loads(websocket.recv(timeout=10))
            if not isinstance(authentication, dict):
                websocket.close(code=1008, reason='invalid authentication message')
                return
            supplied = str(authentication.get("token") or "")
            expected = self.pairing_token()
            if authentication.get("type") != "authenticate" or not hmac.compare_digest(supplied, expected):
                websocket.close(code=1008, reason="authentication failed")
                return
            with self._socket_lock:
                previous = self._socket
                if previous is not None and previous is not websocket:
                    self._fail_pending(previous, 'Browser extension connection was replaced; retry the request.')
                self._socket = websocket
            if previous is not None and previous is not websocket:
                previous.close(code=1000, reason="replaced by a new authenticated extension")
            websocket.send(json.dumps({"type": "authenticated", "protocol": 1}))
            self._event("BROWSER_BRIDGE_CONNECTED", {})
            for raw in websocket:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Browser extension messages must be objects.')
                kind = message.get("type")
                if kind == "response":
                    self._deliver_response(websocket, message)
                elif kind == "page_snapshot" and isinstance(message.get("result"), dict):
                    self._accept_snapshot(message["result"])
        except Exception:
            log_swallowed(_log, "browser bridge message loop", level=logging.WARNING)
        finally:
            with self._socket_lock:
                self._fail_pending(websocket, 'Browser extension disconnected; reconnect and retry the request.')
                if self._socket is websocket:
                    self._socket = None
            self._event("BROWSER_BRIDGE_DISCONNECTED", {})

    def _accept_snapshot(self, result: dict[str, Any]) -> None:
        text = str(result.get("text") or "")[:500_000]
        self._latest = {
            "url": str(result.get("url") or "")[:4000], "title": str(result.get("title") or "")[:500],
            "text": text, "links": list(result.get("links") or [])[:500],
            "forms": list(result.get("forms") or [])[:100], "captured_at": time.time(),
        }
        self._event("BROWSER_PAGE_SNAPSHOT", {
            "url": self._latest["url"], "title": self._latest["title"],
            "characters": len(text), "sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12],
        })

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.publish(name, "browser_extension_bridge", payload)


def register_browser_bridge_tools(registry: Any, bridge: BrowserExtensionBridge) -> None:
    registry.register("browser_bridge_status", "Show authenticated browser-extension bridge status.",
                      {"type": "object", "properties": {}}, "low", "system_read", bridge.status)
    registry.register("browser_bridge_snapshot", "Read the active browser tab through the connected extension.",
                      {"type": "object", "properties": {}}, "medium", "browser_read", bridge.snapshot)
    registry.register("browser_bridge_scroll", "Scroll the active browser tab through the connected extension.",
                      {"type": "object", "properties": {"direction": {"type": "string", "enum": ["up", "down", "top", "bottom"]},
                       "amount": {"type": "integer", "minimum": 1, "maximum": 5000}}},
                      "medium", "browser_click", bridge.scroll)
