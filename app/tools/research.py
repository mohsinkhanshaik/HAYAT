from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.research.crawler import BoundedCrawler
from app.research.github import GitHubResearch
from app.research.manager import ResearchManager
from app.research.models import ResearchMetrics, ResearchResult, SourceDocument
from app.research.papers import PaperAnalyzer
from app.tools.registry import ToolRegistry


def register_research_tools(registry: ToolRegistry, research: ResearchManager,
                            document_learner: Any = None) -> None:
    obj = lambda props, required=None: {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}
    from app.research.handoff import browser_handoff
    registry.register('research_browser_handoff', 'Prepare a public search URL or open a visible user-operated search browser. '
                      'Onion searches and existing Tor intent require the masked browser, with no direct fallback. '
                      'The user completes interactive checks; opening the browser neither extracts content nor bypasses source restrictions.',
                      obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500}, 'onion': {'type': 'boolean'},
                           'open_browser': {'type': 'boolean'}, 'tor_only': {'type': 'boolean'}}, ['query']), 'low', 'application_launch',
                      lambda query, onion=False, open_browser=False, tor_only=False:
                      browser_handoff(research, query, onion, open_browser, tor_only=tor_only))
    from app.research.public_data import PublicData, catalog
    registry.register('public_data_sources',
                      'List registered DataCite, Crossref, Europe PMC and World Bank public API capabilities and official documentation. '
                      'Does not fetch data or establish provider readiness.', obj({}), 'low', 'system_read', catalog)
    registry.register('public_data_search',
                      'Search dataset/publication metadata with provenance and partial-provider errors. kind=literature uses Europe PMC, retains papers without DOIs and returns next_cursor. '
                      'Other kinds use DataCite/Crossref with numbered pages. Limit is per source; at most 20 records. No dataset or full-text downloads or verified findings.',
                      obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                           'kind': {'enum': ['all', 'datasets', 'publications', 'literature']},
                           'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10},
                           'page': {'type': 'integer', 'minimum': 1, 'maximum': 100},
                           'cursor': {'type': 'string', 'minLength': 1, 'maxLength': 2048}}, ['query']),
                      'low', 'public_web_read', lambda query, kind='all', limit=5, page=1, cursor='*':
                      PublicData(research.fetcher).search(query, kind, limit, page, cursor))
    registry.register('public_indicator_read',
                      'Retrieve at most 100 annual World Bank observations for one country/aggregate and indicator code, with source date, '
                      'metadata, units if reported, footnotes and missing/conflicting-value flags. No gap filling or forecast.',
                      obj({'country': {'type': 'string', 'pattern': '^[A-Za-z]{2,3}$'},
                           'indicator': {'type': 'string', 'pattern': '^[A-Za-z0-9_.]{3,80}$'},
                           'start_year': {'type': 'integer', 'minimum': 1900, 'maximum': 2100},
                           'end_year': {'type': ['integer', 'null'], 'minimum': 1900, 'maximum': 2100},
                           'source': {'type': 'integer', 'minimum': 1, 'maximum': 999}}, ['country', 'indicator']),
                      'low', 'public_web_read', lambda country, indicator, start_year=2015, end_year=None, source=2:
                      PublicData(research.fetcher).indicator(country, indicator, start_year, end_year, source))

    def web_search(query: str, limit: int = 10, recency: str | None = None, domains: list[str] | None = None) -> dict[str, Any]:
        results = research.search.search(query, max(1, min(limit, 50)), recency, domains)
        return {"query": query, "count": len(results), "results": [item.to_dict() for item in results], "trust": "untrusted_external_data"}

    def wide_web_search(query: str, limit: int = 20, recency: str | None = None, domains: list[str] | None = None) -> dict[str, Any]:
        search = getattr(research.search, 'search_wide', research.search.search)
        results = search(query, limit, recency, domains)
        return {'query': query, 'count': len(results), 'results': [item.to_dict() for item in results],
                'indexes': getattr(research.search, 'last_coverage', []),
                'errors': getattr(research.search, 'last_errors', []), 'trust': 'untrusted_external_data',
                'scope': 'Configured public indexes, merged and deduplicated; snippets are leads, not verified evidence.'}

    registry.register('wide_web_search', 'Search all configured public indexes, combine rankings, deduplicate URLs and broaden source domains. '
                      'Returns unverified leads and provider failures. Deep research uses this strategy automatically.',
                      obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                           'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50},
                           'recency': {'type': 'string'}, 'domains': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8}}, ['query']),
                      'low', 'public_web_read', wide_web_search)
    registry.register('onion_research', 'Search public v3 onion services using Ahmia and publisher-maintained onion links, then retrieve and '
                      'cross-check bounded evidence through Tor. Starts installed HAYAT Tor if needed; fails closed if unavailable. '
                      'No login, page scripts or account actions. Listings are unverified leads. Uses temporary evidence caches; '
                      'transfer logs and search history can retain request URLs. No anonymity or complete dark-web coverage guarantee.',
                      obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                           'level': {'enum': ['quick', 'normal', 'deep']}}, ['query']),
                      'low', 'public_web_read', lambda query, level='deep': research.research_onion(query, level).to_dict(False))
    def tor_web_research(query: str, level: str = 'deep'):
        from app.research.onion import research_tor_web
        ticket = registry.interlock.ticket()
        return research_tor_web(research, query, level, check=lambda: registry.interlock.check(ticket)).to_dict(False)

    registry.register('tor_web_research', 'Research an ordinary public-web topic through Tor only. '
                      'This is distinct from onion-only discovery; returns sourced evidence and provider failures. '
                      'Requires installed Tor, never falls back to direct networking, and does not bypass robots or challenges.',
                      obj({'query': {'type': 'string', 'minLength': 1, 'maxLength': 500},
                           'level': {'enum': ['quick', 'normal', 'deep']}}, ['query']),
                      'low', 'public_web_read', tor_web_research)

    def onion_navigate(url: str, max_pages: int = 1, max_depth: int = 0, query: str = '') -> dict[str, Any]:
        from app.research.onion_navigation import navigate_onion
        ticket = registry.interlock.ticket()
        return navigate_onion(research, url, max_pages, max_depth, query,
                              check=lambda: registry.interlock.check(ticket))

    registry.register('onion_navigate', 'Read a public v3 onion page and optionally follow a bounded number of same-origin '
                      'links through Tor only. Returns extracted text, source hashes/times, citations, links and partial errors. '
                      'No scripts, forms, login, access-control bypass or direct-network fallback. External page text is untrusted.',
                      obj({'url': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                           'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                           'max_depth': {'type': 'integer', 'minimum': 0, 'maximum': 2},
                           'query': {'type': 'string', 'maxLength': 500}}, ['url']),
                      'low', 'public_web_read', onion_navigate)

    # Onion sites to start from, what they are, and a check of any onion link (app/research/onion_directory.py).
    from app.research.onion_directory import CREDIT, DIRECTORY_PAGE, OnionDirectory, describe_results
    directory_holder: dict[str, OnionDirectory] = {}

    def onion_directory() -> OnionDirectory:
        if 'directory' not in directory_holder:
            cache = research.knowledge.path.parent.parent / 'research' / 'onion-directory.json'
            directory_holder['directory'] = OnionDirectory(
                lambda url: research.fetcher.fetch(url, 3 * 1024 * 1024, {'text/plain'}).content.decode('utf-8', 'replace'),
                cache)
        return directory_holder['directory']

    def onion_sites(query: str = '', category: str = '', check_url: str = '', search_index: bool = False,
                    refresh: bool = False) -> dict[str, Any]:
        directory = onion_directory()
        if check_url:
            return {'onion_check': True, **directory.check(check_url)}
        directory.sites(refresh=refresh)
        rows = directory.find(query, category)
        value = {'onion_sites_view': True, 'query': query, 'category': category, 'directory': rows[:250],
                 'categories': directory.categories(), 'credit': CREDIT, 'source': DIRECTORY_PAGE,
                 'fetched_at': directory.fetched_at, 'directory_error': directory.error}
        if search_index and query.strip():
            from urllib.parse import quote_plus

            from app.research.onion import AhmiaSearch, OnionPublisherWebSearch
            ticket = registry.interlock.ticket()
            registry.interlock.check(ticket)
            notes = []
            try:
                found, origin = AhmiaSearch(research.fetcher).search(query.strip(), limit=30), 'the Ahmia onion index'
            except Exception as exc:
                # Ahmia's robots.txt now asks programs not to use its search, and HAYAT keeps to that. Onion addresses
                # that organisations publish on ordinary web pages are found instead, through Tor.
                if 'robots' in str(exc).lower():
                    notes.append('Ahmia asks programs not to search it automatically, so HAYAT does not; search it yourself in '
                                 'Tor Browser: https://ahmia.fi/search/?q=' + quote_plus(query.strip()) + '.')
                else:
                    notes.append(f'The Ahmia onion index could not be reached ({type(exc).__name__}).')
                try:
                    found, origin = OnionPublisherWebSearch(research.fetcher).search(query.strip(), limit=20), \
                        'onion addresses published on ordinary web pages'
                except Exception as fallback:  # the directory still answers when no search can
                    found, origin = [], ''
                    notes.append(f'Published onion addresses could not be searched either ({type(fallback).__name__}).')
            index = describe_results(found, directory)
            index.update(source=origin, note=' '.join(filter(None, [index['note'], *notes])))
            value['index'] = index
        return value

    registry.register('onion_sites', 'Onion sites to start from and what they are: a verified directory of mainstream '
                      'organisations\' onion services (news, search, social, privacy tools, government, SecureDrop) with '
                      'category, proof link and recent reachability; optionally the Ahmia onion index for a topic, each result '
                      'labelled by its apparent nature and marked unverified, illegal offerings withheld; or check_url to '
                      'check whether an onion address is a listed site, a lookalike copy or unknown.',
                      obj({'query': {'type': 'string', 'maxLength': 200}, 'category': {'type': 'string', 'maxLength': 60},
                           'check_url': {'type': 'string', 'maxLength': 2000}, 'search_index': {'type': 'boolean'},
                           'refresh': {'type': 'boolean'}}),
                      'low', 'public_web_read', onion_sites)

    def public_contact_search(name: str, organization: str, official_url: str = '', max_pages: int | None = None,
                              depth: str = 'normal', tor_only: bool = False):
        from app.research.public_contacts import discover_contacts
        ticket = registry.interlock.ticket()

        def check():
            registry.interlock.check(ticket)
            if getattr(getattr(registry, 'authority', None), 'privacy_mode', False):
                raise PermissionError('Privacy mode blocks public contact discovery.')

        return discover_contacts(research, name, organization, official_url,
                                 max_pages=max_pages, depth=depth, tor_only=tor_only, check=check)

    registry.register('public_contact_search', 'Find explicitly published professional and team contact routes for a known '
                      'name within a supplied organization. Fetches bounded public pages and returns separate source candidates, '
                      'published work channels, source URLs/hashes/retrieval times, errors and unresolved identity. '
                      'depth=deep broadens bounded public index queries; tor_only requires a working Tor route without direct fallback. '
                      'Optional official_url is an owner-selected directory/profile seed, not proof of ownership. '
                      'No guessed email patterns, private/home contacts, broker records, face identification or automatic outreach.',
                      obj({'name': {'type': 'string', 'minLength': 2, 'maxLength': 120},
                           'organization': {'type': 'string', 'minLength': 3, 'maxLength': 240},
                           'official_url': {'type': 'string', 'maxLength': 2000},
                           'max_pages': {'type': 'integer', 'minimum': 1, 'maximum': 12},
                           'depth': {'enum': ['normal', 'deep']}, 'tor_only': {'type': 'boolean'}}, ['name', 'organization']),
                      'low', 'public_web_read', public_contact_search)

    def public_web_fetch(url: str, max_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
        resource = research.fetcher.fetch(url, min(max_bytes, 8 * 1024 * 1024))
        document, extraction_ms = research.extractor.extract(resource)
        data = document.to_dict(include_text=False)
        header_names = ('content-security-policy', 'strict-transport-security', 'x-frame-options',
                        'x-content-type-options', 'referrer-policy', 'permissions-policy',
                        'content-type', 'content-length', 'cache-control')
        data.update({"text": document.text[:50_000], "extraction_ms": extraction_ms,
                     "fetch_elapsed_ms": resource.elapsed_ms,
                     "response_headers": {key: resource.headers[key] for key in header_names if key in resource.headers}})
        return data

    def public_api_get(url: str, max_bytes: int = 2 * 1024 * 1024) -> dict[str, Any]:
        return {"url": url, "data": research.fetcher.get_json(url, min(max_bytes, 4 * 1024 * 1024)), "trust": "untrusted_external_data"}

    def public_source_profile(url: str) -> dict[str, Any]:
        from app.research.osint import profile_source
        return profile_source(research, url)

    def public_download(url: str, filename: str = "") -> dict[str, Any]:
        resource = research.fetcher.fetch(url, 20 * 1024 * 1024)
        safe_name = Path(filename or Path(resource.final_url.split("?", 1)[0]).name or resource.sha256).name
        folder = PROJECT_ROOT / "data" / "knowledge" / "sources"
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / f"{resource.sha256[:12]}-{safe_name}"
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_bytes(resource.content)
        if document_learner is None:
            from app.security.scanner import DefenderFileScanner
            scan = DefenderFileScanner().scan(partial)
        else:
            scan = document_learner.scan_file(partial)
        if not scan.get("clean"):
            blocked = output.with_suffix(output.suffix + ".blocked")
            partial.replace(blocked)
            raise PermissionError(
                f"Download retained as {blocked.name} but access is blocked: {scan.get('reason')}"
            )
        partial.replace(output)
        return {"path": str(output), "bytes": len(resource.content), "sha256": hashlib.sha256(resource.content).hexdigest(), "source_url": resource.final_url, "quarantined": True, "executed": False, "antimalware_scan": scan}

    def learn_public_document(url: str, topic: str = "", filename: str = "",
                              max_bytes: int = 64 * 1024 * 1024,
                              deep: bool = True, remember: bool = True) -> dict[str, Any]:
        if document_learner is None:
            raise RuntimeError("Public document learning is unavailable in this runtime.")
        return document_learner.learn(
            url, topic, filename, max_bytes=max_bytes, deep=deep, remember=remember,
        )

    def public_document_learning_status() -> dict[str, Any]:
        if document_learner is None:
            return {"available": False}
        return {"available": True, **document_learner.status()}

    def bounded_crawl(start_url: str, query: str, max_pages: int = 10, max_depth: int = 2, same_domain: bool = True) -> dict[str, Any]:
        sources, errors = BoundedCrawler(research.fetcher, research.extractor).crawl(start_url, query, min(max_pages, 50), min(max_depth, 3), same_domain=same_domain)
        return {"count": len(sources), "sources": [source.to_dict(include_text=False) for source in sources], "errors": errors, "trust": "untrusted_external_data"}

    def research_topic(query: str, level: str = "normal", official_only: bool = False, remember: bool = True) -> dict[str, Any]:
        return research.research(query, level, official_only, remember).to_dict(False)

    from app.business_acquisition import discover, valuation_scenario
    registry.register("business_acquisition_research", "Discover public business sale candidates, fetch publisher evidence and preserve source checks. Does not establish undervaluation or failure causes.",
                      obj({"query": {"type": "string", "minLength": 1, "maxLength": 4000},
                           "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                           "level": {"type": "string", "enum": ["normal", "deep"]}}, ["query"]),
                      "low", "knowledge_research", lambda query, limit=6, level='normal': discover(research, query, limit, level))
    registry.register("business_valuation_scenario", "Calculate an explicitly assumed valuation range from annual normalized SDE or EBITDA and supplied multiples. Never proves a bargain.",
                      obj({**{key: {"type": "number", "minimum": 0} for key in
                              ("annual_earnings", "multiple_low", "multiple_high", "asking_price", "debt", "cash", "capex", "working_capital")},
                           "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
                           "earnings_basis": {"type": "string", "enum": ["annual_SDE", "annual_EBITDA"]}},
                          ["annual_earnings", "multiple_low", "multiple_high", "asking_price", "currency", "earnings_basis"]),
                      "low", "filesystem_read", valuation_scenario)

    def capture_web_page(url: str, topic: str = "", remember: bool = True,
                         screenshot: bool = True, full_page: bool = False,
                         selector: str = "", scroll_page: bool = True,
                         max_scrolls: int = 20) -> dict[str, Any]:
        """Render, extract, optionally screenshot, and durably index one page."""
        if "://" not in url:
            url = "https://" + url.strip()
        result = research.capture_page(
            url, topic, remember=remember, scroll_page=scroll_page,
            max_scrolls=max_scrolls,
        )
        payload = result.to_dict(False)
        # Surface the observed destination at the top level so the general
        # browser goal verifier can prove navigation after redirects.
        if result.sources:
            payload["url"] = result.sources[0].final_url
            payload["title"] = result.sources[0].title
        payload["remembered"] = bool(remember)
        if screenshot:
            if research.browser is None:
                raise RuntimeError("Webpage screenshots require the browser service.")
            digest = hashlib.sha256(str(result.sources[0].final_url).encode()).hexdigest()[:12]
            path = f"data/artifacts/web-captures/{digest}.png"
            payload["screenshot"] = research.browser.screenshot(
                path=path, full_page=full_page, selector=selector,
            )
        return payload

    def knowledge_search(query: str, limit: int = 8, min_confidence: float = 0.0, domain: str | None = None, after: str | None = None, before: str | None = None) -> dict[str, Any]:
        results = research.knowledge.search(query, min(limit, 50), min_confidence=min_confidence, domain=domain, after=after, before=before)
        return {"query": query, "count": len(results), "results": results}

    def github_research(repository: str, include_issues: bool = True, include_pulls: bool = False, include_source: bool = True) -> dict[str, Any]:
        return GitHubResearch(research.fetcher).inspect(repository, include_issues, include_pulls, include_source)

    registry.register('open_source_search',
                      'Find public open-source candidates for any task using the GitHub API. Returns license, maintenance signals and an inspection step; does not install code.',
                      obj({'query': {'type': 'string', 'maxLength': 250},
                           'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20},
                           'language': {'type': 'string'}, 'license': {'type': 'string'}}, ['query']),
                      'low', 'public_api_get', GitHubResearch(research.fetcher).search)

    def paper_research(url: str) -> dict[str, Any]:
        resource = research.fetcher.fetch(url, 20 * 1024 * 1024)
        document, extraction_ms = research.extractor.extract(resource, "research paper methodology results limitations")
        return {**PaperAnalyzer().analyze(document), "extraction_ms": extraction_ms, "content_hash": document.content_hash}

    def explain_knowledge(query: str, limit: int = 5) -> dict[str, Any]:
        claims = research.knowledge.search_claims(query, min(limit, 20), include_history=True)
        return {"query": query, "claims": claims, "answerable_questions": ["Why do you believe that?", "Which source is strongest?", "Is any source disagreeing?", "When was this last verified?"]}

    def knowledge_conflicts(query: str = "", unresolved_only: bool = True,
                            limit: int = 20) -> dict[str, Any]:
        conflicts = research.knowledge.conflicts(
            query, unresolved_only=unresolved_only, limit=min(limit, 100),
        )
        return {
            "query": query, "count": len(conflicts), "conflicts": conflicts,
            "policy": "Both sides and their provenance are retained; unresolved conflicts are never recalled as settled facts.",
        }

    def export_knowledge(query: str, format: str = "markdown", path: str = "") -> dict[str, Any]:
        content = research.knowledge.export_topic(query, format)
        suffix = "json" if format == "json" else "md"
        target = registry.permissions.validate_project_path(path or f"data/artifacts/knowledge-{hashlib.sha256(query.encode()).hexdigest()[:10]}.{suffix}", write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "format": format, "bytes": len(content.encode("utf-8")), "exists": target.is_file()}

    def track_public_source(url: str, label: str = "") -> dict[str, Any]:
        resource = research.fetcher.fetch(url, 8 * 1024 * 1024)
        research.knowledge.monitor_source(resource.final_url, label or resource.final_url, resource.sha256)
        return {"url": resource.final_url, "label": label or resource.final_url, "content_hash": resource.sha256, "tracking": True}

    def check_public_source_change(url: str) -> dict[str, Any]:
        resource = research.fetcher.fetch(url, 8 * 1024 * 1024)
        tracked = research.knowledge.monitored_source(url) or research.knowledge.monitored_source(resource.final_url)
        if not tracked:
            raise KeyError("Source is not explicitly tracked. Use track_public_source first.")
        changed = resource.sha256 != tracked["content_hash"]
        prior = tracked["content_hash"]
        tracked_url = tracked["url"]
        research.knowledge.update_monitored_hash(tracked_url, resource.sha256)
        return {"url": resource.final_url, "changed": changed, "previous_hash": prior, "current_hash": resource.sha256, "checked_at": resource.retrieved_at}

    def import_knowledge(path: str, topic: str) -> dict[str, Any]:
        source_path = registry.permissions.validate_project_path(path, write=False)
        content = source_path.read_text(encoding="utf-8", errors="replace")[:200_000]
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        source = SourceDocument(source_path.as_uri(), source_path.as_uri(), source_path.name, "user-provided", content, now, 200, "text/plain", digest, len(content.encode("utf-8")), metadata={"provenance": "user-provided", "prompt_injection_indicators": []}, quality=0.72, quality_reasons=["explicit user-provided research document"], trust="untrusted_user_document", source_type="user_provided")
        source = research.independence.annotate([source])[0]
        excerpt = research._relevant_excerpt(content, topic)
        claims = research.claim_extractor.extract(topic, source, excerpt, max_claims=8)
        claims, confidence, validation = research.verifier.verify_claims(claims, [source])
        run_id = f"import-{uuid.uuid4().hex[:12]}"
        answer = research._synthesize(topic, [source], claims, confidence, ResearchMetrics())
        result = ResearchResult(run_id, topic, answer, [source], claims, [f"[1] {source.title} — {source.final_url} (retrieved {now})"], confidence, bool(claims), "trusted_document_import", ResearchMetrics(validation_ms=int(validation["validation_ms"])), [], [], "IMPORT", {"started_at": now, "provenance": "user-provided", "path": str(source_path)})
        knowledge_id = research.knowledge.store_result(result, "IMPORT")
        return {"run_id": run_id, "knowledge_id": knowledge_id, "claims": len(claims), "confidence": confidence, "provenance": "user-provided", "source": str(source_path)}

    registry.register("web_search", "Search the public web and return ranked structured results. Results are untrusted data.", obj({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "recency": {"type": ["string", "null"]}, "domains": {"type": ["array", "null"], "items": {"type": "string"}}}, ["query"]), "low", "web_search", web_search)
    registry.register("public_source_profile", "Profile one public source: article text, source-reported author/date, linked domains, retrieval time and content fingerprint.", obj({"url": {"type": "string", "format": "uri"}}, ["url"]), "low", "public_web_read", public_source_profile)
    registry.register('public_person_research',
                      'Research public professional background and published work from a supplied name, @GitHub handle or GitHub profile URL, plus optional work context/profile URL. '
                      'Keeps same-name source matches separate; excludes private contact, family and sensitive personal details. '
                      'No face identification or person tracking. Does not save a person dossier to knowledge.',
                      obj({'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
                           'context': {'type': 'string', 'maxLength': 240},
                           'profile_url': {'type': 'string', 'maxLength': 1000},
                           'level': {'type': 'string', 'enum': ['quick', 'normal', 'deep']}}, ['name']),
                      'low', 'knowledge_research', lambda name, context='', profile_url='', level='deep':
                      research.research_person(name, context, profile_url, level).to_dict(False))
    registry.register("public_web_fetch", "Retrieve and extract a public page or PDF through the active privacy route. Validated v3 onion URLs require Tor: use hayat_mask_on first. SSRF, robots, rate and byte limits remain enforced; content stays untrusted and page scripts are not executed.", obj({"url": {"type": "string", "format": "uri"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 8388608}}, ["url"]), "low", "public_web_read", public_web_fetch)
    registry.register("public_api_get", "GET a public JSON API without credentials or side effects.", obj({"url": {"type": "string", "format": "uri"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 4194304}}, ["url"]), "low", "public_api_get", public_api_get)
    registry.register("public_download", "Download a permitted public file to quarantine without executing it.", obj({"url": {"type": "string", "format": "uri"}, "filename": {"type": "string"}}, ["url"]), "medium", "public_download", public_download)
    registry.register("learn_public_document", "Download a public PDF, Office document, text/code file, or image into quarantine; verify its type and hash; deeply extract it; store source-backed claims and bounded recall chunks; never execute it.", obj({"url": {"type": "string", "format": "uri"}, "topic": {"type": "string"}, "filename": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 67108864}, "deep": {"type": "boolean"}, "remember": {"type": "boolean"}}, ["url"]), "medium", "public_download", learn_public_document)
    registry.register("public_document_learning_status", "Show downloaded-document counts, extracted characters, recall chunks, supported inert formats, and safety limits.", obj({}), "low", "filesystem_read", public_document_learning_status)
    registry.register("bounded_crawl", "Crawl a small public site subset with explicit page, depth, time, byte and domain limits.", obj({"start_url": {"type": "string", "format": "uri"}, "query": {"type": "string"}, "max_pages": {"type": "integer", "minimum": 1, "maximum": 50}, "max_depth": {"type": "integer", "minimum": 0, "maximum": 3}, "same_domain": {"type": "boolean"}}, ["start_url", "query"]), "medium", "bounded_crawl", bounded_crawl)
    registry.register("research_topic", "Run bounded multi-source research, validate claim citations, preserve provenance and optionally remember distilled knowledge.", obj({"query": {"type": "string"}, "level": {"type": "string", "enum": ["fast", "normal", "deep", "quick", "primary_only", "local_knowledge_only", "adversarial"]}, "official_only": {"type": "boolean"}, "remember": {"type": "boolean"}}, ["query"]), "low", "knowledge_research", research_topic)
    registry.register("capture_web_page", "Render one public webpage, scroll it to reveal lazy content, extract its visible content, optionally capture a full-page, viewport, or CSS-selector screenshot, and retain provenance-bearing knowledge for later recall.", obj({"url": {"type": "string", "format": "uri"}, "topic": {"type": "string"}, "remember": {"type": "boolean"}, "screenshot": {"type": "boolean"}, "full_page": {"type": "boolean"}, "selector": {"type": "string"}, "scroll_page": {"type": "boolean"}, "max_scrolls": {"type": "integer", "minimum": 1, "maximum": 40}}, ["url"]), "low", "knowledge_research", capture_web_page)
    registry.register("knowledge_search", "Search verified locally stored research knowledge by semantic relevance, confidence, source domain, and date.", obj({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "min_confidence": {"type": "number", "minimum": 0, "maximum": 1}, "domain": {"type": ["string", "null"]}, "after": {"type": ["string", "null"]}, "before": {"type": ["string", "null"]}}, ["query"]), "low", "filesystem_read", knowledge_search)
    registry.register("github_research", "Inspect a public GitHub repository README, releases, tags, issues, pull requests, manifests, docs, and CI through the official API.", obj({"repository": {"type": "string"}, "include_issues": {"type": "boolean"}, "include_pulls": {"type": "boolean"}, "include_source": {"type": "boolean"}}, ["repository"]), "low", "public_api_get", github_research)
    registry.register("paper_research", "Extract a public research paper's question, method, data, results, limitations, conflicts and conclusion with PDF provenance.", obj({"url": {"type": "string", "format": "uri"}}, ["url"]), "low", "public_web_read", paper_research)
    registry.register("explain_knowledge", "Show stored claims, evidence, directness, conflicts, confidence, source strength and last verification.", obj({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, ["query"]), "low", "filesystem_read", explain_knowledge)
    registry.register("knowledge_conflicts", "List unresolved or historical claim-to-claim contradictions with evidence for both sides.", obj({"query": {"type": "string"}, "unresolved_only": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}), "low", "filesystem_read", knowledge_conflicts)
    registry.register("export_knowledge", "Export a researched topic with claims, evidence, confidence and freshness as Markdown or JSON.", obj({"query": {"type": "string"}, "format": {"type": "string", "enum": ["markdown", "json"]}, "path": {"type": "string"}}, ["query"]), "low", "filesystem_write_project", export_knowledge)
    registry.register("track_public_source", "Explicitly track one public page by content hash for later change detection.", obj({"url": {"type": "string", "format": "uri"}, "label": {"type": "string"}}, ["url"]), "low", "public_web_read", track_public_source)
    registry.register("check_public_source_change", "Re-fetch an explicitly tracked public page and report whether its content hash changed.", obj({"url": {"type": "string", "format": "uri"}}, ["url"]), "low", "public_web_read", check_public_source_change)
    registry.register("import_knowledge", "Import an explicitly chosen project text/Markdown document with user-provided provenance; generated summaries are not treated as primary evidence.", obj({"path": {"type": "string"}, "topic": {"type": "string"}}, ["path", "topic"]), "medium", "filesystem_read", import_knowledge)
