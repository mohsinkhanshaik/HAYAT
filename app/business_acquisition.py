"""Public acquisition discovery with an evidence ledger, never invented deals.

Search snippets are leads, fetched listing text is seller-reported evidence,
and neither establishes fair value or the cause of a business's failure.
"""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_BUSINESS = re.compile(r"(?i)\b(?:business(?:es)?|compan(?:y|ies)|laundromats?|franchises?|agenc(?:y|ies)|acquisition(?:s)?)\b")
_ACQUISITION = re.compile(r"(?i)\b(?:for sale|buy|buying|acquir\w*|acquisition\w*|undervalued|distressed|bankrupt\w*|dead|failed|struggling|cheap|valuation)\b")
_DEEP_FOLLOWUP = re.compile(r"(?i)^(?:please\s+)?(?:look|research|investigate|check|dig)\s+(?:(?:deep|deeper|deeply|more|again)\s+)?(?:in(?:to)?\s+)?(?:it|this|that|them|the same)(?:\b.*)$")
_AUDIT = re.compile(r"(?i)^(?:what (?:did (?:you |u )?|have you )?(?:look(?:ed)? at|check(?:ed)?|research(?:ed)?|find|found)|which sources (?:did you|have you) (?:check|use))\s*[?.!]*$")
_ACK = re.compile(r"(?i)^(?:ok(?:ay)?|thanks|thank you|yes|yep|sure|got it|complete it|check it)\s*[?.!]*$")


def reported_financials(text: str) -> dict[str, Any]:
    """Extract only explicit labels, retaining currency symbols and periods."""
    money = (r"(?P<currency>USD|GBP|EUR|\$|£|€)\s*(?P<amount>\d[\d,]*(?:\.\d{1,2})?)"
             r"(?![\d,]|\.\d|\s*(?:[kmb]|thousand|million|billion)\b|\s*(?:[-–—]|to)\s*(?:\d|\$|USD|GBP|EUR|£|€))")
    labels = {'asking_price': r'asking price',
              'annual_SDE': r'annual (?:SDE|seller.s discretionary earnings)',
              'annual_EBITDA': r'annual EBITDA',
              'annual_revenue': r'annual (?:revenue|gross sales)'}
    result = {}
    for name, label in labels.items():
        matches = list(re.finditer(rf'(?i)\b{label}\s*[:=]?\s*{money}', text))
        if len(matches) == 1:  # Multiple businesses or conflicting figures require review.
            match = matches[0]
            result[name] = {'amount': float(Decimal(match['amount'].replace(',', ''))),
                            'currency_label': match['currency'], 'evidence': match[0],
                            'projected': bool(re.search(r'(?i)projected|forecast|pro forma', text[max(0, match.start() - 40):match.start()])),
                            'verified': False, 'basis': name}
    return result


def acquisition_request(text: str) -> bool:
    if not _BUSINESS.search(text) or not _ACQUISITION.search(text):
        return False
    if re.match(r"(?i)^\s*(?:do not|don't|never|stop)\s+(?:search|research|look|find|buy|acquire)\b", text):
        return False
    if re.search(r'(?i)\b(?:for sale|buy|buying|acquir\w*|acquisition\w*)\b', text):
        return True
    if re.search(r'(?i)\b(?:start|starting|launch)\b', text):
        return False
    return bool(re.search(r'(?i)\b(?:undervalued|cheap)\b', text)
                and re.search(r'(?i)\b(?:find|search|look for|research|show|list|which|best)\b', text))


def followup_request(text: str) -> bool:
    return bool(_DEEP_FOLLOWUP.match(text.strip()))


def audit_request(text: str) -> bool:
    return bool(_AUDIT.match(text.strip()))


