"""Explicit professional-contact requests; no guessing a person or organization."""
from __future__ import annotations

import re


def request(text):
    value = ' '.join(str(text or '').split()).strip().rstrip('.!?')
    if not value or len(value) > 1800:
        return None
    deep = False
    tor_only = False
    for _ in range(2):
        ending = re.search(r'(?i)\s+(?:(in depth|deeply|thoroughly)|(through|via|over) tor)$', value)
        if not ending:
            break
        deep = deep or bool(ending[1])
        tor_only = tor_only or bool(ending[2])
        value = value[:ending.start()]
    leading = re.match(r'(?i)^(?:please )?deep(?:ly)?\s+', value)
    if leading:
        deep, value = True, value[leading.end():]
    match = re.fullmatch(r'(?i)(?:please )?(?:find|search|look up)(?: for)? '
                         r'(?:public )?(?:(?:professional|team|work) )?(?:contact routes|contacts|contact details) '
                         r'for (.+?) at (.+)', value)
    if not match:
        return None
    name, organization = (part.strip('"\' ') for part in match.groups())
    official_url = ''
    seeded = re.fullmatch(r'(?is)(.+?) (?:using|from) (https?://\S+)', organization)
    if seeded:
        organization, official_url = seeded.groups()
    if not 2 <= len(name) <= 120 or not 3 <= len(organization) <= 240 or len(official_url) > 2000:
        return None
    arguments = {'name': name, 'organization': organization, 'official_url': official_url, 'max_pages': 12 if deep else 4}
    if deep:
        arguments['depth'] = 'deep'
    if tor_only:
        arguments['tor_only'] = True
    return arguments


def plan(text, names):
    arguments = request(text)
    if arguments is None or 'public_contact_search' not in names:
        return None
    return {'description': 'Retrieve public professional contact evidence for the supplied name and organization.',
            'tool': 'public_contact_search', 'arguments': arguments,
            'expected': {'type': 'tool_success'}, 'max_retries': 0}


def report(history):
    # A specialized search report must not obscure the outcome of another action.
    if not history or any(item.get('tool') != 'public_contact_search' for item in history):
        return None
    result = history[-1].get('result') or {}
    if not result.get('success'):
        return 'Professional contact search failed: ' + str(result.get('error') or 'No successful result was returned.')
    output = result.get('output')
    if not isinstance(output, dict):
        return 'Professional contact search returned no readable source report.'
    from app.research.public_contacts import format_report
    return format_report(output)
