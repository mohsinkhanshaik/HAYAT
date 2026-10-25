"""The local web server: auth is enforced and every turn is REMOTE_OWNER."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from app.execution import current_context  # noqa: E402
from app.web.server import create_app  # noqa: E402

TOKEN = "test-passphrase-123"


def _runtime(seen: dict) -> SimpleNamespace:
    def handle(message: str, source: str = "") -> SimpleNamespace:
        # Record what the authority kernel would see for this turn.
        seen["principal"] = current_context().principal
        seen["source"] = source
        seen["message"] = message
        return SimpleNamespace(display_response=f"echo: {message}", complete=True)

    return SimpleNamespace(assistant=SimpleNamespace(handle=handle))


def test_a_token_is_required_to_create_the_app():
    with pytest.raises(ValueError):
        create_app(_runtime({}), "")


def test_decoy_theft_request_never_reaches_runtime_or_authenticates():
    from app.security.intrusion import IntrusionShield
    seen = {}
    shield = IntrusionShield()
    client = TestClient(create_app(_runtime(seen), TOKEN, shield=shield), client=('203.0.113.9', 1234))
    response = client.get('/.env')
    assert response.status_code == 200 and TOKEN not in response.text
    assert response.headers['cache-control'] == 'no-store'
    fake_key = response.text.split('API_KEY=')[1].splitlines()[0]
    assert shield.banned_for('203.0.113.9') > 0 and seen == {}
    innocent = TestClient(create_app(_runtime(seen), TOKEN))
    assert innocent.post('/api/chat', json={'message': 'hello'},
                         headers={'Authorization': 'Bearer ' + fake_key}).status_code == 401
    assert seen == {}
    assert client.get('/api/status').json()['records'] == []


def test_health_needs_no_secret_but_reveals_nothing():
    client = TestClient(create_app(_runtime({}), TOKEN))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "hayat"}


def test_chat_is_refused_without_authentication():
    client = TestClient(create_app(_runtime({}), TOKEN))
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_wrong_passphrase_is_rejected():
    client = TestClient(create_app(_runtime({}), TOKEN))
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401


def test_login_then_chat_runs_the_turn_as_remote_owner():
    seen: dict = {}
    client = TestClient(create_app(_runtime(seen), TOKEN))
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 200
    # The login cookie is carried by the client from here on.
    response = client.post("/api/chat", json={"message": "make me a plan"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["response"] == "echo: make me a plan"
    assert body["message_id"] == "web-00000001"
    assert body["messages"] == 1
    assert body["metrics"] == {"received": 1, "completed": 1, "failed": 0}
    # The security contract: the turn ran under the reduced remote principal.
    assert seen["principal"] == "REMOTE_OWNER"
    assert seen["source"] == "web"


def test_a_bearer_token_also_authenticates():
    seen: dict = {}
    client = TestClient(create_app(_runtime(seen), TOKEN))
    response = client.post("/api/chat", json={"message": "hello"},
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert seen["principal"] == "REMOTE_OWNER"


def test_an_empty_message_is_rejected():
    client = TestClient(create_app(_runtime({}), TOKEN))
    assert client.post("/api/chat", json={"message": "  "},
                       headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 400


def test_the_index_page_serves_the_chat_ui():
    client = TestClient(create_app(_runtime({}), TOKEN))
    page = client.get("/")
    assert page.status_code == 200
    assert "HAYAT" in page.text and "passphrase" in page.text
    assert "messages" in page.text and "background learning" in page.text
    assert "â" not in page.text


def test_security_headers_are_added_to_every_http_response():
    response = TestClient(create_app(_runtime({}), TOKEN)).get("/health")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_lan_policy_allows_only_loopback_and_the_bound_subnet():
    import ipaddress

    from app.web.server import LocalNetworkPolicy

    policy = LocalNetworkPolicy(
        "192.168.12.159", (ipaddress.ip_network("192.168.12.0/24"),),
    )
    assert policy.allows("127.0.0.1")
    assert policy.allows("192.168.12.20")
    assert not policy.allows("192.168.13.20")
    assert not policy.allows("100.64.1.20")
    assert not policy.allows("8.8.8.8")
    assert not policy.allows("not-an-address")


def test_http_lan_admission_rejects_a_client_outside_the_subnet():
    import ipaddress

    from app.web.server import LocalNetworkPolicy

    policy = LocalNetworkPolicy(
        "192.168.12.159", (ipaddress.ip_network("192.168.12.0/24"),),
    )
    allowed = TestClient(create_app(_runtime({}), TOKEN, network_policy=policy),
                         client=("192.168.12.42", 50000))
    denied = TestClient(create_app(_runtime({}), TOKEN, network_policy=policy),
                        client=("192.168.13.42", 50000))
    assert allowed.get("/health").status_code == 200
    response = denied.get("/health")
    assert response.status_code == 403 and response.text == "Local network only."


def test_wildcard_public_and_mesh_bindings_are_rejected(monkeypatch):
    from app.web.server import WebServer

    runtime = SimpleNamespace()
    for host in ("0.0.0.0", "8.8.8.8", "100.64.1.2", "169.254.10.2"):
        with pytest.raises(ValueError):
            WebServer(runtime, host=host, token="t")
    monkeypatch.setattr("app.web.server._lan_ip", lambda: "192.168.12.159")
    server = WebServer(runtime, host="lan", token="t")
    assert server.host == "192.168.12.159"


def test_failed_login_is_throttled_and_success_clears_the_window():
    from app.web.server import LoginRateLimiter

    limiter = LoginRateLimiter(attempts=2, window_seconds=300)
    app = create_app(_runtime({}), TOKEN, login_limiter=limiter)
    client = TestClient(app)
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
    throttled = client.post("/api/login", json={"token": TOKEN})
    assert throttled.status_code == 429 and throttled.headers["retry-after"] == "300"

    second = TestClient(create_app(_runtime({}), TOKEN,
                                   login_limiter=LoginRateLimiter(attempts=2)),
                        client=("127.0.0.2", 50000))
    assert second.post("/api/login", json={"token": TOKEN}).status_code == 200


def test_cross_origin_and_oversized_chat_requests_are_rejected():
    client = TestClient(create_app(_runtime({}), TOKEN))
    client.post("/api/login", json={"token": TOKEN})
    rejected = client.post("/api/chat", json={"message": "hello"},
                           headers={"Origin": "http://attacker.invalid"})
    assert rejected.status_code == 403
    accepted = client.post("/api/chat", json={"message": "hello"},
                           headers={"Origin": "http://testserver"})
    assert accepted.status_code == 200
    oversized = client.post("/api/chat", json={"message": "x" * 32_001})
    assert oversized.status_code == 413


def test_websocket_streams_status_learning_reply_and_counts_messages():
    seen: dict = {}

    class Learner:
        running = True
        paused = False

        def __init__(self) -> None:
            self.pending = 0

        def queue_status(self, _limit: int) -> dict:
            counts = {"pending": self.pending, "learned": 4}
            return {"counts": counts, "topics": []}

    learner = Learner()
    runtime = _runtime(seen)
    original = runtime.assistant.handle

    def handle(message: str, source: str = "") -> SimpleNamespace:
        learner.pending += 1
        return original(message, source)

    runtime.assistant.handle = handle
    runtime.learner = learner
    client = TestClient(create_app(runtime, TOKEN))
    client.post("/api/login", json={"token": TOKEN})

    with client.websocket_connect("/ws") as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "hello" and hello["protocol"] == 2
        assert hello["messages"] == 0 and hello["learning"]["learned"] == 4
        websocket.send_json({"message": "explain the architecture"})
        frames = []
        while not frames or frames[-1].get("type") != "done":
            frames.append(websocket.receive_json())

    assert frames[0] == {
        "type": "status", "stage": "received", "text": "Message received",
        "message_id": "web-00000001", "messages": 1,
    }
    assert {frame.get("stage") for frame in frames if frame["type"] == "status"} >= {
        "received", "thinking", "verifying", "replying",
    }
    learning = next(frame for frame in frames if frame["type"] == "learning")
    assert learning["queued_this_turn"] == 1
    chunks = [frame for frame in frames if frame["type"] == "chunk"]
    assert "".join(frame["text"] for frame in chunks) == "echo: explain the architecture"
    assert [frame["sequence"] for frame in chunks] == list(range(1, len(chunks) + 1))
    done = frames[-1]
    assert done["message_id"] == "web-00000001"
    assert done["metrics"] == {"received": 1, "completed": 1, "failed": 0}
    assert done["learning"]["pending"] == 1


def test_websocket_rejects_cross_origin_and_oversized_messages():
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(create_app(_runtime({}), TOKEN))
    client.post("/api/login", json={"token": TOKEN})
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws", headers={"Origin": "http://attacker.invalid"}):
            pass
    assert rejected.value.code == 1008

    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_json()["type"] == "hello"
        websocket.send_json({"message": "x" * 32_001})
        assert websocket.receive_json() == {"type": "error", "error": "message too large"}
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1009


def test_status_separates_messages_from_actual_learning():
    client = TestClient(create_app(_runtime({}), TOKEN))
    client.post("/api/login", json={"token": TOKEN})
    client.post("/api/chat", json={"message": "hello"})
    body = client.get("/api/status").json()
    assert body["messages"] == 1
    assert body["metrics"] == {"received": 1, "completed": 1, "failed": 0}
    assert body["learned"] == 0
    assert body["learning"]["available"] is False


def test_web_access_is_fail_closed_without_a_passphrase(monkeypatch):
    """Enabled in settings but no HAYAT_WEB_TOKEN must NOT start a server.

    Phone access without a passphrase would be an open door; it stays shut and
    says how to open it. Settings are controlled here so the test does not
    depend on -- or bind a port from -- the developer's own .env."""
    import app.config

    monkeypatch.setattr(app.config, "load_settings",
                        lambda: {"web": {"enabled": True, "host": "127.0.0.1", "port": 8770}})
    monkeypatch.delenv("HAYAT_WEB_TOKEN", raising=False)
    from app.web.server import start_web_if_enabled

    server, message = start_web_if_enabled(SimpleNamespace())
    assert server is None
    assert "passphrase" in message.lower()


