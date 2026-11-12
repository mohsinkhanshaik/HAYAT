from app.research.extractor import ContentExtractor
from tests.test_research import resource


def test_main_article_wins_over_teaser_hidden_content_and_navigation():
    html = b'''<html><title>Evidence</title><body><nav><h1>Menu</h1><a href="/ads">Ad</a></nav>
    <article>Unrelated teaser</article><main><h1>Measurements</h1><p>Observed value is 12.</p>
    <p hidden>hidden fabrication</p><div aria-hidden="true">More hidden text</div>
    <div style="color:red; display: none !important">Not visible</div>
    <a href="/paper#one">Original study</a><a href="/paper#two">Duplicate fragment</a></main></body></html>'''
    result, _ = ContentExtractor().extract(resource(content=html))
    assert "Observed value is 12" in result.text
    assert not any(word in result.text for word in ("hidden", "teaser", "Not visible", "Menu"))
    assert result.headings == ["Measurements"]
    assert len(result.links) == 1 and result.links[0]["url"].endswith("/paper")


def test_relative_base_links_and_original_code_indentation_are_retained():
    html = b'''<head><base href="https://docs.example.com/manual/"></head><main>
    <a href="next">Next</a><a href="https://user:password@example.com/">Credentials</a>
    <pre>def measure():\n    return 12\n</pre></main>'''
    result, _ = ContentExtractor().extract(resource(content=html))
    assert result.links == [{"text": "Next", "url": "https://docs.example.com/manual/next"}]
    assert "\n    return 12" in result.metadata["code_blocks"][0]


def test_bad_declared_charset_does_not_break_extraction():
    value = resource(content="<main>café measurements</main>".encode())
    value.headers["content-type"] = 'text/html; charset="nonexistent-charset"'
    assert "café" in ContentExtractor().extract(value)[0].text


def test_html_meta_charset_is_used_when_http_header_has_none():
    value = resource(content='<meta charset="windows-1252"><main>café measurements</main>'.encode("cp1252"))
    value.headers["content-type"] = "text/html"
    assert "café" in ContentExtractor().extract(value)[0].text
