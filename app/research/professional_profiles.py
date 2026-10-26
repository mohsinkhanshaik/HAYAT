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
    # This is an explicit projection of API fields, not a scraped sentence. Professional fields only: never the
    # location, email or social handles the profile may also carry.
    def clean(value, limit=160):
        text = ' '.join(str(value or '').split())[:limit]
        return '' if not text or PRIVATE.search(text) else text

    company, bio, website = clean(data.get('company'), 80), clean(data.get('bio')), clean(data.get('blog'), 120)
    followers = data.get('followers') if type(data.get('followers')) is int else None
    since = str(data.get('created_at') or '')[:4]
    top = []
    try:
        repositories = fetcher.get_json(endpoint + '/repos?per_page=100&type=owner&sort=updated')
        if isinstance(repositories, list):
            owned = [repo for repo in repositories if isinstance(repo, dict) and not repo.get('fork')
                     and type(repo.get('stargazers_count')) is int]
            for repo in sorted(owned, key=lambda item: -item['stargazers_count'])[:3]:
                top.append({'name': clean(repo.get('name'), 80), 'stars': repo['stargazers_count'],
                            'description': clean(repo.get('description'), 120)})
    except Exception:  # repositories are a bonus; the profile itself already answered
        top = []
    projection = {'login': username, 'display_name': display, 'public_repositories': count, 'company': company,
                  'bio': bio, 'website': website, 'followers': followers, 'account_since': since, 'top_repositories': top}
    normalized = json.dumps(projection, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    statement = f'The public GitHub profile named {display} (login {username}) reports {count} public repositories.'
    details = [f'Company: {company}' if company else '', f'Bio: {bio}' if bio else '', f'Website: {website}' if website else '',
               f'Followers: {followers:,}' if followers is not None else '', f'On GitHub since {since}' if since else '']
    if top:
        details.append('Most-starred repositories: ' + '; '.join(
            f"{repo['name']} ({repo['stars']:,} stars)" + (f" — {repo['description']}" if repo['description'] else '') for repo in top))
    if any(details):
        statement += '\n' + '\n'.join(line for line in details if line)
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
