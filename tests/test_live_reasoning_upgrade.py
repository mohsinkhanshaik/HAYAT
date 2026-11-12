"""Opt-in local inference check: preserve explicit constraints and an unknown."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('HAYAT_LIVE') != '1', reason='opt-in actual local inference')


def test_actual_deep_reasoning_retains_an_explicit_unknown():
    from app.runtime import build_runtime

    runtime = build_runtime(use_model=True, start_background_services=False, interactive_approvals=False)
    try:
        assert runtime.model is not None
        question = (
            'This is a fictional decision using only the facts supplied here. I need an offline Windows notes app, '
            'with no subscription, a total budget of 50 dollars and Markdown export. BirchPad costs 30 dollars once, '
            'runs offline on Windows and exports Markdown. CedarNotes costs 10 dollars each month, requires internet, '
            'and exports Markdown. Whether BirchPad encrypts files is UNKNOWN; do not infer it. '
            'Which candidate meets my stated constraints, what is still unknown, and what small check should I do next? '
            'Give a concise answer without claiming to have tested either app.')
        started = time.monotonic()
        answer = runtime.conversation.reply(question, modes=['DEEP', 'ADVERSARIAL'])
        inquiry = runtime.conversation.last_inquiry or {}
        review = runtime.conversation.last_dual_review or {}
        lower = answer.lower()
        checks = {
            'candidate_named': 'birchpad' in lower,
            'encryption_unknown_preserved': 'encrypt' in lower and any(term in lower for term in ('unknown', 'not known', 'not specified', 'not provided', 'unverified', 'not established', 'verify', 'check')),
            'next_check': any(term in lower for term in ('check', 'verify', 'test', 'confirm')),
            'inquiry_completed': inquiry.get('state') == 'examined' and inquiry.get('questions', 0) > 0,
            'review_completed': review.get('state') == 'reviewed',
            'no_false_verification': review.get('verified') is False,
        }
        report = {'model': getattr(runtime.model, 'model', None), 'seconds': round(time.monotonic() - started, 2),
                  'answer': answer, 'checks': checks, 'inquiry': inquiry, 'review': review,
                  'scope': 'One fictional local-model constraint/uncertainty case, not a general intelligence benchmark.'}
        target = Path(__file__).resolve().parents[1] / '.test-runs/live-reasoning-upgrade.json'
        target.write_text(json.dumps(report, indent=2), encoding='utf-8')
        assert all(checks.values()), report
    finally:
        runtime.close()
