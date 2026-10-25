"""Source-backed world updates with explicit sector coverage and scenarios."""
from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import UTC, datetime

from app.models.streaming import check_cancelled
from app.research.models import ClaimStatus, ResearchMetrics, ResearchResult


SECTORS = (
    ('Industry and technology', r'industr(?:y|ial|ies)|manufactur|technology|supply chain',
     'industrial manufacturing technology energy supply chains official announcements',
     'If the reported changes alter production costs or demand, they could change hiring, supplier choices and the skills employers value. Watch actual investment, orders and hiring rather than assuming an announcement has already changed jobs.'),
    ('Politics and geopolitics', r'politic|geopolitic|government|election|conflict',
     'geopolitics politics trade sanctions conflict official government United Nations updates',
     'If a reported policy or conflict changes trade, transport or energy access, its effects could spread through prices and business confidence. A proposal, a signed policy and implementation are different stages; watch which stage the evidence establishes.'),
    ('Finance and the economy', r'financ|econom|markets?|inflation|interest rates',
     'economy finance inflation interest rates employment central bank official statistical releases',
     'If inflation, employment or financing conditions change, household costs, borrowing and business demand could change too. Your exposure depends on your income, debts and spending; this evidence alone does not justify a trade or a promised return.'),
    ('Natural events and climate', r'natural|climate|disaster|earthquake|storm|flood|wildfire',
     'natural disasters storms earthquakes climate official USGS NOAA WMO emergency updates',
     'If an event affects a place where you live, work or obtain supplies, disruptions could affect safety, travel, deliveries and costs. Check the responsible agency for current location and alerts; a recent report does not prove an event is still active.'),
)


# A "what is going on now" update whose newest dated source is older than this
# is background, not news; it is labelled rather than presented as current.
STALE_DAYS = 7
SOURCES_LISTED = 5


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def source_freshness(sources, now):
    """Per-source publication/retrieval lines plus a stale-data warning."""
    lines, newest = ['Sources checked:'], None
    for source in sources[:SOURCES_LISTED]:
        published, updated, retrieved = (_when(source.published_at), _when(source.updated_at),
                                         _when(source.retrieved_at))
        dated = max((moment for moment in (published, updated) if moment), default=None)
        parts = [f'published {published:%Y-%m-%d}' if published else 'publication date not stated']
        if updated and (not published or updated.date() != published.date()):
            parts.append(f'updated {updated:%Y-%m-%d}')
        parts.append(f'retrieved {retrieved.astimezone(UTC):%Y-%m-%d %H:%M} UTC' if retrieved else 'retrieval time unknown')
        if dated:
            age = (now - dated).days
            parts.append('from today' if age <= 0 else f'{age} day{"s" if age != 1 else ""} old')
        lines.append(f'- {source.domain or source.final_url}: ' + '; '.join(parts))
    for source in sources:
        for moment in (_when(source.published_at), _when(source.updated_at)):
            if moment and (newest is None or moment > newest):
                newest = moment
    if len(sources) > SOURCES_LISTED:
        lines.append(f'- and {len(sources) - SOURCES_LISTED} more')
    stale = None
    if newest and (now - newest).days > STALE_DAYS:
        stale = (f'Stale data: the newest dated source here is from {newest:%Y-%m-%d} ({(now - newest).days} days old); '
                 'treat it as background, not as what is happening now.')
    return lines, stale


def conflicts(claims):
    """Name every claim the sources disagree on instead of stating one side."""
    lines = []
    for claim in claims:
        if claim.status is ClaimStatus.DISPUTED or claim.contradictions:
            text = ' '.join(claim.text.split())
            text = text if len(text) <= 160 else text[:157].rstrip() + '...'
            lines.append(f'Conflict: sources disagree on "{text}" ({len(claim.evidence)} supporting, '
                         f'{len(claim.contradictions)} contradicting); neither side is settled here.')
    return lines


def requested_sectors(text: str):
    value = str(text or '')
    found = [sector for sector in SECTORS if re.search(sector[1], value, re.I)]
    # Require both broad coverage and an update/implication request. Plain
    # event listings, individual camera requests and historical essays stay
    # on their existing routes.
    current = re.search(r"(?i)\b(?:updates?|latest|current|today|news|going on|happening)\b", value)
    broad = re.search(r'(?i)\b(?:world|global|future|international)\b', value)
    local = re.search(r'(?i)\b(?:readme|repository|my files?|project files?|config/)\b', value)
    return found if len(found) >= 2 and current and broad and not local else []


