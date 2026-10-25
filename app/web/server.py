"""The local web server: a mobile chat UI over the same runtime.

Design notes that matter for safety:
 - Every chat turn runs inside `execution_scope(principal=REMOTE_OWNER)`, so the
   authority kernel -- not this module -- decides what a remote request may do.
 - The server binds to 127.0.0.1 by default. A LAN or mesh address is opt-in.
 - It refuses to start without an access token, and compares it in constant time.
 - Turns are serialised behind a lock; a single-user assistant does not run two
   agent loops at once.
"""
# NB: no `from __future__ import annotations` here. FastAPI resolves endpoint
# parameter types from their annotations, and the endpoints are defined inside
# create_app where Request/Response are local imports; stringised annotations
# would fail to resolve against the module globals and be mistaken for query
# params. Evaluating them eagerly against the enclosing scope is what we want.
import hmac
import ipaddress
import json
import logging
import secrets
import ssl
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from app.execution import execution_scope
from app.safety.authority import Principal
from app.logging import log_swallowed

_log = logging.getLogger(__name__)

_PRINCIPAL = Principal.REMOTE_OWNER.value
MAX_MESSAGE_CHARS = 32_000
MAX_REQUEST_BYTES = 64 * 1024
MAX_WEBSOCKET_BYTES = 64 * 1024
MAX_LOGIN_SOURCES = 4096


def _valid_token(presented: str, expected: str) -> bool:
    # compare_digest(str, str) raises for non-ASCII attacker input.
    try:
        return bool(presented) and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False


class LocalNetworkPolicy:
    """Admit loopback and one explicitly bound local interface subnet only."""

    def __init__(self, bind_host: str, networks: tuple[Any, ...] | None = None,
                 *, allow_remote: bool = False) -> None:
        self.bind_host = bind_host
        self.networks = networks or _networks_for_host(bind_host)
        self.allow_remote = bool(allow_remote)

    def allows(self, client_host: str | None) -> bool:
        try:
            address = ipaddress.ip_address(str(client_host or "").split("%", 1)[0])
        except ValueError:
            return False
        if address.is_loopback:
            return True
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if self.allow_remote:
            # mTLS is the admission boundary in Anywhere mode. Still reject
            # addresses that cannot identify an actual unicast peer.
            return not (address.is_unspecified or address.is_multicast)
        return any(address.version == network.version and address in network
                   for network in self.networks)

    def describe(self) -> list[str]:
        return [str(network) for network in self.networks]


class LoginRateLimiter:
    """Small in-memory failed-login window; successful login clears its source."""

    def __init__(self, attempts: int = 5, window_seconds: float = 300.0) -> None:
        self.attempts = max(1, attempts)
        self.window_seconds = max(1.0, window_seconds)
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, source: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if source not in self._failures and len(self._failures) >= MAX_LOGIN_SOURCES:
                self._failures = {host: values for host, values in self._failures.items()
                                  if values and now - values[-1] < self.window_seconds}
                if len(self._failures) >= MAX_LOGIN_SOURCES:
                    return False
            recent = [value for value in self._failures.get(source, ())
                      if now - value < self.window_seconds]
            self._failures[source] = recent
            return len(recent) < self.attempts

    def fail(self, source: str) -> None:
        with self._lock:
            if source in self._failures or len(self._failures) < MAX_LOGIN_SOURCES:
                values = self._failures.setdefault(source, [])
                values.append(time.monotonic())
                self._failures[source] = values[-self.attempts:]

    def clear(self, source: str) -> None:
        with self._lock:
            self._failures.pop(source, None)


class SessionTracker:
    """Counts the devices currently talking to HAYAT.

    Each authenticated browser gets a session id and is remembered by when it
    was last seen. "Connected" means seen within the window -- the web page
    polls every few seconds, which both keeps its session alive and lets the
    desktop show how many devices are attached right now.
    """

    def __init__(self, window: float = 90.0) -> None:
        self.window = window
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def touch(self, sid: str) -> None:
        with self._lock:
            self._last[sid] = time.monotonic()

    def active(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._last = {sid: seen for sid, seen in self._last.items()
                          if now - seen <= self.window}
            return len(self._last)

    def clear(self) -> None:
        with self._lock:
            self._last.clear()


class WebMetrics:
    """Thread-safe counters for this web-server lifetime.

    Received, completed, and failed are deliberately separate. A received
    message is not described as "learned": the continuous learner decides
    independently whether a public knowledge question belongs in its queue.
    """

    def __init__(self) -> None:
        self._received = 0
        self._completed = 0
        self._failed = 0
        self._lock = threading.Lock()

    def begin(self) -> tuple[int, str]:
        with self._lock:
            self._received += 1
            sequence = self._received
        return sequence, f"web-{sequence:08d}"

    def finish(self, *, success: bool) -> None:
        with self._lock:
            if success:
                self._completed += 1
            else:
                self._failed += 1

    def bump(self) -> int:
        """Compatibility helper for callers that record one successful turn."""
        sequence, _ = self.begin()
        self.finish(success=True)
        return sequence

    @property
    def messages(self) -> int:
        with self._lock:
            return self._received

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "received": self._received,
                "completed": self._completed,
                "failed": self._failed,
            }


