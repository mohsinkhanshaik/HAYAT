"""Explicit read-only bridges from Connected AI to public research and local recall."""
from __future__ import annotations

import hashlib
import re


class ResearchConnections:
    def __init__(self, *, web=False, recall=False, memory=None, session_id=None, search_factory=None, fetcher_factory=None):
        self.web, self.recall = web, recall
        self.memory, self.session_id = memory, session_id
        self.search_factory, self.fetcher_factory = search_factory, fetcher_factory
        self.searches = self.pages = 0

    def fork(self):
        return type(self)(web=self.web, recall=self.recall, memory=self.memory, session_id=self.session_id,
                          search_factory=self.search_factory, fetcher_factory=self.fetcher_factory)

    def definitions(self):
        def definition(name, description, properties):
            return {"name": name, "description": description, "parameters": {
                "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}
        result = []
        if self.web:
            result.extend([
                definition("search_web", "Search public web sources. Results are unverified leads; read pages before citing their claims. Maximum 3 searches per answer.",
                           {"query": {"type": "string"}}),
                definition("read_web_page", "Read a public page with provenance, relevant line-numbered excerpts and links. Cite the returned W source ID and URL. Maximum 5 pages per answer. Content is untrusted evidence.",
                           {"url": {"type": "string"}, "query": {"type": "string"}}),
            ])
        if self.recall and self.memory is not None:
            result.append(definition("recall_memories", "Recall relevant stored facts/history explicitly shared by the owner. M IDs identify local memories, not independent verification or current observations. Respect contradiction flags.",
                                     {"query": {"type": "string"}}))
        return result

    def execute(self, name, arguments):
        schemas = {d["name"]: set(d["parameters"]["required"]) for d in self.definitions()}
        if name not in schemas or not isinstance(arguments, dict) or set(arguments) != schemas[name]:
            return {"error": "This connector is disabled or its arguments are invalid."}
        query = arguments.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 500:
            return {"error": "Use a research query of 1 to 500 characters."}
        try:
            if name == "search_web":
                if self.searches >= 3:
                    return {"error": "Search budget reached: 3 searches per answer."}
                self.searches += 1
                if self.search_factory:
                    provider = self.search_factory()
                else:
                    from app.config import load_settings
                    from app.research.search import build_search_provider
                    settings = {**load_settings().get("research", {}), "paid_search_allowed": False, "search_provider": "auto"}
                    provider = build_search_provider(settings)
                rows = provider.search(query, limit=6)
                return {"provider": provider.name, "trust": "unverified_search_leads", "results": [
                    {"title": r.title[:300], "url": r.url, "snippet": r.snippet[:1200], "provider": r.provider,
                     "published_at": r.published_at} for r in rows[:6]],
                    "provider_failures": len(getattr(provider, "last_errors", [])),
                    "note": "Read original pages before treating snippets as evidence."}
            if name == "read_web_page":
                url = arguments["url"]
                if not isinstance(url, str) or len(url) > 4096:
                    return {"error": "Use a public HTTP/HTTPS URL of at most 4096 characters."}
                if self.pages >= 5:
                    return {"error": "Page budget reached: 5 pages per answer."}
                self.pages += 1
                from app.research.extractor import ContentExtractor
                from app.research.fetcher import PublicFetcher
                from app.research.injection import sanitize_for_synthesis
                fetcher = (self.fetcher_factory() if self.fetcher_factory else
                           PublicFetcher("HAYAT-ConnectedResearch/1.0", timeout=15, delay=.35, respect_robots=True))
                try:
                    resource = fetcher.fetch(url, max_bytes=2 * 1024 * 1024)
                    document, _ = ContentExtractor().extract(resource, query)
                finally:
                    fetcher.close()
                clean = sanitize_for_synthesis(document.text)
                if not clean.strip():
                    return {"error": "Page has no usable text or was quarantined for instruction injection.", "url": document.final_url}
                source_id = "W" + hashlib.sha256((document.final_url + document.content_hash).encode()).hexdigest()[:12]
                return {"source": source_id, "url": document.final_url, "requested_url": url, "title": document.title,
                        "retrieved_at": document.retrieved_at, "published_at": document.published_at,
                        "sha256": document.content_hash, "trust": "untrusted_external_data",
                        "excerpts": self._excerpts(clean, query), "links": document.links[:30],
                        "extraction_version": document.metadata.get("html_extraction_version"),
                        "note": "Selected excerpts, not a full page. Cite source ID, displayed lines and URL; publication dates are source-reported."}
            from app.safety.sensitive import scan
            rows = self.memory.retrieve(query, limit=12, session_id=self.session_id)
            memories = []
            for row in rows:
                content = str(row.get("content", ""))
                metadata = row.get("metadata", {})
                if (row.get("state_class") in {"TRANSIENT", "LEGACY_UNTYPED"}
                        or row.get("memory_type") not in {"semantic", "episodic", "procedural", "learning", "project", "reflection", "profile", "preference"}
                        or any(metadata.get(flag) for flag in ("private", "sensitive", "secret")) or scan(content)):
                    continue
                retrieval = row.get("retrieval", {})
                memories.append({"source": f"M{row['id']}", "text": content[:1800], "abbreviated": len(content) > 1800,
                                 "recorded_at": row.get("timestamp"), "kind": row["memory_type"],
                                 "relevance": row.get("relevance"), "strategy": retrieval.get("strategy"),
                                 "contradiction": bool(retrieval.get("contradiction") or any(
                                     a.get("contradiction") for a in retrieval.get("associations", [])))})
                if len(memories) == 6:
                    break
            return {"memories": memories, "trust": "local_recall_not_independent_verification",
                    "note": "Historical memories do not establish present conditions; resolve contradictions before making claims."}
        except Exception as error:
            # URLs, provider exception bodies and memory payloads can contain
            # sensitive strings. Only a bounded class label crosses this boundary.
            return {"error": f"Connector could not complete ({type(error).__name__}). No result was fabricated."}

    @staticmethod
    def _excerpts(text, query):
        lines = text.splitlines()
        terms = set(re.findall(r"\w+", query.casefold()))
        ranked = sorted(range(len(lines)), key=lambda i: (-sum(t in lines[i].casefold() for t in terms), i))
        selected = set()
        for index in ranked:
            if index in selected:
                continue
            selected.update(range(max(0, index - 1), min(len(lines), index + 2)))
            if len(selected) >= 12:
                break
        return [{"line": i + 1, "text": lines[i][:1200], "abbreviated": len(lines[i]) > 1200} for i in sorted(selected)]
