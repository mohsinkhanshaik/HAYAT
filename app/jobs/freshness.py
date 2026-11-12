"""Current listing evidence, independent of a cached fit score."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.jobs.outcomes import timestamp

EXPIRY_REASON = 'Publisher listing expiry has passed; verify a current requisition with the employer'


def listing_evidence(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    measured = now or datetime.now(UTC)
    expiry, seen = timestamp(row.get('expires_at')), timestamp(row.get('last_seen'))
    age = (measured - seen).total_seconds() if seen is not None else None
    state = ('expired' if expiry is not None and expiry <= measured else
             'unknown' if age is None or age < 0 else 'stale' if age > 7 * 86400 else 'recently_seen')
    return {'state': state, 'last_seen': row.get('last_seen') or '', 'expires_at': row.get('expires_at') or '',
            'age_days': round(age / 86400, 1) if age is not None and age >= 0 else None,
            'basis': 'Last seen means observed in a public source, not verified open with the employer. '
                     'Check the employer’s current posting before applying.'}


def current_listing(row: dict[str, Any]) -> dict[str, Any]:
    """Adjust a loaded snapshot when its publisher deadline passed since scoring."""
    evidence = listing_evidence(row)
    row['listing_evidence'] = evidence
    if evidence['state'] == 'expired' and isinstance(row.get('match'), dict):
        match = row['match']
        match['blocked'] = list(dict.fromkeys([*(match.get('blocked') or []), EXPIRY_REASON]))
        row['score'] = match['score'] = min(int(row.get('score') or 0), 25)
        row['verdict'] = match['verdict'] = 'not eligible'
    return row
