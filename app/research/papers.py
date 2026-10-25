from __future__ import annotations

import re
from typing import Any

from app.research.models import SourceDocument


SECTION_PATTERNS = {
    "abstract": r"(?is)\babstract\b\s*(.*?)(?=\n\s*(?:1\.?\s+)?(?:introduction|background)\b)",
    "method": r"(?is)\b(?:methods?|methodology|experimental setup)\b\s*(.*?)(?=\n\s*(?:results?|findings?|experiments?)\b)",
    "results": r"(?is)\b(?:results?|findings?)\b\s*(.*?)(?=\n\s*(?:discussion|limitations?|conclusion)\b)",
    "limitations": r"(?is)\b(?:limitations?|threats to validity)\b\s*(.*?)(?=\n\s*(?:conclusion|references?)\b)",
    "conclusion": r"(?is)\b(?:conclusions?|discussion)\b\s*(.*?)(?=\n\s*(?:references?|acknowledg)\b|\Z)",
}

NUMBERED_SECTION_PATTERNS = {
    "method": ["model architecture", "approach", "methods", "methodology", "experimental setup"],
    "results": ["results", "experiments", "evaluation"],
    "limitations": ["limitations", "threats to validity"],
    "conclusion": ["conclusion", "conclusions", "discussion"],
}


class PaperAnalyzer:
    def analyze(self, document: SourceDocument) -> dict[str, Any]:
        if document.content_type != "application/pdf" and document.source_type != "original_research":
            raise ValueError("Paper analysis requires an extracted research paper or PDF.")
        sections = {name: self._section(document.text, pattern) for name, pattern in SECTION_PATTERNS.items()}
        for name, headings in NUMBERED_SECTION_PATTERNS.items():
            sections[name] = self._numbered_section(document.text, headings) or sections[name]
        sample = self._first_match(document.text, [r"(?i)\b(?:n|sample size)\s*[=:]\s*([\d,]+)", r"(?i)\b([\d,]+)\s+(?:participants?|subjects?|samples?)\b"])
        datasets = sorted(set(re.findall(r"(?i)\b(?:dataset|corpus)\s+(?:called\s+)?([A-Z][A-Za-z0-9_.-]{2,})", document.text)))[:20]
        conflicts = self._first_match(document.text, [r"(?is)\bconflicts? of interest\b\s*[:\n]\s*(.{0,800})", r"(?is)\bcompeting interests?\b\s*[:\n]\s*(.{0,800})"])
        pages = document.metadata.get("pdf_pages", [])
        title, authors = self._front_matter(document)
        return {
            "title": title,
            "authors": authors,
            "publication_date": document.published_at,
            "source_url": document.final_url,
            "abstract": sections["abstract"],
            "research_question": self._research_question(sections["abstract"] or document.text[:4000]),
            "method": sections["method"],
            "datasets": datasets,
            "sample_size": sample,
            "results": sections["results"],
            "limitations": sections["limitations"],
            "conflicts_of_interest": conflicts,
            "authors_conclusion": sections["conclusion"],
            "pages": len(pages) or document.metadata.get("pages"),
            "provenance": "Extracted from the retrieved paper; missing fields are not inferred.",
            "trust": "untrusted_external_data",
        }

    @staticmethod
    def _section(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:5000] if match else None

    @staticmethod
    def _numbered_section(text: str, headings: list[str]) -> str | None:
        names = "|".join(re.escape(name) for name in headings)
        horizontal = r"[^\S\r\n]"
        pattern = rf"^{horizontal}*(?:\d+(?:\.\d+)*\.?{horizontal}+)?(?:{names}){horizontal}*\r?\n(.*?)(?=^{horizontal}*\d+(?:\.\d+)*\.?{horizontal}+[A-Z][^\n]{{1,120}}$|\Z)"
        match = re.search(pattern, text, re.I | re.M | re.S)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:5000] if match else None

    @staticmethod
    def _front_matter(document: SourceDocument) -> tuple[str, str | None]:
        if document.title and document.title.lower() not in {document.domain.lower(), "arxiv.org"}:
            return document.title, document.author
        first_page = document.text.split("Abstract", 1)[0]
        lines = [line.strip() for line in first_page.splitlines() if line.strip() and not line.startswith("[Page")]
        email_index = next((index for index, line in enumerate(lines) if "@" in line), len(lines))
        candidates = [line for line in lines[:email_index] if 3 <= len(line) <= 180 and not re.search(r"(?i)attribution|permission|journalistic|scholarly|google brain|google research|university|equal contribution", line)]
        def title_like(line: str) -> bool:
            words = re.findall(r"[A-Za-z][A-Za-z'-]*", line)
            titled = sum(word[0].isupper() or word.lower() in {"a", "an", "and", "is", "of", "on", "the", "to", "with"} for word in words)
            return 3 <= len(words) <= 20 and titled / len(words) >= 0.8 and not re.search(r"[@*†‡]", line)

        title = next((line for line in candidates if title_like(line)), document.title)
        author_lines = [line for line in candidates[candidates.index(title) + 1:] if re.search(r"[A-Za-z]", line) and not "@" in line]
        authors = ", ".join(re.sub(r"[*†‡]", "", line).strip() for line in author_lines[:20]) or document.author
        return title, authors

    @staticmethod
    def _first_match(text: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()[:1000]
        return None

    @staticmethod
    def _research_question(text: str) -> str | None:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return next((sentence[:1000] for sentence in sentences if re.search(r"(?i)\b(?:we (?:study|investigate|examine|ask)|research question|objective|aim)\b", sentence)), None)