def research_world(manager, query, mode, budget, *, official_only=False, remember=True, now=None):
    """Allocate one existing research budget across sectors, never four full budgets."""
    started = time.perf_counter()
    now = now or datetime.now(UTC)
    sectors = requested_sectors(query)
    sources, claims, errors, results, covered = [], [], [], [], []
    gaps, stale_sectors, disputed = [], [], 0
    metrics = ResearchMetrics()
    lines = [f'World update checked {datetime.now().astimezone().isoformat(timespec="minutes")}.',
             'Evidence and possible effects are separated below. Coverage is partial; source publication dates matter.']
    searches_left, pages_left, bytes_left = budget.searches, budget.pages, budget.max_bytes
    seconds_left = budget.max_seconds
    for index, (title, _pattern, subject, channel) in enumerate(sectors):
        check_cancelled()
        lines.extend(['', title])
        remaining = len(sectors) - index
        seconds = min(seconds_left, budget.max_seconds - (time.perf_counter() - started))
        if min(searches_left, pages_left, bytes_left) <= 0 or seconds <= 0:
            lines.append('Coverage gap: the shared research budget was exhausted before this sector could be checked.')
            gaps.append(title)
            continue
        allocation = replace(budget, searches=max(1, searches_left // remaining),
                             pages=max(1, pages_left // remaining), max_bytes=max(1, bytes_left // remaining),
                             max_seconds=seconds / remaining)
        searches_left -= allocation.searches
        pages_left -= allocation.pages
        bytes_left -= allocation.max_bytes
        seconds_left -= allocation.max_seconds
        # Verify concrete updates first. Conditional explanation below does
        # not need another model call for each sector's acquisition phase.
        dated = f'{datetime.now().astimezone():%Y-%m-%d} current {subject}'
        try:
            result = manager.research(dated, level=mode, official_only=official_only, remember=remember,
                                      _budget=allocation)
        except Exception as exc:
            from app.models.streaming import GenerationCancelled
            if isinstance(exc, GenerationCancelled):
                raise
            errors.append(f'{title}: {type(exc).__name__}: {exc}')
            lines.append('Coverage gap: this sector could not be checked; no current facts are inferred from model memory.')
            gaps.append(title)
            continue
        results.append(result)
        errors.extend(result.errors)
        for field in metrics.__slots__:
            setattr(metrics, field, getattr(metrics, field) + getattr(result.metrics, field))
        supported = [c for c in result.claims if c.status in {
            ClaimStatus.SUPPORTED, ClaimStatus.PARTIALLY_SUPPORTED, ClaimStatus.DISPUTED} and c.evidence]
        if not supported or not result.sources:
            lines.append('Coverage gap: no source-supported current claim was established for this sector.')
            gaps.append(title)
            continue
        sources.extend(s for s in result.sources if s.final_url not in {old.final_url for old in sources})
        claims.extend(supported)
        covered.append(title)
        # Keep the verified research answer, including its uncertainty and
        # sources. Do not promote feed headline confidence to verified facts.
        lines.append(result.answer)
        lines.append('Possible effects (conditional interpretation, not a forecast): ' + channel)
        listed, stale = source_freshness(result.sources, now)
        lines.extend(listed)
        if not any(s.published_at or s.updated_at for s in result.sources):
            lines.append('Freshness gap: these pages were retrieved now, but their publication/update dates were not established.')
        if stale:
            lines.append(stale)
            stale_sectors.append(title)
        disagreements = conflicts(result.claims)
        disputed += len(disagreements)
        lines.extend(disagreements)
    summary = f'Coverage: {len(covered)} of {len(sectors)} sectors checked with sources'
    summary += f'; gaps: {", ".join(gaps)}.' if gaps else '.'
    if stale_sectors:
        summary += f' Stale (newest source over {STALE_DAYS} days old): {", ".join(stale_sectors)}.'
    if disputed:
        summary += f' {disputed} claim{"s" if disputed != 1 else ""} disputed between sources.'
    lines.extend(['', summary])
    lines.extend(['', 'How this could affect your future',
                  'Your location, occupation, financial exposure and time horizon are not established by these reports. '
                  'Use the checked developments to review job/skill demand, essential spending, debt exposure and local safety alerts; '
                  'personal effects remain conditional on those details.',
                  'Near term: watch implemented policies, official alerts and actual price/employment releases. '
                  'Over months: watch whether investment, supply chains and borrowing conditions change. '
                  'Over years: compare repeated evidence of structural change rather than extrapolating one headline.',
                  'Cross-sector scenario: if policy or disaster disruptions constrain supply while financing becomes harder, '
                  'costs and hiring could be affected together. If disruptions ease or productivity improves, that scenario may not occur. '
                  'These are transmission channels to monitor, not observed future outcomes or numeric odds.'])
    metrics.total_ms = int((time.perf_counter() - started) * 1000)
    return ResearchResult('world-' + (results[0].run_id if results else str(time.time_ns())), query,
                          '\n\n'.join(lines), sources, claims, [s.final_url for s in sources],
                          min((r.confidence for r in results), default=0),
                          len(covered) == len(sectors) and all(r.complete for r in results),
                          'sector_evidence_with_explicit_gaps', metrics, errors=errors, mode=mode,
                          trace={'world_brief': True, 'sectors_requested': [s[0] for s in sectors],
                                 'sectors_checked': len(results), 'budget_shared': True,
                                 'coverage_gaps': gaps, 'stale_sectors': stale_sectors,
                                 'disputed_claims': disputed})
