"""Bounded discovery of source-attributed public professional contact routes.

No identity resolution, address brokers, guessed addresses, private social
profiles, phone harvesting, account login, contact storage or message sending.
Search snippets are leads only; every returned route comes from fetched HTML.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import hashlib
from itertools import islice
import logging
import re
import time
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from app.research.deduplication import canonicalize_url
from app.research.injection import assess_prompt_injection
from app.research.people import PersonRequest, blocked_source
from app.research.safety import validate_public_url


_log = logging.getLogger(__name__)
_CONTROLS: ContextVar[dict[str, Any]] = ContextVar('public_contact_controls', default={})
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?![\w.-])")
_PRIVATE = re.compile(r'(?i)\b(?:personal|private|home|residential)\s+(?:e-?mail|contact|address|phone)|\b(?:mobile|cellphone|whatsapp|date of birth|passport|social security)\b')
_WORK = re.compile(r'(?i)\b(?:professional|work|business|office|university|faculty|staff)\s+(?:e-?mail|contact)|\b(?:e-?mail|contact)\s+(?:for|at)\s+(?:work|business|office)\b')
_EMAIL_LABEL = re.compile(r'(?i)\b(?:e-?mail|contact)\b')
_TEAM = re.compile(r'(?i)\b(?:team|department|press|media|communications|admissions|recruitment|recruiting|careers|support|sales|enquiries|inquiries|chief of staff|assistant|secretary|representative|secretariat|general contact|contact us|contact the office)\b')
_ROUTE_LABEL = re.compile(r'(?i)\b(?:contact(?: us| the team)?|team contact|press office|media inquiries|general enquiries|admissions|support)\b')
_DIRECTORY_LABEL = re.compile(r'(?i)(?:our |meet (?:the |our ))?(?:people|team|staff|faculty|members|directory|staff directory|people directory|faculty directory)')
_CHALLENGE = re.compile(r'(?i)\b(?:captcha|verify (?:you are|that you are) human|access denied|authentication required|sign in to (?:view|continue)|log in to (?:view|continue))\b')
_SOCIAL = {'facebook.com', 'instagram.com', 'tiktok.com', 'snapchat.com', 'twitter.com', 'x.com',
           'threads.net', 'pinterest.com', 'reddit.com', 't.me', 'telegram.me', 'discord.com'}
_SECRET_QUERY = re.compile(r'(?i)(?:^|[_-])(?:password|passwd|secret|token|api[_-]?key|authorization|session|auth)(?:$|[_-])')
_SPARE_READS = 3  # failed or duplicate reads may be replaced, but never more than this many times
_RECORD_DUMPS = re.compile(r'(?i)\b(?:leaked (?:database|records|credentials|passwords)|credential dumps?|stolen (?:data|records)|dox(?:xing|bin)|breach (?:database|records|dump))\b')


class PublicContactCancelled(PermissionError):
    pass


class _RunBudgetExceeded(TimeoutError):
    pass


@contextmanager
def contact_controls(*, check=None, progress=None):
    """Bind job controls while the normal registry authorizes a public read."""
    token = _CONTROLS.set({'check': check, 'progress': progress})
    try:
        yield
    finally:
        _CONTROLS.reset(token)


def _same_host(first, second):
    return (urlsplit(first).hostname or '').casefold().removeprefix('www.') == (urlsplit(second).hostname or '').casefold().removeprefix('www.')


def _reference(url):
    """Pure preflight; the real PublicFetcher still validates DNS and redirects."""
    from urllib.parse import parse_qsl
    parts = urlsplit(url)
    host = (parts.hostname or '').casefold().rstrip('.')
    if (parts.scheme not in {'http', 'https'} or not host or parts.username or parts.password or len(url) > 2000
            or any(ord(char) < 33 for char in url) or '\\' in url or parts.port not in {None, 80, 443}
            or _EMAIL.search(unquote(url)) or blocked_source(url) or host.endswith('.onion')
            or any(host == domain or host.endswith('.' + domain) for domain in _SOCIAL)
            or any(_SECRET_QUERY.search(key) for key, _value in parse_qsl(parts.query))):
        raise ValueError('Use a public professional page without credentials, broker listings or personal-social links.')
    # Tor-mode validation performs a pure hostname/IP preflight without local
    # DNS. The actual fetcher still applies the current route's DNS boundary.
    return canonicalize_url(validate_public_url(url, via_tor=True))


def _normalized(value):
    return ' '.join(re.findall(r'[^\W_]+', value.casefold(), flags=re.UNICODE))


def _mentions(text, phrase):
    text, phrase = _normalized(text), _normalized(phrase)
    return bool(phrase and re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text))


def _text(node):
    return ' '.join(node.get_text(' ', strip=True).split())


def _local(node, limit=1000):
    """Keep a channel's nearest visible text, avoiding an unrelated directory row."""
    current = node
    for _ in range(4):
        parent = current.parent
        if not isinstance(parent, Tag) or parent.name in {'body', 'html'}:
            break
        if len(_text(parent)) > limit:
            break
        current = parent
        if parent.name in {'p', 'li', 'tr', 'dd', 'address'}:
            break
    return current


