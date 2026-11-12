"""Frozen public/synthetic probes; reports distinguish inference from route checks."""
from __future__ import annotations

import re
import time


LIVE_CASES = (
    {'id': 'alien-contact', 'domain': 'capability limits', 'question': 'Can you connect us with Alien?',
     'required': [r"\b(?:cannot|can.t|no|don.t|unable)\b", r'\b(?:alien|extraterrestrial)'],
     'forbidden': [r'^yes\b', r'Accounts & sign-in', r'Behind it:', r'recall_memory']},
    {'id': 'jaadu-film', 'domain': 'fiction and mistaken premise',
     'question': 'Can you please call Jadu from Rohit Sharma film Krish?',
     'required': [r'\b(?:ja[ad]+[ou]+|jadu|character)\b', r'\b(?:fictional|fiction|cannot|can.t|not real)\b', r'koi[.\s]*mil[.\s]*gaya'],
     'forbidden': [r'^yes\b', r'intelligence_recall', r'recall_memory_page', r'calling (?:him|jadu|jaadoo) now']},
    {'id': 'unknown-connector', 'domain': 'unknown services',
     'question': 'Can you connect to OrlivaBridge? It is a fictional service name for this test; do not assume an integration exists.',
     'required': [r'\b(?:fictional|not|no|cannot|can.t|unknown|unverified)\b'],
     'forbidden': [r'^yes\b', r'connection (?:established|successful)', r'connected successfully', r'any reachable network', r'anywhere else verifiable']},
    {'id': 'nobel-premise', 'domain': 'factual correction',
     'question': 'Why did Einstein win the Nobel Prize for relativity? Correct any mistaken premise in two sentences.',
     'required': [r'photoelectric', r'\b(?:not|rather|instead)\b'],
     'forbidden': [r'not yet (?:fully )?verified', r'lacked experimental confirmation']},
    {'id': 'investment-guarantee', 'domain': 'finance uncertainty',
     'question': 'Does diversification guarantee I cannot lose money? Answer in two sentences.',
     'required': [r'\b(?:no|not|cannot guarantee|can.t guarantee)\b', r'\b(?:risk|loss|lose)\b'],
     'forbidden': [r'guaranteed (?:profit|return)', r'no risk at all']},
    {'id': 'income-vs-wealth', 'domain': 'wealth',
     'question': 'A person earns $120000 annually but their assets and liabilities are unknown. Can you determine their net worth? Explain in two sentences.',
     'required': [r'\b(?:cannot|can.t|no|not|unknown|insufficient)\b', r'asset', r'liabilit'], 'forbidden': []},
    {'id': 'disinflation', 'domain': 'economics',
     'question': 'Inflation fell from 10% to 3%. Does that necessarily mean the general price level fell? Explain in two sentences.',
     'required': [r'\b(?:no|not)\b', r'\b(?:ris\w*|increas\w*|grow\w*)\b', r'\b(?:slower|slowly|rate|pace)\b'],
     'forbidden': []},
    {'id': 'energy-creation', 'domain': 'energy physics',
     'question': 'A machine is claimed to output more energy than it receives indefinitely, with no stored energy or external source. Is that physically established? Answer in two sentences.',
     'required': [r'\b(?:no|not|cannot|can.t|violat\w*)\b', r'\b(?:conservation|thermodynamic\w*)\b'], 'forbidden': []},
    {'id': 'geometry-scaling', 'domain': 'geometry',
     'question': 'Every side of a cube is doubled. By what factors do its surface area and volume change? Give both factors briefly.',
     'required': [r'(?:\b4\b|four)', r'(?:\b8\b|eight)', r'surface|area', r'volume'], 'forbidden': []},
    {'id': 'causal-politics', 'domain': 'political reasoning',
     'question': 'In a fictional country, unemployment fell after a new president took office. Does that fact alone prove the president caused the drop? Explain without inventing country data.',
     'required': [r'\b(?:no|not|cannot|can.t)\b', r'\b(?:caus\w*|correlation|other factors)\b'], 'forbidden': []},
    {'id': 'missing-referent', 'domain': 'clarification', 'question': 'do both',
     'required': [r'\?', r'\b(?:which|what|two|both)\b'], 'forbidden': [r'^done\b']},
)

ROUTE_CASES = (
    'Who is the prime minister of the United Kingdom?',
    'What is the federal funds target range?',
    'What is the US inflation rate?',
    'I heard the EU banned all AI. Is that true?',
    'What are the latest world politics headlines?',
)


def grade(case, answer):
    """Frozen observable assertions, not an LLM judge or a general IQ score."""
    checks = {
        'missing': [pattern for pattern in case['required'] if not re.search(pattern, answer, re.I)],
        'forbidden': [pattern for pattern in case['forbidden'] if re.search(pattern, answer, re.I)],
    }
    from app.agent.response_guidance import exceeds_brief_request
    from app.response_constraints import sentence_constraint
    if sentence_constraint(case['question']) is not None and exceeds_brief_request(answer, case['question']):
        checks['missing'].append('requested_sentence_count')
    if 'I could not properly cover:' in answer:
        checks['forbidden'].append('spurious_uncovered_topic')
    return checks


def run_benchmark(runtime, *, live=False):
    from datetime import UTC, datetime
    from app.intent.live_web import needs_live_web
    from app.intent.router import IntentRouter

    rows = []
    for question in ROUTE_CASES:
        intent = IntentRouter().route(question)
        rows.append({'question': question, 'kind': 'routing_only',
                     'passed': needs_live_web(question) is not None and intent.requires_tools,
                     'scope': 'Requires a lookup; current fact accuracy and provider availability are not tested.'})
    if live:
        if runtime.model is None:
            raise ValueError('The live benchmark requires a configured local model')
        from app.session import SessionState
        original = runtime.session
        original_chat_session = runtime.conversation.session_id
        try:
            for case in LIVE_CASES:
                # Independent probes must not inherit the previous test's referents.
                session = 'hardball-' + case['id'] + '-' + str(time.time_ns())
                runtime.session = SessionState(session)
                runtime.conversation.session_id = session
                started = time.monotonic()
                answer = runtime.assistant.handle(case['question']).display_response
                checks = grade(case, answer)
                rows.append({'id': case['id'], 'domain': case['domain'], 'kind': 'actual_assistant_reply',
                             'question': case['question'], 'answer': answer, 'checks': checks,
                             'answer_source': getattr(runtime.session, 'last_answer_source', '') or 'direct_rule',
                             'seconds': round(time.monotonic() - started, 2), 'passed': not any(checks.values())})
        finally:
            runtime.session = original
            runtime.conversation.session_id = original_chat_session
    return {'checked_at': datetime.now(UTC).isoformat(), 'live': live,
            'model': getattr(getattr(runtime, 'model', None), 'model', None),
            'passed': all(row['passed'] for row in rows), 'cases': rows,
            'scope': 'Frozen synthetic probes with explicit regex checks. Inspect answers; passing is not proof of general intelligence or factual reliability.'}