def create_app(runtime: Any, token: str, sessions: SessionTracker | None = None,
               metrics: "WebMetrics | None" = None,
               network_policy: "LocalNetworkPolicy | None" = None,
               login_limiter: "LoginRateLimiter | None" = None,
               shield: Any = None,
               *, secure_cookies: bool = False) -> Any:
    """Build the FastAPI app. `token` is the shared secret the browser presents."""
    from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

    if not token:
        raise ValueError("A web access token is required; refusing to serve without one.")

    app = FastAPI(title="HAYAT", docs_url=None, redoc_url=None, openapi_url=None)
    turn_lock = threading.Lock()
    tracker = sessions if sessions is not None else SessionTracker()
    counter = metrics if metrics is not None else WebMetrics()
    limiter = login_limiter if login_limiter is not None else LoginRateLimiter()
    app.state.sessions = tracker
    app.state.metrics = counter
    app.state.network_policy = network_policy

    async def security_gate(request: Request, call_next: Callable[..., Any]) -> Any:
        client_host = request.client.host if request.client else ""
        if network_policy is not None and not network_policy.allows(client_host):
            return PlainTextResponse("Local network only.", status_code=403)
        length = request.headers.get("content-length", "")
        if length and (not length.isascii() or not length.isdecimal()):
            return JSONResponse({"ok": False, "error": "invalid content length"}, status_code=400)
        if length.isdigit() and (len(length) > 16 or int(length) > MAX_REQUEST_BYTES):
            if shield is not None:
                shield.offence(client_host, "oversized", f"{length} bytes")
            return JSONResponse({"ok": False, "error": "request too large"}, status_code=413)
        # Intrusion gate: a banned or probing source is stopped here, before any
        # endpoint runs. Synthetic decoys never pass through runtime or auth.
        if shield is not None:
            verdict = shield.inspect(client_host, request.method, request.url.path, query=request.url.query or "")
            if verdict.blocked:
                decoy = shield.decoy_response(client_host, request.method, request.url.path)
                if decoy is not None:
                    content_type, body = decoy
                    return PlainTextResponse('' if request.method == 'HEAD' else body, media_type=content_type,
                                             headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
                if verdict.delay > 0:
                    import anyio

                    await anyio.sleep(verdict.delay)
                headers = {"Retry-After": str(verdict.retry_after)} if verdict.retry_after else {}
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=verdict.status, headers=headers)
        return await call_next(request)

    @app.middleware("http")
    async def local_security(request: Request, call_next: Callable[..., Any]) -> Any:
        # Include early refusals and decoys, not only successful endpoint replies.
        response = await security_gate(request, call_next)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws: wss:; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if secure_cookies:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    def authorized(request: Request) -> bool:
        presented = request.cookies.get("hayat_token") or ""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            presented = header[7:].strip() or presented
        return _valid_token(presented, token)

    def ensure_session(request: Request) -> tuple[str, bool]:
        """Record this device as present, returning its id and whether it is new.

        Records before the count is read, so a device sees itself included."""
        sid = request.cookies.get("hayat_sid")
        is_new = not sid
        if is_new:
            sid = secrets.token_urlsafe(9)
        tracker.touch(sid)
        return sid, is_new

    def set_session_cookie(response: Any, sid: str, is_new: bool) -> None:
        if is_new:
            response.set_cookie("hayat_sid", sid, httponly=True, samesite="strict",
                                secure=secure_cookies, max_age=60 * 60 * 24 * 30)

    @app.get("/health")
    def health() -> Any:
        # No secret here -- only liveness, nothing about the machine or the chat.
        return JSONResponse({"ok": True, "service": "hayat"})

    @app.get("/")
    def index() -> Any:
        return HTMLResponse(_PAGE)

    @app.post("/api/login")
    async def login(request: Request) -> Any:
        source = request.client.host if request.client else "unknown"
        if not limiter.allowed(source):
            return JSONResponse({"ok": False, "error": "too many attempts"},
                                status_code=429, headers={"Retry-After": "300"})
        body = await _json(request)
        presented = str(body.get("token", ""))
        if not _valid_token(presented, token):
            limiter.fail(source)
            if shield is not None and shield.note_failed_login(source).blocked:
                return JSONResponse({"ok": False, "error": "too many attempts"},
                                    status_code=403, headers={"Retry-After": "900"})
            return JSONResponse({"ok": False, "error": "wrong passphrase"}, status_code=401)
        limiter.clear(source)
        if shield is not None:
            shield.note_success(source)
        response = JSONResponse({"ok": True})
        # HttpOnly so page scripts can't read it; SameSite=Strict; the mesh/https
        # carries confidentiality. Secure is set by the reverse path when https.
        response.set_cookie("hayat_token", token, httponly=True, samesite="strict",
                            secure=secure_cookies, max_age=60 * 60 * 24 * 30)
        return response

    @app.post("/api/logout")
    def logout(request: Request) -> Any:
        if not _same_origin(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"ok": False, "error": "origin rejected"}, status_code=403)
        response = JSONResponse({"ok": True})
        response.delete_cookie("hayat_token")
        return response

    @app.get("/api/status")
    def status(request: Request) -> Any:
        # The web page polls this to show it is connected and how many devices
        # are attached. Auth-gated so the count is not readable by a stranger.
        if not authorized(request):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        sid, is_new = ensure_session(request)
        learning = _learning_snapshot(runtime)
        response = JSONResponse({
            "ok": True, "available": True, "users": tracker.active(),
            "messages": counter.messages, "metrics": counter.snapshot(),
            "learned": learning["learned"], "learning": learning,
        })
        set_session_cookie(response, sid, is_new)
        return response

    @app.post("/api/chat")
    async def chat(request: Request) -> Any:
        if not authorized(request):
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
        if not _same_origin(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"ok": False, "error": "origin rejected"}, status_code=403)
        body = await _json(request)
        message = str(body.get("message", "")).strip()
        if not message:
            return JSONResponse({"ok": False, "error": "empty message"}, status_code=400)
        if len(message) > MAX_MESSAGE_CHARS:
            return JSONResponse({"ok": False, "error": "message too large"}, status_code=413)
        _, message_id = counter.begin()
        learning_before = _learning_snapshot(runtime)
        try:
            reply = await _run_turn(runtime, message, turn_lock)
        except Exception as exc:  # never leak a stack trace to the browser
            counter.finish(success=False)
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}",
                                 "message_id": message_id}, status_code=500)
        counter.finish(success=True)
        learning_after = _learning_snapshot(runtime)
        result = JSONResponse({
            "ok": True, "message_id": message_id,
            "response": reply.get("response", ""), "complete": reply.get("complete"),
            "messages": counter.messages, "metrics": counter.snapshot(),
            "learning": _learning_change(learning_before, learning_after),
        })
        sid, is_new = ensure_session(request)
        set_session_cookie(result, sid, is_new)
        return result

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        # Activity/status is live while the turn runs. Model providers return a
        # completed verified answer today, which is then delivered in ordered
        # chunks. This exposes progress, never private chain-of-thought.
        import anyio

        client_host = websocket.client.host if websocket.client else ""
        if network_policy is not None and not network_policy.allows(client_host):
            await websocket.close(code=1008, reason="local network only")
            return
        # HTTP middleware does not run for WebSocket handshakes. Apply the same
        # ban gate explicitly, before auth or any runtime/session interaction.
        if shield is not None and shield.inspect(client_host, "WS", "/ws").blocked:
            await websocket.close(code=1008, reason="unavailable")
            return
        if not _same_origin(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008, reason="origin rejected")
            return
        presented = websocket.cookies.get("hayat_token") or ""
        if not _valid_token(presented, token):
            if shield is not None:
                shield.note_failed_login(client_host)
            await websocket.close(code=1008)
            return
        await websocket.accept()
        sid = websocket.cookies.get("hayat_sid") or secrets.token_urlsafe(9)
        tracker.touch(sid)
        await websocket.send_json({
            "type": "hello", "protocol": 2, "users": tracker.active(),
            "messages": counter.messages, "metrics": counter.snapshot(),
            "learning": _learning_snapshot(runtime),
        })
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                except (ValueError, UnicodeDecodeError):
                    await websocket.close(code=1003, reason="invalid message")
                    return
                if shield is not None and not shield.trust(client_host) and shield.banned_for(client_host) > 0:
                    await websocket.close(code=1008, reason="unavailable")
                    return
                if not isinstance(data, dict):
                    await websocket.send_json({"type": "error", "error": "invalid message"})
                    continue
                message = str((data or {}).get("message", "")).strip()
                if not message:
                    await websocket.send_json({"type": "error", "error": "empty"})
                    continue
                if len(message) > MAX_MESSAGE_CHARS:
                    await websocket.send_json({"type": "error", "error": "message too large"})
                    await websocket.close(code=1009, reason="message too large")
                    return
                tracker.touch(sid)
                _, message_id = counter.begin()
                learning_before = _learning_snapshot(runtime)
                await websocket.send_json({
                    "type": "status", "stage": "received", "text": "Message received",
                    "message_id": message_id, "messages": counter.messages,
                })
                try:
                    reply = await _stream_turn(websocket, runtime, message, turn_lock, message_id)
                except Exception:
                    counter.finish(success=False)
                    await websocket.send_json({"type": "error", "error": "failed",
                                               "message_id": message_id,
                                               "metrics": counter.snapshot()})
                    continue
                counter.finish(success=True)
                learning_after = _learning_snapshot(runtime)
                learning_change = _learning_change(learning_before, learning_after)
                await websocket.send_json({"type": "learning", "message_id": message_id,
                                           **learning_change})
                await websocket.send_json({"type": "status", "stage": "replying",
                                           "text": "Reply ready", "message_id": message_id})
                for sequence, chunk in enumerate(_word_chunks(reply.get("response", "")), 1):
                    await websocket.send_json({"type": "chunk", "message_id": message_id,
                                               "sequence": sequence, "text": chunk})
                    await anyio.sleep(0.02)
                await websocket.send_json({
                    "type": "done", "message_id": message_id,
                    "complete": reply.get("complete"), "users": tracker.active(),
                    "messages": counter.messages, "metrics": counter.snapshot(),
                    "learning": learning_change["current"],
                })
        except WebSocketDisconnect:
            return
        except Exception:
            try:
                await websocket.close()
            except Exception:
                log_swallowed(_log, "closing a failed websocket")

    return app