def _card(node, name):
    current = node
    for _ in range(7):
        if not isinstance(current, Tag) or current.name in {'body', 'html'}:
            break
        text = _text(current)
        if len(text) > 4000:
            break
        markers = ' '.join(current.get('class', [])) + ' ' + str(current.get('itemtype', ''))
        if (_mentions(text, name) and (current.name in {'article', 'li', 'tr', 'main'}
                or re.search(r'(?i)person|staff|profile|member|card', markers))):
            # Do not take an entire multi-person directory as one person's card.
            other_names = [heading for heading in current.find_all(['h1', 'h2', 'h3', 'h4'])
                           if not _mentions(_text(heading), name) and len(_text(heading).split()) in {2, 3, 4}
                           and not _ROUTE_LABEL.search(_text(heading))]
            if not other_names:
                return current
        current = current.parent
    return None


def _snippet(text, target, allowed_email=''):
    """A shortened/redacted context window, never a synthetic biographical fact."""
    index = text.casefold().find(target.casefold())
    if index < 0:
        index = 0
    text = text[max(0, index - 60):index + 300]
    redacted = False
    def redact_email(match):
        nonlocal redacted
        if allowed_email and match[0].casefold() == allowed_email.casefold():
            return match[0]
        redacted = True
        return '[other email omitted]'
    text = _EMAIL.sub(redact_email, text)
    if _PRIVATE.search(text):
        return 'Exact name and organization occur in the selected professional source; private context omitted.', True
    # Addresses and telephone strings are never part of these evidence snippets.
    cleaned = re.sub(r'(?:\+?\d[\s().-]*){7,}', '[number omitted]', text)
    cleaned = re.sub(r'(?i)\b\d{1,6}\s+[\w .-]{1,50}\s+(?:street|avenue|road|lane|drive|boulevard|st|ave|rd)\b', '[address omitted]', cleaned)
    return cleaned, redacted or cleaned != text


def _route(kind, value, source, context, *, scope='person', label=''):
    snippet, shortened = _snippet(context, value, value if kind == 'email' else '')
    label, label_redacted = _snippet(label, value, value if kind == 'email' else '')
    identity = f"{source['final_url']}\0{scope}\0{value}"
    return {'id': 'route-' + hashlib.sha256(identity.encode()).hexdigest()[:20],
            'kind': kind, 'value': value, 'status': 'public_professional', 'scope': scope,
            'source_url': source['final_url'], 'recorded_at': source['retrieved_at'],
            'retrieved_at': source['retrieved_at'], 'source_hash': source['sha256'],
            'snippet': snippet, 'snippet_redacted': shortened, 'label': label[:160], 'label_redacted': label_redacted}


