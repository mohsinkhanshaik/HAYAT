from __future__ import annotations

import ipaddress
import base64
import hashlib
import re
import socket
from urllib.parse import urlsplit, urlunsplit


class UnsafeUrlError(ValueError):
    pass


#: File extensions that are not internet domain endings (".py" and ".md" are countries, so they are not here).
FILE_ENDINGS = {"yaml", "yml", "txt", "json", "csv", "tsv", "pdf", "docx", "doc", "xlsx", "xls", "pptx", "log", "ini", "cfg", "toml",
                "jpg", "jpeg", "png", "gif", "bmp", "webp", "mp4", "mov", "mkv", "wav", "mp3", "exe", "bat", "ps1", "zip", "rar",
                "html", "htm", "xml", "jsonl", "db", "sqlite", "parquet", "ipynb", "gguf", "safetensors"}


def validate_public_url(url: str, *, via_tor: bool = False) -> str:
    """Reject local files, credentials, and hosts resolving to non-public networks."""
    value = url.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise UnsafeUrlError("Only public HTTP and HTTPS URLs are permitted.")
    if parts.username or parts.password:
        raise UnsafeUrlError("URLs containing credentials are not permitted.")
    host = parts.hostname.rstrip(".").lower()
    if any(ord(char) < 33 for char in value) or "\\" in parts.netloc or "%" in host:
        raise UnsafeUrlError("Ambiguous URL host or control characters are not permitted.")
    port = parts.port  # reject invalid ports before any DNS or connection
    if host.endswith(".onion"):
        if not via_tor:
            raise UnsafeUrlError("Onion services require HAYAT's Tor route; direct lookup is refused.")
        service = host.split(".")[-2]
        if not re.fullmatch(r"[a-z2-7]{56}", service):
            raise UnsafeUrlError("A valid v3 onion service address is required.")
        decoded = base64.b32decode(service.upper())
        checksum = hashlib.sha3_256(b".onion checksum" + decoded[:32] + decoded[-1:]).digest()[:2]
        if decoded[-1] != 3 or decoded[32:34] != checksum:
            raise UnsafeUrlError("The onion service checksum or version is invalid.")
    # "settings.yaml" was once tried as a website four times: a name ending in a file extension that is no
    # internet domain ending is a file on this computer.
    if host.rsplit(".", 1)[-1] in FILE_ENDINGS and "/" not in value.split("://", 1)[-1].rstrip("/"):
        raise UnsafeUrlError(f"{host} looks like a file name, not a web address: read a file on this computer with read_file.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
        raise UnsafeUrlError("Local network hosts are not permitted for public research.")
    if via_tor:
        # SOCKS passes hostnames to Tor. A local DNS preflight would disclose
        # the destination and cannot resolve onion services in the first place.
        # Tor's ClientRejectInternalAddresses policy guards remote resolutions.
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.encode("idna").decode("ascii").split(".")
            if (len(labels) < 2 or len(host) > 253 or
                    any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels) or
                    all(label.isdigit() for label in labels) or host.startswith("0x")):
                raise UnsafeUrlError("A public hostname is required for Tor retrieval.") from None
        else:
            if not address.is_global:
                raise UnsafeUrlError("Non-public network addresses are not permitted through Tor.")
        return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))
    try:
        addresses = {item[4][0].split("%", 1)[0] for item in socket.getaddrinfo(host, port or (443 if parts.scheme == "https" else 80))}
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Host could not be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeUrlError(f"Non-public network address is not permitted: {address}")
    return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))