def _word_chunks(text: str, size: int = 3):
    """Yield the text a few words at a time, so a finished reply types out."""
    words = text.split(" ")
    for start in range(0, len(words), size):
        piece = " ".join(words[start:start + size])
        yield piece if start == 0 else " " + piece


def _learning_snapshot(runtime: Any) -> dict[str, Any]:
    """Return compact, factual background-learning state for the mobile UI."""
    learner = getattr(runtime, "learner", None)
    if learner is None:
        return {
            "available": False, "running": False, "paused": False,
            "pending": 0, "active": 0, "learned": 0, "failed": 0, "total": 0,
        }
    try:
        queue_state = learner.queue_status(3) if hasattr(learner, "queue_status") else {}
        counts = queue_state.get("counts", {}) if isinstance(queue_state, dict) else {}
        topics = queue_state.get("topics", []) if isinstance(queue_state, dict) else []
        pending = int(counts.get("pending", 0))
        active = int(counts.get("learning", 0))
        learned = int(counts.get("learned", 0))
        failed = int(counts.get("failed", 0))
        return {
            "available": True,
            "running": bool(getattr(learner, "running", False)),
            "paused": bool(getattr(learner, "paused", False)),
            "pending": pending, "active": active, "learned": learned, "failed": failed,
            "total": sum(int(value) for value in counts.values()),
            "topics": [
                {"topic": str(item.get("topic", ""))[:160],
                 "status": str(item.get("status", "")),
                 "source": str(item.get("source", ""))}
                for item in topics if isinstance(item, dict)
            ],
        }
    except Exception:
        return {
            "available": True, "running": bool(getattr(learner, "running", False)),
            "paused": bool(getattr(learner, "paused", False)),
            "pending": 0, "active": 0, "learned": 0, "failed": 0, "total": 0,
            "degraded": True,
        }