def _extract(resource, name, organization, official_url):
    soup = BeautifulSoup(resource.content, 'html.parser')
    title = _text(soup.title) if soup.title else (urlsplit(resource.final_url).hostname or '')
    for node in soup.select('script,style,noscript,template,svg,form,[hidden],[aria-hidden="true"]'):
        node.decompose()
    for node in soup.select('[style]'):
        if re.search(r'(?i)(?:display\s*:\s*none|visibility\s*:\s*hidden)', str(node.get('style'))):
            node.decompose()
    visible = _text(soup)
    source = {'url': resource.requested_url, 'final_url': resource.final_url, 'title': title[:250],
              'retrieved_at': resource.retrieved_at, 'sha256': resource.sha256, 'status': resource.status_code,
              'accepted': False, 'reason': '', 'identity_verified': False}
    if _PRIVATE.search(source['title']) or _EMAIL.search(source['title']):
        source['title'] = 'Public professional directory source; identifying contact title omitted'
    else:
        source['title'] = _snippet(source['title'], name)[0][:250]
    if _CHALLENGE.search(visible[:2500]) or assess_prompt_injection(visible)['quarantine']:
        source['reason'] = 'Access challenge or quarantined external instructions; no contact evidence extracted.'
        return [], [], [], source
    if _RECORD_DUMPS.search(title + ' ' + visible[:4000]):
        source['reason'] = 'Leaked, stolen or breach-record sources are outside public professional contact discovery.'
        return [], [], [], source
    org_present = _mentions(visible[:100_000], organization)
    selected_origin = bool(official_url and _same_host(resource.final_url, official_url))
    name_headers = [node for node in soup.find_all('h1') if _mentions(_text(node), name)]
    individual = (len(soup.find_all('h1')) == 1 and len(name_headers) == 1 and org_present)
    # Only headings/title may establish a single-person page. A name in arbitrary prose cannot.
    if not individual and not soup.find('h1') and _mentions(title, name) and org_present:
        individual = bool(re.search(r'(?i)/(?:people|person|staff|faculty|profile|bio)(?:/|\b)', urlsplit(resource.final_url).path))
    candidate_contexts = []
    for node in soup.find_all(['h1', 'h2', 'h3', 'h4', 'strong', 'a'])[:1500]:
        if _mentions(_text(node), name):
            card = _card(node, name)
            if card is not None and org_present:
                context = _text(card)
                # On a selected organizational directory the masthead is organization evidence;
                # otherwise the actual bounded person card must name the organization.
                if _mentions(context, organization) or selected_origin:
                    candidate_contexts.append(context)
    if individual:
        candidate_contexts.append(visible[:100_000])
    candidate = None
    if candidate_contexts:
        shortest = min(candidate_contexts, key=len)
        snippet, redacted = _snippet(shortest, name)
        candidate = {'id': 'candidate-' + hashlib.sha256((resource.final_url + '\0' + name).encode()).hexdigest()[:20],
                     'name': name, 'organization': organization, 'source_url': resource.final_url,
                     'retrieved_at': resource.retrieved_at, 'source_hash': resource.sha256,
                     'snippet': snippet, 'snippet_redacted': redacted,
                     'evidence': {'exact_name': True, 'organization_on_source': True,
                                  'selected_organization_origin': selected_origin},
                     'evidence_status': 'source_candidate_not_identity_verified', 'identity_verified': False, 'channels': []}
    teams, follow = [], []
    for anchor in soup.select('a[href]')[:1500]:
        href, label = str(anchor.get('href') or '').strip(), _text(anchor)
        local = _text(_local(anchor))
        if _PRIVATE.search(local):
            continue
        card = _card(anchor, name)
        person_context = _text(card) if card is not None else ''
        person_near = bool(candidate and person_context and (_mentions(person_context, organization) or selected_origin))
        if person_near and card is not None and len(card.select('a[href^="mailto:"]')) > 1 and not _mentions(local, name):
            person_near = False  # Multiple addresses need an explicit local owner label, not page-level inference.
        if href.casefold().startswith('mailto:'):
            value = unquote(href[7:].split('?', 1)[0]).strip()
            if not _EMAIL.fullmatch(value) or not _EMAIL_LABEL.search(local):
                continue
            # A team/department/representative channel is never attributed to the named person.
            if _TEAM.search(local) and org_present and (_WORK.search(local) or selected_origin):
                route = _route('email', value, source, local, scope='team', label=label or 'Published team contact')
                route.update(team_name=organization + ' — team contact', organization=organization)
                teams.append(route)
            elif person_near and (_WORK.search(local) or (selected_origin and _EMAIL_LABEL.search(local))):
                candidate['channels'].append(_route('email', value, source, local, label=label))
            continue
        if not href or href.startswith('#'):
            continue
        try:
            target = _reference(urljoin(resource.final_url, href))
        except ValueError:
            continue
        if not _same_host(target, resource.final_url):
            continue
        if _ROUTE_LABEL.fullmatch(label.strip()) and org_present:
            if person_near and card is not None and _mentions(person_context, name):
                candidate['channels'].append(_route('website', target, source, local, label=label))
            elif selected_origin or _mentions(local, organization):
                route = _route('website', target, source, local, scope='team', label=label)
                route.update(team_name=organization + ' — ' + label, organization=organization)
                teams.append(route)
        # A generic "Contact" link on a third-party site (theorg.com/contact) is that site's own inbox, not the
        # organization's: follow it only on the organization's own or the selected site.
        if org_present and len(follow) < 12 and (_mentions(label, name)
                or (_ROUTE_LABEL.fullmatch(label.strip()) and (selected_origin or _org_host(organization, resource.final_url)))
                or (selected_origin and _DIRECTORY_LABEL.fullmatch(label.strip()))):
            follow.append(target)
    # Plain-text correspondence is common on official directories. Require
    # the same bounded professional/team labels as clickable mail links.
    for node in soup.find_all(['p', 'li', 'dd', 'address'])[:1500]:
        if node.find('a', href=re.compile(r'(?i)^mailto:')):
            continue
        local = _text(node)
        if len(local) > 1200 or _PRIVATE.search(local):
            continue
        previous = node.find_previous_sibling('dt') if node.name == 'dd' else None
        if previous is not None:
            local = _text(previous) + ' ' + local
        if not _EMAIL_LABEL.search(local):
            continue
        addresses = list(dict.fromkeys(_EMAIL.findall(local)))
        if len(addresses) != 1:
            continue
        value = addresses[0]
        if _TEAM.search(local) and org_present and (_WORK.search(local) or selected_origin):
            route = _route('email', value, source, local, scope='team', label='Published team correspondence')
            route.update(team_name=organization + ' — team contact', organization=organization)
            teams.append(route)
        elif candidate and _mentions(local, name) and _WORK.search(local):
            candidate['channels'].append(_route('email', value, source, local))
    if candidate:
        candidate['channels'] = list({(row['kind'], row['value']): row for row in candidate['channels']}.values())
    teams = list({(row['kind'], row['value']): row for row in teams}.values())
    source['accepted'] = bool(candidate or teams)
    source['reason'] = ('Source candidates and/or explicitly published organizational contact routes; identity remains unverified.'
                        if source['accepted'] else 'No bounded exact-name/organization professional record or labelled team route.')
    return [candidate] if candidate else [], teams, follow, source


