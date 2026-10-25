"""Local certificate authority for HAYAT's mutually authenticated web mode.

The authority never leaves this machine.  Its private key and the HTTPS
server key are encrypted with a random passphrase held by ``SecretBroker``
(Windows Credential Manager in production).  A paired device receives one
password-protected PKCS#12 bundle; HAYAT retains only public metadata.
"""
from __future__ import annotations

import ipaddress
import json
import os
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.config import PROJECT_ROOT


_SECRET_NAME = "remote_access_pki_key"
_SECRET_SCOPE = "remote_tls"


@dataclass(frozen=True, slots=True)
class TLSMaterial:
    certfile: Path
    keyfile: Path
    key_password: str
    trustfile: Path


class LocalPKI:
    """Create, pair, revoke and report local TLS identities."""

    def __init__(self, root: Path | None = None, *, secret_broker: Any = None,
                 passphrase: str | None = None) -> None:
        self.root = root or PROJECT_ROOT / "data/security/remote_access"
        self.root.mkdir(parents=True, exist_ok=True)
        self.secret_broker = secret_broker
        self._explicit_passphrase = passphrase
        if not passphrase and secret_broker is None:
            raise ValueError("LocalPKI requires SecretBroker or an injected test passphrase.")
        self.ca_cert_path = self.root / "hayat-ca.crt"
        self.ca_key_path = self.root / "hayat-ca.key"
        self.server_cert_path = self.root / "hayat-server.crt"
        self.server_key_path = self.root / "hayat-server.key"
        self.trust_path = self.root / "client-trust.pem"
        self.crl_path = self.root / "hayat-client.crl"
        self.devices_path = self.root / "devices.json"
        self.bundles = self.root / "paired-devices"
        self.bundles.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def ensure(self, hosts: Iterable[str] = ()) -> TLSMaterial:
        with self._lock:
            password = self._passphrase()
            if not (self.ca_cert_path.is_file() and self.ca_key_path.is_file()):
                self._create_authority(password)
            requested = self._san_values(hosts)
            if not self._server_is_valid_for(requested):
                self._create_server(password, requested)
            self._write_crl(password)
            return TLSMaterial(self.server_cert_path, self.server_key_path,
                               password, self.trust_path)

    def pair_device(self, name: str) -> dict[str, Any]:
        label = " ".join(str(name or "").split()).strip()
        if not label or len(label) > 80:
            raise ValueError("Device name must be between 1 and 80 characters.")
        with self._lock:
            password = self._passphrase()
            self.ensure(("localhost",))
            ca_cert, ca_key = self._load_authority(password)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = datetime.now(UTC)
            serial = x509.random_serial_number()
            certificate = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)]))
                .issuer_name(ca_cert.subject)
                .public_key(key.public_key())
                .serial_number(serial)
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=825))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=True,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=None, decipher_only=None,
                ), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256())
            )
            bundle_password = secrets.token_urlsafe(18)
            device_id = f"device-{serial:x}"[-32:]
            target = self.bundles / f"{device_id}.p12"
            payload = pkcs12.serialize_key_and_certificates(
                label.encode("utf-8", "replace"), key, certificate, [ca_cert],
                serialization.BestAvailableEncryption(bundle_password.encode("utf-8")),
            )
            self._atomic_bytes(target, payload, private=True)
            devices = self._devices()
            devices.append({
                "id": device_id, "name": label, "serial": str(serial),
                "fingerprint": certificate.fingerprint(hashes.SHA256()).hex(),
                "created_at": now.isoformat(), "expires_at": certificate.not_valid_after_utc.isoformat(),
                "revoked_at": None,
            })
            self._write_devices(devices)
            return {
                "id": device_id, "name": label, "bundle": str(target),
                "password": bundle_password,
                "fingerprint": devices[-1]["fingerprint"],
                "server_ca": str(self.ca_cert_path), "password_display_once": True,
            }

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = self._devices()
            changed = False
            for device in devices:
                if device.get("id") == device_id and not device.get("revoked_at"):
                    device["revoked_at"] = datetime.now(UTC).isoformat()
                    changed = True
            if not changed:
                return False
            self._write_devices(devices)
            self._write_crl(self._passphrase())
            return True

    def status(self) -> dict[str, Any]:
        devices = self._devices()
        public = [{key: value for key, value in item.items() if key != "serial"}
                  for item in devices]
        active = sum(not bool(item.get("revoked_at")) for item in devices)
        return {
            "ready": self.ca_cert_path.is_file() and self.server_cert_path.is_file(),
            "devices": public, "active_devices": active,
            "revoked_devices": len(devices) - active,
            "private_keys_exposed": False,
            "ca_certificate": str(self.ca_cert_path),
        }

    def _passphrase(self) -> str:
        if self._explicit_passphrase:
            return self._explicit_passphrase
        existing = {item.get("name") for item in self.secret_broker.list()}
        if _SECRET_NAME not in existing:
            self.secret_broker.store(
                _SECRET_NAME, secrets.token_urlsafe(36), scopes=[_SECRET_SCOPE],
            )
        lease = self.secret_broker.issue_lease(_SECRET_NAME, _SECRET_SCOPE, ttl_seconds=15)
        return self.secret_broker.redeem(lease, _SECRET_SCOPE)

    def _create_authority(self, password: str) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        now = datetime.now(UTC)
        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "HAYAT Local Security"),
            x509.NameAttribute(NameOID.COMMON_NAME, "HAYAT Local Device CA"),
        ])
        cert = (
            x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=None, decipher_only=None,
            ), critical=True)
            .sign(key, hashes.SHA256())
        )
        self._atomic_bytes(self.ca_cert_path, cert.public_bytes(serialization.Encoding.PEM))
        self._atomic_bytes(
            self.ca_key_path,
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.BestAvailableEncryption(password.encode("utf-8"))),
            private=True,
        )

    def _create_server(self, password: str, values: tuple[str, ...]) -> None:
        ca_cert, ca_key = self._load_authority(password)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        names: list[x509.GeneralName] = []
        for value in values:
            try:
                names.append(x509.IPAddress(ipaddress.ip_address(value)))
            except ValueError:
                names.append(x509.DNSName(value))
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HAYAT Remote Access")]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        self._atomic_bytes(self.server_cert_path, cert.public_bytes(serialization.Encoding.PEM))
        self._atomic_bytes(
            self.server_key_path,
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.BestAvailableEncryption(password.encode("utf-8"))),
            private=True,
        )

    def _write_crl(self, password: str) -> None:
        ca_cert, ca_key = self._load_authority(password)
        now = datetime.now(UTC)
        builder = x509.CertificateRevocationListBuilder().issuer_name(ca_cert.subject)
        builder = builder.last_update(now - timedelta(minutes=1)).next_update(now + timedelta(days=7))
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False,
        )
        for device in self._devices():
            if device.get("revoked_at"):
                when = datetime.fromisoformat(str(device["revoked_at"])).astimezone(UTC)
                revoked = x509.RevokedCertificateBuilder().serial_number(
                    int(str(device["serial"])),
                ).revocation_date(when).build()
                builder = builder.add_revoked_certificate(revoked)
        crl = builder.sign(ca_key, hashes.SHA256())
        crl_bytes = crl.public_bytes(serialization.Encoding.PEM)
        self._atomic_bytes(self.crl_path, crl_bytes)
        self._atomic_bytes(self.trust_path, self.ca_cert_path.read_bytes() + crl_bytes)

    def _load_authority(self, password: str) -> tuple[x509.Certificate, Any]:
        cert = x509.load_pem_x509_certificate(self.ca_cert_path.read_bytes())
        key = serialization.load_pem_private_key(
            self.ca_key_path.read_bytes(), password=password.encode("utf-8"),
        )
        return cert, key

    def _server_is_valid_for(self, requested: tuple[str, ...]) -> bool:
        if not (self.server_cert_path.is_file() and self.server_key_path.is_file()):
            return False
        try:
            cert = x509.load_pem_x509_certificate(self.server_cert_path.read_bytes())
            if cert.not_valid_after_utc <= datetime.now(UTC) + timedelta(days=30):
                return False
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            values = {str(item.value) for item in san}
            return set(requested) <= values
        except Exception:
            return False

    @staticmethod
    def _san_values(hosts: Iterable[str]) -> tuple[str, ...]:
        values = {"localhost", "127.0.0.1", "hayat.local"}
        for host in hosts:
            clean = str(host or "").strip().strip("[]")
            if clean:
                values.add(clean)
        return tuple(sorted(values))

    def _devices(self) -> list[dict[str, Any]]:
        if not self.devices_path.is_file():
            return []
        try:
            value = json.loads(self.devices_path.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _write_devices(self, devices: list[dict[str, Any]]) -> None:
        self._atomic_bytes(
            self.devices_path,
            json.dumps(devices, indent=2, sort_keys=True).encode("utf-8"),
            private=True,
        )

    @staticmethod
    def _atomic_bytes(path: Path, value: bytes, *, private: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(path.suffix + ".tmp")
        partial.write_bytes(value)
        if private:
            try:
                os.chmod(partial, 0o600)
            except OSError:
                pass
        partial.replace(path)