def _learning_change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Describe only an observed queue change; never claim that a message taught itself."""
    queued = max(0, int(after.get("total", 0)) - int(before.get("total", 0)))
    learned = max(0, int(after.get("learned", 0)) - int(before.get("learned", 0)))
    return {"queued_this_turn": queued, "learned_this_turn": learned, "current": after}


def _activity_status() -> dict[str, Any] | None:
    """Translate live activity into a small public progress frame."""
    try:
        from app.activity import ACTIVITY

        items = ACTIVITY.current()
    except Exception:
        return None
    if not items:
        return None
    visible = [item for item in items if item.kind in {
        "model", "research", "learning", "image", "tool", "task", "service",
    }]
    item = max(visible or items, key=lambda value: value.started)
    stage = {
        "model": "generating", "research": "researching", "learning": "learning",
        "image": "generating_image", "tool": "using_tool", "task": "working",
        "service": "checking_service",
    }.get(item.kind, "working")
    return {
        "stage": stage, "text": item.describe(),
        "activity": {
            "kind": item.kind, "elapsed_seconds": round(item.elapsed, 1),
            "remaining_seconds": round(item.remaining, 1),
        },
    }


async def _stream_turn(websocket: Any, runtime: Any, message: str,
                       lock: threading.Lock, message_id: str) -> dict[str, Any]:
    """Run a turn while forwarding bounded lifecycle and activity updates."""
    import asyncio

    loop = asyncio.get_running_loop()
    stages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_stage(stage: str, text: str) -> None:
        loop.call_soon_threadsafe(stages.put_nowait, {"stage": stage, "text": text})

    task = asyncio.create_task(_run_turn(runtime, message, lock, on_stage=on_stage))
    last_signature: tuple[str, str] | None = None
    while not task.done():
        try:
            update = await asyncio.wait_for(stages.get(), timeout=0.20)
        except TimeoutError:
            update = _activity_status()
        if not update:
            continue
        signature = (str(update.get("stage", "")), str(update.get("text", "")))
        if signature == last_signature:
            continue
        last_signature = signature
        await websocket.send_json({"type": "status", "message_id": message_id, **update})
    while not stages.empty():
        update = stages.get_nowait()
        signature = (str(update.get("stage", "")), str(update.get("text", "")))
        if signature != last_signature:
            last_signature = signature
            await websocket.send_json({"type": "status", "message_id": message_id, **update})
    return await task


async def _json(request: Any) -> dict[str, Any]:
    from fastapi import HTTPException

    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="request too large")
        payload.extend(chunk)
    try:
        data = json.loads(payload)
        return data if isinstance(data, dict) else {}
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {}


async def _run_turn(runtime: Any, message: str, lock: threading.Lock,
                    on_stage: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    """Run one turn as REMOTE_OWNER, off the event loop, one at a time."""
    import anyio

    def work() -> dict[str, Any]:
        if on_stage is not None:
            on_stage("queued", "Waiting for the shared HAYAT runtime")
        with lock:
            if on_stage is not None:
                on_stage("thinking", "Understanding and preparing the answer")
            with execution_scope(principal=_PRINCIPAL):
                reply = runtime.assistant.handle(message, source="web")
            if on_stage is not None:
                on_stage("verifying", "Finalizing the verified reply")
        return {"response": getattr(reply, "display_response", "") or "",
                "complete": getattr(reply, "complete", None)}

    return await anyio.to_thread.run_sync(work)


def _same_origin(origin: str | None, host: str | None) -> bool:
    """Accept absent non-browser origins or an exact browser same-origin host."""
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == str(host or "").casefold()


def _resolve_bind_host(host: str) -> str:
    """Resolve the `lan` alias and reject wildcard/public interface exposure."""
    value = str(host or "").strip().casefold()
    if value == "lan":
        value = _lan_ip()
    if value == "localhost":
        value = "127.0.0.1"
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Web host must be 'lan', localhost, or a local IP address.") from exc
    if address.is_unspecified:
        raise ValueError("Wildcard web binding is disabled; use host 'lan' for same-Wi-Fi access.")
    if address.is_multicast or address.is_link_local or not (address.is_loopback or address.is_private):
        raise ValueError("Web host must be a loopback or private LAN address.")
    if not address.is_loopback and str(address) != _lan_ip():
        raise ValueError("Web host must match the primary physical LAN interface; use host 'lan'.")
    return str(address)


def _networks_for_host(host: str) -> tuple[Any, ...]:
    """Find the real interface prefix; conservatively fall back when unavailable."""
    address = ipaddress.ip_address(host)
    if address.is_loopback:
        return (ipaddress.ip_network("127.0.0.0/8"),) if address.version == 4 else (
            ipaddress.ip_network("::1/128"),
        )
    try:
        import psutil

        for rows in psutil.net_if_addrs().values():
            for row in rows:
                raw = str(getattr(row, "address", "")).split("%", 1)[0]
                if raw != str(address) or not getattr(row, "netmask", None):
                    continue
                return (ipaddress.ip_network(f"{address}/{row.netmask}", strict=False),)
    except Exception:
        log_swallowed(_log, "reading the LAN interface netmask")
    prefix = 24 if address.version == 4 else 64
    return (ipaddress.ip_network(f"{address}/{prefix}", strict=False),)


def _build_shield(runtime: Any, network_policy: "LocalNetworkPolicy") -> Any:
    """An IntrusionShield that trusts the owner's own LAN and logs to data/security."""
    from app.config import PROJECT_ROOT
    from app.security.intrusion import IntrusionShield

    trusted = tuple(getattr(network_policy, "networks", ()) or ())
    store = PROJECT_ROOT / "data" / "security" / "intrusion-bans.json"
    return IntrusionShield(store, events=getattr(runtime, "events", None), trusted_networks=trusted)


