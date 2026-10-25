"""Inspectable evidence reports. Identity resolution is always separate from claim support."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from app.research.citation_validator import CitationValidator
from app.research.models import ClaimStatus
from app.research.people import PRIVATE, public_work_claim
from app.research.deduplication import canonicalize_url


def build_report(result, context=''):
    """Accept only retrieved, hash/excerpt-validated evidence; retain conflicts."""
    sources, by_url = [], {}
    words = set(re.findall(r'\w{3,}', context.casefold()))
    for source in result.sources:
        # An HTML canonical tag is publisher-controlled and can point at an
        # unrelated person's page. Only the actually retrieved URL deduplicates.
        canonical = canonicalize_url(source.final_url)
        if canonical not in by_url:
            sid = 'src-' + hashlib.sha256(canonical.encode()).hexdigest()[:16]
            sources.append({'id': sid, 'url': source.final_url, 'canonical_url': canonical,
                            'title': source.title, 'retrieved_at': source.retrieved_at,
                            'published_at': source.published_at, 'hash': source.content_hash,
                            'publisher': source.domain, 'independence_group': source.independence_group or source.domain,
                            'context_matches': sorted(words & set(re.findall(r'\w{3,}', (source.title + ' ' + source.text).casefold()))),
                            'match_status': 'plausible_source_candidate', 'identity_verified': False})
            by_url[canonical] = sources[-1]
        by_url[source.final_url] = by_url[canonical]
    claims, timeline = [], []
    validator = CitationValidator()
    for original in result.claims:
        if not public_work_claim(original.text):
            continue
        claim = deepcopy(original)
        validations = validator.validate_claim(claim, result.sources)
        evidence = []
        for item, check in zip(claim.evidence, validations):
            source = by_url.get(item.source_url)
            if check.valid and source and not PRIVATE.search(item.excerpt):
                evidence.append({'source_id': source['id'], 'url': item.source_url, 'excerpt': item.excerpt,
                                 'retrieved_at': item.retrieved_at, 'support_type': check.support_type.value})
        if not evidence:
            continue
        disputed = original.status == ClaimStatus.DISPUTED or bool(original.contradictions)
        conflicts = []
        for contrary in original.contradictions:
            source = next((s for s in result.sources if s.final_url == contrary.source_url), None)
            if source and contrary.excerpt and not PRIVATE.search(contrary.excerpt) and contrary.excerpt in source.text and (not contrary.source_hash or contrary.source_hash == source.content_hash):
                conflicts.append({'source_id': by_url[source.final_url]['id'], 'url': contrary.source_url,
                                  'excerpt': contrary.excerpt, 'retrieved_at': contrary.retrieved_at})
        entry = {'id': claim.claim_id, 'text': claim.text,
                 'status': 'conflicting_evidence' if disputed else 'source_supported',
                 'confidence': round(min(.6, original.confidence), 3),
                 'confidence_basis': 'Evidence support, not probability of identity; capped at 0.6.',
                 'evidence': evidence, 'contradictions': conflicts}
        claims.append(entry)
        for year in sorted(set(re.findall(r'\b(?:19|20)\d{2}\b', claim.text))):
            timeline.append({'year': int(year), 'claim_id': claim.claim_id, 'text': claim.text,
                             'source_ids': sorted({e['source_id'] for e in evidence}), 'status': entry['status']})
    nodes = [{'id': source['id'], 'label': source['title'], 'kind': 'candidate'} for source in sources]
    publishers = {s['publisher'] for s in sources}
    nodes.extend({'id': 'publisher:' + host, 'label': host, 'kind': 'publisher'} for host in sorted(publishers))
    edges = [{'from': 'publisher:' + s['publisher'], 'to': s['id'], 'relationship': 'publishes source',
              'url': s['url']} for s in sources]
    return {'schema_version': 1, 'run_id': result.run_id, 'query': result.query,
            'retrieved_at': datetime.now(UTC).isoformat(), 'identity_verified': False,
            'sources': sources, 'claims': claims, 'timeline': sorted(timeline, key=lambda item: item['year']),
            'graph': {'nodes': nodes, 'edges': edges}, 'scholarly': result.trace.get('scholarly', {}),
            'unknowns': list(dict.fromkeys([*result.knowledge_gaps, 'Whether separate source candidates describe the same person.',
                                          'Whether historical roles are current.'])),
            'provider_errors': list(result.errors), 'selected_candidates': [],
            'scope': 'Public professional work; source candidates remain separate. No private contact dossier.'}


def report_text(report):
    lines = [report['query'], 'Identity unverified. Source-supported claims do not establish identity.', '']
    for source in report['sources']:
        lines += [f"[{source['id']}] {source['title']}", source['url'], 'Retrieved: ' + source['retrieved_at'], '']
    for claim in report['claims']:
        lines += [claim['status'] + ': ' + claim['text']]
        for evidence in claim['evidence']:
            lines += ['  ' + evidence['excerpt'], f"  [{evidence['source_id']}] {evidence['url']}"]
        for contrary in claim['contradictions']:
            lines += ['  Contrary: ' + contrary['excerpt'], contrary['url']]
    lines += ['', 'Unknown / unresolved:', *report['unknowns'], '', 'Provider errors:', *report['provider_errors']]
    return '\n'.join(lines)


def export_report(report, path):
    """Explicit exports are plaintext, unlike the encrypted saved investigation."""
    path = Path(path)
    if path.suffix.casefold() == '.json':
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    elif path.suffix.casefold() == '.pdf':
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from xml.sax.saxutils import escape
        styles = getSampleStyleSheet()
        items = []
        for line in report_text(report).splitlines():
            items.append(Paragraph(escape(line), styles['BodyText']) if line else Spacer(1, 8))
        SimpleDocTemplate(str(path), title=report['query'], author='HAYAT').build(items)
    else:
        raise ValueError('Choose JSON or PDF.')


def profile_seed(value, profile_url='', get_json=None):
    """Resolve only an explicit public GitHub handle; no cross-account inference."""
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme and (parts.username or parts.password or parts.query or parts.fragment):
        raise ValueError('Use a public professional profile URL without credentials, query parameters or fragments.')
    url = profile_url or (value if parts.scheme else '')
    if value.startswith('@') or (parts.hostname == 'github.com' and len(parts.path.strip('/').split('/')) == 1):
        username = value[1:] if value.startswith('@') else parts.path.strip('/')
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username):
            raise ValueError('Use a valid GitHub username.')
        canonical = 'https://github.com/' + username
        if profile_url and profile_url.rstrip('/') != canonical:
            raise ValueError('The explicit profile URL conflicts with the supplied GitHub handle.')
        if get_json is None:
            raise RuntimeError('The public GitHub API adapter is unavailable.')
        data = get_json('https://api.github.com/users/' + username)
        name = str(data.get('name') or username).strip()
        return name, canonical
    if url and value == url:
        raise ValueError('For this public profile, supply the known name in the search bar and the URL in Profile.')
    return value, url


def contact_sources(manager, identifier, organization, *, publicly_listed=False):
    """Discover directory pages for an explicitly supplied business contact.

    The contact is a search seed only: never extract another person's contacts,
    infer identity or compile a biography from the result.
    """
    from app.research.people import context_reason, blocked_source
    if publicly_listed is not True:
        raise PermissionError('Confirm that this is an already publicly listed professional contact.')
    if not isinstance(organization, str) or not 3 <= len(organization.strip()) <= 240 or context_reason(organization):
        raise ValueError('Supply a public business or organization to constrain this lookup.')
    email = re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', identifier)
    phone = re.fullmatch(r'\+?[0-9 ()-]{7,30}', identifier)
    if not email and not (phone and 7 <= len(re.sub(r'\D', '', identifier)) <= 15):
        raise ValueError('Enter a publicly listed professional email or business phone number.')
    from app.research.models import ResearchBudget, ResearchMetrics, ResearchResult
    import time
    started, errors, metrics = time.perf_counter(), [], ResearchMetrics()
    query = f'"{identifier}" "{organization}" public professional directory'
    rows = manager.search.search(query, limit=5)
    rows = [row for row in rows if not blocked_source(row.url) and identifier.casefold() not in unquote(row.url).casefold()]
    docs = manager._retrieve_bounded(rows[:4], query, ResearchBudget(pages=4, max_seconds=30), started, metrics, errors,
                                     cache_sources=False)
    from dataclasses import replace
    digits = re.sub(r'\D', '', identifier)
    phone_pattern = r'(?<!\d)' + r'[\s().+-]*'.join(digits) + r'(?!\d)'
    sources = []
    for source in docs:
        content = source.text.casefold()
        seed_match = re.search(phone_pattern, content) if phone and not email else re.search(re.escape(identifier.casefold()), content)
        org_words = set(re.findall(r'\w{3,}', organization.casefold())) - {'the', 'and', 'for'}
        # A checkbox cannot turn a publisher-labelled personal contact into a
        # professional directory entry. Do not remove letters to match phones.
        nearby = content[max(0, seed_match.start()-100):seed_match.end()+40] if seed_match else ''
        private_contact = re.search(r'\b(?:private|personal|home)\s+(?:e-?mail|phone|mobile|contact)', nearby)
        if seed_match and not private_contact and org_words and org_words <= set(re.findall(r'\w{3,}', content)):
            sources.append(replace(source, text='', title='Public professional directory source', description='',
                                   headings=[], links=[], tables=[], metadata={'seed_match': True}, author=None))
    result = ResearchResult('contact-' + hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:16],
                            'Professional directory sources for ' + organization, '', sources, [], [], 0, bool(sources),
                            'source_discovery_only', metrics,
                            ['Supply a known name to investigate professional claims. A contact match does not establish identity.'], errors)
    report = build_report(result, organization)
    report['seed_mode'] = 'public_business_contact_source_discovery'
    return report
