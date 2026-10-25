from __future__ import annotations

import io
import json
import re
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from app.research.fetcher import FetchedResource
from app.research.injection import detect_prompt_injection
from app.research.models import SourceDocument
from app.research.ranking import classify_source
from app.research.sources import score_source


class ContentExtractor:
    HTML_VERSION = 3
    def extract(self, resource: FetchedResource, query: str = "") -> tuple[SourceDocument, int]:
        started = time.perf_counter()
        content_type = resource.headers.get("content-type", "application/octet-stream").split(";", 1)[0].lower()
        domain = (urlsplit(resource.final_url).hostname or "").lower()
        title = domain
        text = ""
        description = ""
        canonical: str | None = None
        published: str | None = None
        updated: str | None = None
        headings: list[str] = []
        links: list[dict[str, str]] = []
        tables: list[list[list[str]]] = []
        metadata: dict[str, Any] = {}
        author: str | None = None

        if content_type in {"text/html", "application/xhtml+xml"}:
            decoded = resource.content.decode(self._charset(resource) or "utf-8", errors="replace")
            soup = BeautifulSoup(decoded, "html.parser")
            title = self._clean(soup.title.get_text(" ", strip=True)) if soup.title else domain
            canonical_node = soup.find("link", rel=lambda value: value and "canonical" in value)
            if canonical_node and canonical_node.get("href"):
                canonical = urljoin(resource.final_url, str(canonical_node["href"]))
            description = self._meta(soup, "description") or self._property(soup, "og:description") or ""
            published = self._property(soup, "article:published_time") or self._meta(soup, "date") or self._json_ld_date(soup, "datePublished")
            updated = self._property(soup, "article:modified_time") or self._meta(soup, "last-modified") or self._json_ld_date(soup, "dateModified") or resource.headers.get("last-modified")
            author = self._meta(soup, "author") or self._json_ld_date(soup, "author")
            headings = [self._clean(node.get_text(" ", strip=True)) for node in soup.select("h1,h2,h3")[:100]]
            for anchor in soup.select("a[href]")[:1000]:
                href = urljoin(resource.final_url, str(anchor.get("href", "")))
                if urlsplit(href).scheme in {"http", "https"}:
                    links.append({"text": self._clean(anchor.get_text(" ", strip=True))[:300], "url": href})
            structured_tables: list[dict[str, Any]] = []
            for table_index, table in enumerate(soup.select("table")[:25], 1):
                rows: list[list[str]] = []
                for row in table.select("tr")[:200]:
                    cells = [self._clean(cell.get_text(" ", strip=True))[:1000] for cell in row.select("th,td")]
                    if cells:
                        rows.append(cells)
                if rows:
                    tables.append(rows)
                    caption_node = table.find("caption")
                    caption = self._clean(caption_node.get_text(" ", strip=True)) if caption_node else ""
                    header_cells = [self._clean(cell.get_text(" ", strip=True)) for cell in table.select("thead th")]
                    if not header_cells and table.select_one("tr"):
                        header_cells = [self._clean(cell.get_text(" ", strip=True)) for cell in table.select_one("tr").select("th")]
                    unit_matches = re.findall(r"\(([^)]*(?:%|USD|EUR|GBP|million|billion|thousand|ms|seconds?|bytes?|GB|MB)[^)]*)\)", " ".join(header_cells), re.I)
                    footnotes = [self._clean(node.get_text(" ", strip=True)) for node in table.select("tfoot, .footnote, [role=note]")[:20]]
                    structured_tables.append({"table": table_index, "caption": caption, "headers": header_cells, "rows": rows[1:] if header_cells and rows else rows, "units": unit_matches, "footnotes": footnotes})
            metadata["json_ld"] = self._json_ld(soup)
            metadata["embedded_json"] = self._embedded_json(soup)
            metadata["structured_tables"] = structured_tables
            for node in soup.select("script,style,noscript,template,svg,nav,footer,form"):
                node.decompose()
            main = soup.select_one("article,main,[role=main]") or soup.body or soup
            # Inline tags are not sentence boundaries. Separating every text
            # node by a newline split claims at links/emphasis, while source
            # line wrapping split them again. Preserve only block boundaries.
            from bs4 import Comment, NavigableString
            for comment in main.find_all(string=lambda value: isinstance(value, Comment)):
                comment.extract()
            for node in list(main.find_all(string=True)):
                node.replace_with(NavigableString(' '.join(str(node).split())))
            for node in main.find_all(['p', 'li', 'div', 'section', 'article', 'h1', 'h2', 'h3',
                                       'h4', 'h5', 'h6', 'tr', 'pre', 'br']):
                node.insert_before(NavigableString('\n'))
                node.insert_after(NavigableString('\n'))
            text = self._clean(main.get_text(' ', strip=False))[:500_000]
            metadata['html_extraction_version'] = self.HTML_VERSION
        elif content_type == "application/pdf" or resource.content.startswith(b"%PDF"):
            reader = PdfReader(io.BytesIO(resource.content))
            pdf_metadata = {str(key): str(value) for key, value in (reader.metadata or {}).items()}
            metadata = {"pdf": pdf_metadata, "pages": len(reader.pages), "pdf_pages": []}
            title = str((reader.metadata or {}).get("/Title") or domain)
            author = str((reader.metadata or {}).get("/Author") or "") or None
            page_texts: list[str] = []
            for page_number, page in enumerate(reader.pages[:100], 1):
                page_text = self._clean(page.extract_text() or "")
                heading = next((line for line in page_text.splitlines()[:8] if 4 <= len(line) <= 160), "")
                metadata["pdf_pages"].append({"page": page_number, "heading": heading, "text": page_text[:20_000]})
                page_texts.append(f"[Page {page_number}]\n{page_text}")
            text = "\n\n".join(page_texts)[:500_000]
        elif "json" in content_type:
            payload = json.loads(resource.content)
            text = json.dumps(payload, ensure_ascii=False, indent=2)[:500_000]
            metadata["json_type"] = type(payload).__name__
        elif content_type.startswith("text/"):
            text = resource.content.decode(self._charset(resource) or "utf-8", errors="replace")[:500_000]
        else:
            raise ValueError(f"Content type cannot be safely extracted: {content_type}")

        quality, reasons = score_source(resource.final_url, title, query)
        source_type, primary = classify_source(resource.final_url, title, query)
        metadata["prompt_injection_indicators"] = detect_prompt_injection(text)
        document = SourceDocument(
            resource.requested_url, resource.final_url, title, domain, text, resource.retrieved_at,
            resource.status_code, content_type, resource.sha256, len(resource.content), published,
            updated, canonical, description, headings, links, tables, metadata, quality, reasons,
            "untrusted_external_data", source_type, author, primary,
        )
        return document, int((time.perf_counter() - started) * 1000)

    @staticmethod
    def _clean(value: str) -> str:
        lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _charset(resource: FetchedResource) -> str | None:
        match = re.search(r"charset=([\w-]+)", resource.headers.get("content-type", ""), re.I)
        return match.group(1) if match else None

    @staticmethod
    def _meta(soup: BeautifulSoup, name: str) -> str | None:
        node = soup.find("meta", attrs={"name": re.compile(f"^{re.escape(name)}$", re.I)})
        return str(node.get("content")) if node and node.get("content") else None

    @staticmethod
    def _property(soup: BeautifulSoup, name: str) -> str | None:
        node = soup.find("meta", attrs={"property": re.compile(f"^{re.escape(name)}$", re.I)})
        return str(node.get("content")) if node and node.get("content") else None

    @staticmethod
    def _json_ld(soup: BeautifulSoup) -> list[Any]:
        values: list[Any] = []
        for node in soup.select('script[type="application/ld+json"]')[:20]:
            try:
                value = json.loads(node.string or node.get_text())
                values.append(value)
            except (json.JSONDecodeError, TypeError):
                continue
        return values

    @staticmethod
    def _embedded_json(soup: BeautifulSoup) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for node in soup.select('script[type="application/json"], script#__NEXT_DATA__')[:20]:
            try:
                payload = json.loads(node.string or node.get_text())
            except (json.JSONDecodeError, TypeError):
                continue
            serialized = json.dumps(payload, ensure_ascii=False)
            values.append({"id": node.get("id"), "bytes": len(serialized.encode("utf-8")), "data": payload if len(serialized) <= 100_000 else None})
        return values

    def _json_ld_date(self, soup: BeautifulSoup, key: str) -> str | None:
        def walk(value: Any) -> str | None:
            if isinstance(value, dict):
                if value.get(key):
                    return str(value[key])
                return next((found for item in value.values() if (found := walk(item))), None)
            if isinstance(value, list):
                return next((found for item in value if (found := walk(item))), None)
            return None
        return walk(self._json_ld(soup))
