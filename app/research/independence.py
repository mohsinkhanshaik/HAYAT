from __future__ import annotations

import re
from collections import defaultdict
from urllib.parse import urlsplit

from app.research.models import SourceDocument
from app.research.text import jaccard, stable_id


def registrable_domain(host: str) -> str:
    labels = host.lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


class SourceIndependenceAnalyzer:
    def annotate(self, sources: list[SourceDocument]) -> list[SourceDocument]:
        clusters: list[tuple[str, str]] = []
        for source in sources:
            group = ""
            sample = source.text[:12_000]
            for existing_group, existing_text in clusters:
                if jaccard(sample, existing_text) >= 0.82:
                    group = existing_group
                    break
            if not group:
                root = registrable_domain(source.domain)
                lineage = self._lineage_hint(source)
                group = stable_id("lineage", lineage or root)
                clusters.append((group, sample))
            source.independence_group = group
            source.lineage_urls = self._referenced_origins(source)
        return sources

    @staticmethod
    def _lineage_hint(source: SourceDocument) -> str:
        metadata = str(source.metadata).lower()
        match = re.search(r"(?:source|via|originally published)[^h]{0,40}(https?://[^\s'\"]+)", metadata)
        return match.group(1) if match else ""

    @staticmethod
    def _referenced_origins(source: SourceDocument) -> list[str]:
        own = registrable_domain(source.domain)
        values: list[str] = []
        for link in source.links[:300]:
            host = urlsplit(link.get("url", "")).hostname or ""
            root = registrable_domain(host)
            if root and root != own and root not in values:
                values.append(root)
        return values[:20]

    @staticmethod
    def independent_count(sources: list[SourceDocument]) -> int:
        return len({source.independence_group or registrable_domain(source.domain) for source in sources})

    @staticmethod
    def group_sizes(sources: list[SourceDocument]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for source in sources:
            counts[source.independence_group or registrable_domain(source.domain)] += 1
        return dict(counts)