def remember_topic(session, text: str, intent) -> None:
    from app.chat_feedback import CORRECTION, corrected_request
    from app.intent.models import IntentKind
    if CORRECTION.match(text.strip()):
        replacement = corrected_request(text)
        if replacement:
            session.conversation_topic = replacement[:4000]
        return
    if _ACK.match(text.strip()) or audit_request(text):
        return
    if intent.intent in {IntentKind.CONTROL, IntentKind.SYSTEM_STATE, IntentKind.DIAGNOSTIC}:
        return
    if followup_request(text):
        return
    from app.agent.conversation import refers_to_previous_reply
    from app.research.world_brief import requested_sectors
    if requested_sectors(session.conversation_topic) and refers_to_previous_reply(text):
        # An explanation of the current world brief must not invalidate the
        # pending job's subject or keep its completed evidence out of the audit.
        return
    session.conversation_topic = str(intent.entities.get("query") or intent.goal or text)[:4000]


def _excerpts(text: str) -> list[str]:
    """Short literal evidence spans; do not turn a seller's story into causality."""
    relevant = re.compile(r"(?i)for sale|asking price|cash flow|SDE|EBITDA|revenue|retir|loss|profit|lease|closed|sold|sale pending|under (?:offer|contract)|equipment|debt")
    chunks = re.split(r"[\r\n]+|(?<=[.!?])\s+", text)
    excerpts = []
    words_left = 160
    for chunk in chunks:
        value = chunk.strip()
        if not relevant.search(value) or value in excerpts:
            continue
        clipped = ' '.join(value.split()[:words_left])[:400]
        excerpts.append(clipped)
        words_left -= len(clipped.split())
        if len(excerpts) >= 8 or words_left <= 0:
            break
    return excerpts