def _provider_label(value):
    value = str(value or '')
    return value if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value) else 'configured_provider'


def _provider_issue(value):
    """Provider diagnostics must not echo credentials, personal data or arbitrary remote text."""
    value = str(value)
    provider = _provider_label(value.split(':', 1)[0])
    if re.search(r'(?i)robots|403|forbidden|permission|access denied|captcha|authentication', value):
        reason = 'access blocked; publisher and authentication restrictions were respected'
    elif re.search(r'(?i)timeout|timed out', value):
        reason = 'request timed out'
    elif re.search(r'(?i)no (?:usable )?results', value):
        reason = 'no usable indexed results'
    else:
        reason = 'provider unavailable or returned unsupported results'
    return {'stage': 'search_provider', 'provider': provider, 'error': provider + ': ' + reason + '.'}


_ROUTE_PATH = re.compile(r'(?i)/(?:people|team|leadership|management|executives?|about|contact|press|media|newsroom|'
                         r'investor|ir|staff|directory|faculty|board|officers|speakers?)(?:[/?#.-]|$)')


def _org_host(organization, url):
    """Whether the URL's host carries the organization's name (amd.com, ir.amd.com), a hint it is its own site."""
    slug = re.sub(r'[^a-z0-9]', '', organization.casefold())
    host = (urlsplit(url).hostname or '').lower().replace('-', '').replace('.', '')
    return len(slug) >= 3 and slug in host


def _lead_score(name, organization, url, title='', snippet=''):
    """How likely a search lead is to hold a published professional route for this person at this organization.

    Leads were read in arrival order: for "Lisa Su, AMD" the only page read was a German tourism site, while AMD's
    own pages waited unvisited. Pages naming neither the person nor the organization score below zero.
    """
    text = f'{title} {snippet} {url}'
    score = 0.0
    if _mentions(text, name):
        score += 3
    if _mentions(text, organization):
        score += 2
    if _org_host(organization, url):
        score += 3  # the organization's own site (amd.com, ir.amd.com)
    if _ROUTE_PATH.search(urlsplit(url).path or ''):
        score += 1
    return score if score > 0 else -1.0


