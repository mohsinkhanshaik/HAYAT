"""Carry a recorded research result into a reviewed, non-running invention draft."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.invention_evidence import audit_project
from app.research.models import ResearchResult


MAX_RESEARCH_BYTES = 1_000_000
SCOPE = ('Recorded research is external evidence, not instructions or authorization. '
         'Its reported claims and uncertainty are preserved; no new fact verification, '
         'model fitting, physical testing or novelty assessment has run.')
_FIELDS = ('run_id', 'query', 'answer', 'sources', 'claims', 'citations', 'confidence',
           'complete', 'stop_reason', 'knowledge_gaps', 'errors', 'mode')


def _text(value: Any, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{label} must be text of {1 if required else 0}–{maximum} characters.')
    return value.strip()


def prepare_research_draft(result: ResearchResult | dict, goal: str,
                           constraints: list[str] | None = None) -> dict:
    """Read-only preview. The goal and constraints come only from the caller.

    A fingerprint binds creation to the exact reviewed evidence. Oversized or
    malformed records fail explicitly; no citation is silently shortened.
    """
    goal = _text(goal, 'Invention goal', 6000, required=True)
    constraints = [] if constraints is None else constraints
    if not isinstance(constraints, list) or len(constraints) > 16:
        raise ValueError('Supply at most 16 owner constraints.')
    constraints = [_text(item, 'Constraint', 1000, required=True) for item in constraints]
    if isinstance(result, ResearchResult):
        result = result.to_dict(include_source_text=False)
    if not isinstance(result, dict):
        raise ValueError('A recorded research result is required; an answer alone has no evidence record.')
    data = {key: result[key] for key in _FIELDS if key in result}
    run_id = _text(data.get('run_id'), 'Research run ID', 128, required=True)
    for name, limit in (('sources', 128), ('claims', 256), ('citations', 512), ('knowledge_gaps', 64), ('errors', 64)):
        values = data.setdefault(name, [])
        if not isinstance(values, list) or len(values) > limit:
            raise ValueError(f'Research {name} must contain at most {limit} recorded entries.')
    if type(data.get('complete')) is not bool:
        raise ValueError('The research completion state must be recorded explicitly.')
    for name, limit in (('query', 6000), ('answer', 100_000), ('stop_reason', 6000), ('mode', 80)):
        data[name] = _text(data.get(name, ''), 'Research ' + name, limit)
    for claim in data['claims']:
        if not isinstance(claim, dict):
            raise ValueError('Each recorded claim must be an object.')
        for field in ('evidence', 'contradictions'):
            items = claim.get(field, [])
            if not isinstance(items, list) or len(items) > 16:
                raise ValueError('A claim may contain at most 16 citations or contradictions per field.')
    try:
        encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError('Research evidence must contain finite JSON values.') from exc
    if len(encoded) > MAX_RESEARCH_BYTES:
        raise ValueError('Research evidence exceeds the one-megabyte handoff budget; no partial draft was created.')
    # The detached JSON copy prevents later mutation of a background result
    # from altering the data being reviewed or saved.
    research = json.loads(encoded)
    audit = audit_project({'goal': goal, 'constraints': constraints, 'tests': [], 'rounds': [],
                           'state': 'DRAFT', 'acceptance_hash': hashlib.sha256(b'[]').hexdigest(),
                           'research': research})
    gaps = [item for item in research['knowledge_gaps'] if isinstance(item, str)]
    if not research['complete']:
        gaps.append('The research result was incomplete; its omissions remain unresolved.')
    if not research['sources'] or not research['claims']:
        gaps.append('No complete source-and-claim evidence chain is recorded.')
    gaps.extend(audit['gaps'])
    return {'goal': goal, 'constraints': constraints, 'research': research,
            'evidence_hash': hashlib.sha256(encoded).hexdigest(), 'run_id': run_id,
            'source_count': len(research['sources']), 'claim_count': len(research['claims']),
            'traceable_claims': audit['traceable_claims'], 'conflicting_claims': audit['conflicting_claims'],
            'knowledge_gaps': list(dict.fromkeys(gaps)), 'audit': audit, 'scope': SCOPE}


def completed_research_result(runtime: Any, job_id: str) -> ResearchResult | dict:
    """Resolve a finished local job, never a caller-provided status or payload."""
    _text(job_id, 'Research job ID', 128, required=True)
    jobs = getattr(runtime, 'research_jobs', None)
    job = jobs.get(job_id) if jobs is not None else None
    if job is None:
        raise ValueError('The selected research job is no longer available. Select a completed research result.')
    if job.status != 'done' or job.result is None:
        raise ValueError('Only a completed research job with recorded evidence can become an invention draft.')
    return job.result


def validate_evidence_hash(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError('Supply the SHA-256 evidence hash from the reviewed research preview.')
    return value