class WebServer:
    """Runs the app on a background uvicorn thread, tolerant of being absent."""

    def __init__(self, runtime: Any, *, host: str = "127.0.0.1", port: int = 8770,
                 token: str | None = None, tls: Any = None,
                 allow_remote: bool = False) -> None:
        self.runtime = runtime
        self.requested_host = host
        self.host = _resolve_bind_host(host)
        self.port = port
        # Fail closed: a generated token is printed once so the owner can use it,
        # but the server never runs tokenless.
        self.token = token or secrets.token_urlsafe(18)
        self.tls = tls
        self.allow_remote = bool(allow_remote)
        if self.allow_remote and self.tls is None:
            raise ValueError("Remote clients require mutual TLS; refusing an exposed token-only server.")
        self.sessions = SessionTracker()
        self.metrics = WebMetrics()
        self.network_policy = LocalNetworkPolicy(self.host, allow_remote=self.allow_remote)
        self.login_limiter = LoginRateLimiter()
        self.shield = _build_shield(runtime, self.network_policy)
        self._server: Any = None
        self._thread: threading.Thread | None = None

    def active_users(self) -> int:
        """How many remote devices are attached right now (0 if not serving)."""
        return self.sessions.active()

    def messages_handled(self) -> int:
        """How many authenticated messages this server lifetime received."""
        return self.metrics.messages

    def start(self) -> None:
        import uvicorn

        app = create_app(self.runtime, self.token, self.sessions, self.metrics,
                         self.network_policy, self.login_limiter, self.shield,
                         secure_cookies=self.tls is not None)
        tls_options: dict[str, Any] = {}
        if self.tls is not None:
            tls_options = {
                "ssl_certfile": str(self.tls.certfile),
                "ssl_keyfile": str(self.tls.keyfile),
                "ssl_keyfile_password": self.tls.key_password,
                "ssl_ca_certs": str(self.tls.trustfile),
                "ssl_cert_reqs": ssl.CERT_REQUIRED,
                "ssl_version": ssl.PROTOCOL_TLS_SERVER,
                "ssl_context_factory": _strict_tls_context,
            }
        config = uvicorn.Config(
            app, host=self.host, port=self.port, log_level="warning",
            access_log=False, ws_max_size=MAX_WEBSOCKET_BYTES,
            limit_concurrency=16, timeout_keep_alive=15, **tls_options,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="hayat-web", daemon=True)
        self._thread.start()
        # uvicorn binds inside the thread, so wait briefly to learn whether it
        # actually came up -- a port already in use should be reported, not
        # silently claimed as running.
        for _ in range(40):
            if getattr(self._server, "started", False):
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.05)
        if not getattr(self._server, "started", False):
            raise RuntimeError(f"web server could not bind {self.host}:{self.port} (already in use?)")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def url(self) -> str:
        if self.host in ("127.0.0.1", "localhost"):
            shown = "localhost"
        elif self.host in ("0.0.0.0", "::"):
            # Bound to every interface -- the useful address for a phone is this
            # machine's place on the local network, not 0.0.0.0.
            shown = _lan_ip()
        else:
            shown = self.host
        scheme = "https" if self.tls is not None else "http"
        return f"{scheme}://{shown}:{self.port}/"