def _query_plan(person, official_url, depth):
    name = person.name.replace(chr(34), '')
    organization = person.context.replace(chr(34), '')
    subject = f'"{name}" "{organization}"'
    queries = [subject + ' official staff professional contact', subject + ' team directory']
    if depth == 'deep':
        host = urlsplit(official_url).hostname if official_url else ''
        queries += [f'site:{host} "{name}"' if host else subject + ' official professional profile',
                    subject + ' faculty people biography', subject + ' office contact representative',
                    # An executive's bio page rarely lists a route; the organization's published press and investor
                    # contacts are how such a person is professionally reached. Organizational channels, not personal.
                    f'"{organization}" press media relations contact', f'"{organization}" investor relations contact']
    return list(dict.fromkeys(queries))


def _tor_resources(manager, check):
    """Dedicated required-Tor adapters cannot silently inherit a direct fallback."""
    from app.research.onion import TorRequiredFetcher
    from app.research.search import BingRssSearch, DuckDuckGoHtmlSearch, SearchProviderChain
    settings = getattr(manager, 'settings', {})
    timeout = max(5, min(20, float(settings.get('request_timeout_seconds', 20))))
    fetcher = TorRequiredFetcher('HAYAT-PublicContacts/1.0', timeout=timeout, delay=.35,
                                 respect_robots=True, check=check)
    search = SearchProviderChain([DuckDuckGoHtmlSearch(timeout=timeout, require_tor=True),
                                  BingRssSearch(timeout=timeout, require_tor=True)])
    return fetcher, search


