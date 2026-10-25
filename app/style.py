"""A light presentation pass for ordinary conversation.

Local models often turn every answer into a miniature document: several `#`
headings, bold labels, and a bullet for each sentence. That is useful in a
report and unnatural in a conversation. This pass removes decorative Markdown
without touching fenced code, URLs, citations, or substantive wording.
"""
from __future__ import annotations

import re


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_STAR_BULLET = re.compile(r"^(\s*)[*+]\s+")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_UNDERLINE = re.compile(r"__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<!\*)\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)")


def naturalize(text: str) -> str:
    """Remove ornamental Markdown while preserving code and meaning."""
    lines: list[str] = []
    fenced = False
    for original in str(text or "").splitlines():
        line = original
        if line.lstrip().startswith("```"):
            fenced = not fenced
            lines.append(line)
            continue
        if not fenced:
            line = _HEADING.sub("", line)
            line = _STAR_BULLET.sub(r"\1- ", line)
            line = _BOLD.sub(r"\1", line)
            line = _UNDERLINE.sub(r"\1", line)
            line = _ITALIC.sub(r"\1", line)
        lines.append(line.rstrip())
    result = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", result)