def _strict_tls_context(_config: Any, default_factory: Callable[[], ssl.SSLContext]) -> ssl.SSLContext:
    """Require modern TLS and enforce the locally generated revocation list."""
    context = default_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, "VERIFY_CRL_CHECK_LEAF"):
        context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
    return context


def _lan_ip() -> str:
    """This machine's primary LAN address, including when internet is offline.

    Uses a UDP socket to a public address to learn which local interface the OS
    would route through -- no packet is actually sent."""
    candidates: list[tuple[int, str]] = []
    try:
        import psutil

        excluded = ("tailscale", "vpn", "zerotier", "virtual", "vethernet", "wsl", "hyper-v")
        for interface, rows in psutil.net_if_addrs().items():
            label = interface.casefold()
            if any(term in label for term in excluded):
                continue
            if any(term in label for term in ("wi-fi", "wifi", "wlan", "wireless")):
                priority = 0
            elif "ethernet" in label or label.startswith(("eth", "en")):
                priority = 1
            else:
                priority = 2
            for row in rows:
                raw = str(getattr(row, "address", "")).split("%", 1)[0]
                try:
                    address = ipaddress.ip_address(raw)
                except ValueError:
                    continue
                if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
                    continue
                candidates.append((priority, raw))
        physical = [item for item in candidates if item[0] <= 1]
        if physical:
            return sorted(physical)[0][1]
    except Exception:
        log_swallowed(_log, "choosing a physical LAN address")

    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        candidate = str(sock.getsockname()[0])
        address = ipaddress.ip_address(candidate)
        if address.is_private and not address.is_loopback and not address.is_link_local:
            return candidate
    except Exception:
        log_swallowed(_log, "probing the outbound LAN address")
    finally:
        try:
            sock.close()
        except Exception:
            log_swallowed(_log, "closing the address probe socket")
    if candidates:
        return sorted(candidates)[0][1]
    return "127.0.0.1"