def discover_contacts(manager, name, organization, official_url='', *, max_pages=None, depth='normal',
                      tor_only=False, check=None, progress=None, relevant_only=False):
    """Read bounded professional evidence; deep mode broadens queries, never personal records.

    A budget exhaustion returns explicitly partial evidence. Stop, privacy and
    route changes instead reject the result. No contact is saved or contacted.
    """
    if not isinstance(name, str) or not isinstance(organization, str) or not isinstance(official_url, str):
        raise ValueError('Use a known name, organization and optional public directory URL as text.')
    person = PersonRequest(name, organization)
    if not 3 <= len(person.context) <= 240:
        raise ValueError('A public organization or professional context is required to constrain this discovery.')
    if max_pages is None:  # deep reads twice as far: it used to read the same 4 pages as normal
        max_pages = 8 if depth == 'deep' else 4
    if type(max_pages) is not int or not 1 <= max_pages <= 12:
        raise ValueError('Public contact discovery reads between 1 and 12 pages.')
    if not isinstance(depth, str) or depth not in {'normal', 'deep'}:
        raise ValueError('Choose normal or deep public professional discovery.')
    if type(tor_only) is not bool:
        raise ValueError('tor_only must be a boolean.')
    official_url = _reference(official_url.strip()) if official_url.strip() else ''
    from app.privacy_masks import ROUTE
    initial_route = ROUTE.snapshot()
    via_tor = bool(tor_only or initial_route[0] is not None)
    if via_tor:
        proxy = urlsplit(initial_route[0] or '')
        if (proxy.scheme not in {'socks5', 'socks5h'} or proxy.hostname not in {'127.0.0.1', '::1', 'localhost'}
                or proxy.username or proxy.password or proxy.port in {None, 0}):
            raise PermissionError('A ready local Tor route is required; no direct fallback was used.')
    controls = _CONTROLS.get()
    checks = list(dict.fromkeys(callback for callback in (check, controls.get('check')) if callback is not None))
    callbacks = list(dict.fromkeys(callback for callback in (progress, controls.get('progress')) if callback is not None))
    started = time.monotonic()
    seconds = 180 if depth == 'deep' else 90
    queries = _query_plan(person, official_url, depth)
    report = {'name': person.name, 'organization': person.context, 'official_url': official_url,
              'trust': 'untrusted_external_data', 'depth': depth,
              'route': 'public_web_via_tor' if via_tor else 'direct_public_web', 'tor_required': via_tor,
              'scope': {'route': 'public_web_via_tor' if via_tor else 'direct_public_web',
                        'tor_required': via_tor, 'onion_only': False, 'onion_sources': False,
                        'private_records': False, 'exhaustive': False},
              'retrieved_at': datetime.now(UTC).isoformat(), 'candidates': [], 'team_routes': [], 'sources': [], 'errors': [],
              'identity_verified': False, 'completed': False, 'partial': False, 'cancelled': False,
              'searches_attempted': 0, 'searches_completed': 0, 'pages_checked': 0,
              'coverage': {'queries_planned': len(queries), 'queries': [], 'eligible_leads': 0,
                           'rejected_leads': 0, 'duplicate_leads': 0, 'duplicate_pages': 0,
                           'pages_retrieved': 0, 'pages_failed': 0, 'unvisited_leads': 0,
                           'distinct_source_hosts': 0, 'stop_reason': '', 'absence_of_data': 'unknown'},
              'limits': ['Source candidates remain separate; name and organization matches do not verify identity.',
                         'Only explicitly published professional email and same-site contact pages are projected. No phones or private details.',
                         'A selected organization URL is owner context, not independent proof that the domain is official.',
                         'No personal social platforms, brokers, inferred emails, login bypass, sending or automatic contact saving.',
                         'Search leads alone are never contact evidence. Missing or ambiguous channels remain unknown.',
                         'Coverage is bounded, not all of the web. No result does not establish that data is absent elsewhere.',
                         'Tor reads ordinary public websites; this workflow does not harvest onion, leaked or breach identity records.',
                         f'At most {len(queries)} search queries, {max_pages} read pages ({max_pages + _SPARE_READS} attempts), 2 MiB per page and a cooperative {seconds}-second run budget.']}
    def guard(*, budget=True):
        for callback in checks:
            if callback() is False:
                raise PublicContactCancelled('Public contact discovery was cancelled.')
        if getattr(getattr(manager, 'authority', None), 'privacy_mode', False):
            raise PermissionError('Privacy mode blocks public contact discovery.')
        if ROUTE.snapshot() != initial_route:
            raise PermissionError('Privacy route changed during public contact discovery; start a fresh request.')
        if budget and time.monotonic() - started >= seconds:
            raise _RunBudgetExceeded('Public contact discovery reached its run budget.')
    def notify(stage, message, **fields):
        for callback in callbacks:
            callback({'stage': stage, 'message': message, 'pages_checked': report['pages_checked'],
                      'searches_completed': report['searches_completed'], **fields})
    queue, queued, visited, final_urls = [], set(), set(), set()
    def enqueue(url, *, origin, link_depth=0, parent='', first=False, score=0.0):
        if url in queued or url in visited:
            report['coverage']['duplicate_leads'] += 1
            return False
        queued.add(url)
        entry = {'url': url, 'discovered_via': origin, 'link_depth': link_depth, 'parent_url': parent, 'score': score}
        queue.insert(0, entry) if first else queue.append(entry)
        report['coverage']['eligible_leads'] += 1
        return True
    if official_url:
        enqueue(official_url, origin='selected_organization_url')
    fetcher, search, owns_fetcher = manager.fetcher, manager.search, False
    def read_pages(limit):
        # The budget counts pages actually read; a page that fails to load is replaced by the next lead, up to a
        # small fixed allowance, so a few blocked sites no longer use up a deep search.
        while (queue and report['coverage']['pages_retrieved'] < limit
               and len(visited) < max_pages + _SPARE_READS):
            guard()
            # Prefer selected same-site links, then balance independent source hosts.
            counts = {}
            for url in visited:
                host = urlsplit(url).hostname
                counts[host] = counts.get(host, 0) + 1
            # Then search leads: spread across hosts first, and among the least-read hosts the most relevant lead
            # (person, organization, its own site) first. Irrelevant leads are not dropped, only read last.
            index = 0 if queue[0]['discovered_via'] in {'selected_organization_url', 'same_site_link'} else min(
                range(len(queue)), key=lambda number: (counts.get(urlsplit(queue[number]['url']).hostname, 0),
                                                       -queue[number].get('score', 0), number))
            entry = queue.pop(index)
            target = entry['url']
            if target in visited:
                continue
            visited.add(target)
            report['pages_checked'] = len(visited)
            notify('fetching', 'Reading a public source page.', url=target)
            try:
                resource = fetcher.fetch(target, max_bytes=2 * 1024 * 1024,
                                         timeout=max(.1, min(20, seconds - (time.monotonic() - started))))
            except Exception as exc:
                guard()
                report['coverage']['pages_failed'] += 1
                report['errors'].append({'stage': 'fetch', 'url': target, 'error': type(exc).__name__ + ': source could not be read.'})
                continue
            guard()
            try:
                final = _reference(resource.final_url)
                if len(resource.content) > 2 * 1024 * 1024 or resource.status_code != 200:
                    raise ValueError('Unexpected public page status or size.')
                if official_url and target == official_url and not _same_host(final, official_url):
                    raise ValueError('The selected organization page redirected to a different host.')
                if 'html' not in resource.headers.get('content-type', '').lower():
                    raise ValueError('This discovery requires visible public HTML, not embedded or private records.')
                if hashlib.sha256(resource.content).hexdigest() != resource.sha256 or not resource.retrieved_at:
                    raise ValueError('Retrieved evidence provenance is missing or inconsistent.')
                if final in final_urls:
                    report['coverage']['duplicate_pages'] += 1
                    continue
                final_urls.add(final)
                notify('analysing', 'Checking exact-name and organizational evidence.', url=final)
                candidates, teams, links, source = _extract(resource, person.name, person.context, official_url)
            except Exception as exc:
                report['coverage']['pages_failed'] += 1
                report['errors'].append({'stage': 'evidence', 'url': target, 'error': type(exc).__name__ + ': unsupported or inconsistent source evidence.'})
                continue
            guard()
            source.update({key: entry[key] for key in ('discovered_via', 'link_depth', 'parent_url')})
            report['sources'].append(source)
            report['coverage']['pages_retrieved'] += 1
            report['candidates'].extend(candidates)
            report['team_routes'].extend(teams)
            # Follow bounded same-host directory/contact links before unrelated leads.
            if entry['link_depth'] < (2 if depth == 'deep' else 1):
                for link in reversed(links):
                    enqueue(link, origin='same_site_link', link_depth=entry['link_depth'] + 1, parent=final, first=True)
    try:
        guard()
        if via_tor:
            fetcher, search = _tor_resources(manager, guard)
            owns_fetcher = True
        # A user-selected directory remains useful even when public indexes time out.
        if official_url:
            read_pages(1)
        for query in queries:
            guard()
            notify('searching', 'Searching public professional sources across configured indexes.')
            coverage = {'query': query, 'status': 'unavailable', 'leads_returned': 0, 'eligible_new_leads': 0, 'providers': []}
            report['coverage']['queries'].append(coverage)
            report['searches_attempted'] += 1
            try:
                search_fn = getattr(search, 'search_wide', None) or search.search
                rows = list(islice(search_fn(query, limit=12), 20))
            except Exception as exc:
                guard()
                report['errors'].append({'stage': 'search', 'error': type(exc).__name__ + ': public search unavailable.'})
                continue
            guard()
            report['searches_completed'] += 1
            coverage.update(status='results' if rows else 'no_usable_results', leads_returned=len(rows))
            # Only adapter-validated provider labels/counts are projected. Search snippets are never facts.
            for provider in getattr(search, 'last_coverage', [])[:8]:
                count = provider.get('usable_results', 0)
                coverage['providers'].append({'provider': _provider_label(provider.get('provider')),
                                              'status': str(provider.get('status', 'unknown'))[:40],
                                              'usable_results': count if type(count) is int and count >= 0 else 0})
            for row in rows:
                try:
                    target = _reference(str(row.url))
                except (ValueError, TypeError, AttributeError):
                    report['coverage']['rejected_leads'] += 1
                    continue
                score = _lead_score(person.name, person.context, target, str(getattr(row, 'title', '') or ''),
                                    str(getattr(row, 'snippet', '') or ''))
                if relevant_only and score < 0:
                    # A page naming neither the person nor the organisation (a long organisation string once
                    # brought back a German telecom's pages): skipped when the caller asks for relevant pages only.
                    report['coverage']['rejected_leads'] += 1
                    continue
                if enqueue(target, origin='public_search', score=score):
                    coverage['eligible_new_leads'] += 1
            issues = [_provider_issue(error) for error in getattr(search, 'last_errors', [])[:8]]
            report['errors'].extend(issues)
            if not rows and issues and any('no usable indexed results' not in issue['error'] for issue in issues):
                coverage['status'] = 'provider_unavailable'
            read_pages(min(max_pages, report['coverage']['pages_retrieved'] + 1))
        read_pages(max_pages)
        guard()
        report['completed'] = True
        report['coverage']['stop_reason'] = 'page_limit' if queue else 'eligible_leads_exhausted'
    except _RunBudgetExceeded:
        guard(budget=False)
        report['partial'] = True
        report['coverage']['stop_reason'] = 'run_budget'
        report['errors'].append({'stage': 'budget', 'error': 'Run budget reached; only retrieved evidence is shown. Remaining coverage is unknown.'})
    finally:
        if owns_fetcher:
            fetcher.close()
    guard(budget=False)
    report['coverage']['unvisited_leads'] = len([entry for entry in queue if entry['url'] not in visited])
    report['coverage']['distinct_source_hosts'] = len({urlsplit(row['final_url']).hostname for row in report['sources']})
    report['elapsed_ms'] = int((time.monotonic() - started) * 1000)
    found = bool(report['candidates'] or report['team_routes'])
    unavailable = (not report['sources'] and (report['coverage']['pages_failed'] > 0 or
                   all(row['status'] in {'unavailable', 'provider_unavailable'} for row in report['coverage']['queries'])))
    report['outcome'] = ('partial_evidence' if found else 'coverage_incomplete') if report['partial'] else (
        'evidence_found' if found else 'sources_unavailable' if unavailable else 'no_eligible_evidence')
    notify('partial' if report['partial'] else 'complete',
           'Bounded discovery stopped; review coverage and each source candidate separately.')
    _log.info('public_contact_discovery outcome=%s sources=%s candidates=%s teams=%s errors=%s',
              report['outcome'], len(report['sources']), len(report['candidates']), len(report['team_routes']), len(report['errors']))
    return report


