from __future__ import annotations

from app.research.models import SourceDocument


def citation_list(sources: list[SourceDocument]) -> list[str]:
    return [f"[{index}] {source.title} — {source.final_url} (retrieved {source.retrieved_at})" for index, source in enumerate(sources, 1)]


def markdown_sources(sources: list[SourceDocument], cited: set[int] | None = None) -> str:
    """Numbered source lines; with `cited`, only those numbers (kept as-is so [n] still matches).

    "What is the capital city of Australia" listed four Capital University
    (Ohio) pages that no claim used beside the two that answered it.
    """
    lines = [f"[{index}] [{source.title}]({source.final_url}) — retrieved {source.retrieved_at[:10]}"
             for index, source in enumerate(sources, 1) if cited is None or index in cited]
    hidden = len(sources) - len(lines)
    if cited is not None and hidden:
        lines.append(f"({hidden} other page{'s' if hidden != 1 else ''} read but not cited.)")
    return "\n".join(lines)
