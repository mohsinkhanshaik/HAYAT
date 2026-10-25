"""Small actual-model checks for contextual choices; no network research or outward actions."""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='live check: set HAYAT_LIVE=1')


def test_hayat_model_preserves_context_choices_and_constraints():
    from app.runtime import build_runtime
    from app.agent.conversation import ConversationService
    from app.agent.response_guidance import exceeds_brief_request

    runtime = build_runtime(use_model=True, start_background_services=False)
    rows = []
    try:
        assert runtime.model is not None
        cases = [
            ('For this fictional choice only: I need an offline Windows notes app, with no subscription. '
             'Option 1 is BirchPad, a local text-file editor. Option 2 is CedarNotes, a local SQLite app. '
             'In the next answer, name my selected app in one sentence.',
             '1. BirchPad uses plain files. 2. CedarNotes uses local SQLite.',
             'use the second option', ('cedarnotes',)),
            ('I have two independent tasks for my offline notes app: add Markdown export and add local SQLite backup. '
             'In the next answer, name the selected tasks in one sentence.',
             '1. Add Markdown export. 2. Add local SQLite backup.',
             'do both', ('markdown', 'backup')),
            ('This is a fictional choice, not real market data. The app must run offline on Windows and cost at most '
             '50 dollars total, without a subscription. BirchPad meets all requirements for a one-time 40 dollars. '
             'CloudLedger costs 10 dollars every month and needs the internet. '
             'In the next answer, choose an app by name and explain in one sentence.',
             'CloudLedger might be an option, but it requires a recurring subscription and the internet.',
             'the same without subscriptions', ('birchpad',)),
        ]
        for index, (user, assistant, followup, required) in enumerate(cases):
            session = f'live-understanding-{index}'
            runtime.memory.record_message(session, 'user', user)
            runtime.memory.record_message(session, 'assistant', assistant)
            chat = ConversationService(runtime.model, runtime.memory, session)
            answer = chat.reply(followup)
            format_ok = not exceeds_brief_request(answer, 'Reply in one sentence.')
            passed = all(term in answer.lower() for term in required) and format_ok
            rows.append({'followup': followup, 'answer': answer, 'required': required,
                         'one_sentence': format_ok, 'passed': passed})
            print(json.dumps(rows[-1]), flush=True)
        chat = ConversationService(runtime.model, runtime.memory, 'live-understanding-no-referent')
        answer = chat.reply('do both')
        passed = '?' in answer and any(term in answer.lower() for term in ('which', 'what', 'two', 'both'))
        rows.append({'followup': 'do both (new session)', 'answer': answer, 'passed': passed})
        print(json.dumps(rows[-1]), flush=True)
        report = {'provider': runtime.model.name, 'model': getattr(runtime.model, 'model', None),
                  'passed': all(row['passed'] for row in rows), 'cases': rows,
                  'scope': 'Four frozen contextual cases; not a general reasoning or consciousness benchmark.'}
        (Path(__file__).resolve().parents[1] / '.test-runs/live-understanding.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        assert report['passed'], rows
    finally:
        runtime.close()
