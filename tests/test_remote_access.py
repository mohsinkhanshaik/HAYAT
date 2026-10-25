from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import socket
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from app.web.pki import LocalPKI, TLSMaterial
from app.integrations.secrets import SecretBroker


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def set_password(self, service, username, password):
        self.values[(service, username)] = password

    def get_password(self, service, username):
        return self.values.get((service, username))

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def test_local_pki_encrypts_keys_pairs_and_revokes_a_device(tmp_path: Path):
    pki = LocalPKI(tmp_path, passphrase="test-only-strong-passphrase")
    material = pki.ensure(("192.168.12.159", "remote.example.test"))

    assert material.certfile.is_file() and material.trustfile.is_file()
    with pytest.raises((TypeError, ValueError)):
        serialization.load_pem_private_key(material.keyfile.read_bytes(), password=None)

    paired = pki.pair_device("Mohsin phone")
    key, cert, chain = pkcs12.load_key_and_certificates(
        Path(paired["bundle"]).read_bytes(), paired["password"].encode(),
    )
    assert key is not None and cert is not None and chain
    assert pki.status()["active_devices"] == 1

    assert pki.revoke(paired["id"]) is True
    assert pki.revoke(paired["id"]) is False
    crl = x509.load_pem_x509_crl(pki.crl_path.read_bytes())
    assert len(list(crl)) == 1
    assert pki.status()["revoked_devices"] == 1


def test_anywhere_mode_is_fail_closed_and_never_changes_network_state(monkeypatch, tmp_path: Path):
    import app.web.remote_access as remote

    started = []

    class FakeServer:
        def __init__(self, runtime, **kwargs):
            self.kwargs = kwargs
            self.sessions = SimpleNamespace(clear=lambda: None)
            started.append(self)

        def start(self):
            return None

        def stop(self):
            return None

        def active_users(self):
            return 0

        def messages_handled(self):
            return 0

        def url(self):
            return "https://192.168.12.159:8770/" if self.kwargs.get("tls") else "http://192.168.12.159:8770/"

    class FakePKI:
        active = 0

        def status(self):
            return {"ready": True, "active_devices": self.active,
                    "revoked_devices": 0, "devices": []}

        def ensure(self, hosts):
            return TLSMaterial(tmp_path / "s.crt", tmp_path / "s.key", "pw", tmp_path / "trust")

    monkeypatch.setattr(remote, "WebServer", FakeServer)
    monkeypatch.setattr(remote, "_resolve_bind_host", lambda host: "192.168.12.159")
    monkeypatch.setenv("HAYAT_WEB_TOKEN", "test-token")
    pki = FakePKI()
    manager = remote.RemoteAccessManager(SimpleNamespace(), {"web": {
        "enabled": True, "host": "lan", "port": 8770,
    }}, pki=pki)

    with pytest.raises(RuntimeError, match="Pair at least one"):
        manager.set_mode("anywhere")
    assert manager.status()["mode"] == "off"

    pki.active = 1
    status = manager.set_mode("anywhere")
    assert status["mutual_tls"] is True and status["minimum_tls"] == "1.3"
    assert started[-1].kwargs["allow_remote"] is True
    assert status["external_route_configured"] is False
    assert "firewall" in status["route_note"].lower()


def test_same_wifi_generates_a_stable_protected_login_without_env_setup(monkeypatch, tmp_path: Path):
    import app.web.remote_access as remote

    class FakeServer:
        def __init__(self, runtime, **kwargs):
            self.token = kwargs["token"]
            self.sessions = SimpleNamespace(clear=lambda: None)
        def start(self): pass
        def stop(self): pass
        def active_users(self): return 0
        def messages_handled(self): return 0
        def url(self): return "http://192.168.1.20:8770/"

    monkeypatch.setattr(remote, "WebServer", FakeServer)
    monkeypatch.delenv("HAYAT_WEB_TOKEN", raising=False)
    broker = SecretBroker(tmp_path / "secrets.db", backend=MemorySecrets())
    runtime = SimpleNamespace(secret_broker=broker)
    manager = remote.RemoteAccessManager(runtime, {"web": {"host": "lan", "port": 8770}})
    status = manager.set_mode("same_wifi")
    credentials = manager.owner_access_credentials()
    assert status["serving"] and status["manual_token_setup_required"] is False
    assert credentials["url"] == status["url"]
    assert len(credentials["passphrase"]) >= 32
    assert credentials["passphrase"] not in str(status)
    assert broker.list()[0]["name"] == "remote-access-passphrase"


def test_network_readiness_is_local_evidence_and_does_not_claim_outside_verification(monkeypatch):
    import app.web.remote_access as remote

    monkeypatch.setattr(remote.socket, "getaddrinfo", lambda *args: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 0)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::20", 0, 0, 0)),
    ])
    manager = remote.RemoteAccessManager(SimpleNamespace(), {"web": {
        "host": "lan", "port": 8770, "public_endpoint": "hayat.example.test:8770",
    }})
    status = manager.status()
    network = status["network_readiness"]
    assert network["endpoint_configured"] is True
    assert network["outside_reachability_verified"] is False
    assert status["external_route_configured"] is True
    assert network["private_ipv4"] == ["192.168.1.20"]
    assert "public-IP lookup" in network["privacy"]


def test_web_server_refuses_remote_clients_without_mutual_tls():
    from app.web.server import LocalNetworkPolicy, WebServer

    with pytest.raises(ValueError, match="mutual TLS"):
        WebServer(SimpleNamespace(), host="127.0.0.1", token="x", allow_remote=True)
    policy = LocalNetworkPolicy("127.0.0.1", allow_remote=True)
    assert policy.allows("8.8.8.8")
    assert not policy.allows("0.0.0.0")


def test_real_tls13_socket_requires_a_live_non_revoked_device_certificate(tmp_path: Path):
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    from app.web.server import WebServer

    pki = LocalPKI(tmp_path, passphrase="test-only-strong-passphrase")
    material = pki.ensure(("127.0.0.1",))
    paired = pki.pair_device("Socket test phone")
    key, cert, _chain = pkcs12.load_key_and_certificates(
        Path(paired["bundle"]).read_bytes(), paired["password"].encode(),
    )
    client_cert, client_key = tmp_path / "client.crt", tmp_path / "client.key"
    client_cert.write_bytes(cert.public_bytes(Encoding.PEM))
    client_key.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    context = ssl.create_default_context(cafile=str(pki.ca_cert_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(client_cert, client_key)
    server = WebServer(SimpleNamespace(), host="127.0.0.1", port=port,
                       token="socket-test", tls=material, allow_remote=True)
    server.start()
    try:
        response = httpx.get(f"https://127.0.0.1:{port}/health", verify=context, timeout=5)
        assert response.status_code == 200
    finally:
        server.stop()

    assert pki.revoke(paired["id"])
    material = pki.ensure(("127.0.0.1",))
    revoked_server = WebServer(SimpleNamespace(), host="127.0.0.1", port=port,
                               token="socket-test", tls=material, allow_remote=True)
    revoked_server.start()
    try:
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"https://127.0.0.1:{port}/health", verify=context, timeout=5)
    finally:
        revoked_server.stop()
