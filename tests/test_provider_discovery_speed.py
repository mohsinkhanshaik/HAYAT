"""Provider discovery was the slowest thing in the system.

Measured on the real machine: probing a configured-but-stopped LM Studio cost
4.17 seconds against 135ms for the Ollama that was actually running, the
probes ran serially, and `doctor` asked for the result twice -- 8.6 of its 11
seconds went on one unreachable local port. These tests hold the three fixes
in place: bounded connect for local endpoints, concurrent probing, and a short
cache so asking twice costs once.
"""
from __future__ import annotations

import time

import httpx
import pytest

from app.models.local import discovery_timeout
from app.models.router import ModelRouter


# ------------------------------------------------------------- probe budget


def test_a_local_endpoint_gets_a_short_connect_budget():
    """A local port either has something listening or it does not, and the OS
    answers immediately either way."""
    timeout = discovery_timeout("http://localhost:1234/v1")
    assert timeout.connect <= 0.5, "a refused local probe must not cost seconds"


def test_a_remote_endpoint_keeps_a_realistic_budget():
    timeout = discovery_timeout("https://api.example.com/v1")
    assert timeout.connect >= 1.0, "a real network needs more room than a loopback"


def test_a_reachable_provider_still_gets_time_to_answer():
    """Bounding the connect must not truncate a large model list."""
    assert discovery_timeout("http://localhost:11434").read >= 5.0


# ------------------------------------------------------------------ caching


class SlowProvider:
    """A provider that records how often it was actually probed."""

    def __init__(self, name: str, delay: float = 0.0, base_url: str = "http://localhost:9/v1") -> None:
        self.name = name
        self.base_url = base_url
        self.api_key = ""
        self.delay = delay
        self.probes = 0

    def list_models(self) -> list[str]:
        self.probes += 1
        if self.delay:
            time.sleep(self.delay)
        return [f"{self.name}-model"]


def _router_with(providers: dict) -> ModelRouter:
    router = ModelRouter()
    router.providers = providers
    router._availability = None
    router._availability_expires = 0.0
    return router


def test_asking_twice_in_one_run_probes_once():
    """`doctor` called this twice and paid the full cost both times."""
    provider = SlowProvider("local")
    router = _router_with({"ollama": provider})

    first = router.available()
    second = router.available()

    assert first == second
    assert provider.probes == 1, "the second call must be served from the cache"


def test_refresh_forces_a_real_probe():
    provider = SlowProvider("local")
    router = _router_with({"ollama": provider})
    router.available()
    router.available(refresh=True)
    assert provider.probes == 2


def test_the_cache_expires_so_a_started_service_is_noticed():
    """Availability changes when a service starts; the cache must not hide it."""
    provider = SlowProvider("local")
    router = _router_with({"ollama": provider})
    router.AVAILABILITY_TTL_SECONDS = 0.01
    router.available()
    time.sleep(0.05)
    router.available()
    assert provider.probes == 2


# -------------------------------------------------------------- concurrency


def test_providers_are_probed_concurrently_not_one_after_another():
    """The property that stops one dead port from dominating the total.

    Four providers, each sleeping 200ms. Serial would be ~800ms; concurrent is
    ~200ms. The threshold sits between them with room for scheduling noise.
    """
    providers = {name: SlowProvider(name, delay=0.2) for name in ("ollama", "lm_studio", "a", "b")}
    router = _router_with(providers)

    started = time.perf_counter()
    result = router.available()
    elapsed = time.perf_counter() - started

    assert len(result) == 4
    assert elapsed < 0.5, f"probing looks serial: {elapsed:.2f}s for 4 x 200ms providers"


def test_one_unreachable_provider_does_not_hide_the_others():
    """The real shape of this machine: Ollama up, LM Studio down."""

    class Dead(SlowProvider):
        def list_models(self):
            self.probes += 1
            raise httpx.ConnectError("refused")

    router = _router_with({"ollama": SlowProvider("ollama"), "lm_studio": Dead("lm_studio")})
    result = router.available()

    assert result["ollama"] == ["ollama-model"]
    assert result["lm_studio"] == [], "unreachable is an answer, not a failure"


# --------------------------------------------------------- the real machine


@pytest.mark.parametrize("_", [0])
def test_real_discovery_is_not_pathologically_slow(_):
    """An end-to-end guard against the regression, using whatever is really
    configured on this machine."""
    router = ModelRouter()
    started = time.perf_counter()
    router.available(refresh=True)
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0, f"provider discovery took {elapsed:.2f}s; it was 4.3s before this was fixed"