def test_web_access_is_off_when_disabled(monkeypatch):
    import app.config

    monkeypatch.setattr(app.config, "load_settings", lambda: {"web": {"enabled": False}})
    from app.web.server import start_web_if_enabled

    server, message = start_web_if_enabled(SimpleNamespace())
    assert server is None and message == ""


def test_status_requires_auth_and_reports_availability():
    from app.web.server import SessionTracker

    tracker = SessionTracker()
    app = create_app(_runtime({}), TOKEN, tracker)
    anon = TestClient(app)
    assert anon.get("/api/status").status_code == 401

    client = TestClient(app)
    client.post("/api/login", json={"token": TOKEN})
    body = client.get("/api/status").json()
    assert body["ok"] is True and body["available"] is True
    assert body["users"] >= 1


def test_two_devices_are_counted_as_two_connected_users():
    from app.web.server import SessionTracker

    tracker = SessionTracker()
    app = create_app(_runtime({}), TOKEN, tracker)
    phone = TestClient(app)
    laptop = TestClient(app)
    for device in (phone, laptop):
        device.post("/api/login", json={"token": TOKEN})
        device.get("/api/status")  # each gets its own session id cookie
    assert tracker.active() == 2


def test_a_session_stops_counting_after_its_window():
    from app.web.server import SessionTracker

    tracker = SessionTracker(window=0.0)  # everything is immediately stale
    tracker.touch("sid-1")
    assert tracker.active() == 0


def test_web_server_exposes_the_active_user_count():
    from types import SimpleNamespace

    from app.web.server import WebServer

    server = WebServer(SimpleNamespace(), token="t")
    assert server.active_users() == 0
    server.sessions.touch("sid-x")
    assert server.active_users() == 1
