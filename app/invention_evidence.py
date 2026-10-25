"""Offline provenance and validation-readiness audit for invention snapshots.

This inspects recorded evidence, never fetches a URL, and never promotes a
model opinion or a fitted formula to physical validation or patent novelty.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, urlsplit, urlunsplit


def _text(value, limit=800):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def _items(value, limit):
    return value[:limit] if isinstance(value, list) else []


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return bool(parsed.tzinfo and parsed.utcoffset() is not None)
    except (AttributeError, ValueError, TypeError):
        return False


def public_source_url(value):
    """Syntactic public-URL filter only; opening still needs DNS/route checks."""
    if not isinstance(value, str) or len(value) > 2048 or re.search(r'[\s\\<>"`{}|\x00-\x1f\x7f]', value):
        return ''
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').rstrip('.').lower()
        if (parts.scheme.lower() not in {'https', 'http'} or parts.username is not None
                or parts.password is not None or not host or parts.port not in {None, 80, 443}):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            if ('.' not in host or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid', '.onion'))
                    or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)):
                return ''
            if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.')):
                return ''
        sensitive = {'token', 'access_token', 'api_key', 'apikey', 'password', 'secret', 'authorization', 'signature'}
        if any(key.casefold() in sensitive for key, _ in parse_qsl(parts.query)):
            return ''
        netloc = f'[{host}]' if ':' in host else host
        if parts.port is not None and parts.port != (443 if parts.scheme.lower() == 'https' else 80):
            netloc += f':{parts.port}'
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or '/', parts.query, ''))
    except ValueError:
        return ''


def _contract(data):
    try:
        tests = data['tests']
        if not isinstance(tests, list):
            return 'unavailable'
        tolerance = Decimal(str(data.get('tolerance', 0) or 0))
        if not tolerance.is_finite() or not 0 <= tolerance <= Decimal('0.5'):
            return 'changed'
        payload = {'tests': tests, 'tolerance': str(tolerance)} if tolerance else tests
        actual = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return 'intact' if actual == data.get('acceptance_hash') else 'changed'
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return 'unavailable'


def audit_project(data):
    """Build a reproducible, bounded audit from the stored project alone."""
    research = data.get('research') if isinstance(data.get('research'), dict) else {}
    raw_sources, raw_claims = research.get('sources'), research.get('claims')
    sources, by_url, documents, publishers = [], {}, set(), set()
    excluded = []
    for index, source in enumerate(_items(raw_sources, 128), 1):
        if not isinstance(source, dict):
            excluded.append({'source': index, 'reason': 'Malformed source record'})
            continue
        metadata = source.get('metadata') if isinstance(source.get('metadata'), dict) else {}
        defense = metadata.get('knowledge_poisoning_defense') or {}
        quarantined = ('quarantin' in str(source.get('trust', '')).casefold()
                       or isinstance(defense, dict) and defense.get('quarantine') is True)
        aliases = {public_source_url(source.get(key)) for key in ('url', 'final_url', 'canonical_url')} - {''}
        chosen = public_source_url(source.get('final_url') or source.get('url'))
        digest = source.get('content_hash')
        status = source.get('status_code')
        reason = ('Quarantined source' if quarantined else 'Missing or unsafe source URL' if not chosen
                  else 'No recorded successful retrieval' if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status < 300
                  else 'Missing retrieval timestamp' if not _timestamp(source.get('retrieved_at'))
                  else 'Missing SHA-256 content fingerprint' if not isinstance(digest, str) or not re.fullmatch('[a-fA-F0-9]{64}', digest)
                  else '')
        if reason:
            excluded.append({'source': index, 'reason': reason})
            continue
        digest = digest.lower()
        row = {'url': chosen, 'title': _text(source.get('title'), 240),
               'retrieved_at': source['retrieved_at'], 'content_hash': digest,
               'primary_reported': source.get('primary') is True}
        if not any(saved['url'] == chosen and saved['content_hash'] == digest for saved in sources):
            sources.append(row)
        documents.add(digest)
        publishers.add(urlsplit(chosen).hostname)
        for alias in aliases:
            by_url.setdefault(alias, {})[digest] = source

    claims, traceable_claims, disputed = [], 0, 0
    for claim in _items(raw_claims, 256):
        if not isinstance(claim, dict):
            continue
        citations, rejected, contradictions = [], 0, 0
        for field in ('evidence', 'contradictions'):
            for item in _items(claim.get(field), 16):
                if not isinstance(item, dict):
                    rejected += 1
                    continue
                url = public_source_url(item.get('source_url'))
                digest = item.get('source_hash')
                digest = digest.lower() if isinstance(digest, str) else ''
                source = by_url.get(url, {}).get(digest)
                raw_excerpt = item.get('excerpt')
                excerpt = _text(raw_excerpt, 8000)
                if (not source or not excerpt or not _timestamp(item.get('retrieved_at'))
                        or not isinstance(raw_excerpt, str) or len(raw_excerpt) > 8000
                        or datetime.fromisoformat(item['retrieved_at'].replace('Z', '+00:00'))
                        != datetime.fromisoformat(source['retrieved_at'].replace('Z', '+00:00'))
                        or item.get('claim_id', claim.get('claim_id')) != claim.get('claim_id')):
                    rejected += 1
                    continue
                # Source hashes describe downloaded bytes, not extracted text.
                # When text is present, check the excerpt separately; without it
                # only provenance can be checked, not the assertion's truth.
                source_text = source.get('text')
                excerpt_checked = isinstance(source_text, str)
                if excerpt_checked and excerpt not in ' '.join(source_text.split()):
                    rejected += 1
                    continue
                contradiction = field == 'contradictions' or _text(item.get('stance'), 40) in {'opposing', 'contradicting', 'contradiction'}
                contradictions += int(contradiction)
                citation = {'url': url, 'source_hash': digest, 'retrieved_at': item['retrieved_at'],
                            'excerpt': excerpt[:1600], 'excerpt_checked': excerpt_checked,
                            'stance': 'contradicting' if contradiction else 'reported supporting',
                            'support_type_reported': _text(item.get('support_type'), 40) or 'unknown'}
                if citation not in citations:
                    citations.append(citation)
        status = _text(claim.get('status'), 80) or 'UNVERIFIED'
        has_conflict = bool(contradictions or _items(claim.get('contradictions'), 16) or status == 'DISPUTED')
        disputed += int(has_conflict)
        traceable_claims += int(bool(citations))
        claims.append({'claim': _text(claim.get('text')), 'reported_status': status,
                       'provenance': 'traceable reported citations' if citations else 'untraceable',
                       'citations': citations, 'rejected_citations': rejected, 'has_conflict': has_conflict})

    best = data.get('best') if isinstance(data.get('best'), dict) else {}
    candidate = best.get('candidate') if isinstance(best.get('candidate'), dict) else {}
    measurement = best.get('measurement') if isinstance(best.get('measurement'), dict) else {}
    tests = data.get('tests') if isinstance(data.get('tests'), list) else []
    contract = _contract(data)
    total, passed = measurement.get('total'), measurement.get('passed')
    numeric_pass = (contract == 'intact' and bool(tests) and type(total) is int and type(passed) is int
                    and total == len(tests) and passed == total)
    missing = [key for key in ('hypothesis', 'design') if not _text(candidate.get(key))]
    if not _items(candidate.get('assumptions'), 16):
        missing.append('assumptions')
    if not _items(candidate.get('next_tests'), 16):
        missing.append('next_tests')
    stage = ('CONTRACT_CHANGED' if contract != 'intact' else 'DRAFT' if not best
             else 'NUMERIC_CASES_PASSED' if numeric_pass else 'NUMERIC_CASES_FAILED' if tests else 'PROPOSAL_REQUIRES_EXPERIMENTS')
    gaps = [_text(value) for value in _items(research.get('knowledge_gaps'), 32) if _text(value)]
    gaps += [_text(value) for value in _items(research.get('errors'), 16) if _text(value)]
    if _text(research.get('error')):
        gaps.append(_text(research['error']))
    if research.get('complete') is not True:
        gaps.append('Research is absent or incomplete.')
    if not sources:
        gaps.append('No source has a usable recorded URL, retrieval time and content fingerprint.')
    if disputed:
        gaps.append(f'{disputed} claim(s) have reported conflicting evidence; resolve before relying on them.')
    if len(claims) > traceable_claims:
        gaps.append(f'{len(claims) - traceable_claims} claim(s) have no traceable recorded citation.')
    rejected_citations = sum(claim['rejected_citations'] for claim in claims)
    if rejected_citations:
        gaps.append(f'{rejected_citations} citation(s) did not match usable recorded source provenance or text.')
    if missing:
        gaps.append('Candidate needs: ' + ', '.join(missing) + '.')
    constraints = [{'constraint': _text(value, 1000), 'status': 'not measured',
                    'next_action': 'Define a measurable threshold and record an independent prototype test.'}
                   for value in _items(data.get('constraints'), 16) if _text(value)]
    experiments = [{'kind': 'holdout', 'action': 'Test genuinely new input groups not used for fitting; retain expected values before fitting.'}]
    if not tests:
        experiments = [{'kind': 'acceptance', 'action': 'Define observable success and failure criteria before testing a prototype.'}]
    experiments += [{'kind': 'physical', 'action': 'Build an authorized prototype; measure units, operating limits, uncertainty and repeated trials against a baseline.'},
                    {'kind': 'constraints', 'action': 'Measure each cost, performance and safety constraint separately.'},
                    {'kind': 'prior_art', 'action': 'Compare the design with dated publications and patent claims; record differences. Search alone does not establish novelty.'}]
    experiments += [{'kind': 'candidate_test', 'action': _text(value)} for value in _items(candidate.get('next_tests'), 16) if _text(value)]
    limited = (isinstance(raw_sources, list) and len(raw_sources) > 128 or isinstance(raw_claims, list) and len(raw_claims) > 256
               or any(isinstance(claim, dict) and any(isinstance(claim.get(field), list) and len(claim[field]) > 16
                                                     for field in ('evidence', 'contradictions')) for claim in _items(raw_claims, 256)))
    return {'version': 1, 'stage': stage, 'contract': contract, 'numeric_cases_passed': numeric_pass,
            'physical_validation_verified': False, 'novelty_verified': False,
            'sources': sources, 'source_count': len(sources), 'distinct_document_hashes': len(documents),
            'publisher_hosts': len(publishers), 'reported_primary_sources': sum(source['primary_reported'] for source in sources),
            'excluded_sources': excluded, 'claims': claims, 'traceable_claims': traceable_claims,
            'conflicting_claims': disputed, 'constraints': constraints, 'missing_candidate_fields': missing,
            'next_experiments': experiments, 'gaps': list(dict.fromkeys(gaps)), 'audit_limited': bool(limited),
            'scope': 'Offline audit of recorded provenance; no fresh source retrieval, independent fact verification, physical experiment or patentability assessment. Publisher hosts do not establish source independence.'}


def _markdown(value):
    value = _text(value, 1600)
    return re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', value)


def audit_markdown(audit):
    lines = ['## Evidence and validation readiness', '',
             f"Stage: **{audit['stage']}** · acceptance contract: **{audit['contract']}**", '',
             audit['scope'], '', f"Recorded usable sources: {audit['source_count']} · distinct document fingerprints: {audit['distinct_document_hashes']} · publisher hosts: {audit['publisher_hosts']}",
             f"Claims with traceable reported citations: {audit['traceable_claims']} · claims with conflicts: {audit['conflicting_claims']}",
             'Physical validation: unverified. Novelty: unverified.', '']
    if audit['audit_limited']:
        lines += ['Audit bounds reached; additional records were not assessed.', '']
    if audit['gaps']:
        lines += ['### Evidence gaps', *['- ' + _markdown(gap) for gap in audit['gaps']], '']
    if audit['sources']:
        lines += ['### Recorded source provenance']
        for source in audit['sources']:
            lines += [f"- {_markdown(source['title']) or 'Source'}: <{source['url']}> · retrieved {_markdown(source['retrieved_at'])} · SHA-256 `{source['content_hash']}`"]
        lines.append('')
    if audit['claims']:
        lines += ['### Recorded claims (reported status, not independent verification)']
        for claim in audit['claims'][:64]:
            lines += [f"- {_markdown(claim['claim'])} · {_markdown(claim['reported_status'])} · {claim['provenance']}"
                      + (' · **conflict reported**' if claim['has_conflict'] else '')]
            for citation in claim['citations'][:4]:
                lines += [f"  - <{citation['url']}> · {citation['stance']} · excerpt "
                          + ('matched stored text' if citation['excerpt_checked'] else 'not rechecked: source text unavailable')
                          + f": {_markdown(citation['excerpt'])}"]
        if len(audit['claims']) > 64 or any(len(claim['citations']) > 4 for claim in audit['claims']):
            lines += ['Additional audited claim details are available in the structured audit.']
        lines.append('')
    if audit['constraints']:
        lines += ['### Constraint checks', *[f"- {_markdown(row['constraint'])}: **not measured**. {row['next_action']}" for row in audit['constraints']], '']
    lines += ['### Next experiments', *[f"- {_markdown(item['action'])}" for item in audit['next_experiments']], '']
    return '\n'.join(lines)
