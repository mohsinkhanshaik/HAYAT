from types import SimpleNamespace
import hashlib

import pytest

from app.research.extractor import ContentExtractor
from app.research.fetcher import FetchedResource, PublicFetcher
from app.research.osint import profile_source, toolkit_status


def test_profile_retains_provenance_and_reads_only_vetted_bytes():
    html = b'''<html><head><title>Source review</title><meta name="author" content="Example Researcher">
    <meta property="article:published_time" content="2026-09-29"></head><body>
    <nav>Navigation boilerplate</nav><article><h1>Source review</h1>
    <p>This is a public research article with enough substantive text for extraction. Its observations describe a repeatable process and present evidence that should be checked against independent sources.</p>
    <p>The experiment compares three methods and describes the limitations of each method, including missing observations and possible sources of uncertainty in the measurements.</p>
    <a href="https://docs.python.org/3/">Reference documentation</a></article></body></html>'''
    calls = []
    resource = FetchedResource("https://example.org/start", "https://example.org/article", 200,
                               {"content-type": "text/html"}, html, 1, "2026-09-29T12:00:00Z", hashlib.sha256(html).hexdigest())
    def fetch(url, **kw):
        calls.append((url, kw))
        return resource
    result = profile_source(SimpleNamespace(fetcher=SimpleNamespace(fetch=fetch), extractor=ContentExtractor()), resource.requested_url)
    assert calls == [(resource.requested_url, {"max_bytes": 2097152})]
    assert result["sha256"] == resource.sha256
    assert result["url"] == resource.final_url
    assert result["published"] == "2026-09-29"
    assert result["author"] == "Example Researcher"
    assert result["linked_domains"] == {"docs.python.org": 1}
    assert result["trust"] == "untrusted_external_data"
    assert result["extractor"] == "Trafilatura"
    assert "repeatable process" in result["text"]
    assert result["limitations"] == [] and not result["text_truncated"]


def test_osint_cannot_fetch_local_network_or_file_urls():
    fetcher = PublicFetcher("HAYAT-test", respect_robots=False)
    try:
        research = SimpleNamespace(fetcher=fetcher, extractor=ContentExtractor())
        for url in ("file:///C:/private.txt", "http://localhost:8770/", "http://127.0.0.1/"):
            with pytest.raises(ValueError):
                profile_source(research, url)
    finally:
        fetcher.close()
    assert toolkit_status()["version"] != "unavailable"


def test_source_report_exposes_uncertainty_and_retains_provenance():
    from app.research.osint import format_profile
    text = format_profile({"title": "Sample", "url": "https://example.org/", "sha256": "abc123",
                           "text": "An extracted observation.", "limitations": ["Publication date is unknown"],
                           "prompt_injection_indicators": ["ignore instructions"], "linked_domains": {"example.net": 2}})
    assert "Publication date is unknown" in text
    assert "possible instructions" in text
    assert text.index("An extracted observation") < text.index("abc123")
    assert "example.net — 2 links" in text