def discover(research, query: str, limit: int = 6, level: str = 'normal') -> dict[str, Any]:
    """Bounded public search and fetch. No model-generated price or valuation."""
    from app.models.streaming import check_cancelled
    from app.research.deduplication import canonicalize_url
    started = time.monotonic()
    limit = max(1, min(int(limit), 8))
    searches = [f'{query} business for sale asking price',
                f'{query} distressed business sale seller cash flow',
                f'{query} business sale reason selling financial statements']
    if level == 'deep':
        searches.extend([f'{query} comparable completed business sales annual earnings multiple',
                         f'{query} business failure causes lease debt equipment turnaround evidence'])
    attempted = []
    leads: dict[str, Any] = {}
    lead_ranks: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    for term in searches:
        check_cancelled()
        if time.monotonic() - started > (100 if level == 'deep' else 60):
            break
        attempted.append(term)
        try:
            search = getattr(research.search, 'search_wide', research.search.search) if level == 'deep' else research.search.search
            for rank, item in enumerate(search(term, limit), 1):
                key = canonicalize_url(item.url)
                leads.setdefault(key, item)
                lead_ranks[key] = min(rank, lead_ranks.get(key, rank))
            for problem in getattr(research.search, 'last_errors', []):
                errors.append({'query': term, 'error': str(problem)[:300]})
        except Exception as exc:
            from app.models.streaming import GenerationCancelled
            if isinstance(exc, GenerationCancelled):
                raise
            errors.append({"query": term, "error": str(exc)[:300]})
    candidates = []
    checked = []
    discovery_pages = []
    # Fetch high-ranked leads from different research angles before exhausting
    # every result from the first search. Ranking is discovery, not validation.
    queue = sorted(leads.values(), key=lambda item: lead_ranks[canonicalize_url(item.url)])
    visited = set()
    while queue and len(visited) < limit:
        item = queue.pop(0)
        key = canonicalize_url(item.url)
        if key in visited:
            continue
        visited.add(key)
        check_cancelled()
        if time.monotonic() - started > (180 if level == 'deep' else 90):
            errors.append({"error": "Research time budget reached; remaining leads were not fetched."})
            break
        try:
            resource = research.fetcher.fetch(item.url, 2 * 1024 * 1024)
            doc, _ = research.extractor.extract(resource)
            checked.append({"url": doc.final_url, "title": doc.title,
                            "retrieved_at": doc.retrieved_at, "content_hash": doc.content_hash})
            # Relevance is required even when a search engine ranks a page highly.
            evidence = _excerpts(doc.text)
            if doc.status_code != 200 or not evidence or not _BUSINESS.search(doc.title + " " + doc.text[:12000]):
                continue
            # A generic article about business profits is not a sale listing.
            if not re.search(r'(?i)\b(?:for sale|asking price|sale package|asset sale)\b', doc.title + ' ' + doc.text[:12000]):
                continue
            directory = (len(re.findall(r'(?i)\basking price\b', doc.text)) > 1
                         or bool(re.search(r'(?i)\b(?:businesses|laundromats|franchises) for sale', doc.title))
                         or bool(re.search(r'(?i)listings found|sort by:', doc.text[:2000]))
                         or '/keyword/' in doc.final_url)
            if directory:
                discovery_pages.append(checked[-1])
                from app.research.models import SearchResult
                from urllib.parse import urljoin
                links = []
                for link in getattr(doc, 'links', []):
                    url = urljoin(doc.final_url, str(link.get('url') or link.get('href') or ''))
                    label = str(link.get('text') or '')
                    if canonicalize_url(url) not in visited and re.search(r'(?i)business-opportunity|business-for-sale|/listing/|/business/|view listing|for sale in', url + ' ' + label):
                        links.append(SearchResult(label or 'Listing link', url))
                queue[0:0] = links[:3]
                continue  # Never attach one directory's numbers to a single business.
            financials = reported_financials(doc.text)
            inactive = bool(re.search(r"(?i)\b(?:business (?:is |has been )?sold|listing (?:is )?(?:sold|withdrawn|expired)|sale pending|under contract)\b", doc.title + " " + doc.text[:12000]))
            candidates.append({"title": doc.title, "url": doc.final_url,
                               "retrieved_at": doc.retrieved_at, "content_hash": doc.content_hash,
                               "status": "possibly_inactive" if inactive else "availability_unconfirmed",
                               "evidence": evidence, "evidence_kind": "publisher_reported_unverified",
                               "reported_financials": financials,
                               "criteria_match_verified": False,
                               "fair_value": None, "undervalued": None,
                               "failure_cause": None})
        except Exception as exc:
            from app.models.streaming import GenerationCancelled
            if isinstance(exc, GenerationCancelled):
                raise
            errors.append({"url": item.url, "error": str(exc)[:300]})
    return {"query": query, "checked_at": datetime.now(UTC).isoformat(),
            "searched_queries": attempted, "planned_queries": searches, "level": level,
            "pages_checked": checked, "candidates": candidates,
            "discovery_pages": discovery_pages,
            "errors": errors, "status": "discovery_only" if candidates else "insufficient_evidence",
            "trust": "untrusted_external_data", "valuation_verified": False,
            "missing_evidence": ["Geography, acquisition budget, currency and target sector",
                                 "Current availability and seller identity",
                                 "Tax returns, bank statements and normalized annual SDE or EBITDA",
                                 "Comparable completed sales with matching profit basis, sector and date",
                                 "Debt, working capital, lease transfer, maintenance capex and liabilities",
                                 "Evidence for failure causes; test turnaround assumptions against demand"]}


def valuation_scenario(annual_earnings: float, multiple_low: float, multiple_high: float,
                       asking_price: float, currency: str, earnings_basis: str,
                       debt: float = 0, cash: float = 0, capex: float = 0,
                       working_capital: float = 0) -> dict[str, Any]:
    """Explicit owner-supplied assumptions, not a market appraisal or recommendation."""
    values = [Decimal(str(value)) for value in (annual_earnings, multiple_low, multiple_high,
                                              asking_price, debt, cash, capex, working_capital)]
    if any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("All financial inputs must be finite and non-negative.")
    earnings, low, high, price, debt_d, cash_d, capex_d, wc_d = values
    if high < low or not re.fullmatch(r"[A-Za-z]{3}", currency):
        raise ValueError("Supply an ordered multiple range and a three-letter currency.")
    if earnings_basis not in {"annual_SDE", "annual_EBITDA"}:
        raise ValueError("Use normalized annual SDE or EBITDA; revenue and monthly MRR are different bases.")
    equity_low = earnings * low - debt_d + cash_d - capex_d - wc_d
    equity_high = earnings * high - debt_d + cash_d - capex_d - wc_d
    return {"status": "assumption_scenario", "currency": currency.upper(), "earnings_basis": earnings_basis,
            "equity_value_low": float(equity_low), "equity_value_high": float(equity_high),
            "asking_price": float(price), "undervaluation_proven": False,
            "formula": "annual normalized earnings × supplied multiple − debt + cash − capex − working capital",
            "limitations": "Supplied inputs are unverified. Multiples require comparable completed sales. SDE and EBITDA scenarios are not interchangeable; confirm transaction structure and excluded assets."}


