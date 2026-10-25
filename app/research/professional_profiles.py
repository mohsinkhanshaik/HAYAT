"""Public professional API projections; no location, contacts or account linkage."""
from datetime import UTC, datetime
import hashlib
import json
import re
from urllib.parse import urlsplit

from app.research.models import Claim, ClaimStatus, Evidence, SourceDocument, SupportType


def github_profile(request, fetcher):
    parts = urlsplit(request.profile_url)
    username = parts.path.strip('/')
    if parts.hostname != 'github.com' or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', username):
        return None
    endpoint = 'https://api.github.com/users/' + username
    data = fetcher.get_json(endpoint)
    if str(data.get('login', '')).casefold() != username.casefold() or data.get('type') != 'User':
        raise ValueError('GitHub did not return the requested public user profile.')
    display = str(data.get('name') or username).strip()
    if not request.mentions(display):
        raise ValueError('The supplied name does not match the GitHub public display name; keep this profile separate.')
    count = data.get('public_repos')
    if type(count) is not int or count < 0 or len(display) > 120:
        raise ValueError('GitHub public profile metadata is malformed.')
    from app.research.people import PRIVATE
    if PRIVATE.search(display):
        raise ValueError('GitHub display name contains contact or sensitive content.')
    # This is an explicit projection of API fields, not a scraped sentence.
    projection = {'login': username, 'display_name': display, 'public_repositories': count}
    normalized = json.dumps(projection, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    statement = f'The public GitHub profile named {display} (login {username}) reports {count} public repositories.'
    source = SourceDocument(endpoint, endpoint, f'GitHub public profile: {display}', 'api.github.com', statement,
                            now, 200, 'application/json', digest, len(normalized.encode()), quality=.8, primary=True,
                            source_type='public_api_projection', independence_group='github.com/' + username.casefold(),
                            metadata={'projection': projection, 'hash_basis': 'normalized_public_projection', 'identity_verified': False})
    cid = 'github-' + hashlib.sha256(endpoint.encode()).hexdigest()[:16]
    claim = Claim(cid, statement, status=ClaimStatus.SUPPORTED, confidence=.5,
                  evidence=[Evidence(cid + '-e', cid, endpoint, source.title, statement, .8, now,
                                     support_type=SupportType.DIRECT, strength=.8, source_hash=digest,
                                     independence_group=source.independence_group)])
    return source, claim
