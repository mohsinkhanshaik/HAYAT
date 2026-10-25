"""Explicit customer chat routes; free conversation never becomes a work order."""
from __future__ import annotations

import json
import re
from copy import deepcopy

TOOLS = {'customer_intake', 'customer_details', 'customer_list', 'customer_decide', 'customer_work', 'customer_quote', 'customer_invoice'}


def request(text, session=None):
    value = re.sub(r'(?i)^(?:hayat[,.:]?\s+)?(?:please[,.:]?\s+)?', '', text.strip(), count=1)
    normalized = re.sub(r'\s+', ' ', value).lower().strip(' .!?')
    active = getattr(session, 'active_customer', None)
    exact = re.fullmatch(r'(?:use\s+|run\s+)?(customer_\w+)\s*(\{.*\})?', value, re.S | re.I)
    if exact and exact[1].lower() in TOOLS:
        arguments = json.loads(exact[2]) if exact[2] else {}
        if not isinstance(arguments, dict):
            raise ValueError('Customer tool arguments must be a JSON object.')
        return exact[1].lower(), arguments
    if re.fullmatch(r'(?:show|list|open)(?: me)?(?: my| the)? (?:customers|prospects|customer queue|customer workspace)', normalized):
        return 'customer_list', {}
    followup = re.fullmatch(r'(?is)(?:customer (?:update|reply|feedback)|update this customer|the customer says|this customer says|they said)\s*:\s*(.+)', value)
    if active and followup:
        return 'customer_intake', {'customer': active, 'message': followup[1], 'facts': labelled_facts(followup[1])}
    match = re.fullmatch(r'(?is)(?:my |our )?(?:new |potential |understand |save )?(?:customer|prospect)\s+(.{1,160}?)(?:\s+says)?\s*:\s*(.+)', value)
    if match:
        name, message = match[1].strip(), match[2].strip()
        if name.lower() not in {'update', 'reply', 'feedback'}:
            return 'customer_intake', {'customer': name, 'message': message, 'facts': labelled_facts(message)}
    if active and labelled_facts(value) and all(labelled_facts(line) for line in value.splitlines() if line.strip()):
        return 'customer_intake', {'customer': active, 'message': value, 'facts': labelled_facts(value)}
    match = re.fullmatch(r'(?is)(show|review|understand|assess)\s+(?:the )?(?:customer|prospect)\s+(.{1,160})', value.strip(' .!?'))
    if match and match[2].lower() not in {'queue', 'workspace'}:
        name = active if match[2].lower() in {'brief', 'details', 'profile'} else match[2]
        if name:
            return ('customer_decide' if match[1].lower() in {'understand', 'assess'} else 'customer_details'), {'customer': name}
    if not active:
        return None
    if normalized in {'prepare a quote for this customer', 'prepare their quote'}:
        return 'customer_quote', {'customer': active}
    if normalized in {'prepare an invoice for this customer', 'prepare their invoice'}:
        return 'customer_invoice', {'customer': active}
    match = re.fullmatch(r'(?i)(?:prepare|create|draft) (?:a |the )?quote for (?:this customer|this prospect|them) (?:for|at) (\S+) (\S+)', value.strip(' .!?'))
    if match:
        return 'customer_quote', {'customer': active, 'price': match[1], 'currency': match[2].upper()}
    match = re.fullmatch(r'(?i)(?:prepare|create) (?:an? |the )?invoice for (?:this customer|this prospect|them) from (?:quote )?(Q-\d{4}-\d{4,})', value.strip(' .!?'))
    if match:
        return 'customer_invoice', {'customer': active, 'quote_number': match[1].upper()}
    if normalized in {'show this customer', 'show this prospect', 'show customer details', 'review this customer'}:
        return 'customer_details', {'customer': active}
    if normalized in {'understand this customer', 'assess this customer', 'what should we do next for this customer', 'what does this customer need', 'what should we do next', 'what do they need'}:
        return 'customer_decide', {'customer': active}
    actions = {
        'decide and do the next step': 'next', 'do the next step for this customer': 'next',
        'prepare a reply for this customer': 'reply', 'draft a reply for this customer': 'reply',
        'prepare a reply': 'reply', 'prepare a proposal for this customer': 'proposal',
        'prepare a proposal': 'proposal', 'do the work for this customer': 'deliver',
        'prepare the deliverable for this customer': 'deliver',
        'prepare their reply': 'reply', 'draft their reply': 'reply',
        'prepare their proposal': 'proposal', 'draft their proposal': 'proposal',
        'prepare their deliverable': 'deliver', 'prepare the next customer step': 'next',
    }
    if normalized in actions:
        return 'customer_work', {'customer': active, 'action': actions[normalized]}
    if normalized == 'retry the work for this customer':
        return 'customer_work', {'customer': active, 'retry': True}
    if normalized in {'pause this customer', 'close this customer'}:
        return 'customer_intake', {'customer': active, 'message': value, 'status': 'paused' if normalized.startswith('pause') else 'closed'}
    return None


def labelled_facts(message):
    result = {}
    fields = {'audience': 'audience', 'budget': 'budget', 'deadline': 'deadline', 'file': 'source_path',
              'source file': 'source_path', 'cleanup rule': 'cleanup_rule', 'requirements': 'requirements'}
    for line in message.splitlines():
        match = re.fullmatch(r'(?i)\s*(?:their\s+)?(audience|budget|deadline|file|source file|cleanup rule|requirements|sources|source urls)\s*(?::|\bis\b)\s*(.*)', line)
        if not match:
            continue
        key, value = match[1], match[2]
        if key.strip().lower() in fields:
            result[fields[key.strip().lower()]] = value.strip().strip('"')
        elif key.strip().lower() in {'sources', 'source urls'}:
            result['source_urls'] = [url for url in re.split(r'[\s,]+', value.strip()) if url]
    return result


def plan(text, names, session=None, intent=None):
    bound = getattr(intent, 'entities', {}).get('customer_action') if getattr(intent, 'classification_source', None) == 'customer_work' else None
    parsed = (bound['tool'], deepcopy(bound['arguments'])) if bound else request(text, session)
    if parsed and parsed[0] in names:
        return {'description': 'Run the customer workflow using the supplied brief and selected customer.',
                'tool': parsed[0], 'arguments': parsed[1], 'expected': {'type': 'tool_success'}, 'max_retries': 0}
    return None


def report(history):
    sections = []
    for event in history or []:
        result = event.get('result') or {}
        output = result.get('output')
        if event.get('tool') in TOOLS and result.get('success') and (event.get('verification') or {}).get('passed') and isinstance(output, dict):
            preview = str(output.get('preview') or '').strip()
            sections.append((preview[:3000] + '\n\n' if preview else '') + str(output.get('text') or ''))
    return '\n\n'.join(sections) or None