def format_report(result: dict[str, Any]) -> str:
    lines = [f"Acquisition research: {result['query']}",
             "These are discovery candidates. I have not established fit to your criteria, fair value, current availability, or why an owner failed."]
    if not result["candidates"]:
        lines.append("I did not retrieve enough relevant listing evidence to identify a candidate.")
    if result.get('discovery_pages'):
        lines.append('\nDiscovery pages (directories, not individual listings):')
        for source in result['discovery_pages']:
            lines.append(f"- {source['title']}: {source['url']} (retrieved {source['retrieved_at']})")
    for item in result["candidates"]:
        title = item["title"].replace("[", "(").replace("]", ")").replace("\n", " ")[:200]
        lines.extend([f"\n[{title}]({item['url']}) — {item['status']}; retrieved {item['retrieved_at']}",
                      *[f"Publisher excerpt (unverified): {excerpt}" for excerpt in item["evidence"]]])
        for metric, value in item.get('reported_financials', {}).items():
            projection = 'projected ' if value.get('projected') else ''
            lines.append(f"Seller-reported {projection}{metric}: {value['currency_label']} {value['amount']:,.2f}; unverified.")
    lines.extend(["\nBefore a valuation or shortlist:", *[f"- {gap}" for gap in result["missing_evidence"]]])
    if result["errors"]:
        lines.append(f"\n{len(result['errors'])} search/fetch failure(s) or budget limits; coverage is incomplete.")
    return "\n".join(lines)


def audit_report(audit: dict[str, Any], topic: str) -> str:
    if not audit or audit.get("query") != topic:
        return f"The topic was: {topic or 'not recorded'}. I have no recorded completed source checks for that topic yet."
    lines = [f"I researched: {audit['query']}", f"Status: {audit['status']}."]
    if acquisition_request(audit['query']):
        lines.append("Valuation has not been verified.")
    for source in audit.get("pages_checked", []):
        lines.append(f"- {source['title']}: {source['url']} (retrieved {source['retrieved_at']})")
    if not audit.get("pages_checked"):
        lines.append("No pages were successfully fetched.")
    lines.append(f"Search/fetch errors: {len(audit.get('errors', []))}.")
    return "\n".join(lines)


def remember_research(session, query: str, result: Any) -> None:
    if session.conversation_topic != query or result is None:
        return  # A late job for an older subject must not overwrite the new one.
    scope = {key: session.research_audit[key] for key in ('onion', 'tor_web', 'person', 'public_data')
             if key in (session.research_audit or {})}
    session.research_audit = {
        **scope, 'query': query, 'status': 'completed' if getattr(result, 'complete', True) is True else 'partial',
        'pages_checked': [{'url': source.final_url, 'title': source.title,
                           'retrieved_at': source.retrieved_at, 'content_hash': source.content_hash}
                          for source in getattr(result, 'sources', [])
                          if getattr(source, 'final_url', None) and getattr(source, 'retrieved_at', None)],
        'errors': list(getattr(result, 'errors', [])),
    }


def remember_research_job(session, job) -> None:
    query = getattr(job, 'query', '')
    if session.conversation_topic != query:
        return
    status = getattr(job, 'status', '')
    if status in {'done', 'failed', 'cancelled'}:
        remember_research(session, query, getattr(job, 'result', None))
        audit = session.research_audit or {}
        if audit.get('query') != query:
            audit = {'query': query, 'pages_checked': [], 'errors': []}
        audit.update({key: getattr(job, key, None) for key in ('onion', 'tor_web', 'person', 'public_data')})
        if status != 'done':
            audit['status'] = status
            audit['errors'] = [*audit.get('errors', []), {'error': getattr(job, 'error', '') or status}]
        session.research_audit = audit
