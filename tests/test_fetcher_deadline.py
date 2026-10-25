"""A server that trickles bytes cannot hold a download for ever: the whole download has a ceiling."""
import time

import httpx
import pytest

import app.research.fetcher as module
from app.research.fetcher import PublicFetcher


def trickle(count, pause):
    for _index in range(count):
        time.sleep(pause)
        yield b"x"


@pytest.fixture
def fetcher(monkeypatch):
    monkeypatch.setattr(module, "validate_public_url", lambda url: url)
    value = PublicFetcher("HAYAT-Test/1.0", timeout=.5, delay=0, respect_robots=False)
    monkeypatch.setattr(value, "MIN_DOWNLOAD_SECONDS", 1.0)
    return value


def test_a_trickling_download_is_stopped_at_its_ceiling(fetcher):
    fetcher.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, content=trickle(100, .2))))
    started = time.perf_counter()
    with pytest.raises(httpx.ReadTimeout, match="did not finish"):
        fetcher.fetch("https://slow.example.com/feed")
    assert time.perf_counter() - started < 5  # not the 20 seconds the server would have taken


def test_an_ordinary_download_is_untouched(fetcher):
    fetcher.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, content=trickle(3, .05))))
    assert fetcher.fetch("https://fast.example.com/feed").content == b"xxx"
