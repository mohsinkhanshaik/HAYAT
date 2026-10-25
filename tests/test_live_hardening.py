"""Opt-in checks for the three previously observed local-model failures."""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE_HARDENING') != '1', reason='opt-in actual local-model regression')


def test_previous_chat_failures_on_the_actual_hayat_pipeline():
    from app.runtime import build_runtime
    from app.response_constraints import word_count

    runtime = build_runtime(use_model=True, start_background_services=False)
    rows = []
    try:
        assert runtime.model is not None
        cases = [
            ('binary conclusion', 'If all bloops are razzies and all razzies are lazzies, are all bloops lazzies? Answer yes or no.',
             lambda answer: answer.strip().casefold().rstrip('.') == 'yes'),
            ('list setup', 'Here is my shopping list: eggs, milk, bread, apples, coffee.', bool),
            ('list edit', "Remove milk and add rice. What's on the list now?",
             lambda answer: all(item in answer.casefold() for item in ('eggs', 'bread', 'apples', 'coffee', 'rice'))),
            ('exact words and defining fact', 'In exactly three words: what is the largest planet in our solar system?',
             lambda answer: word_count(answer) == 3 and 'jupiter' in answer.casefold()),
        ]
        from tests.test_live_chat_harder import mentions_kept_milk
        for name, prompt, check in cases:
            answer = runtime.assistant.handle(prompt).response.display_text or ''
            passed = bool(check(answer)) and (name != 'list edit' or not mentions_kept_milk(answer))
            rows.append({'case': name, 'prompt': prompt, 'answer': answer, 'passed': passed})
            print(json.dumps(rows[-1], ensure_ascii=True), flush=True)
        report = {'model': getattr(runtime.model, 'model', None), 'cases': rows, 'passed': all(row['passed'] for row in rows),
                  'scope': 'Four actual assistant turns targeting earlier failures; not a complete rerun of the older 80-minute chat suites.'}
        Path('.test-runs/hardening-live-model.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        assert report['passed'], rows
    finally:
        runtime.close()
