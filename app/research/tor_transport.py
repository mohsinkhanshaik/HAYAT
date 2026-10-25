"""Bound otherwise untimed HTTPcore SOCKS negotiation on our own transport."""
from __future__ import annotations

import time

import httpcore
import httpx


class _HandshakeStream:
    def __init__(self, stream, seconds: float):
        self.stream = stream
        self.deadline = time.monotonic() + seconds

    def _timeout(self, timeout):
        if timeout is not None:
            return timeout  # HTTP body/TLS operations already carry their own limit
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise httpcore.ReadTimeout("Tor SOCKS negotiation exceeded its connection budget")
        return remaining

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self._timeout(timeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self._timeout(timeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return self.stream.start_tls(ssl_context, server_hostname=server_hostname, timeout=self._timeout(timeout))

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def close(self):
        return self.stream.close()


class _HandshakeBackend:
    def __init__(self, backend, seconds):
        self.backend, self.seconds = backend, max(.001, float(seconds))

    def connect_tcp(self, *args, **kwargs):
        seconds = kwargs.get("timeout")
        seconds = min(self.seconds, seconds) if seconds is not None else self.seconds
        return _HandshakeStream(self.backend.connect_tcp(*args, **kwargs), max(.001, seconds))

    def connect_unix_socket(self, *args, **kwargs):
        return self.backend.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds):
        return self.backend.sleep(seconds)


def tor_transport(proxy: str, timeout: float) -> httpx.HTTPTransport:
    transport = httpx.HTTPTransport(proxy=proxy, trust_env=False)
    # HTTPX does not expose network_backend in HTTPTransport's constructor.
    # Keep this compatibility seam local to one pool; no global/vendor patch.
    pool = getattr(transport, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if not isinstance(pool, httpcore.SOCKSProxy) or backend is None:
        transport.close()
        raise RuntimeError("This HTTP dependency cannot enforce Tor handshake timeouts; masked reads are blocked.")
    pool._network_backend = _HandshakeBackend(backend, timeout)
    return transport