def format_report(report):
    lines = ['Public professional contact evidence',
             'Candidates are not identity verification. No message was sent and no contact was saved.']
    coverage = report.get('coverage', {})
    route = report.get('route', report.get('scope', {}).get('route', 'unknown'))
    lines += ['Route: ' + str(route),
              f"Coverage: {report.get('searches_completed', 0)}/{coverage.get('queries_planned', '?')} search queries; "
              f"{coverage.get('pages_retrieved', len(report.get('sources', [])))} retrieved source pages; "
              f"{coverage.get('distinct_source_hosts', '?')} source hosts; "
              f"{coverage.get('unvisited_leads', '?')} unvisited leads.",
              'Absence of data elsewhere: unknown. This is a bounded professional-source search.']
    if report.get('partial'):
        lines.append('PARTIAL: the run budget ended. Only evidence retrieved before stopping is shown.')
    for candidate in report.get('candidates', []):
        lines += ['', candidate['name'] + ' — ' + candidate['organization'], 'Candidate: ' + candidate['id'],
                  'Source: ' + candidate['source_url'], 'Retrieved: ' + candidate['retrieved_at'],
                  'SHA-256: ' + candidate['source_hash'], candidate['snippet']]
        for channel in candidate['channels']:
            lines.append('Published professional ' + channel['kind'] + ': ' + channel['value'])
        if not candidate['channels']:
            lines.append('No sufficiently clear professional contact channel on this source.')
    for route in report.get('team_routes', []):
        lines += ['', 'Team route (not the named person): ' + route['team_name'], route['kind'] + ': ' + route['value'],
                  'Source: ' + route['source_url'], 'Retrieved: ' + route['retrieved_at'], 'SHA-256: ' + route['source_hash'], route['snippet']]
    if not report.get('candidates') and not report.get('team_routes'):
        lines.append('No eligible contact evidence was found in the bounded pages checked; no details were guessed.')
    if report.get('sources'):
        lines += ['', 'Retrieved source audit:']
        for source in report['sources']:
            lines += [source['final_url'], 'Retrieved: ' + source['retrieved_at'],
                      'SHA-256: ' + source['sha256'], source['reason']]
    if report.get('errors'):
        lines += ['', 'Provider/source issues:'] + [str(row.get('error', 'Unavailable source')) for row in report['errors']]
    lines += ['', 'Limits and unknowns:', *report.get('limits', [])]
    return '\n'.join(lines)