def start_web_if_enabled(runtime: Any) -> tuple[Any, str]:
    """Start the web server when settings enable it and a token exists.

    Returns (server_or_None, message). Fail closed: enabled-but-tokenless does
    not start; it explains how to enable instead. This is what the desktop calls
    so web access comes up with the window rather than as a separate process.
    """
    import os

    from app.config import load_settings

    settings = load_settings()
    web = settings.get("web", {}) if isinstance(settings, dict) else {}
    if not web.get("enabled", False):
        return None, ""
    token = os.environ.get("HAYAT_WEB_TOKEN") or web.get("token") or ""
    if not token:
        return None, ("Phone access is enabled but has no passphrase. Add "
                      "HAYAT_WEB_TOKEN=<a long passphrase> to your .env to turn it on.")
    host = str(web.get("host") or "lan")
    port = int(web.get("port") or 8770)
    try:
        server = WebServer(runtime, host=host, port=port, token=token)
        server.start()
    except Exception as exc:
        return None, f"Phone access could not start: {type(exc).__name__}."
    return server, f"Phone access is on at {server.url()} -- open it in your phone's browser."


#: The mobile-first chat page. Inlined so there is nothing else to serve and no
#: external request the CSP or the network could block. Deliberately small.
_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#07080A"><title>HAYAT</title>
<style>
:root{--bg:#07080A;--panel:#12141A;--panel2:#191C22;--white:#F4F6F8;--muted:#8A93A0;--gold:#D4A82C;--royal:#7FA0F0}
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--white);font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;display:flex;flex-direction:column}
header{padding:14px 16px;background:var(--panel2);font-weight:600;display:flex;align-items:center;gap:8px}
header .dot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
.dot.on{background:#39B36A}.dot.off{background:#E05B5B}
#status{margin-left:auto;font-size:13px;color:var(--muted);font-weight:400}
#activity{padding:6px 16px;background:#0D0F14;color:var(--royal);font-size:13px;min-height:32px}
#log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:85%;padding:10px 13px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word}
.me{align-self:flex-end;background:var(--royal);color:#07080A;border-bottom-right-radius:4px}
.ai{align-self:flex-start;background:var(--panel);border-bottom-left-radius:4px}
.sys{align-self:center;color:var(--muted);font-size:13px;background:none}
form{display:flex;gap:8px;padding:10px;background:var(--panel2);padding-bottom:calc(10px + env(safe-area-inset-bottom))}
textarea{flex:1;resize:none;background:var(--bg);color:var(--white);border:1px solid #2A2E36;border-radius:12px;padding:10px 12px;font:inherit;max-height:120px}
button{background:var(--gold);color:#07080A;border:0;border-radius:12px;padding:0 18px;font-weight:600}
#gate{position:fixed;inset:0;background:var(--bg);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px}
#gate input{background:var(--panel2);color:var(--white);border:1px solid #2A2E36;border-radius:12px;padding:12px;width:min(320px,90vw);font:inherit}
#gate button{padding:12px 18px}
.hide{display:none!important}
</style></head><body>
<div id="gate"><div class="dot" style="width:14px;height:14px;border-radius:50%;background:var(--gold)"></div>
<div style="font-weight:600;font-size:20px">HAYAT</div>
<div style="color:var(--muted);font-size:14px">Enter your access passphrase</div>
<input id="pass" type="password" autocomplete="current-password" placeholder="passphrase">
<button onclick="login()">Unlock</button><div id="gerr" style="color:#E05B5B;font-size:13px"></div></div>
<header><span class="dot" id="sdot"></span>HAYAT<span id="status">connecting...</span></header>
<div id="activity" role="status" aria-live="polite">Ready</div>
<div id="log"></div>
<form onsubmit="return send(event)">
<textarea id="in" rows="1" placeholder="Message HAYAT..." autocomplete="off"></textarea>
<button id="sendbtn" type="submit">Send</button></form>
<script>
const log=document.getElementById('log'),input=document.getElementById('in');
const sdot=document.getElementById('sdot'),statusEl=document.getElementById('status'),activityEl=document.getElementById('activity'),sendBtn=document.getElementById('sendbtn');
function add(cls,text){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;log.appendChild(d);log.scrollTop=log.scrollHeight;return d}
function setStatus(users,messages,learning){sdot.className='dot on';const learned=learning&&learning.learned||0;statusEl.textContent=(users||1)+' connected | '+(messages||0)+' messages | '+learned+' learned';}
function setActivity(text){activityEl.textContent=text||'Ready';}
function offline(){sdot.className='dot off';statusEl.textContent='offline';}
async function poll(){try{const r=await fetch('/api/status');if(!r.ok)throw 0;const j=await r.json();setStatus(j.users,j.messages,j.learning);}catch(e){offline();}}
let ws=null,current=null,busy=false;
function connectWS(){try{const p=location.protocol==='https:'?'wss://':'ws://';ws=new WebSocket(p+location.host+'/ws');
 ws.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch(_){return}
   if(m.type==='hello'){setStatus(m.users,m.messages,m.learning);setActivity('Ready');}
   else if(m.type==='status'){setActivity(m.text||m.stage);if(current&&current.dataset.s!=='1')current.textContent=m.text||'Working...';}
   else if(m.type==='learning'){const q=m.queued_this_turn||0,l=m.learned_this_turn||0;setActivity(q?('Queued '+q+' topic for background learning'):l?('Learned '+l+' queued topic'):'Learning queue checked; no new topic was added');}
   else if(m.type==='chunk'&&current){if(current.dataset.s!=='1'){current.textContent='';current.dataset.s='1';}current.textContent+=m.text;log.scrollTop=log.scrollHeight;}
   else if(m.type==='done'){current=null;busy=false;sendBtn.disabled=false;setStatus(m.users,m.messages,m.learning);setActivity('Ready');}
   else if(m.type==='error'&&current){current.textContent='['+(m.error||'error')+']';current=null;busy=false;sendBtn.disabled=false;setActivity('Request failed');}};
 ws.onclose=()=>{ws=null;};ws.onerror=()=>{};}catch(e){ws=null;}}
async function login(){const t=document.getElementById('pass').value;
 const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})});
 if(r.ok){document.getElementById('gate').classList.add('hide');add('sys','Connected to HAYAT on your machine. Useful public questions can be queued for background learning.');connectWS();poll();setInterval(poll,8000);}
 else{document.getElementById('gerr').textContent='Wrong passphrase.';}}
document.getElementById('pass').addEventListener('keydown',e=>{if(e.key==='Enter')login()});
input.addEventListener('input',()=>{input.style.height='auto';input.style.height=Math.min(input.scrollHeight,120)+'px'});
input.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(e)}});
function send(e){e.preventDefault();const text=input.value.trim();if(!text||busy)return false;
 busy=true;sendBtn.disabled=true;
 add('me',text);input.value='';input.style.height='auto';
 current=add('ai','Sending...');current.dataset.s='0';
 if(ws&&ws.readyState===1){ws.send(JSON.stringify({message:text}));}
 else{fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})})
   .then(r=>r.json()).then(j=>{current.textContent=j.ok?(j.response||'(no reply)'):('['+(j.error||'error')+']');current=null;busy=false;sendBtn.disabled=false;if(j.ok)poll();})
   .catch(()=>{current.textContent='[connection lost]';current=null;busy=false;sendBtn.disabled=false;});
  if(!ws)connectWS();}
 return false;}
</script></body></html>"""
